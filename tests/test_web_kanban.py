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

        # 无 filter
        data = dashboard_data(store)
        assert data["metrics"]["total"] == 3
        # 过滤 mod
        data_mod = dashboard_data(store, box_filter=mod_def.id)
        assert data_mod["metrics"]["total"] == 2
        assert data_mod["box_filter"] == mod_def.id
        assert data_mod["boxes"] == [mod_def.id]


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
        assert data["metrics"]["total"] == 3
        assert len(data["boxes"]) == 2
        assert data["box_filter"] is None
        conn.close()

    def test_api_data_box_filter(self, server_url: tuple[str, TaskRunStore]):
        url, store = server_url
        inv_box_id = get_inv_def().id
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", f"/api/data?box={inv_box_id}")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["box_filter"] == inv_box_id
        # 2 个 inv task run
        assert data["metrics"]["total"] == 2
        # 所有 recent 的 box_id 必须匹配
        for r in data["recent"]:
            assert r["box_id"] == inv_box_id
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