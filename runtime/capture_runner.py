"""
runtime.capture_runner - 需求捕获盒的 5 个 operation 实现

与 mock_runner 的区别：
- mock_runner 的 op 全是机械模拟（假装有真实引擎）
- 本模块的 extract_requirements **真调 LLM**（自然语言理解是真的）
  其余 4 个 op 是纯机械的（切句 / 编号 / 集合运算 / 字符串匹配）

**这个模块是"LLM 只做语义、机械做校验"这条边界的落地**：
真上生产时只有 extract_requirements 需要换成真实模型调用，
其余 4 个算子可以永远保持确定性。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any
from uuid import uuid4

# 业务枚举来自盒子（业务层），不从 runtime 复制一份
from boxes.requirement_capture.schemas import (
    EXTRACTORS,
    REQUIREMENT_PRIORITIES,
    REQUIREMENT_TYPES,
)


_TYPE_PREFIX = {
    "object": "obj",
    "rule": "rule",
    "process": "proc",
    "metric": "met",
    "event": "evt",
    "goal": "goal",
}

# 句末标点（不含顿号——顿号是句内枚举，不切句）
_SENT_SPLIT = re.compile(r"[。；;！!？?\n]+")
# 句内并列分隔符
_ENUM_SEP = re.compile(r"[、,，]")
# 存在性声明词：左侧是目标，右侧每项是业务事实
_EXIST_WORDS = ("有", "包含", "包括", "分为", "涵盖", "涉及")
_ENUM_MIN_LEN = 2


# ============================================================
# op 1: split_source_sentences —— 机械切句
# ============================================================


def _split_sentences(narrative: str) -> list[dict[str, Any]]:
    """自然语言 → 带 id 的句子列表（role: goal / fact）

    只做切分和角色标注，不做语义判断：
    - 首句（或首句顿号枚举的左半）= goal
    - 其余 = fact
    """
    raw = [s.strip() for s in _SENT_SPLIT.split(narrative) if s.strip()]
    if not raw:
        return []

    sentences: list[dict[str, Any]] = []

    def _add(text: str, role: str) -> None:
        if text:
            sentences.append({
                "sentence_id": f"sent_{len(sentences) + 1}",
                "text": text,
                "role": role,
            })

    head, rest = raw[0], raw[1:]

    # 首句内含顿号枚举时，目标取枚举词左侧，枚举项各自成句
    parts = [p.strip() for p in _ENUM_SEP.split(head) if p.strip()]
    parts = [p for p in parts if len(p) >= _ENUM_MIN_LEN] or parts[:1]
    if len(parts) >= 2:
        first, enum_items = parts[0], parts[1:]
        goal_text = first
        for w in _EXIST_WORDS:
            if w in first:
                goal_text = first.split(w)[0].strip() or first
                lead = first.split(w, 1)[1].strip()
                if lead:
                    enum_items = [lead, *enum_items]
                break
        _add(goal_text, "goal")
        for item in enum_items:
            _add(item, "fact")
    else:
        _add(head, "goal")

    for s in rest:
        _add(s, "fact")
    return sentences


def _simulate_source_sentence_splitter(input_data: dict[str, Any]) -> dict[str, Any]:
    narrative = input_data.get("narrative", "")
    return {
        "set_id": input_data.get("set_id") or f"req_set_nl_{uuid4().hex[:8]}",
        "business_goal": "",
        "extractor": "rule",
        "source_sentences": _split_sentences(narrative),
        "requirements": [],
        "requirement_count": 0,
        "narrative": narrative,
    }


# ============================================================
# op 2: extract_requirements —— 唯一走 LLM 的 op
# ============================================================

_EXTRACT_PROMPT = """从下面这份中文业务表述里抽取结构化业务需求。只输出 JSON，不要解释。

原文已按句子切分，每句有 sentence_id。你要做的：
1. 只抽取原文中真实存在的内容，绝对不要补充、推断或编造
2. 每条需求必须写明它来自哪一句：trace_to 填该句的 sentence_id
3. description 必须是原文中出现过的片段（可截取，不要改写）
4. role=goal 的句子是业务目标，不产出需求
5. requirement_type 只能是：object / rule / process / metric / event

输出格式：
{{"requirements": [{{"description": "原文片段", "requirement_type": "rule", "trace_to": "sent_2"}}]}}

