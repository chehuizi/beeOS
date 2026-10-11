"""tests.test_receipt_box - 小票识别盒

重点不在「能不能跑通」，在两件事：

1. **判据是真的**。给一份算术错的解析，判据必须红——
   一条永远绿的判据比没有判据更坏，它把「没验」说成「验过了」。
   这里逐条喂错误数据，断言对应的判据转红。

2. **盲区是真的**。「模型把 12.50 认成 12.05」这种错误，
   算术照样自洽，机械判据必然全绿——这不是判据的缺陷，
   是"内容正确"里机械验不到的那一段。测试把这件事钉住，
   免得以后有人以为加了判据就等于内容正确了。

LLM 那一步不 stub（真调要花钱且慢），机械那两步全真测。
"""

from __future__ import annotations

import base64
import inspect
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# 一张真的、能被 sips 读的 JPEG（4.5KB 合成小票）。
# 手搓的假 JPEG（ffd8ff + 一串 0）sips 会拒——转换测试必须走真文件，
# 否则测的是「我塞了个对的魔数」，不是「真的转出来了」。
RECEIPT_JPEG = Path(__file__).parent / "fixtures" / "receipt_sample.jpg"
from pathlib import Path

import pytest

from boxes.receipt import get_definition
from boxes.receipt import measure as boxes_receipt_measure
from boxes.receipt.measure import COMPUTERS, _rate
from boxes.receipt.runner import _simulate_verify_receipt as verify
from core import metrics as m
from core.beeline_models import NextRef
from core.models import NOT_APPLICABLE, SchemaDef
from kanban.trigger import (
    BOX_REGISTRY,
    RUNNER_REGISTRY,
    _payload_kind,
    media_fields,
    trigger_task,
)
from runtime.store import TaskRunStore


BOX = "receipt_capture_box"


def _parsed(**over):
    base = {
        "merchant_name": "阳光超市",
        "receipt_no": "20261010-0847",
        "receipt_date": "2026-10-10",
        "receipt_time": "14:23",
        "items": [
            {"line_no": 1, "name": "可乐", "qty": 2, "unit_price": 3.5, "amount": 7.0},
            {"line_no": 2, "name": "面包", "qty": 1, "unit_price": 12.8, "amount": 12.8},
        ],
        "subtotal": 19.8, "tax": 1.19, "total": 20.99,
        "payment_method": "微信支付",
    }
    base.update(over)
    return base


class TestJudgeActuallyJudges:
    """判据必须真的会红"""

    def test_consistent_receipt_passes_everything(self):
        r = verify({"parsed": _parsed()})
        assert all(r[k] for k in (
            "field_completeness", "line_arithmetic", "sum_arithmetic",
            "total_arithmetic", "date_wellformed", "totals_match",
        ))

    def test_line_arithmetic_catches_a_bad_line(self):
        """某行 数量×单价 ≠ 金额"""
        r = verify({"parsed": _parsed(
            items=[{"qty": 2, "unit_price": 3.5, "amount": 7.5}],
            subtotal=7.5, tax=0.4, total=7.9,
        )})
        assert r["line_arithmetic"] is False
        assert r["totals_match"] is False
        assert r["line_errors"] == [1]

    def test_total_arithmetic_catches_subtotal_plus_tax_mismatch(self):
        r = verify({"parsed": _parsed(
            items=[{"qty": 1, "unit_price": 10.0, "amount": 10.0}],
            subtotal=10.0, tax=0.5, total=11.5,   # 10 + 0.5 = 10.5 ≠ 11.5
        )})
        assert r["total_arithmetic"] is False
        assert r["line_arithmetic"] is True, "行算术是对的，不该被误判"

    def test_sum_arithmetic_catches_row_total_mismatch(self):
        r = verify({"parsed": _parsed(
            items=[{"qty": 1, "unit_price": 10.0, "amount": 10.0}],
            subtotal=99.0, tax=0.0, total=99.0,
        )})
        assert r["sum_arithmetic"] is False
        assert r["total_arithmetic"] is True, "小计+税=合计是对的，不该误判"

    def test_date_wellformed_catches_a_free_text_date(self):
        r = verify({"parsed": _parsed(receipt_date="2026年10月10日")})
        assert r["date_wellformed"] is False
        assert r["totals_match"] is False

    def test_field_completeness_catches_a_totally_empty_read(self):
        """模型什么都没读出来 —— 全是空不该算「解析成功」"""
        r = verify({"parsed": {
            "merchant_name": "", "receipt_no": "", "items": [],
            "subtotal": None, "tax": None, "total": None,
            "receipt_date": "2026-10-10",
        }})
        assert r["field_completeness"] is False
        assert r["totals_match"] is False

    def test_missing_line_is_not_silently_zero(self):
        """读不出的行不能当 0 混进求和——那会让 Σ行金额 看起来自洽"""
        r = verify({"parsed": _parsed(
            items=[{"qty": 1, "unit_price": 10.0, "amount": None}],
            subtotal=10.0, tax=0.0, total=10.0,
        )})
        assert r["line_arithmetic"] is False


class TestDeclaredBlindSpot:
    """验不到的那一段，如实钉住"""

    def test_wrong_text_with_consistent_maths_still_passes(self):
        """模型把「香菜」认成「香芋」，12.50 认成 12.05 —— 算术照样自洽

        这条测试看起来在庆祝一个缺陷。它确实在庆祝：
        它把「机械判据不能证明内容正确」这件事钉成可执行的断言。
        以后有人想给「解析内容完全正确」加一条机械判据，
        会先撞到这里想清楚自己到底在验什么。
        """
        r = verify({"parsed": _parsed(
            items=[{"line_no": 1, "name": "香芋", "qty": 1,
                    "unit_price": 12.05, "amount": 12.05}],
            subtotal=12.05, tax=0.0, total=12.05,
        )})
        assert r["totals_match"] is True, (
            "这条断言在记录盲区的存在。哪天它失败了，"
            "说明机械判据开始误以为自己能验内容正确性了——那更要查"
        )

    def test_blind_spot_is_written_down_in_the_definition(self):
        """盲区不能只活在测试里，盒子的声明上也得写

        否则下一个人看 acceptance 全绿，以为「内容正确」有保证。
        """
        top = (get_definition().description or "") + Path(
            "boxes/receipt/definition.py"
        ).read_text(encoding="utf-8")
        assert "人工对账" in top
        assert "low_confidence_fields" in top

    def test_forbidden_rule_bans_fixing_numbers_to_go_green(self):
        """盒子的 queen 明令禁止「为了让判据变绿去改数字」"""
        forbidden = get_definition().queen.rules.exception_handling["forbidden"]
        assert "recompute_totals_to_pass" in forbidden


