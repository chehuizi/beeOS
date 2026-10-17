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
        """推不出环时必须禁用 + 写明原因，不许静默退回网格"""
        assert "'<button type=\"button\" class=\"side-item disabled\" disabled'" in HTML_PAGE, (
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

    def test_the_sidebar_compares_hrefs_that_agree_with_each_other(self):
        """侧边栏 active 高亮是拿 href 比 href 的——两边必须用同一个函数

        `ensureSidebar` 里 `a.getAttribute('href') === overviewHref(_view, _domainFilter)`。
        两边一旦不是同一套拼法，高亮就永远对不上——而且症状是「点了没反应」
        加「当前项不亮」，看着像筛选坏了，实际只是比较的两端长得不一样。
        """
        assert "overviewHref(_view, _domainFilter)" in HTML_PAGE, (
            "侧边栏 active 的比较基准必须用 overviewHref —— "
            "换成手拼字符串就会和链接对不上，症状是永远不高亮"
        )
        assert "_domainFilter === key" in HTML_PAGE, (
            "范围项的 active 判据应当是「就是当前这个范围」——"
            "拼出来的字符串比较会让空串范围永远不高亮"
        )

    def test_the_page_reads_the_empty_domain_back_as_empty(self):
        """URL 读回一侧：domain= 必须读成空串，不能静默变成「全部域」

        这一半和拼链接那一半是同一个 bug 的两头。链接侧已经用 has() 判
        「参数在不在」；读回侧如果还用 `_params.get('domain') || ALL_DOMAINS`，
        空串就是 falsy，会被当成没给这个参数——点「未归域」看见的是全部 8 只
        盒，侧边栏高亮也回不到它身上，同样一条错都不报。

        这里直接把 HTML_PAGE 里那段真源码掏出来，在 node 里喂三种 URL。
        """
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            pytest.skip("没有 node，跳过 URL 读回守卫")

        start = HTML_PAGE.index("const _params = new URLSearchParams(")
        end = HTML_PAGE.index(";", HTML_PAGE.index("_domainFilter", start)) + 1
        reader = HTML_PAGE[start:end]

        for search, expected in (
            ("", "__all__"),          # 没给 domain
            ("?domain=WMS", "WMS"),   # 真域
            ("?domain=", ""),         # 未归域：空串是合法取值
            ("?domain=WMS&view=topology", "WMS"),
        ):
            src = (
                "const location = { search: " + json.dumps(search) + " };\n"
                + _js_const("ALL_DOMAINS") + "\n"
                + reader
                + "\nconsole.log(JSON.stringify(_domainFilter));"
            )
            proc = subprocess.run(
                [node, "-e", src], capture_output=True, text=True, timeout=30
            )
            assert proc.returncode == 0, f"node 跑 URL 读回失败：\n{proc.stderr}"
            got = json.loads(proc.stdout.strip().splitlines()[-1])
            assert got == expected, (
                f"URL {search!r} 读回来是 {got!r}，应该是 {expected!r}。"
                "用真值判断（||）会把未归域的空串当成「没给这个参数」，"
                "静默退回全部域——看起来像筛选没生效。"
            )
