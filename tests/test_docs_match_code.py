"""tests/test_docs_match_code.py — 文档与代码的对齐守卫

文档落后是必然的：代码在演进，文档靠人记。除非有东西盯着。

这条守卫盯五件**结构事实**（不盯散文措辞——措辞会漂，事实不会）：

1. **路径存在性**——文档里提到的仓库路径（``boxes/...`` / ``runtime/...`` 等）必须真实存在
2. **导入可解析**——文档里的 ``from boxes.x import y`` 必须真的能 import，且名字存在
3. **README 覆盖全部注册盒子**——新盒子注册进 BOX_REGISTRY，README 必须提到它
4. **退休名不得复活**——改名前的目录名 / 已删除的模块名一旦在文档里回来，文档就在骗人
5. **盒子的 README 里的条数必须等于实际条数**——判据 8 条就写 8 条，不写 5 条

第 5 条是从真实事故来的：``boxes/wms_design/README.md`` 写着「5 项机械 acceptance」，
实际是 8 条；写着「3 exception rules」，实际是 4 条。数字对不上，读者照着文档验收就会漏。

设计约束（跟本仓其它守卫同族）：
- **每条声明配一个会红的守卫**——本文件里的每条规则都能在历史上抓出真问题
- **不许只查词在不在**——路径要真的能打开，模块要真的能 import
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

# 扫描文档时跳过的目录（vendor 里有一堆第三方 README）
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "vendor", "dist", "build", ".pytest_cache"}

# 文档里提到的路径，只有这些前缀算「仓库内路径」——别的（如 logs/、flows/）不管
PATH_PREFIXES = ("boxes", "runtime", "core", "kanban", "tests", "docs", "scripts", "beelines")

# 改名前 / 已删除的名字：文档里再出现就是在骗人
RETIRED_NAMES = (
    "mock_runner",
    "beelines/",
    "from beelines",
    "import beelines",
    "boxes/modeling",
    "boxes/requirement_capture",
    "boxes.modeling",
    "boxes.requirement_capture",
    "test_modeling_box",
    "test_modeling_binder",
    "test_modeling_expectations",
    "test_capture_box",
    "test_capture_extraction",
)

# 已经搬走 / 拆掉的模块名：import 它 = 文档在教人一条走不通的路
RETIRED_MODULES = ("beelines",)

# 路径 token：允许前缀目录 / 相对路径 / 通配
PATH_TOKEN = re.compile(
    r"(?:\.\./)*(?:\./)?(?:" + "|".join(PATH_PREFIXES) + r")/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*"
)
# 裸测试文件名（没有 tests/ 前缀）
TEST_FILE_TOKEN = re.compile(r"\btest_[a-z0-9_]+\.py\b")
# 内联代码里的 import
INLINE_IMPORT = re.compile(r"\bfrom\s+([A-Za-z_][\w.]*)\s+import\s+([A-Za-z_][\w,\s]*)")

# 「N 条判据 / N 项 acceptance / N exception」——用于核对盒子的实际条数
COUNT_PHRASE = re.compile(
    r"(\d+)\s*(?:条|项|个|条机械)?\s*(?:机械\s*)?"
    r"(判据|验收|例外|acceptance|acceptance rules?|exception|exception rules?)",
    re.IGNORECASE,
)


def _md_files() -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*.md"):
        if SELF in p.parents:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def _code_spans(text: str) -> list[str]:
    """内联代码片段（单行反引号）。"""
    return re.findall(r"`([^`\n]+)`", text)


def _python_blocks(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, re.S)


# --------------------------------------------------------------------------
# 1. 路径存在性
# --------------------------------------------------------------------------
@pytest.mark.parametrize("md", _md_files(), ids=_rel)
def test_md_referenced_paths_exist(md: Path) -> None:
    text = md.read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for token in PATH_TOKEN.findall(line):
            if any(ch in token for ch in "*<>"):
                continue  # 通配 / 占位符，不是真实路径
            raw = token
            if raw.startswith(("./", "../")):
                target = (md.parent / raw).resolve()
            else:
                target = (ROOT / raw).resolve()
            if not target.exists():
                bad.append(f"{_rel(md)}:{lineno}: 文档提到 {raw} —— 仓库里没有这个路径")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("md", _md_files(), ids=_rel)
def test_md_referenced_test_files_exist(md: Path) -> None:
    """文档里提到的测试文件名必须真实存在（裸文件名也算）。"""
    text = md.read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name in set(TEST_FILE_TOKEN.findall(line)):
            if not (ROOT / "tests" / name).exists():
                bad.append(f"{_rel(md)}:{lineno}: 文档提到测试文件 {name} —— tests/ 下没有")
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 2. 导入可解析
# --------------------------------------------------------------------------
def _check_import(module: str, names: list[str], where: str, bad: list[str]) -> None:
    root = module.split(".")[0]
    if root in RETIRED_MODULES:
        bad.append(f"{where}: `from {module} import ...` —— 这个模块已经不存在了")
        return
    if root not in {"boxes", "runtime", "core", "kanban"}:
        return
    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 — 文档里的 import 坏了就是坏了，报原样
        bad.append(f"{where}: `from {module} import ...` —— 真的 import 失败：{exc}")
        return
    for name in names:
        if not hasattr(mod, name):
            bad.append(f"{where}: `from {module} import {name}` —— {module} 里没有 {name}")


@pytest.mark.parametrize("md", _md_files(), ids=_rel)
def test_md_imports_are_resolvable(md: Path) -> None:
    text = md.read_text(encoding="utf-8")
    bad: list[str] = []

    for lineno, line in enumerate(text.splitlines(), 1):
        for module, names in INLINE_IMPORT.findall(line):
            _check_import(
                module,
                [n.strip() for n in names.split(",") if n.strip()],
                f"{_rel(md)}:{lineno}",
                bad,
            )

    for block in _python_blocks(text):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue  # 片段不是完整 python，跳过
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [a.name for a in node.names]
                _check_import(node.module, names, _rel(md), bad)

    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 3. README 覆盖全部注册盒子
# --------------------------------------------------------------------------
def _registered_box_ids() -> list[str]:
    from kanban.trigger import BOX_REGISTRY

    return sorted(BOX_REGISTRY)


def test_readme_mentions_every_registered_box() -> None:
    """README 必须提到每一只已注册的盒子。

    **按整词匹配**：子串不算。`test_wms_ops_box.py` 里含有 `wms_ops_box`，
    但那不是「提到了这只盒子」——用子串判定会被文件名掩盖掉。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [
        b
        for b in _registered_box_ids()
        if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(b)}(?![A-Za-z0-9_])", readme)
    ]
    assert not missing, f"README.md 没提到这些已注册盒子：{missing}"


