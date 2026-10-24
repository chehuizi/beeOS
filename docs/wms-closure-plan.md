# 补齐未实现的盒子：让 1 个需求沿链路完成交付并验证业务价值

> 写于 2026-10-22，基线 commit `a61d0fd`。
> 本文里的每条可行性结论都来自本次实测，不是推断。

---

## 0. 结论

**补齐的顺序不能按流水线排，要按「谁产出业务价值证据」排。**

四只骨架盒（development / testing / ops / analysis）的合同形状已经是对的——
4 条判据全部机械可判，缺的只是腿。真正的瓶颈不是「腿难写」：

| 盒 | 对谁负责 | 现在缺什么 |
|---|---|---|
| requirement | 业务方要什么 | **可判定的「预期」没有载体**（现在只有指标名，没有目标值） |
| design | 这条需求在 WMS 领域是什么 | 产出是硬编码套路的模板，condition/action 是占位符 |
| development | 变更能不能构建成能跑的代码 | 腿（可做，但要定构建执行器在哪） |
| **testing** | **仓储现场的规则守没守住** | **腿（唯一能采到业务实测值的地方）** |
| ops | 上线了没、出事了能不能回滚 | 腿（最容易真做，主要是核对与回写） |
| **analysis** | **线上到底满没满足需求** | **腿（唯一对「业务价值」负责的盒）** |

只有 analysis 盒的判据（convergence_rate / requirement_traceability /
gap_attribution / next_requirement_seeded）真的在问「业务价值」。
其余四只验的是**过程质量**——构建过没过、模型自不自洽、变更批没批。

**所以：先把 testing 和 analysis 两条腿做真，链路才有终点；
只补齐前面几只，闭环仍然是假的**（它证明的是「过程合规」，不是「需求被满足」）。

---

## 1. 已验证的可行性（本次实测）

### 1.1 WMS 真能构建、真能跑测试
```
cd /tmp/wms-src2/server && mvn -o test
→ Tests run: 61, Failures: 0, Errors: 0, Skipped: 0
```
（本次复跑确认，2026-10-22。）

### 1.2 隔离验证实例跑得通完整业务单据流
本机 PostgreSQL 16 + JDK21 + Maven 齐备。起独立库 `wms_verify` +
独立端口 8081，实测跑通：

```
建到货单 → 提交 → 收货(带批次/效期) → 生成上架任务 → 执行上架
→ 建出库单 → 提交 → 分配库存 → 生成拣货任务 → 拣货 → 发货
→ 读 dashboard / inventory-summary / inbound-outbound
```
全链路 200，库存/锁定/流水都对得上。

### 1.3 抓到**真实缺陷**：批次效期在上架时丢失 → FIFO 临期先出失效

**现象**（实测）：
```
收货后 RCV-01：batch=B-NEAR  expire=2026-12-01  onHand=200
上架后 A-01-02：batch=B-NEAR  expire=None         onHand=200   ← 效期没了
出库分配挑的是：B-FAR（效期 2027-06-01，远期）    ← 期望是 B-NEAR（临期）
```

**根因**（已定位到表和代码）：
- `wms_inbound_receipt_item` 有 `produced_date` / `expire_date` 列 → 收货时正确写入
- `wms_putaway_task` **没有这两列** → 效期在生成上架任务时就断了
- `PutawayServiceImpl.java:83-86`：
  ```java
  Inventory from = stockService.lockOrCreate(..., task.getBatchNo(), task.getSerialNo(), null, null);
  Inventory to   = stockService.lockOrCreate(..., task.getBatchNo(), task.getSerialNo(), null, null);
  //                                                                          ↑producedDate ↑expireDate
  ```

**污染面**（三处，全是仓储核心不变式）：
1. `InventoryMapper.xml:160` — FIFO 分配 `ORDER BY expire_date ASC NULLS LAST`
   → 所有经上架的库存 expire_date 都是 NULL，**全排最后** → 临期先出规则静默失效
2. `ReportMapper.xml:33` — 临期预警 `AND expire_date IS NOT NULL`
   → **临期 SKU 数恒为 0**（dashboard 实测 `expiringSoonSkuCount: 0`）
3. 合同 `API_CONTRACT_BIZ.md` §15.2 明写「批次和序列号随货移动」→ **实现违反合同**

