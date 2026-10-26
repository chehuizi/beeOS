"""tests.test_requirement_document - 需求文档投影的守卫

需求盒多了一层「人读的需求文档」。这一层最大的风险不是画得不好看，
是**它偷偷变成了第二份事实**——文档里出现一句原文里没有的话，
读文档的人就以为业务方真这么说过。

所以这里的守卫盯的都是**不新增事实**：

- 每个条目的描述必须**逐字**出现在原文句里
- 每个条目必须能指回流程图里的一个元素
- 条目数 = 图里能派生的条目数（不多不少）
- 追溯矩阵覆盖每一句原文
- markdown 里不许静默丢条目
- 验收标准只能是占位（需求盒不定「怎么算满足」）
- 实体不许单独成条（那是平铺投影的碎片，文档不该继承）
"""

from __future__ import annotations

import pytest

from boxes.requirement_capture.document import (
    ACCEPTANCE_PLACEHOLDER,
    TYPE_CONSTRAINT,
    TYPE_EVENT,
    TYPE_METRIC,
    TYPE_STEP,
    build_document,
)


# ============================================================
# 夹具：一段真实的抽取结果（图 + 原文句 + 平铺列表）
# ============================================================


SENTENCES = [
    {"sentence_id": "sent_1", "text": "拣货准确率要能在库存看板看得到", "role": "goal"},
    {"sentence_id": "sent_2", "text": "出库要按先进先出", "role": "fact"},
    {"sentence_id": "sent_3", "text": "库存要够才能出库", "role": "fact"},
    {"sentence_id": "sent_0", "text": "库存管理", "role": "title"},
]

NODES = [
    {
        "node_id": "node_1", "action": "拣货准确率要能在库存看板看得到",
        "guard": "", "writes": [], "reads": [], "measures": ["拣货准确率"],
        "guard_sources": [], "measure_sources": ["sent_1"], "trace_to": "sent_1",
    },
    {
        "node_id": "node_2", "action": "出库要按先进先出",
        "guard": "", "writes": ["出库单"], "reads": [], "measures": [],
        "guard_sources": [], "measure_sources": [], "trace_to": "sent_2",
    },
]

EDGES = [
    {"from": "node_1", "to": "node_2", "guard": "库存要够", "trigger": "",
     "trace_to": "sent_3"},
]

REQUIREMENTS = [
    {"requirement_id": "req_proc_1", "description": "拣货准确率要能在库存看板看得到",
     "requirement_type": "process", "trace_to": "sent_1", "priority": "must_have"},
    {"requirement_id": "req_met_1", "description": "拣货准确率",
     "requirement_type": "metric", "trace_to": "sent_1", "priority": "must_have"},
    {"requirement_id": "req_proc_2", "description": "出库要按先进先出",
     "requirement_type": "process", "trace_to": "sent_2", "priority": "must_have"},
    {"requirement_id": "req_obj_1", "description": "出库单",
     "requirement_type": "object", "trace_to": "sent_2", "priority": "must_have"},
    {"requirement_id": "req_rule_1", "description": "库存要够",
     "requirement_type": "rule", "trace_to": "sent_3", "priority": "must_have"},
]


@pytest.fixture()
def doc():
    return build_document(
        set_id="req_set_1",
        goal="拣货准确率要能在库存看板看得到",
        context="仓储现场",
        sentences=SENTENCES,
        nodes=NODES,
        edges=EDGES,
        requirements=REQUIREMENTS,
    )


# ============================================================
# 不新增事实
# ============================================================


