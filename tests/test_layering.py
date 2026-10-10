"""tests.test_layering - 内核不许持有业务知识

分层约束（一句）：`runtime/` 是内核，`boxes/` 是产品。
**业务语义必须真的，基础设施可以假的**——但反过来不成立：
内核不该懂任何一只具体盒子的业务。

以前 `runtime/mock_runner.py` 里塞了三只盒子的全部 op 实现，
包括建模盒的 DDD 战术模式映射（object→entity / rule→specification…）。
结果是内核比任何一只盒子都懂业务，而且盒子带不走自己的实现——
它只是个声明，不是可独立交付的单元。

这组测试把边界钉死。违规的表现形式通常是：
- 内核 import 某个盒子
- 内核的常量表里出现某只盒子的 op 名 / 业务词
- 盒子之间互相 import

靠人 review 守不住这种边界：搬家时顺手 `import` 一下就漏了。
"""

from __future__ import annotations

import ast
import pathlib

import pytest


REPO = pathlib.Path(__file__).resolve().parent.parent
RUNTIME_DIR = REPO / "runtime"
BOXES_DIR = REPO / "boxes"


def _py_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
    return out


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """所有 docstring 节点的 id()——它们是文档，不是实现"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _code_strings_and_names(path: pathlib.Path) -> tuple[list[str], list[str]]:
    """文件里「代码部分」用到的字符串字面量和标识符名（不含 docstring）

    分层边界只管代码。注释和 docstring 里出现业务词是在解释机制，
    不构成内核持有业务知识——用纯文本 grep 会把这些一起算成违规，
    测出来的就只剩「少写注释」，那不是这条边界要的东西。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docs = _docstring_nodes(tree)
    strings: list[str] = []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docs:
                strings.append(node.value)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
    return strings, names


# 任何盒子出现过的 op 名（从三只盒子的 runner 里读出来，不手抄——手抄会漂）
def _all_box_op_types() -> set[str]:
    from boxes.inventory_shortage.runner import INVENTORY_HANDLERS
    from boxes.modeling.runner import MODEL_HANDLERS
    from boxes.requirement_capture.runner import CAPTURE_HANDLERS
    return set(INVENTORY_HANDLERS) | set(MODEL_HANDLERS) | set(CAPTURE_HANDLERS)


# 业务词表：出现在内核里就说明业务语义漏下去了。
# 刻意选「业务概念」而不是「变量名」——变量名可以中性，业务概念不行。
BUSINESS_WORDS = (
    "DDD_PATTERNS",
    "domain_service",
    "domain_event",
    "domain_metric",
    "specification",
    "business_goal",
    "requirement_type",
    "source_coverage",
    "no_hallucination",
    "model_elements",
    "resolution_type",
    "customer_notified",
    "inventory_shortage",
    "安全库存",
    "需求",
)


# 跨盒 import 的登记簿。每条都要写清为什么可以豁免、怎么还。
#
# 捕获盒 → 建模盒：捕获盒的产出必须满足建模盒的输入契约，而
# schema 归消费者所有，所以它得去读建模盒声明的 schema，
# 不能自己另立一套同形状的定义。这是「捕获盒换 LLM 建模盒零改动」的根据。
# 代价：删掉建模盒，捕获盒 import 不动。要消除得把契约引用声明化
#（捕获盒 definition 里写下游 box_id，由解析层查），本轮记债。
KNOWN_CROSS_BOX_IMPORTS = {
    "boxes/requirement_capture/runner.py → boxes.modeling",
}


class TestKernelHasNoBusinessKnowledge:
    def test_runtime_imports_no_box(self):
        """内核不许依赖任何盒子

        反向依赖（boxes → runtime）是正常的；内核 → boxes 会让盒子
        变成内核的一部分，删不掉也搬不走。
        """
        offenders = {
            p.relative_to(REPO).as_posix(): [m for m in _imported_modules(p)
                                              if m.startswith("boxes.")]
            for p in _py_files(RUNTIME_DIR)
        }
        offenders = {k: v for k, v in offenders.items() if v}
        assert offenders == {}, f"内核反向依赖了盒子：{offenders}"

    def test_runtime_carries_no_box_op_names(self):
        """内核里不许出现任何一只盒子的 op 名

        以前 MOCK_HANDLERS 那张表把三只盒子的 20 个 op 全收在内核。
        只查代码里的字符串字面量，注释里举例说明不算。
        """
        box_ops = _all_box_op_types()
        hits = []
        for p in _py_files(RUNTIME_DIR):
            strings, names = _code_strings_and_names(p)
            for op in box_ops:
                if op in names or any(op in s for s in strings):
                    hits.append(f"{p.relative_to(REPO).as_posix()}: {op}")
        assert hits == [], "内核里出现了业务 op 名：\n  " + "\n  ".join(hits)

    def test_runtime_carries_no_business_vocabulary(self):
        """内核的代码里不许出现业务概念名

        中文注释解释机制（"说明上游没产出这些量"）不在此列——
        那是文档，不是实现。
        """
        hits = []
        for p in _py_files(RUNTIME_DIR):
            strings, names = _code_strings_and_names(p)
            for word in BUSINESS_WORDS:
                if word in names or any(word in s for s in strings):
                    hits.append(f"{p.relative_to(REPO).as_posix()}: {word}")
        assert hits == [], "内核代码里出现了业务语义：\n  " + "\n  ".join(hits)


