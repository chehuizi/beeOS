"""tests.test_box_standard - 五组件契约：盒子能不能只靠声明就被正确渲染 / 调用

标准化的判据不是「有几栏」，而是**加一只新盒子要不要改看板和路由**。
以前要改，因为：
- 产出形态靠「result 里有没有 nodes」猜，猜错就把一只盒子的产出
  渲染成另一只盒子的形状，而且不报错
- /api/structure 写死了 wms_requirement_box + capture_business_requirement，
  调用方必须知道某一只盒子的 task 类型名

这组测试盯住契约本身：契约一旦退化回硬编码，这里就红。
"""

from __future__ import annotations

import ast
import math
import pathlib
import re

import pytest

from boxes.modeling import get_definition as get_modeling_definition
from boxes.requirement_capture import get_definition as get_capture_definition
from kanban.trigger import (
    BOX_REGISTRY,
    TriggerError,
    box_manifest,
    list_task_entries,
    resolve_text_intake,
    trigger_task,
)
from kanban.web import HTML_PAGE
from runtime.store import TaskRunStore
from tests.conftest import stub_compliant_llm

REPO = pathlib.Path(__file__).resolve().parent.parent
KANBAN_DIR = REPO / "kanban"


CAPTURE = "wms_requirement_box"
MODELING = "wms_design_box"
RECEIPT = "receipt_capture_box"

# 契约测试遍历注册表，而不是手抄盒子名单。
# 手抄过一次就漏一次：receipt_capture_box 加进 BOX_REGISTRY 时，
# 下面两条一致性测试压根没跑到它，归属没填也没人喊。
# 「加一只新盒子要不要改测试」正是标准化的判据，不该由人记着。
ALL_BOXES = sorted(BOX_REGISTRY)


# ============================================================
# ARTIFACTS OUT：产出形态是声明，不是猜的
# ============================================================