class TestTheDocumentAddsNoFacts:
    def test_every_item_is_verbatim_from_the_source_sentences(self, doc):
        """每条描述都必须**逐字**出现在原文句里

        文档多一句原文没有的话，读文档的人就会以为业务方真这么说过——
        这是这份文档唯一不能犯的错。
        """
        texts = [s["text"] for s in SENTENCES]
        for item in doc["items"]:
            assert any(item["description"] in t for t in texts), (
                f"{item['doc_id']} 的描述「{item['description']}」"
                "在原文里逐字找不到——文档新增了事实"
            )

    def test_every_item_points_back_to_a_source_sentence(self, doc):
        """每条都要指得回原文句

        指不回去的条目 = 悬空的条目 = 读者没法核对它。
        """
        known = {s["sentence_id"] for s in SENTENCES}
        for item in doc["items"]:
            assert item["source_sentence_ids"], (
                f"{item['doc_id']} 没有原文出处"
            )
            for sid in item["source_sentence_ids"]:
                assert sid in known, f"{item['doc_id']} 指向不存在的原文句 {sid}"

    def test_the_quote_is_verbatim_from_the_source(self, doc):
        for item in doc["items"]:
            quote = item["source_quote"]
            assert quote
            for sid in item["source_sentence_ids"]:
                src = next(s["text"] for s in SENTENCES if s["sentence_id"] == sid)
                assert quote == src or quote in src, (
                    f"{item['doc_id']} 的原文引用「{quote}」不是原文句 {sid} 的逐字片段"
                )

    def test_every_item_is_derived_from_the_graph(self, doc):
        """每个条目都要能指回图里的一个元素

        图是主结构，平铺列表只是兼容投影。文档条目**只**从图来——
        从平铺列表来的就是碎片（「库存看板」单独成条那种）。
        """
        graph_descriptions = set()
        for n in NODES:
            graph_descriptions.add(n["action"])
            if n.get("guard"):
                graph_descriptions.add(n["guard"])
            for m in n.get("measures", []):
                graph_descriptions.add(m)
        for e in EDGES:
            if e.get("guard"):
                graph_descriptions.add(e["guard"])
            if e.get("trigger"):
                graph_descriptions.add(e["trigger"])
        for item in doc["items"]:
            assert item["description"] in graph_descriptions, (
                f"{item['doc_id']} 的描述不在流程图里——它不是从图派生的"
            )

    def test_entities_do_not_become_items_on_their_own(self, doc):
        """实体不许单独成条

        「出库单」单独成一条，是平铺投影的碎片（它给建模盒按类型分组用）。
        文档里它是流程步骤的属性（产出/读取），不是一条需求。
        """
        descriptions = [i["description"] for i in doc["items"]]
        assert "出库单" not in descriptions
        step = next(i for i in doc["items"] if i["description"] == "出库要按先进先出")
        assert "出库单" in step["outputs"]


class TestTheItemCountMatchesTheGraph:
    def test_the_item_count_is_exactly_what_the_graph_yields(self, doc):
        """不多不少：条目数 == 图里能派生的条目数

        少了 = 静默丢条目；多了 = 凭空多出条目。两种都是坏结果。
        """
        expected = 0
        for n in NODES:
            expected += 1                      # 步骤
            expected += 1 if n.get("guard") else 0
            expected += len(n.get("measures") or [])
        for e in EDGES:
            expected += 1 if e.get("guard") else 0
            expected += 1 if e.get("trigger") else 0
        assert len(doc["items"]) == expected

    def test_doc_ids_are_sequential_and_unique(self, doc):
        ids = [i["doc_id"] for i in doc["items"]]
        assert ids == [f"REQ-{n + 1:03d}" for n in range(len(ids))]
        assert len(set(ids)) == len(ids)

    def test_the_markdown_does_not_drop_any_item(self, doc):
        """markdown 里每个 doc_id 都得出现

        「结构里有、文档里没有」= 静默丢失。
        """
        for item in doc["items"]:
            # **必须是章节标题**——只查 doc_id 出现过是不够的：
            # 追溯矩阵里也写着 doc_id，条目正文没了它照样「出现过」。
            assert f"### {item['doc_id']}" in doc["markdown"], (
                f"{item['doc_id']} 在 markdown 的条目正文里不见了"
            )


# ============================================================
# 追溯矩阵
# ============================================================


class TestTheTraceMatrix:
    def test_every_source_sentence_has_a_row(self, doc):
        rows = {r["sentence_id"] for r in doc["trace_matrix"]}
        assert rows == {s["sentence_id"] for s in SENTENCES}

    def test_a_row_reports_who_claimed_the_sentence(self, doc):
        row = next(r for r in doc["trace_matrix"] if r["sentence_id"] == "sent_1")
        assert row["claimed_by"]
        assert row["status"] == "已认领"

    def test_an_unclaimed_content_sentence_is_reported_not_hidden(self, doc):
        """没被认领的内容句必须出现在「未被认领」里

        藏起来 = 覆盖率分母里的洞被文档盖住了。
        """
        doc2 = build_document(
            set_id="s", goal="g", context="",
            sentences=SENTENCES, nodes=NODES, edges=[],
            requirements=REQUIREMENTS,
        )
        unclaimed = doc2["unclaimed_sentence_ids"]
        assert "sent_3" in unclaimed
        row = next(r for r in doc2["trace_matrix"] if r["sentence_id"] == "sent_3")
        assert "未被认领" in row["status"]

    def test_a_non_content_sentence_is_not_counted_as_uncovered(self, doc):
        """标题句/铺垫句不是「漏了」——它本来就不进需求

        把它们算成未覆盖，文档就会凭空指控一句没要求的话。
        """
        row = next(r for r in doc["trace_matrix"] if r["sentence_id"] == "sent_0")
        assert row["status"] == "非内容句（不进需求）"
        assert "sent_0" not in doc["unclaimed_sentence_ids"]