class TestBoxWiring:
    def test_photo_field_is_declared_as_media(self):
        d = get_definition()
        schema = d.get_schema(d.task[0].task_schema)
        assert _payload_kind(schema) == "media"
        assert [f.name for f in media_fields(schema)] == ["photo"]

    def test_beeline_has_exactly_one_start_op(self):
        """起点判定是「没人指向它」——next 不串起来会有三个起点，直接中止"""
        beeline = BOX_REGISTRY[BOX][1]["beeline_receipt_capture_v1"]()
        assert len(beeline.get_start_operations()) == 1
        assert beeline.get_start_operations()[0].op_id == "read_receipt"

    def test_only_the_first_op_touches_the_model(self):
        """LLM 只在第 1 步。校验和打包再碰模型，「凑平数字」这条路就堵死了。"""
        import inspect

        from boxes.receipt import runner

        assert "complete_json" in inspect.getsource(runner._simulate_read_receipt)
        assert "complete_json" not in inspect.getsource(runner._simulate_verify_receipt)
        assert "complete_json" not in inspect.getsource(runner._simulate_package_receipt)

    def test_runner_handles_every_declared_op(self):
        runner = RUNNER_REGISTRY[BOX]
        beeline = BOX_REGISTRY[BOX][1]["beeline_receipt_capture_v1"]()
        for op in beeline.operations:
            assert runner.supports(op.type)

    def test_declared_metrics_have_collectors(self):
        d = get_definition()
        assert m.unwired(d, COMPUTERS) == []
        assert m.orphan_computers(d, COMPUTERS) == []

    def test_every_acceptance_metric_is_a_produced_field(self):
        """判据读的每个值都得是产出契约的一部分，否则读不到"""
        d = get_definition()
        fields = {f.name for f in d.get_schema(d.result.result_schema).fields}
        for rule in d.result.acceptance:
            assert rule.metric in fields

    def test_missing_photo_is_refused_before_fulfillment(self):
        """第一道闸：形状不对就不产生 TaskRun"""
        from kanban.trigger import TriggerError

        store = TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))
        with pytest.raises(TriggerError):
            trigger_task(BOX, "capture_receipt", {}, store)
        assert store.list_recent(limit=5) == []

    def test_a_receipt_with_broken_maths_is_rejected(self, monkeypatch):
        """端到端：判据红 → acceptance rejected（stub LLM，不花钱）

        patch 的是 boxes.receipt.runner 上的名字，不是 runtime.llm：
        runner 里是模块级 from ... import complete_json，
        改源模块的属性影响不到已经绑定的引用。
        """
        from boxes.receipt import runner as receipt_runner

        monkeypatch.setattr(receipt_runner, "complete_json", lambda *a, **kw: _parsed(
            items=[{"line_no": 1, "name": "可乐", "qty": 2,
                    "unit_price": 3.5, "amount": 7.5}],   # 2×3.5=7.0≠7.5
            subtotal=7.5, tax=0.0, total=8.5,
        ))
        store = TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))
        r = trigger_task(
            BOX, "capture_receipt",
            {"photo": "data:image/jpeg;base64," + base64.b64encode(b"x").decode()},
            store,
        )
        assert r["status"] == "completed"
        assert r["acceptance_status"] == "rejected"
        failed = {a["metric"] for a in r["acceptance_detail"] if not a["passed"]}
        assert "line_arithmetic" in failed
        assert "totals_match" in failed

    def test_a_consistent_receipt_is_accepted(self, monkeypatch):
        from boxes.receipt import runner as receipt_runner

        monkeypatch.setattr(receipt_runner, "complete_json", lambda *a, **kw: _parsed())
        store = TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))
        r = trigger_task(
            BOX, "capture_receipt",
            {"photo": "data:image/jpeg;base64," + base64.b64encode(b"x").decode()},
            store,
        )
        assert r["acceptance_status"] == "accepted"
        assert r["artifact_view"] == "receipt"
        assert r["result"]["merchant_name"] == "阳光超市"

    def test_llm_failure_aborts_instead_of_faking_a_receipt(self, monkeypatch):
        """LLM 挂了就是履约中止，不许吐一份空解析装成功"""
        from runtime import llm
        from boxes.receipt import runner as receipt_runner

        def boom(*a, **kw):
            raise llm.LLMError("vision unavailable")

        monkeypatch.setattr(receipt_runner, "complete_json", boom)
        store = TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))
        r = trigger_task(
            BOX, "capture_receipt",
            {"photo": "data:image/jpeg;base64," + base64.b64encode(b"x").decode()},
            store,
        )
        assert r["status"] == "failed"
        assert r["acceptance_status"] is None
        assert r["result"] in (None, {})
        rec = store.list_recent(limit=1)[0]
        assert rec["error_kind"] == "llm_call"

# ============================================================
# 媒体格式：能在投料口拒掉的，不该让它走到 API 才 400
# ============================================================


