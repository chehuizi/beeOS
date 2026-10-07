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
        "source_sentences": _split_sentences(narrative),
        "nodes": [],
        "edges": [],
        "requirements": [],
        "requirement_count": 0,
        "narrative": narrative,
    }


# ============================================================
# op 2: extract_requirements —— 唯一走 LLM 的 op
# ============================================================

_EXTRACT_PROMPT = """从下面这份中文业务表述里抽出一张**流程图**。只输出 JSON，不要解释、不要分析、不要复述原文。

业务方描述一件事，说的是流程图：节点（做什么）+ 边（走到哪）+ 守卫（什么条件下才走）。
不要把它摊平成一串需求——摊平会把"哪个约束挂在哪个动作上"这个信息弄丢。

原文已按句子切分，每句有 sentence_id。你要做的：
1. 只用原文中真实存在的内容，绝对不要补充、推断或编造
2. nodes：每个业务动作一个节点。action 写原文里出现的片段，不要改写
3. edges：把节点按原文的先后顺序串起来，from/to 用 node_id
4. 同一句里如果既有动作又有约束，动作进 nodes，约束写进**那一步之后那条边的 guard**
5. 动作产出或读入的实体写进 writes / reads；说要量的指标写进 measures
6. 每个节点和边都填 trace_to，指向它依据的那一句的 sentence_id
7. role=goal 的句子是业务目标，写进 goal，不产出节点

输出格式：
{{"goal": "首句原文片段",
 "nodes": [{{"action": "原文片段", "writes": [], "reads": [], "measures": [],
              "trace_to": "sent_2"}}],
 "edges": [{{"from": "node_1", "to": "node_2", "guard": "", "trace_to": "sent_3"}}]}}

注意：
- 没有守卫时 guard 留空字符串，不要编一个
- 没有产出实体时 writes / reads 留空数组
- measures 只在原文明确说要度量什么时才填
- 如果原文没有可抽取的业务事实，返回 {{"goal": "", "nodes": [], "edges": []}}

直接输出这段 JSON 就好，不要在 JSON 前后加任何文字。
原文句子：
{sentences}"""


def _attach_sources(
    fragments: list[str], sentences: list[dict[str, Any]]
) -> list[tuple[str, str]]:
    """把挂在节点身上的元素，逐个归到它真实出现的那一句上

    覆盖率按「每句都有图上元素指向它」算。节点 action 之外的实体 / 指标
    可能来自别的句子，不归位就会被误判成"这句没被覆盖"。
    归不到任何一句 → 是编的，返回空丢掉。
    """
    out: list[tuple[str, str]] = []
    for frag in fragments:
        for s in sentences:
            if s["role"] == "fact" and frag in s["text"]:
                out.append((frag, s["sentence_id"]))
                break
    return out


