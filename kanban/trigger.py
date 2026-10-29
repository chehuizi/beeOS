"""kanban.trigger - task 触发入口（trigger handler 的 kanban 侧实现）

按 [beebox-runtime-design.md §6] 的 "task run 触发" 接口落地到看板：
任何履约盒子都通过接收 task 开始履约——本模块是这条链路的服务端：

  POST payload → 校验 box + task_type → create_task_run
  → BeelineExecutor.execute → evaluate_acceptance → store.append

PoC 取舍：同步执行（建模盒 7 op 毫秒级，POST 直接返回终态）。
真实 worker 接入后应异步化（POST 只落盘 triggered 状态）。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Iterable, Optional
from uuid import uuid4

# beeline 住在盒子里（boxes/<box>/beeline.py）——它跟盒子是 1:1 且不可分割：
# 它引用的 schema id 全部来自本盒的 schemas.py，每个 op 的 handler 都在
# 本盒的 runner.py 里。顶层一个 beelines/ 目录会让人以为存在一种可复用的
# 跨盒路线，实际上一条都没有。
from boxes.wms_design.beeline import get_beeline as get_modeling_beeline
from boxes.wms_analysis.beeline import get_beeline as get_wms_analysis_beeline
from boxes.wms_development.beeline import get_beeline as get_wms_development_beeline
from boxes.wms_ops.beeline import get_beeline as get_wms_ops_beeline
from boxes.wms_system.beeline import get_beeline as get_wms_system_beeline
from boxes.wms_testing.beeline import get_beeline as get_wms_testing_beeline
from boxes.receipt.beeline import get_beeline as get_receipt_capture_beeline
from boxes.wms_requirement.beeline import get_beeline as get_requirement_capture_beeline
from boxes.wms_analysis.measure import COMPUTERS as WMS_ANALYSIS_COMPUTERS
from boxes.wms_analysis.runner import RUNNER as WMS_ANALYSIS_RUNNER
from boxes.wms_development.measure import COMPUTERS as WMS_DEV_COMPUTERS
from boxes.wms_development.runner import RUNNER as WMS_DEV_RUNNER
from boxes.wms_ops.measure import COMPUTERS as WMS_OPS_COMPUTERS
from boxes.wms_ops.runner import RUNNER as WMS_OPS_RUNNER
from boxes.wms_system.measure import COMPUTERS as WMS_SYS_COMPUTERS
from boxes.wms_system.runner import RUNNER as WMS_SYS_RUNNER
from boxes.wms_testing.measure import COMPUTERS as WMS_TEST_COMPUTERS
from boxes.wms_testing.runner import RUNNER as WMS_TEST_RUNNER
from boxes.wms_design import get_definition as get_mod_definition
from boxes.wms_design.measure import COMPUTERS as MODELING_COMPUTERS
from boxes.wms_design.runner import RUNNER as MODELING_RUNNER
from boxes.wms_analysis import get_definition as get_wms_analysis_definition
from boxes.wms_development import get_definition as get_wms_development_definition
from boxes.wms_ops import get_definition as get_wms_ops_definition
from boxes.wms_system import get_definition as get_wms_system_definition
from boxes.wms_testing import get_definition as get_wms_testing_definition
from boxes.receipt.runner import RUNNER as RECEIPT_RUNNER
from boxes.receipt import get_definition as get_receipt_definition
from boxes.receipt.measure import COMPUTERS as RECEIPT_COMPUTERS
from boxes.wms_requirement import get_definition as get_capture_definition
from boxes.wms_requirement.measure import COMPUTERS as CAPTURE_COMPUTERS
from boxes.wms_requirement.runner import RUNNER as CAPTURE_RUNNER
from core import metrics as core_metrics
from core.beeline_models import Beeline
from core.models import BeeBoxDefinition, FieldDef, SchemaDef
from core.beeline_models import Beeline
from runtime import BeelineExecutor, create_task_run, evaluate_acceptance
from runtime.contract import validate_payload
from runtime.handoff import HandoffError, assemble_handoff
from runtime.models import TaskRunStatus
from runtime.runner import HandlerRunner
from runtime.store import TaskRunStore


# box_id → (definition 加载器, {beeline_id: beeline 加载器})
# 当前聚焦业务建模履约盒子；运营线盒子代码保留在仓库，但未注册 =
# 看板不显示、trigger 不接收（恢复时加回注册表即可）
BOX_REGISTRY: dict[
    str,
    tuple[Callable[[], BeeBoxDefinition], dict[str, Callable[[], Beeline]]],
] = {
    "wms_requirement_box": (
        get_capture_definition,
        {"beeline_wms_requirement_v1": get_requirement_capture_beeline},
    ),
    "wms_design_box": (
        get_mod_definition,
        {"beeline_wms_design_v1": get_modeling_beeline},
    ),
    "wms_system_box": (
        get_wms_system_definition,
        {"beeline_wms_system_v2": get_wms_system_beeline},
    ),
    "wms_development_box": (
        get_wms_development_definition,
        {"beeline_wms_development_v1": get_wms_development_beeline},
    ),
    "wms_testing_box": (
        get_wms_testing_definition,
        {"beeline_wms_testing_v1": get_wms_testing_beeline},
    ),
    "wms_ops_box": (
        get_wms_ops_definition,
        {"beeline_wms_ops_v1": get_wms_ops_beeline},
    ),
    "wms_analysis_box": (
        get_wms_analysis_definition,
        {"beeline_wms_analysis_v1": get_wms_analysis_beeline},
    ),
    "receipt_capture_box": (
        get_receipt_definition,
        {"beeline_receipt_capture_v1": get_receipt_capture_beeline},
    ),
}

# box_id → 指标采集算法表。跟 BOX_REGISTRY 一一对应：
# 在这里注册一只盒子，就必须同时给出它的指标采集算法，
# 否则 definition.metrics 里声明的指标在看板上是拿不到的数字。
METRIC_COMPUTERS: dict[str, dict[str, core_metrics.Computer]] = {
    "wms_requirement_box": CAPTURE_COMPUTERS,
    "wms_design_box": MODELING_COMPUTERS,
    "receipt_capture_box": RECEIPT_COMPUTERS,
    "wms_system_box": WMS_SYS_COMPUTERS,
    "wms_development_box": WMS_DEV_COMPUTERS,
    "wms_testing_box": WMS_TEST_COMPUTERS,
    "wms_ops_box": WMS_OPS_COMPUTERS,
    "wms_analysis_box": WMS_ANALYSIS_COMPUTERS,
}

# box_id → 该盒自带的 op 实现。
# op 的执行代码住在 boxes/<box>/runner.py，不在 runtime/：
# 盒子必须带得走自己的实现，否则它只是个声明，不是可独立交付的单元。
# 内核（runtime/executor.py）只认分派协议，不认识任何一只盒子的 op。
RUNNER_REGISTRY: dict[str, HandlerRunner] = {
    "wms_requirement_box": CAPTURE_RUNNER,
    "wms_design_box": MODELING_RUNNER,
    "receipt_capture_box": RECEIPT_RUNNER,
    "wms_system_box": WMS_SYS_RUNNER,
    "wms_development_box": WMS_DEV_RUNNER,
    "wms_testing_box": WMS_TEST_RUNNER,
    "wms_ops_box": WMS_OPS_RUNNER,
    "wms_analysis_box": WMS_ANALYSIS_RUNNER,
}

# ~~Box 元信息（归属 / 接线）~~
#
# 原先这里有一张 BOX_META 表，写着每只盒子的 display_name / value_stream /
# role / feeds_into。现在全部搬回各盒自己的 definition（placement）。
#
# 为什么搬：这些是**业务知识**。「盒子之间的关系是业务信息」这句注释本来
# 就写在 BOX_META 头上——它自己承认了这件事，只是放错了地方。放在看板层
# 的直接后果是：盒子只能通过看板去知道自己接谁，换掉看板接线就丢了。
# 而接线是「这只盒子是什么」的一部分，恰恰属于盒子自己。
#
# 搬走之后看板变成纯读者：box_meta() 从 definition.placement 取值，
# 看板自己一个字都不持有。


def box_meta(box_id: str) -> dict[str, str]:
    """盒子的展示元信息（未注册盒子返回空 dict）

    **来源是盒子自己的 definition，不是看板**。接线（feeds_into）和
    价值流归属是业务知识，住在 `kanban/` 的话，换掉看板这只盒子
    就不知道自己接谁——而接线是「这只盒子是什么」的一部分。
    看板读它，不拥有它。
    """
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        return {}
    return entry[0]().placement.model_dump()


def registered_box_ids() -> set[str]:
    """已注册（看板可见 / 可触发）的 box_id 集合"""
    return set(BOX_REGISTRY)


def box_maturity(box_id: str) -> str:
    """盒子的成熟度：live（能跑）| skeleton（判据已声明，腿还没实现）

    声明住在 `ResultDef.maturity`——**产出契约**的属性，不是看板属性。
    看板读它来标「未实现」，但不拥有它：一只盒子换到别的看板，
    「它还没实现」这个事实不会跟着丢。
    """
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        return ""
    return entry[0]().result.maturity


def declared_beeline_ops(box_id: str, beeline_id: str | None = None) -> list[str]:
    """这只盒子声明的 op 链（按 beeline 声明的顺序）

    **来源是盒子自己的 beeline，不是看板的一张表**。
    op 链是「这只盒子应该跑哪几跳」的事实，住在盒子里；
    看板读它，不拥有它——换掉看板这只盒子照样知道自己该跑哪几跳。

    为什么要单独给一份「声明」：落盘的 op_trace 是**实际跑过什么**，
    失败时会比声明短一截（停在 dev_code，后面的两跳没跑到）。
    只给轨迹，读者会把「少了两跳」误读成「这只盒子只有两跳」。

    `beeline_id` 是那一次运行用的 beeline——优先按它取，
    因为一只盒子可能有多版 beeline，取错版本就说错了「应该跑几跳」。
    """
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        return []
    loaders: dict[str, Callable[[], Beeline]] = entry[1]
    loader = None
    if beeline_id and beeline_id in loaders:
        loader = loaders[beeline_id]
    elif len(loaders) == 1:
        loader = next(iter(loaders.values()))
    if loader is None:
        return []
    try:
        beeline = loader()
    except Exception:
        # 声明读不出来就返回空——看板照实说「读不到声明」，
        # 不用别处的版本凑一条「看起来对」的链。
        return []
    return [op.op_id for op in beeline.operations]


def box_domains(box_ids: Iterable[str]) -> dict[str, list[str]]:
    """按 domain 分组盒子（域成员从各盒 placement 派生，不另立域表）

    **为什么没有一张「域 → 成员」的表**：它会跟各盒自己的 domain 声明
    漂。BOX_META 教训的同一个形状——两边各写一遍，改了一边另一边悄悄
    过时，而没有任何东西会报错。所以域成员是**算出来的**。

    `""`（没声明 domain）单独归到「未归域」，不假装它属于某个域。
    """
    groups: dict[str, list[str]] = {}
    for bid in box_ids:
        entry = BOX_REGISTRY.get(bid)
        domain = entry[0]().placement.domain if entry else ""
        groups.setdefault(domain, []).append(bid)
    return {k: groups[k] for k in sorted(groups)}


def domain_layout(domain: str) -> dict[str, Any]:
    """一个域的布局：谁在圆心、环上什么顺序、有哪两类边

    **全部从各盒 placement.feeds_into 推导，没有域表、没有写死的中心。**

    平铺的看板把接线扔了：读者看不出哪只是终点、哪两只之间有交接。
    这些信息服务端早就算出来了，画出来等于把已有的东西用上。

    规则（都跟「WMS」「system box」这些具体名字无关）：

    - **中心 = 唯一汇点**：域内出度 0 且入度 > 0。恰好一个才成立；
      零个或多个都**不猜**，`center=None` 且 `reason` 说清楚，退回平铺。
      今天 WMS 域推导出来恰好是 system box，但推导规则跟它无关——
      写死 `center = "wms_system_box"` 就等于在看板层声明了业务事实，
      跟 BOX_META 一模一样的病，只是换了张脸。
    - **两类边**：指向中心的边是**累积**（这次产出进档案），
      其余域内边是**交接**（交出去让下一只验）。混成一条就看不出
      「流水线」和「记账」是两件事。
    - **环序**：交接边的拓扑序，同层按 box_id 字母序（稳定）。
    """
    ids = [b for b in order_boxes_by_flow(registered_box_ids())
           if (BOX_REGISTRY[b][0]().placement.domain == domain)]
    return _layout_for(ids, domain)


# 「全部域」哨兵，与前端 ALL_DOMAINS 一致。
# 它**不是**一个域：它没有 placement.domain，也没有成员表。
ALL_DOMAINS_SCOPE = "__all__"


def scope_layout(scope: str) -> dict[str, Any]:
    """一个**范围**（不一定是单个域）的布局

    「视图」和「范围」是两个独立的轴：网格/拓扑由读者选，域只决定看哪些盒子。
    所以这里必须能回答「全部域」这种跨域范围——以前没有这种情况，
    是因为点域 chip 顺手把视图也换了，两个轴被焊死成一个。

    **跨域范围一律不出环，而且这不是偷懒**：见 `_layout_for` 里
    multi_domain 那段，域内唯一汇点的规则跨了几条互不相干的流水线之后
    就不成立了，硬凑一个中心等于替业务声明一条不存在的拓扑。
    """
    if scope == ALL_DOMAINS_SCOPE:
        ids = order_boxes_by_flow(registered_box_ids())
        domains = sorted({BOX_REGISTRY[b][0]().placement.domain or "（未归域）"
                          for b in ids})
        return _layout_for(ids, "全部域", multi_domain=len(domains) > 1)
    return domain_layout(scope)


def _layout_for(
    ids: list[str],
    domain: str,
    *,
    multi_domain: bool = False,
) -> dict[str, Any]:
    """给定一批盒子，推导出中心 / 环序 / 三类边

    `domain` 只是报错文案里的称呼。`multi_domain` 为真表示这批盒子横跨
    多个域——此时**直接退回平铺**，连中心都不找。
    """
    ids = list(ids)
    idset = set(ids)

    if multi_domain:
        # 域内唯一汇点这条规则，跨了几条互不相干的流水线之后就不成立了。
        # 硬凑的话会怎样：拿「全部域」试，唯一汇点算出来是
        # wms_system_box，于是费用报销线的 receipt_capture_box 被画成
        # WMS 流水线的一环、还画了条边投给它——**假的**。
        # 看板一旦这么画，就是在替业务声明一个没人声明过的拓扑，
        # 而且图上看不出任何异常。所以这里连边都不画。
        return {
            "domain": domain,
            "layout": "flat",
            "reason": (
                "范围横跨多个域，各域流水线互不相干，没有共同汇点——"
                "硬凑一个中心等于替业务声明一条不存在的拓扑"
            ),
            "center": None,
            "ring": list(ids),
            "handoffs": [],
            "feeds_center": [],
            "feedback": [],
            "boxes": ids,
        }

    edges = [
        (b, t)
        for b in ids
        for t in BOX_REGISTRY[b][0]().placement.feeds_into
        if t in idset
    ]
    # 反馈边**不进**拓扑排序：闭环那条边一旦混进 feeds_into，
    # 排序就遇到环，于是按原顺序静默收尾——环序变任意，
    # 看板照常渲染，只是结构是错的。摘出来，主干就永远无环。
    feedback = [
        [b, t]
        for b in ids
        for t in BOX_REGISTRY[b][0]().placement.feedback_into
        if t in idset
    ]
    indeg: Counter = Counter()
    outdeg: Counter = Counter()
    for src, dst in edges:
        outdeg[src] += 1
        indeg[dst] += 1
    for src, dst in feedback:
        outdeg[src] += 1
        indeg[dst] += 1

    sinks = [b for b in ids if outdeg[b] == 0 and indeg[b] > 0]
    center = sinks[0] if len(sinks) == 1 else None

    ring = [b for b in ids if b != center] if center else list(ids)
    handoffs = [[s, d] for s, d in edges if d != center]
    feeds_center = [s for s, d in edges if d == center]

    # 环序 = 交接边的拓扑序，自己排而不是借道 order_boxes_by_flow：
    # 那个函数遇到环会静默按原顺序收尾，而这里必须知道「排不出来」。
    # 中心摘掉——它是汇点，让它参与排序会把整环的深度都压平。
    ordered, cyclic = _topo_within(ring, edges)

    layout = "radial"
    reason = ""
    if center is None:
        layout = "flat"
        if len(ids) < 3:
            reason = f"域内只有 {len(ids)} 只盒子，撑不起环"
        elif not sinks:
            reason = "域内没有汇点（每只盒子都还有下游）"
        else:
            reason = f"域内有 {len(sinks)} 个汇点，中心不唯一：" + "、".join(sinks)
    elif cyclic:
        layout = "flat"
        # 闭环的边声明成 feeds_into 了。主干因此成环，环序排不出来。
        # 不猜：退回平铺，并把是哪几只说清楚——不然图上就只是一个
        # 顺序不对的环，看不出哪里声明错了。
        reason = (
            "域内交接边成环（" + "、".join(cyclic) + "）——"
            "闭环那条边应该声明成 feedback_into，不是 feeds_into"
        )

    return {
        "domain": domain,
        "layout": layout,
        "reason": reason,
        "center": center,
        "ring": ordered,
        "handoffs": handoffs,
        "feeds_center": feeds_center,
        "feedback": feedback,
        "boxes": ids,
    }


def _topo_within(ids: list[str], edges: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """ids 内部的拓扑排序；返回 (有序结果, 卡在环上的节点)

    跟 `order_boxes_by_flow` 的区别只有一个，但很要紧：那个函数遇到环
    就按原顺序收尾——看板不能白屏，所以它**选择**不报错。
    这里正相反：成环必须被**看见**，因为成环只有一个原因，
    就是有人把闭环的边声明成了 feeds_into，而那会让环序变成任意的。
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


