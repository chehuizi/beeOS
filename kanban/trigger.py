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
from typing import Any, Callable
from uuid import uuid4

from beelines import get_modeling_beeline
from boxes.modeling import get_definition as get_mod_definition
from core.beeline_models import Beeline
from core.models import BeeBoxDefinition, FieldDef
from runtime import BeelineExecutor, create_task_run, evaluate_acceptance
from runtime.models import TaskRunStatus
from runtime.store import TaskRunStore


# box_id → (definition 加载器, {beeline_id: beeline 加载器})
# 当前聚焦业务建模履约盒子；运营线盒子代码保留在仓库，但未注册 =
# 看板不显示、trigger 不接收（恢复时加回注册表即可）
BOX_REGISTRY: dict[
    str,
    tuple[Callable[[], BeeBoxDefinition], dict[str, Callable[[], Beeline]]],
] = {
    "business_modeling_box": (
        get_mod_definition,
        {"beeline_business_modeling_v1": get_modeling_beeline},
    ),
}

# Box 元信息（声明式归属，不从 box_id 关键词推断）：
# Line = 企业价值流（value stream），Box = 业务责任单元
BOX_META: dict[str, dict[str, str]] = {
    "business_modeling_box": {
        "display_name": "Business Modeling Box",
        "value_stream": "Software Delivery",
        "role": "Business Modeling Fulfillment",
    },
}


def box_meta(box_id: str) -> dict[str, str]:
    """盒子的展示元信息（未注册盒子返回空 dict）"""
    return BOX_META.get(box_id, {})


def registered_box_ids() -> set[str]:
    """已注册（看板可见 / 可触发）的 box_id 集合"""
    return set(BOX_REGISTRY)


class TriggerError(ValueError):
    """task 触发校验失败（box 未知 / task_type 未注册 / beeline 不匹配）"""


def _sample_field(field: FieldDef) -> Any:
    """按 FieldDef 生成示例值（payload 骨架）"""
    if field.type == "string":
        return ""
    if field.type == "integer":
        return 0
    if field.type == "number":
        return 0.0
    if field.type == "boolean":
        return True
    if field.type == "enum":
        return "<enum>"
    if field.type == "array":
        return [_sample_field(field.items)] if field.items else []
    if field.type == "object":
        return {p.name: _sample_field(p) for p in (field.properties or [])}
    if field.type.startswith("ref:"):
        return {}
    return None


# 手写示例（演示效果优于 schema 骨架）；缺省回退到 schema 生成
_EXAMPLE_PAYLOADS: dict[tuple[str, str], dict[str, Any]] = {
    ("business_modeling_box", "handle_modeling_request"): {
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
}


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
        sample = _EXAMPLE_PAYLOADS.get((box_id, task.type))
        if sample is None:
            schema = definition.get_schema(task.task_schema)
            sample = (
                {f.name: _sample_field(f) for f in schema.fields}
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
        })
    return entries


def trigger_task(
    box_id: str,
    task_type: str,
    payload: dict[str, Any],
    store: TaskRunStore,
    originator: str = "kanban.web",
) -> dict[str, Any]:
    """触发 1 次履约：校验 → 执行 → 验收 → 落盘

    Args:
        box_id: beeBox id（必须在 BOX_REGISTRY 注册）
        task_type: task 类型（必须在 definition.task 注册）
        payload: task 输入（按 task_schema 形状）
        store: TaskRunStore（追加履约记录）
        originator: 触发源标识

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

    task_run = create_task_run(
        originator=originator,
        intent=task_type,
        contract_ref=definition.result.result_schema,
        release_id=f"{definition.id}@v{definition.version}",
        beeline_id=beeline.id,
        beeline_version=beeline.version,
        runtime_id="runtime_kanban",
        instance_id="instance_kanban",
    )
    task_run = BeelineExecutor().execute(task_run, beeline, payload)

    acceptance_status = None
    if task_run.status == TaskRunStatus.COMPLETED:
        evaluation = evaluate_acceptance(task_run, definition.result)
        acceptance_status = evaluation.status.value

    store.append(task_run, box_id=box_id, acceptance_status=acceptance_status)

    return {
        "task_run_id": task_run.identity.task_run_id,
        "box_id": box_id,
        "task_type": task_type,
        "status": task_run.status.value,
        "acceptance_status": acceptance_status,
        "result": task_run.result,
        # 真实执行轨迹（按执行顺序）——看板用来在盒子内部回放履约过程
        "op_trace": [
            {"op_id": rec.op_id, "status": rec.status}
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


def structure_business_text(text: str) -> dict[str, Any]:
    """自然语言业务表述 → BusinessRequirementSet（TASK IN 的结构化表达）

    约定：按 。；！？\\n 切句；首句 = business_goal，其余每句 = 1 条业务需求。
    """
    sentences = [s.strip() for s in re.split(r"[。；;！!？?\n]+", text) if s.strip()]
    if not sentences:
        return {"set_id": f"req_set_nl_{uuid4().hex[:8]}", "business_goal": "", "requirements": []}

    goal, rest = sentences[0], sentences[1:]
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
