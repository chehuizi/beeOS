"""boxes.wms_ops.schemas - WMS Ops Box 的业务 schemas

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、
以及把线上真实状态回写进系统档案。

**这一版的关键变化：输入接上了上游**。
以前运维盒的四个输入全是自带原始值（`ops_task_id` / `change_request` /
`alerts` / `rollback_target`），一个 `ref:` 都没有——**它跟这条链没有任何
关系**，所以它只能是骨架盒：没东西可验，也没法验。

现在有两个 `ref:` 输入：

| 输入 | 来自 | 运维盒拿它干什么 |
|---|---|---|
| `verification_ref` | 测试盒的验收报告 | 「这次要放行的变更，验过没有」 |
| `artifact_ref` | 开发盒的制品 | 「要放行的是哪份构建」「回滚回哪一版」 |

**形状由消费方自己声明**（schema 归消费者所有）：这里抄一份测试盒/开发盒
的产出形状，交接时 runtime/handoff.py 按**这份**声明校验上游产出。
抄两份会漂，但漂了会**响**（交接当场失败）——比悬空引用那种「永远没人
发现」好得多。

**判据 metric 名就是这里的字段名。** `acceptance` 指向一个 schema 里没有的
字段，等于判了一条判不出来的判据。生成期就硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef



# 线上真实状态的属性（**观测现场的那一份**）
OPS_RUNTIME_STATE_PROPERTIES = [
    FieldDef(name="observed_at", type="string", required=False),
    FieldDef(name="base_url", type="string", required=False),
    FieldDef(name="mode", type="string", required=False),
    FieldDef(name="reachable", type="boolean", required=False),
    FieldDef(name="dashboard", type="object", required=False,
             properties=[FieldDef(name="field", type="string", required=False)]),
    FieldDef(name="health", type="object", required=False,
             properties=[FieldDef(name="field", type="string", required=False)]),
    FieldDef(name="business_fields", type="array", required=False,
             items=FieldDef(name="f", type="string")),
    FieldDef(name="error", type="string", required=False),
]

# 观测明细的属性（**跟着观测走**，不能等下游回头去翻计划书）
#
# 「这次测的是哪个实例」必须写在观测里：生产的数跟验证实例的数
# 不是一回事，混在一起看的人会以为它们是同一件事。
OPS_OBSERVATION_DETAIL_PROPERTIES = [
    FieldDef(name="measured_base_url", type="string", required=False),
    FieldDef(name="measured_mode", type="string", required=False),
    FieldDef(name="field", type="string", required=False),
    FieldDef(name="value", type="string", required=False),
    FieldDef(name="business_fields", type="array", required=False,
             items=FieldDef(name="f", type="string")),
    FieldDef(name="error", type="string", required=False),
]

# ---- 消费方声明的形状：测试盒的验收报告 ----
#
# 运维盒只读它关心的那几个键（判据结果 + 观测列表）。
# 观测列表是运维盒在生产上复算的起点：**哪些概念要在生产上再验一遍**。
OPS_VERIFICATION_INPUT_SCHEMA = SchemaDef(
    id="schema_ops_verification_input",
    description="运维盒要消费的验收报告（形状由运维盒自己声明）",
    fields=[
        FieldDef(name="test_task_id", type="string", required=False),
        FieldDef(name="case_pass_rate", type="number", required=False),
        FieldDef(name="open_defect_count", type="integer", required=False),
        FieldDef(name="no_blocking_defect", type="boolean", required=False),
        FieldDef(name="verification_determined", type="boolean", required=False),
        FieldDef(name="artifact_identified", type="boolean", required=False),
        FieldDef(
            name="observations", type="array", required=False,
            items=FieldDef(
                name="observation", type="object", properties=[
                    FieldDef(name="expectation_id", type="string"),
                    FieldDef(name="requirement_id", type="string"),
                    FieldDef(name="wms_kind", type="string"),
                    FieldDef(name="statement", type="string", required=False),
                    FieldDef(name="evidence", type="string", required=False),
                    FieldDef(
                        name="outcome",
                        type="enum",
                        values=["pass", "fail", "not_applicable", "indeterminate"],
                    ),
                    FieldDef(name="summary", type="string", required=False),
                    FieldDef(name="details", type="object", required=False,
                             properties=OPS_OBSERVATION_DETAIL_PROPERTIES),
                ],
            ),
        ),
    ],
)

# ---- 消费方声明的形状：开发盒的制品 ----
OPS_ARTIFACT_INPUT_SCHEMA = SchemaDef(
    id="schema_ops_artifact_input",
    description="运维盒要消费的构建制品（形状由运维盒自己声明）",
    fields=[
        FieldDef(name="commit_count", type="integer", required=False),
        FieldDef(name="artifact_digest", type="string", required=False),
        FieldDef(name="build_failed", type="boolean", required=False),
        FieldDef(name="build_reproducibility", type="boolean", required=False),
        FieldDef(name="unit_test_green", type="boolean", required=False),
        FieldDef(name="review_coverage", type="boolean", required=False),
        FieldDef(name="artifact_traceable", type="boolean", required=False),
        FieldDef(name="commit_sha", type="string", required=False),
        FieldDef(name="branch", type="string", required=False),
        FieldDef(name="artifact_path", type="string", required=False),
    ],
)

OPS_TASK_INPUT_SCHEMA = SchemaDef(
    id="schema_ops_task_input",
    description="运维任务（变更单 / 告警 / 回滚请求）（一次运维任务的投料）",
    fields=[
        FieldDef(name="ops_task_id", type="string", required=True),
        # 变更描述：一行一条。留空 = 这次没有要放行的变更。
        FieldDef(name="change_request", type="string", required=False),
        # 生产告警：每一条都要有处置记录。
        FieldDef(name="alerts", type="array", required=False,
                 items=FieldDef(name="item", type="string")),
        # 回滚目标：git 引用（sha / 分支）或制品摘要（sha256:…）。
        FieldDef(name="rollback_target", type="string", required=False),
        # ---- 接上上游 ----
        FieldDef(
            name="verification_ref", type="ref:schema_ops_verification_input",
            required=False,
            description="测试盒的验收报告：这次要放行的变更验过没有",
        ),
        FieldDef(
            name="artifact_ref", type="ref:schema_ops_artifact_input",
            required=False,
            description="开发盒的制品：要放行的是哪份构建、回滚回哪一版",
        ),
        # 生产地址（留空取环境变量 BEEOS_WMS_BASE_URL）
        FieldDef(name="target_base_url", type="string", required=False),
        # 实例模式（留空取环境变量 BEEOS_WMS_MODE，默认 production）——
        # 链上按盒子切实例用：测试盒在验证实例上造单据，
        # 运维盒读生产。两个盒子共用一套环境变量，不给「按任务指定」的口子
        # 就只能一个一个改环境变量。
        FieldDef(name="target_mode", type="string", required=False),
    ],
)

OPS_HANDLING_SCHEMA = SchemaDef(
    id="schema_ops_handling",
    description="处置过程（放行什么 / 处置什么 / 回滚回哪 / 读生产）",
    fields=[
        FieldDef(name="ops_task_id", type="string", required=True),
        FieldDef(name="change_count", type="integer", required=True),
        FieldDef(name="incident_count", type="integer", required=True),
        # ---- 往下带 ----
        FieldDef(name="changes", type="array", required=False,
                 items=FieldDef(name="change", type="object", properties=[
                     FieldDef(name="change_id", type="string"),
                     FieldDef(name="text", type="string"),
                     FieldDef(name="approved", type="boolean"),
                     FieldDef(name="reason", type="string", required=False),
                 ])),
        FieldDef(name="incidents", type="array", required=False,
                 items=FieldDef(name="incident", type="object", properties=[
                     FieldDef(name="incident_id", type="string"),
                     FieldDef(name="alert", type="string"),
                     FieldDef(name="category", type="string"),
                     FieldDef(name="status", type="string"),
                     FieldDef(name="recorded_at", type="string"),
                 ])),
        FieldDef(name="rollback_target", type="string", required=False),
        FieldDef(name="rollback_ready", type="boolean", required=False),
        FieldDef(name="rollback_reason", type="string", required=False),
        FieldDef(name="target_base_url", type="string", required=False),
        FieldDef(name="measured_mode", type="string", required=False),
        FieldDef(name="runtime_state", type="object", required=False,
                     properties=OPS_RUNTIME_STATE_PROPERTIES),
        FieldDef(name="production_observations", type="array", required=False,
                 items=FieldDef(name="observation", type="object", properties=[
                     FieldDef(name="expectation_id", type="string"),
                     FieldDef(name="requirement_id", type="string"),
                     FieldDef(name="wms_kind", type="string"),
                     FieldDef(name="statement", type="string", required=False),
                     FieldDef(name="evidence", type="string", required=False),
                     FieldDef(
                         name="outcome",
                         type="enum",
                         values=["pass", "fail", "not_applicable", "indeterminate"],
                     ),
                     FieldDef(name="summary", type="string", required=False),
                     FieldDef(name="details", type="object", required=False,
                             properties=OPS_OBSERVATION_DETAIL_PROPERTIES),
                 ])),
        FieldDef(name="verification_ref", type="object", required=False,
                     properties=OPS_VERIFICATION_INPUT_SCHEMA.fields),
        FieldDef(name="artifact_ref", type="object", required=False,
                     properties=OPS_ARTIFACT_INPUT_SCHEMA.fields),
    ],
)

OPS_VERDICT_SCHEMA = SchemaDef(
    id="schema_ops_verdict",
    description="运维定性结论（审批 / 回滚能力 / 记录 / 是否已回写档案）",
    fields=[
        FieldDef(name="ops_task_id", type="string", required=True),
        FieldDef(name="mttr_minutes", type="number", required=True),
        FieldDef(name="profile_ref", type="string", required=True),
        # ---- 判据读的量（名字就是 acceptance 里写的 metric 名）----
        FieldDef(name="change_approval_rate", type="boolean_or_na", required=True),
        FieldDef(name="rollback_readiness", type="boolean_or_na", required=True),
        FieldDef(name="incident_documented", type="boolean_or_na", required=True),
        FieldDef(name="runtime_state_recorded", type="boolean", required=True),
        # **「这次真的干了事」**——
        # 没有它，一只什么都没做的盒子会显示成「几条 n/a + 一条通过」。
        FieldDef(name="ops_determined", type="boolean", required=True),
        # ---- 证据 ----
        FieldDef(name="approval_reason", type="string", required=False),
        FieldDef(name="rollback_reason", type="string", required=False),
        FieldDef(name="incident_reason", type="string", required=False),
        FieldDef(name="runtime_reason", type="string", required=False),
        FieldDef(name="mttr_measurable", type="boolean", required=False),
    ],
)

OPS_RECORD_SCHEMA = SchemaDef(
    id="schema_ops_record",
    description="运维记录（变更 / 故障 / 回滚结果）",
    fields=[
        FieldDef(name="change_count", type="integer", required=True),
        FieldDef(name="incident_count", type="integer", required=True),
        FieldDef(name="mttr_minutes", type="number", required=True),
        FieldDef(name="rollback_ready", type="boolean", required=True),
        FieldDef(name="change_approval_rate", type="boolean_or_na", required=True),
        FieldDef(name="rollback_readiness", type="boolean_or_na", required=True),
        FieldDef(name="incident_documented", type="boolean_or_na", required=True),
        FieldDef(name="runtime_state_recorded", type="boolean", required=True),
        FieldDef(name="ops_determined", type="boolean", required=True),
        # ---- 证据 ----
        FieldDef(name="ops_task_id", type="string", required=False),
        FieldDef(name="changes", type="array", required=False,
                 items=FieldDef(name="change", type="object", properties=[
                     FieldDef(name="change_id", type="string"),
                     FieldDef(name="text", type="string"),
                     FieldDef(name="approved", type="boolean"),
                     FieldDef(name="reason", type="string", required=False),
                 ])),
        FieldDef(name="incidents", type="array", required=False,
                 items=FieldDef(name="incident", type="object", properties=[
                     FieldDef(name="incident_id", type="string"),
                     FieldDef(name="alert", type="string"),
                     FieldDef(name="category", type="string"),
                     FieldDef(name="status", type="string"),
                     FieldDef(name="recorded_at", type="string"),
                 ])),
        FieldDef(name="runtime_state", type="object", required=False,
                     properties=OPS_RUNTIME_STATE_PROPERTIES),
        FieldDef(name="production_observations", type="array", required=False,
                 items=FieldDef(name="observation", type="object", properties=[
                     FieldDef(name="expectation_id", type="string"),
                     FieldDef(name="requirement_id", type="string"),
                     FieldDef(name="wms_kind", type="string"),
                     FieldDef(name="outcome", type="string", required=False),
                     FieldDef(name="summary", type="string", required=False),
                 ])),
        # **合并后的观测**（分析盒直接消费这一份）
        #
        # 合并规则（机械的，不靠感觉）：
        # - 验收实例上的观测打底
        # - **生产上的确定结论（pass / fail）覆盖它**——生产是现场
        # - 生产上「验不了」（indeterminate）**不覆盖**：它没有信息量，
        #   拿它盖掉一条实测，等于把一条真的观测抹了
        FieldDef(name="observations", type="array", required=False,
                 items=FieldDef(
                     name="observation", type="object", properties=[
                         FieldDef(name="expectation_id", type="string"),
                         FieldDef(name="requirement_id", type="string"),
                         FieldDef(name="wms_kind", type="string"),
                         FieldDef(name="statement", type="string", required=False),
                         FieldDef(name="evidence", type="string", required=False),
                         FieldDef(
                             name="outcome",
                             type="enum",
                             values=["pass", "fail", "not_applicable", "indeterminate"],
                         ),
                         FieldDef(name="summary", type="string", required=False),
                         FieldDef(name="details", type="object", required=False,
                                  properties=OPS_OBSERVATION_DETAIL_PROPERTIES),
                     ])),
        FieldDef(name="approval_reason", type="string", required=False),
        FieldDef(name="rollback_reason", type="string", required=False),
        FieldDef(name="incident_reason", type="string", required=False),
        FieldDef(name="runtime_reason", type="string", required=False),
        FieldDef(name="mttr_measurable", type="boolean", required=False),
        FieldDef(name="profile_ref", type="string", required=False),
    ],
)


ALL_SCHEMAS = [
    OPS_VERIFICATION_INPUT_SCHEMA,
    OPS_ARTIFACT_INPUT_SCHEMA,
    OPS_TASK_INPUT_SCHEMA,
    OPS_HANDLING_SCHEMA,
    OPS_VERDICT_SCHEMA,
    OPS_RECORD_SCHEMA,
]