**为什么 61 个单测没抓到**：
`PutawayServiceImplTest` 只 `setBatchNo("B1")`，**从不断言 expireDate / producedDate**。

> 这是「静默降级 = 假信号」在业务系统里的真实翻版：分配照常成功、不报任何错，
> 只是挑错了批次——**看起来成功，其实守错了规则**。

### 1.4 线上 WMS 目前没有业务流量
线上 `http://101.37.146.194/wms/` 只有主数据（3 仓 / 9 库位 / 6 SKU / 4 客户），
业务单据全为 0（`todayInboundOrders: 0`）。**生产实测暂时采不到业务价值**。

### 1.5 需求侧缺「预期值」载体
- `schema_requirement_set_package.requirements[]` 只有 `measures`（指标**名**），没有目标值
- `schema_analysis_input.expected_metrics` 是 `array of string` —— 只能装名字

→ **照现状，「验证业务价值」会退化成「验一个指标名存不存在」。** 必须先补载体。

---

## 2. 主线需求：选它是因为它三条性质同时成立

业务方原话：
> 仓库出库的时候，同一个 SKU 必须先出快过期的批次，不能先出效期远的；
> 临期 30 天以内的 SKU 要能在库存看板看到。

| 性质 | 为什么必须 |
|---|---|
| **可机械判定** | 「分配批次 == 效期最早批次」是纯布尔对比，不需要人看 |
| **现在真的是坏的** | 补齐的腿第一次跑就有真信号（红），不是走过场 |
| **WMS 域专属** | 批次 / 效期 / FIFO / 临期预警 全是 WMS 概念，`wms_binding` 认得出 |

它同时覆盖 WMS 的**规则类**需求（效期先进先出）和**指标类**需求（临期 SKU 可查），
能把「规则型断言」和「数值型目标」两种验收形状都走一遍。

---

## 3. 六只盒子的腿

### 3.1 预期载体 —— 「可判定的预期」长什么样

**现状**：LLM 抽流程图 → 平铺需求集，4 条判据全绿。**但没有「期望」这个东西。**

没有期望，「验证业务价值」就退化成「验这个名字存不存在」——
需求描述是散文，下游拿着它只能猜「算不算被满足」。

**这一节改造过一次**（原方案：需求盒抽取时多产一组 `expectations[]`，
每条带 `subject` / `by` / `metric` / `comparator` / `target`）。
改的原因是**「可判定」需要一个 WMS 概念**：

| | 原方案（需求盒产出） | 现在的实现（设计盒产出） |
|---|---|---|
| 产出方 | 需求盒（只有业务原话） | 设计盒（知道 WMS 概念绑定） |
| 形状 | kind + subject/by/metric/comparator/target | requirement_id + wms_kind + statement + evidence |
| 口径（怎么测） | 塞进预期里 | **测试盒的探针**（测的人声明口径） |
| 数值目标 | 从原文抠数字 | 概念词表的定义里（例：临期 = 30 天内到期） |

需求盒那边只有业务原话，做不出**可判定**的预期——只能做出一句复述。
而 `comparator` / `target` 这类「怎么测」是**测的人**的知识，
塞进设计盒的产出里等于让设计盒替测试盒决定口径。

现在的形状：

```jsonc
{"expectation_id": "exp_req_proc_1_fefo",
 "requirement_id": "req_proc_1",     // 可追溯到哪条需求
 "wms_kind": "fefo",                 // 落在哪个 WMS 概念上（可判定的根据）
 "statement": "出库按批次失效日期升序取货",   // 概念的正式表述（机械取自词表）
 "evidence": "必须先出快过期的批次"}          // 业务原话的逐字片段
```

**机械派生，不新开一处模型发挥**：`statement` 取自概念词表，
`evidence` 取自绑定时已校验过的原文片段。所以这一步既不引入新的
LLM 调用点，也不引入新的幻觉面。

**判据**：设计盒加第 8 条 `expectation_completeness`
（模型认出来的每个 WMS 概念，都变成了一条预期）。

> **为什么判据放在设计盒，而不是像原方案那样放需求盒**：
> 需求盒那边做不出可判定的预期，放过去只能判「每条需求有没有复述」——
> 那是判一个我们自己造的字段，不是判业务价值。
>
> **为什么判据不在产出预期的那一跳里算**：
> 载体在 `generate_model_elements` 产出，判据在 `check_consistency` 核，
> **隔了两跳**。同一跳里既产出又核 = 自己判自己，永远绿。
> 两边各自算：绑定集合现场重算，预期集合从上一跳传下来——
> 少一条就是中间某一跳把载体吃了（「吃了不传」的第四例）。

