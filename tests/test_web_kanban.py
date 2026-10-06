"""tests.test_web_kanban - Kanban Web HTTP server + JSON API

覆盖范围：
- HTTP / 返回 HTML
- HTTP /api/data 返回完整 JSON（boxes / status / acceptance / metrics / recent / exceptions）
- HTTP /api/data?box=xxx 按 box 过滤
- HTTP /notfound 返回 404
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from kanban.web import KanbanRequestHandler, dashboard_data
from runtime.store import TaskRunStore
from runtime import create_task_run, BeelineExecutor
from boxes.inventory_shortage import get_definition as get_inv_def
from beelines.inventory_shortage import get_beeline as get_inv_beeline
from boxes.modeling import get_definition as get_mod_def
from beelines.modeling import get_beeline as get_mod_beeline


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server_url(tmp_path: Path):
    """启动一个临时 Kanban server，返回 base url"""
    store = TaskRunStore(path=tmp_path / "task_runs.jsonl")

    # 灌入测试数据
    inv_def = get_inv_def()
    inv_beeline = get_inv_beeline()
    mod_def = get_mod_def()
    mod_beeline = get_mod_beeline()

    # 2 个 inv + 1 个 mod
    for _ in range(2):
        tr = create_task_run(
            "o", "i", inv_def.result.result_schema, f"{inv_def.id}@v{inv_def.version}",
            inv_beeline.id, inv_beeline.version, "rt", "in",
        )
        BeelineExecutor().execute(tr, inv_beeline, {"exception_type": "inventory_shortage"})
        store.append(tr, box_id=inv_def.id, acceptance_status="accepted")

    tr = create_task_run(
        "pm", "i", mod_def.result.result_schema, f"{mod_def.id}@v{mod_def.version}",
        mod_beeline.id, mod_beeline.version, "rt", "in",
    )
    BeelineExecutor().execute(tr, mod_beeline, {
        "set_id": "s1", "business_goal": "x",
        "requirements": [{"requirement_id": "r1", "description": "x", "requirement_type": "object", "priority": "must_have"}],
    })
    store.append(tr, box_id=mod_def.id, acceptance_status="rejected")

    # 起 server
    port = _find_free_port()
    KanbanRequestHandler.store = store
    server = ThreadingHTTPServer(("127.0.0.1", port), KanbanRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)

    yield f"http://127.0.0.1:{port}", store

    server.shutdown()
    server.server_close()


# ============================================================
# dashboard_data 函数测试
# ============================================================


class TestDashboardData:
    def test_returns_expected_keys(self, tmp_path: Path):
        store = TaskRunStore(path=tmp_path / "x.jsonl")
        data = dashboard_data(store)
        assert set(data.keys()) == {
            "box_filter",
            "boxes",
            "box_stats",
            "status_counts",
            "acceptance_counts",
            "metrics",
            "recent",
            "exceptions",
        }

    def test_box_filter_applied(self, tmp_path: Path):
        store = TaskRunStore(path=tmp_path / "x.jsonl")
        inv_def = get_inv_def()
        inv_beeline = get_inv_beeline()
        mod_def = get_mod_def()
        mod_beeline = get_mod_beeline()

        # 1 inv
        tr = create_task_run(
            "o", "i", inv_def.result.result_schema, f"{inv_def.id}@v{inv_def.version}",
            inv_beeline.id, inv_beeline.version, "rt", "in",
        )
        BeelineExecutor().execute(tr, inv_beeline, {"exception_type": "inventory_shortage"})
        store.append(tr, box_id=inv_def.id, acceptance_status="accepted")

        # 2 mod
        for _ in range(2):
            tr = create_task_run(
                "pm", "i", mod_def.result.result_schema, f"{mod_def.id}@v{mod_def.version}",
                mod_beeline.id, mod_beeline.version, "rt", "in",
            )
            BeelineExecutor().execute(tr, mod_beeline, {
                "set_id": "s1", "business_goal": "x",
                "requirements": [{"requirement_id": "r1", "description": "x", "requirement_type": "object", "priority": "must_have"}],
            })
            store.append(tr, box_id=mod_def.id, acceptance_status="rejected")

        # 无 filter：未注册盒子的记录被隐藏，只剩 2 条 mod
        data = dashboard_data(store)
        assert data["metrics"]["total"] == 2
        assert data["boxes"] == [mod_def.id]
        # 过滤 mod
        data_mod = dashboard_data(store, box_filter=mod_def.id)
        assert data_mod["metrics"]["total"] == 2
        assert data_mod["box_filter"] == mod_def.id
        assert data_mod["boxes"] == [mod_def.id]
        # 过滤未注册盒子：空
        data_inv = dashboard_data(store, box_filter=inv_def.id)
        assert data_inv["metrics"]["total"] == 0
        assert data_inv["boxes"] == []

    def test_box_stats_per_box(self, tmp_path: Path):
        """box_stats 只含已注册盒子的聚合数据（未注册盒子隐藏）"""
        store = TaskRunStore(path=tmp_path / "x.jsonl")
        inv_def = get_inv_def()
        inv_beeline = get_inv_beeline()
        mod_def = get_mod_def()
        mod_beeline = get_mod_beeline()

        # 1 inv (accepted) —— 未注册，应被隐藏
        tr = create_task_run(
            "o", "i", inv_def.result.result_schema, f"{inv_def.id}@v{inv_def.version}",
            inv_beeline.id, inv_beeline.version, "rt", "in",
        )
        BeelineExecutor().execute(tr, inv_beeline, {"exception_type": "inventory_shortage"})
        store.append(tr, box_id=inv_def.id, acceptance_status="accepted")

        # 1 mod (rejected)
        tr = create_task_run(
            "pm", "i", mod_def.result.result_schema, f"{mod_def.id}@v{mod_def.version}",
            mod_beeline.id, mod_beeline.version, "rt", "in",
        )
        BeelineExecutor().execute(tr, mod_beeline, {
            "set_id": "s1", "business_goal": "x",
            "requirements": [{"requirement_id": "r1", "description": "x", "requirement_type": "object", "priority": "must_have"}],
        })
        store.append(tr, box_id=mod_def.id, acceptance_status="rejected")

        stats = dashboard_data(store)["box_stats"]
        assert len(stats) == 1

        mod_stat = stats[0]
        assert mod_stat["box_id"] == mod_def.id
        assert mod_stat["total"] == 1
        assert mod_stat["accepted"] == 0
        assert mod_stat["rejected"] == 1
        assert mod_stat["acceptance_rate"] == "0.0%"


# ============================================================
# HTTP server 端到端
# ============================================================


class TestKanbanServer:
    def test_html_endpoint(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "beeOS Kanban" in body
        assert "<script>" in body
        assert "/api/data" in body  # JS fetch URL
        conn.close()

    def test_api_data_full(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/api/data")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        # 只展示已注册盒子：2 条 inv 历史记录被隐藏，只剩 1 条 mod
        assert data["metrics"]["total"] == 1
        assert data["boxes"] == ["business_modeling_box"]
        assert data["box_filter"] is None
        conn.close()

    def test_api_data_box_filter(self, server_url: tuple[str, TaskRunStore]):
        url, store = server_url
        mod_box_id = get_mod_def().id
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", f"/api/data?box={mod_box_id}")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["box_filter"] == mod_box_id
        assert data["metrics"]["total"] == 1
        # 所有 recent 的 box_id 必须匹配
        for r in data["recent"]:
            assert r["box_id"] == mod_box_id
        conn.close()

    def test_api_data_unregistered_box_hidden(self, server_url: tuple[str, TaskRunStore]):
        """未注册盒子（order_exception_box）即使有历史记录也不显示"""
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/api/data?box=order_exception_box")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["metrics"]["total"] == 0
        assert data["boxes"] == []
        assert data["recent"] == []
        conn.close()

    def test_api_data_unknown_box(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/api/data?box=nonexistent_box")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        # 空结果
        assert data["metrics"]["total"] == 0
        assert data["recent"] == []
        conn.close()

    def test_vendor_static_serving(self, server_url: tuple[str, TaskRunStore]):
        """本地化前端依赖（three.js）可访问"""
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/vendor/three.module.js")
        resp = conn.getresponse()
        assert resp.status == 200
        assert "javascript" in resp.getheader("Content-Type")
        assert len(resp.read()) > 100_000  # three.module.js ~650KB
        conn.close()

    def test_vendor_path_traversal_blocked(self, server_url: tuple[str, TaskRunStore]):
        """/vendor/../web.py 之类的路径穿越被拒绝"""
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/vendor/../web.py")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 404
        conn.close()

    def test_404_for_unknown_path(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/notfound")
        resp = conn.getresponse()
        assert resp.status == 404
        conn.close()

    def test_html_contains_auto_refresh_js(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        # 自动刷新 setInterval 2s
        assert "setInterval" in body
        assert "2000" in body  # 2 秒
        conn.close()


# ============================================================
# POST /api/trigger（task 投料口）
# ============================================================


class TestTriggerApi:
    def _post(self, url: str, body: dict | None, raw: bytes | None = None):
        conn = HTTPConnection(url.replace("http://", ""))
        payload = raw if raw is not None else json.dumps(body).encode("utf-8")
        conn.request(
            "POST", "/api/trigger", body=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def test_trigger_modeling_box_accepted(self, server_url: tuple[str, TaskRunStore]):
        """有效触发：建模盒走完整履约链 → completed + accepted，并落盘"""
        url, store = server_url
        before = len(store._cache)
        status, data = self._post(url, {
            "box_id": "business_modeling_box",
            "task_type": "handle_modeling_request",
            "payload": {
                "set_id": "req_api_1",
                "business_goal": "建模退款流程",
                "requirements": [
                    {"requirement_id": "r_obj", "description": "x", "requirement_type": "object", "priority": "must_have"},
                    {"requirement_id": "r_met", "description": "x", "requirement_type": "metric", "priority": "must_have"},
                ],
            },
        })
        assert status == 200
        assert data["status"] == "completed"
        assert data["acceptance_status"] == "accepted"
        assert data["result"]["requirement_coverage"] == 100.0
        # 真实执行轨迹：7 个 op 按顺序全部 completed
        assert [s["op_id"] for s in data["op_trace"]] == [
            "parse_requirements",
            "classify_requirements",
            "generate_model_elements",
            "verify_coverage",
            "check_consistency",
            "generate_evidence",
            "package_model",
        ]
        assert all(s["status"] == "completed" for s in data["op_trace"])
        # 已追加到 store
        assert len(store._cache) == before + 1
        assert store._cache[-1]["task_run_id"] == data["task_run_id"]
        assert store._cache[-1]["acceptance_status"] == "accepted"

    def test_trigger_rejected_flow(self, server_url: tuple[str, TaskRunStore]):
        """履约完成但验收失败（无 metric 需求）→ completed + rejected"""
        url, _ = server_url
        status, data = self._post(url, {
            "box_id": "business_modeling_box",
            "task_type": "handle_modeling_request",
            "payload": {
                "set_id": "req_api_2",
                "business_goal": "x",
                "requirements": [
                    {"requirement_id": "r_obj", "description": "x", "requirement_type": "object", "priority": "must_have"},
                ],
            },
        })
        assert status == 200
        assert data["status"] == "completed"
        assert data["acceptance_status"] == "rejected"

    def test_removed_box_not_triggerable(self, server_url: tuple[str, TaskRunStore]):
        """运营线盒子已从看板移除：未注册 = trigger 拒收（代码保留在仓库）"""
        url, _ = server_url
        status, data = self._post(url, {
            "box_id": "order_exception_box",
            "task_type": "handle_order_exception",
            "payload": {"exception_type": "inventory_shortage", "amount": 100},
        })
        assert status == 400
        assert "order_exception_box" in data["error"]

    def test_unknown_box_400(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        status, data = self._post(url, {
            "box_id": "ghost_box", "task_type": "x", "payload": {},
        })
        assert status == 400
        assert "ghost_box" in data["error"]

    def test_unknown_task_type_400(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        status, data = self._post(url, {
            "box_id": "business_modeling_box",
            "task_type": "handle_unknown_task",
            "payload": {},
        })
        assert status == 400
        assert "handle_unknown_task" in data["error"]

    def test_invalid_json_400(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        status, data = self._post(url, None, raw=b"{not json")
        assert status == 400
        assert "error" in data

    def test_missing_fields_400(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        status, data = self._post(url, {"box_id": "business_modeling_box"})
        assert status == 400
        assert "error" in data

    def test_task_entries_in_dashboard_data(self, tmp_path: Path):
        """dashboard_data 的 box_stats 带 task 入口（task_type + 示例 payload）"""
        store = TaskRunStore(path=tmp_path / "x.jsonl")
        mod_def = get_mod_def()
        tr = create_task_run(
            "pm", "i", mod_def.result.result_schema, f"{mod_def.id}@v{mod_def.version}",
            "beeline_business_modeling_v1", 1, "rt", "in",
        )
        store.append(tr, box_id=mod_def.id, acceptance_status="accepted")

        stats = dashboard_data(store, box_filter=mod_def.id)["box_stats"]
        entries = stats[0]["task_entries"]
        assert len(entries) == 1
        assert entries[0]["task_type"] == "handle_modeling_request"
        # 示例 payload 符合 task_schema 顶层字段
        assert set(entries[0]["sample_payload"]) >= {"set_id", "business_goal", "requirements"}
        # 内部工位图：beeline 7 个 op 按声明顺序
        assert entries[0]["beeline_ops"] == [
            "parse_requirements",
            "classify_requirements",
            "generate_model_elements",
            "verify_coverage",
            "check_consistency",
            "generate_evidence",
            "package_model",
        ]

# ============================================================
# POST /api/structure（自然语言 → TASK IN 结构化表达）
# ============================================================


class TestStructureApi:
    def _post(self, url: str, body: dict):
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request(
            "POST", "/api/structure", body=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def test_structure_natural_language(self, server_url: tuple[str, TaskRunStore]):
        """自然语言 → BusinessRequirementSet

        这里验证**规则降级路径**（LLM 不可用时必须还能产出合规投料）。
        LLM 路径的清洗 / 五道检查在 test_llm_structurer 覆盖，不在这里真调模型。
        """
        from runtime import llm
        llm.complete_json = lambda *a, **kw: (_ for _ in ()).throw(
            llm.LLMError("stubbed in web-layer test")
        )
        url, _ = server_url
        status, data = self._post(url, {
            "text": "建模订单退款流程。退款必须在 7 天内完成。退款申请走主管审批流程。"
                    "退款已完成要通知财务。退款处理时长要可度量。订单是核心实体。",
        })
        assert status == 200
        payload = data["payload"]
        assert payload["business_goal"] == "建模订单退款流程"
        assert payload["set_id"].startswith("req_set_nl_")
        assert data["extractor"] == "rule"
        types = [r["requirement_type"] for r in payload["requirements"]]
        # 图 → 平铺列表的投影：规则挂到节点守卫、指标挂到节点 measures，
        # 其余（流程、事件、实体）都是节点动作
        assert types == ["rule", "process", "process", "process", "metric"]
        # requirement_id 唯一且带类型前缀
        ids = [r["requirement_id"] for r in payload["requirements"]]
        assert len(set(ids)) == len(ids)
        assert ids[0].startswith("req_rule_")
        assert data["requirement_count"] == 5
        # 图本身：3 个节点串成链，规则兜底挂在首节点（原文首句就是约束，无前驱边）
        assert [n["action"] for n in payload["nodes"]] == [
            "退款申请走主管审批流程", "退款已完成要通知财务", "订单是核心实体",
        ]
        assert payload["nodes"][0]["guard"] == "退款必须在 7 天内完成"
        assert payload["nodes"][2]["measures"] == ["退款处理时长要可度量"]
        assert [(e["from"], e["to"]) for e in payload["edges"]] == [
            ("node_1", "node_2"), ("node_2", "node_3"),
        ]

    def test_structure_empty_text_400(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        status, data = self._post(url, {"text": "   "})
        assert status == 400
        assert "text" in data["error"]

    def test_structured_payload_fulfills_end_to_end(self, server_url: tuple[str, TaskRunStore]):
        """结构化产物可以直接履约：structure → trigger 全链路

        LLM 路径被 stub 掉——web 层只测编排，LLM 行为在 test_llm_structurer 覆盖。
        """
        from runtime import llm
        llm.complete_json = lambda *a, **kw: (_ for _ in ()).throw(
            llm.LLMError("stubbed in web-layer test")
        )
        url, _ = server_url
        _, structured = self._post(url, {
            "text": "建模订单退款流程。退款必须在 7 天内完成。退款申请走审批流程。退款处理时长要可度量。",
        })
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request(
            "POST", "/api/trigger",
            body=json.dumps({
                "box_id": "business_modeling_box",
                "task_type": "handle_modeling_request",
                "payload": structured["payload"],
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        assert resp.status == 200
        assert data["status"] == "completed"
        assert data["acceptance_status"] == "accepted"
        # 自然语言里的规则 / 流程 / 指标都进了领域模型
        model = data["result"]["model_elements"]
        assert model["specifications"][0]["name"] == "退款必须在 7 天内完成"
        assert model["domain_services"][0]["name"] == "退款申请走审批流程"
        assert model["domain_metrics"][0]["name"] == "退款处理时长要可度量"