class TestMediaTypeGate:
    """image/* 是浏览器的说法，不是「系统认的」的说法

    实测踩坑：iPhone 的 .HEIC，MIME 是 image/heic，浏览器能选中、
    FileReader 能读出 data URL，一路带到 API 才被 400 拒——
    `invalid param: image data url media type ["image/heic"] not supported`。
    用户等一整轮，才看到一句读不懂的 JSON。
    """

    def test_heic_is_rejected_with_a_readable_message(self):
        from runtime.llm import image_data_url

        with pytest.raises(ValueError) as ei:
            image_data_url("data:image/heic;base64,AAAA")
        msg = str(ei.value)
        assert "image/heic" in msg
        # 光说「不支持」等于让用户以为自己拍错了，得告诉他该拿它怎么办
        assert "JPEG" in msg

    @pytest.mark.parametrize("mime", ["image/avif", "image/svg+xml", "image/bmp"])
    def test_other_unsupported_types_also_rejected(self, mime):
        from runtime.llm import image_data_url

        with pytest.raises(ValueError):
            image_data_url(f"data:{mime};base64,AAAA")

    @pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp", "image/gif"])
    def test_supported_types_pass_through_byte_identical(self, mime):
        """放行的路径一个字节都不能变——这道闸不是来改数据的"""
        from runtime.llm import image_data_url

        u = f"data:{mime};base64,AAAA"
        assert image_data_url(u) == u

    def test_plain_bytes_still_builds_jpeg_url_unchanged(self):
        """纯字节 + 默认 mime 的老路径，逐字节不变"""
        from runtime.llm import image_data_url

        assert image_data_url(b"xx") == "data:image/jpeg;base64,eHg="

    def test_gate_sits_where_every_call_converges(self):
        """闸必须开在 complete()，不能只挂在 image_data_url 上

        实测踩坑：把校验挂在 image_data_url() 里看着挺对，但投料口那边
        FileReader 已经把完整 data URL 拼好传过来了，压根不经过这个
        辅助函数 —— image/heic 一路穿到 API 才 400。
        凡是 images 参数最终汇合的地方，就得在那儿查。
        """
        import inspect as _inspect

        from runtime.llm import complete

        src = _inspect.getsource(complete)
        assert "_check_image_url" in src, "complete() 里没有校验 images，闸形同虚设"

    def test_check_image_url_rejects_data_url_but_passes_http(self):
        from runtime.llm import _check_image_url

        with pytest.raises(ValueError):
            _check_image_url("data:image/heic;base64,AAAA")
        _check_image_url("data:image/jpeg;base64,AAAA")
        # http 直链本地查不出 MIME，不该在这里误杀
        _check_image_url("https://example.com/a.heic")

    def test_box_declares_what_it_accepts(self):
        """支持哪些格式由盒子声明，不在前端硬编码

        换只盒子支持别的格式，看板一个字都不用改。
        """
        from core.models import FieldDef
        from kanban.trigger import box_manifest

        photo = next(
            f for f in get_definition().get_schema("schema_receipt_photo").fields
            if f.name == "photo"
        )
        # 盒子自己说了算：小票盒收 HEIC（iPhone 拍的就是这个），
        # 不代表平台默认集也得收——默认集是给「没声明」的盒子用的保守值
        assert "image/jpeg" in photo.accepted_media_types()
        assert photo.media_accept, "photo 字段没显式声明 media_accept"

        mf = box_manifest(BOX)["tasks"][0]["media_fields"][0]
        assert mf["name"] == "photo"
        assert mf["accept"] == photo.accepted_media_types()

    def test_platform_default_excludes_heic(self):
        """没声明 media_accept 的盒子走平台默认集，那套里不该有 HEIC

        没转码能力就收 HEIC = 让它撞 400。默认集保守是对的：
        收得窄不会出错，收得宽会。
        """
        from core.models import DEFAULT_MEDIA_ACCEPT

        assert "image/heic" not in DEFAULT_MEDIA_ACCEPT
        assert "image/jpeg" in DEFAULT_MEDIA_ACCEPT

    def test_kanban_takes_accept_from_the_declaration(self):
        """投料口的 accept 得来自盒子声明，不能退回 image/*

        HTML_PAGE 是模板：accept 的值在渲染时才算出来。
        所以这里查两件事——属性位引的是变量而不是字面量，
        以及那个变量确实是从 mediaField.accept 取的、兜底也不含 heic。
        """
        import re

        from kanban.web import HTML_PAGE

        m = re.search(r'<input type="file" id="media-file" accept="([^"]*)"', HTML_PAGE)
        assert m, "找不到 media-file 的 accept 属性"
        assert m.group(1) == "${acceptAttr}", (
            f"accept 不再取自声明，硬编码成了 {m.group(1)!r}"
        )
        # 声明缺失时的兜底值也得收窄：退回 image/* 等于这道闸白设。
        # 只查兜底那一句，别全文找 —— MEDIA_FIX_HINT 里必须有 image/heic，
        # 那是「遇到它该说什么」的映射表，正是这道闸的一部分。
        assert (
            "['image/jpeg', 'image/png', 'image/webp', 'image/gif']" in HTML_PAGE
        ), "accept 的兜底清单变了，得确认它仍然不含 HEIC"
        assert "mediaField.accept" in HTML_PAGE, "accept 没接到盒子声明上"


# ============================================================
# HEIC：iPhone 拍的小票默认就是这个格式，得收
# ============================================================


class TestHeicAccepted:
    """HEIC 不是「不支持的怪格式」，是小票最常见的来源之一

    拒它的理由只有两条，而且都得先转：模型接口 400（确定性拒绝，
    重试无用），Chrome 也解不了它（预览是张破图）。
    格式本身没有优劣——传出去是 JPEG 还是 HEIC，取决于下游要什么。
    """

    HEIC = base64.b64encode(b"not-really-heic-but-bytes").decode()

    def _url(self, mime):
        return f"data:{mime};base64,{self.HEIC}"

    def test_box_declares_heic_as_accepted(self):
        photo = next(
            f for f in get_definition().get_schema("schema_receipt_photo").fields
            if f.name == "photo"
        )
        assert "image/heic" in photo.accepted_media_types()
        assert "image/heif" in photo.accepted_media_types()

    @pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp", "image/gif"])
    def test_non_heic_passes_through_byte_identical(self, mime):
        """这道闸不是来改数据的：能直接用的格式一个字节都不许动"""
        from boxes.receipt.runner import _as_viewable_image

        u = self._url(mime)
        assert _as_viewable_image(u) == u

    def test_http_url_passes_through(self):
        """http 直链本地无从转，原样交给下游，不瞎猜"""
        from boxes.receipt.runner import _as_viewable_image

        assert _as_viewable_image("https://x/y.heic") == "https://x/y.heic"

    def test_heic_becomes_a_real_jpeg(self):
        """真的出 JPEG（看 SOI 魔数），不是把 MIME 标签改了就当转过了

        走真文件：仓库里这张 fixture JPEG → sips 转成真 HEIC → 盒子转回
        JPEG。手搓的假 JPEG（ffd8ff + 一串 0）sips 直接拒，本轮就踩过。
        """
        from boxes.receipt.runner import _as_viewable_image

        if shutil.which("sips") is None:
            pytest.skip("没有 sips，跳过真转换验证")
        tmp = Path(tempfile.mkdtemp())
        heic = tmp / "probe.heic"
        subprocess.run(
            ["sips", "-s", "format", "heic", str(RECEIPT_JPEG), "--out", str(heic)],
            capture_output=True, check=True,
        )
        url = "data:image/heic;base64," + base64.b64encode(heic.read_bytes()).decode()
        out = _as_viewable_image(url)
        assert out.startswith("data:image/jpeg;base64,")
        assert base64.b64decode(out.split(",", 1)[1])[:3] == b"\xff\xd8\xff"

    def test_missing_sips_fails_loudly(self, monkeypatch):
        """转不了就如实说，不悄悄把 HEIC 原样发出去

        悄悄退回去 = 把同样的 400 原封不动还给用户，只是晚了一拍。
        """
        from boxes.receipt import runner as receipt_runner

        monkeypatch.setattr(receipt_runner.shutil, "which", lambda _: None)
        with pytest.raises(ValueError) as ei:
            receipt_runner._as_viewable_image(self._url("image/heic"))
        assert "sips" in str(ei.value)

    def test_converter_failure_is_reported(self, monkeypatch):
        """sips 在但转不动（文件不是真 HEIC 等）——报 sips 说的原话"""
        from boxes.receipt import runner as receipt_runner

        monkeypatch.setattr(receipt_runner.shutil, "which", lambda _: "/usr/bin/sips")

        class _Proc:
            returncode = 1
            stdout = b""
            stderr = b"bad image data"

        monkeypatch.setattr(receipt_runner.subprocess, "run", lambda *a, **k: _Proc())
        with pytest.raises(ValueError) as ei:
            receipt_runner._as_viewable_image(self._url("image/heic"))
        assert "bad image data" in str(ei.value)

    def test_read_receipt_converts_before_calling_the_model(self, monkeypatch):
        """转换必须发生在调模型**之前**，而不是指望模型自己看得懂 HEIC"""
        from boxes.receipt import runner as receipt_runner

        seen = {}

        def spy(prompt, **kw):
            seen["images"] = kw.get("images")
            return {"merchant_name": "某超市", "items": []}

        monkeypatch.setattr(receipt_runner.shutil, "which", lambda _: None)
        monkeypatch.setattr(receipt_runner, "complete_json", spy)
        # 没 sips → 转换失败 → 一张图都不该发出去
        with pytest.raises(ValueError):
            receipt_runner._simulate_read_receipt({"photo": self._url("image/heic")})
        assert not seen, "转换失败后仍然调了模型"

    def test_kanban_shows_a_note_not_a_broken_image(self):
        """浏览器解不了的格式摆说明牌，不摆破图

        破图会被读成「载了张图但坏了」，实际是「压根没坏，是这个格式
        浏览器解不了」——这两件事得让用户分得清。
        """
        from kanban.web import HTML_PAGE

        assert "NO_PREVIEW_TYPES" in HTML_PAGE, "没有「画不出就摆说明牌」的分支"
        assert "media-nopreview" in HTML_PAGE