def order_boxes_by_flow(box_ids: Iterable[str]) -> list[str]:
    """按业务流向排盒子：产出方在前，消费方在后（需求捕获 → 业务建模）

    顺序从各盒 definition.placement.feeds_into 推导，
    不按 box_id 字母序也不按注册顺序。
    字母序会把 wms_design_box 排在 wms_requirement_box 前面，
    而实际流程正好相反；注册顺序则只是开发历史的偶然（谁先加谁在前）。

    稳定：同一层内保持传入顺序（Kahn 分层取出，不做字典序重排）。

    **只排 feeds_into，不排 feedback_into**：闭环的边塞进线性列表就成环，
    而成环时本函数会**静默**收尾成任意顺序。这个列表答的是
    「按流水线顺序排一遍」，而流水线是线性的——闭环不在这个问题里。
    闭环由 `domain_layout` 单独处理：反馈边画成环上闭环，不参与排序。
    兜底：feeds_into 声明成环时不抛错，剩下的按传入顺序收尾——看板不能白屏。

    **入参先 sorted**：本函数的结果不该依赖调用方传进来的迭代顺序。
    `registered_box_ids()` 返回的是 set，而 Python 的字符串哈希按进程
    随机化——直接把 set 传进来，同层里哪只盒子排前面会随进程重启而变。
    后果是看板顺序随机抖动，而且**没有任何东西会报错**：页面照常渲染，
    只是盒子换了位置。排序收在这里，是为了让「传 set 还是传 list」不再
    是个调用方得记着的隐含约定。
    """
    ids = sorted(box_ids)
    # preds[b] = 哪些盒子把产出投给 b（b 的上游）
    preds: dict[str, set[str]] = {b: set() for b in ids}
    for src in ids:
        entry = BOX_REGISTRY.get(src)
        if entry is None:
            continue
        for dst in entry[0]().placement.feeds_into:
            if dst in preds:
                preds[dst].add(src)

    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(ids)
    while remaining:
        ready = [b for b in remaining if preds[b] <= placed]
        if not ready:
            # 环：剩下的按原顺序收尾，不死循环
            ordered.extend(remaining)
            break
        for b in ready:
            ordered.append(b)
            placed.add(b)
        remaining = [b for b in remaining if b not in placed]
    return ordered


