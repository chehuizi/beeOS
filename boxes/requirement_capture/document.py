"""
boxes.requirement_capture.document - 把需求集机械投影成**人读的需求文档**

**为什么要有这一层**：需求盒的产出是给建模盒吃的——流程图 + 可判定的
结构化字段。那是机器的形状，不是人的形状。业务方要看的是一份文档：
背景、目标、一条条编号的需求、每条出自哪句原文、在流程的哪个位置。

**这一层是投影，不是新产出**：
- 条目来自**流程图**（节点 / 守卫 / 指标 / 事件），不是平铺列表——
  平铺列表把一句话拆成碎片（「库存看板」单独成条），那是给建模盒的
  兼容投影，拿它写文档等于把碎片写进文档。
- 每条都带**原文出处**（逐字引自 source_sentences）和**流程位置**。
- **不新增任何事实**：文档里出现的每一句业务话，都必须逐字来自原文。
  这条由 tests/test_requirement_document.py 守着。

**为什么验收标准是占位**：需求盒只回答「业务方要什么」，
不回答「怎么算满足」——那是建模盒绑定 WMS 概念后产出的**可判定预期**，
测试盒去验它。在需求盒这里编一条验收标准，就是编。

**优先级也要说实话**：抽取层目前把所有需求统一标成 `must_have`
（它还没有区分 must/should 的依据）。文档照实写，并在未决项里点出来——
写一个看起来有区分度、其实没有的优先级，比不写更坏。
"""

from __future__ import annotations

from typing import Any

# 验收标准占位（固定文案，不编内容）
ACCEPTANCE_PLACEHOLDER = (
    "待定：由建模盒绑定 WMS 概念后产出可判定预期，测试盒按它验"
)

# 条目类型（**由结构推出**，不由模型猜）
TYPE_STEP = "流程步骤"
TYPE_CONSTRAINT = "约束"
TYPE_METRIC = "指标"
TYPE_EVENT = "事件"


def _sentences_by_id(sentences: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(s.get("sentence_id")): str(s.get("text") or "")
        for s in sentences
        if isinstance(s, dict)
    }


