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
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ~/.minimax/.builtin-skills/llm-call/scripts/llm_call.py
_LLM_CALL_SCRIPT = (
    Path.home() / ".minimax/.builtin-skills/llm-call/scripts/llm_call.py"
)

# 推理段（<think> / <think> 变体）
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# markdown 代码块围栏：```json ... ``` 或 ``` ... ```
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    """LLM 调用失败（端点错 / 凭证错 / 超时 / 输出无法解析为 JSON）"""


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
    raise LLMError(f"model output is not valid JSON: {cleaned[:200]!r}")


def complete(
    prompt: str,
    *,
    model: str = "minimax/MiniMax-M3",
    max_tokens: int = 2000,
    timeout: float = 120.0,
) -> str:
    """调用 LLM，返回原始文本（未剥杂质）"""
    if not _LLM_CALL_SCRIPT.exists():
        raise LLMError(f"llm_call.py not found at {_LLM_CALL_SCRIPT}")

    cmd = [
        sys.executable,
        str(_LLM_CALL_SCRIPT),
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

    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        err = (proc.stderr or out).strip()[:300]
        raise LLMError(f"llm call failed (exit {proc.returncode}): {err}")
    if not out:
        raise LLMError("llm returned empty output")
    return out


def complete_json(prompt: str, **kw: Any) -> Any:
    """调用 LLM 并直接返回解析后的 JSON"""
    return extract_json(complete(prompt, **kw))