class TriggerError(ValueError):
    """task 触发校验失败（box 未知 / task_type 未注册 / beeline 不匹配）"""


def _sample_field(field: FieldDef, schemas: Any = None) -> Any:
    """按 FieldDef 生成示例值（payload 骨架）

    **`ref:` 字段要按目标形状生成整份对象**，不能给空 dict：
    空 dict 不满足 ref 的必填字段，契约校验会拒——
    于是看板给出的示例是一份**投不出去的东西**。
    用户照着示例投，得到的是一句看不懂的契约错误。
    """
    if field.type == "string":
        return ""
    if field.type == "integer":
        return 0
    if field.type == "number":
        return 0.0
    if field.type == "boolean":
        return True
    if field.type == "enum":
        # **有取值表就给一个真值**：示例投料必须**投得出去**。
        # 占位符 "<enum>" 过不了契约校验（枚举值不在合法取值表里），
        # 于是看板上那份示例是一份投出去就被拒的东西。
        # 取值表没声明时（不约束取值）才退回占位符。
        if field.values:
            return field.values[0]
        return "<enum>"
    if field.type == "array":
        return [_sample_field(field.items, schemas)] if field.items else []
    if field.type == "object":
        return {p.name: _sample_field(p, schemas) for p in (field.properties or [])}
    if field.type.startswith("ref:"):
        target = _resolve_schema(field.type[len("ref:"):], schemas)
        if target is None:
            return {}
        return {f.name: _sample_field(f, schemas) for f in target.fields}
    return None


def _resolve_schema(schema_id: str, schemas: Any) -> Any:
    """按 id 找 schema（ref: 的目标只在本盒的 schemas 里）"""
    if not schemas:
        return None
    if isinstance(schemas, dict):
        found = schemas.get(schema_id)
        return found if isinstance(found, SchemaDef) else None
    for s in schemas:
        if isinstance(s, SchemaDef) and s.id == schema_id:
            return s
    return None