# ============================================================
# 没读到的 ≠ 读到的 0
# ============================================================


class TestNoFabricatedZeros:
    """拿不到就留空，不补 0

    实测踩坑（真盒马小票）：模型对小计看不清，返回 null —— 这是对的，
    prompt 就写着「看不清填 null」。verify 那步也确实知道，
    field_completeness / sum_arithmetic 都因为它是 None 而转红。
    结果 package 那步把 None 兜成 0.0，产出一份「小计 0.00」。

    「模型没读到这一格」和「这一格真的是 0」是两件事。编成 0 等于替模型
    做了个它没依据的断言，而这份产出是给人**人工对账**用的：有人拿着
    0.00 去票上找，能白找半天；更糟的是它把证据销毁了——
    low_confidence_fields 说小计没把握，产出却把 0.00 摆在正中间。
    """

    PARSED = {
        "merchant_name": "杭州盒马NB", "receipt_no": "002026-10-07-174-01",
        "receipt_date": "2026-10-07", "receipt_time": "17:53",
        "subtotal": None, "tax": -0.18, "total": 28.62,
        "items": [
            {"name": "李锦记薄盐生抽", "qty": 2, "unit_price": 14.9, "amount": 28.8},
            {"name": "巴马铂泉", "qty": 1, "unit_price": 13.9, "amount": 13.9},
        ],
        "low_confidence_fields": ["subtotal", "receipt_no", "tax"],
    }

    def test_missing_numbers_stay_none(self):
        from boxes.receipt.runner import _simulate_package_receipt

        out = _simulate_package_receipt({"parsed": self.PARSED, "field_completeness": False})
        assert out["subtotal"] is None, "把「没读到」编成了数字"
        # 读到了的照旧，不因为隔壁缺一个就连累
        assert out["tax"] == -0.18
        assert out["total"] == 28.62

    def test_missing_line_numbers_stay_none(self):
        from boxes.receipt.runner import _simulate_package_receipt

        parsed = {**self.PARSED, "items": [{"name": "某商品", "qty": None,
                                            "unit_price": 3.5, "amount": None}]}
        out = _simulate_package_receipt({"parsed": parsed, "field_completeness": False})
        assert out["items"][0]["qty"] is None
        assert out["items"][0]["amount"] is None
        assert out["items"][0]["unit_price"] == 3.5

    def test_a_real_zero_is_still_zero(self):
        """真的 0 就该是 0——别把这道修复写成「所有 null 都变 0」的反面"""
        from boxes.receipt.runner import _simulate_package_receipt

        parsed = {**self.PARSED, "subtotal": 0, "tax": 0, "total": 0}
        out = _simulate_package_receipt({"parsed": parsed})
        assert out["subtotal"] == 0.0
        assert out["subtotal"] is not None

    def test_renderer_shows_dash_not_zero_for_missing(self):
        """产出侧留了 None，渲染侧得显示「—」，不能又变回 0.00"""
        import re

        from kanban.web import HTML_PAGE

        m = re.search(r"const money = \(v\) => .*", HTML_PAGE)
        assert m, "找不到 receipt 视图的金额格式化"
        assert "null" in m.group(0) and "'—'" in m.group(0), (
            "money() 不认 null，空值会又被渲染成 0.00"
        )


class TestPromptKeepsTranscriptionSeparateFromArithmetic:
    """抄 ≠ 算

    实测踩坑（真盒马小票，HEIC）：模型把
    「李锦记薄盐生抽(酿造酱油) **两件装**」名字里的「两」当成了数量，
    数量栏其实印的是 1、金额栏印的是 14.9。整张票其余部分全对
    （14.9 + 13.9 = 28.80 小计，减 0.18 抹零，实付 28.62，账能合上），
    就这一行错了。
    而且模型没把这行标进 low_confidence_fields——它觉得自己抄对了，
    是机械判据发现的。
    """

    def _prompt(self):
        from boxes.receipt.runner import _READ_PROMPT

        return _READ_PROMPT

    def test_prompt_forbids_reading_qty_from_the_spec_string(self):
        """规格里的数字不是数量

        反例用真票上的写法：`500ml*2` 是规格（500ml 的瓶子 ×2），
        数量栏印的是 `x 1`。早先这里写的是「两件装」——真票上根本没这三个字，
        照着一个票上不存在的写法让模型理解，反而误导。
        """
        p = self._prompt()
        assert "500ml*2" in p, "没点名「规格里的数字不是数量」这个坑"
        assert "规格" in p
        assert "两件装" not in p, "反例是编的，真票上写的是 500ml*2"

    def test_prompt_forbids_computing_amount_from_qty_times_price(self):
        """拿 qty × unit_price 去填 amount，这条校验就永远发现不了错

        amount 和 qty × unit_price 本来是该被互相校验的两个独立数字。
        要是模型自己动手算，line_arithmetic 恒为真，判据直接废掉。
        """
        p = self._prompt()
        assert "qty × unit_price" in p or "qty x unit_price" in p

    def test_prompt_still_says_do_not_self_check(self):
        p = self._prompt()
        assert "不要自己" in p and "算术" in p

    def test_arithmetic_check_still_catches_the_two_pack_misread(self):
        """模型旧行为（qty=2 / 金额=28.8）必须仍然被拦下

        prompt 只是把坑说清楚，兜底的判据不能因此松掉。
        """
        from boxes.receipt.runner import _simulate_verify_receipt

        # 票面真值：两件装 SKU，数量 1，金额 14.9 —— 这张是能过的
        good = _simulate_verify_receipt({"parsed": {
            "merchant_name": "杭州盒马NB", "receipt_no": "002026-10-07-174-01",
            "receipt_date": "2026-10-07", "receipt_time": "17:53",
            "subtotal": 28.80, "tax": -0.18, "total": 28.62,
            "items": [
                {"name": "李锦记薄盐生抽(酿造酱油) 两件装", "qty": 1,
                 "unit_price": 14.9, "amount": 14.9},
                {"name": "巴马铂泉 饮用天然泉水500ml*12", "qty": 1,
                 "unit_price": 13.9, "amount": 13.9},
            ],
        }})
        assert good["totals_match"] is True, "票面真值竟然过不了"

        # 模型旧行为：从名字里捡了「两」当数量 —— 必须红
        bad = _simulate_verify_receipt({"parsed": {
            "merchant_name": "杭州盒马NB", "receipt_no": "002026-10-07-174-01",
            "receipt_date": "2026-10-07", "receipt_time": "17:53",
            "subtotal": 28.80, "tax": -0.18, "total": 28.62,
            "items": [
                {"name": "李锦记薄盐生抽(酿造酱油) 两件装", "qty": 2,
                 "unit_price": 14.9, "amount": 28.8},
                {"name": "巴马铂泉 饮用天然泉水500ml*12", "qty": 1,
                 "unit_price": 13.9, "amount": 13.9},
            ],
        }})
        assert bad["line_arithmetic"] is False
        assert bad["totals_match"] is False


