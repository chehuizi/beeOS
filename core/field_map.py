"""core/field_map.py —— 场图（BeeBox Map）

**一个域的场**：这个域里有哪些盒子、谁交给谁、谁的产出汇进档案（汇点）、
哪条边是上一轮结论变成下一轮输入（闭环反馈）。

## 为什么它是**派生**的，不是一张手写的表

场的接线事实**已经有一份了**——每只盒子自己的 `definition.placement`
（`feeds_into` / `feedback_into` / `domain`）。那是唯一的事实源，
住在盒子里面：换掉看板，这只盒子照样知道自己接谁。

再手写一张「WMS 场图」表，就是**第二份事实**。两份必然漂，而且漂了
**没有任何东西会报错**——图照常渲染，只是有一只盒子接错了对象。
所以场图只能是**从 placement 推出来的视图**：改一只盒子的接线，
场图自己跟着变；想让场图变，就去改那只盒子。

## 跨域没有场图

「全部域」不是一个场：各域的流水线互不相干，硬凑一个汇点等于
替业务声明一条没人声明过的拓扑。所以跨域时**不给场图**，给的是
一句能读懂的原因（`reason`），而不是一张勉强画出来的图。

## 它是**结构**，不只是图

场图是可以被程序读的东西：哪些盒子在这个场里、它们之间的边是什么类型、
谁是汇点。画布上的虚线边界只是它的一张照片。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class PlacementView:
    """一只盒子在这个场里的位置和接线

    **只抄 placement 里跟场有关的那几项**——这样 `core` 不必认识
    任何一只盒子，也不必认识注册表：注册表在 `kanban/trigger.py`，
    由它把 placement 抄成这个视图再交给这里推导。
    """

    box_id: str
    domain: str
    feeds_into: tuple[str, ...] = ()
    feedback_into: tuple[str, ...] = ()


@dataclass(frozen=True)
class BeeBoxMap:
    """一个域的场图

    `reason` 为空 = 场图成立；非空 = **画不出来**，原因写在这里。
    跨域、没有汇点、汇点不唯一、交接成环——四种情况各有各的原因，
    不合并成一个「画不出来」。
    """

    domain: str
    boxes: tuple[str, ...] = ()
    center: str | None = None
    ring: tuple[str, ...] = ()
    handoffs: tuple[tuple[str, str], ...] = ()
    feeds_center: tuple[str, ...] = ()
    feedback: tuple[tuple[str, str], ...] = ()
    reason: str = ""

    @property
    def drawable(self) -> bool:
        """场图画不画得出来（有没有汇点、环序排不排得出来）"""
        return self.center is not None and not self.reason

    def to_dict(self) -> dict:
        """给 API / 看板用的形状"""
        return {
            "domain": self.domain,
            "boxes": list(self.boxes),
            "center": self.center,
            "ring": list(self.ring),
            "handoffs": [list(e) for e in self.handoffs],
            "feeds_center": list(self.feeds_center),
            "feedback": [list(e) for e in self.feedback],
            "reason": self.reason,
            "drawable": self.drawable,
        }


def _topo_within(ids: Sequence[str], edges: Sequence[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """ids 内部的拓扑排序；返回 (有序结果, 卡在环上的节点)

    遇到环**必须被看见**：成环只有一个原因，就是有人把闭环的边声明成了
    feeds_into，而那会让环序变成任意的。这里返回卡住的那几个节点，
    交给调用方把原因写进 reason——不静默按原顺序收尾。
    """
    inside = set(ids)
    preds: dict[str, set[str]] = {b: set() for b in ids}
    for src, dst in edges:
        if src in inside and dst in inside:
            preds[dst].add(src)
    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(ids)
    while remaining:
        ready = [b for b in remaining if preds[b] <= placed]
        if not ready:
            return ordered, remaining
        ordered.extend(ready)
        placed.update(ready)
        remaining = [b for b in remaining if b not in placed]
    return ordered, []


def derive_field_map(domain: str, placements: Sequence[PlacementView]) -> BeeBoxMap:
    """从各盒的 placement 推导出这个域的场图

    **纯函数**：只吃 placement，不认识注册表、不认识任何一只盒子。
    这样它能被看板、API、将来产出场图的盒子共用同一份推导。
    """
    ids = [p.box_id for p in placements]
    idset = set(ids)

    edges = [
        (b, t)
        for p in placements
        for t in p.feeds_into
        if t in idset
        for b in (p.box_id,)
    ]
    # 反馈边**不进**拓扑排序：闭环那条边一旦混进 feeds_into，
    # 排序就遇到环，于是按原顺序静默收尾——环序变任意，看板照常渲染，
    # 只是结构是错的。摘出来，主干就永远无环。
    feedback = [
        (p.box_id, t)
        for p in placements
        for t in p.feedback_into
        if t in idset
    ]

    indeg: dict[str, int] = {b: 0 for b in ids}
    outdeg: dict[str, int] = {b: 0 for b in ids}
    for src, dst in edges:
        outdeg[src] += 1
        indeg[dst] += 1
    for src, dst in feedback:
        outdeg[src] += 1
        indeg[dst] += 1

    sinks = [b for b in ids if outdeg[b] == 0 and indeg[b] > 0]
    center = sinks[0] if len(sinks) == 1 else None

    ring = [b for b in ids if b != center] if center else list(ids)
    handoffs = tuple((s, d) for s, d in edges if d != center)
    feeds_center = tuple(s for s, d in edges if d == center)

    # 环序 = 交接边的拓扑序。中心摘掉——它是汇点，让它参与排序
    # 会把整环的深度都压平。
    ordered, cyclic = _topo_within(ring, edges)

    reason = ""
    if center is None:
        if len(ids) < 3:
            reason = f"域内只有 {len(ids)} 只盒子，撑不起环"
        elif not sinks:
            reason = "域内没有汇点（每只盒子都还有下游）"
        else:
            reason = "域内有 " + str(len(sinks)) + " 个汇点，中心不唯一：" + "、".join(sinks)
    elif cyclic:
        # 闭环的边声明成 feeds_into 了。主干因此成环，环序排不出来。
        # 不猜：把是哪几只说清楚——不然图上就只是一个顺序不对的环，
        # 看不出哪里声明错了。
        reason = (
            "域内交接边成环（" + "、".join(cyclic) + "）——"
            "闭环那条边应该声明成 feedback_into，不是 feeds_into"
        )

    return BeeBoxMap(
        domain=domain,
        boxes=tuple(ids),
        center=center,
        ring=tuple(ordered),
        handoffs=handoffs,
        feeds_center=feeds_center,
        feedback=tuple(feedback),
        reason=reason,
    )


def field_map_of_scope(
    scope: str,
    scopes: dict[str, list[str]],
    placements: dict[str, PlacementView],
) -> BeeBoxMap:
    """一个**范围**（可能横跨多个域）的场图

    横跨多个域时**没有场图**：各域流水线互不相干，硬凑一个中心等于
    替业务声明一条不存在的拓扑。给的是一句能读懂的原因。
    """
    domains = [d for d in scopes if d in scopes and scopes[d]]
    if len(domains) > 1:
        every: list[str] = []
        for d in domains:
            every.extend(scopes[d])
        return BeeBoxMap(
            domain=scope,
            # 盒子还是列出来——「这个范围里有哪几只盒子」是事实，
            # 缺的只是**场**（共同汇点 + 接线）。
            boxes=tuple(every),
            reason=(
                "范围横跨多个域，各域流水线互不相干，没有共同汇点——"
                "硬凑一个中心等于替业务声明一条不存在的拓扑"
            ),
        )
    if not domains:
        return BeeBoxMap(domain=scope, reason="这个范围里没有盒子")
    domain = domains[0]
    ids = scopes[domain]
    return derive_field_map(
        domain, [placements[b] for b in ids if b in placements]
    )
