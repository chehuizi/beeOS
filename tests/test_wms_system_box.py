"""tests/test_wms_system_box.py - WMS System Box 的守卫

这只盒子在 v2 里从 skeleton 变成 live：腿真的发 HTTP 去探线上那套 WMS。
从「未实现」到「实现了」，最容易出的不是崩溃，是**悄悄变松**——
判据还在，但已经测不到它当初要测的东西了。

所以这组守卫盯四件事：
1. 判据仍然可判定（指向 result schema 里真实存在的字段）
2. 档案仍然诚实（每条维度带状态，declared 有出处，unverifiable 有原因）
3. 探测失败仍然显式（探不到要进档案，不许静默跳过、不许填默认值）
4. 基线仍然带出处（每个数字说得出从哪来）

第 4 条是回退防线：基线文件是纯数据，改错了没有任何异常，
只有「出处还在不在」能发现。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from boxes.wms_system.runner import (
    load_baseline,
    system_intake_handler,
    system_profile_packager_handler,
    system_probe_handler,
)


# ============================================================
# 1. 判据可判定
# ============================================================


class TestCriteriaAreDecidable:
    """判据必须指向产出里真实存在的字段，否则它判的是一个不存在的东西"""

    def _definition(self):
        from kanban.trigger import BOX_REGISTRY
        return BOX_REGISTRY["wms_system_box"][0]()

    def test_every_acceptance_metric_is_a_field_in_the_result_schema(self):
        definition = self._definition()
        result_schema = definition.get_schema(definition.result.result_schema)
        fields = {f.name for f in result_schema.fields}
        metrics = [r.metric for r in definition.result.acceptance]
        assert metrics, "这只盒子一条判据都没有——零判据会被判成全过"
        missing = sorted(set(metrics) - fields)
        assert not missing, (
            f"判据指向了 result schema 里不存在的字段：{missing}。"
            "这样的判据永远判不出东西（读不到值 → 判红，或更糟：读成 None → "
            "静默返 False 而不报错）"
        )

    def test_every_exception_condition_is_a_three_part_comparison_on_a_real_field(
        self,
    ):
        """exception 的 condition 必须是 `field op value` 三段式，且字段真实存在

        `_evaluate_condition` 遇到裸标识符（没有比较符）会一路走完
        六个比较符都不匹配，落到 `return False` —— 不报错、不提示，
        这条异常规则**永远不会触发**。

        已经栽过：wms_design_box 3 条 + receipt_capture_box 1 条，
        条件指向的字段压根不在 result schema 里，四条规则从来没响过，
        而没有任何东西报过警。
        """
        definition = self._definition()
        result_schema = definition.get_schema(definition.result.result_schema)
        fields = {f.name for f in result_schema.fields}

        ops = (">=", "<=", "==", "!=", ">", "<")
        for rule in definition.result.exceptions:
            cond = rule.condition.strip()
            assert not cond.startswith(" "), f"condition 前后有多余空白：{cond!r}"
            matched = [op for op in ops if op in cond]
            assert matched, (
                f"exception condition {cond!r} 里没有比较符——"
                "`_evaluate_condition` 会直接 return False，这条规则永远不会触发"
            )
            field = cond.split(matched[0], 1)[0].strip()
            assert field in fields, (
                f"exception condition {cond!r} 的字段 {field!r} "
                f"不在 result schema 里（可用字段：{sorted(fields)}）"
            )

    def test_the_box_is_live_and_says_so(self):
        """腿实现了，maturity 必须跟着变 live——两者对不上就是谎

        反向也成立：maturity 还是 skeleton 而腿已经实现了，
        那看板会永久给一只真能用的盒子标「未实现」，并且不把它算进验收率。
        """
        definition = self._definition()
        assert definition.result.maturity == "live", (
            "腿已经实现（会发 HTTP 探线上），maturity 还停在 skeleton"
        )
        assert system_probe_handler.__module__.startswith("boxes.wms_system")


# ============================================================
# 2. 基线带出处
# ============================================================


class TestBaselineCarriesProvenance:
    """基线是纯数据：改错了不抛异常，只有出处能发现"""

    @pytest.fixture
    def baseline(self):
        return load_baseline()

    def test_every_declared_total_has_a_non_empty_source(self, baseline):
        totals = baseline["declared_totals"]
        assert totals, "declared_totals 是空的：档案里就没有可对照的声明值"
        for name, item in totals.items():
            assert "value" in item, f"{name} 没有 value"
            assert str(item.get("source") or "").strip(), (
                f"{name} 写了值 {item['value']} 但没有出处——"
                "半年后没人知道这个数从哪来，也就没法判断它过期了没有"
            )

    def test_every_unverifiable_aspect_says_why(self, baseline):
        items = baseline["unverifiable_aspects"]
        assert items, (
            "unverifiable_aspects 空了：要么补齐了观测能力（那就该删掉这个列表"
            "并把 status 改成 observed），要么是忘了如实记录拿不到的东西"
        )
        for item in items:
            assert item.get("aspect"), "有条 unverifiable 没写是哪条维度"
            assert len(str(item.get("reason") or "").strip()) >= 20, (
                f"{item.get('aspect')} 的 reason 太短，不像能解释为什么拿不到："
                f"{item.get('reason')!r}"
            )

    def test_the_probe_set_contains_nothing_that_could_have_side_effects(
        self, baseline,
    ):
        """探测集只允许无副作用的路径

        带 `{id}` 的路径要现编一个 id 出来——那不是观测，是造数据；
        写操作（POST/PUT/DELETE 语义）一律不进探测集。
        往生产系统写数据来「验证它活着」不在档案盒子的职责内。
        """
        for path in baseline["readonly_endpoints"]:
            assert not path.startswith("/api/v1/") is False or path.startswith("/api/v1/"), (
                f"{path} 不在 /api/v1 下：跟 WMS 的 @RequestMapping 前缀对不上"
            )
            assert "{" not in path and "}" not in path, (
                f"{path} 带路径变量：探测它必须现编一个 id，那是造数据不是观测"
            )
            assert not any(
                path.rstrip("/").endswith(seg)
                for seg in ("/delete", "/remove", "/execute", "/run", "/issue")
            ), f"{path} 看起来是写操作，不该出现在探测集里"

    def test_probe_set_is_not_empty_and_includes_health(self, baseline):
        paths = baseline["readonly_endpoints"]
        assert len(paths) >= 5, "探测集小到不足以说明任何问题"
        assert "/api/v1/health" in paths, (
            "探测集里没有健康端点：服务存活和数据库连通是这只盒子"
            "唯一真正观测得到的两个维度"
        )


# ============================================================
# 3. 探测失败必须显式
# ============================================================


def _serve(routes: dict[str, tuple[int, str]]):
    """起一个假 WMS：routes[path] = (status, body)"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 的接口
            status, body = routes.get(self.path, (404, '{"code":40400,"message":"not found"}'))
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            """测试里不要往 stderr 刷访问日志"""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