**载体**：`schema_business_model_package` 加 `expectations` 字段
（下游两只盒各自声明它要消费的形状，见 3.4 / 3.5）。

### 3.2 design —— 从「套模板」改成「落到 WMS 代码位置」

**现状问题**：`DDD_PATTERNS` 硬编码映射；规则的条件/动作是占位符
（`<condition for req_x>`）；`rule_consistency` 恒 True（占位符无语义可判矛盾）。

**补法**：给 `boxes/modeling/domain.py` 的每个 WMS 概念加一个**代码锚点**
（`code_anchor`，指向 WMS 的模块/服务/方法），让设计包能回答「这条需求要动哪里」：
```python
WMS_ENTITIES = {
    "batch": {"label": "批次", "code_anchor": "inbound/PutawayServiceImpl.execute"},
    ...
}
```
- `design` 产出里每个实体/规则都带 `code_anchor` → development 盒据此确定变更范围
- `rule_consistency` 不再恒 True：把 WMS 约束做成**互斥表**（如
  「库位允许负库存」vs「库位不允许负库存」），两条规则命中互斥项 → 判矛盾
- WMS 概念认不出（`wms_kind is None`）→ 继续判红，**不猜**

### 3.3 development —— 制品准入审计员

**先说清楚分工**：写业务代码是 WMS 团队（人）的事；beeOS 的开发盒做**准入判定**——
「这个制品确实由这批变更构建出来的吗？单测绿吗？可复现吗？」

这样不需要在 1.8 GB 的服务器上跑 Maven（JVM 构建有 OOM 掉 beeOS 的风险）。

**腿**（消费 CI 产出的构建记录，JSON 契约）：
1. `dev_intake` — 校验设计包与目标 commit range 对齐；确定要验的模块集合
2. `dev_build` — 跑构建命令（`dev_build_command` 可配置）+ 跑测试套件
3. `build_packager` — 组装构建记录

**4 条判据怎么变成机械可判**：
| 判据 | 机械口径 |
|---|---|
| `unit_test_green` | 测试套件整体退出码 0 + 失败数为 0（实测 61 个） |
| `artifact_traceable` | 制品 sha256 + git commit + 构建日志路径三者绑定，缺一判红 |
| `build_reproducibility` | 连续两次构建产物内容一致（需固定 `project.build.outputTimestamp`） |
| `review_coverage` | **改成**：本次变更涉及的 WMS 模块都有对应测试类，且该测试类全绿（原口径「评审覆盖」是主观的，必须换掉） |

**构建执行器在哪**：
- 本机：可行（已验证 mvn 离线可用）
- 线上：先指向独立构建机。**不要**塞进 1.8 GB 的 beeOS 服务器

### 3.4 testing —— 仓储现场规则守没守住（业务价值的第一现场）

**现状**：runner 里三个桩，刻意不产出判据 metric。

**腿**（真跑，已验证路径可行）：
1. `test_intake` — 起隔离验证实例（独立库 + 随机端口 + 本次制品）
2. `test_execute` — 按场景跑真实单据流，逐条 expectation 采实测值
3. `test_packager` — 组装测试报告 + **实测业务指标字典**

**能采到的实测业务指标**（全部机械可算）：
```
allocation_fifo_correct        分配批次是否效期最早        实测 False
putaway_date_preserved         上架后效期是否还在            实测 False
expiring_soon_sku_count        临期 SKU 数                  实测 0（期望 ≥1）
negative_stock_violations      负库存违规数                  实测 0（守住了）
pick_accuracy                  拣货数/订单数                实测 1.0
inventory_ledger_conservation  收货 = 上架 + 在库            实测 1.0
```

**4 条判据**：
- `requirement_case_coverage` — 每条 expectation 都有对应用例且跑了
- `scenario_coverage` — 每个场景都跑了
- `no_blocking_defect` — 无红断言
- `case_pass_rate >= 0.99`