# 手写示例（演示效果优于 schema 骨架）；缺省回退到 schema 生成
_EXAMPLE_PAYLOADS: dict[tuple[str, str], dict[str, Any]] = {
    ("wms_design_box", "handle_modeling_request"): {
        # **需求集整个装在 ref: 字段里**——建模盒的投料走接力通道，
        # 形状由建模盒自己声明（schema_model_task_input）。
        # 看板的示例投料也得按这个形状给，否则示例一投就被契约拒。
        "requirement_set_ref": {
            "set_id": "req_set_demo",
            "business_goal": "建模订单退款流程",
            "requirements": [
                {"requirement_id": "req_obj_order", "description": "订单实体", "requirement_type": "object", "priority": "must_have"},
                {"requirement_id": "req_rule_7days", "description": "退款必须在 7 天内", "requirement_type": "rule", "priority": "must_have"},
                {"requirement_id": "req_proc_apply", "description": "退款申请流程", "requirement_type": "process", "priority": "must_have"},
                {"requirement_id": "req_evt_refunded", "description": "退款已完成", "requirement_type": "event", "priority": "should_have"},
                {"requirement_id": "req_met_sla", "description": "退款处理时长", "requirement_type": "metric", "priority": "should_have"},
            ],
        },
    },
    ("wms_requirement_box", "capture_business_requirement"): {
        "narrative": (
            "建模订单退款流程。\n"
            "订单是核心实体。\n"
            "退款必须在 7 天内完成。\n"
            "退款申请走主管审批流程。\n"
            "退款已完成要通知财务。\n"
            "退款处理时长要可度量。"
        ),
    },
}


def _payload_kind(schema) -> str:
    """投料框形态：由 task_schema 的形状决定，不在前端硬编码

    - 有字段声明 media="image" → "media"（投料框是图片上传，不是文本框）
    - 必填字段里有 array / object / ref → "json"（嵌套结构，文本框会毁掉它）
    - 否则全是标量 → "text"（纯文本直接打字，别套一层 JSON 编辑器）

    media 优先于 json：小票那种「一张照片 + 一段备注」应该是 media，
    因为备注是可选的，真正的输入是图。
    """
    if schema is None:
        return "json"
    if any(f.media for f in schema.fields):
        return "media"
    has_complex = any(
        f.required and (f.type in ("array", "object") or f.type.startswith("ref:"))
        for f in schema.fields
    )
    return "json" if has_complex else "text"


def media_fields(schema) -> list:
    """schema 里声明为媒体的字段（投料口按它决定接收几张图）"""
    if schema is None:
        return []
    return [f for f in schema.fields if f.media]


def resolve_text_intake(
    box_id: Optional[str] = None,
    task_type: Optional[str] = None,
) -> tuple[str, str]:
    """解析「一段自然语言」该投给哪只盒子的哪个 task

    按**能力**解析（谁吃 text），不按 id 硬编码。以前这里是写死
    wms_requirement_box + capture_business_requirement 的——那等于让
    调用方知道某一只盒子的 task 类型名，盒子就不再是可插拔单元：
    加一只吃自然语言的新盒，这条路不会跟着它走。

    解析规则：
    - box_id 和 task_type 都给了 → 直接用（调用方明确指定）
    - 只给 box_id → 该盒第一个 payload_kind == "text" 的 task
    - 都没给 → 全注册表里吃 text 的 task；有且仅有一个就用它，
      有多个说明调用方必须指明（不猜——猜错会把业务表述投进错误的盒子）
    """
    if box_id is not None and task_type is not None:
        return box_id, task_type

    if box_id is None:
        candidates: list[tuple[str, str]] = []
        for bid in BOX_REGISTRY:
            candidates.extend(
                (bid, t.type) for t in _text_tasks(bid)
            )
        if not candidates:
            raise TriggerError(
                "no registered box accepts plain text input"
            )
        if len(candidates) > 1:
            raise TriggerError(
                "several registered boxes accept plain text "
                f"({['/'.join(c) for c in candidates]}); "
                "specify box_id and task_type"
            )
        return candidates[0]

    tasks = _text_tasks(box_id)
    if not tasks:
        raise TriggerError(
            f"box {box_id!r} has no task taking plain text input "
            f"(available: {[t.type for t in BOX_REGISTRY[box_id][0]().task]})"
        )
    return box_id, tasks[0].type


def _text_tasks(box_id: str):
    """该盒所有**显式声明**吃自然语言的 task（按声明顺序）

    这里读的是 `TaskDef.accepts_free_text`，不是 `_payload_kind`。
    `_payload_kind` 答的是「投料框渲染成文本框还是 JSON 编辑器」，
    形状上像文本框不等于业务上吃自然语言——详见 core.models.TaskDef。
    """
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        raise TriggerError(f"unknown box_id: {box_id!r}")
    definition = entry[0]()
    return [t for t in definition.task if t.accepts_free_text]


def list_task_entries(box_id: str) -> list[dict[str, Any]]:
    """列出某 box 可接收的 task 入口（task_type + 触发说明 + 示例 payload + 内部工位）

    看板单盒视图用来渲染触发面板和盒子内部的 beeline 工位图。
    box 未注册返回空列表。
    """
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        return []
    get_definition, beeline_loaders = entry
    definition = get_definition()
    entries = []
    for task in definition.task:
        schema = definition.get_schema(task.task_schema)
        sample = _EXAMPLE_PAYLOADS.get((box_id, task.type))
        if sample is None:
            sample = (
                {f.name: _sample_field(f, definition.schemas) for f in schema.fields}
                if schema is not None
                else {}
            )
        beeline_loader = beeline_loaders.get(task.beeline_id)
        beeline_ops = (
            [op.op_id for op in beeline_loader().operations]
            if beeline_loader is not None
            else []
        )
        entries.append({
            "task_type": task.type,
            "trigger": task.trigger,
            "sample_payload": sample,
            "beeline_ops": beeline_ops,
            "payload_kind": _payload_kind(schema),
            # media 形态时投料口要知道收哪个字段、要不要多张
            "media_fields": [
                {
                    "name": f.name,
                    "media": f.media,
                    # 投料口按它决定「能收什么格式」。前端不自己列清单——
                    # 换只盒子支持别的格式，看板一个字都不用改。
                    "accept": f.accepted_media_types(),
                }
                for f in media_fields(schema)
            ],
        })
    return entries


def _hop(box_id: str) -> dict[str, str]:
    """价值流上的一跳：id + 显示名。显示名跟着 id 一起出，
    前端单盒过滤时手上只有一只盒的名字表，让它自己补下游名字会露出原始 id。"""
    return {"id": box_id, "name": box_meta(box_id).get("display_name", box_id)}