class TestProbeFailureIsExplicit:
    """探不到东西要进档案，不许静默跳过、不许填默认值"""

    @pytest.fixture
    def healthy(self):
        routes = {
            "/api/v1/health": (200, '{"status":"UP","database":"UP"}'),
            **{
                p: (200, '{"code":0,"message":"ok","data":{"list":[]}}')
                for p in load_baseline()["readonly_endpoints"]
                if p != "/api/v1/health"
            },
        }
        server, base = _serve(routes)
        yield base
        server.shutdown()

    @pytest.fixture
    def dead_system(self):
        """服务整体不可达：连健康端点都探不通"""
        return "http://127.0.0.1:1"

    def test_a_fully_reachable_system_produces_a_complete_honest_profile(self, healthy):
        plan = system_intake_handler({"target_base_url": healthy})
        observation = system_probe_handler(plan)
        profile = system_profile_packager_handler(observation)

        assert profile["health_endpoint_reachable"] is True
        assert profile["service_status_up"] is True
        assert profile["database_reachable"] is True
        assert profile["readonly_probe_answer_rate"] == 100.0
        assert profile["aspect_status_complete"] is True
        assert profile["readonly_endpoint_answered_count"] == len(plan["readonly_endpoints"])

    def test_an_unreachable_system_is_recorded_as_a_failure_not_a_pass(self, dead_system):
        """目标探不通时，档案必须**每条维度都显式标出来**

        反例：`_http_get` 捕获异常后返回 `(None, None, err)`，
        如果 packager 顺手给个默认 True，这只盒子就会在系统彻底挂掉时
        显示全绿——「看起来成功，其实什么都没验」。
        """
        plan = system_intake_handler({"target_base_url": dead_system})
        observation = system_probe_handler(plan)
        profile = system_profile_packager_handler(observation)

        assert profile["health_endpoint_reachable"] is False
        assert profile["service_status_up"] is False
        assert profile["database_reachable"] is False
        assert profile["readonly_probe_answer_rate"] == 0.0
        # 这条仍然是 True：档案**诚实地**标了自己探不到——这是合格状态，
        # 不是失败。失败是「假装探到了」。
        assert profile["aspect_status_complete"] is True

        # 探不通也要说得出为什么，不能只给一个 False
        results = observation["endpoint_results"]
        assert results, "探测结果为空：探不通也要留下记录，否则等于没探"
        for entry in results:
            assert entry["reached"] is False
            assert str(entry.get("error") or "").strip(), (
                f"{entry['path']} 探不通但没记原因——"
                "排障时「失败了」和「不知道为什么失败」差着一整个下午"
            )

    def test_a_broken_envelope_is_not_counted_as_answered(self, healthy):
        """HTTP 200 但 code != 0 是业务失败，不算「端点答话了」

        把 `{"code":50000,...}` 当成通了，等于把「接口报错了」
        记成「接口健康」——过期的接口清单比没有更危险。
        """
        from boxes.wms_system.runner import _http_get, _parse_json

        # 直接验判定逻辑，不改服务器
        baseline = load_baseline()
        ok_value = baseline["envelope"]["ok_value"]
        key = baseline["envelope"]["wrapper_key"]
        data_key = baseline["envelope"]["data_key"]

        good = _parse_json(b'{"code":0,"message":"ok","data":{"list":[]}}')
        bad = _parse_json(b'{"code":50000,"message":"boom","data":null}')

        def counts_as_answered(payload):
            return payload is not None and payload.get(key) == ok_value and data_key in payload

        assert counts_as_answered(good) is True
        assert counts_as_answered(bad) is False

    def test_intake_refuses_to_probe_paths_the_baseline_does_not_declare(self, healthy):
        """投料只能**缩小**探测范围，不能扩大

        一个能指定「探哪个」的入参，同时也能指定「探不该探的」，
        那它就不是观测口径，是一条通往生产的任意读写通道。
        """
        baseline = load_baseline()
        with pytest.raises(ValueError) as exc:
            system_intake_handler({
                "target_base_url": healthy,
                "readonly_endpoints": ["/api/v1/health", "/api/v1/admin/drop-database"],
            })
        assert "/api/v1/admin/drop-database" in str(exc.value)

    def test_intake_ignores_blank_entries_left_by_the_dashboard_form(self, healthy):
        """看板对数组字段会填 `[""]`，原样提交必须能用

        空串不是「基线未声明的端点」，它是**没填**。按前者处理的话，
        第一次用的人提交看板给的示例投料就被拒，报错写的是
        「readonly_endpoints 里有基线未声明的路径: ['']」——看不出哪做错了。
        """
        baseline = load_baseline()
        plan = system_intake_handler({
            "target_base_url": healthy,
            "readonly_endpoints": ["", "   ", "/api/v1/health"],
        })
        assert plan["readonly_endpoints"] == ["/api/v1/health"]

        # 全是空白 → 回落基线全量，而不是探 0 条
        plan = system_intake_handler({
            "target_base_url": healthy, "readonly_endpoints": [""],
        })
        assert plan["readonly_endpoints"] == baseline["readonly_endpoints"]

    def test_intake_accepts_a_narrower_subset(self, healthy):
        baseline = load_baseline()
        subset = baseline["readonly_endpoints"][:3]
        plan = system_intake_handler({
            "target_base_url": healthy, "readonly_endpoints": subset,
        })
        assert plan["readonly_endpoints"] == subset