def _clean_graph(raw: Any, sentences: list[dict[str, Any]]) -> dict[str, Any]:
    """LLM 图 → 机械清洗后的图

    只收能溯源到真实句子、且文本在原文中出现过的内容——
    幻觉在这一层就被挡掉，不留到 acceptance 才报。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"llm returned {type(raw).__name__}, want dict")
    sent_by_id = {s["sentence_id"]: s for s in sentences}
    goal = next((s["text"] for s in sentences if s["role"] == "goal"), "")

    def ok_fragment(sid: Any, text: Any) -> bool:
        """文本必须是它所溯源那一句的原文子串"""
        src = sent_by_id.get(str(sid or ""))
        frag = (text or "").strip() if isinstance(text, str) else ""
        return bool(src and frag and frag in src["text"])

    def str_list(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [x.strip() for x in v if isinstance(x, str) and x.strip()]

    nodes: list[dict[str, Any]] = []
    for c in (raw.get("nodes") or []):
        if not isinstance(c, dict) or not ok_fragment(c.get("trace_to"), c.get("action")):
            continue
        writes = _attach_sources(str_list(c.get("writes")), sentences)
        measures = _attach_sources(str_list(c.get("measures")), sentences)
        reads = _attach_sources(str_list(c.get("reads")), sentences)
        nodes.append({
            "node_id": f"node_{len(nodes) + 1}",
            "action": (c["action"] or "").strip(),
            "writes": [t for t, _ in writes],
            "reads": [t for t, _ in reads],
            "measures": [t for t, _ in measures],
            "guard_sources": [],
            "measure_sources": [sid for _, sid in measures if sid],
            "trace_to": str(c["trace_to"]),
        })

    if not nodes:
        # 区分两件都表现为"抽不出东西"的事：
        #   原文压根没有业务事实（只有一句业务目标）→ 盒子声明了
        #     no_deliver 例外，产出空需求集，交给判据拒。这是正常的业务结果。
        #   原文有事实、但模型产出全被幻觉校验剔掉 → 模型没履约，中止。
        # 降级删掉之前这两种都被 except 吞成规则版，no_deliver 也就跟着失效了。
        has_facts = any(s["role"] == "fact" and s["text"].strip() for s in sentences)
        if not has_facts:
            return {"goal": goal, "nodes": [], "edges": []}
        raise ValueError("llm produced no traceable node")

    node_ids = {n["node_id"] for n in nodes}
    edges: list[dict[str, Any]] = []
    for c in (raw.get("edges") or []):
        if not isinstance(c, dict):
            continue
        frm, to = c.get("from"), c.get("to")
        # 边的两端必须指向真实存在的节点——悬空边会让"按位置定类型"失效
        if frm not in node_ids or to not in node_ids or frm == to:
            continue
        guard = (c.get("guard") or "").strip() if isinstance(c.get("guard"), str) else ""
        trigger = (c.get("trigger") or "").strip() if isinstance(c.get("trigger"), str) else ""
        trace = c.get("trace_to")
        # 守卫 / 触发词同样要能在原文里找到，否则视为编造
        if guard and not ok_fragment(trace, guard):
            guard = ""
        if trigger and not ok_fragment(trace, trigger):
            trigger = ""
        edges.append({
            "edge_id": f"edge_{len(edges) + 1}",
            "from": frm,
            "to": to,
            "guard": guard,
            "trigger": trigger,
            "trace_to": str(trace) if trace else "",
        })

    # 原文明写先后顺序而模型漏了边：补成链。图断成一堆孤点就没用了
    if not edges and len(nodes) > 1:
        for a, b in zip(nodes, nodes[1:]):
            edges.append({
                "edge_id": f"edge_{len(edges) + 1}",
                "from": a["node_id"], "to": b["node_id"],
                "guard": "", "trigger": "", "trace_to": b["trace_to"],
            })

    return {"goal": goal, "nodes": nodes, "edges": edges}


def _graph_to_requirements(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """流程图 → 建模盒要的平铺列表（兼容投影，零业务判断）

    这一步纯粹是把图摊平给还没升级的建模盒——它按 requirement_type 分组。
    摊平后的 type 是**由位置推出来的**，不是模型猜的：
      node          → process
      node.writes   → object
      node.measures → metric
      edge.guard    → rule
      edge.trigger  → event
    建模盒升级成直接吃图之后，这个函数就退休。
    """
    out: list[dict[str, Any]] = []
    for n in graph.get("nodes", []):
        trace = n.get("trace_to", "")
        guard_traces = n.get("guard_sources") or []
        measure_traces = n.get("measure_sources") or []
        if n.get("guard"):
            out.append({
                "description": n["guard"], "requirement_type": "rule",
                "trace_to": guard_traces[0] if guard_traces else trace,
            })
        out.append({
            "description": n["action"],
            "requirement_type": "process",
            "trace_to": trace,
        })
        for w in n.get("writes", []):
            out.append({"description": w, "requirement_type": "object", "trace_to": trace})
        for r in n.get("reads", []):
            out.append({"description": r, "requirement_type": "object", "trace_to": trace})
        for i, m in enumerate(n.get("measures", [])):
            out.append({
                "description": m, "requirement_type": "metric",
                "trace_to": measure_traces[i] if i < len(measure_traces) else trace,
            })
    for e in graph.get("edges", []):
        trace = e.get("trace_to", "")
        if e.get("guard"):
            out.append({"description": e["guard"], "requirement_type": "rule", "trace_to": trace})
        if e.get("trigger"):
            out.append({"description": e["trigger"], "requirement_type": "event", "trace_to": trace})
    return out


def _simulate_requirement_extractor(input_data: dict[str, Any]) -> dict[str, Any]:
    """LLM 抽图 + 机械后处理。LLM 不可用就抛，不降级。

    这里曾经有个 except 兜底：任何 LLM 侧问题都退成 `_rule_extract` 的
    词面分类。看着是「投料口不瘫」，实际是开了条绕过质量门禁的通道——
    词面版语义最差，却因为逐句切分不丢句子、每句都挂 trace_to，
    4 条判据（契约/覆盖率/追溯/无幻觉，测的都不是语义质量）必然全过，
    于是拿绿灯、被放行下游。降级不是兜底，是假信号。
    所以这里让异常往上走：executor 会记原因并把 TaskRun 置 FAILED。
    """
    from runtime import llm

    sentences = input_data.get("source_sentences", [])
    goal = next((s["text"] for s in sentences if s["role"] == "goal"), "")

    rendered = "\n".join(
        f'{s["sentence_id"]} [{s["role"]}] {s["text"]}' for s in sentences
    )
    raw = llm.complete_json(
        _EXTRACT_PROMPT.format(sentences=rendered), max_tokens=8000
    )
    graph = _clean_graph(raw, sentences)

    # 图 → 兼容平铺列表，编号按 type 分别计数（建模盒按 type 分组）
    requirements = _graph_to_requirements(graph)
    counters: Counter[str] = Counter()
    for r in requirements:
        rtype = r["requirement_type"]
        counters[rtype] += 1
        r["requirement_id"] = f"req_{_TYPE_PREFIX.get(rtype, 'obj')}_{counters[rtype]}"
        r["priority"] = "must_have"

    result = dict(input_data)
    result.update({
        "business_goal": goal,
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "requirements": requirements,
        "requirement_count": len(requirements),
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
# op 4: verify_graph_fidelity —— 4 项机械 acceptance
# ============================================================


def _simulate_graph_fidelity_verifier(input_data: dict[str, Any]) -> dict[str, Any]:
    """真算 4 项：契约 / 覆盖 / 溯源 / 幻觉

    全部是集合运算与字符串匹配——不需要模型，结果可复现。
    """
    from runtime.contract import validate_payload
    from boxes.requirement_capture.definition import get_definition

    sentences = input_data.get("source_sentences", [])
    requirements = input_data.get("requirements", [])
    nodes = input_data.get("nodes", [])
    edges = input_data.get("edges", [])
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

    # ② 溯源：图上每个节点/边都要指向真实句子，边两端要指向真实节点
    #    平铺列表也查一遍——它是图的投影，不该比图更可信
    sent_ids = {s["sentence_id"] for s in sentences}
    node_ids = {n["node_id"] for n in nodes}
    broken = [n["node_id"] for n in nodes if n.get("trace_to") not in sent_ids]
    broken += [e["edge_id"] for e in edges if e.get("trace_to") not in sent_ids]
    broken += [e["edge_id"] for e in edges
               if e.get("from") not in node_ids or e.get("to") not in node_ids]
    broken += [r.get("requirement_id") for r in requirements
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

    # ④ 覆盖：每句 fact 都要在图上留下痕迹
    #    节点 action / 节点 guard / 节点 measures / 边 guard / 边 trigger
    #    都被原文的某一句支撑——挂在节点身上的也要单独记账，
    #    否则「一句约束 + 一个动作」这种写法会被误判成没覆盖
    fact_ids = {s["sentence_id"] for s in sentences if s["role"] == "fact"}
    traced: set[Any] = set()
    for n in nodes:
        traced.add(n.get("trace_to"))
        traced |= set(n.get("guard_sources", []))
        traced |= set(n.get("measure_sources", []))
    for e in edges:
        traced.add(e.get("trace_to"))
    uncovered = sorted(fact_ids - traced)
    result["source_coverage"] = (
        round(100.0 * (len(fact_ids) - len(uncovered)) / len(fact_ids), 2)
        if fact_ids else 0.0
    )
    if uncovered:
        details["uncovered_sentences"] = uncovered

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
        "source_sentences": input_data.get("source_sentences", []),
        "nodes": input_data.get("nodes", []),
        "edges": input_data.get("edges", []),
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
    "graph_fidelity_verifier": _simulate_graph_fidelity_verifier,
    "requirement_set_packager": _simulate_requirement_set_packager,
}
