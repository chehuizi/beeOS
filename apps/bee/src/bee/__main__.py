"""Bee CLI - 独立运行（不依赖 Queen / PG / Redis）。

用法：
    python -m bee --box month_close --period 2026-07
    python -m bee --list                    # 列出所有已注册 Box
    python -m bee --box month_close --audit-path ./logs/audit.jsonl

调试用：Bee 加载 Box，跑完 6 步，写本地审计。
V1+ Queen 接管后，CLI 仍保留作为单 Bee 调试入口。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from bee import Bee, list_supported


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bee CLI - 独立跑一个 Box（不依赖 Queen）",
    )
    parser.add_argument("--box", help="Box 类型（month_close 等）")
    parser.add_argument("--period", default="2026-07", help="账期 YYYY-MM")
    parser.add_argument("--approver", default="manager@example.com", help="审批人邮箱")
    parser.add_argument("--audit-path", default="./logs/audit.jsonl", help="审计日志路径")
    parser.add_argument("--list", action="store_true", help="列出所有已注册 Box")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    if args.list:
        print("已注册的 Box：")
        for bt in list_supported():
            print(f"  · {bt}")
        return 0

    if not args.box:
        parser.error("--box 必填（除非用 --list）")

    bee = Bee()
    context = {"period": args.period, "approver": args.approver}
    result = asyncio.run(bee.run(args.box, context))

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(f"🐝 Bee · box={result.box_type} · period={result.period}")
        print(f"⏱  {result.elapsed_ms}ms · status={result.status.value}")
        print()
        for i, s in enumerate(result.steps, 1):
            print(f"  ✓ [{i}/{len(result.steps)}] {s['step']:<20} ({s['elapsed_ms']}ms)")
        if result.error:
            print(f"\n❌ {result.error}", file=sys.stderr)
            return 1
        print(f"\n✅ 审计已写入 {args.audit_path}")

    return 0 if result.status.value == "Done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
