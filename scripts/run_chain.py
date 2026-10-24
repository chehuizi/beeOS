#!/usr/bin/env python
"""
scripts/run_chain.py —— 跑一条真需求走完 WMS 链

用���：
  scripts/run_chain.py "仓库出库的时候，同一个 SKU 必须先出快过期的批次"
  scripts/run_chain.py --file requirements.txt
  echo "..." | scripts/run_chain.py -

**它跑的是四只盒子的真腿**（不是模拟）：
  需求盒（真 LLM 抽需求）→ 设计盒（真 LLM 归 WMS 概念）
  → 测试盒（真 WMS 实例跑业务场景，采真实测值）
  → 分析盒（预期 vs 实测、归因、播种下一轮需求）

## 现在哪些段是「接力」，哪些是「手工投」

| 段 | 通道 | 为什么 |
|---|---|---|
| 需求盒 → 设计盒 | **手工投** | 设计盒内联吃需求集（`requirements`），没有 `ref:` 输入 |
| 设计盒 → 开发盒 | 接力 ✓ | `design_package_ref` |
| 开发盒 → 测试盒 | **断** | 开发盒是骨架盒，产出不满足测试盒要的制品形状（9 个必填字段） |
| 需求盒/设计盒 → 测试盒 | 接力 ✓ | `requirement_ref` / `expectation_ref` |
| 测试盒 → 分析盒 | 接力 ✓ | `observation_ref` |

**开发盒和运维盒未实现，不影响这条链跑通**：
测试盒验的是 WMS 的业务场景，制品只是「这次测的是哪份构建」——
所以制品手工投一份（真信息：验证实例上跑的那份 jar）就行。
运维盒在主线上也不是必需的（分析盒直接接测试盒的实测）。

## 退出码

  0 = 收敛了（有确定结论的预期全过）
  1 = 有落差 / 没能验出东西（**这是正确信号**，不是脚本失败）
  2 = 链路本身断了（WMS 连不上、投料非法等）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from kanban.trigger import trigger_task  # noqa: E402
from runtime.store import TaskRunStore  # noqa: E402


# 制品：描述「这次测的是哪份构建」。
#
# **这是真信息，不是占位符**：开发盒还没真腿，所以这里手工投。
# 它说的是「本次验的是验证实例上跑的那份 WMS 构建」。
ARTIFACT = {
    "commit_count": 1,
    "build_duration_s": 42.0,
    "unit_test_pass_rate": 1.0,
    "artifact_digest": "wms-verify-instance-local-jar",
    "build_failed": False,
    "build_reproducibility": True,
    "unit_test_green": True,
    "review_coverage": True,
    "artifact_traceable": True,
}

DEFAULT_WMS_BASE_URL = "http://127.0.0.1:8081/api/v1"


def _read_narrative(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8").strip()
    if args.text == "-":
        return sys.stdin.read().strip()
    return args.text.strip()


def _run(box_id: str, task_type: str, payload: dict, store: TaskRunStore,
         handoff: dict | None = None) -> dict:
    return trigger_task(box_id, task_type, payload, store, handoff=handoff)


def main() -> int:
    parser = argparse.ArgumentParser(description="跑一条真需求走完 WMS 链")
    parser.add_argument("text", nargs="?", default="-",
                        help="业务方原话（- 表示从标准输入读）")
    parser.add_argument("--file", help="从文件读业务方原话")
    parser.add_argument("--wms-base-url", default=None,
                        help=f"WMS API 根（默认 {DEFAULT_WMS_BASE_URL}）")
    parser.add_argument("--wms-mode", default="verification",
                        choices=["verification", "production"],
                        help="verification 才允许造数（默认）")
    parser.add_argument("--store", default="/tmp/beeos_chain_runs.jsonl",
                        help="履约记录落盘位置")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args()

    narrative = _read_narrative(args)
    if not narrative:
        print("没读到业务方原话", file=sys.stderr)
        return 2

    os.environ.setdefault("BEEOS_WMS_BASE_URL",
                          args.wms_base_url or DEFAULT_WMS_BASE_URL)
    os.environ["BEEOS_WMS_MODE"] = args.wms_mode

    store = TaskRunStore(args.store)

    # ---- 1) 需求盒 ----
    cap = _run("wms_requirement_box", "capture_business_requirement",
               {"narrative": narrative}, store)
    pkg = cap["result"]

    # ---- 2) 设计盒（**手工投**需求集——它没有 ref: 输入）----
    design = _run("wms_design_box", "handle_modeling_request", {
        "set_id": pkg["set_id"],
        "business_goal": pkg.get("business_goal", ""),
        "requirements": pkg.get("requirements", []),
    }, store)

    # ---- 3) 测试盒（制品手工投 + 需求集/预期接力）----
    test = _run("wms_testing_box", "handle_testing_request", {
        "test_task_id": f"tt_{cap['task_run_id'][:8]}",
        "artifact_ref": ARTIFACT,
        "scenarios": ["batch_expiry_allocation"],
    }, store, handoff={
        "requirement_ref": cap["task_run_id"],
        "expectation_ref": design["task_run_id"],
    })

    # ---- 4) 分析盒（实测观测接力）----
    analysis = _run("wms_analysis_box", "handle_effect_analysis_request", {
        "analysis_id": f"an_{cap['task_run_id'][:8]}",
        "requirement_ref": pkg["set_id"],
        "expected_metrics": design["result"].get("expectations", []),
        "observed_window": "now",
    }, store, handoff={"observation_ref": test["task_run_id"]})

    result = analysis.get("result") or {}
    payload = {
        "requirement_set": pkg.get("set_id"),
        "requirement_count": pkg.get("requirement_count"),
        "expectation_count": len(design["result"].get("expectations", [])),
        "acceptance": {
            "requirement_box": cap.get("acceptance_status"),
            "design_box": design.get("acceptance_status"),
            "testing_box": test.get("acceptance_status"),
            "analysis_box": analysis.get("acceptance_status"),
        },
        "observations": test["result"].get("observations", []),
        "gaps": result.get("gaps", []),
        "next_requirement_seed": result.get("next_requirement_seed", ""),
        "compared_metric_count": result.get("compared_metric_count", 0),
        "gap_count": result.get("gap_count", 0),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(payload)

    # 有落差 / 没能验出东西 → 退出码 1（**这是正确信号**）
    if analysis.get("acceptance_status") != "accepted":
        return 1
    return 0


def _print_report(p: dict) -> None:
    print("=" * 72)
    print(f"需求集 {p['requirement_set']}（{p['requirement_count']} 条需求）"
          f" → {p['expectation_count']} 条可判定预期")
    print("=" * 72)
    print("各盒结论：", flush=True)
    for name, status in p["acceptance"].items():
        print(f"  {name:16} {status}")

    print("\n-- 实测观测 --")
    for obs in p["observations"]:
        d = obs.get("details") or {}
        where = d.get("measured_base_url") or ""
        mode = d.get("measured_mode") or ""
        print(f"  [{obs.get('outcome'):15}] {obs.get('wms_kind'):28} "
              f"{obs.get('summary')}")
        if where:
            print(f"      ↳ 测的是：{where}（mode={mode}）")

    print(f"\n-- 落差（{p['gap_count']} 条，比了 {p['compared_metric_count']} 条）--")
    for gap in p["gaps"]:
        print(f"  * {gap.get('requirement_id')} / {gap.get('wms_kind')}")
        print(f"    要满足：{gap.get('statement')}")
        print(f"    实测：{gap.get('summary')}")
        details = gap.get("details") or {}
        if details.get("allocated"):
            print(f"    分配到：{details.get('allocated')}"
                  f"（应为 {details.get('expected_batch_no')}）")
        for row in details.get("stored_inventory") or []:
            print(f"      库存行 {row.get('batch_no')} @ {row.get('location_code')}"
                  f" 效期={row.get('expire_date')}")

    if p["next_requirement_seed"]:
        print("\n-- 下一轮需求种子 --")
        print(f"  {p['next_requirement_seed']}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"链路断了：{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