def _flat_index(requirements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """平铺列表按描述建索引（用来把文档条目挂回它的 requirement_id）"""
    index: dict[str, list[dict[str, Any]]] = {}
    for r in requirements:
        if not isinstance(r, dict):
            continue
        desc = str(r.get("description") or "").strip()
        if not desc:
            continue
        index.setdefault(desc, []).append(r)
    return index


def build_document(
    *,
    set_id: str,
    goal: str,
    context: str,
    sentences: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成需求文档（机械投影）"""
    text_by_id = _sentences_by_id(sentences)
    flat = _flat_index(requirements)

    items: list[dict[str, Any]] = []

    def _add(item_type: str, description: str, trace_ids: list[str],
             position: str, outputs: list[str] | None = None,
             inputs: list[str] | None = None) -> None:
        desc = (description or "").strip()
        if not desc:
            return
        traces = [t for t in trace_ids if t]
        # 去重但保序
        seen: set[str] = set()
        traces = [t for t in traces if not (t in seen or seen.add(t))]
        linked = flat.get(desc, [])
        items.append({
            "doc_id": f"REQ-{len(items) + 1:03d}",
            "item_type": item_type,
            "description": desc,
            "priority": (linked[0].get("priority") if linked else "") or "",
            "source_sentence_ids": traces,
            "source_quote": " / ".join(
                text_by_id[t] for t in traces if t in text_by_id
            ),
            "flow_position": position,
            "linked_requirement_ids": [
                str(r.get("requirement_id")) for r in linked if r.get("requirement_id")
            ],
            "acceptance_criteria": [ACCEPTANCE_PLACEHOLDER],
            "outputs": outputs or [],
            "inputs": inputs or [],
        })

    # ---- 流程步骤（主结构）----
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or "")
        action = str(node.get("action") or "")
        trace = str(node.get("trace_to") or "")
        _add(
            TYPE_STEP, action, [trace],
            f"节点 {node_id}",
            outputs=[str(w) for w in (node.get("writes") or [])],
            inputs=[str(r) for r in (node.get("reads") or [])],
        )
        # 节点自带的守卫（单节点图里没有边可挂，守卫就挂在节点上）
        guard = str(node.get("guard") or "")
        if guard:
            _add(
                TYPE_CONSTRAINT, guard,
                [str(s) for s in (node.get("guard_sources") or [])] or [trace],
                f"节点 {node_id} 的前置条件",
            )
        # 指标（可判定的东西，单独成条）
        measures = [str(m) for m in (node.get("measures") or [])]
        measure_sources = [str(s) for s in (node.get("measure_sources") or [])]
        for i, m in enumerate(measures):
            src = measure_sources[i] if i < len(measure_sources) else trace
            _add(TYPE_METRIC, m, [src], f"节点 {node_id} 产出的度量")

    # ---- 边上的守卫 / 触发 ----
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        pos = f"边 {edge.get('from')} → {edge.get('to')}"
        trace = str(edge.get("trace_to") or "")
        guard = str(edge.get("guard") or "")
        if guard:
            _add(TYPE_CONSTRAINT, guard, [trace], f"{pos} 的守卫")
        trigger = str(edge.get("trigger") or "")
        if trigger:
            _add(TYPE_EVENT, trigger, [trace], f"{pos} 的触发")

    # ---- 追溯矩阵：每句原文被哪些条目认领 ----
    claimed_by: dict[str, list[str]] = {}
    for item in items:
        for sid in item["source_sentence_ids"]:
            claimed_by.setdefault(sid, []).append(item["doc_id"])

    matrix: list[dict[str, Any]] = []
    unclaimed: list[str] = []
    for s in sentences:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("sentence_id") or "")
        role = str(s.get("role") or "")
        owners = claimed_by.get(sid, [])
        if role in ("title", "context"):
            status = "非内容句（不进需求）"
        elif owners:
            status = "已认领"
        else:
            status = "未被认领（这句没被任何需求条目覆盖）"
            unclaimed.append(sid)
        matrix.append({
            "sentence_id": sid,
            "text": str(s.get("text") or ""),
            "role": role,
            "claimed_by": owners,
            "status": status,
        })

    doc = {
        "title": goal or set_id,
        "goal": goal,
        "background": context,
        "set_id": set_id,
        "items": items,
        "trace_matrix": matrix,
        "unclaimed_sentence_ids": unclaimed,
    }
    doc["markdown"] = render_markdown(doc)
    return doc


def render_markdown(doc: dict[str, Any]) -> str:
    """把文档渲染成 markdown（给人读的那一份）"""
    lines: list[str] = []
    title = str(doc.get("title") or doc.get("set_id") or "需求文档")
    lines.append(f"# 需求文档：{title}")
    lines.append("")

    lines.append("## 1. 背景与目标")
    lines.append("")
    lines.append(f"- 业务目标：{doc.get('goal') or '（业务方没单独说目标）'}")
    if doc.get("background"):
        lines.append(f"- 背景：{doc['background']}")
    lines.append(f"- 需求集编号：{doc.get('set_id') or '—'}")
    lines.append(f"- 需求条目数：{len(doc.get('items') or [])}")
    lines.append(f"- 原文句数：{len(doc.get('trace_matrix') or [])}")
    lines.append("")

    lines.append("## 2. 需求条目")
    lines.append("")
    items = doc.get("items") or []
    if not items:
        lines.append("（没有抽出任何需求条目）")
    for item in items:
        lines.append(f"### {item['doc_id']}　{item['item_type']}：{item['description']}")
        lines.append("")
        if item.get("source_quote"):
            lines.append(f"- 原文出处：{item['source_quote']}")
        else:
            lines.append("- 原文出处：（未挂到原文句）")
        if item.get("flow_position"):
            lines.append(f"- 流程位置：{item['flow_position']}")
        if item.get("outputs"):
            lines.append(f"- 产出：{'、'.join(item['outputs'])}")
        if item.get("inputs"):
            lines.append(f"- 读取：{'、'.join(item['inputs'])}")
        if item.get("priority"):
            lines.append(f"- 优先级：{item['priority']}")
        if item.get("linked_requirement_ids"):
            lines.append(f"- 关联条目：{'、'.join(item['linked_requirement_ids'])}")
        lines.append(f"- 验收标准：{'；'.join(item.get('acceptance_criteria') or [])}")
        lines.append("")

    lines.append("## 3. 需求 ↔ 原文追溯矩阵")
    lines.append("")
    lines.append("| 原文句 | 角色 | 被哪些条目认领 | 状态 |")
    lines.append("|---|---|---|---|")
    for row in doc.get("trace_matrix") or []:
        owners = "、".join(row.get("claimed_by") or []) or "—"
        lines.append(
            f"| {row.get('sentence_id')} {row.get('text')} | {row.get('role')} "
            f"| {owners} | {row.get('status')} |"
        )
    lines.append("")

    lines.append("## 4. 未决项")
    lines.append("")
    lines.append(
        "- 验收标准：本需求盒不定——它只回答「业务方要什么」，"
        "「怎么算满足」由建模盒绑定 WMS 概念后产出可判定预期、测试盒去验"
    )
    lines.append(
        "- 优先级：抽取层目前把每条都标成 `must_have`，**尚无区分度**——"
        "文档照实写，不假装它分得清"
    )
    if doc.get("unclaimed_sentence_ids"):
        lines.append(
            f"- 没被任何条目认领的原文句：{'、'.join(doc['unclaimed_sentence_ids'])}"
            "（这些句子目前没有对应的需求条目）"
        )
    else:
        lines.append("- 没被任何条目认领的原文句：无")
    lines.append("")
    return "\n".join(lines)