def box_manifest(box_id: str) -> dict[str, Any]:
    """盒子的完整自描述——它作为独立 API 单元时对外的全部契约

    五个组件在这里各自成形，正好是看板五栏的投影：
      task   → TASK IN 能接什么（类型 / schema / 必填 / 触发说明）
      beeline→ BEELINE 跑哪条程序（id + 版本 + op 清单）
      result → ARTIFACTS OUT 产出什么（形态 / schema / 字段 / 判据 / 例外）
      acceptance 在 result 里，不另起一节
      box_context（含 metrics）→ BOX CONTEXT 底座

    为什么要这么一份东西：要把 beeBox 当作可独立提供的 API 单元，
    调用方就得能在**不读源码、不看看板**的前提下知道这只盒子是什么、
    怎么调它、它会吐出什么。以前的声明散在 definition / 看板 /
    measure.py 三处，调用方要凑齐得先读完整个 kanban 包。

    records 传 None 表示「还没跑过」，metrics 里的 actual 会是 None。
    """
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        return {}
    get_definition, beeline_loaders = entry
    definition = get_definition()
    result = definition.result

    tasks = []
    beelines = []
    for task in definition.task:
        schema = definition.get_schema(task.task_schema)
        tasks.append({
            "type": task.type,
            "schema": task.task_schema,
            "beeline_id": task.beeline_id,
            "beeline_version": task.beeline_version,
            "trigger": task.trigger,
            "payload_kind": _payload_kind(schema),
            # **投料能不能直接是一段自由文本**——盒子自己声明的，不是推的。
            # 以前调用方读到 manifest 不知道自己能不能直接 POST 一段自然
            # 语言进去——那个能力在 HTTP 层的 /api/structure 里，不在任何
            # 声明里。读不到就只能试一次，而试错会把业务表述投进错误的盒子
            # （resolve_text_intake 遇到多个候选时是拒绝而不是猜，但拒绝对
            # 调用方来说和「不支持」长得一模一样）。
            #
            # 报 task 的声明值，不报 `_payload_kind`：payload_kind 是投料框
            # **长什么样**，这里是**能不能直接喂人话**。wms_system_box 的
            # 投料全是非必填标量，形状上是文本框，但它不吃业务表述——
            # 报成 true 就等于告诉调用方「把需求描述发这儿」，然后我们
            # 去拿它当观测地址解析。
            "accepts_free_text": task.accepts_free_text,
            # write 这一步今天到底能不能单独做。**如实标注，别假装有三个动词**：
            # POST /api/trigger 是「投料 + 执行」一把梭，没有「只存不跑」——
            # 所以 write 的唯一合法实现就是 trigger。写清楚比留白强。
            "write_mode": "trigger_only",
            "media_fields": [
                {
                    "name": f.name,
                    "media": f.media,
                    # 投料口按它决定「能收什么格式」。前端不自己列清单——
                    # 换只盒子支持别的格式，看板一个字都不用改。
                    "accept": f.accepted_media_types(),
                }
                for f in media_fields(schema)
            ],
            "required": [
                {"name": f.name, "type": f.type, "media": f.media}
                for f in (schema.fields if schema else []) if f.required
            ],
        })
        loader = beeline_loaders.get(task.beeline_id)
        if loader is not None:
            beeline = loader()
            if beeline.id not in [b["id"] for b in beelines]:
                beelines.append({
                    "id": beeline.id,
                    "version": beeline.version,
                    "ops": [{"id": op.op_id, "type": op.type} for op in beeline.operations],
                })

    return {
        "box_id": definition.id,
        "version": definition.version,
        "description": definition.description,
        "display_name": box_meta(box_id).get("display_name", box_id),
        "value_stream": box_meta(box_id).get("value_stream", ""),
        "feeds_into": [
            _hop(dst) for dst in box_meta(box_id).get("feeds_into", [])
        ],
        "tasks": tasks,
        "beelines": beelines,
        "produces": {
            "type": result.type,
            "view": result.view,
            "schema": result.result_schema,
            "fields": [
                f.name for f in (
                    definition.get_schema(result.result_schema).fields
                    if definition.get_schema(result.result_schema) else []
                )
            ],
        },
        "acceptance": [
            {"metric": r.metric, "op": r.op, "expected": r.value}
            for r in result.acceptance
        ],
        "exceptions": [
            {"condition": e.condition, "action": e.action}
            for e in result.exceptions
        ],
        "metrics": box_metrics_readings(box_id, []),
    }


def box_metrics_readings(
    box_id: str,
    records: list[dict[str, Any]],
    *,
    window: int = core_metrics.WINDOW,
) -> list[dict[str, Any]]:
    """盒子声明的指标 + 履约历史实测 → 看板可渲染的读数"""
    entry = BOX_REGISTRY.get(box_id)
    computers = METRIC_COMPUTERS.get(box_id)
    if entry is None or computers is None:
        return []
    return core_metrics.measure(entry[0](), records, computers, window=window)