类型含义：
- object：业务实体/对象（订单、库位、批次）
- rule：业务规则或约束（必须/不得/只有…才 / 阈值限制）
- process：业务流程或步骤（有先后顺序的动作）
- metric：业务指标（时长、数量、比率，需要被度量）
- event：已发生或应发出的业务事件（已XX / 通知 / 触发）

如果原文没有可抽取的业务事实，返回 {{"requirements": []}}

原文句子：
{sentences}"""


def _rule_extract(sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规则降级版抽取——纯关键词，只覆盖词面明显的类型"""
    from kanban.trigger import _classify_sentence

    out: list[dict[str, Any]] = []
    for s in sentences:
        if s["role"] != "fact":
            continue
        rtype = _classify_sentence(s["text"])
        if rtype == "object" and not s["text"].strip():
            continue
        out.append({
            "description": s["text"],
            "requirement_type": rtype,
            "trace_to": s["sentence_id"],
        })
    return out


def _simulate_requirement_extractor(input_data: dict[str, Any]) -> dict[str, Any]:
    """LLM 抽取（含机械后处理 + 降级）"""
    from runtime import llm
    from runtime.contract import validate_payload
    from boxes.requirement_capture.definition import get_definition

    sentences = input_data.get("source_sentences", [])
    narrative = input_data.get("narrative", "")
    goal = next((s["text"] for s in sentences if s["role"] == "goal"), "")

    result = dict(input_data)
    degrade_reason = ""

    try:
        rendered = "\n".join(
            f'{s["sentence_id"]} [{s["role"]}] {s["text"]}' for s in sentences
        )
        raw = llm.complete_json(
            _EXTRACT_PROMPT.format(sentences=rendered), max_tokens=2000
        )
        cands = raw.get("requirements") if isinstance(raw, dict) else None
        if not isinstance(cands, list):
            raise llm.LLMError(f"llm returned no requirements list: {type(raw).__name__}")

        # 只接受能溯源到真实句子、且 description 在原文中出现的
        sent_by_id = {s["sentence_id"]: s for s in sentences}
        cleaned: list[dict[str, Any]] = []
        for c in cands:
            if not isinstance(c, dict):
                continue
            sid = c.get("trace_to")
            src = sent_by_id.get(sid or "")
            if src is None:
                continue
            desc = (c.get("description") or "").strip()
            if not desc or desc not in src["text"]:
                continue
            rtype = c.get("requirement_type")
            if rtype not in REQUIREMENT_TYPES or rtype == "goal":
                rtype = "object"
            cleaned.append({
                "description": desc,
                "requirement_type": rtype,
                "trace_to": sid,
            })

        if not cleaned:
            raise llm.LLMError("llm produced no traceable requirement")

        requirements = cleaned
        extractor = "llm"
    except Exception as e:   # noqa: BLE001 — 任何 LLM 侧问题都降级，不该让投料口瘫
        requirements = _rule_extract(sentences)
        extractor = "rule"
        degrade_reason = f"{type(e).__name__}: {str(e)[:160]}"

    # 机械后处理：编号 + priority（LLM 不该决定系统标识）
    counters: Counter[str] = Counter()
    for r in requirements:
        rtype = r["requirement_type"]
        counters[rtype] += 1
        r["requirement_id"] = f"req_{_TYPE_PREFIX.get(rtype, 'obj')}_{counters[rtype]}"
        r["priority"] = "must_have"

    result.update({
        "business_goal": goal,
        "extractor": extractor,
        "requirements": requirements,
        "requirement_count": len(requirements),
        "degrade_reason": degrade_reason,
    })
    return result


# ============================================================
# op 3: build_requirement_set —— 机械组装
# ============================================================


