"""tests.test_web_kanban - Kanban Web HTTP server + JSON API

覆盖范围：
- HTTP / 返回 HTML
- HTTP /api/data 返回完整 JSON（boxes / status / acceptance / metrics / recent / exceptions）
- HTTP /api/data?box=xxx 按 box 过滤
- HTTP /notfound 返回 404
"""

from __future__ import annotations

import html as html_mod
import json
import re
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

# 看板盒子名单 = 注册表按业务流向排出来的那一串。
# 手抄进各个测试会漂：加一只盒子就得改 N 处，漏一处就是一条假绿。
# 这里钉一次，各处共用；真正要盯「顺序对不对」的地方仍单独断言。
FLOW_ORDER = [
    "receipt_capture_box",    # 费用报销线，独立于 WMS 域
    "wms_requirement_box",
    "wms_design_box",
    "wms_development_box",
    "wms_testing_box",
    "wms_ops_box",
    "wms_analysis_box",       # 线上效果分析：接运维的实测，回头验需求
    "wms_system_box",        # WMS 生命周期链的终点
]


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
            "domains",
            # scope_layouts 不是 domain_layouts：键是**范围**，除了每个真域
            # 还带一条「全部域」。以前只有真域，「全部域」查不到布局，
            # 视图于是被域隐式决定了。
            "scope_layouts",
            "status_counts",
            "acceptance_counts",
            "metrics",
            "excluded_skeleton_boxes",
            "recent",
            "exceptions",
        }

    def test_the_all_domains_scope_is_in_the_layout_table(self, tmp_path: Path):
        """「全部域」必须有一份诚实的布局，而不是让前端自行猜测

        缺了它，前端拿不到 `scope_layouts['__all__']`，就退回到
        「有没有 radial 记录」来判断视图——域于是又偷偷决定了视图。
        """
        from kanban.trigger import ALL_DOMAINS_SCOPE

        store = TaskRunStore(path=tmp_path / "x.jsonl")
        lays = dashboard_data(store)["scope_layouts"]
        assert ALL_DOMAINS_SCOPE in lays, (
            "scope_layouts 里没有「全部域」这一条——"
            "前端会退回按域隐式决定视图，两个轴又焊回去了"
        )
        assert set(lays) == {ALL_DOMAINS_SCOPE, *lays.keys()} | set(
            dashboard_data(store)["domains"]
        ), "scope_layouts 的键应恰好是「全部域」+ 每个域"

    def test_a_cross_domain_scope_never_becomes_a_ring(self, tmp_path: Path):
        """跨域范围一律不出环——而且不能只靠「碰巧推不出来」

        「全部域」确实算得出唯一汇点（wms_system_box），因为域内汇点
        那条规则跨了几条互不相干的流水线之后**照样成立**：它只看图结构。
        于是费用报销线的 receipt_capture_box 会被画成 WMS 流水线的一环，
        还画了条边投给它——**假的**，而图上看不出任何异常。

        看板一旦这么画，就是在替业务声明一条没人声明过的拓扑。
        """
        from kanban.trigger import ALL_DOMAINS_SCOPE

        store = TaskRunStore(path=tmp_path / "x.jsonl")
        lay = dashboard_data(store)["scope_layouts"][ALL_DOMAINS_SCOPE]
        assert lay["layout"] == "flat", (
            "跨域范围被画成了环——那等于替业务声明了一个不存在的拓扑"
        )
        assert lay["center"] is None, "退回平铺就不要再报中心"
        assert lay["reason"], "退回平铺必须说清为什么"
        assert lay["handoffs"] == [] and lay["feeds_center"] == [] and lay["feedback"] == [], (
            "跨域范围不该画出任何边——边是接线的事实，跨域的边没人声明过"
        )

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

        # 无 filter：未注册盒子的记录被隐藏，只剩 2 条 mod。
        # 名单是注册表（7 只都在），运行记录只影响计数。
        data = dashboard_data(store)
        assert data["metrics"]["total"] == 2
        assert data["boxes"] == FLOW_ORDER
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
        # 所有注册盒子都在，哪怕只有 1 只跑过——「没跑过」读成 0 次运行，
        # 不是读成不存在。未注册的 order_exception_box 不在名单里。
        assert [s["box_id"] for s in stats] == FLOW_ORDER
        assert all(s["total"] == 0 for s in stats if s["box_id"] != mod_def.id), (
            "没跑过的盒子也该有统计条目，只是计数为 0"
        )

        mod_stat = next(s for s in stats if s["box_id"] == mod_def.id)
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
        # **名单是注册表，不是运行记录**：三只注册盒子全在，哪怕只有 1 只跑过。
        # 以前这里断言 ["wms_design_box"]——它把「只显示跑过的盒子」
        # 也写死了，于是清一次 RUNS 整个看板就空成「没有盒子」。
        # 「没跑过」该读成 0 次运行，不是读成不存在。
        # 顺序 = WMS 生命周期链 + 费用报销线。
        # 传 set 会让同层顺序随进程哈希随机化变，所以这里必须是稳定顺序。
        assert data["boxes"] == FLOW_ORDER
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
        和 box context 底座之后总和超过一屏，页面开始滚——而看板的用处
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

    def test_box_context_exposes_box_level_facts(self, server_url: tuple[str, TaskRunStore]):
        """盒子上下文是四栏共同依赖的底座——不是第五个先后步骤。
        上游从别人的 feeds_into 反推，不另写一遍。"""
        url, _ = server_url
        conn = HTTPConnection(url.replace("http://", ""))
        conn.request("GET", "/api/data?box=wms_design_box")
        body = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        ctx = body["box_stats"][0]["box_context"]
        assert ctx["self_name"] == "WMS Design Box"
        # 上游 = 声明了 feeds_into 包含本盒的那只盒
        assert [h["id"] for h in ctx["fed_by"]] == ["wms_requirement_box"]
        # 上游名字必须跟着 id 出，不能让前端自己补——单盒过滤时它只有一只盒的名字表
        assert ctx["fed_by"][0]["name"] == "WMS Requirement Box"
        assert [h["id"] for h in ctx["feeds_into"]] == [
            "wms_development_box",
            "wms_system_box",
        ]
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
            "box_id": "wms_design_box",
            "task_type": "handle_modeling_request",
            "payload": {
                "set_id": "req_api_1",
                "business_goal": "把入库上架和拣货出库管清楚",
                # description 是真 WMS 需求。以前这里是 "x" 和「建模退款流程」——
                # 现在盒子只服务 WMS 域，喂非 WMS 需求被 wms_binding 打回是对的，
                # 而这条测试要验的是「有效触发 → accepted」，所以得给真需求。
                "requirements": [
                    {"requirement_id": "r_obj", "description": "库存按 SKU 和库位记账",
                     "requirement_type": "object", "priority": "must_have"},
                    {"requirement_id": "r_met", "description": "拣货准确率要达到 99.5%",
                     "requirement_type": "metric", "priority": "must_have"},
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
            "box_id": "wms_design_box",
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
            "box_id": "wms_design_box",
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
        status, data = self._post(url, {"box_id": "wms_design_box"})
        assert status == 400
        assert "error" in data

    def test_task_entries_in_dashboard_data(self, tmp_path: Path):
        """dashboard_data 的 box_stats 带 task 入口（task_type + 示例 payload）"""
        store = TaskRunStore(path=tmp_path / "x.jsonl")
        mod_def = get_mod_def()
        tr = create_task_run(
            "pm", "i", mod_def.result.result_schema, f"{mod_def.id}@v{mod_def.version}",
            "beeline_wms_design_v1", 1, "rt", "in",
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
                "box_id": "wms_design_box",
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


class TestTheRosterComesFromTheRegistry:
    """看板列哪些盒子，由注册表说了算，不由运行记录说了算

    以前 `all_boxes = order_boxes_by_flow(store.list_boxes())`，
    而 `list_boxes()` 返回「跑过过的盒子」。后果是：一只还没跑过的盒子
    在看板上根本不存在——清一次 RUNS 就能让整个看板空成「没有盒子」。

    「没跑过」该读成 **0 次运行**，不是读成 **不存在**。前者是诚实的读数，
    后者是谎报，而且没人会注意到。
    """

    def _fresh_store(self, tmp_path: Path) -> TaskRunStore:
        return TaskRunStore(path=tmp_path / "empty.jsonl")

    def test_an_empty_store_still_lists_every_registered_box(self, tmp_path: Path):
        """刚开箱、一条都没跑过的看板，该是「3 只盒子 · 0 次运行」"""
        from kanban.trigger import BOX_REGISTRY, registered_box_ids

        data = dashboard_data(self._fresh_store(tmp_path))
        assert set(data["boxes"]) == set(BOX_REGISTRY), (
            "空 store 下看板列不出全部注册盒子——"
            "读者会以为这些盒子不存在，而不是没跑过"
        )
        assert len(data["box_stats"]) == len(BOX_REGISTRY)
        assert all(s["total"] == 0 for s in data["box_stats"]), (
            "没跑过的盒子计数应为 0，不是缺条目"
        )
        # 未注册盒子（运营线那只）不进名单，但它的 runner 还在仓库里
        assert "order_exception_box" not in data["boxes"]
        assert "order_exception_box" not in registered_box_ids()

    def test_ordering_is_the_same_for_a_set_as_for_a_list(self):
        """本函数的结果不许依赖调用方传进来的迭代顺序

        以前 `order_boxes_by_flow` 直接用传入顺序分层，而
        `registered_box_ids()` 返回 set——Python 的字符串哈希按进程
        随机化，于是同层里哪只盒子排前面会随进程重启而变。看板照常渲染，
        只是顺序悄悄换了，而且没有任何东西会报错。

        排序收进函数之后，「传 set 还是传 list」不再是调用方的隐含约定。
        这条在**单进程内**就能确定性地验：同一个 set 和同样内容的 list
        必须给出同一个答案。以前则会分道扬镳（看 PYTHONHASHSEED）。
        """
        from kanban.trigger import order_boxes_by_flow, registered_box_ids

        ids = registered_box_ids()
        assert not isinstance(ids, (list, tuple)), "前提变了：它现在不是 set 了"

        # **倒序 list 而不是 set**：set 的迭代顺序随进程哈希变，
        # 用它当对照，测试在某些 PYTHONHASHSEED 下会「碰巧」抓不到回退
        # （实测 8 个 seed 里只有 4 个红）。倒序 list 一定跟正序不同，
        # 不管哈希怎么随机化，回退都 100% 被逮住。
        ascending = sorted(ids)
        descending = sorted(ids, reverse=True)
        assert order_boxes_by_flow(descending) == order_boxes_by_flow(ascending), (
            "结果依赖入参顺序——同层顺序会随调用方怎么传而变"
        )
        assert order_boxes_by_flow(ids) == order_boxes_by_flow(ascending), (
            "传 set 和传同样内容的 list 结果不同"
        )

    def test_the_names_land_in_flow_order(self):
        """钉住具体顺序：同层按 box_id 字母序，有上游的垫后

        只测「set == list」不够——那两条完全乱序也会一起变。
        业务流向（WMS 生命周期：需求 → 设计 → 开发 → 测试 → 运维 → 分析
        → 系统档案）必须真的体现在顺序里。

        分析盒在这条链上，但它**反馈**给需求盒那条边不进排序：
        闭环塞进线性列表就成环，而成环时 `order_boxes_by_flow` 会
        静默按原顺序收尾。反馈由 domain_layout 单独处理。
        """
        from kanban.trigger import order_boxes_by_flow, registered_box_ids

        assert order_boxes_by_flow(registered_box_ids()) == [
            # 无上游 → 同层最前，按 box_id 字母序
            "receipt_capture_box",
            "wms_requirement_box",
            # 每一环都被上一环挡住，必须严格垫后
            "wms_design_box",
            "wms_development_box",
            "wms_testing_box",
            "wms_ops_box",
            "wms_analysis_box",
            "wms_system_box",          # 全链的终点
        ]


class TestTheFlatGridStaysScannable:
    """平铺视图：8 张卡片的规格区必须能按列扫

    这条守卫守的是一次真实的排版修复。原来 `.cube-caption .spec` 是
    `display: inline-block`：宽度由**该卡片自己最长的一行**决定，8 张卡片
    就有 8 个不同宽度，再被 `text-align: center` 各自居中——
    DOMAIN / VALUE STREAM / ROLE 三行的左边缘全部错开，整片网格读起来像散沙。

    危险的是它不报错、不空白、不缺内容，只是「不好读」。所以必须钉住。

    同理 `box_id` 早先是行内跟着名字走的，名字一长就折行、被挤到第二行
    还贴着名字，卡片之间规格区的起始高度也跟着参差。
    """

    @staticmethod
    def _rule(selector: str) -> str:
        """取一条 CSS 规则的完整声明体（支持 {} 嵌套，够用了）。"""
        start = HTML_PAGE.find(selector)
        assert start != -1, f"HTML_PAGE 里找不到 {selector}"
        brace = HTML_PAGE.find("{", start)
        depth = 0
        for i in range(brace, len(HTML_PAGE)):
            if HTML_PAGE[i] == "{":
                depth += 1
            elif HTML_PAGE[i] == "}":
                depth -= 1
                if depth == 0:
                    return HTML_PAGE[brace : i + 1]
        raise AssertionError(f"{selector} 的规则体没闭合")

    def test_the_spec_block_spans_the_whole_card(self):
        """规格区必须占满整卡宽——inline-block 会让每张卡各自居中、左边缘全错开"""
        body = self._rule(".cube-caption .spec")
        assert "display: block" in body, (
            "`.cube-caption .spec` 必须 display: block。"
            "改回 inline-block 的话，每张卡的宽度由它自己最长的一行决定，"
            "8 张卡得到 8 个不同宽度，各自居中后规格区左边缘全部错开"
        )
        assert "inline-block" not in body, (
            "`.cube-caption .spec` 里又出现 inline-block——"
            "inline-block 宽度随内容变，正是左边缘错开的根源"
        )
        assert "width: 100%" in body, (
            "规格区没占满整卡宽，.k 的定宽右对齐就失去意义，"
            "分界线不会落在同一个 x 上"
        )

    def test_the_box_id_gets_a_line_of_its_own(self):
        """box_id 独占一行，否则名字一长就折行把规格区整体顶下去"""
        body = self._rule(".cube-caption .name .bid")
        assert "display: block" in body, (
            "box_id 必须是块级独占一行。行内的话，名字一长它就被挤到第二行"
            "还贴着名字，卡片之间规格区的起始高度跟着参差"
        )

    def test_a_ragged_last_row_starts_from_the_same_line(self):
        """除不尽列数时，最后一排要左对齐——居中会像漏排了几只"""
        body = self._rule(".boxes-scene")
        assert "justify-content: flex-start" in body, (
            "`.boxes-scene` 要 justify-content: flex-start。"
            "居中时 8 只盒子排 6 列，最后一排两只孤零零浮在正中间，"
            "看着像漏排——而且每排起点都不同，没法按列扫"
        )


class TestViewAndScopeAreTwoSeparateAxes:
    """视图（怎么摆）和范围（摆哪些）必须是两个独立的选择

    以前代码里根本没有「视图」：渲染分支写的是
    `lay && lay.layout === 'radial' ? 环形 : 平铺`——也就是**点域 chip
    顺手把整页布局换掉**。读者以为自己在筛选，实际在换页面，而 URL 里
    只有 `domain` 没有 `view`，换视图这件事根本看不出来。

    更贵的代价是两个轴被焊死：全部域下永远看不到环形，WMS 下永远看不了网格。

    这条守卫守的就是「视图由 _view 决定，域只决定能不能画」。
    """

    def test_both_axes_are_read_from_the_url(self):
        """两个轴都进 URL——否则换视图既看不出来也没法分享"""
        assert "_params.get('view')" in HTML_PAGE, (
            "视图没从 URL 读——换个视图地址栏纹丝不动，刷新就丢"
        )
        assert "_params.get('domain')" in HTML_PAGE, "范围没从 URL 读"

    def test_the_render_branch_follows_the_view_not_the_scope(self):
        """渲染分支必须先问 _view，不能拿 lay.layout 当条件

        只查 `render()` 函数体：`currentScopeLayout` 里有同一段
        `lay && lay.layout === 'radial'`，那是**能力判断**（这个范围
        能不能画成环），是合法且必要的。一把梭子搜全文会把它一起打掉，
        那种守卫逼着人把能力判断也改成假的——守住的东西自己先错了。
        """
        start = HTML_PAGE.index("\nfunction render(data) {")
        end = HTML_PAGE.index("\nasync function tick(", start)
        # 先剥掉行注释再查：render() 里那条注释**正是在解释这个反模式**
        # （`lay.layout === 'radial' ? 环形 : 平铺`），查全文会命中注释本身。
        # 守的是代码，不是散文——不然哪天有人把注释改个说法，守卫就废了。
        body = "\n".join(
            line.split("//", 1)[0] for line in HTML_PAGE[start:end].splitlines()
        )

        assert "_view === VIEW_TOPOLOGY && canRadial" in body, (
            "渲染分支必须先判断读者选了哪个视图。"
            "写成只看 lay.layout 就是域在替读者决定视图"
        )
        assert "lay.layout" not in body, (
            "render() 里不该再出现 lay.layout —— "
            "「点域 chip 顺手换整页布局」的老写法，两个轴又焊回去了。"
            "能不能画环由 currentScopeLayout 算好传进来，render 只看 canRadial"
        )

    def test_a_scope_that_cannot_ring_disables_topology_instead_of_falling_back(self):
        """推不出环时必须禁用 + 写明原因，不许静默退回网格

        行为那条由 TestHomepageIsQuieterAndTheViewIsATabBar 里真跑 renderFilters 的守卫
        守；这里只钉住「它必须是个真 disabled button，而不是没有 href 的
        <a>」这个形状——它在无障碍树里会退化成 role=generic，
        还会把 title 当成可访问名，读屏念出来的是那句原因而不是「拓扑」。
        """
        assert (
            "'<button type=\"button\" class=\"seg-item disabled\" disabled'"
            in HTML_PAGE
        ), (
            "拓扑不可用时要渲染成真的 disabled button——"
            "没有 href 的 <a> 在无障碍树里退化成 role=generic"
        )
        assert "这个范围画不出拓扑" in HTML_PAGE, (
            "选拓扑但推不出环时，得在正文里说清原因，"
            "不能悄悄画成网格——看着像「这个范围没有结构」"
        )

    def test_the_scope_table_covers_the_all_domains_sentinel(self):
        """scope_layouts 必须含「全部域」——少了它前端就退回按域猜视图"""
        assert "ALL_DOMAINS_SCOPE" in HTML_PAGE or "__all__" in HTML_PAGE, (
            "scope_layouts 没覆盖「全部域」这个范围"
        )


def _extract_js_function(name: str) -> str:
    """从 HTML_PAGE 里抠出某个 JS 函数的完整源码（按花括号配平）。

    用抠出来的**真源码**去 node 里跑，不是抄一份逻辑到 Python 里再测——
    抄的那份和页面上的那份是两份代码，改了页面上的那份，测试照样绿。
    """
    start = HTML_PAGE.index(f"\nfunction {name}(") + 1
    i = HTML_PAGE.index("{", start)
    depth = 0
    for j in range(i, len(HTML_PAGE)):
        if HTML_PAGE[j] == "{":
            depth += 1
        elif HTML_PAGE[j] == "}":
            depth -= 1
            if depth == 0:
                return HTML_PAGE[start : j + 1]
    raise AssertionError(f"{name} 的花括号没配平")


def _strip_js_comments(text: str) -> str:
    """剥掉 // 整行注释和 /* */ 块注释。

    存在性守卫必须查这个结果，不查原文：页面里解释「这次删掉了什么」的
    注释里，往往就写着被删掉的那句话原文。查全文等于守注释。
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("//")
    )


def _js_const(name: str) -> str:
    for line in HTML_PAGE.splitlines():
        s = line.strip()
        if s.startswith(f"const {name} ="):
            return s
    raise AssertionError(f"HTML_PAGE 里找不到常量 {name}")


def _run_scope_links(cases: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """在 node 里真跑 scopeQuery/overviewHref/boxHref，返回每个组合的 href。"""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("没有 node，跳过链接形状守卫")

    src = "\n".join(
        [
            _js_const("ALL_DOMAINS"),
            _js_const("VIEW_GRID"),
            _extract_js_function("scopeQuery"),
            _extract_js_function("overviewHref"),
            _extract_js_function("boxHref"),
            "const CASES = " + json.dumps(cases),
            "const out = {};",
            "for (const [v, d] of CASES) {",
            "  out[JSON.stringify([v, d])] = "
            "[overviewHref(v, d), boxHref('wms_ops_box', d, v)];",
            "}",
            "console.log(JSON.stringify(out));",
        ]
    )
    proc = subprocess.run(
        [node, "-e", src], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"node 跑链接拼装失败：\n{proc.stderr}"
    raw = json.loads(proc.stdout.strip().splitlines()[-1])
    return {tuple(json.loads(k)): tuple(v) for k, v in raw.items()}


class TestScopeAndViewLinksAreRealQueryUrls:
    """侧边栏每一条 href 都必须是一条**真的**查询 URL

    这里守的是一个已经咬过人的坑：`overviewHref` 曾写成 `'/' + v + d`，
    两段各自以 `&` 开头、谁也不写 `?`，拼出来是 `/&domain=WMS`——不是
    查询串，是一条路径。浏览器跳过去，服务端返 404，nginx 兜底再把首页
    原样吐回来。于是点「WMS」看着毫无反应：页面纹丝不动、高亮还留在
    「全部域」、盒子一只没少，**而且一条错都不报**。

    之前 7 条轴相关的守卫全绿也照样没抓到：它们查的是源码里有没有
    `_params.get('view')` 这类字符串，没有一条断言过 href 的形状。

    所以这里不用文本匹配，直接把真源码扔进 node 跑一遍，断言产出**能被
    解析回原来那两个轴**。
    """

    CASES = [
        (view, domain)
        for view in ("grid", "topology")
        for domain in ("__all__", "WMS", "")
    ]

    def test_a_scope_link_is_a_query_url_not_a_path(self):
        """href 要么正好是 '/'，要么是 '/?...'——绝不能是 '/&...'"""
        for (view, domain), (overview, _box) in _run_scope_links(self.CASES).items():
            assert overview == "/" or overview.startswith("/?"), (
                f"范围/视图链接拼出了路径而不是查询串：view={view!r} "
                f"domain={domain!r} -> {overview!r}。"
                "少一个 '?' 的话浏览器会把它当未知路由跳走，"
                "服务端 404、兜底再把首页吐回来——点上去看着毫无反应。"
            )

    def test_the_link_round_trips_both_axes_back(self):
        """拼出来的查询串解析回来，必须还是当初选的那两个轴

        形状对了还不够：查询串里少写一个轴，页面看着也是「能点」，
        但回到总览时另一个轴已经悄悄丢了。
        """
        for (view, domain), (overview, _box) in _run_scope_links(self.CASES).items():
            from urllib.parse import parse_qs, urlsplit

            parts = urlsplit(overview)
            # keep_blank_values：domain=（未归域）取到的是空串，
            # 默认的 parse_qs 会把空值整个丢掉，测试就会自己骗自己。
            q = parse_qs(parts.query, keep_blank_values=True)

            assert parts.path == "/", f"总览链接的路径不该变：{overview!r}"
            assert q.get("domain", ["__all__"])[0] == domain, (
                f"范围没能从链接里读回来：{overview!r}"
            )
            if view == "grid":
                assert "view" not in q, f"网格是默认值，不该写进 URL：{overview!r}"
            else:
                assert q.get("view", [None])[0] == view, (
                    f"视图没能从链接里读回来：{overview!r}"
                )

    def test_the_unassigned_domain_survives_the_round_trip(self):
        """未归域（空串）必须显式写出 domain=

        URL 里「domain=」和「不写这个参数」长得一样，于是未归域和全部域
        会生成同一个 href，侧边栏里两项一起亮成 active——看着像同时选中了
        两个范围。
        """
        hrefs = _run_scope_links(self.CASES)
        unassigned = hrefs[("grid", "")][0]
        everything = hrefs[("grid", "__all__")][0]

        assert "domain=" in unassigned, f"未归域没显式写进 URL：{unassigned!r}"
        assert unassigned != everything, "未归域和全部域生成了同一个 href"

    def test_a_box_link_keeps_the_axes_and_has_exactly_one_question_mark(self):
        """盒子链接要保住两个轴，且只能有一个 '?'"""
        from urllib.parse import parse_qs, urlsplit

        for (view, domain), (_overview, box) in _run_scope_links(self.CASES).items():
            assert box.count("?") == 1, f"盒子链接里有多个 '?'：{box!r}"

            parts = urlsplit(box)
            q = parse_qs(parts.query, keep_blank_values=True)
            assert parts.path == "/", f"盒子链接的路径不该变：{box!r}"
            assert q.get("box", [None])[0] == "wms_ops_box", f"盒子 id 丢了：{box!r}"
            assert q.get("domain", ["__all__"])[0] == domain, f"盒子链接丢了范围：{box!r}"
            if view == "topology":
                assert q.get("view", [None])[0] == view, f"盒子链接丢了视图：{box!r}"

    def test_the_sidebar_highlights_the_open_box_by_id_not_by_href(self):
        """侧边栏的 active 判据是「哪只盒开着」，不是「href 等于总览链接」

        「视图」和「范围」都搬去筛选条之后，侧边栏只剩盒子链接，而盒子链接
        的 href（`/?box=xxx`）永远不等于总览链接（`/` 或 `/?domain=WMS`）。
        所以那个「href == overviewHref」的循环每轮都会把盒子的高亮**抹掉**——
        打开某只盒的详情页，侧边栏里它却是不亮的，而 renderSidebar 刚给它
        加上的 active 正好被这个循环覆盖。
        """
        sidebar = _extract_js_function("renderSidebar")
        assert "data-box-id" in sidebar, (
            "盒子项要带上 data-box-id —— 没有它就没法按「哪只开着」判高亮"
        )
        ensure = _extract_js_function("ensureSidebar")
        assert "overviewHref(_view, _domainFilter)" not in ensure, (
            "ensureSidebar 不该再拿总览 href 去比侧边栏项——"
            "侧边栏现在只有盒子链接，这个比较每轮都会抹掉高亮"
        )
        assert "data-box-id" in ensure, "高亮同步必须改按 data-box-id 判"



def _js_const_array(name: str) -> str:
    """抽 `const NAME = [...]` 这种字面量常量（多行也要）。"""
    start = HTML_PAGE.index(f"const {name} = ")
    i = HTML_PAGE.index("[", start)
    depth = 0
    for j in range(i, len(HTML_PAGE)):
        if HTML_PAGE[j] == "[":
            depth += 1
        elif HTML_PAGE[j] == "]":
            depth -= 1
            if depth == 0:
                return HTML_PAGE[start : j + 1]
    raise AssertionError(f"{name} 的方括号没配平")


# 两个范围的口径：一个真域（算得出环）、一个跨域（算不出）。
# 「为什么跨域推不出环」是服务端 _layout_for 的推导结果，前端不另造判断。
SCOPE_DOMAINS = {"": ["receipt_capture_box"], "WMS": ["wms_ops_box"]}

# 同一份口径的两种范围：WMS 单域画得出环，__all__ 跨域画不出。
SCOPE_LAYOUTS = {
    "__all__": {"layout": "flat", "reason": "跨域范围推不出唯一汇点"},
    "WMS": {"layout": "radial", "domain": "WMS", "ring": [], "reason": ""},
}


def _run_filters(scope_layouts: dict, view: str, domain: str) -> str:
    """在 node 里真跑 renderFilters，返回它吐出的 HTML。

    用 HTML_PAGE 里的真源码，不抄一份到 Python——抄的那份和页面上的那份
    是两份代码，改了页面上的那份测试照样绿。
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("没有 node，跳过筛选条守卫")

    data = {"scope_layouts": scope_layouts, "domains": SCOPE_DOMAINS}
    src = "\n".join(
        [
            _js_const("ALL_DOMAINS"),
            _js_const("VIEW_GRID"),
            _js_const("VIEW_TOPOLOGY"),
            _js_const("UNASSIGNED_DOMAIN"),
            _js_const_array("FILTER_VIEWS"),
            _extract_js_function("domainLabel"),
            _extract_js_function("esc"),
            _extract_js_function("scopeQuery"),
            _extract_js_function("overviewHref"),
            _extract_js_function("currentScopeLayout"),
            _extract_js_function("renderFilters"),
            "let _view = " + json.dumps(view) + ";",
            "let _domainFilter = " + json.dumps(domain) + ";",
            "console.log(JSON.stringify(renderFilters(" + json.dumps(data) + ")));",
        ]
    )
    proc = subprocess.run(
        [node, "-e", src], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"node 跑 renderFilters 失败：\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestHomepageIsQuieterAndTheViewIsATabBar:
    """首页减字 + 卡片左对齐 + 「视图」搬成内容区顶部的 Tab"""

    def test_the_tab_bar_offers_both_axes_when_the_scope_can_ring(self):
        """能画环的范围：两个 Tab 都是可点的链接，且带着当前范围"""
        # 比对前先 unescape：渲染出来的 HTML 里 & 会被 esc() 转成 &amp;
        # （这是**正确**的 HTML），浏览器读到的才是下面这个裸 URL。
        # 拿转义后的串去断言 URL，等于在断言「转义规则对不对」，
        # 查不出「Tab 指向了哪个视图」——那才是这里要守的东西。
        html = html_mod.unescape(_run_filters(SCOPE_LAYOUTS, "grid", "WMS"))

        assert 'href="/?domain=WMS"' in html, (
            f"网格 Tab 应当指向 WMS 的网格：{html}"
        )
        assert 'href="/?domain=WMS&view=topology"' in html, (
            f"拓扑 Tab 应当指向 WMS 的拓扑：{html}"
        )
        assert "disabled" not in html, "WMS 推得出环，拓扑不该是禁用的"

    def test_the_tab_bar_disables_topology_when_the_scope_cannot_ring(self):
        """推不出环：拓扑渲染成真 <button disabled>，而且没有 href

        不能是一个没有 href 的 <a>——它在无障碍树里退化成 role=generic，
        还会把 title 当成可访问名。
        """
        html = _run_filters(SCOPE_LAYOUTS, "grid", "__all__")

        assert '<button type="button" class="seg-item disabled" disabled' in html, (
            f"跨域范围下拓扑该是禁用 button：{html}"
        )
        assert "view=topology" not in html, (
            f"禁用的拓扑不该留下可点的 href：{html}"
        )
        assert "跨域范围推不出唯一汇点" in html, (
            f"禁用时要写明原因：{html}"
        )

    def test_each_filter_group_highlights_exactly_one_choice(self):
        """每一组各有且仅有一个选中项——两组是两个独立的选择，不是同一个

        断言「整页只有一个 active」是错的：视图组选一个、范围组也选一个，
        那是两个轴各自的状态。判据得按组数。
        """
        for view, scope in (("grid", "WMS"), ("topology", "WMS")):
            html = _run_filters(SCOPE_LAYOUTS, view, scope)
            groups = html.split('class="filter-group"')[1:]
            assert len(groups) == 2, (
                f"应当有视图 / 范围两组，实际 {len(groups)} 组：{html}"
            )
            for g in groups:
                n = g.count('class="seg-item active"')
                assert n == 1, (
                    f"view={view} scope={scope}：每组应恰好一个 active，"
                    f"实际 {n} 个"
                )
            assert 'aria-selected="true"' in html, f"aria-selected 没跟上：{html}"
            assert 'aria-selected="false"' in html, f"非选中项也该标 false：{html}"
            # 范围组用 aria-current（它是筛选，不是 tab 切换）
            assert 'aria-current="true"' in html, f"范围组的当前项没标：{html}"

    def test_the_sidebar_no_longer_owns_the_view_axis(self):
        """「视图」「范围」都搬走了：侧边栏只剩盒子列表"""
        sidebar = _extract_js_function("renderSidebar")
        assert "VIEW_TOPOLOGY" not in sidebar, (
            "侧边栏不该再渲染视图选项——它搬进内容区顶部的筛选条了"
        )
        assert "currentScopeLayout" not in sidebar, (
            "侧边栏不该再算「这个范围能不能画环」——那是筛选条的事"
        )
        # 判据用「侧边栏只生成盒子链接」，而不是「侧边栏里没有 ALL_DOMAINS」：
        # 盒子列表要按当前范围过滤，`_domainFilter === ALL_DOMAINS` 这个判断
        # 是必需的，拿它当「范围搬走了」的证据会把正确代码判成违规。
        assert "overviewHref(" not in sidebar, (
            "侧边栏不该再生成总览链接——它现在只该有盒子详情链接"
        )
        assert "boxHref(" in sidebar, "盒子列表的链接得还在"
        assert 'side-group-label">盒子' in sidebar, "盒子列表得还在"
        # 只查「没有 overviewHref」有盲区：有人用**硬编码** href 加回一个
        # 范围段也照样滑过去。结构判据没有盲区——侧边栏只准有一个分组。
        assert sidebar.count('side-group"') == 1, (
            "侧边栏只该剩「盒子」一个分组，实际 "
            f"{sidebar.count('side-group')} 个——范围被搬回来了"
        )
        assert "renderFilters" in HTML_PAGE, "顶部筛选条的渲染函数不见了"

    def test_the_page_header_is_just_the_title(self):
        """副标题和 in-flight 计数都删了，标题改名

        先剥注释再查：解释「这次为什么删掉它们」的注释里**正好写着**
        这些文案（「beeBoxes in Flight (N)」）。不剥的话守卫命中的是
        注释，删没删都绿——守的是散文，不是页面。
        """
        code = _strip_js_comments(HTML_PAGE)

        assert "<h1>BeeBox Fulfillment Center</h1>" in HTML_PAGE
        assert "BeeBox Fulfillment Overview" not in code, (
            "旧标题还在（<title> 或 h1）"
        )
        assert "auto-refresh every 2s" not in code, (
            "「beeOS Kanban · live view · auto-refresh every 2s」删了"
        )
        assert "beeBoxes in Flight" not in code, (
            "「beeBoxes in Flight (N)」删了——数字和侧边栏清单重复"
        )
        assert "box-count" not in code, (
            "box-count 元素和同步它的死代码都要一起删干净"
        )

    def test_deleting_markup_also_deletes_its_css(self):
        """删掉的元素不许留下孤儿 CSS

        .subtitle 和 .side-note 各只有一个产家，两个产家都随这次改动搬走了。
        留着样式就是「以后有人照着这个 class 写 markup，写出来是隐形的」。
        """
        code = _strip_js_comments(HTML_PAGE)
        for css in (".subtitle {", ".side-note {"):
            assert css not in code, f"{css} 已经没人用了，删掉"

    def test_the_caption_is_left_aligned_and_the_cube_stays_centred(self):
        """立方体居中、文字左对齐，是两件独立的事

        以前整张卡 text-align:center，立方体居中对，文字跟着居中就错了：
        名字长短不一于是每张卡首行起点都不同，ROLE 折行后第二行还是居中，
        卡片底部参差。所以立方体自己居中（auto margin），铭牌管自己的文字。
        """
        assert ".cube-caption { text-align: left;" in HTML_PAGE, (
            "铭牌文字应当左对齐"
        )
        assert ".cube-scene { margin-left: auto; margin-right: auto; }" in (
            HTML_PAGE
        ), "立方体要自己居中，不能靠整张卡 text-align 带着走"
        assert "text-align: center" not in HTML_PAGE.split(".cube-caption")[1][:80], (
            ".cube-caption 上不该再有 text-align: center"
        )

    def test_the_spec_label_column_still_has_a_fixed_width(self):
        """标签列定宽不能丢——那是纵向当表扫的基础"""
        assert ".spec-row .k {" in HTML_PAGE
        assert "width: 104px" in HTML_PAGE, (
            "标签列的定宽被改掉了，8 张卡片的规格行就散开了"
        )


class TestTheThreeRegionsAreVisuallySeparate:
    """头部 / 左侧导航 / 右侧内容要分得开，页面也不该有假的滚动条"""

    def test_the_body_measures_its_min_height_including_padding(self):
        """body 必须 box-sizing:border-box，否则永远多出 64px 可滚

        `min-height:100vh` 量的是**内容盒**，padding:32px 加在它外面——
        于是页面总高恒等于 100vh + 64px，内容再短也多出 64px 可滚，
        右侧永远挂一条滚动条，而读者根本没东西可滚。

        这条曾经只在单盒视图里设过（那里本来就要 border-box），总览页一直漏着。
        """
        body_rule = HTML_PAGE[
            HTML_PAGE.index("body {") : HTML_PAGE.index("h1 {")
        ]
        assert "box-sizing: border-box" in body_rule, (
            "body 少了 box-sizing:border-box —— min-height:100vh 量的是内容盒，"
            "上下 padding 会加在外面，于是内容再短也多出 64px 可滚"
        )

    def test_the_three_regions_each_have_their_own_surface(self):
        """三块各有各的表面，压在同一张底色上就分不出来"""
        code = _strip_js_comments(HTML_PAGE)
        # 头部 / 侧栏 / 内容区三处都要有白底 + 描边的卡片
        assert code.count("background: #fff; border: 1px solid #dbe3ec") >= 3, (
            "头部 / 侧栏 / 内容区应当各是一张白底描边的卡片，"
            "否则三块全糊在同一张底色上"
        )
        for sel in (".page-head {", ".sidebar {", "body:not(.single-box) #content {"):
            assert sel in code, f"{sel} 这块区域不见了"

    def test_the_content_card_is_withheld_from_the_single_box_page(self):
        """单盒详情页不能套内容卡片

        它是 `height:100vh; overflow:hidden` 的整屏 flex 四栏，外面再套一层
        卡片会凭空多吃 padding，矮屏上把四栏挤破。
        """
        assert "body:not(.single-box) #content {" in HTML_PAGE, (
            "内容区卡片必须排除 single-box —— 那是整屏四栏，不该再被卡片包一层"
        )
        assert "body.single-box .page-head {" in HTML_PAGE, (
            "单盒页的头部卡片要单独收薄（padding 8px），把高度让给四栏"
        )