**⚠️ 这条链路第一次跑会红**——因为需求是真的坏的（§1.3）。
**这是正确行为**：测试盒发现缺陷 → 不放行 → 开发盒修 → 重测。
「1 个需求完成交付」= 该需求的 testing 判据**全绿的那一次**履约，不是第一次。

### 3.5 ops —— 变更准入 + 状态回写（最容易真做）

**腿**不执行生产发布（重启生产 WMS 是危险动作，必须人工确认），只做核对与回写：
1. `ops_intake` — 变更单 / 告警 / 回滚目标入队
2. `ops_handle` — 变更准入核对 + **把线上真实状态回写进系统档案**（复用 wms_system 盒已有的真腿：`system_intake → system_probe → system_profile_packager`）
3. `ops_packager` — 组装运维记录

**4 条判据**：
| 判据 | 机械口径 |
|---|---|
| `change_approval_rate` | 本次变更有审批记录（审批人 + 审批时间） |
| `rollback_readiness` | 上一版制品仍在 + 回滚路径可达 |
| `incident_documented` | 每条告警都有处置记录（处置人 + 动作 + 时间） |
| `runtime_state_recorded` | 线上真实状态已回写进档案，且带 `source_task_run_id` |

### 3.6 analysis —— 业务价值判定（终点）

**现状**：runner 三个桩。**这是唯一对业务价值负责的盒。**

**腿**：
1. `analysis_intake` — 取需求预期（`expectations`）+ 实测（testing 报告 / ops 回写的线上状态）
2. `analysis_compare` — 逐条 expectation 比对
3. `analysis_packager` — 出分析记录 + 下一轮需求种子

**4 条判据怎么机械可判**：
| 判据 | 机械口径 |
|---|---|
| `convergence_rate` | 达标 expectation 数 / 总数 == 100%（同时产出数值版） |
| `requirement_traceability` | 每条 expectation 都能追到需求 id + 原文句子 + 实测来源 |
| `gap_attribution` | 落差归因三选一，机械判：**有测试报告且红**→实现缺陷；**无测试报告**→未验证；**测试绿但线上不满足**→环境/数据差异 |
| `next_requirement_seeded` | 有落差时必须存在一条 `trace_to` 指向该 expectation 的新需求草案 |

**升级**：`schema_analysis_input.expected_metrics` 从 `array of string`
改成结构化 `{expectation_id, metric, comparator, target, source}`。

---

## 4. 需要的机制补丁

**这四件不先做，上面写的腿写出来也是错的。**

### 4.1 `subject_ref` 支持从对象取子字段
现在 `subject_ref_field="design_package_ref"` 要求「值必须是 id 字符串」，
但 `ref:` 语义是**装整份对象** → 正确投料会被拒。
→ 加 `subject_ref_subfield`：dev 取 `design_package_ref.set_id`，
testing 取 `requirement_ref.set_id`。修完 6 只盒子全部按同一个 `set_id` 分组，
归档里能看到「一条需求走完全链」。

### 4.2 `TaskDef.subject_kind`
ops 的 `subject_ref_field="ops_task_id"` 是运维事件号，不是需求号。
→ 加 `subject_kind`：其余 5 只 = `"requirement"`，ops = `"ops_event"`。
**由盒子声明，不从字符串前缀猜。**

### 4.3 盒间交接通道（当前完全没有）
6 只盒子之间**没有任何数据通道**——`feeds_into` 只是看板上的箭头，
投料要人手工把上一只盒的产出 copy 进下一只盒的 payload。
`runtime/contract.py:60` 对 `ref:` 类型**不做深校验**（注释原话：
「`ref:<schema_id>` 等暂不做深校验，放行」）→ 传什么都过。

**补法**（最小可行 → 再全自动）：
1. `ResultDef` 加 `produces` 声明：产出里哪块对应哪个 schema
   （例：`{"schema_business_model_package": "model_package"}`）
2. 组装函数：目标盒 task_schema 里的 `ref:` 字段，从上游产出的 `produces` 里取
3. 触发端点支持 `from_run_id` → 自动组装投料
4. **同时实现 `ref:` 深校验**：投料里 `ref:` 字段必须真的符合目标 schema 的形状
   —— 这同时修掉已知的 3 处悬空 `ref:`（dev→`schema_business_model_package`、
   testing→`schema_requirement_set_package` / `schema_build_artifact`，
   目标都在**别的盒子**里，违反「ref 只引用本 definition 内」）

