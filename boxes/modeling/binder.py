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
)

# ============================================================
# 概念目录：给 LLM 看的候选集（**只有这个，没有别的**）
# ============================================================
#
# 需求类型 → 允许的概念类别。**类别由声明的 requirement_type 定，不由 LLM 选**：
# 一条 rule 只能绑约束，一条 metric 只能绑指标。
# 这是机械结构——类型是声明的，就不该让模型去猜它属于哪一类。

_TYPE_CATEGORY: dict[str, str] = {
    "object": "entity",
    "process": "entity",
    "event": "entity",
    "rule": "constraint",
    "metric": "metric",
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
    if category == "metric":
        return [
            {
                "kind": m["name"],
                "label": m["label"],
                "statement": m["definition"],
                "examples": [m["label"], m["name"]],
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
        or any(m["name"] == kind for m in WMS_METRICS)
    )


_CATEGORY_LABEL = {"entity": "实体", "constraint": "约束", "metric": "指标"}


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
1. `kind` 必须逐字来自上面的概念 id。**自造 id 会被判为幻觉，作废**
2. `evidence` 必须**逐字**出现在那条需求的描述里——不能改写、不能翻译、不能补充说明
3. 一条需求可能同时对应多个概念（比如一句话里藏了两件事）
4. 真的对不上就给空列表 `[]`。**宁可空着，也不要硬凑**——空列表会被判据打回，
   那是正常结果；硬凑出来的绑定会让模型说它没说的话
5. 需求列表里每一条都要在输出里出现（对不上就给空列表），不要漏"""


def _build_prompt(requirements: list[dict[str, Any]]) -> str:
    catalog_lines: list[str] = []
    for category in ("entity", "constraint", "metric"):
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
        req_lines.append(
            f"- id={r.get('requirement_id', '')} "
            f"type={r.get('requirement_type', '')} "
            f"描述：{r.get('description', '')}"
        )
    return _BIND_PROMPT.format(catalog=catalog, requirements="\n".join(req_lines))


# ============================================================
# 对外
# ============================================================


class BindingError(ValueError):
    """绑定这一步本身没法做（LLM 不可用 / 返回的不是 JSON）→ 履约中止"""


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
        allowed = _catalog_for(_TYPE_CATEGORY.get(req.get("requirement_type", ""), ""))
        allowed_kinds = {c["kind"] for c in allowed}

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
            if kind not in allowed_kinds:
                errors.append({
                    "requirement_id": rid,
                    "reason": (
                        f"概念 {kind!r} 不属于 {req.get('requirement_type')!r} 类型"
                        f"允许的类别"
                    ),
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

    return bindings, errors