def box_context(
    box_id: str,
    records: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """盒子的上下文——这只盒子是谁、跟谁接、边界在哪、跑得怎么样

    **为什么叫 box_context 而不是 domain_context**：`domain` 在这个项目里
    已经有明确含义了——`core` 的分层守卫把 `domain_service` / `domain_event` /
    `domain_metric` 列成业务词表（`tests/test_layering.py`），建模盒那边还有
    一个 `renderDddModel`。叫 domain context 会跟 DDD 的 bounded context 撞车，
    而这里根本不是领域模型：它就是这只盒子自己的声明。
    邻居也全是 `box_*`（box_manifest / box_meta / box_metrics_readings），
    夹个 domain 在中间像个混血。

    跟 task run 无关。看板上那四栏（投料 / 流水线 / 验收 / 产出）讲的全是
    「这一次履约发生了什么」，这里讲的是「这只盒子是什么」：它在价值流的哪一段、
    上下游是谁、吃进去的契约长什么样、吐出来的契约长什么样、真实指标如何。

    所以它不能在四栏里当第五栏——上下文在履约之前就在那儿，不是跑出来的。
    它是盒子外面的一条 band。

    只回看板要渲染的字段。判据不在这里：声明值和实测值都在 ACCEPTANCE 栏，
    两处各留一份判据 = 两处都要跟着改。

    metrics 这一块是唯一带实测值的：definition.metrics 里写的是 target（承诺），
    这里补的是 actual（事实），两者并排才看得出盒子兑现了多少。
    records 传 None 表示「还没跑过」，读数里的 actual 会是 None 而不是 0.0。
    """
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        return {}
    get_definition, _ = entry
    definition = get_definition()
    meta = box_meta(box_id)
    result = definition.result

    consumes = []
    for task in definition.task:
        schema = definition.get_schema(task.task_schema)
        consumes.append({
            "task_type": task.type,
            "task_schema": task.task_schema,
            "required": [
                {"name": f.name, "type": f.type}
                for f in (schema.fields if schema else [])
                if f.required
            ],
        })

    result_schema = definition.get_schema(result.result_schema)
    placement = definition.placement
    return {
        "value_stream": placement.value_stream,
        "role": placement.role,
        "self_name": placement.display_name or box_id,
        # 上游从**别的盒子的 definition** 反推——接线只在「投给谁」那一侧
        # 声明，「谁投给我」是同一条声明的另一边，两边各写一遍就会漂。
        # 反推源是 BOX_REGISTRY（由各盒 definition 装起来的），不是看板的表。
        "fed_by": [
            _hop(src) for src in BOX_REGISTRY
            if box_id in BOX_REGISTRY[src][0]().placement.feeds_into
        ],
        "feeds_into": [_hop(dst) for dst in placement.feeds_into],
        # 反馈单列：并进 fed_by 会让「上游接着做」和「上一轮结论回到起点」
        # 在单盒页上长得一样，而它们要求做的事完全不同
        "feedback_from": [
            _hop(src) for src in BOX_REGISTRY
            if box_id in BOX_REGISTRY[src][0]().placement.feedback_into
        ],
        "feedback_into": [_hop(dst) for dst in placement.feedback_into],
        "consumes": consumes,
        "produces": {
            "type": result.type,
            "result_schema": result.result_schema,
            "fields": [
                f.name for f in (result_schema.fields if result_schema else [])
            ],
        },
        "metrics": box_metrics_readings(box_id, records or []),
    }


def trigger_task(
    box_id: str,
    task_type: str,
    payload: dict[str, Any],
    store: TaskRunStore,
    originator: str = "kanban.web",
    handoff: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """触发 1 次履约：校验 → 执行 → 验收 → 落盘

    Args:
        box_id: beeBox id（必须在 BOX_REGISTRY 注册）
        task_type: task 类型（必须在 definition.task 注册）
        payload: task 输入（按 task_schema 形状）
        store: TaskRunStore（追加履约记录）
        originator: 触发源标识
        handoff: {投料字段名: 上游 task_run_id}——把上游盒子的产出接到
            投料的 `ref:` 字段上（盒间接力）。见 runtime/handoff.py。

    Returns:
        {task_run_id, box_id, task_type, status, acceptance_status, result}

    Raises:
        TriggerError: box / task_type / beeline 校验失败
    """
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        raise TriggerError(
            f"unknown box_id: {box_id!r} (registered: {sorted(BOX_REGISTRY)})"
        )
    get_definition, beeline_loaders = entry
    definition = get_definition()

    task = definition.get_task(task_type)
    if task is None:
        available = [t.type for t in definition.task]
        raise TriggerError(
            f"box {box_id!r} does not accept task type {task_type!r} "
            f"(available: {available})"
        )

    beeline_loader = beeline_loaders.get(task.beeline_id)
    if beeline_loader is None:
        raise TriggerError(
            f"beeline {task.beeline_id!r} not registered for box {box_id!r}"
        )
    beeline = beeline_loader()
    if beeline.version != task.beeline_version:
        raise TriggerError(
            f"beeline version mismatch: definition pins "
            f"{task.beeline_id}@v{task.beeline_version}, "
            f"registry has v{beeline.version}"
        )

    # ===== 第一道闸：结构契约（投料前，不产生 TaskRun） =====
    # 形状不对（字段缺失 / 类型错 / 枚举值非法）在这里中断，
    # 不进入履约——看板上也看不到这次投料，因为它没开始过。
    # 注意：空需求集在结构上合法（0 条违规），会放行到 acceptance 由业务判据接管。
    schema = definition.get_schema(task.task_schema)
    if schema is None:
        raise TriggerError(
            f"task {task_type!r} pins task_schema {task.task_schema!r} "
            f"which is not declared in definition.schemas"
        )

    # ===== 盒间接力：先把上游盒子的产出接到投料上 =====
    #
    # 放在契约校验**之前**：handoff 填的字段（`ref:` 类型）通常是必填的，
    # 不先接上就会被判成「缺字段」，而那不是投料错，是还没接。
    try:
        payload = assemble_handoff(
            payload,
            handoff,
            task_schema=schema,
            schemas=definition.schemas,
            get_run=store.get_run,
        )
    except HandoffError as e:
        raise TriggerError(str(e)) from e

    contract_errors = validate_payload(payload, schema, schemas=definition.schemas)
    if contract_errors:
        raise TriggerError(
            f"payload violates task_schema {task.task_schema!r}: "
            + "; ".join(contract_errors)
        )

    # ===== 业务对象标识：按盒子的声明从投料抽取 =====
    # 声明了就必须真的给，缺 → 拒绝这次履约。
    # 不静默降级成空：一条「归不到任何工作单元」的履约在归档里等于凭空消失，
    # 而没人会注意到少了一条——那正是「算了却看不见」那一族病。
    subject_ref = None
    subject_kind = None
    if task.subject_ref_field:
        if task.subject_ref_field not in {f.name for f in schema.fields}:
            raise TriggerError(
                f"box {box_id!r} task {task_type!r} declares "
                f"subject_ref_field={task.subject_ref_field!r}, but that field "
                f"is not in task_schema {task.task_schema!r} "
                f"(fields: {[f.name for f in schema.fields]})"
            )
        raw = payload.get(task.subject_ref_field)
        if raw is None or (not isinstance(raw, str) and not isinstance(raw, dict)):
            raise TriggerError(
                f"box {box_id!r} task {task_type!r} requires "
                f"{task.subject_ref_field!r} in the payload — it identifies "
                f"which business object this fulfillment is about. "
                f"Got: {raw!r}. 缺它，这次履约在归档里就归不到任何工作单元"
            )

        # 标识可能**藏在对象里面**：`ref:` 字段装的是整份上游产出，
        # 「这次履约关于哪个需求」的答案在对象的某个键上（dev 盒看设计包的
        # set_id）。这时用 subject_ref_subfield 声明从哪儿取。
        #
        # 声明了就必须是对象：值不是 dict → 拒；对象里没这个键 → 拒。
        # 不静默降级——「算了却看不见」。
        if task.subject_ref_subfield:
            if not isinstance(raw, dict):
                raise TriggerError(
                    f"box {box_id!r} task {task_type!r}: "
                    f"{task.subject_ref_field!r} 声明了 "
                    f"subject_ref_subfield={task.subject_ref_subfield!r}，"
                    f"说明它装的是整份对象，但拿到的是 "
                    f"{type(raw).__name__}"
                )
            if task.subject_ref_subfield not in raw:
                raise TriggerError(
                    f"box {box_id!r} task {task_type!r}: "
                    f"{task.subject_ref_field!r} 里没有 "
                    f"{task.subject_ref_subfield!r} 这个键——"
                    f"标识取不到，这次履约在归档里就归不到任何工作单元。"
                    f"Got keys: {sorted(raw)}"
                )
            raw = raw[task.subject_ref_subfield]

        # 取出来的值必须是 **id 字符串**，不能是整份对象。
        # `ref:` 字段装整份对象是对的（那是它该装的），但**标识**是对象里
        # 那个 id 字符串；把整份对象当标识塞进归档，会出现
        # `{'package_id': ...}` 这种东西。
        if not isinstance(raw, str) or not raw.strip():
            raise TriggerError(
                f"box {box_id!r} task {task_type!r}: {task.subject_ref_field!r} "
                f"must yield an id string, got {type(raw).__name__}. "
                f"它标的是「这次履约关于哪个业务对象」，不是那份对象本身"
            )
        subject_ref = raw.strip()

        # 业务对象的**种类**同样由盒子声明。
        # 不声明就等于让运行时猜——猜错了没人会发现（归档里两类记录混在一起，
        # 看板上还是「一切正常」）。
        if not task.subject_kind:
            raise TriggerError(
                f"box {box_id!r} task {task_type!r} 声明了 "
                f"subject_ref_field={task.subject_ref_field!r}，"
                f"却没声明 subject_kind——「它关于哪一类业务对象」不能靠猜"
            )
        subject_kind = task.subject_kind

    task_run = create_task_run(
        originator=originator,
        intent=task_type,
        contract_ref=definition.result.result_schema,
        release_id=f"{definition.id}@v{definition.version}",
        beeline_id=beeline.id,
        beeline_version=beeline.version,
        runtime_id="runtime_kanban",
        instance_id="instance_kanban",
        subject_ref=subject_ref,
        subject_kind=subject_kind,
    )
    runner = RUNNER_REGISTRY.get(box_id)
    if runner is None:
        raise TriggerError(
            f"box {box_id!r} has no runner registered in RUNNER_REGISTRY; "
            "a box must carry its own op implementations "
            "(boxes/<box>/runner.py) so it stays self-contained"
        )
    task_run = BeelineExecutor(runner).execute(task_run, beeline, payload)

    acceptance_status = None
    rejection_class = None
    # observed：本次履约实测到的判据原值。落盘它是为了让盒子能聚合自己的指标——
    # 实测值原先只活在这次请求的响应体里，请求一关就没了，
    # definition.metrics 里的 target 就永远只是个目标。
    # 这里只搬运 definition 自己声明的判据名，不引入任何盒外词汇。
    observed: dict[str, Any] = {}
    # acceptance 明细：盒内要单列一道闸，看板得能逐条显示判据 / 实测 / 过没过，
    # 只给一个 accepted 徽章等于把"为什么不过"藏起来了。
    acceptance_detail = []
    acceptance_passed = []
    acceptance_not_applicable = []
    if task_run.status == TaskRunStatus.COMPLETED:
        evaluation = evaluate_acceptance(task_run, definition.result)
        acceptance_status = evaluation.status.value
        acceptance_passed = list(evaluation.passed_rules)
        acceptance_not_applicable = list(evaluation.not_applicable_rules)
        rejection_class = (
            evaluation.rejection_class.value if evaluation.rejection_class else None
        )
        failed_map = {f.get("metric"): f for f in evaluation.failed_rules}
        na_set = set(acceptance_not_applicable)
        acceptance_detail = [
            {
                "metric": rule.metric,
                "op": rule.op,
                "expected": rule.value,
                "actual": task_run.result.get(rule.metric),
                # 三态，不是一个 passed 布尔：把「用不上」算成 passed，
                # 看板就会显示「6/6 全过」，而实际只验了 5 条。
                "outcome": (
                    "not_applicable" if rule.metric in na_set
                    else ("failed" if rule.metric in failed_map else "passed")
                ),
                "passed": rule.metric not in failed_map and rule.metric not in na_set,
                "not_applicable": rule.metric in na_set,
            }
            for rule in definition.result.acceptance
        ]
        observed = {
            rule.metric: task_run.result[rule.metric]
            for rule in definition.result.acceptance
            if task_run.result.get(rule.metric) is not None
        }

    store.append(
        task_run,
        box_id=box_id,
        acceptance_status=acceptance_status,
        rejection_class=rejection_class,
        observed=observed,
    )

    return {
        "task_run_id": task_run.identity.task_run_id,
        "box_id": box_id,
        "task_type": task_type,
        "status": task_run.status.value,
        "acceptance_status": acceptance_status,
        "acceptance_detail": acceptance_detail,
        "acceptance_passed": acceptance_passed,
        "acceptance_not_applicable": acceptance_not_applicable,
        "rejection_class": rejection_class,
        # 产出自描述：这只盒子声明的展示形态 + 结果类型。
        # 消费方（看板渲染器、将来的 API 调用方）据此知道该拿哪种视图，
        # 不需要去猜 result 里有没有某个字段。猜错会渲染成另一种形状还不报错。
        "artifact_view": definition.result.view,
        "artifact_type": definition.result.type,
        "result": task_run.result,
        # 真实执行轨迹（按执行顺序）——看板用来在盒子内部回放履约过程。
        # error 必须带上：降级删掉之后，LLM 不可用就是履约中止，
        # 看板得能显示「哪个 op 因为什么挂了」，而不是只看到一个 failed 标签。
        "op_trace": [
            {
                "op_id": rec.op_id,
                "status": rec.status,
                **({"error": rec.error} if rec.error else {}),
            }
            for rec in task_run.operations.values()
        ],
    }


# ============================================================
# 自然语言 → TASK IN 结构化表达（kanban 演示用）
# ============================================================
#
# 用户的业务表述是自然语言；履约需要的是结构化的 BusinessRequirementSet。
# 这里是确定性的关键词启发式——真实实现是 NLU / LLM 能力，
# PoC 只要保证映射规则显式、可测、可替换。

# 业务事件（已XX / 事件）→ event；业务规则（必须 / 不得 / 以内…）→ rule；
# 业务指标（时长 / 率 / 指标）→ metric；业务流程（流程 / 审批 / 步骤）→ process；
# 其余 → object
_EVENT_WORDS = ("已完成", "已提交", "已支付", "已退款", "已发货", "已取消", "已创建", "事件")
_RULE_WORDS = ("必须", "不得", "只能", "应当", "至少", "不超过", "以内", "天内")
_METRIC_WORDS = ("时长", "指标", "效率", "率", "SLA", "sla")
_PROCESS_WORDS = ("流程", "审批", "步骤")

_TYPE_PREFIX = {
    "object": "obj",
    "rule": "rule",
    "process": "proc",
    "metric": "met",
    "event": "evt",
}

# 句内枚举分隔符（顿号 + 中英文逗号）——中文枚举习惯用顿号
_ENUM_SEP = r"[、,，]"

# 存在性声明词：出现在枚举项之前时，其左侧是业务目标，右侧枚举项是业务需求
_EXIST_WORDS = ("有", "包含", "包括", "分为", "涵盖", "涉及")

# 枚举碎片最短长度——短于此的（顿号拆出的单字）视为切碎噪声丢弃
_ENUM_MIN_LEN = 2


def _classify_sentence(sentence: str) -> str:
    """单句业务表述 → 需求类型（event > rule > metric > process > object）"""
    if any(w in sentence for w in _EVENT_WORDS):
        return "event"
    if any(w in sentence for w in _RULE_WORDS):
        return "rule"
    if any(w in sentence for w in _METRIC_WORDS):
        return "metric"
    if any(w in sentence for w in _PROCESS_WORDS):
        return "process"
    return "object"


def _split_enum_phrase(phrase: str) -> tuple[str, list[str]]:
    """枚举短语 → (业务目标, 枚举出的业务事实)

    处理 "wms的流程有入库流程、出库流程、盘点流程" 这类句内枚举：
    存在性声明词（"有"）左侧是业务目标，右侧每项算 1 条业务需求。

    Returns:
        (goal, facts)；无枚举时 facts 为空（goal = 原短语）
    """
    parts = [p.strip() for p in re.split(_ENUM_SEP, phrase) if p.strip()]
    parts = [p for p in parts if len(p) >= _ENUM_MIN_LEN] or parts[:1]
    if len(parts) < 2:
        return phrase, []

    head, facts = parts[0], parts[1:]
    for w in _EXIST_WORDS:
        if w in head:
            goal = head.split(w)[0].strip()
            if goal:
                # 存在词右侧粘着首个枚举项（"有入库流程"），取回来一并算需求
                lead = head.split(w, 1)[1].strip()
                if lead and lead not in facts:
                    facts = [lead, *facts]
                return goal, facts
    return head, facts


def structure_business_text(text: str) -> dict[str, Any]:
    """自然语言业务表述 → BusinessRequirementSet（TASK IN 的结构化表达）

    分句约定：
    1. 按 。；！？\\n 切句
    2. 首句 = business_goal，其余每句 = 1 条业务需求
    3. 枚举兜底：首句含顿号枚举时，按"存在性声明"拆成 1 个目标 + N 条需求
       （否则整句会被当成目标，requirements 落空）
    """

    def _mk(goal: str, rest: list[str]) -> dict[str, Any]:
        counters: Counter[str] = Counter()
        requirements = []
        for s in rest:
            rtype = _classify_sentence(s)
            counters[rtype] += 1
            requirements.append({
                "requirement_id": f"req_{_TYPE_PREFIX[rtype]}_{counters[rtype]}",
                "description": s,
                "requirement_type": rtype,
                "priority": "must_have",
            })
        return {
            "set_id": f"req_set_nl_{uuid4().hex[:8]}",
            "business_goal": goal,
            "requirements": requirements,
        }

    sentences = [s.strip() for s in re.split(r"[。；;！!？?\n]+", text) if s.strip()]
    if not sentences:
        return _mk("", [])

    goal, rest = sentences[0], sentences[1:]
    # 首句若含顿号枚举也拆开（"wms的流程有入库流程、出库流程。后续句…"），
    # 枚举出的业务事实排在后续句之前，保持业务表述的原始顺序
    enum_goal, facts = _split_enum_phrase(goal)
    return _mk(enum_goal, facts + rest)


# ============================================================
# LLM 结构化（自然语言 → BusinessRequirementSet）
# ============================================================
#
# 规则版（structure_business_text）的能力上限已经被实测卡死：
# 类型分类只认词面，"出库要先进先出"→object、"盘点差异 3 个工作日内处理"→object
# 这类语义在词面之外的句子，10 条实测错 6 条。补词表是打地鼠。
#
# 但 LLM 不可信，所以它的输出**不能直接投料**——必须过三道机械检查：
#   1. 结构契约（validate_payload）—— 形状对不对
#   2. 幻觉检查（_no_hallucination）—— 有没有编造原文没有的需求
#   3. 降级兜底（_structure_business_text_rule）—— 上面任一不过就退回规则版
# 这正是结构契约闸在有 LLM 之后价值翻倍的原因：它从"锦上添花"变成"第一道防线"。

_CAPTURE_PROMPT = """把下面的中文业务表述，抽成结构化业务需求。只输出 JSON，不要任何解释文字。

输出格式：
{{"set_id": "req_set_xxx", "business_goal": "第一句的业务目标", "requirements": [{{"requirement_id": "req_xxx_1", "description": "一条业务事实", "requirement_type": "object|rule|process|metric|goal|event", "priority": "must_have"}}]}}

requirement_type 取值含义：
- object：业务实体/对象（订单、库位、批次）
- rule：业务规则或约束（必须/不得/只有…才 / 阈值限制）
- process：业务流程或步骤（有先后顺序的动作）
- metric：业务指标（时长、数量、比率，需要被度量）
- event：已经发生或应该发出的业务事件（已XX / 通知 / 触发）
- goal：业务目标本身

硬性要求：
1. 只抽取原文里真实存在的内容，绝对不要补充、推断或编造原文没有的需求
2. 中文顿号「、」和逗号连接并列项时，每一项单独成一条
3. requirement_type 必填且只能是上面 6 个值之一
4. description 必须是原文中出现过的片段（可截取，可去掉主语），不要改写
5. 如果原文里没有可抽取的业务事实，requirements 返回空数组

业务表述：
{text}"""


def _no_hallucination(payload: dict[str, Any], text: str) -> list[str]:
    """幻觉检查：每条需求的 description 必须在原文里找得到

    不是语义判断，是字面子串匹配——模型改写会漏掉这项检查，
    但"凭空多出原文没有的需求"这类最常见的幻觉能拦住。
    """
    errors: list[str] = []
    haystack = re.sub(r"\s+", "", text)
    for r in payload.get("requirements", []):
        desc = re.sub(r"\s+", "", r.get("description", ""))
        if desc and desc not in haystack:
            errors.append(
                f"requirement {r.get('requirement_id')!r} description "
                f"{r.get('description')!r} not found in source text (possible hallucination)"
            )
    return errors


def _normalize_requirement_ids(payload: dict[str, Any]) -> list[str]:
    """按 requirement_type 分组重编 requirement_id —— 机械规范化，不涉及语义

    LLM 输出的 id 格式不稳（实测出现过 req_001_1 / req_proc_1 混用，
    同一批里前缀不一致），而 id 是 acceptance 里 trace_to 的锚点，
    格式必须统一。这里只重编号，不改 description / type。
    """
    counters: Counter[str] = Counter()
    changed: list[str] = []
    for r in payload.get("requirements", []):
        rtype = r.get("requirement_type") or "object"
        counters[rtype] += 1
        want = f"req_{_TYPE_PREFIX.get(rtype, 'obj')}_{counters[rtype]}"
        if r.get("requirement_id") != want:
            changed.append(f"{r.get('requirement_id')}→{want}")
            r["requirement_id"] = want
    return changed


def _structure_business_text_llm(text: str) -> dict[str, Any]:
    """LLM 结构化：调模型 → 机械检查 → 有一道不过就抛，不降级规则版

    返回里带 _extractor_note 的，是「模型参与了、只是某些机械环节按规则走」
    （id 重编号、business_goal 取规则版）——那是"LLM 只做语义、机械做校验"
    的正确应用，不是降级。真正没模型参与的情况现在不存在了。

    Returns:
        {"set_id", "business_goal", "requirements", "_extractor", "_extractor_note"}
    """
    from runtime import llm

    # 不关 thinking：这条腿要判断 requirement_type（哪句是规则、哪句是指标、
    # 哪句是流程），那是真语义判断，不是照抄。抽取腿关 think 是因为它只做
    # 结构化搬运；这里不能一刀切。
    raw = llm.complete_json(
        _CAPTURE_PROMPT.format(text=text), max_tokens=8000
    )

    if not isinstance(raw, dict):
        raise llm.LLMError(
            f"llm returned {type(raw).__name__}, expected object"
        )

    # 补齐可选字段，避免下游因缺 context/traceable_to 报错
    raw.setdefault("context", "")

    # 第 1 道：结构契约
    from boxes.wms_design import get_definition
    schema = get_definition().get_schema("schema_business_requirement_set")
    errors = validate_payload(raw, schema)
    if errors:
        raise llm.LLMError(
            "llm output violates task_schema: " + "; ".join(errors[:3])
        )

    # set_id 是系统内部标识，必须由系统生成——LLM 编的（实测 req_set_001）
    # 既不保证唯一也不保证前缀，重复投料会撞 id
    raw["set_id"] = f"req_set_nl_{uuid4().hex[:8]}"

    # 第 2 道：幻觉
    errors = _no_hallucination(raw, text)
    if errors:
        raise llm.LLMError(
            "hallucination check failed: " + "; ".join(errors[:3])
        )

    # 第 3 道：空需求不算成功（模型说"抽不出来"就是它没履约）
    if not raw.get("requirements"):
        raise llm.LLMError("llm returned 0 requirements")

    raw["_extractor"] = "llm"
    # 第 4 道：id 规范化（机械、不涉及语义，固定锚点格式）
    renumbered = _normalize_requirement_ids(raw)
    if renumbered:
        raw["_extractor_note"] = "id renormalized: " + ", ".join(renumbered[:4])

    # 第 5 道：business_goal 一律取规则版结果，不采信 LLM
    # 实测同一句跑 3 次，LLM 的 goal 给出 3 个不同版本，且会**改写**原文
    # （"wms的流程有…" → "WMS包含…等流程"，原文没有"包含/等"）。
    # 目标抽取不需要语义理解（首句 + 存在性声明词已足够），
    # 规则版严格截取原文、零幻觉、可复现；需求抽取才需要 LLM。
    rule_goal = _structure_business_text_rule(text)["business_goal"]
    if raw.get("business_goal") != rule_goal:
        raw["_extractor_note"] = (
            (raw.get("_extractor_note") + "; ") if raw.get("_extractor_note") else ""
        ) + "business_goal 取规则版（LLM 会改写原文）"
        raw["business_goal"] = rule_goal
    return raw


# 规则版仍被用，但只剩一处：business_goal 一律取它（LLM 会改写原文）。
# 它不再是「LLM 挂掉时的兜底」——那条路已经删了。
_structure_business_text_rule = structure_business_text


def structure_business_text_with_llm(text: str, *, model: str = "minimax/MiniMax-M3") -> dict[str, Any]:
    """投料口入口：只有 LLM 一条路

    产出保证满足 schema_business_requirement_set 的结构契约——
    不满足就抛（LLMError），不退回规则版。
    """
    return _structure_business_text_llm(text)
