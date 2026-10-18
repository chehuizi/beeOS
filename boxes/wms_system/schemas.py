"""boxes.wms_system.schemas - WMS System Box 的业务 schemas

WMS 系统档案盒——把「真实在跑的那套 WMS 是什么样」写成一份**带出处、
带缺口**的档案。

## 这只盒子描述哪 6 个维度

| 维度 | 今天的拿法 |
|---|---|
| 服务存活 | 探 `/api/v1/health` |
| 数据库连通 | 探同一个端点的 `database` 字段 |
| 对外接口清单 | 基线声明 + 只读端点逐条探测 |
| 数据表 | 基线声明（Flyway 迁移） |
| 部署版本 | **拿不到**——jar 无 git.properties，pom 未配 git-commit-id |
| 外部依赖 | **拿不到**——系统没有可查询的依赖清单 |

## 关键设计：每个维度自带状态

档案里没有一个裸数字。每条维度都带 `status`，取值三选一：

- `observed` —— 这次真的探到了
- `declared_from_baseline` —— 只有声明值，这次没能验证；**必须写出处**
- `unverifiable` —— 拿不到，**必须写为什么拿不到**

理由：盒子自己的 `description` 曾经在介绍 6 个维度，其中 4 个今天拿不到。
一份看起来完整、实际上有一半是编的档案，比一份明说「这四条我拿不到」
的档案危险得多——前者不会让人去补。

**判据只判「状态标注是否如实」，不判「拿到了什么」。**
拿到什么是运气，标得实不实是纪律。

## 判据 metric 名就是本文件的字段名

这不是风格约定：`acceptance` 指向 schema 里没有的字段，等于判了一条
判不出来的判据。生成期硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef


# 维度状态的合法取值。同时被 runner 写入和判据校验使用——
# 它是一个契约，不是一句注释。
ASPECT_STATUS_VALUES = ["observed", "declared_from_baseline", "unverifiable"]


SYSTEM_PROBE_INPUT_SCHEMA = SchemaDef(
    id="schema_system_probe_input",
    description="投料：要对哪个地址做观测（留空则用基线里的默认地址）",
    fields=[
        FieldDef(
            name="target_base_url",
            type="string",
            required=False,
            description="被观测系统的根地址，如 http://127.0.0.1:8080；留空取环境变量 BEEOS_WMS_BASE_URL，再留空取基线默认值",
        ),
        FieldDef(
            name="timeout_seconds",
            type="integer",
            required=False,
            description="单次探测超时（秒），默认 8",
        ),
        FieldDef(
            name="readonly_endpoints",
            type="array",
            required=False,
            items=FieldDef(name="item", type="string"),
            description="要探测的只读端点（相对 target_base_url）；留空取基线全量。用于缩小探测范围，不是用于扩大——写操作不接受投料指定",
        ),
    ],
)


SYSTEM_PROBE_PLAN_SCHEMA = SchemaDef(
    id="schema_system_probe_plan",
    description="探测计划：解析完的观测目标（地址已定、端点已展开、基线已挂上）",
    fields=[
        FieldDef(name="target_base_url", type="string", required=True),
        FieldDef(name="timeout_seconds", type="integer", required=True),
        FieldDef(name="health_path", type="string", required=True),
        FieldDef(
            name="readonly_endpoints",
            type="array",
            required=True,
            items=FieldDef(name="item", type="string"),
        ),
        FieldDef(name="baseline_id", type="string", required=True),
        FieldDef(name="baseline_commit", type="string", required=True),
    ],
)


SYSTEM_OBSERVATION_SCHEMA = SchemaDef(
    id="schema_system_observation",
    description="观测结果：这次真的探到了什么（探不到的如实记为未响应）",
    fields=[
        FieldDef(name="observed_at", type="string", required=True),
        # 探的是哪个地址，必须**跟着观测走**，不能等下游回头去翻计划书。
        # 计划说探 A、实际探了 B 而档案写 A，这种 bug 一旦发生没人查得出来：
        # 探针不会报告自己探错了地方。
        FieldDef(name="target_base_url", type="string", required=True),
        FieldDef(name="baseline_id", type="string", required=True),
        FieldDef(name="baseline_commit", type="string", required=True),
        FieldDef(name="health_path", type="string", required=True),
        FieldDef(name="health_reachable", type="boolean", required=True),
        FieldDef(name="health_status", type="string", required=True),
        FieldDef(name="health_database", type="string", required=True),
        FieldDef(
            name="endpoint_results",
            type="array",
            required=True,
            items=FieldDef(name="item", type="object", properties=[
                FieldDef(name="path", type="string", required=True),
                FieldDef(name="reached", type="boolean", required=True),
                FieldDef(name="status_code", type="integer", required=False),
                FieldDef(name="envelope_ok", type="boolean", required=False),
                FieldDef(name="error", type="string", required=False),
            ]),
        ),
        FieldDef(name="endpoint_probed_count", type="integer", required=True),
        FieldDef(name="endpoint_answered_count", type="integer", required=True),
    ],
)


SYSTEM_PROFILE_SCHEMA = SchemaDef(
    id="schema_system_profile",
    description="系统档案：真实在跑的 WMS 的描述，每条维度自带来源与状态",
    fields=[
        FieldDef(name="baseline_id", type="string", required=True),
        FieldDef(name="baseline_commit", type="string", required=True),
        FieldDef(name="observed_at", type="string", required=True),
        FieldDef(name="target_base_url", type="string", required=True),

        # --- 判据读数（5 条 acceptance 全部落在这里）---
        FieldDef(
            name="health_endpoint_reachable",
            type="boolean",
            required=True,
            description="健康端点这次是否探通",
        ),
        FieldDef(
            name="service_status_up",
            type="boolean",
            required=True,
            description="健康端点自报 service status 是否为 UP",
        ),
        FieldDef(
            name="database_reachable",
            type="boolean",
            required=True,
            description="健康端点自报 database 是否为 UP",
        ),
        FieldDef(
            name="readonly_probe_answer_rate",
            type="number",
            required=True,
            description="基线声明的只读端点里，这次拿到合法响应的比例（百分数）",
        ),
        FieldDef(
            name="aspect_status_complete",
            type="boolean",
            required=True,
            description=(
                "每条维度都带合法 status，且每条 declared_from_baseline 写了出处、"
                "每条 unverifiable 写了原因。**这是「诚实」本身的判据**："
                "它不关心档案内容对不对，只关心档案有没有假装自己是全知的"
            ),
        ),

        # --- 计数 ---
        FieldDef(name="aspect_count", type="integer", required=True),
        FieldDef(name="observed_aspect_count", type="integer", required=True),
        FieldDef(name="declared_only_aspect_count", type="integer", required=True),
        FieldDef(name="unverifiable_aspect_count", type="integer", required=True),
        FieldDef(name="readonly_endpoint_count", type="integer", required=True),
        FieldDef(name="readonly_endpoint_answered_count", type="integer", required=True),

        # --- 档案本体 ---
        FieldDef(
            name="aspects",
            type="array",
            required=True,
            items=FieldDef(
                name="item",
                type="object",
                properties=[
                    FieldDef(name="aspect", type="string", required=True),
                    FieldDef(
                        name="status",
                        type="enum",
                        required=True,
                        values=ASPECT_STATUS_VALUES,
                    ),
                    FieldDef(name="value", type="string", required=False),
                    FieldDef(name="source", type="string", required=False),
                    FieldDef(name="reason", type="string", required=False),
                ],
            ),
        ),
    ],
)


ALL_SCHEMAS = [
    SYSTEM_PROBE_INPUT_SCHEMA,
    SYSTEM_PROBE_PLAN_SCHEMA,
    SYSTEM_OBSERVATION_SCHEMA,
    SYSTEM_PROFILE_SCHEMA,
]
