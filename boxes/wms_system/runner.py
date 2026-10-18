"""boxes.wms_system.runner - WMS System Box 的 3 个 operation 实现

WMS 系统档案盒——把「真实在跑的那套 WMS 是什么样」写成一份带出处的档案。

op 链：
  1. `system_intake`            挂基线、解析观测目标  → 探测计划
  2. `system_probe`             真发 HTTP 探活 + 逐条探只读端点 → 观测结果
  3. `system_profile_packager`  装配档案，标注每条维度的来源与状态

## 这三个 op 都是真的

上一版是桩，而且刻意「不产出判据要读的 metric」——让盒子全红、
在看板上显式写「未实现」。那个做法是对的：宁可红，不要假装绿。

现在腿实现了，盒子转 live。**判据没有因此变松**——它们仍然是机械的，
读的是这次真的探到的数。区别只在于：现在探不到了会红，
而不是因为「本来就没实现」而红。

## 失败路径必须显式

探测失败**不抛异常、不静默跳过、不填默认值**。每一次 HTTP 失败都进
`endpoint_results`，带 `reached=False` 和错误原因，然后由判据去判红。
理由跟 nginx 那个坑一样：兜底会把链路 bug 伪装成 UI bug，
静默降级等于假信号。档案盒子的兜底尤其危险——它兜的是「事实」。

只读、不写：探测集里只允许 GET。往生产系统写数据来「验证它活着」
是另一回事，本盒子不做。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.runner import HandlerRunner


BASELINE_PATH = Path(__file__).with_name("baseline") / "system_baseline.json"

# 健康端点的路径**从基线读**，不在这里写死。
# runner 里留一个业务路径，等于盒子换了系统就忘了改这里——而基线是带出处
# 的单一真相源，写死的那份不带出处，也不会随基线更新。
DEFAULT_TIMEOUT_SECONDS = 8


# ============================================================
# 基线
# ============================================================


def load_baseline() -> dict[str, Any]:
    """读基线。读不到就抛——档案盒子的输入读不到时没有「默认值」可言"""
    with BASELINE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


# ============================================================
# op 1：system_intake —— 挂基线、解析观测目标
# ============================================================


def system_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """把投料 + 基线解析成一份探测计划

    地址优先级：投料 > 环境变量 > 基线默认值。
    环境变量排在投料后面是有意的：投料是「这次要观测谁」的业务意图，
    环境变量是部署事实，业务意图不该被部署事实压过。
    """
    baseline = load_baseline()
    target = baseline["target"]

    base_url = (
        input_data.get("target_base_url")
        or os.environ.get(target["base_url_env"])
        or target["default_base_url"]
    ).rstrip("/")

    timeout = input_data.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS

    endpoints = input_data.get("readonly_endpoints") or baseline["readonly_endpoints"]
    # 看板的示例投料对数组字段会填 `[""]`，人要是原样提交就带着一条空串来。
    # 空串不是「一条基线未声明的端点」，它是**没填**——按前者处理会报
    # 「readonly_endpoints 里有基线未声明的路径: ['']」，第一眼完全看不懂。
    endpoints = [p for p in endpoints if str(p).strip()]
    if not endpoints:
        endpoints = list(baseline["readonly_endpoints"])

    # 只放行基线里的端点：投料可以缩小探测范围，不能扩大。
    # 一个能指定「探哪个」的入参，同时也能指定「探不该探的」，
    # 那它就不是观测口径，是一条通往生产的任意读写通道。
    allowed = set(baseline["readonly_endpoints"])
    unknown = [p for p in endpoints if p not in allowed]
    if unknown:
        raise ValueError(
            f"readonly_endpoints 里有基线未声明的路径: {unknown}. "
            "投料只能缩小探测范围，不能扩大——"
            "新增可观测端点要改 baseline 并带出处，不是运行时指定。"
        )

    return {
        "target_base_url": base_url,
        "timeout_seconds": int(timeout),
        "health_path": baseline["health"]["path"],
        "readonly_endpoints": list(endpoints),
        "baseline_id": baseline["baseline_id"],
        "baseline_commit": baseline["provenance"]["commit"],
    }


# ============================================================
# op 2：system_probe —— 真发 HTTP
# ============================================================


def _http_get(url: str, timeout: int) -> tuple[int | None, bytes | None, str | None]:
    """GET 一个地址。返回 (状态码, 响应体, 错误原因)

    错误一律**返回**而不是抛出：调用方要把每一次失败记进档案，
    抛异常会让第一条失败就把整份档案废掉——那等于「一条不通 =
    什么都没观测到」，而真实情况是「19 条里通了 18 条」。
    """
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read(), None
    except urllib.error.HTTPError as exc:
        # 4xx/5xx 也是「服务答了话」，正文可能是合法业务包络，不能当传输失败
        return exc.code, exc.read(), None
    except Exception as exc:  # 超时 / DNS / 连接拒绝 / TLS
        return None, None, f"{type(exc).__name__}: {exc}"


def _parse_json(body: bytes | None) -> dict[str, Any] | None:
    if not body:
        return None
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def system_probe_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """探活 + 逐条探只读端点"""
    baseline = load_baseline()
    base_url = input_data["target_base_url"]
    timeout = int(input_data.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    envelope = baseline["envelope"]
    health_path = input_data.get("health_path") or baseline["health"]["path"]
    # 不走统一包络的端点：按 WMS 契约第 8 节，健康检查返回扁平 JSON。
    # 这份例外表来自基线，不是代码里的 if——漏了它，健康端点会在只读循环里
    # 因为「没有 code 字段」被判 envelope_ok=False，白扣一次应答率。
    exception_paths = set(envelope.get("exception_paths") or [])

    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- 健康端点单独探：它不走统一包络，解析方式跟别的不一样 ---
    status_code, body, error = _http_get(base_url + health_path, timeout)
    health_payload = _parse_json(body)
    health_reachable = status_code == 200 and health_payload is not None
    health_status = str(health_payload.get("status", "")) if health_reachable else ""
    health_database = str(health_payload.get("database", "")) if health_reachable else ""

    # --- 只读端点逐条探 ---
    results: list[dict[str, Any]] = []
    answered = 0
    for path in input_data.get("readonly_endpoints", []):
        code, resp_body, err = _http_get(base_url + path, timeout)
        entry: dict[str, Any] = {"path": path, "reached": code == 200}
        if err:
            entry["error"] = err
        if code is not None:
            entry["status_code"] = code
        payload = _parse_json(resp_body)
        if code == 200 and payload is not None:
            if path in exception_paths:
                # 契约明说它不走包络：有合法 JSON 就算答了话
                entry["envelope_ok"] = True
                answered += 1
            else:
                # 统一包络：code == 0 才算业务上答了话。
                # HTTP 200 但 code != 0 是业务失败，把它算成「通了」是在撒谎。
                ok = (
                    payload.get(envelope["wrapper_key"]) == envelope["ok_value"]
                    and envelope["data_key"] in payload
                )
                entry["envelope_ok"] = bool(ok)
                if ok:
                    answered += 1
        results.append(entry)

    return {
        "observed_at": observed_at,
        # 探的是哪个地址、对着哪份基线探的，随观测一起带下去
        "target_base_url": base_url,
        "baseline_id": input_data["baseline_id"],
        "baseline_commit": input_data["baseline_commit"],
        "health_path": health_path,
        "health_reachable": health_reachable,
        "health_status": health_status,
        "health_database": health_database,
        "endpoint_results": results,
        "endpoint_probed_count": len(results),
        "endpoint_answered_count": answered,
    }


# ============================================================
# op 3：system_profile_packager —— 装配档案
# ============================================================


def _aspect(aspect: str, status: str, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"aspect": aspect, "status": status}
    for key, value in extra.items():
        if value is not None:
            entry[key] = value
    return entry


def system_profile_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """把观测结果 + 基线装配成档案，并算出判据要读的那 5 个数"""
    from boxes.wms_system.schemas import ASPECT_STATUS_VALUES

    baseline = load_baseline()
    provenance = baseline["provenance"]
    totals = baseline["declared_totals"]

    observed_at = input_data["observed_at"]
    health_reachable = bool(input_data["health_reachable"])
    health_status = input_data["health_status"]
    health_database = input_data["health_database"]
    probed = int(input_data["endpoint_probed_count"])
    answered = int(input_data["endpoint_answered_count"])

    service_up = health_reachable and health_status == "UP"
    database_up = health_reachable and health_database == "UP"
    answer_rate = round(answered / probed * 100.0, 1) if probed else 0.0

    aspects: list[dict[str, Any]] = []

    # --- 能探到的 ---
    aspects.append(_aspect(
        "service_liveness",
        "observed" if health_reachable else "unverifiable",
        value=f"{health_status or '未答话'} @ {observed_at}",
        source=f"{input_data['target_base_url']}{input_data.get('health_path', '')}",
        reason=None if health_reachable else "健康端点本次未探通",
    ))
    aspects.append(_aspect(
        "database_reachable",
        "observed" if health_reachable else "unverifiable",
        value=health_database or "未答话",
        source=f"{input_data['target_base_url']}{input_data.get('health_path', '')}",
        reason=None if health_reachable else "健康端点本次未探通",
    ))
    aspects.append(_aspect(
        "readonly_endpoint_inventory",
        "observed",
        value=f"{answered}/{probed} 条只读端点返回合法业务响应",
        source=f"{input_data['target_base_url']}",
    ))
    aspects.append(_aspect(
        "interface_inventory",
        "declared_from_baseline",
        value=f"{totals['http_endpoint_count']['value']} 个 HTTP 端点 / "
              f"{totals['controller_count']['value']} 个控制器",
        source=f"wms@{provenance['branch']}:{provenance['commit']} {provenance['note']}",
    ))
    aspects.append(_aspect(
        "schema_tables",
        "declared_from_baseline",
        value=f"{totals['schema_table_count']['value']} 张表 / "
              f"{totals['flyway_migration_count']['value']} 个迁移",
        source=f"wms@{provenance['branch']}:{provenance['commit']} "
               f"{totals['schema_table_count']['source']}",
    ))

    # --- 拿不到的：从基线读原因，如实标 unverifiable ---
    for item in baseline["unverifiable_aspects"]:
        aspects.append(_aspect(item["aspect"], "unverifiable", reason=item["reason"]))

    # --- 「诚实」判据 ---
    # 不看档案内容对不对，只看：每条维度有没有标状态，
    # declared 有没有出处，unverifiable 有没有原因。
    status_complete = True
    for entry in aspects:
        if entry.get("status") not in ASPECT_STATUS_VALUES:
            status_complete = False
        if entry.get("status") == "declared_from_baseline" and not entry.get("source"):
            status_complete = False
        if entry.get("status") == "unverifiable" and not entry.get("reason"):
            status_complete = False

    return {
        "baseline_id": baseline["baseline_id"],
        "baseline_commit": provenance["commit"],
        "observed_at": observed_at,
        "target_base_url": input_data["target_base_url"],

        # 判据读数
        "health_endpoint_reachable": health_reachable,
        "service_status_up": service_up,
        "database_reachable": database_up,
        "readonly_probe_answer_rate": answer_rate,
        "aspect_status_complete": status_complete,

        # 计数
        "aspect_count": len(aspects),
        "observed_aspect_count": sum(1 for a in aspects if a["status"] == "observed"),
        "declared_only_aspect_count": sum(1 for a in aspects if a["status"] == "declared_from_baseline"),
        "unverifiable_aspect_count": sum(1 for a in aspects if a["status"] == "unverifiable"),
        "readonly_endpoint_count": probed,
        "readonly_endpoint_answered_count": answered,

        # 档案本体
        "aspects": aspects,
    }


WMS_SYSTEM_HANDLERS: dict[str, Any] = {
    "system_intake": system_intake_handler,
    "system_probe": system_probe_handler,
    "system_profile_packager": system_profile_packager_handler,
}

RUNNER = HandlerRunner(WMS_SYSTEM_HANDLERS, owner="wms_system_box")