def _simulate_requirement_set_builder(input_data: dict[str, Any]) -> dict[str, Any]:
    """组装：补 context、校验必填、保证 id 唯一"""
    result = dict(input_data)
    result.setdefault("context", "")
    result["set_id"] = result.get("set_id") or f"req_set_nl_{uuid4().hex[:8]}"
    if result.get("extractor") not in EXTRACTORS:
        result["extractor"] = "rule"
    # 去重（LLM 可能对同一句产出多条）
    seen: set[tuple[str, str]] = set()
    uniq = []
    for r in result.get("requirements", []):
        key = (r.get("trace_to", ""), (r.get("description") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    result["requirements"] = uniq
    result["requirement_count"] = len(uniq)
    return result


# ============================================================
# op 4: verify_source_fidelity —— 4 项机械 acceptance
# ============================================================


def _simulate_source_fidelity_verifier(input_data: dict[str, Any]) -> dict[str, Any]:
    """真算 4 项：契约 / 覆盖 / 溯源 / 幻觉

    全部是集合运算与字符串匹配——不需要模型，结果可复现。
    """
    from runtime.contract import validate_payload
    from boxes.requirement_capture.definition import get_definition

    sentences = input_data.get("source_sentences", [])
    requirements = input_data.get("requirements", [])
    narrative = re.sub(r"\s+", "", input_data.get("narrative", ""))

    result = dict(input_data)
    details: dict[str, Any] = {}

    # ① 契约：拿**建模盒的输入 schema** 校验
    #    schema 归消费者（建模盒）所有——捕获盒负责满足它，不自己另立一套。
    #    这正是"捕获盒换 LLM 建模盒零改动"的根据。
    from boxes.modeling import get_definition as get_modeling_definition
    contract_schema = get_modeling_definition().get_schema(
        "schema_business_requirement_set"
    )

    # ① 契约：产出的需求集是否符合建模盒的输入契约
    candidate = {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "context": input_data.get("context", ""),
        "requirements": [
            {
                "requirement_id": r.get("requirement_id", ""),
                "description": r.get("description", ""),
                "requirement_type": r.get("requirement_type", ""),
                "priority": r.get("priority", ""),
            }
            for r in requirements
        ],
    }
    contract_errors = validate_payload(candidate, contract_schema)
    result["contract_compliance"] = not contract_errors
    if contract_errors:
        details["contract_errors"] = contract_errors[:5]

    # ② 溯源：每条需求的 trace_to 指向真实句子
    sent_ids = {s["sentence_id"] for s in sentences}
    broken = [r.get("requirement_id") for r in requirements
              if r.get("trace_to") not in sent_ids]
    result["trace_integrity"] = not broken
    if broken:
        details["broken_traces"] = broken

    # ③ 幻觉：description 必须是原文片段
    hallucinated = [r.get("requirement_id") for r in requirements
                    if re.sub(r"\s+", "", r.get("description", "")) not in narrative]
    result["no_hallucination"] = not hallucinated
    if hallucinated:
        details["hallucinated"] = hallucinated

    # ④ 覆盖：每句 fact 要么被至少一条需求覆盖
    fact_ids = {s["sentence_id"] for s in sentences if s["role"] == "fact"}
    traced = {r.get("trace_to") for r in requirements}
    uncovered = sorted(fact_ids - traced)
    result["source_coverage"] = (
        round(100.0 * (len(fact_ids) - len(uncovered)) / len(fact_ids), 2)
        if fact_ids else 0.0
    )
    if uncovered:
        details["uncovered_sentences"] = uncovered

    details["degrade_reason"] = input_data.get("degrade_reason", "")
    result["evidence_detail"] = details
    return result


# ============================================================
# op 5: package_requirement_set —— 机械合并
# ============================================================


def _simulate_requirement_set_packager(input_data: dict[str, Any]) -> dict[str, Any]:
    """产出建模盒可直接消费的 RequirementSetPackage"""
    return {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "context": input_data.get("context", ""),
        "extractor": input_data.get("extractor", "rule"),
        "source_sentences": input_data.get("source_sentences", []),
        "requirements": input_data.get("requirements", []),
        "requirement_count": input_data.get("requirement_count", 0),
        "contract_compliance": input_data.get("contract_compliance", False),
        "source_coverage": input_data.get("source_coverage", 0.0),
        "trace_integrity": input_data.get("trace_integrity", False),
        "no_hallucination": input_data.get("no_hallucination", False),
        "evidence_detail": input_data.get("evidence_detail", {}),
    }


# ============================================================
# 注册
# ============================================================

CAPTURE_HANDLERS: dict[str, Any] = {
    "source_sentence_splitter": _simulate_source_sentence_splitter,
    "requirement_extractor": _simulate_requirement_extractor,
    "requirement_set_builder": _simulate_requirement_set_builder,
    "source_fidelity_verifier": _simulate_source_fidelity_verifier,
    "requirement_set_packager": _simulate_requirement_set_packager,
}