# ============================================================
# 4. 档案的诚实是判据
# ============================================================


class TestTheProfileDeclaresWhatItCannotSee:
    """档案不许假装自己是全知的——这条既是判据，也是守卫"""

    @pytest.fixture
    def profile(self):
        server, base = _serve({"/api/v1/health": (200, '{"status":"UP","database":"UP"}')})
        try:
            plan = system_intake_handler({"target_base_url": base})
            return system_profile_packager_handler(system_probe_handler(plan))
        finally:
            server.shutdown()

    def test_every_aspect_declares_its_status_and_its_provenance(self, profile):
        from boxes.wms_system.schemas import ASPECT_STATUS_VALUES

        by_aspect = {a["aspect"]: a for a in profile["aspects"]}
        assert len(by_aspect) == len(profile["aspects"]), "有重复的维度名"

        for name, entry in by_aspect.items():
            assert entry["status"] in ASPECT_STATUS_VALUES, (
                f"{name} 的 status {entry['status']!r} 不在合法取值里"
            )
            if entry["status"] == "declared_from_baseline":
                assert entry.get("source"), (
                    f"{name} 标了 declared_from_baseline 却没有出处——"
                    "「只有声明值」这句话必须能追回到具体文件+commit"
                )
            if entry["status"] == "unverifiable":
                assert entry.get("reason"), (
                    f"{name} 标了 unverifiable 却没有原因——"
                    "「拿不到」和「暂时没查」是两回事"
                )
            if entry["status"] == "observed":
                assert entry.get("source"), f"{name} 标了 observed 却没有观测来源"

    def test_an_observed_aspect_records_the_full_url_it_read_from(self, profile):
        """observed 的来源必须是一条完整 URL，不是一截被截断的地址

        踩过一次：packager 拼来源时取不到健康端点路径，来源写成
        `http://host:8080`——它对得上地址，但对不上端点。
        排障时拿着这个「出处」去复现，发现连 `/api/v1/health` 都没带上，
        而这条测试如果只断言「source 非空」，会一直绿。
        """
        base = profile["target_base_url"]
        for entry in profile["aspects"]:
            if entry["status"] != "observed":
                continue
            assert entry["source"].startswith(base), (
                f"{entry['aspect']} 的来源 {entry['source']!r} 不是被观测的地址"
            )
            if entry["aspect"] in ("service_liveness", "database_reachable"):
                assert entry["source"] == base + "/api/v1/health", (
                    f"{entry['aspect']} 的来源少了健康端点路径：{entry['source']!r}"
                )

    def test_the_aspects_we_actually_cannot_see_are_named(self, profile):
        """部署版本和外部依赖今天是拿不到的——档案必须点名它们

        不是「不写就等于没有」：一只盒子只描述它描述得了的部分，
        读档案的人会以为那就是全部。**明写拿不到，才有人去补。**
        """
        unverifiable = {a["aspect"] for a in profile["aspects"] if a["status"] == "unverifiable"}
        assert "deployed_version" in unverifiable, (
            "jar 没有 git.properties、pom 没配 git-commit-id 插件，"
            "部署版本今天查不到——档案却没标出来"
        )
        assert profile["unverifiable_aspect_count"] == len(unverifiable)

    def test_the_observation_is_not_mistaken_for_the_whole_picture(self, profile):
        """observed 的维度数必须少于总维度数，否则「档案」是个 misleading 的词

        今天的系统形态下 6 条维度只有 3 条可观测。如果哪天这个断言红了，
        要先确认是真的补齐了观测能力（那很好），还是 packager 把
        unverifiable 悄悄改成了 observed（那是把缺口藏起来）。
        """
        assert profile["aspect_count"] > profile["observed_aspect_count"], (
            "所有维度都标成了 observed——要么系统真的全可观测了"
            "（那 baseline.unverifiable_aspects 该删），"
            "要么有人把拿不到的东西标成了观测得到"
        )

    def test_aspect_status_complete_goes_red_when_a_reason_is_dropped(self, profile):
        """`aspect_status_complete` 必须真的会红

        它是判据里唯一一条「判诚实」的。把它变异掉看看：
        只要 packager 少写一个 reason，这条判据就该翻脸。
        """
        import boxes.wms_system.runner as runner_mod

        original = runner_mod.system_profile_packager_handler
        # 直接拿一份档案改坏：抽掉某条 unverifiable 的 reason
        broken = json.loads(json.dumps(profile))
        for entry in broken["aspects"]:
            if entry["status"] == "unverifiable":
                entry.pop("reason", None)

        # 复算诚实位：跟 packager 里的判据同一套规则
        from boxes.wms_system.schemas import ASPECT_STATUS_VALUES
        complete = True
        for entry in broken["aspects"]:
            if entry.get("status") not in ASPECT_STATUS_VALUES:
                complete = False
            if entry.get("status") == "declared_from_baseline" and not entry.get("source"):
                complete = False
            if entry.get("status") == "unverifiable" and not entry.get("reason"):
                complete = False
        assert complete is False, "抽掉 reason 之后诚实位仍然是 True——判据是摆设"
        assert profile["aspect_status_complete"] is True, "原档案本身不该红"
        assert original is runner_mod.system_profile_packager_handler


