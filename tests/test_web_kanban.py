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

from boxes.inventory_shortage.runner import RUNNER as INVENTORY_RUNNER
from boxes.modeling.runner import RUNNER as MODELING_RUNNER
from kanban.web import HTML_PAGE, KanbanRequestHandler, dashboard_data
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
        BeelineExecutor(INVENTORY_RUNNER).execute(tr, inv_beeline, {"exception_type": "inventory_shortage"})
        store.append(tr, box_id=inv_def.id, acceptance_status="accepted")

    tr = create_task_run(
        "pm", "i", mod_def.result.result_schema, f"{mod_def.id}@v{mod_def.version}",
        mod_beeline.id, mod_beeline.version, "rt", "in",
    )
    BeelineExecutor(MODELING_RUNNER).execute(tr, mod_beeline, {
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
        BeelineExecutor(INVENTORY_RUNNER).execute(tr, inv_beeline, {"exception_type": "inventory_shortage"})
        store.append(tr, box_id=inv_def.id, acceptance_status="accepted")

        # 2 mod
        for _ in range(2):
            tr = create_task_run(
                "pm", "i", mod_def.result.result_schema, f"{mod_def.id}@v{mod_def.version}",
                mod_beeline.id, mod_beeline.version, "rt", "in",
            )
            BeelineExecutor(MODELING_RUNNER).execute(tr, mod_beeline, {
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
        BeelineExecutor(INVENTORY_RUNNER).execute(tr, inv_beeline, {"exception_type": "inventory_shortage"})
        store.append(tr, box_id=inv_def.id, acceptance_status="accepted")

        # 1 mod (rejected)
        tr = create_task_run(
            "pm", "i", mod_def.result.result_schema, f"{mod_def.id}@v{mod_def.version}",
            mod_beeline.id, mod_beeline.version, "rt", "in",
        )
        BeelineExecutor(MODELING_RUNNER).execute(tr, mod_beeline, {
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
    def test_beeline_runs_left_to_right_with_ports_at_both_ends(self):
        """BEELINE 栏内：IN 在最左、OUT 在最右，每排都左→右

        以前是蛇形（第二排右→左），于是逻辑上最后一个 op 被甩到最左边，
        OUT 跟着掉到左下角——可读图的人认的尽头在右边，一看就觉得
        「链条怎么往回走」。Z 字折行（每排左→右，折行处走中间道回绕）
        消掉这个逆流，也不用横穿任何工位框。
        """
        html = HTML_PAGE
        assert "Z 字折行" in html
        assert "蛇形两排布局" not in html, "蛇形布局会把末步甩到左下角，OUT 跟着错位"
        # 折行路由：同排一条横线，跨排下到中间道再横移
        assert "a.y === b.y" in html
        assert "[a.x, midY], [b.x, midY]" in html
        # 两端分居：IN 靠左贴投料栏，OUT 靠右贴产出口
        assert "pos._in = { x: 34" in html or "x: 34, y: 96" in html
        assert "x: 526" in html

    def test_intake_names_the_task_type_instead_of_a_bare_dropdown(self):
        """投料口要有「任务类型」label；只有一个 task 时不画下拉

        以前那个 select 没 label，读图的人不知道框里那串英文是什么；
        而且只有一只盒注册了一个 task，下拉根本没有第二个选项——
        一个不能选的下拉是纯装饰，还会让人以为这里能选。
        """
        html = HTML_PAGE
        assert "任务类型" in html
        assert "task-type-label" in html
        assert "entries.length > 1" in html, "多 task 才给下拉"
        # 静态化后仍要保住 #task-type 的取值路径：隐藏 input，
        # 三处调用方都写 getElementById('task-type').value
        assert '<input type="hidden" id="task-type"' in html

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

    def test_html_wires_four_parallel_columns(self, server_url: tuple[str, TaskRunStore]):
        """四栏都在：投料 / 流水线 / 验收闸 / 产出。

        验收闸曾经被画成 BEELINE 栏里的一个菱形工位——跟单个 op 一样大，
        读起来就成了流水线上的第 6 步，但它判的是整条链。这条测试盯的就是
        别再退回去。
        """
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/")
        body = conn.getresponse().read().decode("utf-8")
        conn.close()
        assert 'id="box2d-stage"' in body
        assert 'class="lane-acc"' in body
        assert 'id="acc-stage"' in body
        assert 'id="acc-detail"' in body
        assert 'id="artifact-slot"' in body
        # 闸不再挂在 beeline 那张图的坐标系上
        assert "pos._acc" not in body
        assert "_scene2d.setAcceptance" not in body

    def test_html_shows_artifact_before_it_is_judged(self, server_url: tuple[str, TaskRunStore]):
        """链条是 IN → BEELINE → OUT → ACCEPTANCE：先明确产物，再判它过不过。

        顺序和标签是同一个决定，挪不动。产出口排在验收前面，是因为闸
        并没有拦住产物——REJECTED 的产出照样渲染在产出口里，禁用的只是
        接力口。把菱形画在产出口前面，它就不像在拦任何东西。
        """
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/")
        body = conn.getresponse().read().decode("utf-8")
        conn.close()
        out_at = body.index("ARTIFACTS OUT · 产出口")
        acc_at = body.index("ACCEPTANCE · 验收闸")
        assert out_at < acc_at
        # 接力口是「验收通过则交付」的最后两个字，跟着闸住，不住产物旁边
        assert "renderAcceptance(a) + renderRelayBar()" in body
        assert "renderArtifact() + renderRelayBar()" not in body

    def test_html_distinguishes_abort_from_rejection(self, server_url: tuple[str, TaskRunStore]):
        """履约中止 ≠ 跑完了但没过验收。

        降级删掉之后，LLM 不可用会让这一轮压根跑不完（没有产出）。
        如果照样走「产物 + 验收判据」那套呈现，看板上会显示
        「0/0 条判据」——读起来像"判了但全挂"，实际是"没得判"。
        这两种失败必须长得不一样，否则等于把显性失败又变回假信号。
        """
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/")
        body = conn.getresponse().read().decode("utf-8")
        conn.close()
        # 中止有专属呈现，且不走闸
        assert "履约中止 · 未进入验收" in body
        assert "ABORTED" in body
        assert "data.status === 'failed'" in body
        # 降级这套东西整体没了
        assert "ruleAppliedNotice" in body
        assert "degradeNotice" not in body
        assert "degrade_reason" not in body

    def test_llm_error_keeps_the_actual_cause(self):
        """降级原因要能读：traceback 头部全是路径，答案在最后一两行。

        按字符截前 300 会把真正的原因（httpx.ReadTimeout / 401）切掉，
        看板上就只剩一串文件路径——那不叫原因。
        """
        from runtime.llm import _readable_error

        tb = (
            "Traceback (most recent call last):\n"
            '  File "/venv/lib/httpx/_transports/default.py", line 101, in map_httpcore_exceptions\n'
            "    yield\n"
            "httpx.ReadTimeout: timed out\n"
        )
        out = _readable_error(tb)
        assert "httpx.ReadTimeout: timed out" in out
        assert "default.py" not in out          # 堆栈路径不该出现在原因里
        assert "Traceback (most recent call last)" not in out

        # 已经够短的就原样保留，别加省略号
        assert _readable_error("401 Unauthorized") == "401 Unauthorized"
        # 空输入不炸
        assert _readable_error("   \n  ") == ""

    def test_html_ships_no_scroll_lock(self, server_url: tuple[str, TaskRunStore]):
        """四栏 + 底座要一屏装得下。

        之前 .beebox-body 写死 clamp(380px, 62vh, 560px)，加上 nameplate
        和 domain context 底座之后总和超过一屏，页面开始滚——而看板的用处
        就是一屏看完这盒子的全貌。整页 flex 锁高 + 四栏 min-height:0 是
        这件事的全部机制，少一条矮屏上内容就压到底座上。
        """
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/")
        body = conn.getresponse().read().decode("utf-8")
        conn.close()
        assert "body.single-box" in body
        assert "100vh" in body
        # 四栏能被压扁：grid item 默认不许缩到内容高度以下
        assert ".beebox-body > * { min-height: 0; }" in body
        assert "single-box" in body

    def test_domain_context_exposes_box_level_facts(self, server_url: tuple[str, TaskRunStore]):
        """领域上下文是四栏共同依赖的底座——不是第五个先后步骤。
        上游从别人的 feeds_into 反推，不另写一遍。"""
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/api/data?box=business_modeling_box")
        body = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        ctx = body["box_stats"][0]["domain_context"]
        assert ctx["self_name"] == "Business Modeling Box"
        # 上游 = 声明了 feeds_into 包含本盒的那只盒
        assert [h["id"] for h in ctx["fed_by"]] == ["requirement_capture_box"]
        # 上游名字必须跟着 id 出，不能让前端自己补——单盒过滤时它只有一只盒的名字表
        assert ctx["fed_by"][0]["name"] == "Requirement Capture Box"
        assert ctx["feeds_into"] == []
        assert ctx["consumes"][0]["task_type"] == "handle_modeling_request"
        assert ctx["produces"]["type"] == "business_model_produced"
        assert "model_elements" in ctx["produces"]["fields"]
        # 判据不进上下文：声明值和实测值都归 ACCEPTANCE 栏
        assert "acceptance" not in ctx


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

    def test_structure_llm_failure_is_explicit(self, server_url: tuple[str, TaskRunStore]):
        """LLM 不可用 → 履约中止，调用方能一眼看出"没成"。

        这里原先断言降级路径能产出合规投料（extractor == "rule"）。
        降级删掉之后那条断言是错的：规则版语义最差却能过全部机械判据，
        拿绿灯、被放行下游。

        注意返回码仍是 200：投料被结构契约拒才是 400，
        LLM 失败是履约跑到一半中止——TaskRun 已经产生了（记着失败原因），
        这是执行结果不是请求错误。所以判断依据是 status == "failed"，
        而 error 必须跟着 op_trace 带出来，否则等于什么都没说。
        """
        from runtime import llm
        llm.complete_json = lambda *a, **kw: (_ for _ in ()).throw(
            llm.LLMError("stubbed in web-layer test")
        )
        url, _ = server_url
        status, data = self._post(url, {
            "text": "建模订单退款流程。退款必须在 7 天内完成。退款申请走主管审批流程。",
        })
        assert status == 200
        assert data["status"] == "failed"
        # 没有产出，也没有验收判定
        assert data["acceptance_status"] is None
        assert data["payload"] == {}
        # 失败原因随 op_trace 带出来
        failed = [s for s in data["op_trace"] if s["status"] == "failed"]
        assert len(failed) == 1
        assert "stubbed in web-layer test" in failed[0]["error"]

    def test_structure_empty_text_400(self, server_url: tuple[str, TaskRunStore]):
        url, _ = server_url
        status, data = self._post(url, {"text": "   "})
        assert status == 400
        assert "text" in data["error"]

    def test_structured_payload_fulfills_end_to_end(self, server_url: tuple[str, TaskRunStore]):
        """结构化产物可以直接履约：structure → trigger 全链路

        LLM 路径被 stub 掉——web 层只测编排，LLM 行为在 test_llm_structurer 覆盖。
        这里 stub 的是**一份合规输出**：降级删掉之后捕获盒没有"LLM 挂了
        也能出东西"这条路了，编排要验就得喂一份真能过判据的产出。
        建模盒同样没有降级——它没参与就是履约中止，不会产出粗糙模型。
        """
        from runtime import llm
        llm.complete_json = lambda *a, **kw: {
            "goal": "建模订单退款流程",
            "nodes": [
                {"action": "退款申请走审批流程", "writes": ["退款单"],
                 "reads": [], "measures": [], "trace_to": "sent_3"},
                {"action": "通知财务", "writes": [], "reads": ["退款单"],
                 "measures": ["退款处理时长要可度量"], "trace_to": "sent_4"},
            ],
            "edges": [
                {"from": "node_1", "to": "node_2", "guard": "退款必须在 7 天内完成",
                 "trace_to": "sent_2"},
            ],
        }
        url, _ = server_url
        _, structured = self._post(url, {
            "text": "建模订单退款流程。退款必须在 7 天内完成。退款申请走审批流程。"
                    "通知财务。退款处理时长要可度量。",
        })
        assert structured["status"] == "completed"
        assert structured["acceptance_status"] == "accepted"
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
        # 建模盒的 LLM 也被 stub 了，但没有降级路径可走：
        # 要么按这份产出履约完成，要么直接中止——不能"假装成功"
        if data["status"] == "failed":
            bad = [s for s in data["op_trace"] if s["status"] == "failed"]
            assert bad and bad[0].get("error")
        else:
            assert data["status"] == "completed"
            assert data["acceptance_status"] in ("accepted", "rejected")