class TestRetailPosReceipt:
    """零售 POS 小票不是增值税发票

    实测踩坑（真盒马 NB 小票）：
        商品合计 28.80 / 支付宝优惠 -0.18 / 支付明细 28.62
    票上**没有税**。而原来的公式是「小计 + 税 = 实付」，那张优惠数
    在数据契约里无处安放，模型只能给 null，于是每张打过折的票必然转红。

    这不是「模型没抄好」，是 schema 漏了一个真实存在的业务事实。
    漏字段 = 声明了却拿不到的另一个变体：票上有，盒子里没有。
    """

    # 票面真值（照着真票抄的）
    POS = {
        "merchant_name": "杭州盒马NB拱墅桥西邻里店",
        "receipt_no": "5127745356775056818",
        "receipt_date": "2026-10-07", "receipt_time": "17:54",
        "subtotal": 28.80, "tax": None, "discount": 0.18, "total": 28.62,
        "payment_method": "碰一下支付",
        "items": [
            {"name": "李锦记 薄盐生抽(酿造酱油) 500ml*2", "qty": 1,
             "unit_price": 14.90, "amount": 14.90},
            {"name": "巴马铂泉 饮用天然泉水 500ml*12", "qty": 1,
             "unit_price": 13.90, "amount": 13.90},
        ],
    }

    def _verify(self, parsed):
        from boxes.receipt.runner import _simulate_verify_receipt

        return _simulate_verify_receipt({"parsed": parsed})

    def test_pos_receipt_with_discount_passes_all_rules(self):
        v = self._verify(self.POS)
        assert v["totals_match"] is True, (
            f"零售 POS 小票竟然过不了：{v}"
        )
        assert v["total_arithmetic"] is True

    def test_vat_invoice_without_discount_still_passes(self):
        """另一类票据：增值税发票有税没优惠，公式也得认"""
        v = self._verify({**self.POS, "subtotal": 100.0, "tax": 13.0,
                          "discount": None, "total": 113.0})
        assert v["total_arithmetic"] is True

    def test_discount_is_not_silently_absorbed_into_tax(self):
        """优惠不能塞进税额栏——POS 小票没有税，增值税发票没有支付宝优惠"""
        v = self._verify({**self.POS, "tax": 0.18, "discount": None})
        assert v["total_arithmetic"] is False, (
            "把优惠当成税，公式就又回到「小计+税=实付」那个漏字段的老路"
        )

    def test_schema_declares_discount(self):
        """产出契约里必须有 discount，否则这个字段无处安放"""
        fields = {f.name for f in get_definition().get_schema("schema_receipt_parsed").fields}
        assert "discount" in fields
        assert "tax" in fields, "税也得留着——增值税发票那张还没有"

    def test_packaging_passes_discount_through(self):
        from boxes.receipt.runner import _simulate_package_receipt

        out = _simulate_package_receipt({"parsed": self.POS, **self._verify(self.POS)})
        assert out["discount"] == 0.18
        assert out["tax"] is None, "票上没有税，就该是 None，不该补 0"

    def test_prompt_names_the_discount_concept(self):
        """prompt 里没有「优惠」这个词，模型就不知道 -0.18 该放哪"""
        from boxes.receipt.runner import _READ_PROMPT

        p = _READ_PROMPT
        assert "discount" in p
        for label in ("支付宝优惠", "抹零"):
            assert label in p, f"prompt 没点名「{label}」这类标签"

    def test_prompt_says_pos_receipts_have_no_tax(self):
        """不说清楚，模型会为了凑公式把优惠硬塞进税额栏"""
        from boxes.receipt.runner import _READ_PROMPT

        assert "POS" in _READ_PROMPT and "没有" in _READ_PROMPT

    def test_prompt_uses_the_real_spec_pattern_not_a_made_up_one(self):
        """上一版写的反例「两件装」是编的——真票上写的是 500ml*2

        结论碰巧对（都是「规格里的数字不是数量」），但反例得是真的，
        不然模型照着一个票上不存在的写法去理解，反而误导。
        """
        from boxes.receipt.runner import _READ_PROMPT

        assert "500ml*2" in _READ_PROMPT
        assert "两件装" not in _READ_PROMPT


# ============================================================
# 判据三态：passed / failed / not_applicable
# ============================================================

# 一张餐馆结账单：只印「品项 / 数量 / 金额」，没有单价栏。
# 这就是「判据用不上」的真实样本——不是造出来的边界情况。
_RESTAURANT_BILL = {
    "merchant_name": "老王川菜馆",
    "receipt_no": "A20261011-0031",
    "receipt_date": "2026-10-11",
    "receipt_time": "19:04",
    "items": [
        {"line_no": 1, "name": "水煮鱼", "qty": 1, "unit_price": None, "amount": 58.0},
        {"line_no": 2, "name": "米饭", "qty": 2, "unit_price": None, "amount": 4.0},
    ],
    "subtotal": 62.0, "tax": None, "discount": None, "total": 62.0,
    "payment_method": "微信支付",
}


