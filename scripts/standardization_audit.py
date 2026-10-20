"""beeBox 标准化体检：把「每条声明都配一个会红的守卫」逐项量出来。

用法：
    .venv/bin/python scripts/standardization_audit.py

体检项：
  判据可判定  acceptance 的每个 metric 都在 result schema 里有字段
  例外可判定  exception 的 condition 每一段都是 `field op value`，
              且 field 在 result schema 里（**用被验者自己的解析器**）
  指标闭合    声明的指标都有采集算法，有采集算法的都声明了
  引用闭合    task 的 beeline_id 能找到，beeline 的每个 op 都有 handler
  形态诚实    live 的盒子真的实现了腿；skeleton 的 op 是桩（合法状态）

这个脚本是**体检**，不是守卫——它不参与 pytest，答案是「现在长什么样」，
不是「以后不许变」。不许变的那部分在 tests/test_box_standard.py 里。


口径（和 boxes/ 的设计原则一致）：
- 判据可判定：acceptance 的每个 metric 都在 result schema 里有字段
- 例外可判定：exception 的 condition 是 `field op value` 三段式，且 field 在 result schema 里
- 指标闭合：声明的指标都有采集算法，有采集算法的都声明了
- 引用闭合：task 的 beeline_id 能找到，beeline 的每个 op 都有 handler
- 形态诚实：live 的盒子真的实现了腿，skeleton 的盒子真的不产判据 metric
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import metrics as cm  # noqa: E402
from kanban.trigger import (  # noqa: E402
    BOX_REGISTRY,
    METRIC_COMPUTERS,
    RUNNER_REGISTRY,
    _payload_kind,
)

OPS = (">=", "<=", "==", "!=", ">", "<")

rows = []
for bid, (loader, beelines) in sorted(BOX_REGISTRY.items()):
    d = loader()
    res = d.result
    schema = d.get_schema(res.result_schema)
    fields = {f.name for f in schema.fields} if schema else set()

    # 判据可判定
    bad_metrics = [r.metric for r in res.acceptance if r.metric not in fields]

    # 例外可判定——用**被验者自己的解析器**，别自己写一套
    from runtime.acceptance import condition_fields, condition_parts
    bad_exc = []
    for r in res.exceptions:
        cond = r.condition.strip()
        for part in condition_parts(cond):
            if not re.search(r"(>=|<=|==|!=|>|<)", part):
                bad_exc.append((cond, f"段 {part!r} 是裸标识符，永不触发"))
        for f in condition_fields(cond):
            if f not in fields:
                bad_exc.append((cond, f"字段 {f} 不在 result schema"))

    # 指标闭合
    computers = METRIC_COMPUTERS.get(bid, {})
    unwired = cm.unwired(d, computers)
    orphans = cm.orphan_computers(d, computers)

    # 引用闭合
    runner = RUNNER_REGISTRY.get(bid)
    missing_ops = []
    if runner is not None:
        for bl_id, bl_loader in beelines.items():
            bl = bl_loader()
            for op in bl.operations:
                if not runner.supports(op.type):
                    missing_ops.append(f"{bl_id}:{op.type}")

    # 形态诚实：live 盒子的每个 op 都要有真 handler
    stub_ops = []
    if runner is not None:
        for bl_id, bl_loader in beelines.items():
            for op in bl_loader().operations:
                if not runner.supports(op.type):
                    continue
                # 抓桩：handler 源码里出现 skeleton 就说明没实现
                import inspect
                from kanban.trigger import RUNNER_REGISTRY as RR
                try:
                    src = inspect.getsource(RR[bid]._handlers[op.type])
                except Exception:
                    src = ""
                if "skeleton" in src.lower():
                    stub_ops.append(op.type)

    free_text = [t.type for t in d.task if t.accepts_free_text]

    rows.append({
        "box": bid,
        "maturity": res.maturity,
        "version": d.version,
        "criteria": len(res.acceptance),
        "bad_metrics": bad_metrics,
        "bad_exc": bad_exc,
        "unwired": unwired,
        "orphans": orphans,
        "missing_ops": missing_ops,
        "stub_ops": stub_ops,
        "metrics": len(d.metrics.quality) + len(d.metrics.latency),
        "free_text": free_text,
        "feeds": len(d.placement.feeds_into),
    })

W = 26
print("=" * 92)
print(f"{'盒子':<24}{'成熟度':<10}{'判据':<5}{'指标':<5}{'判据可判定':<11}{'例外可判定':<11}{'引用闭合':<9}")
print("=" * 92)
for r in rows:
    print(
        f"{r['box']:<24}{r['maturity']:<10}{r['criteria']:<5}{r['metrics']:<5}"
        f"{'OK' if not r['bad_metrics'] else 'X':<11}"
        f"{'OK' if not r['bad_exc'] else 'X':<11}"
        f"{'OK' if not (r['missing_ops'] or r['stub_ops']) else 'X':<9}"
    )

print("\n" + "=" * 92)
print("问题明细")
print("=" * 92)
any_problem = False
for r in rows:
    probs = []
    if r["bad_metrics"]:
        probs.append(f"判据指向不存在的字段: {r['bad_metrics']}")
    if r["bad_exc"]:
        probs.append(f"例外不可判定: {r['bad_exc']}")
    if r["unwired"]:
        probs.append(f"声明了没采集器: {r['unwired']}")
    if r["orphans"]:
        probs.append(f"有采集器没声明: {r['orphans']}")
    if r["missing_ops"]:
        probs.append(f"op 没有 handler: {r['missing_ops']}")
    if r["stub_ops"]:
        probs.append(f"op 还是桩: {r['stub_ops']}")
    if probs:
        any_problem = True
        print(f"\n{r['box']}  (maturity={r['maturity']}, v{r['version']})")
        for p in probs:
            print(f"   - {p}")
if not any_problem:
    print("\n（无）")

print("\n" + "=" * 92)
live = [r for r in rows if r["maturity"] == "live"]
skel = [r for r in rows if r["maturity"] == "skeleton"]
print(f"live {len(live)} 只 / skeleton {len(skel)} 只   共 {len(rows)} 只")
print(f"判据总数 {sum(r['criteria'] for r in rows)} 条，指标总数 {sum(r['metrics'] for r in rows)} 条")
print("吃自然语言的盒子:", {r['box']: r['free_text'] for r in rows if r['free_text']})
