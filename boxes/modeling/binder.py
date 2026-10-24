"""
boxes.modeling.binder - 把需求绑到 WMS 概念上（**LLM 做语义，机械做校验**）

**为什么要换掉关键词匹配**

原来的绑定器是纯关键词的。它认不出的不是「罕见说法」，而是**最普通的说法**：

    '同一 SKU 出库按效期升序分配'   → None
    '同一 SKU 必须先出快过期的批次' → None   ← 业务方的原话
    '出库按批次失效日期升序取货'   → None   ← 连 fefo 自己的 statement 都绑不上

（绑定只认 keywords，statement 是给人看的——于是词表和 statement 长期漂移。）

结果就是**主线需求在第 2 只盒子就断了**：绑不上约束 → `wms_rule_binding` 判红
+ 覆盖率掉到 50% → 7 条判据全红。而判红本身还是设计盒「宁可红，不要假装
绑上了」的正确行为——问题不在判据，在绑定的能力。

**为什么是 LLM**

「这句话讲的是不是效期先出」是**语义问题**，不是查表问题。
LLM 只做这一件事（把需求归到哪个概念），机械层做全部校验：

1. `kind` 必须**逐字**来自本盒的 WMS 概念表 → 自造的概念 id = 幻觉，作废
2. `evidence` 必须**逐字**出现在该条需求的描述里 → 改写过的不算，作废
3. 认不出就是认不出（空列表）→ 落到 `wms_binding` 的红里，**不猜**

**不降级**：LLM 不可用就是履约中止。降级到关键词匹配看起来「总有结果」，
但那正是这个模块存在的理由——关键词匹配就是那个不够用的东西。
"""

from __future__ import annotations

import json
from typing import Any

from boxes.modeling.domain import (
    WMS_CONSTRAINTS,
    WMS_ENTITIES,
    WMS_METRICS,
    WMS_REPORTS,
)

# ============================================================
# 概念目录：给 LLM 看的候选集（**只有这个，没有别的**）
# ============================================================
#
# 需求类型 → 概念类别（**只是给 LLM 的提示，不是限制**）
#
# 需求盒的 requirement_type 是**位置**推出来的（图上的节点→process、
# 边的守卫→rule、节点的 measures→metric），不是语义判断。
#
# 真 LLM 实测过：业务方说「同一个 SKU 必须先出快过期的批次」，需求盒把它
# 放在节点上（类型 = process），而设计盒正确地认出了 `fefo`。
# **拿位置标签去否决语义判断，等于把对的答案改成错的**——所以这里只提示，
# 不限制。概念类别由**概念本身**决定（见 domain.kind_category）。

_TYPE_HINT: dict[str, str] = {
    "object": "实体",
    "process": "流程",
    "event": "事件",
    "rule": "规则",
    "metric": "指标",
}


def _catalog_for(category: str) -> list[dict[str, Any]]:
    """某个类别下的概念目录"""
    if category == "entity":
        return [
            {
                "kind": kind,
                "label": spec["label"],
                "statement": spec.get("statement", spec["label"]),
                "examples": spec["keywords"],
            }
            for kind, spec in WMS_ENTITIES.items()
        ]
    if category == "constraint":
        return [
            {
                "kind": kind,
                "label": spec["label"],
                "statement": spec["statement"],
                "examples": spec["keywords"],
            }
            for kind, spec in WMS_CONSTRAINTS.items()
        ]
    if category == "report":
        return [
            {
                "kind": kind,
                "label": spec["label"],
                "statement": spec["statement"],
                "examples": spec["keywords"],
            }
            for kind, spec in WMS_REPORTS.items()
        ]
    if category == "metric":
        # **例词来自词表里的 keywords**，不是只有标签和 id。
        # 业务方说的是「临期 30 天内 SKU」，标签是「临期 SKU 数」——
        # 不是同一串字。目录里没有例词，LLM 就看不见这个指标，
        # 绑不上 → `metrics_defined` 判红（真 LLM 实测：同一批需求，
        # 一次绑上指标一次没绑上——波动来自目录信息不全，不是模型时好时坏）。
        return [
            {
                "kind": m["name"],
                "label": m["label"],
                "statement": m["definition"],
                "examples": [*m["keywords"], m["label"], m["name"]],
            }
            for m in WMS_METRICS
        ]
    return []


