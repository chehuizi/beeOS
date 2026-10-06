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
from typing import Any, Callable, Iterable
from uuid import uuid4

from beelines import get_modeling_beeline, get_requirement_capture_beeline
from boxes.modeling import get_definition as get_mod_definition
from boxes.requirement_capture import get_definition as get_capture_definition
from core.beeline_models import Beeline
from core.models import BeeBoxDefinition, FieldDef
from runtime import BeelineExecutor, create_task_run, evaluate_acceptance
from runtime.contract import validate_payload
from runtime.models import TaskRunStatus
from runtime.store import TaskRunStore


# box_id → (definition 加载器, {beeline_id: beeline 加载器})
# 当前聚焦业务建模履约盒子；运营线盒子代码保留在仓库，但未注册 =
# 看板不显示、trigger 不接收（恢复时加回注册表即可）
BOX_REGISTRY: dict[
    str,
    tuple[Callable[[], BeeBoxDefinition], dict[str, Callable[[], Beeline]]],
] = {
    "requirement_capture_box": (
        get_capture_definition,
        {"beeline_requirement_capture_v1": get_requirement_capture_beeline},
    ),
    "business_modeling_box": (
        get_mod_definition,
        {"beeline_business_modeling_v1": get_modeling_beeline},
    ),
}

# Box 元信息（声明式归属，不从 box_id 关键词推断）：
# Line = 企业价值流（value stream），Box = 业务责任单元
BOX_META: dict[str, dict[str, Any]] = {
    "requirement_capture_box": {
        "display_name": "Requirement Capture Box",
        "value_stream": "Software Delivery",
        "role": "Business Requirement Capture Fulfillment",
        # 本盒产出投给谁（看板产出口的"接力"按钮读它）。
        # 声明在盒子上而不是硬编码在前端——盒子之间的关系是业务信息。
        "feeds_into": ["business_modeling_box"],
    },
    "business_modeling_box": {
        "display_name": "Business Modeling Box",
        "value_stream": "Software Delivery",
        "role": "Business Modeling Fulfillment",
        "feeds_into": [],
    },
}


def box_meta(box_id: str) -> dict[str, str]:
    """盒子的展示元信息（未注册盒子返回空 dict）"""
    return BOX_META.get(box_id, {})


def registered_box_ids() -> set[str]:
    """已注册（看板可见 / 可触发）的 box_id 集合"""
    return set(BOX_REGISTRY)


def order_boxes_by_flow(box_ids: Iterable[str]) -> list[str]:
    """按业务流向排盒子：产出方在前，消费方在后（需求捕获 → 业务建模）

    顺序从 BOX_META 的 feeds_into 声明推导，不按 box_id 字母序也不按注册顺序。
    字母序会把 business_modeling_box 排在 requirement_capture_box 前面，
    而实际流程正好相反；注册顺序则只是开发历史的偶然（谁先加谁在前）。

    稳定：同一层内保持传入顺序（Kahn 分层取出，不做字典序重排）。
    兜底：feeds_into 声明成环时不抛错，剩下的按传入顺序收尾——看板不能白屏。
    """
    ids = list(box_ids)
    # preds[b] = 哪些盒子把产出投给 b（b 的上游）
    preds: dict[str, set[str]] = {b: set() for b in ids}
    for src in ids:
        for dst in BOX_META.get(src, {}).get("feeds_into", []):
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
    ("requirement_capture_box", "capture_business_requirement"): {
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
    """投料框形态：由 task_schema 的必填字段形状决定，不在前端硬编码

    - 必填字段里有 array / object / ref → "json"（嵌套结构，文本框会毁掉它）
    - 否则全是标量 → "text"（纯文本直接打字，别套一层 JSON 编辑器）
    """
    if schema is None:
        return "json"
    has_complex = any(
        f.required and (f.type in ("array", "object") or f.type.startswith("ref:"))
        for f in schema.fields
    )
    return "json" if has_complex else "text"


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
            "payload_kind": _payload_kind(schema),
        })
    return entries


def _hop(box_id: str) -> dict[str, str]:
    """价值流上的一跳：id + 显示名。显示名跟着 id 一起出，
    前端单盒过滤时手上只有一只盒的名字表，让它自己补下游名字会露出原始 id。"""
    return {"id": box_id, "name": box_meta(box_id).get("display_name", box_id)}