class TestRuleCanBeNotApplicable:
    """「这张票压根没这一栏」得是个独立结论，不能硬塞进通过/失败

    两边都撒谎：判成「通过」是盖住症状，判成「失败」是把用不上说成做错了。
    餐馆结账单没有单价栏，连着两版都卡在这儿——这不是模型的问题，
    是判据适用性问题。
    """

    def test_a_bill_without_unit_prices_is_not_applicable(self):
        v = verify({"parsed": _RESTAURANT_BILL})
        assert v["line_arithmetic"] == NOT_APPLICABLE

    def test_not_applicable_is_neither_pass_nor_fail(self):
        """n/a 就是 n/a。不能是真值也不能是假值——它两个都不是。"""
        v = verify({"parsed": _RESTAURANT_BILL})["line_arithmetic"]
        assert v is not True and v is not False, "n/a 被压成布尔了，三态又塌回两态"

    def test_the_other_rules_still_run_and_still_judge(self):
        """n/a 只放掉它自己，不许顺带放行别人"""
        v = verify({"parsed": _RESTAURANT_BILL})
        assert v["field_completeness"] is True
        assert v["sum_arithmetic"] is True
        assert v["total_arithmetic"] is True
        assert v["date_wellformed"] is True

    def test_n_a_does_not_drag_the_overall_verdict_down(self):
        """n/a 只表示「这条没拖后腿」，不能反过来把整张票判红"""
        v = verify({"parsed": _RESTAURANT_BILL})
        assert v["totals_match"] is True
        assert isinstance(v["totals_match"], bool), (
            "totals_match 是判据值，必须一直是布尔"
        )

    def test_totals_match_cannot_inherit_a_string_from_any_rule(self):
        """组合方式必须与判据顺序无关

        and 短路返回第一个假值、否则**最后一个操作数**。所以三态判据一旦
        落在链尾，字符串就会以 totals_match 的身份漏出去——下游拿到
        `"n/a" == True` 会是 False，一个「过了」的票被判成没过。
        全链用 `is not False` 就切断了这个顺序依赖：不管谁在链尾，
        出来的永远是布尔。
        """
        src = inspect.getsource(verify)
        tail = src[src.index('"totals_match"'):]
        tail = tail[:tail.index("\n        ),")]
        # 三个可能三态的（此刻只有 line_arithmetic）必须逐条显式判 False；
        # 两个恒布尔的（date_wellformed / time_ok）直接用即可。
        for name in ("line_arithmetic",):
            assert f"{name} is not False" in tail, (
                f"{name} 退回了 and 串联：三态判据在链尾时会漏出字符串"
            )
        for name in ("field_completeness", "sum_arithmetic", "total_arithmetic"):
            assert f"{name} is not False" in tail, (
                f"{name} 也得显式判 False——它哪天也可能变成三态，"
                "而且 and 链的位置依赖是隐形的"
            )
        for name in ("date_wellformed", "time_ok"):
            assert re.search(rf"\b{name}\b(?!\s+is\s+not\s+False)", tail), (
                f"{name} 不见了，总口径漏判一条"
            )

    def test_a_receipt_with_prices_is_still_judged_normally(self):
        """有单价栏的票不受影响——三态不是给所有票开后门"""
        assert verify({"parsed": _parsed()})["line_arithmetic"] is True

    def test_some_rows_priced_some_not_is_a_real_failure(self):
        """有的有、有的没有 → 票上有这栏、模型漏抄 → 真失败，不是 n/a"""
        v = verify({"parsed": _parsed(items=[
            {"line_no": 1, "name": "可乐", "qty": 2, "unit_price": 3.5, "amount": 7.0},
            {"line_no": 2, "name": "面包", "qty": 1, "unit_price": None, "amount": 12.8},
        ], subtotal=19.8, tax=1.19, total=20.99)})
        assert v["line_arithmetic"] is False, (
            "漏抄单价被当成「票上没单价栏」放行了——这是最阴的一种漏判"
        )

    def test_unreadable_rows_are_a_failure_not_a_free_pass(self):
        """items 全是字符串时，不许拿「没读到单价」当不适用糊弄过去

        这一条钉的是 struct_rows 那个前提。垃圾行同样「没有单价」，
        但那说明模型抄砸了，不是票上没这栏——放行等于把失败洗成不适用。
        """
        v = verify({"parsed": _parsed(items=["可乐", "面包"], subtotal=19.8,
                                       tax=1.19, total=20.99)})
        assert v["line_arithmetic"] is False
        assert v["field_completeness"] is False, (
            "items 里全是字符串，还报「必填字段齐全」——那不叫解析出了小票"
        )

    def test_empty_items_do_not_count_as_present(self):
        """空 items 之前靠 len(items) > 0 挡着，换成 any(dict) 后得还在"""
        v = verify({"parsed": _parsed(items=[], subtotal=0.0, tax=None, total=0.0)})
        assert v["field_completeness"] is False
        assert v["line_arithmetic"] == NOT_APPLICABLE, (
            "没行可验就是不适用；但整张票仍因 field_completeness 被拦"
        )
        assert v["totals_match"] is False


class TestPackagingKeepsTheThreeStates:
    def test_packaging_does_not_turn_na_into_pass(self):
        """bool("n/a") 是 True —— 打包那一步会把「用不上」悄悄变成「通过」

        判据本来存在的意义就是「过没过得说清」，
        在最后一步被 bool() 抹平，等于整套白做。
        """
        from boxes.receipt.runner import _simulate_package_receipt

        out = _simulate_package_receipt({"parsed": _RESTAURANT_BILL,
                                         **verify({"parsed": _RESTAURANT_BILL})})
        assert out["line_arithmetic"] == NOT_APPLICABLE, "n/a 在打包时被写成了 True"
        assert out["totals_match"] is True

    def test_a_real_failure_stays_false_through_packaging(self):
        from boxes.receipt.runner import _simulate_package_receipt

        bad = _parsed(items=[{"line_no": 1, "name": "可乐", "qty": 2,
                              "unit_price": 3.5, "amount": 7.5}],
                      subtotal=7.5, tax=0.0, total=7.5)
        out = _simulate_package_receipt({"parsed": bad, **verify({"parsed": bad})})
        assert out["line_arithmetic"] is False


class TestAcceptanceKeepsNAOutOfBothSides:
    """端到端：n/a 既不挡交付，也不进通过名单"""

    def _run(self, monkeypatch, parsed):
        from boxes.receipt import runner as receipt_runner

        monkeypatch.setattr(receipt_runner, "complete_json", lambda *a, **kw: parsed)
        store = TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))
        return trigger_task(
            BOX, "capture_receipt",
            {"photo": "data:image/jpeg;base64," + base64.b64encode(b"x").decode()},
            store,
        )

    def test_a_bill_without_unit_prices_is_accepted(self, monkeypatch):
        r = self._run(monkeypatch, _RESTAURANT_BILL)
        assert r["acceptance_status"] == "accepted", (
            f"没有单价栏的结账单被判不合格：{r['acceptance_detail']}"
        )

    def test_na_is_reported_in_its_own_field(self, monkeypatch):
        r = self._run(monkeypatch, _RESTAURANT_BILL)
        assert r["acceptance_not_applicable"] == ["line_arithmetic"]

    def test_na_is_never_listed_as_passed(self, monkeypatch):
        """「5 条过 + 1 条用不上」说成「6 条全过」就是在撒谎"""
        r = self._run(monkeypatch, _RESTAURANT_BILL)
        assert "line_arithmetic" not in r["acceptance_passed"]
        assert len(r["acceptance_passed"]) == 5, (
            f"6 条判据里 1 条不适用，通过的应该是 5 条：{r['acceptance_passed']}"
        )

    def test_detail_row_carries_its_own_outcome(self, monkeypatch):
        r = self._run(monkeypatch, _RESTAURANT_BILL)
        row = next(d for d in r["acceptance_detail"] if d["metric"] == "line_arithmetic")
        assert row["outcome"] == "not_applicable"
        assert row["not_applicable"] is True
        assert row["passed"] is False, "passed=True 会让看板画成 ✓"
        assert row["actual"] == NOT_APPLICABLE

    def test_a_priced_receipt_reports_no_na_at_all(self, monkeypatch):
        r = self._run(monkeypatch, _parsed())
        assert r["acceptance_not_applicable"] == []
        assert "line_arithmetic" in r["acceptance_passed"]
        assert all(d["outcome"] == "passed" for d in r["acceptance_detail"])

    def test_na_does_not_launder_a_broken_receipt(self, monkeypatch):
        """没单价栏不能变成万能借口：别的判据红照样红"""
        bad = {**_RESTAURANT_BILL, "total": 99.0}   # 小计 62 ≠ 实付 99
        r = self._run(monkeypatch, bad)
        assert r["acceptance_status"] == "rejected"
        failed = {d["metric"] for d in r["acceptance_detail"] if d["outcome"] == "failed"}
        assert "total_arithmetic" in failed
        assert "line_arithmetic" not in failed