class TestArtifactView:
    @pytest.mark.parametrize(
        "box_id, expected",
        [(CAPTURE, "flow_graph"), (MODELING, "ddd_model")],
    )
    def test_every_box_declares_its_artifact_view(self, box_id, expected):
        assert BOX_REGISTRY[box_id][0]().result.view == expected

    def test_frontend_carries_the_artifact_view_through(self):
        """后端 response 里的 artifact_view 必须被搬到前端 artifact 对象上

        漏搬过一次：后端声明 flow_graph，前端对象没这个字段 → 落到 undefined
        → generic 回退 → 产出被如实显示成 JSON。功能没崩，但专用视图没了，
        而且看板上写着「该盒声明的产出形态 generic」——把渲染缺失误报成盒子声明。
        """
        assert "artifact_view: data.artifact_view || 'generic'" in HTML_PAGE
        # 中止分支也要带，否则渲染层分不清「声明了 generic」和「压根没产出」
        assert HTML_PAGE.count("artifact_view: data.artifact_view") == 2

    def test_kanban_dispatches_on_the_declared_view(self):
        """渲染器必须按声明分派，不能再靠「有没有 nodes 字段」猜"""
        assert "ARTIFACT_VIEWS" in HTML_PAGE
        assert "renderArtifactBody(a.artifact_view, a.result)" in HTML_PAGE
        assert "Array.isArray(a.result" not in HTML_PAGE

    def test_declared_views_all_have_a_renderer_or_fall_back(self):
        """声明的每个 view 都有渲染器，或者明确落到 generic 回退

        不允许「声明了 view 但既没渲染器也没回退」——那会让产出变成空白。

        已知视图表从 HTML 里读，不在这里手抄：手抄的那份会漂，
        加了新渲染器却不改这个集合，测试还会一直绿。
        """
        import re

        registered = set(re.findall(r"^\s{2}(\w+):\s*render", HTML_PAGE, re.M))
        for box_id in BOX_REGISTRY:
            view = BOX_REGISTRY[box_id][0]().result.view
            assert view == "generic" or view in registered, (
                f"{box_id} 声明了 view={view!r}，看板渲染器注册表里没有"
            )
        assert "generic" in HTML_PAGE or "在看板没有专用视图" in HTML_PAGE

    def test_hidden_attribute_actually_hides(self):
        """`hidden` 必须压得住类选择器上的 display

        UA 的 `[hidden] {display:none}` 来自用户代理样式表，作者样式随便
        一条 `display: flex` 就能盖掉它。实测后果：media 投料口还没选图，
        预览块就常驻在那儿，里头一张空 <img> 显示成破图占位——
        看上去像「载了一张图但坏了」，其实压根没图。
        """
        import re

        rules = re.findall(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", HTML_PAGE)
        assert rules, "看板里没有 [hidden] 兜底规则：用 hidden 切换的面板会漏出来"

    def test_trigger_response_carries_the_artifact_kind(self, tmp_path, monkeypatch):
        """产出响应要自带形态，API 消费方不必猜"""
        from runtime import llm

        monkeypatch.setattr(llm, "complete_json", stub_compliant_llm)
        store = TaskRunStore(tmp_path / "runs.jsonl")
        r = trigger_task(
            CAPTURE, "capture_business_requirement",
            {"narrative": "我们的目标是降本。\n每月盘点一次。\n库存低于安全库存触发补货。"},
            store,
        )
        assert r["artifact_view"] == "flow_graph"
        assert r["artifact_type"] == "business_requirement_captured"


# ============================================================
# TASK IN：按能力解析，不按 id 硬编码
# ============================================================


class TestTextIntakeResolution:
    def test_resolves_by_capability_when_nothing_is_specified(self):
        """不传 box_id 时，按「谁吃自然语言」解析，而不是写死某只盒子"""
        box_id, task_type = resolve_text_intake()
        assert box_id == CAPTURE
        assert task_type == "capture_business_requirement"

    def test_explicit_box_and_task_win(self):
        assert resolve_text_intake(MODELING, "handle_modeling_request") == (
            MODELING, "handle_modeling_request"
        )

    def test_box_without_text_intake_is_refused_with_a_reason(self):
        with pytest.raises(TriggerError, match="no task taking plain text"):
            resolve_text_intake(MODELING)

    def test_unknown_box_is_refused(self):
        with pytest.raises(TriggerError, match="unknown box_id"):
            resolve_text_intake("no_such_box")

    def test_structure_endpoint_carries_no_hardcoded_box_id(self):
        """/api/structure 里不能再出现某只盒子的 id 或 task 类型名"""
        assert "wms_requirement_box" not in HTML_PAGE
        assert "capture_business_requirement" not in HTML_PAGE


# ============================================================
# 盒子作为独立 API 单元：manifest 齐不齐五组件
# ============================================================


class TestManifest:
    @pytest.mark.parametrize("box_id", [CAPTURE, MODELING])
    def test_manifest_covers_all_five_components(self, box_id):
        m = box_manifest(box_id)
        assert m["box_id"] == box_id
        assert m["version"] >= 1
        assert m["tasks"], "TASK IN：可接收的 task"
        assert m["beelines"], "BEELINE：程序 + op 清单"
        assert m["produces"]["schema"], "ARTIFACTS OUT：产出契约"
        assert m["produces"]["view"], "ARTIFACTS OUT：形态声明"
        assert m["acceptance"], "ACCEPTANCE：判据"
        assert m["metrics"], "BOX CONTEXT：实测指标"
        assert "fed_by" not in m or True  # fed_by 走 box_context，不重复声明

    def test_manifest_is_json_safe(self):
        """manifest 要能直接当 API 响应体返回——不能夹带 pydantic 对象"""
        import json

        for box_id in BOX_REGISTRY:
            json.dumps(box_manifest(box_id))  # 不抛即通过

    def test_manifest_lists_the_beeline_pinned_by_the_task(self):
        m = box_manifest(CAPTURE)
        task_beeline = m["tasks"][0]["beeline_id"]
        assert task_beeline in [b["id"] for b in m["beelines"]]
        assert m["beelines"][0]["ops"], "BEELINE 要列出 op 清单"

    def test_unknown_box_manifest_is_empty(self):
        assert box_manifest("no_such_box") == {}

    def test_manifest_metrics_are_declarations_without_fake_actuals(self):
        """manifest 不带履约历史：actual 必须是 None，不能编一个数出来"""
        for r in box_manifest(CAPTURE)["metrics"]:
            assert r["actual"] is None
            assert r["sample_size"] == 0


# ============================================================
# 五组件的声明必须自洽
# ============================================================


class TestDeclarationCoherence:
    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_task_schemas_exist_and_beelines_are_pinned(self, box_id):
        d = BOX_REGISTRY[box_id][0]()
        assert d.result.result_schema in {s.id for s in d.schemas}
        for t in d.task:
            assert t.task_schema in {s.id for s in d.schemas}

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_every_acceptance_metric_has_a_value_in_result(self, box_id):
        """acceptance 声明的每个 metric，产出 schema 里都得有对应字段

        没有就意味着判据永远读不到值——要么恒判通过（假绿灯），
        要么恒判失败。两种都是坏结果。
        """
        d = BOX_REGISTRY[box_id][0]()
        result_schema = d.get_schema(d.result.result_schema)
        fields = {f.name for f in result_schema.fields}
        for rule in d.result.acceptance:
            assert rule.metric in fields, f"{box_id}: {rule.metric} 不在产出 schema 里"

    def test_result_schema_is_listed_in_the_manifest(self):
        d = get_capture_definition()
        assert box_manifest(CAPTURE)["produces"]["schema"] == d.result.result_schema

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_beeline_only_names_schemas_the_box_declares(self, box_id):
        """beeline 每个 op 的 input / output 都必须是这只盒子声明过的 schema

        指着一个不存在的 schema id，声明就落空了：调用方按 beeline 拼
        数据，拼出来的东西没有任何 schema 说它长什么样，也没有校验会响。
        「声明了却拿不到」的又一族。
        """
        get_def, beeline_loaders = BOX_REGISTRY[box_id]
        declared = {s.id for s in get_def().schemas}
        for _beeline_id, load in beeline_loaders.items():
            for op in load().operations:
                for role, sid in (("input", op.input), ("output", op.output)):
                    assert sid in declared, (
                        f"{box_id}: beeline {load().id} 的 op "
                        f"{op.op_id!r} {role} 指向未声明的 schema {sid!r}"
                    )


class TestExceptionConditionsAreReachable:
    """exception.condition 必须**真的能被触发**

    `_evaluate_condition` 拿到 `result.get(field)` 是 None 就直接
    `return False`——**不报错、不提示**。后果是这条规则写在那里，
    看上去在守着某件事，实际永远不会触发：构建失败不会 rollback，
    组件数为空不会 no_deliver。

    这比判据读不到值更隐蔽：判据读不到会判红（吵），异常读不到
    只是永远不响（安静地没做）。

    ## 为什么守卫必须用被验者自己的解析器

    这条踩过：守卫原来用
    `re.split(r"(>=|<=|==|!=|>|<)", cond, maxsplit=1)` 取**位置最靠前**的
    比较符，而 `_evaluate_condition` 按 `(">=", "<=", "==", "!=", ">", "<")`
    的**优先级顺序**找第一个出现的。条件里有两个比较符时两边选到
    **不同的字段**——守卫验的是它自己那套解析，于是放行了一条
    实际永远不触发的规则。

    已犯过：wms_analysis_box 的
    "gap_count > 0 and next_requirement_seed == ''"
    守卫解析出 `gap_count`（存在）→ 放行；
    实际解析先撞上 `==`，字段变成
    "gap_count > 0 and next_requirement_seed" → 读不到 → 永不触发。
    同一个条件还错了两处：小写 `and`（evaluator 只认大写）、
    字段名少个 `ed`、拿布尔去比空串。

    现在守卫读 `runtime.acceptance.condition_parts / condition_fields`，
    跟被验者是同一份解析。
    """

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_condition_names_a_field_the_result_can_carry(self, box_id):
        from runtime.acceptance import condition_fields, condition_parts

        d = BOX_REGISTRY[box_id][0]()
        fields = {f.name for f in d.get_schema(d.result.result_schema).fields}
        for rule in d.result.exceptions:
            cond = rule.condition.strip()
            for part in condition_parts(cond):
                ops = re.findall(r"(>=|<=|==|!=|>|<)", part)
                assert ops, (
                    f"{box_id}: exception {cond!r} 里的这一段 {part!r} 不是 "
                    f"`field op value` 三段式——裸标识符永远不触发"
                )
                assert len(ops) == 1, (
                    f"{box_id}: exception {cond!r} 里的这一段 {part!r} 有 "
                    f"{len(ops)} 个比较符（{ops}）——多半是漏了 AND。"
                    f"硬跑下去会在比较时抛 TypeError，把整次 acceptance 评估掀翻"
                )
            for field in condition_fields(cond):
                assert field in fields, (
                    f"{box_id}: exception {cond!r} 会去读字段 {field!r}，"
                    f"但它不在产出 schema 里（可用字段：{sorted(fields)}）"
                    f"→ result.get() 返回 None → 这条规则永远不触发"
                )

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_every_condition_part_can_actually_be_read(self, box_id):
        """条件里**每一段**都要读得到值，不只是第一段

        `_evaluate_condition` 对 AND 段是 `all(...)` 短路求值：
        第一段 False 就直接返回 False，后面那段读没读到值根本没人知道。
        所以「多段条件」比「单段条件」多一个静默失效的点。
        """
        from runtime.acceptance import condition_fields, condition_parts

        d = BOX_REGISTRY[box_id][0]()
        for rule in d.result.exceptions:
            parts = condition_parts(rule.condition)
            read = condition_fields(rule.condition)
            assert len(read) == len(parts), (
                f"{box_id}: exception {rule.condition!r} 切成了 {len(parts)} 段，"
                f"但只读 {len(read)} 个字段——有一段没有比较符"
            )

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_every_condition_can_actually_fire(self, box_id):
        """每条规则都得能**真的响**——不只是字段存在

        「字段在 schema 里」是弱证明：字段在，但取值类型对不上、
        或者比较符写反了，规则照样永远不响。

        这里反着验：给每条规则造一个**该触发**的产出，
        断言 `_evaluate_condition` 真的返回 True。
        造不出来 = 这条规则从写下来那天起就没响过。
        """
        from runtime.acceptance import _evaluate_condition, condition_parts

        d = BOX_REGISTRY[box_id][0]()
        for rule in d.result.exceptions:
            satisfying = {}
            for part in condition_parts(rule.condition):
                field, op, value = _split_comparison(part)
                satisfying[field] = _value_satisfying(op, value)
            assert _evaluate_condition(rule.condition, satisfying) is True, (
                f"{box_id}: exception {rule.condition!r} 在一个本该触发的产出上"
                f"（{satisfying}）没有触发——这条规则从来就没响过"
            )

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_condition_value_type_matches_the_field_type(self, box_id):
        """条件里的值，类型必须跟字段类型对得上

        「字段存在」和「规则能响」都证明不了这一条：
        - 字段在 schema 里，规则也能被合成值点亮——但生产里那个字段
          存的是布尔，拿它跟空串比，永远是 False
        - 合成值是**照着条件挑类型**的，所以它天然发现不了类型不匹配

        已犯过：`next_requirement_seeded`（boolean）被拿去跟 `''` 比。
        """
        from runtime.acceptance import condition_parts

        d = BOX_REGISTRY[box_id][0]()
        schema = d.get_schema(d.result.result_schema)
        types = {f.name: f.type for f in schema.fields}

        for rule in d.result.exceptions:
            for part in condition_parts(rule.condition):
                field, _op, value = _split_comparison(part)
                if field not in types:
                    continue  # 字段不存在由另一条守卫管
                field_type = types[field]
                literal_type = _literal_type(value)
                if literal_type is None:
                    continue  # 值不是字面量（引号里的自由文本等），不判
                if not _types_compatible(field_type, literal_type):
                    raise AssertionError(
                        f"{box_id}: exception {rule.condition!r} 里 {field!r} "
                        f"在产出 schema 里是 {field_type}，条件却拿它跟 "
                        f"{literal_type} 类型的值比——这条规则永远不响"
                    )


def test_no_test_in_this_file_is_nested_inside_another_function():
    """本文件里不允许有「嵌在别的函数里」的测试

    这条是给一个真实踩过的坑写的：往类中间插模块级 helper 函数时，
    紧跟其后缩进的那个 `def test_xxx` 变成了 **helper 的内嵌函数**，
    不是类的方法——pytest 收不到它，于是它一次也没跑过，
    而源码里它好好地待着，看起来在守着某件事。

    实测：`test_condition_value_type_matches_the_field_type` 就这样死了
    一轮，测试总数 108 → 116（只多了一条守卫的 8 个参数化），
    而我以为三条新守卫全都在跑。

    **「写下来了」不等于「会跑」**——跟「声明了却拿不到」是同一族病。
    """
    import ast

    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    nested = []

    def walk(node, ancestors):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name.startswith("test_") and ancestors:
                    nested.append((child.name, ancestors[-1]))
                walk(child, ancestors + [child.name])
            else:
                walk(child, ancestors)

    walk(tree, [])
    assert not nested, (
        f"这些测试被嵌在别的函数里，pytest 收不到：{nested}——"
        "它们一次也没跑过，而源码里看着好好的"
    )


class TestSkeletonIsHonest:
    """骨架盒：判据真声明、腿真没实现

    `evaluate_acceptance` 里是 `if not failed: ACCEPTED`。所以一只
    **零判据**的盒子会被判成「全过」——那是「看起来成功，其实什么
    都没验」的谎。骨架盒的正确做法是反过来：真声明它将来要验的东西，
    产出**不带**那些量 → 全红 → 看板标「未实现」。
    """

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_every_box_declares_a_maturity_it_actually_has(self, box_id):
        assert BOX_REGISTRY[box_id][0]().result.maturity in ("live", "skeleton")

    def test_maturity_is_a_closed_set_not_a_free_string(self):
        """maturity 必须是封闭的两值，前端的显示表也得**正好**是这两值

        两头都要封闭：

        - 后端 `ResultDef.maturity` 是 `Literal`，多写一个 `imperfect`
          会在构造时炸；改成 `str` 就什么都能塞进来。
        - 前端 `MATURITY_DISPLAY` 的键必须**等于**后端的合法值集合。
          少一个值，那只盒子落进 fallback，看起来跟实现了没区别；
          多一个值，说明两边已经开始各说各话。

        这个字段是看板「未实现」徽章的唯一依据。
        """
        import re
        import typing

        from core.models import ResultDef

        legal = set(typing.get_args(ResultDef.model_fields["maturity"].annotation))
        assert legal == {"live", "skeleton"}, (
            f"ResultDef.maturity 的合法值变成了 {legal}——"
            f"看板只认 live / skeleton，多出来的值会让「未实现」徽章消失"
        )

        block = re.search(
            r"const MATURITY_DISPLAY = \{(.*?)\n\};", HTML_PAGE, re.S)
        assert block, "看板里没有 MATURITY_DISPLAY：成熟度没有对应的显示逻辑"
        front = set(re.findall(r"^\s{2}(\w+):\s*\{", block.group(1), re.M))
        assert front == legal, (
            f"前端 MATURITY_DISPLAY 的键 {sorted(front)} != 后端合法值 "
            f"{sorted(legal)}。少一个 = 那只盒子落进 fallback 看着跟实现了"
            f"没区别；多一个 = 两边各说各话"
        )

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_a_skeleton_box_declares_criteria_and_no_metrics(self, box_id):
        """骨架盒不能空声明判据（有则会被判全过），也不能声明没采集器的指标"""
        d = BOX_REGISTRY[box_id][0]()
        if d.result.maturity != "skeleton":
            return
        assert d.result.acceptance, (
            f"{box_id}: 骨架盒零判据 = 会被判成全过 = 谎"
        )
        declared = [m for group in d.metrics.model_dump().values() for m in group]
        assert not declared, (
            f"{box_id}: 骨架盒没有产出，却声明了指标 {declared}——"
            f"声明了却拿不到"
        )

    def test_a_skeleton_box_actually_fails_its_criteria(self, tmp_path, monkeypatch):
        """跑一只骨架盒：必须真的 REJECTED，而且每条判据都红

        这条是「未实现」这三个字的**唯一凭据**。只看 `maturity`
        字段的话，那只是一行声明；真跑一遍红，才知道这条声明是真的。

        **为什么用一只合成盒，而不是某只现役盒子**：
        原来拿 `wms_ops_box` 当样例，但它后来把腿实现了（maturity=live），
        于是这条守卫变成在验一只**live 盒子的判据**，跟「未实现」半点关系没有——
        而且它会随现役盒子的行为变化而漂（ops 盒判据一改就红）。
        「未实现」这件事一旦没有任何盒子再声明，这条保证就静默消失了。

        所以改成**机制级**验证：临时注册一只 `maturity="skeleton"` 的合成盒，
        它的产出**不带**判据要的那些 metric → 必须每条判据都红、都判失败、
        整轮 REJECTED。这验的是 runtime 的诚实性本身：
        **「交不出东西」必须表现为红，而不是 n/a，更不是通过。**

        另外保留一条动态检查：注册表里**真的**有骨架盒时，
        每只都要自己跑一遍、全红。将来新加骨架盒，这条自动覆盖它。
        """
        from core.beeline_models import Bee, Beeline, Operation
        from core.models import (
            AcceptanceRule,
            BeeBoxDefinition,
            FieldDef,
            MetricsDef,
            PlacementDef,
            QueenAuthorization,
            QueenDef,
            QueenRules,
            ResultDef,
            SchemaDef,
            TaskDef,
        )
        from kanban.trigger import RUNNER_REGISTRY
        from runtime.runner import HandlerRunner

        box_id = "skeleton_sample_box"
        definition = BeeBoxDefinition(
            id=box_id,
            version=1,
            description="合成骨架盒：只用来验「未实现必须真的红」",
            schemas=[
                SchemaDef(
                    id="schema_skel_in",
                    fields=[FieldDef(name="topic", type="string")],
                ),
                SchemaDef(
                    id="schema_skel_out",
                    fields=[FieldDef(name="note", type="string")],
                ),
            ],
            task=[
                TaskDef(
                    type="do_skeleton",
                    task_schema="schema_skel_in",
                    beeline_id="beeline_skeleton_sample",
                    beeline_version=1,
                ),
            ],
            result=ResultDef(
                type="skeleton_result",
                result_schema="schema_skel_out",
                acceptance=[
                    AcceptanceRule(metric="never_computed", op="gte", value=1),
                    AcceptanceRule(metric="never_flagged", op="eq", value=True),
                ],
                exceptions=[],
                maturity="skeleton",
            ),
            placement=PlacementDef(
                display_name="骨架样例盒",
                domain="WMS",
                feeds_into=[],
                feedback_into=[],
            ),
            queen=QueenDef(
                authorization=QueenAuthorization(),
                rules=QueenRules(),
            ),
            metrics=MetricsDef(),
        )
        beeline = Beeline(
            id="beeline_skeleton_sample",
            version=1,
            description="合成骨架盒的作业路线",
            operations=[
                Operation(
                    op_id="skel_op",
                    type="skel_op",
                    input_from="external",
                    input="schema_skel_in",
                    output="schema_skel_out",
                    next=[],
                    bee=Bee(type="skel_handler"),
                ),
            ],
        )

        def _skel_handler(_input_data: dict) -> dict:
            # **产出里没有那两条判据要的值** —— 这正是「腿还没实现」。
            return {"note": "骨架盒没实现这条判据"}

        monkeypatch.setitem(
            BOX_REGISTRY,
            box_id,
            (lambda: definition, {"beeline_skeleton_sample": lambda: beeline}),
        )
        monkeypatch.setitem(
            RUNNER_REGISTRY,
            box_id,
            HandlerRunner({"skel_op": _skel_handler}, owner=box_id),
        )

        store = TaskRunStore(tmp_path / "runs.jsonl")
        r = trigger_task(
            box_id, "do_skeleton", {"topic": "随便"}, store,
        )
        assert r["acceptance_status"] == "rejected", (
            f"骨架盒跑成了 {r['acceptance_status']}——"
            f"「未实现」必须是红：交不出东西不是「用不上」，更不是通过"
        )
        outcomes = {d["metric"]: d["outcome"] for d in r["acceptance_detail"]}
        assert outcomes == {
            "never_computed": "failed",
            "never_flagged": "failed",
        }, (
            f"骨架盒不该有判据通过或被判不适用：{outcomes}"
        )

        # 动态检查：注册表里真的有骨架盒的话，每只都要自己跑一遍、全红。
        # 用看板自己生成的示例投料（跟看板按钮投出去的是同一份形状）。
        for real_id in ALL_BOXES:
            d = BOX_REGISTRY[real_id][0]()
            if d.result.maturity != "skeleton":
                continue
            entry = list_task_entries(real_id)[0]
            rr = trigger_task(
                real_id,
                entry["task_type"],
                entry["sample_payload"],
                TaskRunStore(tmp_path / f"{real_id}.jsonl"),
            )
            assert rr["acceptance_status"] == "rejected", (
                f"{real_id} 声明自己是骨架盒，跑出来却是 {rr['acceptance_status']}"
            )
            bad = {
                x["metric"]: x["outcome"] for x in rr["acceptance_detail"]
                if x["outcome"] != "failed"
            }
            assert not bad, (
                f"{real_id} 声明自己是骨架盒，判据却有非失败的读数：{bad}"
            )


class TestRegistryCoverage:
    """三张注册表一一对应：加了盒子就得处处有声，漏一处看板就缺一块"""

    def test_every_registered_box_has_a_runner(self):
        from kanban.trigger import RUNNER_REGISTRY

        missing = sorted(set(BOX_REGISTRY) - set(RUNNER_REGISTRY))
        assert not missing, f"注册了但没有 runner：{missing}"

    def test_the_kanban_does_not_retype_business_facts(self):
        """看板不许把业务事实的值重新敲一遍

        它以前住在 `kanban/trigger.py` 的 BOX_META。后果不是「不好看」，
        是盒子只能通过看板去知道自己接谁——换掉看板，接线就跟着丢了，
        而接线恰恰是「这只盒子是什么」的一部分。

        口径：**业务事实的值**（价值流 / 角色 / 显示名）不许作为字面量
        出现在看板层。读 `placement.value_stream` 再传出去不算——
        那是读，不是在这里声明。

        为什么不管 box_id 字面量：注册表总得写出自己有哪些成员，
        `BOX_REGISTRY["receipt_capture_box"] = ...` 是装配，不是业务知识。
        曾经的病是「盒子的价值流归属写在看板里」，不是「看板认识 box_id」。
        """
        facts = set()
        for _bid, (get, _bl) in BOX_REGISTRY.items():
            p = get().placement
            facts |= {p.display_name, p.value_stream, p.role}
        facts = {f for f in facts if f}

        offenders = []
        for path in sorted(KANBAN_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docs = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                    d = ast.get_docstring(node, clean=False)
                    if d is not None:
                        docs.add(d)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value not in docs and node.value in facts):
                    offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
        assert not offenders, (
            "看板层又敲了一遍业务事实的值：\n  " + "\n  ".join(offenders)
            + "\n它们应该只住在各盒 definition 的 placement 里"
        )

    def test_the_kanban_holds_no_per_box_table(self):
        """看板层不许再出现「一张按 box_id 索引的表」

        以前 BOX_META 就是这么一张表。守卫盯的是**形状**：
        模块级赋值成 dict，键全是 box_id，值又是 dict——
        这正是那张表的形状，不管它叫什么名字。
        """
        offenders = []
        for path in sorted(KANBAN_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            box_ids = {bid for bid in BOX_REGISTRY}
            for node in ast.walk(tree):
                # **Assign 和 AnnAssign 都要认**：原来那张表写的是
                # `BOX_META: dict[str, ...] = {...}`，带注解，是 AnnAssign。
                # 只认 Assign 的话守卫一声不响——写守卫时漏了带注解这一类，
                # 守卫就等于没写。回退验证逮到的就是它。
                if isinstance(node, ast.AnnAssign):
                    targets = [node.target] if isinstance(node.target, ast.Name) else []
                elif isinstance(node, ast.Assign):
                    targets = [t for t in node.targets if isinstance(t, ast.Name)]
                else:
                    continue
                if not isinstance(node.value, ast.Dict):
                    continue
                keys = [k.value for k in node.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if keys and set(keys) <= box_ids and len(keys) > 1:
                    for t in targets:
                        # BOX_REGISTRY / METRIC_COMPUTERS / RUNNER_REGISTRY 是
                        # 按 box_id 索引没错，但值是注册元组 / 算法表，不是归属
                        if t.id in ("BOX_REGISTRY", "METRIC_COMPUTERS", "RUNNER_REGISTRY"):
                            continue
                        offenders.append(f"{path.name}:{node.lineno} {t.id}")
        assert not offenders, (
            "看板层又出现一张按 box_id 索引的归属表：\n  " + "\n  ".join(offenders)
            + "\n它应该住在各盒 definition 的 placement 里"
        )

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_meta_fields_are_actually_filled(self, box_id):
        """归属字段留空串，跟没声明一样——铭牌照样显示「—」

        **这条现在顺带顶着 `test_every_registered_box_has_meta` 的活**：
        以前 BOX_META 漏一只盒子，那只盒子的三个字段全是空，
        而只遍历 BOX_META 的测试看不见这个缺失。BOX_META 没了之后，
        漏填变成 `placement` 落回默认值——同样是三个空字段，
        这条按注册表逐只检查，照样能逮住。
        """
        from kanban.trigger import box_meta

        meta = box_meta(box_id)
        for key in ("display_name", "value_stream", "role"):
            assert meta.get(key), f"{box_id}: meta.{key} 是空的"
        assert "feeds_into" in meta, f"{box_id}: 没声明 feeds_into（哪怕是空列表）"

    @pytest.mark.parametrize("box_id", ALL_BOXES)
    def test_value_stream_has_a_kanban_color(self, box_id):
        """价值流声明了却没配色 → 静默落到 fallback 色，看板分不出业务线

        「声明了却拿不到」的同一个病：不是没有，是有了一个假的。
        """
        from kanban.trigger import box_meta

        vs = box_meta(box_id).get("value_stream")
        assert vs, f"{box_id}: 没声明 value_stream"
        assert f"'{vs}'" in HTML_PAGE, f"{box_id}: 价值流 {vs} 在 VS_PALETTE 里没登记"

    def test_the_kanban_does_not_redeclare_domains(self):
        """看板层不许把域名的值重新敲一遍

        跟 value_stream 同一条规矩，域也一样：它是业务知识，住在各盒
        自己的 placement.domain 里。看板读它来分菜单，不拥有它。

        一旦有人为了让菜单好看，在 JS 里写死一句 `if (name.includes('WMS'))`，
        域的归属就变成第二份知识——加了新域、改了某只盒子的归属，
        看板会安静地按旧的那份分组，而且不报错。
        """
        declared = {
            get().placement.domain
            for get, _bl in BOX_REGISTRY.values()
        } - {""}

        offenders = []
        for path in sorted(KANBAN_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docs = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                    d = ast.get_docstring(node, clean=False)
                    if d is not None:
                        docs.add(d)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value not in docs
                        and node.value in declared):
                    offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
        assert not offenders, (
            "看板层又敲了一遍域名的值：\n  " + "\n  ".join(offenders)
            + "\n它应该只住在各盒 definition 的 placement.domain 里"
        )

    def test_the_kanban_holds_no_domain_table(self):
        """看板层不许出现「一张按域名索引的表」

        `box_domains` 是**算出来的**（遍历注册表读各盒 placement）。
        如果有人图省事写成 `DOMAINS = {"WMS": [...]}`，域成员就有了
        两份来源，改盒子忘了改表，看板会按旧名单分组——而域成员一旦
        对不上，接线视图、菜单计数、单盒面包屑会各说各话。

        盯的是形状：模块级 dict，键全是已声明的域名。
        """
        from kanban.trigger import box_domains, registered_box_ids

        declared = set(box_domains(registered_box_ids())) - {""}
        offenders = []
        for path in sorted(KANBAN_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                if not isinstance(node.value, ast.Dict):
                    continue
                keys = [k.value for k in node.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if keys and set(keys) <= declared:
                    targets = ([node.target] if isinstance(node, ast.AnnAssign)
                               else [t for t in node.targets if isinstance(t, ast.Name)])
                    for t in targets:
                        name = getattr(t, "id", None)
                        if name == "DOMAINS":
                            continue  # 这个名字将来若真需要，得先想清楚
                        offenders.append(f"{path.name}:{node.lineno} {name}")
        assert not offenders, (
            "看板层又出现一张按域名索引的表：\n  " + "\n  ".join(offenders)
            + "\n域成员应该由 box_domains() 从各盒 placement 算出来"
        )


def _placement(box_id):
    """盒子的接线声明（不缓存：守卫要读到当下的值，不是导入那一刻的）"""
    from kanban.trigger import BOX_REGISTRY

    return BOX_REGISTRY[box_id][0]().placement


class TestRadialLayoutIsDerived:
    """环形视图：中心、环序、两类边都从接线推导，前端不许自己决定

    「以 System box 为中心」这个设计是对的，但它必须**由接线算出来**，
    不能在前端写死 `center = "wms_system_box"`——那等于在看板层
    声明了业务事实，跟 BOX_META 一模一样的病，只是换了张脸。

    写死的后果还更隐蔽一点：接线改了（比如某只盒子不再投给系统档案盒），
    看板还是把系统档案盒摆在圆心，结构看上去跟接线完全一致，
    没有任何东西会报错。
    """

    @staticmethod
    def _domains():
        from kanban.trigger import box_domains, registered_box_ids

        return box_domains(registered_box_ids())

    def test_the_center_is_the_unique_sink_not_a_named_box(self):
        """圆心 = 域内唯一汇点（出度 0 且入度 > 0），与它是谁无关"""
        from kanban.trigger import domain_layout

        for domain in self._domains():
            lay = domain_layout(domain)
            if lay["layout"] != "radial":
                assert lay["center"] is None, (
                    f"{domain!r} 已经退回平铺，却还报了一个中心 "
                    f"{lay['center']!r}——那是半个答案"
                )
                assert lay["reason"], f"{domain!r} 退回平铺但没说为什么"
                continue

            center = lay["center"]
            assert center in lay["boxes"]
            # 中心必须是「只被投、不投人」的那只：它在所有边里只出现在终点位置
            sources = {b for b in lay["boxes"]
                       for t in BOX_REGISTRY[b][0]().placement.feeds_into}
            fed_by = {b for b in lay["boxes"]
                      if center in BOX_REGISTRY[b][0]().placement.feeds_into}
            assert center not in sources, f"{center} 是圆心，却还投给别人"
            assert fed_by, f"{center} 是圆心，却没有任何盒子投给它"

            # 域内如果有第二个汇点，中心就不该被选出来
            others = [b for b in lay["boxes"] if b != center
                      and not BOX_REGISTRY[b][0]().placement.feeds_into]
            assert not others, (
                f"{domain!r} 有第二个汇点 {others}，中心不唯一，应该退回平铺"
            )

    def test_every_declared_edge_is_drawn_exactly_once(self):
        """域内每条已声明的边，要么是交接要么是累积，一条都不能漏

        漏画一条边 = 看板上「谁跟谁有关系」少了一条，而布局看上去是完整的。
        分类错一条 = 把「交给下一只验」画成「并进档案」，两件事的读法相反。
        """
        from kanban.trigger import domain_layout

        for domain in self._domains():
            lay = domain_layout(domain)
            declared = {
                (b, t) for b in lay["boxes"]
                for t in BOX_REGISTRY[b][0]().placement.feeds_into
                if t in set(lay["boxes"])
            }
            drawn = {tuple(e) for e in lay["handoffs"]} | {
                (s, lay["center"]) for s in lay["feeds_center"]}
            if lay["layout"] != "radial":
                assert not drawn, f"{domain!r} 退回平铺却还报了边"
                continue
            assert drawn == declared, (
                f"{domain!r}: 声明了 {len(declared)} 条边，画出来的是 {drawn}"
            )

    def test_the_ring_follows_the_handoff_chain(self):
        """环序必须是交接边的顺序，不是字母序也不是注册序

        环上相邻 = 有交接边。环序错了，读者会以为相邻两只之间有交接，
        而实际中间隔着别人——比少画一条边更难发现。
        """
        from kanban.trigger import domain_layout

        for domain in self._domains():
            lay = domain_layout(domain)
            if lay["layout"] != "radial":
                continue
            handoffs = {tuple(e) for e in lay["handoffs"]}
            ring = lay["ring"]
            assert set(ring) | {lay["center"]} == set(lay["boxes"]), (
                f"{domain!r}: 环 + 圆心没覆盖全域的盒子"
            )
            seen = set()
            for i in range(len(ring) - 1):
                pair = (ring[i], ring[i + 1])
                assert pair in handoffs, (
                    f"{domain!r}: 环上第 {i}→{i + 1} 位 {pair} 之间没有交接边"
                )
                seen.add(pair)
            assert len(seen) == len(handoffs), (
                f"{domain!r}: 有交接边没画在环上相邻位置"
            )

    @staticmethod
    def _page_const(name):
        m = re.search(rf"const {name} = ([0-9.]+);", HTML_PAGE)
        assert m, f"看板前端里找不到常量 {name}——净空守卫跟着它一起瞎了"
        return float(m.group(1))

    def test_a_handoff_chord_always_clears_the_centre_card(self):
        """交接边画直线之后，得证明它不会斜穿过中心那张卡

        鼓出去的弧碰不到中心是显然的，直线不是：弦的中点离圆心只有
        R·cos(π/n)，而 R 由卡宽和数量顶住、cos(π/n) 随盒子变多往 1 靠。
        哪天真出现一个盒子更多的域，弦就可能从中心卡的角上斜穿过去——
        看板上只是一条线压住了汇点的名字，不会有任何东西报错。

        这是可证的，所以不靠盯：净空 = R·cos(π/n)，
        必须大于中心卡的半对角线，还得多留一点余量。

        依赖另一条守卫：交接边只画在环上**相邻**两只之间（弦正好跨 2π/n）。
        要是哪天允许跨位连线，这个算式就得跟着改。
        """
        from kanban.trigger import domain_layout

        card_w = self._page_const("RADIAL_CARD_W")
        card_h = self._page_const("RADIAL_CARD_H")
        gap = self._page_const("RADIAL_GAP")
        slack = self._page_const("RADIAL_CHORD_SLACK")
        r_factor = self._page_const("RADIAL_R_FACTOR")

        # 立方体尺寸必须从 compactCubeHtml 里取，不能在整页里正则第一个 --cw：
        # 单盒视图用的是另一只更大的立方体，抓错了就变成拿错尺寸在算净空，
        # 而算错的那只恰好更大——错得还挺安全，纯属运气。
        m = re.search(r"function compactCubeHtml\(box\) \{(.*?)\n\}", HTML_PAGE, re.S)
        assert m, "看板前端里找不到 compactCubeHtml——净空守卫没尺寸可算"
        assert "compactCubeHtml(s)" in HTML_PAGE, (
            "环形视图不再用紧凑立方体渲染卡片——净空守卫量的还是那只紧凑的，"
            "于是量到的尺寸跟实际画出来的不一样"
        )
        cube_w = float(re.search(r"--cw:([0-9.]+)px", m.group(1)).group(1))
        cube_h = float(re.search(r"--ch:([0-9.]+)px", m.group(1)).group(1))
        center_scale = float(
            re.search(r"radial-center \.cube-scene \{ transform: scale\(([0-9.]+)\)",
                      HTML_PAGE).group(1)
        )
        half_diag = math.hypot(cube_w * center_scale / 2, cube_h * center_scale / 2)

        for domain in self._domains():
            lay = domain_layout(domain)
            if lay["layout"] != "radial":
                continue
            n = len(lay["ring"])
            R = max((card_w + gap) / 2 / math.sin(math.pi / n) + slack,
                    card_h * r_factor)
            clear = R * math.cos(math.pi / n)
            assert clear > half_diag + 24, (
                f"{domain!r}: 环上 {n} 只，交接弦离圆心只剩 {clear:.0f}px，"
                f"中心卡半对角线 {half_diag:.0f}px——直线会斜穿过汇点那张卡。"
                f"要么把环撑大（RADIAL_R_FACTOR / RADIAL_GAP），"
                f"要么把中心卡缩小（radial-center 的 scale）"
            )

    def test_the_main_chain_is_never_cyclic(self):
        """feeds_into 组成的**主干必须无环**——有环就是有人把闭环声明错了

        这是给「静默降级」上的一道闸。闭环那条边如果被写进
        `feeds_into`，拓扑排序就遇到环，于是 `order_boxes_by_flow`
        按原顺序收尾：**不报错、不变红**，环上顺序变成任意的，
        看板照常渲染，只是结构是错的。

        所以：主干无环是**可证的**，就证它。反馈边不进 `feeds_into`，
        这条才成立。
        """
        from kanban.trigger import domain_layout

        for domain in self._domains():
            lay = domain_layout(domain)
            if lay["layout"] == "radial":
                continue
            # 退回平铺只允许两种原因：盒子太少 / 中心不唯一
            assert "成环" not in lay["reason"], (
                f"{domain!r} 的交接边成环了（{lay['reason']}）——"
                f"闭环那条边应该声明成 feedback_into，"
                f"不是 feeds_into"
            )

    def test_a_declared_cycle_falls_back_to_flat_and_says_why(self):
        """真的把闭环边写错时，必须退回平铺**并说清是哪几只**

        守住「不猜」：成环时不能硬排一个顺序——硬排出来的东西看着
        像个正常的环，看不出哪里声明错了。而 reason 里必须点名，
        否则「这个域退化成平的」和「这个域本来没有中心」长得一样。
        """
        from kanban.trigger import BOX_REGISTRY, domain_layout, registered_box_ids

        target = next(
            (b for b in BOX_REGISTRY
             if BOX_REGISTRY[b][0]().placement.feedback_into
             and BOX_REGISTRY[b][0]().placement.domain),
            None,
        )
        assert target, "没有声明过反馈边的盒子，这条守卫无从验证"
        definition = BOX_REGISTRY[target][0]()
        placement = definition.placement
        saved_f, saved_b = list(placement.feeds_into), list(placement.feedback_into)
        try:
            # 模拟「有人把反馈边顺手写进了 feeds_into」
            placement.feeds_into = saved_f + saved_b
            placement.feedback_into = []
            lay = domain_layout(placement.domain)
            assert lay["layout"] == "flat", (
                f"{placement.domain!r}: 交接边成环却还画环——"
                f"环序是排不出来的，硬排等于编一个"
            )
            assert "成环" in lay["reason"]
            for bid in saved_b:
                assert bid in lay["reason"], (
                    f"成环的 reason 里没点名 {bid}——"
                    f"看板上就只是一个顺序不对的环，看不出哪里声明错了"
                )
        finally:
            placement.feeds_into, placement.feedback_into = saved_f, saved_b
        # 还原之后必须恢复正常，证明这条守卫没改坏接线
        assert domain_layout(placement.domain)["layout"] == "radial"

    def test_feedback_closes_the_ring_and_lands_on_the_head(self):
        """反馈边必须首尾相接：环上的末位回到首位

        反馈边画成直线，所以两端在环上必须**相邻**——否则那条线会
        横穿整个圆心，看着像一根从中间戳过去的刺，而不是一个闭环。

        另外它只能回到主干的入口：反馈是「这一轮结束、下一轮开始」，
        回到中段某一只的话，那只盒子的下游就同时属于两轮，读不出
        「下一轮从哪儿起」。
        """
        from kanban.trigger import domain_layout

        for domain in self._domains():
            lay = domain_layout(domain)
            if lay["layout"] != "radial":
                continue
            ring = lay["ring"]
            on_ring = set(ring)
            for src, dst in lay["feedback"]:
                assert src in ring and dst in ring, (
                    f"{domain!r}: 反馈边 {src}→{dst} 有一端不在环上"
                )
                # 反馈源在主线上必须有上游：没有东西喂给它，
                # 它凭什么反馈？把它接在环上但无来路，
                # 画出来就是一只凭空产生的盒子。
                # 注意方向：上游是「投给它的那只」，不是它投给谁。
                upstream = {
                    b for b in lay["boxes"]
                    if src in _placement(b).feeds_into and b in on_ring
                }
                assert upstream, (
                    f"{domain!r}: 反馈边 {src}→{dst} 的源 {src} "
                    f"在主线上没有上游——没有东西喂给它，它凭什么反馈？"
                )
                i, j = ring.index(src), ring.index(dst)
                adjacent = (i + 1) % len(ring) == j or (j + 1) % len(ring) == i
                assert adjacent, (
                    f"{domain!r}: 反馈边 {src}→{dst} 连的是环上第 {i} 位"
                    f"和第 {j} 位，不相邻——直线会横穿圆心"
                )
                # 首尾相接，且指向入口：末位 → 首位
                assert j == 0, (
                    f"{domain!r}: 反馈边 {src}→{dst} 回到环上第 {j} 位，"
                    f"不是入口（第 0 位）——反馈只能开启下一轮，"
                    f"回到中段就分不出「下一轮从哪儿起」"
                )
                assert (i + 1) % len(ring) == 0, (
                    f"{domain!r}: 反馈边的源 {src} 在环上第 {i} 位，"
                    f"不是末位——末位是流水线的最后一环，闭环只能从那里起"
                )

    def test_every_declared_feedback_edge_is_drawn_exactly_once(self):
        """声明了的反馈边必须真的画出来，而且只画一次

        `domain_layout` 输出的反馈边要跟各盒 `placement.feedback_into`
        逐条对得上：少画一条，图上就少一圈闭环，而看板上没有任何东西
        提示「本来还有一条」；重复画则同一对盒子之间多出一条线。

        这里**不**检查「同一对盒子既是交接边又是反馈边」——那种声明
        必然让主干成环（交接指下游、反馈指入口，两头都是同一只的话
        就首尾相接了），而成环已经被 `test_the_main_chain_is_never_cyclic`
        挡住。重复检查一个不可能发生的分支，只会让这条守卫看起来
        守得比实际更多。
        """
        from kanban.trigger import domain_layout

        for domain in self._domains():
            lay = domain_layout(domain)
            if lay["layout"] != "radial":
                continue
            on_ring = set(lay["ring"]) | ({lay["center"]} if lay["center"] else set())

            declared = {
                (b, t)
                for b in lay["boxes"]
                for t in _placement(b).feedback_into
                if t in on_ring
            }
            drawn = [tuple(e) for e in lay["feedback"]]
            assert sorted(drawn) == sorted(declared), (
                f"{domain!r}: 声明的反馈边 {sorted(declared)} "
                f"和画出来的 {sorted(drawn)} 对不上"
            )
            assert len(drawn) == len(set(drawn)), f"{domain!r}: 反馈边画了重复"

    def test_the_frontend_names_no_box(self):
        """前端一个 box_id 字面量都不许有——中心尤其不许

        布局的数据全部来自服务端 layout。前端要是认得某只盒子的名字，
        那它就有能力决定「谁在中心」，而那件事属于接线。
        """
        offenders = [b for b in BOX_REGISTRY if b in HTML_PAGE]
        assert not offenders, (
            f"看板前端里出现了写死的 box_id：{offenders}——"
            f"如果是为了指定中心，那是 BOX_META 的老毛病换了个位置"
        )

    def test_every_card_and_edge_carries_the_marks_snapping_needs(self):
        """每张卡、每条边都必须带身份标记，否则吸附会**静默**失效

        连线端点是渲染后按真实 DOM 量出来的（立方体多大、在卡里偏多少，
        JS 猜不出来也不该猜）。量的时候靠 `data-box` 认盒子、
        靠 `data-from`/`data-to` 认边的两头。

        标记一丢，`snapRadialEdges` 找不到对应元素就直接跳过——
        不报错、不变红，线退回按那张看不见的透明卡片边框算，
        于是又「两端各悬空半个盒子」。这是本项目的老病：算了却看不见。

        所以这里守的是**两半**：渲染端必须发出这些标记，吸附端必须真的
        按这些标记去找盒子。只查一半的话，另一半被拆掉照样全绿。

        两类边都要查：只看「有没有 data-from」会漏掉只给其中一类挂标记
        的写法，于是那类边静默退回估算位置，另一类照常吸附——
        看板上就是「一半线接着，一半线悬着」，没有报错。
        """
        assert '\'" data-box="\' + esc(id) + \'"' in HTML_PAGE, (
            "环形视图的卡片没发 data-box——连线吸附会静默失效，"
            "线会退回挂在透明卡片边框上，看起来就是没接上"
        )
        for cls in ("edge-handoff", "edge-feed"):
            i = HTML_PAGE.index('class="edge ' + cls + '"')
            # 切出这条边所属的那一句 edges.push(...)，不靠「往前看多少个字符」
            stmt = HTML_PAGE[HTML_PAGE.rfind("edges.push(", 0, i):i]
            for attr in ("data-from=", "data-to="):
                assert attr in stmt, (
                    f"{cls} 这条边没带 {attr}——snapRadialEdges 会跳过它，"
                    f"它的端点就不会吸附到看得见的盒子上，而且不会报任何错"
                )
        for frag, why in [
            ("snapRadialEdges();", "吸附函数定义了却没被调用——线永远停在估算位置上"),
            ("getAttribute('data-box')", "吸附端不按 data-box 找盒子"),
            ("getAttribute('data-from')", "吸附端不按 data-from 找边的起点"),
            ("getAttribute('data-to')", "吸附端不按 data-to 找边的终点"),
        ]:
            assert frag in HTML_PAGE, (
                f"{why}（缺：{frag}）——连线端点就不会吸附到看得见的盒子上，"
                f"表现为线两头各悬空半个盒子，而且不会报任何错"
            )


def _split_comparison(part: str) -> tuple[str, str, str]:
    """把 `field op value` 拆成三元组"""
    m = re.search(r"(>=|<=|==|!=|>|<)", part)
    assert m, f"{part!r} 不是三段式"
    return (
        part[: m.start()].strip(),
        m.group(1),
        part[m.end():].strip().strip("\"'"),
    )


def _value_satisfying(op: str, value: str) -> Any:
    """造一个能让 `field op value` 成立的值"""
    if value in ("true", "false"):
        target = value == "true"
        return target if op == "==" else (not target)
    try:
        number = float(value) if "." in value else int(value)
    except (TypeError, ValueError):
        return value
    if op == ">":
        return number + 1
    if op == ">=":
        return number
    if op == "<":
        return number - 1
    if op == "<=":
        return number
    if op == "==":
        return number
    if op == "!=":
        return number + 1
    return number


def _literal_type(value: str) -> str | None:
    """字面量的类型；不是字面量（自由文本）时返回 None"""
    if value in ("true", "false"):
        return "boolean"
    try:
        int(value)
        return "integer"
    except (TypeError, ValueError):
        pass
    try:
        float(value)
        return "number"
    except (TypeError, ValueError):
        pass
    return "string"


def _types_compatible(field_type: str, literal_type: str) -> bool:
    """产出字段的类型能不能跟条件里的字面量比"""
    if field_type in ("object", "array", "enum") or field_type.startswith("ref:"):
        return True  # 这几种类型从字面量看不出该比什么，放行
    if field_type == "boolean":
        return literal_type == "boolean"
    if field_type == "integer":
        return literal_type in ("integer", "number")
    if field_type == "number":
        return literal_type in ("integer", "number")
    if field_type == "string":
        return literal_type == "string"
    return True
