"""
runtime.llm - LLM 调用客户端（最小可替换实现）

定位：
- 这是 beeOS 的基础设施，不是业务语义——runtime 只知道"调一个模型"，
  不知道被建模的领域是什么
- 业务提示词不属于本模块：它由调用方（结构化器）传入，本模块只管传输与解析

为什么不直接用 HTTP 手搓：
- 端点 / 协议 / 凭证都在 ~/.minimax/config.yaml 里（provider + model 引用），
  本模块只依赖这一个契约，换 provider = 改配置，不改代码

模型返回的两种杂质（实测 MiniMax-M3）：
1. <think>...</think> 推理段
2. ```json ... ``` markdown 包裹
两者都要剥掉才能拿到裸 JSON。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# ~/.minimax/.builtin-skills/llm-call/scripts/llm_call.py
_LLM_CALL_SCRIPT = (
    Path.home() / ".minimax/.builtin-skills/llm-call/scripts/llm_call.py"
)

# ~/.minimax/config.yaml —— 注意：这是 MCode runtime 拥有的文件，它会重写
# （实测引号风格被 YAML 库规范化写回），所以我们不假设里面的值是稳定的。
_CONFIG_PATH = Path.home() / ".minimax/config.yaml"

# 覆盖凭证的环境变量：runtime 改写 config.yaml 时不至于把 LLM 腿打断
_ENV_KEY = "BEEOS_LLM_API_KEY"
_ENV_BASE_URL = "BEEOS_LLM_BASE_URL"
# 协议：@ai-sdk/openai 走 /chat/completions + Bearer，@ai-sdk/anthropic 走
# /messages + x-api-key。配错协议会得到 401，而不是一个能看懂的报错——实测
# 内部代理只认前者，所以把它也纳入覆盖范围。
_ENV_NPM = "BEEOS_LLM_NPM"

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


def _resolve_config_path() -> tuple[Path, Any]:
    """决定传给 llm_call.py 的 config 路径

    - 有 env 覆盖 → 写一份派生 config 到临时目录（凭证不入库、不回显、随用随删）
    - 没有        → 用 ~/.minimax/config.yaml，但先做占位符预检

    返回 (config 路径, 需要清理的临时目录 or None)
    """
    env_key = (os.environ.get(_ENV_KEY) or "").strip()
    env_base = (os.environ.get(_ENV_BASE_URL) or "").strip()
    env_npm = (os.environ.get(_ENV_NPM) or "").strip()

    if not env_key and not env_base and not env_npm:
        key = str(_provider_options(_read_config()).get("apiKey") or "").strip()
        if _is_placeholder(key):
            raise LLMError(
                "no usable LLM credential: "
                f"{_CONFIG_PATH} provider.minimax.options.apiKey is a placeholder. "
                f"Set a real key in that file, or export {_ENV_KEY}=<key> "
                "before starting beeOS (and restart the web server)."
            )
        return _CONFIG_PATH, None

    config = _read_config()
    options = dict(_provider_options(config))
    if env_key:
        if _is_placeholder(env_key):
            raise LLMError(
                f"{_ENV_KEY} is set to a placeholder value, not a real key. "
                "Export the actual API key, or clear it to fall back to "
                f"{_CONFIG_PATH}."
            )
        options["apiKey"] = env_key
    if env_base:
        options["baseURL"] = env_base
    if _is_placeholder(str(options.get("apiKey") or "")):
        raise LLMError("resolved LLM apiKey is empty or a placeholder")

    tmpdir = Path(tempfile.mkdtemp(prefix="beeos-llm-"))
    try:
        (tmpdir / "config.yaml").write_text(_dump_yaml(config, options, env_npm))
        os.chmod(tmpdir / "config.yaml", 0o600)
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"failed to write derived llm config: {e}") from e
    return tmpdir / "config.yaml", tmpdir


def _dump_yaml(
    config: dict[str, Any], options: dict[str, Any], npm: str = ""
) -> str:
    import yaml
    merged = dict(config)
    provider = dict(merged.get("provider") or {})
    minimax = dict(provider.get("minimax") or {})
    if npm:
        minimax["npm"] = npm
    minimax["options"] = options
    provider["minimax"] = minimax
    merged["provider"] = provider
    return yaml.safe_dump(merged, allow_unicode=True)


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


def complete(
    prompt: str,
    *,
    model: str = "minimax/MiniMax-M3",
    max_tokens: int = 2000,
    timeout: float = 240.0,
) -> str:
    """调用 LLM，返回原始文本（未剥杂质）

    timeout 默认 240s 不是保守，是实测出来的：MiniMax-M3 是推理模型，
    抽取那条腿 prompt 只有 ~1k 字符，但 max_tokens=8000 会被吃满，
    实测输出 28k 字符（大部分是 <think> 段）、耗时 118s。
    原来的 120s 上限只剩 2 秒余量 —— 输入稍长就必然超时降级，
    而超时会静默退成规则版抽取（capture_runner 捕所有异常）。
    这是治标：真该做的是压输出，那是另一轮的事。
    """
    if not _LLM_CALL_SCRIPT.exists():
        raise LLMError(f"llm_call.py not found at {_LLM_CALL_SCRIPT}")

    config_path, tmpdir = _resolve_config_path()
    cmd = [
        sys.executable,
        str(_LLM_CALL_SCRIPT),
        "--config", str(config_path),
        "--model", model,
        "--max-tokens", str(max_tokens),
        "--timeout", str(int(timeout)),
        "--prompt", prompt,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 15
        )
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"llm call timed out after {timeout}s") from e
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)

    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise LLMError(
            f"llm call failed (exit {proc.returncode}): "
            f"{_readable_error(proc.stderr or out)}"
        )
    if not out:
        # 整轮空输出和"输出了但解析不了"是同一类随机故障，同样可重试
        raise LLMParseError("llm returned empty output")
    return out


def complete_json(prompt: str, *, retries: int = 2, **kw: Any) -> Any:
    """调用 LLM 并解析成 JSON；解析失败（含空输出）自动重试。

    只对 `LLMParseError` 重试，这是刻意的：
    - 空输出 / 不是 JSON：模型思考吃满了 max_tokens，JSON 没轮到输出。
      换次采样通常就正常，重试走的是同一条腿，不引入任何假数据——
      和"降级到规则版"性质完全相反，那条路已经删了。
    - 401 / 凭证错：重试多少次都一样，白等。
    - 超时：单次 240 秒，重试三次就是 12 分钟干等，该由调用方决定重投。

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