class TestRateIgnoresNA:
    """度量层：n/a 分子分母都不算

    算进分母 = 把「用不上」记成「做错了」；算进分子 = 把「没验」记成「验过了」。

    直接测 _rate 这个共享助手：现在没有哪个 COMPUTERS 条目读三态字段
    （totals_match / field_completeness 都是二值的），拿现成指标测会绕开
    真正要守的那段逻辑。
    """

    def _record(self, observed, status="accepted"):
        return {"acceptance_status": status, "observed": observed}

    def test_na_is_dropped_from_both_sides(self):
        recs = [
            self._record({"line_arithmetic": True}),
            self._record({"line_arithmetic": False}),
            self._record({"line_arithmetic": NOT_APPLICABLE}),
        ]
        # 3 条里 1 条不适用 → 分母只剩 2，不是 3；分子只有 1，不是 2
        actual, n = _rate(recs, "line_arithmetic")
        assert actual == 50.0 and n == 2

    def test_all_na_yields_no_sample_rather_than_a_fake_hundred(self):
        recs = [self._record({"line_arithmetic": NOT_APPLICABLE})]
        actual, n = _rate(recs, "line_arithmetic")
        assert actual is None and n == 0, (
            "全是不适用时该是「没样本」(None)，不是 100% 也不是 0%"
        )

    def test_na_is_not_counted_as_a_pass(self):
        recs = [
            self._record({"line_arithmetic": NOT_APPLICABLE}),
            self._record({"line_arithmetic": False}),
        ]
        actual, n = _rate(recs, "line_arithmetic")
        assert actual == 0.0 and n == 1, "n/a 混进分子会把 0% 洗成 50%"

    def test_the_helper_is_the_one_the_box_uses(self):
        """别测一份影子实现——COMPUTERS 里的指标得真走 _rate"""
        assert boxes_receipt_measure._arithmetic_consistency_rate is not None
        recs = [self._record({"totals_match": True, "line_arithmetic": NOT_APPLICABLE})]
        assert COMPUTERS["arithmetic_consistency_rate"](recs) == _rate(recs, "totals_match")


def _one_field_schema(name):
    """只留一个字段的 schema——单字段校验不该被别的字段带偏"""
    fields = get_definition().get_schema("schema_receipt_parsed").fields
    return SchemaDef(id="s", fields=[f for f in fields if f.name == name])


class TestNAIsDeclaredHonestly:
    """声明层：n/a 得在 schema 上说清楚，否则消费方只会当成 boolean"""

    def test_line_arithmetic_is_declared_tristate_not_boolean(self):
        fields = {f.name: f for f in get_definition()
                  .get_schema("schema_receipt_parsed").fields}
        assert fields["line_arithmetic"].type == "boolean_or_na", (
            "还声明成 boolean：manifest 是将来 API 的面，"
            "对着 boolean 读的人会以为它只会是 True/False"
        )
        assert fields["field_completeness"].type == "boolean", (
            "其余判据不该跟着放宽——它们确实是二值的"
        )

    def test_contract_validation_accepts_the_three_states(self):
        from runtime.contract import validate_payload

        line = _one_field_schema("line_arithmetic")
        for value in (True, False, NOT_APPLICABLE):
            assert validate_payload({"line_arithmetic": value}, line) == []
        assert validate_payload({"line_arithmetic": "yes"}, line), (
            "三态类型也得挡住 'yes' 这种随手写的字符串"
        )

    def test_plain_boolean_fields_still_reject_na(self):
        """别为了方便把所有 boolean 都放行 n/a——放松要显式，逐字段来"""
        from runtime.contract import validate_payload

        assert validate_payload(
            {"field_completeness": NOT_APPLICABLE}, _one_field_schema("field_completeness")
        )

    def test_the_definition_writes_down_the_blind_spot(self):
        from boxes.receipt import definition as receipt_definition

        doc = receipt_definition.__doc__ or ""
        assert "n/a" in doc, "三态没写进盒子文档，后来的人还会当成两态"
        assert "不算通过" in doc, (
            "光说三态不够——必须写明 n/a 不算通过，"
            "否则下一个人照样会拿它凑「6/6 全过」"
        )

    def test_the_heuristic_and_its_hole_are_both_written_down(self):
        """判别 n/a 用的是启发式，不是判据——它的盲区必须跟着一起声明"""
        from boxes.receipt.runner import _simulate_verify_receipt

        assert "盲区" in inspect.getsource(_simulate_verify_receipt), (
            "启发式判 n/a 一定有洞（真印了单价栏但每行都漏抄的票会被放行），"
            "这个洞必须写在代码旁边，不能装作没有"
        )


class TestKanbanDoesNotFakeAPass:
    """看板层：n/a 绝不能画成 ✓"""

    def _render_fn(self):
        from kanban.web import HTML_PAGE

        i = HTML_PAGE.index("function renderAcceptance(")
        j = HTML_PAGE.index("\n}\n", i)
        return HTML_PAGE[i:j]

    def test_the_mark_for_na_is_a_dash(self):
        body = self._render_fn()
        assert "const mark = na ? '—' : (passed ? '✓' : '✗');" in body, (
            "n/a 的标记必须是「—」。画成 ✓ 等于拿没验过的冒充验过了"
        )

    def test_the_denominator_counts_only_judged_rules(self):
        body = self._render_fn()
        assert "const nJudged = nOk + (rows.length - nOk - nNa);" in body, (
            "分母把不适用算进去了——6 条里 1 条用不上会说成 5/6，"
            "让人以为还有一条没过"
        )

    def test_the_summary_tells_you_how_many_were_skipped(self):
        body = self._render_fn()
        assert "条不适用" in body

    def test_na_has_its_own_style(self):
        """n/a 行现在没有专属样式时，会渲染成默认白底——看不出它被跳过了"""
        from kanban.web import HTML_PAGE

        assert ".ac-row.na" in HTML_PAGE


# ============================================================
# 行级折扣：折后商品的小票
# ============================================================

# MUJI POS 真票实测：
#     1x498×5.83折=290.00   →  ¥290.0
# 原价 498、折后行金额 290、折扣 5.83 折。三个数都印在票上。
# 498 × 0.583 = 290.334，票面按抹零印 290.00。
_MUJI = {
    "merchant_name": "MUJI",
    "receipt_no": "X5006731012602230111",
    "receipt_date": "2026-02-23",
    "receipt_time": "13:27",
    "items": [
        {"line_no": 1, "name": "不易沾水 羽绒大衣", "qty": 1,
         "unit_price": 498, "amount": 290, "discount_rate": 5.83},
    ],
    "subtotal": 290.0, "tax": None, "discount": 0.0, "total": 290.0,
    "payment_method": "支付宝",
}