### 4.4 修 3 处悬空 `ref:`
dev / testing 各自**声明自己需要的形状**，不引用别的盒子的 schema id
（schema 归消费者所有）。修完配守卫——**文档写了规则但代码没实现**，
这跟之前那 4 条永不触发的 exception 是同一族病。

---

## 5. 阶段划分

### 阶段 0：机制补丁（不产出业务价值，但没有它后面全是错的）✅ 已完成

- 4.1 `subject_ref_subfield` + 4.2 `subject_kind`
- 4.3 盒间交接通道 + `ref:` 深校验
- 4.4 修 3 处悬空 `ref:` + 补守卫
- **验收**：一条需求能被投进 6 只盒子且 `subject_ref` 全部指向同一个 `set_id`；
  `ref:` 传错形状会被拒（**守卫会红**，不是靠人说）

**落地的样子**：

```bash
# 需求 → 设计 → 开发，三只盒子接力
curl -X POST localhost:8765/api/trigger -d '{
  "box_id": "wms_development_box",
  "task_type": "handle_development_request",
  "payload": {"dev_task_id": "d1", "commit_range": "a..b", "target_env": "dev"},
  "handoff": {"design_package_ref": "<设计盒那次履约的 run_id>"}
}'
```
`handoff` 把上游产出接到下游的 `ref:` 字段上，**按下游盒自己声明的形状校验**——
接不过去就当场拒（不是传什么都能过）。

三只盒子的归档记录共享同一个 `subject_ref`（需求集的 `set_id`）和同一个
`subject_kind`（`requirement`）。ops 盒的 `subject_kind` 是 `ops_event`，
跟需求号撞号也分得开。

**17 条守卫，每条都做过回退验证（17/17 因正确理由变红）**。

### 阶段 1：预期载体 + 测试盒 + 分析盒（**业务价值闭环**）
- ✅ 设计盒产出 `expectations`（可判定的预期）+ 第 8 条判据 `expectation_completeness`
- ✅ testing 盒声明它要消费的预期形状（`expectation_ref` 必填）
- ✅ analysis 的 `expected_metrics` 从字符串数组升级成结构化
- ✅ testing 真腿（真 WMS 实例 / 真单据流 / 真实测值；四种结论分开）
- ⬜ analysis 真腿（比对 / 归因 / 播种）
- **验收**：§1.3 那个需求走完 → analysis 判红，证据里能指到
  「分配批次 expire_date 晚于候选批次最小值」——**红灯带定位，不是布尔**

### 阶段 2：开发盒 + 运维盒
- design 加 `code_anchor`
- dev 真腿（制品准入）
- ops 真腿（变更核对 + 回写档案）
- **验收**：WMS 侧修掉效期 bug 后，同一条需求重跑 → testing 绿 → analysis 收敛

### 阶段 3：WMS 侧修复（可选，独立仓）
修 `wms_putaway_task` 缺 `produced_date` / `expire_date` 两列 + 传递链 +
补回归测试（`PutawayServiceImplTest` 现在完全不断言效期）。
**这正好是 dev 盒的第一个真实用例。**

---

## 6. 诚实边界

1. **「验证业务价值」≠「验证商业收益」**
   这条链验的是「需求被满足且可追溯」，不是「赚了钱」。
   「满足」和「有收益」之间还隔着业务侧判断——不把它写成后者。

2. **生产业务价值暂时采不到**
   线上 WMS 业务单据全为 0。阶段 1 的实测值来自**验证实例**，
   不是生产流量。生产实测要等有真实业务流之后。

3. **design 盒的 `rule_consistency` 只能做到有限强度**
   规则矛盾检测靠 WMS 约束的**互斥表**，不是完整的规则推理。
   覆盖不到的矛盾类型 → 判据如实声明覆盖范围，不假装全知。

4. **testing 第一次跑会红**
   需求是真的坏的。红是正确信号，不是失败。
   链路是迭代的：「完成交付」= testing 绿的那一次。

5. **AI 参与面被压到最小**
   只有两处需要语义：需求抽取（抄原文）、模型元素命名（抄需求描述）。
   全部校验机械做。**没有第二个 LLM 调用点。**

6. **一次只推一条需求**
   链路是迭代的，不是直线一次过。多需求并行会让「一次履约的证据」互相污染。
