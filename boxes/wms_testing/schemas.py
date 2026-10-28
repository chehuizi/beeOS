"""boxes.wms_testing.schemas - WMS Testing Box 的业务 schemas

WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。

**骨架盒**：schema 是真的（input / 沿途 / 产出 的形状先立住），但产出由
`runner` 的桩返回——它**刻意不产出判据要读的 metric**。

**判据 metric 名就是这里的字段名。** 这不是风格：`acceptance` 指向一个
schema 里没有的字段，等于判了一条判不出来的判据。生成期就硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

# ---- 消费方自己声明的形状 ----
#
# 以前这里是 `ref:schema_build_artifact` / `ref:schema_requirement_set_package`
# ——**两个都指向别的盒子的 schema id**（开发盒 / 需求盒）。
# 文档（docs/beebox-definition-schema.md §4）：ref 只能引用本 definition 内。
#
# 悬空的后果是**没人校验**：那个 id 在本盒里不存在，于是「制品该长什么样」
# 「需求集该长什么样」都没定义过，投料里塞什么都行。
#
# 现在由**测试盒自己**声明它要收的两份形状——schema 归消费者所有。
# 交接时 runtime/handoff.py 按这份声明校验上游产出。
#
# 复制一份形状是有代价的（上游改了字段这里会旧），但那个代价是**响的**：
# 旧了就交接失败、当场报出来。而悬空引用的代价是**哑的**——永远没人发现。
BUILD_ARTIFACT_INPUT_SCHEMA = SchemaDef(
    id="schema_build_artifact_input",
    description="测试盒要消费的构建产物（形状由测试盒自己声明）",
    fields=[
        FieldDef(name="commit_count", type="integer", required=True),
        FieldDef(name="build_duration_s", type="number", required=True),
        FieldDef(name="unit_test_pass_rate", type="number", required=True),
        FieldDef(name="artifact_digest", type="string", required=True),
        FieldDef(name="build_failed", type="boolean", required=True),
        FieldDef(name="build_reproducibility", type="boolean", required=True),
        FieldDef(name="unit_test_green", type="boolean", required=True),
        FieldDef(name="review_coverage", type="boolean", required=True),
        FieldDef(name="artifact_traceable", type="boolean", required=True),
    ],
)

REQUIREMENT_SET_INPUT_SCHEMA = SchemaDef(
    id="schema_requirement_set_input",
    description="测试盒要消费的需求集（形状由测试盒自己声明）",
    fields=[
        # set_id 是「这批用例服务哪条需求」——测试盒的 subject_ref 从这儿取
        FieldDef(name="set_id", type="string", required=True),
        FieldDef(name="business_goal", type="string", required=False),
        FieldDef(name="requirement_count", type="integer", required=False),
        FieldDef(name="requirements", type="array", required=True, items=FieldDef(
            name="requirement", type="object", properties=[
                FieldDef(name="requirement_id", type="string"),
                FieldDef(name="description", type="string"),
                FieldDef(name="requirement_type", type="string"),
            ],
        )),
    ],
)

# ---- 可判定的预期（测试盒要消费的形状）----
#
# 测试盒要回答的是「**这条需求被满足了没有**」。要能回答这个，
# 它得先知道每条需求**怎样才算被满足**——那就是预期。
#
# 预期由建模盒产出（只有它知道哪个需求绑到了哪个 WMS 概念，
# 而探针是按概念找的），测试盒**自己声明**它要的形状——
# schema 归消费者所有。
EXPECTATION_SET_INPUT_SCHEMA = SchemaDef(
    id="schema_expectation_set_input",
    description="测试盒要消费的可判定预期（每条 WMS 绑定一条）",
    fields=[
        FieldDef(name="expectations", type="array", required=True, items=FieldDef(
            name="expectation", type="object", properties=[
                FieldDef(name="expectation_id", type="string"),
                FieldDef(name="requirement_id", type="string"),
                FieldDef(name="wms_kind", type="string"),
                FieldDef(name="statement", type="string"),
                FieldDef(name="evidence", type="string"),
            ],
        )),
    ],
)

TEST_TASK_INPUT_SCHEMA = SchemaDef(
    id="schema_test_task_input",
    description="测试任务（制品 + 需求集 + 预期 + 场景集）（一次测试任务的投料）",
    fields=[
        FieldDef(name="test_task_id", type="string", required=True),
        FieldDef(name="artifact_ref", type="ref:schema_build_artifact_input", required=True),
        FieldDef(name="requirement_ref", type="ref:schema_requirement_set_input", required=True),
        # **预期是必填的**：没有它，测试盒就不知道该验什么，
        # 跑出来的「全绿」等于「什么也没验」。
        #
        # 它从**建模盒**接力过来（不是开发盒）——预期是建模盒产出的，
        # 开发盒只是往下游递东西。让测试盒等开发盒转发，
        # 等于把「这条需求该怎样算被满足」的责任压给一个造制品的盒子。
        FieldDef(name="expectation_ref", type="ref:schema_expectation_set_input", required=True),
        FieldDef(name="scenarios", type="array", required=True, items=FieldDef(name="item", type="string")),
    ],
)

TEST_EXECUTION_SCHEMA = SchemaDef(
    id="schema_test_execution",
    description="测试执行过程（场景与用例跑下来了，尚未定性）",
    fields=[
        FieldDef(name="test_task_id", type="string", required=True),
        FieldDef(name="case_count", type="integer", required=True),
        FieldDef(name="defect_refs", type="array", required=True, items=FieldDef(name="item", type="string")),
    ],
)

TEST_VERDICT_SCHEMA = SchemaDef(
    id="schema_test_verdict",
    description="测试定性结论（覆盖率 / 缺陷 / 是否放行）",
    fields=[
        FieldDef(name="test_task_id", type="string", required=True),
        FieldDef(name="scenario_count", type="integer", required=True),
        FieldDef(name="open_defect_count", type="integer", required=True),
    ],
)

# 观测明细的**固定词表**。
#
# 探针的细节千差万别，但不能让它是自由形状的 object——
# **object 字段必须声明 properties**（core.models 的校验就是这么定的），
# 而且自由形状的字段消费方读不了：「里面有什么键」没人知道。
#
# 所以定一份**所有探针共用**的键表。探针写了表外的键也不会报错
# （契约校验容忍多余字段），但表内的键是**承诺过的**——
# 看板和分析盒可以照着这组键渲染。
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


# 一条观测：一条预期 ↔ 一次实测
TEST_OBSERVATION_SCHEMA = SchemaDef(
    id="schema_test_observation",
    description="一条预期的实测结果（pass / fail / not_applicable / indeterminate）",
    fields=[
        FieldDef(name="expectation_id", type="string", required=True),
        FieldDef(name="requirement_id", type="string", required=True),
        FieldDef(name="wms_kind", type="string", required=True),
        FieldDef(name="statement", type="string", required=False),
        FieldDef(name="evidence", type="string", required=False),
        FieldDef(
            name="outcome",
            type="enum",
            values=["pass", "fail", "not_applicable", "indeterminate"],
            required=True,
            description="pass=实测满足预期；fail=不满足；"
                        "not_applicable=没有探针；indeterminate=现场判不了",
        ),
        FieldDef(name="summary", type="string", required=True),
        FieldDef(name="details", type="object", required=False,
                 properties=OBSERVATION_DETAIL_PROPERTIES),
    ],
)

# 场景事实：这次造了什么数（供看板 / 分析盒看）
SCENARIO_FACT_SCHEMA = SchemaDef(
    id="schema_test_scenario_fact",
    description="一个场景造出来的数据（批次与分配结果）",
    fields=[
        FieldDef(name="scenario_id", type="string", required=True),
        FieldDef(name="sku_code", type="string", required=False),
        FieldDef(name="batches", type="array", required=False, items=FieldDef(
            name="batch", type="object", properties=[
                FieldDef(name="batch_no", type="string"),
                FieldDef(name="expire_date", type="string"),
                FieldDef(name="produced_date", type="string"),
                FieldDef(name="qty", type="number"),
            ])),
        FieldDef(name="allocated_batch_no", type="string", required=False),
        FieldDef(name="allocated_qty", type="number", required=False),
    ],
)

TEST_REPORT_SCHEMA = SchemaDef(
    id="schema_test_report",
    description="测试报告（场景 / 用例 / 缺陷 / 逐条观测）",
    fields=[
        FieldDef(name="scenario_count", type="integer", required=True),
        FieldDef(name="case_count", type="integer", required=True),
        # **三态**：一条都没验出来时是 n/a（没验到），不是 0%（全都没过）。
        # 但它必须能过下游的交接校验（运维盒声明的形状是 number）——
        # 所以类型是 number_or_na，不是 number。
        FieldDef(name="case_pass_rate", type="number_or_na", required=True),
        FieldDef(name="open_defect_count", type="integer", required=True),
        FieldDef(name="requirement_case_coverage", type="boolean", required=True),
        FieldDef(name="scenario_coverage", type="boolean", required=True),
        FieldDef(name="no_blocking_defect", type="boolean", required=True),
        # **「这次验出了点东西」**——
        # 没有它，「一条都没验出来」会显示成「几条通过 + 几条用不上」，
        # 读起来像「基本没问题」。那是假信号。
        FieldDef(name="verification_determined", type="boolean", required=True),
        # **「这次验的是哪份构建」**
        #
        # 观测里必须带着制品摘要（开发盒交付的那份构建）。
        # 没有它，「临期 SKU 数是 1」这句话是无主的——
        # 不知道测的是哪份代码，就没法说这次验算不算数。
        FieldDef(name="artifact_identified", type="boolean", required=True),
        # **产出里一直有这个字段，schema 里一直没写**——
        # 消费方（运维盒）要按它归档「这次验的是哪一次」，就得更。
        FieldDef(name="test_task_id", type="string", required=False),
        # ---- 证据 ----
        FieldDef(name="observations", type="array", required=True,
                 items=FieldDef(name="observation", type="object", properties=[
                     FieldDef(name="expectation_id", type="string"),
                     FieldDef(name="requirement_id", type="string"),
                     FieldDef(name="wms_kind", type="string"),
                     FieldDef(name="statement", type="string", required=False),
                     FieldDef(name="evidence", type="string", required=False),
                     FieldDef(name="outcome", type="string"),
                     FieldDef(name="summary", type="string"),
                     FieldDef(name="details", type="object", required=False,
                              properties=OBSERVATION_DETAIL_PROPERTIES),
                 ])),
        FieldDef(name="passed_count", type="integer", required=False),
        FieldDef(name="failed_count", type="integer", required=False),
        FieldDef(name="not_applicable_count", type="integer", required=False),
        FieldDef(name="indeterminate_count", type="integer", required=False),
        FieldDef(name="failed_expectations", type="array", required=False,
                 items=FieldDef(name="failed", type="object", properties=[
                     FieldDef(name="expectation_id", type="string"),
                     FieldDef(name="requirement_id", type="string"),
                     FieldDef(name="wms_kind", type="string"),
                     FieldDef(name="summary", type="string"),
                     FieldDef(name="details", type="object", required=False,
                              properties=OBSERVATION_DETAIL_PROPERTIES),
                 ])),
        FieldDef(name="not_applicable_expectations", type="array", required=False,
                 items=FieldDef(name="na", type="object", properties=[
                     FieldDef(name="expectation_id", type="string"),
                     FieldDef(name="requirement_id", type="string"),
                     FieldDef(name="wms_kind", type="string"),
                     FieldDef(name="summary", type="string"),
                 ])),
        FieldDef(name="indeterminate_expectations", type="array", required=False,
                 items=FieldDef(name="ind", type="object", properties=[
                     FieldDef(name="expectation_id", type="string"),
                     FieldDef(name="requirement_id", type="string"),
                     FieldDef(name="wms_kind", type="string"),
                     FieldDef(name="summary", type="string"),
                 ])),
        FieldDef(name="uncovered_requirements", type="array", required=False,
                 items=FieldDef(name="r", type="string")),
        FieldDef(name="unknown_scenarios", type="array", required=False,
                 items=FieldDef(name="s", type="string")),
        FieldDef(name="scenario_facts", type="array", required=False,
                 items=FieldDef(name="fact", type="object", properties=[
                     FieldDef(name="scenario_id", type="string"),
                     FieldDef(name="sku_code", type="string", required=False),
                     FieldDef(name="batches", type="array", required=False,
                              items=FieldDef(name="batch", type="object", properties=[
                                  FieldDef(name="batch_no", type="string"),
                                  FieldDef(name="expire_date", type="string"),
                                  FieldDef(name="produced_date", type="string"),
                                  FieldDef(name="qty", type="number"),
                              ])),
                     FieldDef(name="allocated_batch_no", type="string", required=False),
                     FieldDef(name="allocated_qty", type="number", required=False),
                 ])),
    ],
)


ALL_SCHEMAS = [
    BUILD_ARTIFACT_INPUT_SCHEMA,
    REQUIREMENT_SET_INPUT_SCHEMA,
    EXPECTATION_SET_INPUT_SCHEMA,
    TEST_TASK_INPUT_SCHEMA,
    TEST_EXECUTION_SCHEMA,
    TEST_VERDICT_SCHEMA,
    TEST_OBSERVATION_SCHEMA,
    SCENARIO_FACT_SCHEMA,
    TEST_REPORT_SCHEMA,
]
