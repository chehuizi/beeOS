#!/usr/bin/env python
"""
scripts/run_chain.py —— 跑一条真需求走完 WMS 链

用法：
  scripts/run_chain.py "仓库出库的时候，同一个 SKU 必须先出快过期的批次"
  scripts/run_chain.py --file requirements.txt
  echo "..." | scripts/run_chain.py -

**它跑的是六只盒子的真腿**（不是模拟）：
  需求盒（真 LLM 抽需求）
  → 设计盒（真 LLM 归 WMS 概念，出可判定预期）
  → 开发盒（真起隔离工作区、真按方案改代码、真编译、真跑仓库测试套件）
  → 测试盒（真 WMS 验证实例跑业务场景，采真实测值）
  → 运维盒（真读**生产**实例 + 真核对上游验收/回滚/告警）
  → 分析盒（预期 vs 实测、归因、播种下一轮需求）

## 接力在哪，手工投在哪

| 段 | 通道 | 为什么 |
|---|---|---|
| 需求盒 → 设计盒 | 接力 ✓ | `requirement_set_ref`（建模盒声明的形状，需求集的产出整个接过来） |
| 设计盒 → 开发盒 | 接力 ✓ | `design_package_ref` |
| 开发盒 → 测试盒 | 接力 ✓ | `artifact_ref`（**不再是手工常量**） |
| 需求盒/设计盒 → 测试盒 | 接力 ✓ | `requirement_ref` / `expectation_ref` |
| 测试盒 + 开发盒 → 运维盒 | 接力 ✓ | `verification_ref` / `artifact_ref` |
| 运维盒 → 分析盒 | 接力 ✓ | `observation_ref`（生产上的确定结论优先） |

**运维盒读的是生产**，测试盒跑的是验证实例——两个盒子读的不是同一个地方，
所以观测里必须带着「我测的是哪个实例」（`measured_base_url` / `measured_mode`）。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `BEEOS_WMS_REPO` | WMS 源码仓（开发盒改代码 / 运维盒查回滚目标） | 不配 = 履约中止 |
| `BEEOS_MVN` | maven 可执行文件 | PATH 里的 `mvn` |
| `BEEOS_DEV_WORKSPACE` | 隔离工作区 + 制品库根目录 | `/tmp/beeos-dev` |
| `BEEOS_PROD_WMS_BASE_URL` | **生产** WMS 的 API 根（运维盒读它） | 不配 = 运维盒判「读不到生产」 |
| `BEEOS_VERIFY_WMS_BASE_URL` | 验证实例的 API 根（测试盒在它上面造单据） | `http://127.0.0.1:8081/api/v1` |

## 退出码
  0 = 收敛了（有确定结论的预期全过）
  1 = 有落差 / 没能验出东西（**这是正确信号**，不是脚本失败）
  2 = 链路本身断了（WMS 连不上、投料非法等）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from kanban.trigger import trigger_task  # noqa: E402
from runtime.store import TaskRunStore  # noqa: E402
from boxes.wms_testing.probes import SCENARIOS  # noqa: E402


DEFAULT_VERIFY_BASE_URL = "http://127.0.0.1:8081/api/v1"

# 验证实例的启动参数（**可配**——机器不同，端口/库/用户也不同）
VERIFY_DB_URL_ENV = "BEEOS_VERIFY_DB_URL"
VERIFY_DB_USER_ENV = "BEEOS_VERIFY_DB_USER"
VERIFY_DB_PASSWORD_ENV = "BEEOS_VERIFY_DB_PASSWORD"
VERIFY_JAVA_ENV = "BEEOS_VERIFY_JAVA"
VERIFY_LOG_ENV = "BEEOS_VERIFY_LOG"
VERIFY_DEPLOY_ENV = "BEEOS_VERIFY_DEPLOY"
VERIFY_RESET_DB_ENV = "BEEOS_VERIFY_RESET_DB"


def _read_narrative(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8").strip()
    if args.text == "-":
        return sys.stdin.read().strip()
    return args.text.strip()


def _git_head(repo: str) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
    )
    return proc.stdout.strip()


def _port_of(base_url: str) -> int:
    """从 API 根里取出端口"""
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _port_open(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _stop_instance(port: int) -> list[int]:
    """把占着端口的进程停掉（验证实例要换成新构建）"""
    pids: list[int] = []
    try:
        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        pids = [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:  # noqa: BLE001 - 没有 lsof 就当没找到进程
        pids = []
    for pid in pids:
        subprocess.run(["kill", str(pid)], check=False)
    for _ in range(60):
        if not _port_open(port):
            break
        time.sleep(0.5)
    return pids


def _reset_verify_db(db_url: str, db_user: str) -> str:
    """把验证实例的库重置（Flyway 会重新跑一遍迁移，含种子数据）

    **为什么每次链跑都要重置**：开发盒的迁移版本号是按**仓库基线**算的
    （仓里到 V4 → 新建 V5）。可验证实例的库里可能已经有**上一次实验**
    留下的 V5 —— 内容还不一样（模型每次写出来的 SQL 不一样）→
    Flyway 报 `Migration checksum mismatch` → **服务根本起不来**。

    验证实例本来就是一次性的实验环境，重置它才对。
    生产库不归这条链管（运维盒只读它）。
    """
    if os.environ.get(VERIFY_RESET_DB_ENV, "1") == "0":
        return "（按配置跳过了重置）"
    # jdbc:postgresql://host:port/db → psql 参数
    raw = db_url.replace("jdbc:", "")
    if not raw.startswith("postgresql://"):
        return f"（看不懂的数据库地址 {db_url!r}，没重置）"
    rest = raw[len("postgresql://"):]
    hostport, _, dbname = rest.partition("/")
    host, _, port = hostport.partition(":")
    cmd = [
        "psql", "-h", host or "127.0.0.1", "-p", port or "5432",
        "-U", db_user or os.environ.get("USER", "postgres"),
        "-d", dbname, "-v", "ON_ERROR_STOP=1",
        "-c", "DROP SCHEMA public CASCADE",
        "-c", "CREATE SCHEMA public",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return f"（重置失败：{(proc.stderr or proc.stdout).strip()[:200]}）"
    return f"已重置 {dbname}（Flyway 会重新跑迁移 + 种子数据）"


def _deploy_to_verify_instance(
    dev_result: dict, verify_url: str
) -> str:
    """把**开发盒交付的那份构建**部署到验证实例

    **为什么这一步不能省**：不做它，测试盒对着**跑着的旧实例**跑，
    然后报告说「验过了」——**测的不是被测的那个**。
    开发盒交的是新构建，测试盒量的是旧代码，
    链上却显示成「已验证」。这是这条链上最贵的一种假绿。

    部署不了就**中止**（退出码 2），不降级成「对着现役实例跑」——
    降级之后没人看得出那份报告量的不是新构建。
    """
    if os.environ.get(VERIFY_DEPLOY_ENV, "1") == "0":
        return (
            f"⚠️  跳过了部署（{VERIFY_DEPLOY_ENV}=0）："
            "测试盒量的是**跑着的那个实例**，不保证是开发盒刚交的那份构建"
        )

    # **制品在制品库里**（工作区拆了，jar_path 那条路已经没了）
    jar = str(dev_result.get("artifact_path") or dev_result.get("jar_path") or "")
    if not jar or not Path(jar).exists():
        raise SystemExit(
            "开发盒没交付可部署的构建（artifact_path="
            f"{dev_result.get('artifact_path')!r}）——链不能继续："
            "否则测试盒会对着旧实例跑，报告却说验过了"
        )

    port = _port_of(verify_url)
    stopped = _stop_instance(port)

    # **先把验证库重置**：上一次实验留下的迁移（内容还不一样）
    # 会让 Flyway 报 checksum mismatch，服务起不来
    reset_note = _reset_verify_db(
        os.environ.get(VERIFY_DB_URL_ENV, ""),
        os.environ.get(VERIFY_DB_USER_ENV, ""),
    )

    java = os.environ.get(VERIFY_JAVA_ENV, "java")
    db_url = os.environ.get(VERIFY_DB_URL_ENV, "")
    db_user = os.environ.get(VERIFY_DB_USER_ENV, "")
    db_password = os.environ.get(VERIFY_DB_PASSWORD_ENV, "")
    log_path = os.environ.get(
        VERIFY_LOG_ENV, f"/tmp/beeos-verify-{port}.log"
    )
    cmd = [java, "-jar", jar, f"--server.port={port}"]
    if db_url:
        cmd.append(f"--spring.datasource.url={db_url}")
    if db_user:
        cmd.append(f"--spring.datasource.username={db_user}")
    if db_password:
        cmd.append(f"--spring.datasource.password={db_password}")

    with open(log_path, "ab") as log:
        log.write(
            f"\n===== 部署验证实例 {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
            f"jar: {jar}\ncmd: {' '.join(cmd)}\n".encode()
        )
        log.flush()
        subprocess.Popen(
            cmd, stdout=log, stderr=log, start_new_session=True,
        )

    # 等它真的能应答（Flyway 迁移也在这个过程里跑）
    deadline = time.time() + 120
    while time.time() < deadline:
        if _port_open(port):
            return (
                f"验证实例已换成开发盒交付的构建（jar={Path(jar).name}，"
                f"停掉旧进程 {stopped or '无'}，{reset_note}，日志 {log_path}）"
            )
        time.sleep(1.0)
    raise SystemExit(
        f"新构建起来了但 120 秒内没应答（端口 {port}）——看日志 {log_path}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="跑一条真需求走完 WMS 链")
    parser.add_argument("text", nargs="?", default="-",
                        help="业务方原话（- 表示从标准输入读）")
    parser.add_argument("--file", help="从文件读业务方原话")
    parser.add_argument("--verify-base-url", default=None,
                        help=f"验证实例 API 根（默认 {DEFAULT_VERIFY_BASE_URL}）")
    parser.add_argument("--prod-base-url", default=None,
                        help="生产 WMS API 根（运维盒读它）")
    parser.add_argument("--wms-repo", default=None,
                        help="WMS 源码仓路径（开发盒改代码、运维盒查回滚目标）")
    parser.add_argument("--alerts", default="",
                        help="生产告警（逗号分隔；每条都要有处置记录）")
    parser.add_argument("--store", default="/tmp/beeos_chain_runs.jsonl",
                        help="履约记录落盘位置")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args()

    narrative = _read_narrative(args)
    if not narrative:
        print("没读到业务方原话", file=sys.stderr)
        return 2

    wms_repo = args.wms_repo or os.environ.get("BEEOS_WMS_REPO") or ""
    if wms_repo:
        os.environ["BEEOS_WMS_REPO"] = wms_repo
    verify_url = args.verify_base_url or os.environ.get(
        "BEEOS_VERIFY_WMS_BASE_URL") or DEFAULT_VERIFY_BASE_URL
    prod_url = args.prod_base_url or os.environ.get("BEEOS_PROD_WMS_BASE_URL") or ""

    store = TaskRunStore(args.store)

    # ---- 1) 需求盒 ----
    cap = trigger_task("wms_requirement_box", "capture_business_requirement",
                       {"narrative": narrative}, store)
    pkg = cap["result"]

    # ---- 2) 设计盒（需求集**接力**过来）----
    design = trigger_task("wms_design_box", "handle_modeling_request", {}, store,
                          handoff={"requirement_set_ref": cap["task_run_id"]})
    expectations = design["result"].get("expectations", [])

    # ---- 3) 开发盒（真腿：隔离工作区 → 改代码 → 编译 → 制品）----
    commit_range = ""
    if wms_repo:
        base = _git_head(wms_repo)
        commit_range = f"{base}..HEAD" if base else ""
    dev = trigger_task("wms_development_box", "handle_development_request", {
        "dev_task_id": f"dev_{cap['task_run_id'][:8]}",
        "commit_range": commit_range,
        "target_env": "verification",
    }, store, handoff={"design_package_ref": design["task_run_id"]})
    dev_result = dev.get("result") or {}

    # ---- 3.5) 把开发盒交付的那份构建部署到验证实例 ----
    #
    # 不做这一步，测试盒量的是**旧代码**，报告却说「验过了」。
    deploy_note = _deploy_to_verify_instance(dev_result, verify_url)
    print(f"[deploy] {deploy_note}", file=sys.stderr)

    # ---- 4) 测试盒（制品 + 需求 + 预期 全部接力）----
    os.environ["BEEOS_WMS_BASE_URL"] = verify_url
    os.environ["BEEOS_WMS_MODE"] = "verification"
    test = trigger_task("wms_testing_box", "handle_testing_request", {
        "test_task_id": f"tt_{cap['task_run_id'][:8]}",
        # **场景清单 = 盒子里登记的全部场景**——
        # 写死一个的话，新登记的场景永远不会被跑到，
        # 而「没跑到」和「没通过」在报告上长得一样。
        "scenarios": sorted(SCENARIOS.keys()),
    }, store, handoff={
        "requirement_ref": cap["task_run_id"],
        "expectation_ref": design["task_run_id"],
        "artifact_ref": dev["task_run_id"],
    })

    # ---- 5) 运维盒（真读生产 + 真核对上游）----
    commit_sha = str(dev_result.get("commit_sha") or "")
    change_request = (
        f"放行 {commit_sha[:8]} 这份构建（拣货准确率上库存看板）"
        if commit_sha else ""
    )
    rollback_target = _git_head(wms_repo) if wms_repo else ""
    alerts = [a.strip() for a in (args.alerts or "").split(",") if a.strip()]
    ops = trigger_task("wms_ops_box", "handle_ops_request", {
        "ops_task_id": f"ops_{cap['task_run_id'][:8]}",
        "change_request": change_request,
        "alerts": alerts,
        "rollback_target": rollback_target,
        "target_base_url": prod_url,
        "target_mode": "production",
    }, store, handoff={
        "verification_ref": test["task_run_id"],
        "artifact_ref": dev["task_run_id"],
    })
    ops_result = ops.get("result") or {}

    # ---- 6) 分析盒（观测接力：运维盒合并后的那一份）----
    analysis = trigger_task("wms_analysis_box", "handle_effect_analysis_request", {
        "analysis_id": f"an_{cap['task_run_id'][:8]}",
        "requirement_ref": pkg["set_id"],
        "expected_metrics": expectations,
        "observed_window": "now",
    }, store, handoff={"observation_ref": ops["task_run_id"]})
    ares = analysis.get("result") or {}

    payload = {
        "requirement_set": pkg.get("set_id"),
        "requirement_count": pkg.get("requirement_count"),
        "expectation_count": len(expectations),
        "acceptance": {
            "requirement_box": cap.get("acceptance_status"),
            "design_box": design.get("acceptance_status"),
            "development_box": dev.get("acceptance_status"),
            "testing_box": test.get("acceptance_status"),
            "ops_box": ops.get("acceptance_status"),
            "analysis_box": analysis.get("acceptance_status"),
        },
        "development": {
            "commit_sha": dev_result.get("commit_sha"),
            "branch": dev_result.get("branch"),
            "changed_files": dev_result.get("changed_files"),
            "review_reason": dev_result.get("review_reason"),
            "trace_reason": dev_result.get("trace_reason"),
        },
        "ops": {
            "change_approval_rate": ops_result.get("change_approval_rate"),
            "rollback_readiness": ops_result.get("rollback_readiness"),
            "incident_documented": ops_result.get("incident_documented"),
            "runtime_state_recorded": ops_result.get("runtime_state_recorded"),
            "ops_determined": ops_result.get("ops_determined"),
            "approval_reason": ops_result.get("approval_reason"),
            "rollback_reason": ops_result.get("rollback_reason"),
            "runtime_reason": ops_result.get("runtime_reason"),
            "runtime_state": ops_result.get("runtime_state"),
        },
        "observations": ops_result.get("observations") or [],
        "gaps": ares.get("gaps", []),
        "gap_count": ares.get("gap_count", 0),
        "next_requirement_seed": ares.get("next_requirement_seed", ""),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(payload)

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
        print(f"  {name:18} {status}")

    dev = p.get("development") or {}
    if dev.get("commit_sha"):
        print(f"\n-- 开发盒改动 --\n  提交 {dev['commit_sha'][:8]}（{dev.get('branch')}）")
        for f in dev.get("changed_files") or []:
            print(f"    {f}")
        if dev.get("review_reason"):
            print(f"  评审：{dev['review_reason']}")
        if dev.get("trace_reason"):
            print(f"  追溯：{dev['trace_reason']}")

    ops = p.get("ops") or {}
    print("\n-- 运维盒 --")
    for key in ("change_approval_rate", "rollback_readiness", "incident_documented",
                "runtime_state_recorded", "ops_determined"):
        print(f"  {key:26} {ops.get(key)}")
    for key in ("approval_reason", "rollback_reason", "runtime_reason"):
        if ops.get(key):
            print(f"    {key}: {ops[key]}")
    state = ops.get("runtime_state") or {}
    if state:
        print(f"  生产现场：{state.get('base_url')} mode={state.get('mode')} "
              f"可达={state.get('reachable')}")
        print(f"    看板业务字段：{'、'.join(state.get('business_fields') or []) or '（无）'}")

    print("\n-- 观测（生产优先）--")
    for obs in p.get("observations") or []:
        d = obs.get("details") or {}
        print(f"  [{str(obs.get('outcome')):15}] {str(obs.get('wms_kind')):32} "
              f"{obs.get('summary')}")
        if d.get("measured_base_url"):
            print(f"      ↳ 测的是：{d['measured_base_url']}（mode={d.get('measured_mode')}）")

    print(f"\n-- 落差（{p['gap_count']} 条）--")
    for gap in p.get("gaps") or []:
        print(f"  * {gap.get('requirement_id')} / {gap.get('wms_kind')}")
        print(f"    要满足：{gap.get('statement')}")
        print(f"    实测：{gap.get('summary')}")

    if p.get("next_requirement_seed"):
        print("\n-- 下一轮需求种子 --")
        print(f"  {p['next_requirement_seed']}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"链路断了：{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
