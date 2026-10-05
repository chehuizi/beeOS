"""tests.test_llm_structurer - LLM 结构化 + 五道机械检查

不 mock LLM：只测机械部分（清洗、解析、五道检查、降级）。
真实模型调用在手工验证里做（成本 + 不稳定），不进 CI。

覆盖：
- runtime/llm.py：剥 think 段 / 剥 markdown 围栏 / 平衡括号兜底提取
- kanban/trigger.py：幻觉检查 / id 规范化 / 降级链路
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kanban.trigger import (
    _no_hallucination,
    _normalize_requirement_ids,
    structure_business_text,
    structure_business_text_with_llm,
)
from runtime.llm import LLMError, extract_json, strip_noise


# ============================================================
# 1. 输出清洗与 JSON 提取
# ============================================================


class TestOutputCleaning:
    def test_strip_think_block(self):
        raw = "<think>让我想想…</think>\n{\"a\": 1}"
        assert strip_noise(raw) == '{"a": 1}'

    def test_strip_markdown_fence(self):
        raw = "```json\n{\"a\": 1}\n```"
        assert strip_noise(raw) == '{"a": 1}'

    def test_strip_bare_fence_without_language(self):
        raw = "```\n{\"a\": 1}\n```"
        assert strip_noise(raw) == '{"a": 1}'

    def test_extract_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_extract_from_think_and_fence(self):
        raw = '<think>分析中</think>\n```json\n{"set_id": "s", "requirements": []}\n```'
        assert extract_json(raw) == {"set_id": "s", "requirements": []}

    def test_extract_with_surrounding_prose(self):
        raw = '好的，结果如下：\n{"a": 1}\n希望有帮助。'
        assert extract_json(raw) == {"a": 1}

    def test_extract_handles_braces_inside_strings(self):
        raw = 'note: {"desc": "含 } 大括号", "n": 2} done'
        assert extract_json(raw) == {"desc": "含 } 大括号", "n": 2}

    def test_extract_raises_on_non_json(self):
        with pytest.raises(LLMError):
            extract_json("完全不是 JSON 的一段话")


# ============================================================
# 2. 幻觉检查
# ============================================================


class TestNoHallucination:
    def test_description_found_in_source_passes(self):
        p = {"requirements": [
            {"requirement_id": "r1", "description": "库存数量不能为负"},
        ]}
        assert _no_hallucination(p, "库存数量不能为负。") == []

    def test_invented_requirement_rejected(self):
        p = {"requirements": [
            {"requirement_id": "r1", "description": "订单必须7天内退款"},
        ]}
        errs = _no_hallucination(p, "库存数量不能为负。")
        assert len(errs) == 1
        assert "hallucination" in errs[0]

    def test_whitespace_insensitive(self):
        """中文里空格无意义，'同一 个 SKU' 应能匹配 '同一个SKU'"""
        p = {"requirements": [
            {"requirement_id": "r1", "description": "同一个 SKU 只能有一个批次"},
        ]}
        assert _no_hallucination(p, "同一个SKU只能有一个批次。") == []

    def test_rewritten_goal_detected(self):
        """实测 LLM 会把 'wms的流程有…' 改写成 'WMS包含…' —— 幻觉检查要能抓"""
        p = {"requirements": [
            {"requirement_id": "r1", "description": "WMS包含入库流程"},
        ]}
        assert _no_hallucination(p, "wms的流程有入库流程")

    def test_no_requirements_is_clean(self):
        assert _no_hallucination({"requirements": []}, "任意原文") == []


# ============================================================
# 3. requirement_id 规范化
# ============================================================


class TestNormalizeIds:
    def test_grouped_by_type(self):
        p = {"requirements": [
            {"requirement_id": "req_001_1", "requirement_type": "rule"},
            {"requirement_id": "req_001_2", "requirement_type": "rule"},
            {"requirement_id": "x", "requirement_type": "process"},
        ]}
        changed = _normalize_requirement_ids(p)
        assert [r["requirement_id"] for r in p["requirements"]] == [
            "req_rule_1", "req_rule_2", "req_proc_1",
        ]
        assert len(changed) == 3

    def test_already_correct_is_noop(self):
        p = {"requirements": [
            {"requirement_id": "req_rule_1", "requirement_type": "rule"},
        ]}
        assert _normalize_requirement_ids(p) == []

    def test_does_not_touch_other_fields(self):
        p = {"requirements": [
            {"requirement_id": "bad", "description": "库存不能为负",
             "requirement_type": "rule", "priority": "must_have"},
        ]}
        _normalize_requirement_ids(p)
        r = p["requirements"][0]
        assert r["description"] == "库存不能为负"
        assert r["priority"] == "must_have"

    def test_unknown_type_falls_back_to_obj_prefix(self):
        p = {"requirements": [
            {"requirement_id": "a", "requirement_type": "weird_type"},
        ]}
        _normalize_requirement_ids(p)
        assert p["requirements"][0]["requirement_id"] == "req_obj_1"


# ============================================================
# 4. 降级链路（LLM 不可用时必须还能产出合规投料）
# ============================================================


class TestFallback:
    def test_llm_failure_degrades_to_rule(self, monkeypatch):
        """LLM 不可用（端点错/超时）时不能整个投料口挂掉"""
        from runtime import llm

        def boom(*a, **kw):
            raise llm.LLMError("simulated outage")

        monkeypatch.setattr(llm, "complete_json", boom)
        r = structure_business_text_with_llm("wms的流程有入库流程、出库流程。")
        assert r["_extractor"] == "rule"
        assert "llm unavailable" in r["_extractor_note"]
        assert len(r["requirements"]) == 2

    def test_contract_violation_degrades(self, monkeypatch):
        from runtime import llm
        monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: {
            "set_id": "s", "business_goal": "g",
            "requirements": [{"requirement_id": "r1", "description": "d",
                              "requirement_type": "POLICY"}],
        })
        r = structure_business_text_with_llm("库存数量不能为负。")
        assert r["_extractor"] == "rule"
        assert "task_schema" in r["_extractor_note"]

    def test_hallucination_degrades(self, monkeypatch):
        from runtime import llm
        monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: {
            "set_id": "s", "business_goal": "g",
            "requirements": [{"requirement_id": "r1", "description": "订单必须7天内退款",
                              "requirement_type": "rule", "priority": "must_have"}],
        })
        r = structure_business_text_with_llm("库存数量不能为负。")
        assert r["_extractor"] == "rule"
        assert "hallucination" in r["_extractor_note"]

    def test_zero_requirements_degrades(self, monkeypatch):
        """模型说抽不出来时，规则版再试一次——两条路都空才算真空"""
        from runtime import llm
        monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: {
            "set_id": "s", "business_goal": "g", "requirements": [],
        })
        r = structure_business_text_with_llm("wms的流程有入库流程、出库流程。")
        assert r["_extractor"] == "rule"
        assert len(r["requirements"]) == 2

    def test_non_dict_output_degrades(self, monkeypatch):
        from runtime import llm
        monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: ["a", "list"])
        r = structure_business_text_with_llm("wms的流程有入库流程。")
        assert r["_extractor"] == "rule"

    @pytest.mark.parametrize("text", [
        "库存数量不能为负。",
        "wms的流程有入库流程、出库流程、盘点流程",
        "",
    ])
    def test_every_path_produces_contract_valid_payload(self, text, monkeypatch):
        """无论走哪条路，产出都必须过结构契约——投料口下游不会再因结构被拒"""
        from runtime.contract import validate_payload
        from boxes.modeling import get_definition
        from runtime import llm

        schema = get_definition().get_schema("schema_business_requirement_set")
        for stub in (lambda *a, **kw: {"boom": 1},
                     lambda *a, **kw: (_ for _ in ()).throw(llm.LLMError("x"))):
            monkeypatch.setattr(llm, "complete_json", stub)
            p = structure_business_text_with_llm(text)
            assert validate_payload(p, schema) == [], f"契约不合法: {p}"

    def test_rule_version_unchanged_and_has_no_extractor_keys(self):
        """规则版保持原样，不带 _extractor 等内部标记"""
        p = structure_business_text("wms的流程有入库流程、出库流程。")
        assert set(p) == {"set_id", "business_goal", "requirements"}


# ============================================================
# 凭证解析：占位符预检 + env 覆盖
# ============================================================


class TestCredentialResolution:
    """~/.minimax/config.yaml 是 MCode runtime 拥有的文件，会被重写

    所以不能假设里面的 apiKey 一直有效——要么预检出占位符给出可操作的错，
    要么用环境变量覆盖，不能让 LLM 腿静默断掉。
    """

    def test_placeholder_detected(self):
        from runtime.llm import _is_placeholder
        for bad in ("sk-xxx", "XXX", "your-api-key", "changeme", "placeholder", "", "   "):
            assert _is_placeholder(bad), f"{bad!r} 应判为占位符"

    def test_real_looking_key_not_flagged(self):
        from runtime.llm import _is_placeholder
        assert not _is_placeholder("sk-abc123def456ghi789jkl")
        assert not _is_placeholder("eyJhbGciOiJIUzI1NiJ9.payload.sig")

    def test_placeholder_raises_actionable_error(self, monkeypatch):
        """配置里是假 key 时，错误要说清怎么修，而不是甩一个 401"""
        from runtime import llm
        monkeypatch.delenv(llm._ENV_KEY, raising=False)
        monkeypatch.delenv(llm._ENV_BASE_URL, raising=False)
        monkeypatch.setattr(llm, "_CONFIG_PATH", _tmp_config({"apiKey": "sk-xxx"}))
        with pytest.raises(llm.LLMError) as ei:
            llm.complete("hi", timeout=5)
        msg = str(ei.value)
        assert "no usable LLM credential" in msg
        assert llm._ENV_KEY in msg, "错误里要给出可操作的修法"

    def test_real_key_passes_preflight(self, monkeypatch):
        """真 key 通过预检（只验解析，不发网络请求）"""
        from runtime import llm
        monkeypatch.delenv(llm._ENV_KEY, raising=False)
        monkeypatch.delenv(llm._ENV_BASE_URL, raising=False)
        path = _tmp_config({"apiKey": "sk-realkey123456"})
        monkeypatch.setattr(llm, "_CONFIG_PATH", path)
        resolved, tmpdir = llm._resolve_config_path()
        assert resolved == path
        assert tmpdir is None, "没有 env 覆盖时不该写临时 config"

    def test_env_key_writes_derived_config_and_cleans_up(self, monkeypatch):
        """env 覆盖：派生 config 写到 0600 临时文件，用完即删"""
        import os
        from runtime import llm
        monkeypatch.setenv(llm._ENV_KEY, "sk-from-env-999")
        monkeypatch.setenv(llm._ENV_BASE_URL, "https://example.invalid/v1")
        monkeypatch.setattr(llm, "_CONFIG_PATH", _tmp_config({"apiKey": "sk-xxx"}))
        resolved, tmpdir = llm._resolve_config_path()
        try:
            assert tmpdir is not None
            assert resolved != llm._CONFIG_PATH
            assert oct(os.stat(resolved).st_mode)[-3:] == "600"
            import yaml
            opts = yaml.safe_load(resolved.read_text())["provider"]["minimax"]["options"]
            assert opts["apiKey"] == "sk-from-env-999"
            assert opts["baseURL"] == "https://example.invalid/v1"
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        assert not os.path.exists(tmpdir), "临时 config 必须清理"

    def test_env_empty_placeholder_rejected(self, monkeypatch):
        from runtime import llm
        monkeypatch.setenv(llm._ENV_KEY, "sk-xxx")
        monkeypatch.setattr(llm, "_CONFIG_PATH", _tmp_config({"apiKey": "sk-realkey"}))
        with pytest.raises(llm.LLMError):
            llm._resolve_config_path()

    def test_npm_protocol_override(self, monkeypatch):
        """协议覆盖：@ai-sdk/openai 决定 /chat/completions + Bearer，配错就是 401"""
        import shutil
        import yaml
        from runtime import llm
        monkeypatch.setenv(llm._ENV_KEY, "sk-realkey-abc")
        monkeypatch.setenv(llm._ENV_NPM, "@ai-sdk/openai")
        monkeypatch.setattr(llm, "_CONFIG_PATH", _tmp_config({"apiKey": "sk-xxx"}))
        resolved, tmpdir = llm._resolve_config_path()
        try:
            minimax = yaml.safe_load(resolved.read_text())["provider"]["minimax"]
            assert minimax["npm"] == "@ai-sdk/openai"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_npm_only_override_still_writes_config(self, monkeypatch):
        """只覆盖协议时也要走派生 config（不能当成"无需覆盖"而跳过）"""
        import shutil
        import yaml
        from runtime import llm
        monkeypatch.delenv(llm._ENV_KEY, raising=False)
        monkeypatch.delenv(llm._ENV_BASE_URL, raising=False)
        monkeypatch.setenv(llm._ENV_NPM, "@ai-sdk/openai")
        monkeypatch.setattr(llm, "_CONFIG_PATH", _tmp_config({"apiKey": "sk-realkey-abc"}))
        resolved, tmpdir = llm._resolve_config_path()
        try:
            assert tmpdir is not None
            minimax = yaml.safe_load(resolved.read_text())["provider"]["minimax"]
            assert minimax["npm"] == "@ai-sdk/openai"
            assert minimax["options"]["apiKey"] == "sk-realkey-abc"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

def _tmp_config(options: dict) -> Path:
    import tempfile
    import yaml
    d = Path(tempfile.mkdtemp(prefix="beeos-test-cfg-"))
    (d / "config.yaml").write_text(
        yaml.safe_dump({"provider": {"minimax": {"options": options}}})
    )
    return d / "config.yaml"
