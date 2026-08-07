# beeOS 术语表（Glossary）

> **版本**：v0.1
> **日期**：2026-08-07
> **状态**：共享术语表 · 3 份架构文档共用
> **关联文档**：[技术架构](tech-architecture.md) ｜ [产品架构](product-architecture.md) ｜ [业务架构](business-architecture.md) ｜ [商业模型 v0.1](../business-model.md)

---

## 1. 命名约定

### 1.1 英文术语规范

- **同一概念只用同一英文术语**。如 Bee = 蜜蜂，**不混用 Agent / Bot / Worker**
- **十巧板命名**全部沿用 DeepSeek 讨论中钦定的英文名
- **产品名 / 商标名**用大写或品牌写法：`beeOS` / `BeBox` / `MonthCloseBox`
- **代码名 / 模块名**用 CamelCase 或 snake_case：`QueenCore` / `month_close_bee`

### 1.2 十巧板命名对照

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| **蜂王** | **Queen** | 调度层大脑。接工单 → 拆任务 → 派 Bee → 收结果 |
| **蜜蜂** | **Bee** | 执行层单元。MVP 阶段只有 MonthCloseBee |
| **盒子 / 工作空间** | **BeeBox** | 环境层。封装某垂直领域的技能、知识、凭证 |
| **蜂巢** | **Hive** | 状态层。任务状态 / 服务注册 / 审计日志 / 花粉篮 |
| **守卫者** | **Guardian** | 安全层。鉴权 / 凭证 / 注入防护 / 审计入口 |
| **粮仓** | **Granary** | 知识层。客户私有文档 / 向量库 / 行业知识 |
| **桥接器** | **Bridge** | 集成层。外部 SaaS / 遗留系统适配 |
| **蜂巢入口** | **Portal** | 交互层。Web 应用，业务专家日常工作 |
| **养蜂人控制台** | **Beekeeper Console** | 治理层。IT 治理员管人、管权限、管模型 |
| **工具工坊** | **Workshop** | 构建层。Box / 模块自定义编辑器（V2 才做） |

### 1.3 其他核心术语

| 术语 | 定义 | 备注 |
|---|---|---|
| **beeOS** | 整个产品名 | "蜂核"是中文品牌 |
| **BeBox** | 早期概念名（= BeeBox），现与 beeOS 同义 | 商业模型已统一为 beeOS |
| **业务 Box** | 行业 Box 模板（如 MonthCloseBox） | MVP 只做 MonthCloseBox |
| **工单** | 用户提交给 Bee 的任务包 | 详见 [产品架构 §6.1](product-architecture.md#61-工单契约用户输入) |
| **花粉篮** | 跨任务 / 跨 Box 的上下文容器 | 也称 Context 或 Pollen |
| **花粉** | 跨 Box 传递的标准化数据 | Markdown / JSON |
| **摇摆舞** | 模块间联动规则 | V1+ 才用 |
| **蜂巢视图** | 监控运维面板 | Beekeeper Console 一部分 |
| **MVP** | Minimum Viable Product，最小可行产品 | 单机 + MonthCloseBox + 1 Bee |
| **V1 / V2** | 第二 / 第三版本 | 详见 [产品架构 §7](product-architecture.md#7-mvp--v1--v2-能力路线图) |
| **ReAct** | Reasoning + Acting，LLM 推理-执行循环 | Bee 的核心模式 |
| **Box Runtime** | BeeBox 的运行时容器 | Docker 容器 |
| **MCP** | Model Context Protocol | Bee 与 Box 间的工具调用协议 |
| **北极星指标** | North Star Metric | 周活付费 Box 数 |
| **WAPB** | Weekly Active Paid Boxes | 北极星指标缩写 |
| **ARR** | Annual Recurring Revenue | 年度经常性收入 |
| **LTV** | Lifetime Value | 客户终身价值 |
| **CAC** | Customer Acquisition Cost | 获客成本 |
| **ICP** | Ideal Customer Profile | 理想客户画像 |
| **PM** | Product Manager / Project Manager | 视上下文而定 |
| **SLA** | Service Level Agreement | 服务等级协议 |
| **RBAC** | Role-Based Access Control | 基于角色的访问控制 |
| **SSO** | Single Sign-On | 单点登录（V1+） |
| **pgvector** | PostgreSQL 向量扩展 | 用于知识库 |
| **litellm** | LLM 多模型统一接口库 | 模型 AB 用 |
| **k3s** | 轻量 K8s 发行版 | V1+ 部署 |
| **AB 切换** | 多模型主备切换 | 单一供应商故障时切走 |

---

## 2. 反向规则（不允许的叫法）

| 不允许 | 应改为 | 原因 |
|---|---|---|
| Agent / Bot / Worker | **Bee** | 统一术语 |
| Workplace（工作场所） | **Workspace**（工作空间） | 架构上有本质区别 |
| 万能 Agent | **专业 Box** | beeOS 拒绝通用 AI 定位 |
| 自定义角色权限 | **4 类固定角色** | MVP 简化 RBAC |
| SaaS 多租户 | **单机单租户** | MVP 私有化部署 |
| LLM 拆解任务 | **写死工作流** | MVP 阶段，V1 才用 LLM Planner |
| 模板市场 / Workshop | **不做** | V2 才有 |
| 移动端 | **不做** | MVP 不支持 |
| SSO / LDAP | **不做** | V1+ |

---

## 3. 缩写速查

| 缩写 | 全称 |
|---|---|
| WAPB | Weekly Active Paid Boxes |
| ARR | Annual Recurring Revenue |
| LTV | Lifetime Value |
| CAC | Customer Acquisition Cost |
| ICP | Ideal Customer Profile |
| SLA | Service Level Agreement |
| RBAC | Role-Based Access Control |
| SSO | Single Sign-On |
| MCP | Model Context Protocol |
| MVP | Minimum Viable Product |
| PMF | Product-Market Fit |
| IPO | Initial Public Offering（暂未涉及） |
| KS | Key Stakeholder |
| KPI | Key Performance Indicator |

---

## 4. 十巧板"看不见"的实现细节

客户视角只看到 3 件东西（Portal / Beekeeper Console / Beekeeper），但下面是 MVP 实际在跑的组件：

| 客户可见 | 客户不可见但已实现 |
|---|---|
| Portal Web | Queen Core / Scheduler / Dispatcher |
| Beekeeper Console | Hive（PostgreSQL + Redis Streams） |
| MonthCloseBox 激活按钮 | MonthCloseBee / Guardian / Granary / Bridge |
| 任务列表 / 详情 | 全部审计日志（治理员可见） |
| 凭证管理 | 凭证加密存储 / 哈希链审计 |

---

## 5. 维护规则

- **新增术语必须先入本表**，再出现在其他文档
- **修改术语必须同步更新所有引用**
- **保留反规则**：错误叫法连同正确叫法一起列，避免后续混淆

---

## 变更日志

| 日期 | 变更 |
|---|---|
| 2026-08-07 | 初版 v0.1，建立十巧板命名对照 |
| 2026-08-07 | 加入 18 个核心术语 + 12 个反规则 |
| 2026-08-07 | 加入 13 个常用缩写 |