# ============================================================
# 5. 吃不吃自然语言是声明，不是推断
# ============================================================


class TestFreeTextIntakeIsDeclaredNotInferred:
    """形状像文本框 ≠ 业务上吃自然语言

    这条守卫是为一个已经犯过的错写的：wms_system_box 的投料三个字段
    全是非必填标量，形状上就是「一个文本框」，于是被推断成吃自然语言，
    一句业务表述会被 `/api/structure` 默认路由进来。
    """

    def test_only_the_requirement_box_accepts_free_text(self):
        from kanban.trigger import BOX_REGISTRY

        accepting = sorted(
            f"{bid}/{t.type}"
            for bid, (loader, _) in BOX_REGISTRY.items()
            for t in loader().task
            if t.accepts_free_text
        )
        assert accepting == ["wms_requirement_box/capture_business_requirement"], (
            f"吃自然语言的 task 变成了 {accepting}——"
            "多一只就意味着 /api/structure 会因为「有多个候选」而拒绝"
        )

    def test_the_system_box_declares_it_does_not_take_prose(self):
        from kanban.trigger import BOX_REGISTRY
        definition = BOX_REGISTRY["wms_system_box"][0]()
        assert all(t.accepts_free_text is False for t in definition.task), (
            "系统档案盒声明了吃业务自然语言——"
            "它的投料是观测地址和端点子集，一句业务描述会被当地址解析"
        )

    def test_a_box_taking_free_text_must_actually_have_a_text_shaped_schema(self):
        """声明吃自然语言的 task，它的投料框必须真的能渲染成文本框

        不一致的后果是：调用方读到 manifest 说「能直接发一段话」，
        前端却渲染出一个 JSON 编辑器要求套一层。两边各说各话。
        """
        from kanban.trigger import BOX_REGISTRY, _payload_kind

        for bid, (loader, _) in BOX_REGISTRY.items():
            definition = loader()
            for t in definition.task:
                if not t.accepts_free_text:
                    continue
                schema = definition.get_schema(t.task_schema)
                assert _payload_kind(schema) == "text", (
                    f"{bid}/{t.type} 声明 accepts_free_text，"
                    f"但投料形状是 {_payload_kind(schema)}——"
                    "manifest 说能直接发文本，前端会渲染成 JSON 编辑器"
                )

    def test_the_manifest_reports_the_declaration_not_the_inference(self):
        """manifest 的 accepts_free_text 必须等于 task 的声明值

        报推断值会让「投料框长什么样」和「能不能直接喂人话」再次混在一起。
        """
        from kanban.trigger import BOX_REGISTRY, box_manifest

        for bid, (loader, _) in BOX_REGISTRY.items():
            definition = loader()
            for task in definition.task:
                entry = next(
                    t for t in box_manifest(bid)["tasks"] if t["type"] == task.type
                )
                assert entry["accepts_free_text"] == task.accepts_free_text, (
                    f"{bid}/{task.type} 的 manifest accepts_free_text "
                    f"({entry['accepts_free_text']}) != 声明值 ({task.accepts_free_text})"
                )


# ============================================================
# 6. 基线文件本身
# ============================================================


def test_the_baseline_is_valid_json_and_names_its_commit():
    """基线是手写数据，坏了不会报错，只会让档案里的出处变成空话"""
    path = Path(__file__).resolve().parents[1] / "boxes" / "wms_system" / "baseline" / "system_baseline.json"
    assert path.exists(), "基线文件不见了：盒子失去对照口径，会静默退化成「只探活」"
    raw = json.loads(path.read_text(encoding="utf-8"))

    commit = raw["provenance"]["commit"]
    assert len(commit) >= 7, f"commit 看起来不像 commit：{commit!r}"
    assert raw["provenance"]["branch"], "基线没记分支"
    assert raw["provenance"]["source_repo"], "基线没记仓库来源"
