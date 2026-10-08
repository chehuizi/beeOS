"""tests.test_metrics - 盒子声明的指标是不是真在跑

这条链要成立，四个环节都得通：
    definition.metrics 声明 → 每次履约落盘实测信号 → 按窗口聚合 → 看板渲染

以前只做得到第一环：5 个指标写得挺漂亮，看板一个字不显示。
这组测试盯的是第二三四环，以及两头都不漏：
「声明了却没人采集」和「采集了却没声明」是同一族病，两个方向都测。

聚合算法用构造记录验证真实数值——只测「有没有接上」的话，
把 actual 写死成 0.0 也能过。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from boxes.modeling import get_definition as get_modeling_definition
from boxes.modeling.measure import COMPUTERS as MODEL_COMPUTERS
from boxes.requirement_capture import get_definition as get_capture_definition
from boxes.requirement_capture.measure import COMPUTERS as CAPTURE_COMPUTERS
from core import metrics as m
from kanban.trigger import METRIC_COMPUTERS, box_metrics_readings, trigger_task
from kanban.web import HTML_PAGE
from runtime.store import TaskRunStore
from tests.conftest import stub_compliant_llm


CAPTURE = "requirement_capture_box"
MODELING = "business_modeling_box"

NARRATIVE = (
    "wms的流程有入库流程、出库流程、盘点流程。\n"
    "出库要先进先出拣货。\n"
    "库存低于安全库存时触发补货。"
)


def _rec(created_at, *, status="completed", acc="accepted", observed=None, error_kind=None, ms=3000):
    return {
        "created_at": created_at,
        "status": status,
        "acceptance_status": acc,
        "observed": observed or {},
        "error_kind": error_kind,
        "duration_ms": ms,
    }


def _capture_rec(i, *, hallucination=False, acc="accepted", status="completed", ms=3000):
    return _rec(
        f"2026-10-0{i}T00:00:00",
        status=status,
        acc=acc,
        observed={
            "contract_compliance": True,
            "source_coverage": 100.0,
            "trace_integrity": True,
            "no_hallucination": not hallucination,
        },
        ms=ms,
    )


# ============================================================
# 两头都不漏：声明 ↔ 采集算法 一一对应
# ============================================================


class TestWiring:
    @pytest.mark.parametrize(
        "getter, computers",
        [(get_capture_definition, CAPTURE_COMPUTERS), (get_modeling_definition, MODEL_COMPUTERS)],
    )
    def test_no_metric_is_declared_without_a_collector(self, getter, computers):
        assert m.unwired(getter(), computers) == []

    @pytest.mark.parametrize(
        "getter, computers",
        [(get_capture_definition, CAPTURE_COMPUTERS), (get_modeling_definition, MODEL_COMPUTERS)],
    )
    def test_no_collector_is_missing_from_the_declaration(self, getter, computers):
        """反向：算法表里躺着的名字，definition 忘了写就永远上不了看板"""
        assert m.orphan_computers(getter(), computers) == []

    def test_every_registered_box_has_computers(self):
        from kanban.trigger import BOX_REGISTRY

        assert set(BOX_REGISTRY) == set(METRIC_COMPUTERS)

    def test_abandoned_metrics_are_gone_from_the_declaration(self):
        """算不出数的指标不该还挂在 definition 上

        manual_rewrite_rate 没有人工改写事件，model_reuse_rate / cost_per_modeling_request
        / human_escalation_rate 没有信号源。留着它们就是在看板上摆永远填不满的洞。
        """
        names = {x.name for x in m.declared(get_capture_definition())}
        names |= {x.name for x in m.declared(get_modeling_definition())}
        assert names.isdisjoint({
            "manual_rewrite_rate",
            "model_reuse_rate",
            "cost_per_modeling_request",
            "human_escalation_rate",
        })

    def test_queen_no_longer_observes_an_unmeasurable_metric(self):
        """queen.observe 引用一个算不出来的指标，等于让它盯一个不存在的数"""
        d = get_capture_definition()
        observed = d.queen.rules.continuous_improvement["observe"]
        assert "manual_rewrite_rate" not in observed


# ============================================================
# 聚合算得对不对
# ============================================================


class TestAggregation:
    def _reading(self, records, name):
        out = box_metrics_readings(CAPTURE, records)
        return next(r for r in out if r["name"] == name)

    def test_extraction_precision_counts_the_hallucination_runs(self):
        records = [_capture_rec(1), _capture_rec(2, hallucination=True), _capture_rec(3)]
        r = self._reading(records, "extraction_precision")
        assert r["actual"] == pytest.approx(66.7, abs=0.1)
        assert r["sample_size"] == 3
        assert r["unit"] == "pct"

    def test_extraction_recall_averages_source_coverage(self):
        records = [
            _capture_rec(1),
            {**_capture_rec(2), "observed": {"source_coverage": 80.0}},
        ]
        assert self._reading(records, "extraction_recall")["actual"] == pytest.approx(90.0)

    def test_first_pass_rate_uses_acceptance_not_completion(self):
        """跑完了但被验收闸打回的，算首次没通过——不能按 status 算"""
        records = [
            _capture_rec(1, acc="accepted"),
            _capture_rec(2, acc="rejected"),
            _capture_rec(3, acc="accepted"),
        ]
        r = self._reading(records, "first_pass_capture_rate")
        assert r["actual"] == pytest.approx(66.7, abs=0.1)

    def test_llm_failure_rate_denominator_includes_aborted_runs(self):
        """分母只算跑完的，会把「挂了」从失败率里洗掉"""
        records = [
            _capture_rec(1),
            _rec("2026-10-02T00:00:00", status="failed", acc=None, error_kind="llm_call"),
            _rec("2026-10-03T00:00:00", status="failed", acc=None, error_kind="op_error"),
        ]
        r = self._reading(records, "llm_call_failure_rate")
        assert r["actual"] == pytest.approx(33.3, abs=0.1)
        assert r["sample_size"] == 3

    def test_latency_only_counts_finished_runs(self):
        records = [
            {**_capture_rec(1), "duration_ms": 3000},
            {**_capture_rec(2), "duration_ms": 5000},
            _rec("2026-10-03T00:00:00", status="failed", acc=None, ms=0),
        ]
        r = self._reading(records, "capture_latency")
        assert r["actual"] == pytest.approx(4.0)
        assert r["unit"] == "s"

    def test_window_keeps_the_most_recent_runs(self):
        records = [{**_capture_rec(i), "duration_ms": i * 1000} for i in range(1, 9)]
        r = self._reading(records, "capture_latency")
        # 窗口 20 > 样本 8，全取；均值 1..8 = 4.5 秒
        assert r["actual"] == pytest.approx(4.5)
        assert r["window"] == m.WINDOW

    def test_p95_percentile_of_modeling_latency(self):
        recs = [
            {"created_at": f"2026-10-01T00:00:{i:02d}", "status": "completed",
             "acceptance_status": "accepted", "observed": {}, "error_kind": None,
             "duration_ms": sec * 1000}
            for i, sec in enumerate([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], start=1)
        ]
        out = box_metrics_readings(MODELING, recs)
        p95 = next(r for r in out if r["name"] == "p95_modeling_turnaround_time")
        assert p95["actual"] == pytest.approx(95.5, abs=0.5)
        assert p95["unit"] == "s"

    def test_unmeasured_is_none_not_zero(self):
        """还没测过和测出来是 0 是两回事，看板上必须长得不一样"""
        out = box_metrics_readings(CAPTURE, [])
        assert out, "声明了的指标即使没数据也该出现在读数里（actual=None）"
        assert all(r["actual"] is None for r in out)
        assert all(r["sample_size"] == 0 for r in out)

    def test_one_run_can_feed_some_metrics_and_not_others(self):
        """同一次履约可以喂饱判据类指标却喂不饱延迟类指标

        duration_ms 是毫秒取整：跑得比 1ms 还快的履约会落成 0。
        0 不是「零延迟」，是「没测到时间」，该被排除而不是当样本算进去。
        """
        rec = _rec("2026-10-01T00:00:00", observed={
            "contract_compliance": True,
            "source_coverage": 100.0,
            "trace_integrity": True,
            "no_hallucination": True,
        }, ms=0)
        by_name = {r["name"]: r for r in box_metrics_readings(CAPTURE, [rec])}
        assert by_name["extraction_precision"]["actual"] == pytest.approx(100.0)
        assert by_name["capture_latency"]["actual"] is None
        assert by_name["capture_latency"]["sample_size"] == 0


# ============================================================
# 端到端：履约 → 落盘 → 聚合
# ============================================================


class TestEndToEnd:
    @pytest.fixture
    def store(self):
        return TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))

    def _stub_ok(self, monkeypatch):
        from runtime import llm

        monkeypatch.setattr(llm, "complete_json", stub_compliant_llm)

    def test_a_real_run_persists_measured_values(self, store, monkeypatch):
        """真跑一次：判据实测值必须落进记录，否则指标永远没数据源"""
        self._stub_ok(monkeypatch)
        trigger_task(CAPTURE, "capture_business_requirement", {"narrative": NARRATIVE}, store)

        rec = store.list_recent(limit=1)[0]
        assert rec["observed"], "observed 没落盘"
        assert rec["observed"]["source_coverage"] == 100.0
        assert rec["observed"]["no_hallucination"] is True
        assert rec["error_kind"] is None
        # 不断言 duration_ms > 0：stub 跑得比 1ms 还快时会落成 0，
        # 那是毫秒取整的真实结果，不是没计时。延迟聚合滤掉 0 另有测试。
        assert rec["duration_ms"] >= 0

        r = next(x for x in box_metrics_readings(CAPTURE, store._cache)
                 if x["name"] == "extraction_recall")
        assert r["actual"] == pytest.approx(100.0)
        assert r["sample_size"] == 1

    def test_llm_failure_is_attributed_and_counted(self, store):
        """LLM 挂了就该被记成 llm_call，不能混进 op_error 里稀释掉"""
        from runtime import llm

        llm.complete_json = lambda *a, **kw: (_ for _ in ()).throw(llm.LLMError("boom"))
        try:
            trigger_task(CAPTURE, "capture_business_requirement", {"narrative": NARRATIVE}, store)
        except Exception:
            pass

        rec = store.list_recent(limit=1)[0]
        assert rec["status"] == "failed"
        assert rec["error_kind"] == "llm_call"
        assert rec["observed"] == {}, "中止的 run 不该有实测值"

        r = next(x for x in box_metrics_readings(CAPTURE, store._cache)
                 if x["name"] == "llm_call_failure_rate")
        assert r["actual"] == pytest.approx(100.0)

    def test_two_runs_aggregate_over_both(self, store, monkeypatch):
        self._stub_ok(monkeypatch)
        for _ in range(2):
            trigger_task(CAPTURE, "capture_business_requirement", {"narrative": NARRATIVE}, store)
        r = next(x for x in box_metrics_readings(CAPTURE, store._cache)
                 if x["name"] == "first_pass_capture_rate")
        assert r["sample_size"] == 2
        assert r["actual"] in (0.0, 100.0)


class TestDirection:
    """达标方向是声明，不是从 unit 猜出来的"""

    def test_a_low_failure_rate_reads_as_meeting_target(self):
        """失败率越低越好，方向必须由盒子声明而不是从 unit 猜

        pct 里既有越高越好（达成率）也有越低越好（失败率）。
        从单位猜方向会让「失败率 0%，目标 2%」被画成没达标——
        那是把最好的一种结果标成问题。
        """
        readings = {r["name"]: r for r in box_metrics_readings(CAPTURE, [_capture_rec(1)])}
        f = readings["llm_call_failure_rate"]
        assert f["direction"] == "lower_is_better"
        assert f["actual"] == 0.0 and f["target"] == 2.0
        # 达标判定在渲染层，但方向必须从读数里拿得到
        assert "m.direction === 'lower_is_better'" in HTML_PAGE

    def test_latency_metrics_declare_lower_is_better(self):
        for r in box_metrics_readings(CAPTURE, []):
            if r["unit"] == "s":
                assert r["direction"] == "lower_is_better", r["name"]
        for r in box_metrics_readings(MODELING, []):
            if r["unit"] == "s":
                assert r["direction"] == "lower_is_better", r["name"]

    def test_rates_and_precisions_declare_higher_is_better(self):
        """达成率类越高越好——这里反向断言，防止默认值被误用到失败率上"""
        names = {"extraction_precision", "extraction_recall", "first_pass_capture_rate"}
        readings = {r["name"]: r for r in box_metrics_readings(CAPTURE, [])}
        assert {n for n in names if readings[n]["direction"] == "higher_is_better"} == names


# ============================================================
# 看板渲染
# ============================================================


class TestKanbanRendering:
    def test_html_carries_the_metrics_renderer(self):
        assert "renderBoxMetrics" in HTML_PAGE
        assert "ctx-metrics" in HTML_PAGE

    def test_unmeasured_renders_as_untested_not_zero(self):
        """actual 为 None 画成「未测」——画成 0 就是谎报质量"""
        assert "未测" in HTML_PAGE

    def test_latency_target_is_rendered_in_human_units(self):
        """target 常是 3600 / 7200 秒，直接显示 3600s 没人读得下去"""
        assert "'h'" in HTML_PAGE or ".toFixed(1) + 'h'" in HTML_PAGE