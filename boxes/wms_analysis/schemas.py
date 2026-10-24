"""boxes.wms_analysis.schemas - WMS Analysis Box 的业务 schemas

WMS 线上效果分析盒——上线之后拿真实运行数据回头看：**当初那条需求，
线上到底满足了没有**。它读的是测试盒（/ 运维盒）跑出来的实测值，
拿需求声明的预期值去比，落差就是下一轮需求的种子。

**判据 metric 名就是这里的字段名。** 这不是风格：`acceptance` 指向一个
schema 里没有的字段，等于判了一条判不出来的判据。生成期就硬校验。

**这组 schema 现在是「真」的形状**（以前分析盒是骨架盒，产出是桩）：
- 输入 = 预期（结构化）+ 实测观测（从测试盒接力过来）
- 输出 = 逐项比对 → 落差归因 → 下一轮需求种子
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef


# ---- 消费方自己声明的形状 ----
#
# 观测（实测结果）的形状由**分析盒自己声明**——
# 别的盒子声明的形状引用不到（ref 只能引用本 definition 内），
# 而悬空引用的代价是**哑的**：投料里塞什么都行，没人发现。
OBSERVATION_DETAIL_PROPERTIES = [
    FieldDef(name="allocated", type="string", required=False),
    FieldDef(name="allocated_expire_date", type="string", required=False),
    FieldDef(name="expected_batch_no", type="string", required=False),
    FieldDef(name="expected_expire_date", type="string", required=False),
    # **诊断字段一律 required=False**：
    # 定位证据是「有就报、没有也不该拦住交接」的东西。
    # 以前把 produced_date 声明成必填，而两个探针产出的键集不一样
    # （一个带 produced_date、一个不带）→ 交接直接被拒，
    # 表现是「分析盒收不到测试盒的产出」。
    FieldDef(name="candidates", type="array", required=False, items=FieldDef(
        name="candidate", type="object", properties=[
            FieldDef(name="batch_no", type="string", required=True),
            FieldDef(name="expire_date", type="string", required=False),
            FieldDef(name="qty", type="number", required=False),
        ])),
    FieldDef(name="stored_inventory", type="array", required=False, items=FieldDef(
        name="stored", type="object", properties=[
            FieldDef(name="batch_no", type="string", required=True),
            FieldDef(name="expire_date", type="string", required=False),
            FieldDef(name="produced_date", type="string", required=False),
            FieldDef(name="location_code", type="string", required=False),
            FieldDef(name="on_hand_qty", type="number", required=False),
        ])),
    FieldDef(name="expiringSoonSkuCount", type="number", required=False),
    FieldDef(name="near_batch", type="string", required=False),
    FieldDef(name="near_batch_still_has_expiry", type="boolean", required=False),
    FieldDef(name="expiring_window_days", type="integer", required=False),
    FieldDef(name="dashboard_keys", type="array", required=False,
             items=FieldDef(name="k", type="string")),
    FieldDef(name="concept", type="string", required=False),
    # ---- 「这次测的是哪个实例」----
    #
    # **没有这两个字段，报告就分不清「我在验证实例上量的」
    # 和「我在生产上量的」**。运维盒没实现时，这条信息无处可去——
    # 于是「生产上满没满足」这件事在链上根本答不了，
    # 连「答不了」这件事都看不见（只看到一句「临期 SKU 数是 0」）。
    FieldDef(name="measured_base_url", type="string", required=False),
    FieldDef(name="measured_mode", type="string", required=False),
    FieldDef(name="scenario_id", type="string", required=False),
    FieldDef(name="mode", type="string", required=False),
    FieldDef(name="outbound_id", type="integer", required=False),
]

ANALYSIS_OBSERVATION_INPUT_SCHEMA = SchemaDef(
    id="schema_analysis_observation_input",
    description="分析盒要消费的实测观测（每条预期一条）",
    fields=[
        FieldDef(
            name="observations",
            type="array",
            required=True,
            items=FieldDef(
                name="observation",
                type="object",
                properties=[
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
                             properties=OBSERVATION_DETAIL_PROPERTIES),
                ],
            ),
        ),
    ],
)

ANALYSIS_INPUT_SCHEMA = SchemaDef(
    id="schema_analysis_input",
    description="分析投料（需求预期 + 线上实测指标）（一次效果分析的投料）",
    fields=[
        FieldDef(name="analysis_id", type="string", required=True),
        FieldDef(name="requirement_ref", type="string", required=True),
        # **expected_metrics 从「字符串数组」升级成结构化**。
        #
        # 以前是 `array of string`——一串指标名。拿着它只能做
        # 「这个名字在实测结果里存不存在」的检查：
        # 名字在 → 通过。**那不是验证业务价值，是验证词汇表。**
        #
        # 现在每条预期带：
        #   expectation_id —— 对上哪一条预期
        #   requirement_id —— 来自哪条需求（可追溯）
        #   wms_kind       —— 落在哪个 WMS 概念上
        #   statement      —— 该概念的正式表述（**这条需求该怎样才算被满足**）
        #
        # 有了 statement，落差才能归因到「哪个要求没达成」，
        # 而不是「哪个名字没对上」。
        FieldDef(
            name="expected_metrics",
            type="array",
            required=True,
            items=FieldDef(
                name="expected_metric",
                type="object",
                properties=[
                    FieldDef(name="expectation_id", type="string"),
                    FieldDef(name="requirement_id", type="string"),
                    FieldDef(name="wms_kind", type="string"),
                    FieldDef(name="statement", type="string"),
                ],
            ),
        ),
        # **实测观测从哪来**：测试盒（或运维盒）那次的产出。
        # 没有它就无从比对——分析盒不能自己去跑探针（那是测试盒的活）。
        FieldDef(
            name="observation_ref",
            type="ref:schema_analysis_observation_input",
            required=True,
        ),
        FieldDef(name="observed_window", type="string", required=True),
    ],
)

ANALYSIS_COMPARISON_SCHEMA = SchemaDef(
    id="schema_analysis_comparison",
    description="预期 vs 实测的逐项比对",
    fields=[
        FieldDef(name="analysis_id", type="string", required=True),
        FieldDef(name="compared_metric_count", type="integer", required=True),
        FieldDef(name="gap_count", type="integer", required=True),
    ],
)

ANALYSIS_FINDING_SCHEMA = SchemaDef(
    id="schema_analysis_finding",
    description="分析结论（哪些达标、哪些有落差、落差来自哪）",
    fields=[
        FieldDef(name="analysis_id", type="string", required=True),
        FieldDef(name="converged", type="boolean", required=True),
        FieldDef(name="next_requirement_seed", type="string", required=True),
    ],
)

# ---- 落差的一条归因 ----
ANALYSIS_GAP_SCHEMA = SchemaDef(
    id="schema_analysis_gap",
    description="一条落差：哪条需求、哪个概念、实测是什么、证据在哪",
    fields=[
        FieldDef(name="expectation_id", type="string", required=True),
        FieldDef(name="requirement_id", type="string", required=True),
        FieldDef(name="wms_kind", type="string", required=True),
        FieldDef(name="statement", type="string", required=False),
        FieldDef(name="evidence", type="string", required=False),
        FieldDef(name="summary", type="string", required=True),
        FieldDef(name="details", type="object", required=False,
                 properties=OBSERVATION_DETAIL_PROPERTIES),
        FieldDef(name="seed", type="string", required=True),
    ],
)

ANALYSIS_RECORD_SCHEMA = SchemaDef(
    id="schema_analysis_record",
    description="效果分析记录（归档用）",
    fields=[
        FieldDef(name="compared_metric_count", type="integer", required=True),
        FieldDef(name="gap_count", type="integer", required=True),
        FieldDef(name="convergence_rate", type="boolean", required=True),
        FieldDef(name="requirement_traceability", type="boolean", required=True),
        FieldDef(name="gap_attribution", type="boolean", required=True),
        FieldDef(name="next_requirement_seeded", type="boolean", required=True),
        # ---- 判据之外的分项：读者要知道「比了几条、过了几条、漏了几条」----
        FieldDef(name="passed_count", type="integer", required=False),
        FieldDef(name="failed_count", type="integer", required=False),
        FieldDef(name="not_observed_count", type="integer", required=False),
        FieldDef(name="analysis_id", type="string", required=False),
        FieldDef(name="requirement_ref", type="string", required=False),
        FieldDef(name="observed_window", type="string", required=False),
        # ---- 落差逐条带定位证据 ----
        FieldDef(name="gaps", type="array", required=False, items=FieldDef(
            name="gap", type="object", properties=[
                FieldDef(name="expectation_id", type="string"),
                FieldDef(name="requirement_id", type="string"),
                FieldDef(name="wms_kind", type="string"),
                FieldDef(name="statement", type="string", required=False),
                FieldDef(name="evidence", type="string", required=False),
                FieldDef(name="summary", type="string"),
                FieldDef(name="details", type="object", required=False,
                         properties=OBSERVATION_DETAIL_PROPERTIES),
                FieldDef(name="seed", type="string"),
            ])),
        # ---- 下一轮需求的种子 ----
        FieldDef(name="next_requirement_seed", type="string", required=False),
    ],
)


ALL_SCHEMAS = [
    ANALYSIS_INPUT_SCHEMA,
    ANALYSIS_OBSERVATION_INPUT_SCHEMA,
    ANALYSIS_COMPARISON_SCHEMA,
    ANALYSIS_FINDING_SCHEMA,
    ANALYSIS_GAP_SCHEMA,
    ANALYSIS_RECORD_SCHEMA,
]
