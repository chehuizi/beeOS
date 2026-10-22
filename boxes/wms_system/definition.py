"""boxes.wms_system.definition - WMS System Box Definition 数据

WMS 系统档案盒——把「真实在跑的那套 WMS 是什么样」写成一份**带出处、带缺口**的档案。

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息 + placement（归属 / 接谁 / 投给谁 / 属于哪个域）
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + 5 条机械 acceptance + 3 条 exception）
- 履约政策 Policy（queen）
- 履约度量 Metrics

**成熟度：live**。腿已经实现了——真的会发 HTTP 去探线上那套 WMS。

## 这只盒子存结果，不存过程

周边五只盒子（需求 / 设计 / 开发 / 测试 / 运维）各自做自己那一段的过程，
过程产物留在它们自己那里。**本盒只存结果**：这套系统由什么构成、
对外契约是什么、哪些部分此刻可观测。

将来要对账时（本轮不做），对的是「两边都声称的结果」，
不是「代码里写的」——所以接线是五只盒子都 feeds_into 本盒，
唯一例外是需求盒：它产出的是读基线（现在这份档案的输入来源之一），
不是往档案里加东西。

## 判据只判「标注是否如实」，不判「拿到了什么」

5 条判据全部读本盒产出，没有一条跨盒。原因是
`runtime/acceptance.py::_evaluate_condition` 只能读本盒 result，
判据里没有跨盒入口——这条限制不是待办，是判据保持 0 主观的前提。

其中 `aspect_status_complete` 判的是诚实本身：每条维度是否带了合法状态，
declared 有没有出处，unverifiable 有没有原因。它不关心档案内容对不对，
只关心档案有没有假装自己是全知的。
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricsDef,
    PlacementDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    TaskDef,
)
from boxes.wms_system.schemas import ALL_SCHEMAS

WMS_SYSTEM_BOX = BeeBoxDefinition(
    id="wms_system_box",
    version=2,
    description=(
        "WMS 系统档案盒——把「真实在跑的那套 WMS 是什么样」写成一份带出处的档案。\n\n"
        "**它存结果，不存过程。** 需求、设计、开发、测试、运维五只盒子各自做自己那一段的"
        "过程，过程产物留在它们自己那里；本盒只沉淀结果——这套系统由什么构成、对外契约是"
        "什么、哪些部分此刻真的可观测。\n\n"
        "**档案里没有一个裸数字。** 每条维度自带 `status`，三选一："
        "`observed`（这次真的探到了）、`declared_from_baseline`（只有声明值、这次没能验证，"
        "必须写出处）、`unverifiable`（拿不到，必须写为什么）。"
        "今天 6 条维度里只有 3 条能观测，部署版本和外部依赖是以现在的系统形态查不到的——"
        "那句话说在档案里，不说在档案外。\n\n"
        "它是 WMS 域的唯一汇点：五只过程盒的产出最终都要跟这份档案对上。"
    ),

    schemas=ALL_SCHEMAS,

    placement=PlacementDef(
        display_name="WMS System Box",
        value_stream="Software Delivery",
        role="WMS System Description Fulfillment",
        domain="WMS",
        # 汇点没有下游：它不被投给谁，它是所有人的落点。
        feeds_into=[],
    ),

    task=[
        TaskDef(
            # v1 叫 handle_system_request（「处理一个系统请求」）——
            # 名不副实：它不处理请求，它主动去观测然后归档。
            # 改名跟 beeline 升 v2 是同一件事的两面。
            type="sync_system_profile",
            task_schema="schema_system_probe_input",
            beeline_id="beeline_wms_system_v2",
            beeline_version=2,
            # **不声明 subject_ref_field**：这只盒子**就是**档案本身。
            # 它不针对某个业务对象——它描述整个系统。
            # （它一次同步的产出是全量观测，不属于任何一个需求单元。）
            trigger=(
                "发版后同步系统档案\nOR 新增/下线一个服务要更新接口清单\n"
                "OR 架构评审前拉一份当前可观测状态\nOR 发现档案跟真实部署对不上"
            ),
        ),
    ],

    result=ResultDef(
        type="wms_system_profiled",
        result_schema="schema_system_profile",
        maturity="live",
        acceptance=[
            AcceptanceRule(metric="health_endpoint_reachable", op="eq", value=True),
            AcceptanceRule(metric="service_status_up", op="eq", value=True),
            AcceptanceRule(metric="database_reachable", op="eq", value=True),
            # 基线声明的只读端点必须条条答得上来。
            # 低于 100 就是有端点失联：档案里那份接口清单开始过期了，
            # 而过期的接口清单比没有更危险——它让人以为问对了接口。
            AcceptanceRule(metric="readonly_probe_answer_rate", op="gte", value=100.0),
            AcceptanceRule(metric="aspect_status_complete", op="eq", value=True),
        ],
        exceptions=[
            # 探不到系统，谈不上档案：升级人工，而不是产出一份空档案
            ExceptionRule(condition="health_endpoint_reachable == false", action="escalate"),
            # 服务活着但数据库掉了：业务不可用，升级人工
            ExceptionRule(condition="database_reachable == false", action="escalate"),
            # 一条只读端点都没探通，这份档案没有覆盖面可言，不投递
            ExceptionRule(condition="readonly_probe_answer_rate == 0", action="no_deliver"),
        ],
        view="generic",
    ),

    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            # observe_only 的理由**不是**「腿还没实现」（腿实现了），
            # 而是 queen 根本没有运行时：kanban/trigger.py 调
            # evaluate_acceptance 时从不传 queen_decision_fn，
            # escalate 只会落到 status=REJECTED + decision=pending。
            # 三个授权位现在都是声明，放开等于放开门上的锁，
            # 而锁后面没有房间。queen 接到执行路径上之前，这里保持 observe_only。
            continuous_improvement="observe_only",
        ),
        rules=QueenRules(
            task_routing={},
            exception_handling={
                "escalation_rules": [
                    {"trigger": "health_endpoint_reachable == false", "action": "escalate_to_human"},
                    {"trigger": "database_reachable == false", "action": "escalate_to_human"},
                ],
            },
            continuous_improvement={},
        ),
    ),

    metrics=MetricsDef(
        # 每个声明都必须有采集算法（boxes/wms_system/measure.py）。
        # 这 4 条全部从 acceptance 的实测值推出——`observed` 只搬运
        # definition 自己声明的判据名，指标想读别的字段就读不到。
        quality=[
            {
                "name": "health_endpoint_up_rate",
                "definition": "健康端点探通的履约占比",
                "target": 100.0,
                "unit": "pct",
                "direction": "higher_is_better",
            },
            {
                "name": "readonly_probe_answer_rate",
                "definition": "基线只读端点的平均应答率",
                "target": 100.0,
                "unit": "pct",
                "direction": "higher_is_better",
            },
            {
                "name": "first_pass_profile_rate",
                "definition": "首次观测即通过全部 acceptance 的比例",
                "target": 95.0,
                "unit": "pct",
                "direction": "higher_is_better",
            },
        ],
        latency=[
            {
                "name": "system_probe_latency",
                "definition": "一次系统观测的端到端时长",
                "target": 30.0,
                "unit": "s",
                "direction": "lower_is_better",
            },
        ],
    ),
)


def get_definition() -> BeeBoxDefinition:
    return WMS_SYSTEM_BOX