# ============================================================
# 诚实：验收标准与优先级
# ============================================================


class TestTheDocumentIsHonest:
    def test_acceptance_criteria_are_a_placeholder_only(self, doc):
        """需求盒不定「怎么算满足」

        在这里编一条验收标准 = 编。那是建模盒的可判定预期的事。
        """
        for item in doc["items"]:
            assert item["acceptance_criteria"] == [ACCEPTANCE_PLACEHOLDER]

    def test_priority_is_copied_not_invented(self, doc):
        """优先级只能来自平铺列表，不能自己给

        （抽取层目前统一标 must_have——文档照实写，并在未决项里说明。）
        """
        known = {r.get("priority") for r in REQUIREMENTS}
        for item in doc["items"]:
            assert item["priority"] in known or item["priority"] == ""

    def test_the_markdown_says_the_priority_has_no_discrimination(self, doc):
        assert "尚无区分度" in doc["markdown"]

    def test_the_markdown_lists_the_open_items(self, doc):
        assert "未决项" in doc["markdown"]
        assert "验收标准" in doc["markdown"]


# ============================================================
# 接线：产出里必须有文档
# ============================================================


class TestTheBoxShipsTheDocument:
    def test_the_packager_produces_a_document(self):
        from boxes.requirement_capture.runner import _simulate_requirement_set_packager

        out = _simulate_requirement_set_packager({
            "set_id": "req_set_1",
            "business_goal": "拣货准确率要能在库存看板看得到",
            "context": "",
            "source_sentences": SENTENCES,
            "nodes": NODES,
            "edges": EDGES,
            "requirements": REQUIREMENTS,
            "requirement_count": len(REQUIREMENTS),
        })
        assert out["document"]["items"]
        assert out["document"]["markdown"].startswith("# 需求文档")

    def test_the_result_schema_declares_the_document(self):
        from boxes.requirement_capture import get_definition

        definition = get_definition()
        fields = {f.name for f in definition.get_schema(definition.result.result_schema).fields}
        assert "document" in fields, (
            "产出 schema 里没有 document——按 schema 生成的消费方永远拿不到需求文档"
        )

    def test_the_document_shape_is_declared(self):
        """文档自己的形状也得声明（ref: 只能引用本 definition 内）"""
        from boxes.requirement_capture import get_definition

        definition = get_definition()
        schema = definition.get_schema("schema_requirement_document")
        assert schema is not None
        names = {f.name for f in schema.fields}
        assert {"items", "trace_matrix", "markdown", "goal"} <= names

    def test_an_empty_graph_still_produces_a_readable_document(self):
        """空图 → 文档仍然是文档（而不是崩掉或空字符串）"""
        doc = build_document(
            set_id="s", goal="", context="",
            sentences=[], nodes=[], edges=[], requirements=[],
        )
        assert doc["markdown"].startswith("# 需求文档")
        assert "没有抽出任何需求条目" in doc["markdown"]
        assert doc["items"] == []


# ============================================================
# 看板：文档得真的能被看见
# ============================================================


class TestTheKanbanShowsTheDocument:
    def test_the_kanban_renders_the_document(self):
        from kanban.web import HTML_PAGE

        assert "renderRequirementDocument" in HTML_PAGE, (
            "看板里没有需求文档的渲染器——文档生成了但没人看得见"
        )
        assert "r.document" in HTML_PAGE, (
            "渲染器没有接上产出里的 document 字段"
        )

    def test_the_kanban_javascript_still_parses(self):
        """看板的 JS 必须能解析

        看板是**纯客户端渲染**：JS 语法错误 = 白屏，而服务端照样返回 200，
        **没有任何报错**。所以这里用 node --check 真跑一遍语法检查——
        「改完看板能打开」不能靠肉眼。
        """
        import re
        import shutil
        import subprocess
        import tempfile
        import pathlib

        from kanban.web import HTML_PAGE

        node = shutil.which("node")
        if not node:
            pytest.skip("没有 node，跳过 JS 语法检查")
        scripts = re.findall(r"<script>(.*?)</script>", HTML_PAGE, re.S)
        assert scripts, "看板里没有 <script> 块"
        with tempfile.TemporaryDirectory() as tmp:
            js = pathlib.Path(tmp) / "kanban.js"
            js.write_text("\n".join(scripts), encoding="utf-8")
            proc = subprocess.run([node, "--check", str(js)],
                                  capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, (
            f"看板 JS 语法错误（页面会白屏而且不报错）：\n{proc.stderr[:1500]}"
        )