def test_every_box_dir_has_a_readme() -> None:
    missing = [name for name in _box_dirs() if not (ROOT / "boxes" / name / "README.md").exists()]
    assert not missing, f"这些盒子目录没有 README.md：{missing}"


def _box_dirs() -> list[str]:
    return [
        d.name
        for d in sorted((ROOT / "boxes").iterdir())
        if d.is_dir() and not d.name.startswith("__")
    ]


def test_box_readme_mentions_its_own_box_id() -> None:
    """盒子的 README 必须能对上是哪只盒子（靠 box_id，不靠目录名猜）。

    box_id 从本目录的 definition 里读——**不查注册表**：未注册的盒子（代码保留、
    看板不显示）也得有自己的 README，不能因为没注册就没人管。
    """
    bad: list[str] = []
    for name in _box_dirs():
        readme = ROOT / "boxes" / name / "README.md"
        if not readme.exists():
            continue
        module = importlib.import_module(f"boxes.{name}")
        box_id = module.get_definition().id
        if box_id not in readme.read_text(encoding="utf-8"):
            bad.append(f"boxes/{name}/README.md 没提到自己的 box_id（{box_id}）")
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 4. 退休名不得复活
# --------------------------------------------------------------------------
@pytest.mark.parametrize("md", _md_files(), ids=_rel)
def test_md_has_no_retired_names(md: Path) -> None:
    text = md.read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name in RETIRED_NAMES:
            if name in line:
                bad.append(f"{_rel(md)}:{lineno}: 退休名 `{name}` 出现在文档里 —— 这个东西已经不存在了")
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 6. README 不许写死测试规模
# --------------------------------------------------------------------------
# 为什么不写死数字而写个守卫去核对数字？
# 因为核对数字这条规则**自己就是漂移源**：并行会话加一条测试，README 就过期，
# 红的是我的守卫、活的是别人的代码——这种守卫最后只会被人加白名单。
# 所以这里反过来：**README 不许写死条数**，规模用「量级」表述。
# 真实的条数由 pytest 自己报，文档不重复一份会过期的事实。
_STALE_COUNT = re.compile(
    r"\d+\s*(?:个测试文件|个测试|个用例|条(?:测试|用例|守卫)|tests?\b)",
    re.IGNORECASE,
)


