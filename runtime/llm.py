"""
runtime.llm - LLM 调用客户端（最小可替换实现）

定位：
- 这是 beeOS 的基础设施，不是业务语义——runtime 只知道"调一个模型"，
  不知道被建模的领域是什么
- 业务提示词不属于本模块：它由调用方（结构化器）传入，本模块只管传输与解析

端点 / 协议 / 凭证仍来自 ~/.minimax/config.yaml（provider + model 引用），
本模块只依赖这一个契约，换 provider = 改配置，不改代码。

直连 HTTP 而不经过 llm-call skill 的 CLI：那个 CLI 只暴露
prompt / model / max-tokens / temperature / timeout，thinking 参数传不进去，
而 think 恰恰是这里最大的变量（见 complete() 的说明）。直连之后少一个
subprocess 依赖，也不用再把派生 config 写到临时目录。

模型返回的两种杂质（实测 MiniMax-M3）：
1. <think>...</think> 推理段
2. ```json ... ``` markdown 包裹
两者都要剥掉才能拿到裸 JSON。（thinking=disabled 时不会有第 1 种。）
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

# ~/.minimax/config.yaml —— 注意：这是 MCode runtime 拥有的文件，它会重写
# （实测引号风格被 YAML 库规范化写回），所以我们不假设里面的值是稳定的。
_CONFIG_PATH = Path.home() / ".minimax/config.yaml"

# 覆盖凭证的环境变量：runtime 改写 config.yaml 时不至于把 LLM 腿打断
_ENV_KEY = "BEEOS_LLM_API_KEY"
_ENV_BASE_URL = "BEEOS_LLM_BASE_URL"

# 占位符 key：不是"没配"，是"配了个假的"——必须区分，否则只会得到一个 401
_PLACEHOLDER_HINTS = ("xxx", "your", "changeme", "placeholder", "dummy", "fake", "test")

# 推理段（<think> / <think> 变体）
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# markdown 代码块围栏：```json ... ``` 或 ``` ... ```
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    """LLM 调用失败（端点错 / 凭证错 / 超时 / 输出无法解析为 JSON）"""


class LLMParseError(LLMError):
    """模型输出无法解析为 JSON（含输出为空）

    单独一类，因为它是**随机性**故障：推理模型可能把 max_tokens 全花在
    <think> 上，JSON 压根没轮到输出（实测有整轮返回空串）。换个采样重试
    通常就正常了——所以值得自动重试，而 401 / 超时那类不值得。
    """


def _is_placeholder(key: str) -> bool:
    """判断 apiKey 是不是占位符（sk-xxx / your-api-key / ...）"""
    low = key.strip().lower()
    if not low:
        return True
    return any(h in low for h in _PLACEHOLDER_HINTS)


def _read_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
    except Exception:  # noqa: BLE001 — 读不动就当没配，交给上层报可操作的错
        return {}
    return data if isinstance(data, dict) else {}


def _provider_options(config: dict[str, Any]) -> dict[str, Any]:
    provider = (config.get("provider") or {}).get("minimax") or {}
    options = provider.get("options") or {}
    return options if isinstance(options, dict) else {}


def _resolve_endpoint() -> tuple[str, str]:
    """决定端点和凭证：config.yaml 为底，env 可覆盖。

    以前这里要把派生 config 写到临时目录再喂给 llm_call.py —— 那是"必须
    经由 CLI 传参"的副作用。现在直连 API 了，临时文件那套整个不需要，
    凭证也就不必落盘。

    返回 (url, api_key)
    """
    options = dict(_provider_options(_read_config()))
    env_key = (os.environ.get(_ENV_KEY) or "").strip()
    env_base = (os.environ.get(_ENV_BASE_URL) or "").strip()

    key = str(options.get("apiKey") or "").strip()
    if env_key:
        if _is_placeholder(env_key):
            raise LLMError(
                f"{_ENV_KEY} is set to a placeholder value, not a real key. "
                f"Export the actual API key, or clear it to fall back to {_CONFIG_PATH}."
            )
        key = env_key
    elif _is_placeholder(key):
        raise LLMError(
            "no usable LLM credential: "
            f"{_CONFIG_PATH} provider.minimax.options.apiKey is a placeholder. "
            f"Set a real key in that file, or export {_ENV_KEY}=<key> "
            "before starting beeOS (and restart the web server)."
        )

    base = (env_base or str(options.get("baseURL") or "")).strip().rstrip("/")
    if not base:
        raise LLMError(
            f"no LLM endpoint: neither {_ENV_BASE_URL} nor "
            f"{_CONFIG_PATH} provider.minimax.options.baseURL is set"
        )
    return f"{base}/chat/completions", key


def strip_noise(text: str) -> str:
    """剥掉推理段和 markdown 围栏，保留 JSON 本体"""
    cleaned = _THINK_RE.sub("", text).strip()
    m = _FENCE_RE.search(cleaned)
    if m:
        cleaned = m.group(1).strip()
    return cleaned


def extract_json(text: str) -> Any:
    """从模型原始输出里取出 JSON 对象

    依次尝试：整体是 JSON → markdown 围栏内是 JSON → 首个 {...} 平衡括号段
    """
    candidates = [text.strip()]
    candidates.append(strip_noise(text))

    for cand in candidates:
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass

    # 兜底：扫描第一个平衡的 {...}（模型偶尔会在 JSON 前后加话）
    cleaned = strip_noise(text)
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(cleaned[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMParseError(f"model output is not valid JSON: {cleaned[:200]!r}")


def _readable_error(text: str, limit: int = 300) -> str:
    """从子进程错误里挑出能读的那几行。

    子进程异常时 stderr 是一整段 traceback，前 300 字符全是
    "Traceback (most recent call last):" 加文件路径，而真正的原因
    （httpx.ReadTimeout / 401 / JSON 解析失败…）在最后一两行。
    按字符数截头部等于把答案切掉——降级原因照抄这段的话，
    看板上显示的是路径，不是原因。
    """
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    if len(lines) <= 2:
        picked = lines
    elif lines[0].startswith("Traceback"):
        picked = ["…"] + lines[-1:]
    else:
        picked = lines[:2] + ["…"] + lines[-1:]
    return "\n".join(picked)[:limit]


def image_data_url(data: bytes | str, *, mime: str = "image/jpeg") -> str:
    """图片 → API 认的 data URL

    Args:
        data: 图片字节，或已经 base64 编码好的字符串（会自动剥掉 data: 前缀）
        mime: MIME 类型

    这是**基础设施**：内核只负责把字节拼成合法 URL，
    不关心这些像素是什么业务对象（小票 / 合同 / 发票是盒子自己的事）。
    """
    if isinstance(data, bytes):
        b64 = base64.b64encode(data).decode("ascii")
    else:
        b64 = data.strip()
        # 已经是完整 data URL 就原样返回，别套第二层
        if b64.startswith("data:"):
            return b64
    return f"data:{mime};base64,{b64}"


def complete(
    prompt: str,
    *,
    model: str = "minimax/MiniMax-M3",
    max_tokens: int = 8000,
    timeout: float = 120.0,
    thinking: str | None = None,
    images: list[str] | None = None,
) -> str:
    """调用 LLM，返回原始文本（未剥杂质）

    images 传图（OpenAI 兼容的 image_url 结构，每个元素一个 URL，
    可用 http(s) 地址或 image_data_url() 拼出来的 data: URL）。
    不传时请求体跟以前逐字节一样——纯文本调用点不受影响。

    thinking 传给 API 的 `thinking.type`，只接受 "adaptive" / "disabled"，
    传别的会被 API 拒（实测 allowed: adaptive, disabled）。None = 不传，
    用模型默认值（M3 默认开）。

    为什么需要它：M3 默认开推理，实测抽取那条腿的 JSON 本体只要
    439~1121 字符，<think> 却占 95% 以上（原始输出 27k 字符）——
    think 会把 max_tokens 用满，JSON 轮不到输出，整轮返回空串。
    传 "disabled" 后：3 秒、296 token、正常 JSON（对照：33 秒、8000 token、
    空输出）。抽取这种"照着原文抽结构"的任务本来不需要长时间推理。
    需要真推理的调用点就别传 disabled。

    以前这里是 subprocess 调 llm_call.py，所以只能用它暴露的参数，
    thinking 传不进去。现在直连，参数自己说了算。
    """
    url, key = _resolve_endpoint()
    if images:
        content: Any = [{"type": "text", "text": prompt}]
        content.extend({"type": "image_url", "image_url": {"url": u}} for u in images)
    else:
        content = prompt
    body: dict[str, Any] = {
        "model": model.split("/", 1)[-1],
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
    }
    if thinking:
        body["thinking"] = {"type": thinking}

    try:
        resp = httpx.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except httpx.TimeoutException as e:
        raise LLMError(f"llm call timed out after {timeout}s") from e
    except httpx.HTTPError as e:
        raise LLMError(f"llm request failed: {_readable_error(str(e))}") from e

    if resp.status_code != 200:
        raise LLMError(
            f"llm call failed (http {resp.status_code}): {_readable_error(resp.text)}"
        )

    try:
        out = (resp.json()["choices"][0]["message"].get("content") or "").strip()
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected response shape: {_readable_error(resp.text)}") from e
        # 整轮空输出和"输出了但解析不了"是同一类随机故障，同样可重试
        raise LLMParseError("llm returned empty output")
    return out


def complete_json(prompt: str, *, retries: int = 2, **kw: Any) -> Any:
    """调用 LLM 并解析成 JSON；解析失败（含空输出）自动重试。

    只对 `LLMParseError` 重试，这是刻意的：
    - 空输出 / 不是 JSON：随机故障（think 吃满额度、或采样没给全），
      换次采样通常就正常。重试走的是同一条腿，不引入任何假数据——
      和"降级到规则版"性质完全相反，那条路已经删了。
    - 401 / 凭证错：重试多少次都一样，白等。
    - 超时：重试三次就是好几分钟干等，该由调用方决定重投。

    retries=2 即最多 3 次尝试。真的三次都解析不了，那是该失败，不是该兜底。
    """
    last: LLMParseError | None = None
    for attempt in range(retries + 1):
        try:
            return extract_json(complete(prompt, **kw))
        except LLMParseError as e:
            last = e
    assert last is not None
    raise last
