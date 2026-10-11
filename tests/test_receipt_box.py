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
import tempfile
from pathlib import Path

import pytest

from boxes.receipt import get_definition
from boxes.receipt.measure import COMPUTERS
from boxes.receipt.runner import _simulate_verify_receipt as verify
from core import metrics as m
from core.beeline_models import NextRef
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
        assert "image/heic" not in photo.accepted_media_types()
        assert "image/jpeg" in photo.accepted_media_types()

        mf = box_manifest(BOX)["tasks"][0]["media_fields"][0]
        assert mf["name"] == "photo"
        assert mf["accept"] == photo.accepted_media_types()

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