def test_readme_has_no_hardcoded_test_count() -> None:
    """README 里出现「120 个测试通过」这种话，就是一份会过期的事实。

    曾经的真实事故：README 写着「120 个测试通过」，实际已经 960 条。
    现在的规则：**不写**——规模用量级表述，真实条数由 pytest 报。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(readme.splitlines(), 1):
        for hit in _STALE_COUNT.findall(line):
            bad.append(f"README.md:{lineno}: 「{hit.strip()}」——测试规模不许写死数字")
    assert not bad, "\n".join(bad)
def _box_facts() -> dict[str, tuple[int, int]]:
    """目录 → (判据条数, 例外条数)。按目录读 definition，不靠目录名猜。"""
    facts: dict[str, tuple[int, int]] = {}
    for name in _box_dirs():
        module = importlib.import_module(f"boxes.{name}")
        d = module.get_definition()
        facts[name] = (len(d.result.acceptance), len(d.result.exceptions or []))
    return facts


_COUNT_KIND = {
    "判据": 0,
    "验收": 0,
    "acceptance": 0,
    "acceptance rule": 0,
    "acceptance rules": 0,
    "例外": 1,
    "exception": 1,
    "exception rule": 1,
    "exception rules": 1,
}


@pytest.mark.parametrize("box_dir", sorted(_box_facts()), ids=lambda d: d)
def test_box_readme_counts_match_definition(box_dir: str) -> None:
    """盒子 README 里写的「N 条判据 / N 条例外」必须等于 definition 里的实际条数。

    只在**盒子自己的 README** 上生效——根 README 的表格里有多只盒子，数字不带归属，
    拿不准就不查（拿不准就判红是给判据的规矩，这里是给人看的文档，规矩不一样）。
    """
    facts = _box_facts()
    if box_dir not in facts:
        pytest.skip(f"{box_dir} 不是已注册盒子")
    acc_n, exc_n = facts[box_dir]
    readme = ROOT / "boxes" / box_dir / "README.md"
    if not readme.exists():
        pytest.skip(f"boxes/{box_dir}/README.md 不存在")

    allowed = {0: {acc_n}, 1: {exc_n}}
    bad: list[str] = []
    for lineno, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), 1):
        for number, word in COUNT_PHRASE.findall(line):
            kind = _COUNT_KIND.get(word.lower().strip())
            if kind is None:
                continue
            if int(number) not in allowed[kind]:
                label = "判据" if kind == 0 else "例外"
                bad.append(
                    f"boxes/{box_dir}/README.md:{lineno}: 写了「{number} {word}」，"
                    f"实际是 {acc_n} 条判据 / {exc_n} 条例外"
                )
    assert not bad, "\n".join(bad)