def _kind_exists(kind: str) -> bool:
    """这个概念 id 真的在 WMS 概念表里吗

    **WMS_METRICS 是元组不是字典**——`kind in WMS_METRICS` 是在一堆 dict 里
    找字符串，永远是 False，指标名会被误判成「自造的概念」。
    （这个坑踩过一次：5 条需求里唯一绑不上的就是那条指标需求。）
    """
    return (
        kind in WMS_ENTITIES
        or kind in WMS_CONSTRAINTS
        or kind in WMS_REPORTS
        or any(m["name"] == kind for m in WMS_METRICS)
    )


_CATEGORY_LABEL = {
    "entity": "实体", "constraint": "约束", "metric": "指标", "report": "报表",
}


# ============================================================
# prompt
# ============================================================

_BIND_PROMPT = """你是 WMS（仓储管理）领域建模助手。下面每条业务需求，请判断它对应下列 WMS 概念里的哪一个或哪几个。

可用概念（**只能从这里选，不要自造 id**）：

{catalog}

需求列表：
{requirements}

输出格式（只输出 JSON，不要解释、不要复述）：

{{"bindings": [
  {{"requirement_id": "req_obj_1",
    "kinds": [{{"kind": "sku", "evidence": "按 SKU 和库位分别记账"}}]}}
]}}

规则：
0. 每条需求后面的「位置」只是**参考**，不是限制——你要判断的是
   「这句话讲的是哪个 WMS 概念」，不是「它出现在流程图的哪个位置」。
   一句被放在流程里的规则，照样可以是一条 WMS 约束
1. `kind` 必须逐字来自上面的概念 id。**自造 id 会被判为幻觉，作废**
2. `evidence` 必须**逐字**出现在那条需求的描述里——不能改写、不能翻译、不能补充说明
3. 一条需求可能同时对应多个概念（比如一句话里藏了两件事）
4. 真的对不上就给空列表 `[]`。**宁可空着，也不要硬凑**——空列表会被判据打回，
   那是正常结果；硬凑出来的绑定会让模型说它没说的话
5. 需求列表里每一条都要在输出里出现（对不上就给空列表），不要漏"""


def _build_prompt(requirements: list[dict[str, Any]]) -> str:
    catalog_lines: list[str] = []
    for category in ("entity", "constraint", "metric", "report"):
        items = _catalog_for(category)
        if not items:
            continue
        catalog_lines.append(f"[{_CATEGORY_LABEL[category]}]")
        for c in items:
            catalog_lines.append(
                f"- {c['kind']}（{c['label']}）：{c['statement']}"
                f"  例：{'、'.join(c['examples'])}"
            )
    catalog = "\n".join(catalog_lines)

    req_lines = []
    for r in requirements:
        rtype = r.get("requirement_type", "")
        req_lines.append(
            f"- id={r.get('requirement_id', '')} "
            f"type={rtype}（它在业务流里的位置：{_TYPE_HINT.get(rtype, rtype)}） "
            f"描述：{r.get('description', '')}"
        )
    return _BIND_PROMPT.format(catalog=catalog, requirements="\n".join(req_lines))


# ============================================================
# 对外
# ============================================================


class BindingError(ValueError):
    """绑定这一步本身没法做（LLM 不可用 / 返回的不是 JSON）→ 履约中止"""


def _metric_examples(kind: str) -> list[str]:
    """一个 WMS 指标在业务方嘴里怎么说（词表里的例词）"""
    for m in WMS_METRICS:
        if m["name"] == kind:
            return list(m["keywords"])
    return []