class TestBoxIsSelfContained:
    def test_each_runner_carries_its_own_ops(self):
        """每只盒子都带得走自己的 op 实现"""
        from boxes.inventory_shortage.runner import INVENTORY_HANDLERS, RUNNER as INV
        from boxes.modeling.runner import MODEL_HANDLERS, RUNNER as MOD
        from boxes.requirement_capture.runner import CAPTURE_HANDLERS, RUNNER as CAP

        for handlers, runner, name in [
            (INVENTORY_HANDLERS, INV, "inventory_shortage"),
            (MODEL_HANDLERS, MOD, "business_modeling_box"),
            (CAPTURE_HANDLERS, CAP, "requirement_capture_box"),
        ]:
            assert handlers, f"{name} 没有 op 实现"
            assert runner.owner == name
            for op in handlers:
                assert runner.supports(op), f"{name} 的 RUNNER 不认自己的 op {op}"

    def test_a_box_does_not_import_another_box(self):
        """盒子之间不许出现未登记的互相 import

        它们在同一条价值流上，但那是 feeds_into 声明的关系，不是代码依赖。
        代码依赖会让「删掉下游盒」变成不可能。
        """
        offenders = self._cross_box_imports()
        unexpected = [o for o in offenders if o not in KNOWN_CROSS_BOX_IMPORTS]
        assert unexpected == [], (
            "盒子之间出现了未登记的互相 import：\n  " + "\n  ".join(unexpected)
        )

    def test_known_cross_box_imports_are_still_owed(self):
        """已登记的例外不许悄悄扩大，也不许悄悄消失

        捕获盒 import 建模盒有正当理由：schema 归消费者所有，
        捕获盒的产出必须满足建模盒的输入契约，不自己另立一套——
        这是「捕获盒换 LLM 建模盒零改动」的根据。

        但它确实是跨盒代码依赖：删掉建模盒，捕获盒就 import 不了。
        正解是把契约引用声明化（捕获盒 definition 里写下游 box_id，
        由解析层去查），代价是要动契约解析。本轮先记债不动手，
        但债不许越欠越多——集合对不上就红。
        """
        offenders = set(self._cross_box_imports())
        assert offenders == KNOWN_CROSS_BOX_IMPORTS, (
            "跨盒 import 集合变了。\n"
            f"  现在: {sorted(offenders)}\n"
            f"  登记: {sorted(KNOWN_CROSS_BOX_IMPORTS)}\n"
            "新增的要么消除，要么在 KNOWN_CROSS_BOX_IMPORTS 里登记理由。"
        )

    def _cross_box_imports(self) -> list[str]:
        box_names = {
            p.stem for p in BOXES_DIR.iterdir()
            if p.is_dir() and p.name != "__pycache__"
        }
        offenders = []
        for p in _py_files(BOXES_DIR):
            # boxes/__init__.py 是包导出，把所有盒子列出来是它的职责
            if p.name == "__init__.py" and p.parent == BOXES_DIR:
                continue
            own = p.relative_to(BOXES_DIR).parts[0]
            for mod in _imported_modules(p):
                if mod.startswith("boxes."):
                    target = mod.split(".")[1]
                    if target != own and target in box_names:
                        offenders.append(
                            f"{p.relative_to(REPO).as_posix()} → {mod}"
                        )
        return offenders

    def test_executor_refuses_to_guess_a_runner(self):
        """executor 不能再兜底一张「什么都认识」的表

        它兜底过，那张表塞满三只盒子的实现。兜底的代价是
        加盒子必须改内核，且盒子带不走自己的实现。
        """
        from runtime.executor import BeelineExecutor

        with pytest.raises(ValueError, match="requires a runner from the box"):
            BeelineExecutor(None)


class TestRegistry:
    def test_every_registered_box_has_a_runner(self):
        from kanban.trigger import BOX_REGISTRY, RUNNER_REGISTRY

        assert set(BOX_REGISTRY) <= set(RUNNER_REGISTRY), (
            "注册进 BOX_REGISTRY 的盒子必须同时注册 runner，"
            "否则它接不到履约"
        )

    def test_runner_handles_every_op_in_its_beeline(self):
        """盒子声明的每条 beeline，它的 runner 都得能跑

        声明了却跑不了 = 又一族的「声明了却拿不到」。
        """
        from kanban.trigger import BOX_REGISTRY, RUNNER_REGISTRY

        for box_id, (get_def, beeline_loaders) in BOX_REGISTRY.items():
            runner = RUNNER_REGISTRY[box_id]
            definition = get_def()
            for task in definition.task:
                beeline = beeline_loaders[task.beeline_id]()
                for op in beeline.operations:
                    assert runner.supports(op.type), (
                        f"{box_id}: beeline {beeline.id} 声明了 op "
                        f"{op.type!r}，但它的 runner 不认"
                    )

    def test_a_runner_never_runs_another_boxes_ops(self):
        """跨盒执行要显式换 runner，不许一只盒子的 runner 顺手跑别人的 op"""
        from boxes.modeling.runner import RUNNER as MOD
        from boxes.requirement_capture.runner import RUNNER as CAP

        assert not MOD.supports("requirement_extractor")
        assert not CAP.supports("model_generator")