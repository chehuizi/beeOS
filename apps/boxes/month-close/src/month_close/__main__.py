"""MonthCloseBox CLI - 独立运行（不依赖 Bee / Queen）。

用法：
    python -m month_close --period 2026-07
    python -m month_close --period 2026-07 --approver alice@x.com --json
    python -m month_close --manifest    # 打印 manifest 然后退出

调试用：跑 6 步，看输出，不用启 Queen / PG / Redis。
V1+ Bee 接管调度后，Box 仍保留此 CLI 作为单步调试入口。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

from month_close import MANIFEST, WORKFLOW, run_step


def _print_manifest() -> None:
    """打印 manifest。"""
    print(json.dumps(MANIFEST, ensure_ascii=False, indent=2))
    print()
    print(f"WORKFLOW ({len(WORKFLOW)} steps):")
    for i, step in enumerate(WORKFLOW, 1):
        print(f"  {i}. {step['name']:<20} {step['description']}")


async def _run_all(period: str, approver: str, as_json: bool) -> dict:
    """执行全部 6 步。"""
    context = {"period": period, "approver": approver}
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    if not as_json:
        print(f"🐝 MonthCloseBox · period={period} · approver={approver}")
        print(f"⏱  started at {started_at.isoformat()}")
        print()

    steps_trace: list[dict] = []
    prev_outputs: dict = {}
    final_status = "done"

    for i, step in enumerate(WORKFLOW, 1):
        step_t0 = time.perf_counter()
        try:
            output = run_step(step["name"], context, prev_outputs)
            elapsed_ms = round((time.perf_counter() - step_t0) * 1000, 2)
            prev_outputs[step["name"]] = output
            steps_trace.append({
                "step": step["name"],
                "tool": step["tool"],
                "input": {"period": period},
                "output": output if isinstance(output, dict) else {"value": output},
                "elapsed_ms": elapsed_ms,
                "status": "ok",
            })
            if not as_json:
                print(f"  ✓ [{i}/{len(WORKFLOW)}] {step['name']:<20} ({elapsed_ms}ms)")
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - step_t0) * 1000, 2)
            steps_trace.append({
                "step": step["name"],
                "tool": step["tool"],
                "error": f"{type(e).__name__}: {e}",
                "elapsed_ms": elapsed_ms,
                "status": "failed",
            })
            final_status = "failed"
            if not as_json:
                print(f"  ✗ [{i}/{len(WORKFLOW)}] {step['name']:<20} FAILED: {e}", file=sys.stderr)
            break

    total_ms = round((time.perf_counter() - t0) * 1000, 2)
    finished_at = datetime.now(timezone.utc)

    return {
        "box_type": MANIFEST["box_type"],
        "version": MANIFEST["version"],
        "status": final_status,
        "period": period,
        "approver": approver,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_ms": total_ms,
        "steps": steps_trace,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MonthCloseBox CLI - 独立跑月结 6 步（不依赖 Bee / Queen）",
    )
    parser.add_argument("--period", default="2026-07", help="账期 YYYY-MM（默认 2026-07）")
    parser.add_argument("--approver", default="manager@example.com", help="审批人邮箱")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--manifest", action="store_true", help="只打印 manifest 然后退出")
    args = parser.parse_args()

    if args.manifest:
        _print_manifest()
        return 0

    result = asyncio.run(_run_all(args.period, args.approver, args.json))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print()
        print(f"📊 总耗时 {result['elapsed_ms']}ms · 状态 {result['status']}")
        if result["status"] == "done":
            print(f"✅ 全部 {len(result['steps'])} 步完成")
        else:
            print(f"❌ 在第 {len(result['steps'])} 步失败")

    return 0 if result["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