def box_domain_context(box_id: str) -> dict[str, Any]:
    """盒子的领域上下文——这只盒子是谁、跟谁接、边界在哪

    跟 task run 无关。看板上那四栏（投料 / 流水线 / 验收 / 产出）讲的全是
    「这一次履约发生了什么」，这里讲的是「这只盒子是什么」：它在价值流的哪一段、
    上下游是谁、吃进去的契约长什么样、吐出来的契约长什么样。

    所以它不能在四栏里当第五栏——上下文在履约之前就在那儿，不是跑出来的。
    它是盒子外面的一条 band。

    只回看板要渲染的字段。判据不在这里：声明值和实测值都在 ACCEPTANCE 栏，
    两处各留一份判据 = 两处都要跟着改。
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
    return {
        "value_stream": meta.get("value_stream", ""),
        "role": meta.get("role", ""),
        "self_name": meta.get("display_name", box_id),
        # 上游从别人的 feeds_into 反推——BOX_META 只声明了「投给谁」，
        # 「谁投给我」是同一条声明的另一边，不该在两处各写一遍
        "fed_by": [
            _hop(src) for src, m in BOX_META.items()
            if box_id in m.get("feeds_into", [])
        ],
        "feeds_into": [_hop(dst) for dst in meta.get("feeds_into", [])],
        "consumes": consumes,
        "produces": {
            "type": result.type,
            "result_schema": result.result_schema,
            "fields": [
                f.name for f in (result_schema.fields if result_schema else [])
            ],
        },
    }


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
    contract_errors = validate_payload(payload, schema)
    if contract_errors:
        raise TriggerError(
            f"payload violates task_schema {task.task_schema!r}: "
            + "; ".join(contract_errors)
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
    rejection_class = None
    # acceptance 明细：盒内要单列一道闸，看板得能逐条显示判据 / 实测 / 过没过，
    # 只给一个 accepted 徽章等于把"为什么不过"藏起来了。
    acceptance_detail = []
    acceptance_passed = []
    if task_run.status == TaskRunStatus.COMPLETED:
        evaluation = evaluate_acceptance(task_run, definition.result)
        acceptance_status = evaluation.status.value
        acceptance_passed = list(evaluation.passed_rules)
        rejection_class = (
            evaluation.rejection_class.value if evaluation.rejection_class else None
        )
        failed_map = {f.get("metric"): f for f in evaluation.failed_rules}
        acceptance_detail = [
            {
                "metric": rule.metric,
                "op": rule.op,
                "expected": rule.value,
                "actual": task_run.result.get(rule.metric),
                "passed": rule.metric not in failed_map,
            }
            for rule in definition.result.acceptance
        ]

    store.append(
        task_run,
        box_id=box_id,
        acceptance_status=acceptance_status,
        rejection_class=rejection_class,
    )

    return {
        "task_run_id": task_run.identity.task_run_id,
        "box_id": box_id,
        "task_type": task_type,
        "status": task_run.status.value,
        "acceptance_status": acceptance_status,
        "acceptance_detail": acceptance_detail,
        "acceptance_passed": acceptance_passed,
        "rejection_class": rejection_class,
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
    """LLM 结构化：调模型 → 三道机械检查 → 不过就降级规则版

    Returns:
        {"set_id", "business_goal", "requirements", "_extractor", "_extractor_note"}
        _extractor = "llm" 表示模型输出通过了全部检查
    """
    from runtime import llm

    def _fallback(note: str) -> dict[str, Any]:
        p = _structure_business_text_rule(text)
        p["_extractor"] = "rule"
        p["_extractor_note"] = note
        return p

    try:
        raw = llm.complete_json(
            _CAPTURE_PROMPT.format(text=text), max_tokens=2000
        )
    except llm.LLMError as e:
        return _fallback(f"llm unavailable: {e}")

    if not isinstance(raw, dict):
        return _fallback(f"llm returned {type(raw).__name__}, expected object")

    # 补齐可选字段，避免下游因缺 context/traceable_to 报错
    raw.setdefault("context", "")

    # 第 1 道：结构契约
    from boxes.modeling import get_definition
    schema = get_definition().get_schema("schema_business_requirement_set")
    errors = validate_payload(raw, schema)
    if errors:
        return _fallback("llm output violates task_schema: " + "; ".join(errors[:3]))

    # set_id 是系统内部标识，必须由系统生成——LLM 编的（实测 req_set_001）
    # 既不保证唯一也不保证前缀，重复投料会撞 id
    raw["set_id"] = f"req_set_nl_{uuid4().hex[:8]}"

    # 第 2 道：幻觉
    errors = _no_hallucination(raw, text)
    if errors:
        return _fallback("hallucination check failed: " + "; ".join(errors[:3]))

    # 第 3 道：空需求不算成功（模型说"抽不出来"时退回规则版再试一次）
    if not raw.get("requirements"):
        return _fallback("llm returned 0 requirements")

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


# 保留规则版原名，供 LLM 版降级调用
_structure_business_text_rule = structure_business_text


def structure_business_text_with_llm(text: str, *, model: str = "minimax/MiniMax-M3") -> dict[str, Any]:
    """投料口入口：优先 LLM 结构化，不可用则降级规则版

    无论走哪条路，产出都保证满足 schema_business_requirement_set 的结构契约——
    投料口下游（trigger_task）不会再因为结构问题被拒。
    """
    return _structure_business_text_llm(text)
