"""bee-kernel CLI 入口。

用法：
  bee-kernel start                    启动 FastAPI server (默认 0.0.0.0:8085)
  bee-kernel submit --workspace WH-001 --objective "会计月结" --period 2026-07
  bee-kernel list-boms
  bee-kernel list-workspaces
  bee-kernel info
"""
from __future__ import annotations

import argparse
import json
import sys


def cmd_start(args) -> int:
    """启动 FastAPI server。"""
    import uvicorn
    from bee_kernel.api import app
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_submit(args) -> int:
    """提交任务。"""
    from bee_kernel.kernel import Kernel
    from bee_kernel.task import Task
    kernel = Kernel()
    task = Task(
        workspace_id=args.workspace,
        objective=args.objective,
        params={"period": args.period, "approver": args.approver} if args.period else {},
        priority=args.priority,
        submitted_by="cli",
    )
    try:
        result = kernel.submit(task)
    except KeyError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    return 0 if result.status == "Done" else 1


def cmd_list_boms(args) -> int:
    """列 BOM 蓝图。"""
    from bee_kernel.kernel import Kernel
    kernel = Kernel()
    boms = kernel.list_boms()
    print(json.dumps(boms, ensure_ascii=False, indent=2))
    return 0


def cmd_list_workspaces(args) -> int:
    """列 workspace。"""
    from bee_kernel.kernel import Kernel
    kernel = Kernel()
    ws = kernel.list_workspaces()
    print(json.dumps(ws, ensure_ascii=False, indent=2))
    return 0


def cmd_info(args) -> int:
    """显示 Kernel 状态。"""
    from bee_kernel.kernel import Kernel
    kernel = Kernel()
    print("beeOS Kernel M0")
    print(f"  loaded BOMs:  {kernel.bom_cache.loaded_count}")
    print(f"  workspaces:   {len(kernel.list_workspaces())}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="beeOS Kernel CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_start = sub.add_parser("start", help="启动 FastAPI server")
    p_start.add_argument("--host", default="0.0.0.0")
    p_start.add_argument("--port", type=int, default=8085)

    p_submit = sub.add_parser("submit", help="提交任务")
    p_submit.add_argument("--workspace", required=True)
    p_submit.add_argument("--objective", required=True)
    p_submit.add_argument("--period", default=None)
    p_submit.add_argument("--approver", default="manager@example.com")
    p_submit.add_argument("--priority", type=int, default=5)

    sub.add_parser("list-boms", help="列出所有 BOM")
    sub.add_parser("list-workspaces", help="列出所有 workspace")
    sub.add_parser("info", help="Kernel 状态")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 1

    return {
        "start": cmd_start,
        "submit": cmd_submit,
        "list-boms": cmd_list_boms,
        "list-workspaces": cmd_list_workspaces,
        "info": cmd_info,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