def _complete_report_metrics(
    bindings: dict[str, list[dict[str, str]]],
    requirements: list[dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    """报表绑定 → 补上「这张看板承载的那个被点名的数」

    **为什么需要这一步**：LLM 认得出「库存看板」，但有时认不出
    看板上报的那个被点名的量（「临期 30 天内 SKU」）——真 LLM 实测
    同一批需求连跑 4 次，漏了 1 次。漏了的后果不是好不好看的问题：
    那条指标不会变成**可判定的预期**，测试盒就不会去验它，
    **业务价值根本没被验证**。

    **这不是「机械猜概念」**：补的方向来自词表里声明的关系
    （报表承载哪些指标——WMS 的事实，线上 dashboard 真的报这个数），
    而且补上的证据仍然必须**逐字**出现在需求描述里，
    跟 LLM 绑定的证据规则完全一样。原文没提那个数，就补不上——
    证据这道闸是机械的，补出来的绑定不可能是幻觉。
    """
    by_id = {r.get("requirement_id"): r for r in requirements}
    out = {rid: list(bs) for rid, bs in bindings.items()}
    for rid, bs in list(out.items()):
        req = by_id.get(rid) or {}
        description = str(req.get("description") or "")
        if not description:
            continue
        for b in bs:
            report = WMS_REPORTS.get(b.get("kind", ""))
            if not report:
                continue
            for metric_kind in report.get("carries_metrics", []):
                if any(x["kind"] == metric_kind for x in bs):
                    continue
                # 证据 = 指标例词里**最长**的那个出现在需求描述里的
                # （「临期 30 天」比「临期」更像证据）
                hits = [kw for kw in _metric_examples(metric_kind) if kw in description]
                if not hits:
                    continue
                bs.append({"kind": metric_kind, "evidence": max(hits, key=len)})
    return out


def bind_requirements(
    requirements: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    """把一批需求绑到 WMS 概念上

    Args:
        requirements: [{requirement_id, requirement_type, description}, ...]

    Returns:
        (bindings, errors)
        - bindings: {requirement_id: [{"kind":..., "evidence":...}, ...]}
          **只含通过机械校验的绑定**
        - errors: [{"requirement_id":..., "reason":...}] —— 作废的绑定及原因

    Raises:
        BindingError: LLM 不可用 / 返回的不是 JSON / 结构不对。
            **中止，不降级**——降级到关键词匹配就是退回那个不够用的东西。
    """
    if not requirements:
        return {}, []

    from runtime import llm

    raw = llm.complete_json(
        _build_prompt(requirements),
        max_tokens=4000,
        thinking="disabled",
    )
    if not isinstance(raw, dict):
        raise BindingError(
            f"llm returned {type(raw).__name__}, want dict（绑定要的是结构化结果）"
        )
    entries = raw.get("bindings")
    if not isinstance(entries, list):
        raise BindingError("llm output has no 'bindings' list")

    by_id = {r.get("requirement_id"): r for r in requirements}
    bindings: dict[str, list[dict[str, str]]] = {}
    errors: list[dict[str, str]] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rid = str(entry.get("requirement_id") or "")
        req = by_id.get(rid)
        if req is None:
            # LLM 编了一个不存在的需求 id → 幻觉，丢掉（不点名到某条需求上）
            continue
        description = str(req.get("description") or "")

        kinds = entry.get("kinds")
        if kinds is None:
            kinds = []
        if not isinstance(kinds, list):
            errors.append({"requirement_id": rid, "reason": "kinds 不是列表"})
            continue

        kept: list[dict[str, str]] = []
        for k in kinds:
            if not isinstance(k, dict):
                continue
            kind = str(k.get("kind") or "")
            evidence = str(k.get("evidence") or "")
            if not kind:
                continue
            if not _kind_exists(kind):
                errors.append({
                    "requirement_id": rid,
                    "reason": f"概念 {kind!r} 不在 WMS 概念表里（自造的概念）",
                })
                continue
            if not evidence or evidence not in description:
                errors.append({
                    "requirement_id": rid,
                    "reason": f"证据 {evidence!r} 不是需求描述里的原文",
                })
                continue
            if any(x["kind"] == kind for x in kept):
                continue
            kept.append({"kind": kind, "evidence": evidence})
        if kept:
            bindings.setdefault(rid, []).extend(kept)

    # 报表 → 它承载的那个数（域事实驱动，证据仍要在原文里）
    return _complete_report_metrics(bindings, requirements), errors