class TestLineLevelDiscount:
    """折后商品：1 × 498 ≠ 290 不是错

    这张票以前必然 rejected，而产出全对。同一个病根的第三次：
    把某类票的形状当成所有票的形状。
    """

    def test_the_real_muji_receipt_now_passes(self):
        v = verify({"parsed": _MUJI})
        assert v["line_arithmetic"] is True, (
            f"折后商品被判不合格：{v}（这三个数票上都印着，抄得没错）"
        )
        assert v["totals_match"] is True

    def test_the_discount_is_read_not_derived(self):
        """折扣率必须来自票面，不能从金额倒推

        倒推的话 amount / (qty*price) 恒等于它自己，
        这条判据就成了永远绿的——比没有判据更坏。
        """
        from boxes.receipt.runner import _discount_multiplier

        assert _discount_multiplier(5.83) == 0.583
        assert _discount_multiplier(8) == 0.8, "「8折」= 付 80%"
        assert _discount_multiplier(None) is None

    def test_a_wrong_discount_still_fails(self):
        """折扣抄错必须转红——不然上面那条就成了摆设"""
        v = verify({"parsed": {**_MUJI, "items": [
            {**_MUJI["items"][0], "discount_rate": 7},
        ]}})
        assert v["line_arithmetic"] is False, (
            "折扣抄成 7 折也算过——这条判据就是在查这个"
        )

    def test_a_missing_discount_is_a_failure_not_a_pass(self):
        """票上有折却没抄 → 红。

        不能拿「没有折扣栏」放行：原价和行金额明明对不上，
        没有任何东西解释这个差额，那就是产出有问题。
        """
        v = verify({"parsed": {**_MUJI, "items": [
            {**_MUJI["items"][0], "discount_rate": None},
        ]}})
        assert v["line_arithmetic"] is False

    def test_ten_zhe_is_refused_as_a_made_up_no_discount(self):
        """「10折」= 不打折，票上不会这么印。

        模型拿它当「我没看到折扣」的挡箭牌，就等于把真折扣藏起来了。
        """
        from boxes.receipt.runner import _discount_multiplier

        assert _discount_multiplier(10) is None
        assert _discount_multiplier(0) is None
        assert _discount_multiplier(-1) is None

    def test_rounding_down_to_yuan_is_legal(self):
        """498 × 5.83折 = 290.334，票面印 290.00 —— 抹零，不是算错"""
        exact = {**_MUJI, "items": [{**_MUJI["items"][0], "amount": 290.334}]}
        assert verify({"parsed": exact})["line_arithmetic"] is True

    def test_the_rounding_tolerance_has_an_upper_bound(self):
        """抹零最多抹到元，再往下就是抄错了

        写成「向下取整到元」这条规则，而不是「容差放宽 1 元」——
        后者放行的东西比前者多：289.5 距正确值 290.334 只差 0.83 元，
        但它不是 290.334 的任何一种抹零，是个实打实的误读。
        """
        misread = {**_MUJI, "items": [{**_MUJI["items"][0], "amount": 289.5}]}
        assert verify({"parsed": misread})["line_arithmetic"] is False, (
            "金额读错不到 1 元就被放行了——容差变成了没人敢动的橡皮"
        )

    def test_a_rounding_further_than_a_yuan_is_rejected(self):
        far = {**_MUJI, "items": [{**_MUJI["items"][0], "amount": 289.0}]}
        assert verify({"parsed": far})["line_arithmetic"] is False

    def test_a_full_price_line_still_needs_no_discount_field(self):
        """没折扣的普通行照旧按原价验，不能被折扣逻辑误伤"""
        assert verify({"parsed": _parsed()})["line_arithmetic"] is True

    def test_only_the_discounted_line_may_skip_the_original_price(self):
        """一张票里有的打折有的全价，两种都得判对"""
        parsed = _parsed(items=[
            {"line_no": 1, "name": "大衣", "qty": 1,
             "unit_price": 498, "amount": 290, "discount_rate": 5.83},
            {"line_no": 2, "name": "袜子", "qty": 2,
             "unit_price": 15.0, "amount": 30.0, "discount_rate": None},
        ], subtotal=320.0, tax=None, discount=0.0, total=320.0)
        assert verify({"parsed": parsed})["line_arithmetic"] is True

    def test_packaging_carries_the_discount_out(self):
        """折扣得进产出——不然人工对账看到 498 → 290 会一头雾水"""
        from boxes.receipt.runner import _simulate_package_receipt

        v = verify({"parsed": _MUJI})
        out = _simulate_package_receipt({"parsed": _MUJI, **v})
        assert out["items"][0]["discount_rate"] == 5.83
        assert out["items"][0]["unit_price"] == 498.0, (
            "原价也得留着：498 和 290 都对，差的那部分靠折扣解释"
        )

    def test_a_line_without_discount_ships_as_null_not_zero(self):
        from boxes.receipt.runner import _simulate_package_receipt

        v = verify({"parsed": _parsed()})
        out = _simulate_package_receipt({"parsed": _parsed(), **v})
        assert out["items"][0]["discount_rate"] is None, (
            "没有折扣是 null，不是 0——0 是个票上根本不存在的折数"
        )


class TestDiscountIsDeclared:
    def test_the_item_schema_declares_the_field(self):
        """schema 里没有 discount_rate，判据就没地方拿折扣"""
        fields = {f.name for f in get_definition().get_schema("schema_receipt_item").fields}
        assert "discount_rate" in fields

    def test_the_schema_says_it_is_the_printed_number_not_a_ratio(self):
        f = next(x for x in get_definition().get_schema("schema_receipt_item").fields
                 if x.name == "discount_rate")
        assert "5.83" in (f.description or ""), (
            "得说清存的是折数不是比例，否则模型会填 0.583"
        )

    def test_the_prompt_names_the_real_pattern(self):
        from boxes.receipt.runner import _READ_PROMPT

        assert "5.83折" in _READ_PROMPT, (
            "prompt 里没有真实折扣写法，模型不知道该抄什么"
        )

    def test_the_prompt_forbids_backfilling_the_original_price(self):
        """最阴的一种作弊：模型看着 1×498≠290，把 unit_price 改成 290

        这么一改折扣就凭空消失，判据全绿，而票上明明印着 5.83 折。
        """
        from boxes.receipt.runner import _READ_PROMPT

        assert "别这么干" in _READ_PROMPT or "不要这么干" in _READ_PROMPT

    def test_the_prompt_says_null_not_ten(self):
        from boxes.receipt.runner import _READ_PROMPT

        assert "10" in _READ_PROMPT, "没告诉模型「不打折填 null 而不是 10」"

    def test_the_definition_explains_why_the_old_form_was_wrong(self):
        from boxes.receipt import definition as receipt_definition

        doc = receipt_definition.__doc__ or ""
        assert "5.83折" in doc, "折后票这个坑没写进盒子文档"
        assert "抹零" in doc, (
            "抹零容差是业务事实，必须跟着声明；不写就成了没人敢动的橡皮"
        )
