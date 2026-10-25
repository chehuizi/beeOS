"""
boxes.wms_development.codegen - 按设计包改代码（LLM 出改动，机械应用）

**为什么是「出改动」而不是「出整个文件」**：让模型重写整个文件，
它会把没关系的行也改一遍（格式、空格、import 顺序），diff 全是噪音，
review 的人/判据都看不出来。**让它给「精确片段替换」**：
每一处替换必须逐字命中原文，否则机械层直接拒绝并把错误喂回模型。

**为什么必须有隔离工作区**：开发盒会真的动文件。动的是 WMS 主仓的话，
一次跑失败的履约就把主仓弄脏了——而主仓是别人的工作现场。
所以：git worktree 起一个隔离工作区 → 在里面改 → 编译 → 提交 →
把提交落到主仓的一个分支上（**交付物必须能追回去**）。

**不降级**：模型不可用 / 改不动 / 编译不过 → 履约中止或判红，
不产出「看起来像改了」的制品。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

# 环境变量
WMS_REPO_ENV = "BEEOS_WMS_REPO"        # WMS 仓路径（没配 = 没地方改代码）
WORKSPACE_ENV = "BEEOS_DEV_WORKSPACE"   # 隔离工作区根目录

# 给模型看的源码上下文字数上限
MAX_CONTEXT_FILES = 12
MAX_CONTEXT_BYTES = 40_000
MAX_FILE_BYTES = 12_000

# 模型最多试几轮（每一轮失败都把错误原样喂回去）
MAX_CODGEN_ATTEMPTS = 3

# 残留工作区多久算过期（小时）——上一次崩在中间的工作区不许一直堆着
STALE_WORKSPACE_HOURS = 12


class CodegenError(RuntimeError):
    """改代码这一步做不下去（工作区/模型/应用任一环节失败）"""


def _run(cmd: list[str], cwd: Optional[Path] = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout,
    )


def repo_path() -> Path:
    """WMS 仓路径

    **没配就是没地方改代码**——不许猜一个默认路径（那是把
    「开发盒碰巧改到了某个仓」当成「开发盒按方案改了代码」）。
    """
    raw = (os.environ.get(WMS_REPO_ENV) or "").strip()
    if not raw:
        raise CodegenError(
            f"没配 {WMS_REPO_ENV}，开发盒不知道去哪儿改代码（履约中止，不是降级）"
        )
    path = Path(raw).expanduser()
    if not (path / ".git").exists():
        raise CodegenError(f"{WMS_REPO_ENV} 指向的不是 git 仓：{path}")
    return path


def workspace_root() -> Path:
    """隔离工作区的根目录"""
    raw = (os.environ.get(WORKSPACE_ENV) or "").strip()
    root = Path(raw).expanduser() if raw else Path(tempfile.gettempdir()) / "beeos-dev"
    root.mkdir(parents=True, exist_ok=True)
    return root


def prepare_workspace(base_ref: str = "HEAD", task_id: str = "") -> Path:
    """在隔离工作区里起一份代码（git worktree）

    **用 worktree 不用 clone**：共享对象库，起得快；
    失败时 `git worktree remove` 就干净了。

    工作区名字**跟着任务走**（不是随机名）：同一个任务重跑落在同一个目录，
    上一轮的残渣会被清掉，不会攒出一堆没人认领的目录。
    """
    repo = repo_path()
    root = workspace_root()
    ws = root / f"ws-{task_id or 'anon'}"
    if ws.exists():
        cleanup_workspace(ws)
        shutil.rmtree(ws, ignore_errors=True)

    ref = (base_ref or "").strip() or "HEAD"
    # 校验 base_ref 存在——不存在就该中止，不是拿 HEAD 顶替
    check = _run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo)
    if check.returncode != 0:
        raise CodegenError(f"基线提交不存在：{ref}（{check.stderr.strip()[:200]}）")

    add = _run(["git", "worktree", "add", "--detach", str(ws), ref], cwd=repo)
    if add.returncode != 0:
        raise CodegenError(f"起隔离工作区失败：{add.stderr.strip()[:300]}")
    return ws


def cleanup_workspace(ws: Optional[Path]) -> None:
    """拆掉隔离工作区（不留残渣在别人的仓里）"""
    if not ws or not ws.exists():
        return
    try:
        repo = repo_path()
    except CodegenError:
        return
    _run(["git", "worktree", "remove", "--force", str(ws)], cwd=repo)
    _run(["git", "worktree", "prune"], cwd=repo)
    shutil.rmtree(ws, ignore_errors=True)


def prune_stale_workspaces(max_age_hours: int = STALE_WORKSPACE_HOURS) -> list[str]:
    """清掉过期的工作区（上次崩在中间留下的）

    **不许清掉还在跑的**：判据是「目录的修改时间距今超过阈值」，
    一次跑几分钟，12 小时足够宽松。
    """
    root = workspace_root()
    cutoff = time.time() - max_age_hours * 3600
    removed: list[str] = []
    for child in root.glob("ws-*"):
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                cleanup_workspace(child)
                removed.append(child.name)
        except OSError:
            continue
    return removed


def collect_context(ws: Path, locations: list[str]) -> dict[str, str]:
    """把可能相关的源码喂给模型

    **按位置片段挑文件**，再截断——整个仓 150 个源文件全塞进去，
    模型既看不动也容易改错地方。
    """
    out: dict[str, str] = {}
    total = 0
    base = ws / "server" / "src" / "main"
    for root in (base / "java", base / "resources"):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in (".java", ".xml"):
                continue
            rel = str(path.relative_to(ws))
            if not any(loc in rel for loc in locations):
                continue
            if len(out) >= MAX_CONTEXT_FILES or total >= MAX_CONTEXT_BYTES:
                break
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if len(text) > MAX_FILE_BYTES:
                text = text[:MAX_FILE_BYTES] + "\n…（文件过长已截断）\n"
            out[rel] = text
            total += len(text)
    return out


_CODE_PROMPT = """你在一个 Java（Spring Boot + MyBatis）仓储管理系统（WMS）里改代码。

**要满足的业务需求**（来自设计盒，措辞是业务方的原话）：

{expectations}

**这次要落地的 WMS 概念**：

{concepts}

**必须在本次改动里新实现的概念**（机械查过：下面这些在源码里查不到实现
——**这就是本次的主要任务**）：

{missing}

**这些 WMS 概念在代码里的落点**（这是本仓的事实，不是你的猜测）：

{locations}

**相关源码**：

{files}
{feedback}
**你要做的**：按上面的需求改代码，让系统满足它。

规则：
1. 只用上面给出的源码里真实存在的片段。**不要编造 import、类名、方法名**
2. 改动要**最小**：只动实现这条需求必需的地方
3. 不许删测试、不许改测试来「让它过」——测试是判据的一部分
4. 不许改与这条需求无关的文件
5. **「必须新实现的概念」一条都不许漏**：每个都要在 edits 里给出它的实现
   （SQL / 字段 / 映射 / 装配，缺哪一环补哪一环）
6. 「已经实现」不是你的结论，是**机械查出来的事实**：
   只要「必须新实现的概念」清单非空，edits 就不能是空的。
   你可以额外说明哪些概念已有实现（给出文件名 + 标识符），但那只是补充说明

输出格式（只输出 JSON，不要解释、不要复述）：

{{"summary": "一句话说明改了什么",
  "edits": [{{"file": "server/src/main/java/.../Xxx.java",
             "old": "要被替换的原文片段（**逐字**复制，含缩进）",
             "new": "替换后的新片段"}}]}}

**old 必须在文件里逐字出现且只出现一次**——不唯一或找不到都会被机械层拒绝。
old 留空字符串**只**用于真正的新建文件（那个文件此刻还不存在）；
要改一个已经存在的文件，必须给出 old 片段（逐字复制）。
直接输出这段 JSON。"""


def build_prompt(expectations: list[dict[str, Any]], locations: list[str],
                 files: dict[str, str], feedback: str = "",
                 concepts: Optional[list[str]] = None,
                 must_implement: Optional[list[tuple[str, tuple[str, ...]]]] = None,
                 ) -> str:
    """把期望 + 落点 + 源码拼成 prompt"""
    exp_lines = []
    for e in expectations:
        exp_lines.append(
            f"- 概念 {e.get('wms_kind')}｜要求：{e.get('statement')}\n"
            f"  业务方原话：{e.get('evidence')}"
        )
    loc_lines = [f"- {loc}" for loc in locations] or ["（没有落点——这个概念在代码里还没有对应位置）"]
    file_blocks = []
    for path, text in files.items():
        file_blocks.append(f"===== {path} =====\n{text}")
    if feedback:
        fb = (
            "\n**上一次你给的改动没落下去，原因是：**\n"
            f"{feedback}\n\n"
            "**请修正后重新给出完整的 edits 数组**（不是补丁，是完整的数组）。\n"
        )
    else:
        fb = ""
    concept_lines = [f"- {c}" for c in (concepts or [])] or ["（没有概念）"]
    missing_lines = []
    for concept, tokens in (must_implement or []):
        tok = "、".join(tokens) if tokens else "（没有登记实现证据）"
        missing_lines.append(f"- {concept}：源码里找不到 {tok} → **这次要新增实现**")
    missing_text = "\n".join(missing_lines) or "（没有：本次涉及的每个概念源码里都已有实现）"
    return _CODE_PROMPT.format(
        expectations="\n".join(exp_lines) or "（没有预期）",
        concepts="\n".join(concept_lines),
        missing=missing_text,
        locations="\n".join(loc_lines),
        files="\n".join(file_blocks) or "（没有相关源码）",
        feedback=fb,
    )


def _resolve_edit_path(ws: Path, rel: str) -> Path:
    """把模型给的路径解析成工作区内的绝对路径

    **不许越出工作区**（模型给个 ../../etc/passwd 试试）。

    **模型给绝对路径也是合法的**（它看到的就是绝对路径）：
    但要按**同一个基准**比——工作区在 /tmp/beeos-dev/…，
    而 macOS 上 /tmp 是 /private/tmp 的软链，两边不 resolve 就会
    「同一个文件」被判成「越出工作区」。
    """
    root = ws.resolve()
    candidate = Path(rel)
    path = (candidate if candidate.is_absolute() else root / rel).resolve()
    if path != root and root not in path.parents:
        raise CodegenError(f"edit 指向工作区外：{rel}")
    return path


def _validate_edit(ws: Path, edit: Any) -> tuple[Path, str, str]:
    """校验一处替换（不写盘）

    校验和写盘**分成两步**：这样「三条改动里第二条落不下去」时，
    第一条不会留在盘上——重试才是干净的。
    """
    if not isinstance(edit, dict):
        raise CodegenError(f"edit 不是对象：{edit!r}")
    rel = str(edit.get("file") or "").strip()
    old = edit.get("old")
    new = edit.get("new")
    if not rel or not isinstance(old, str) or not isinstance(new, str):
        raise CodegenError(f"edit 缺字段：{edit!r}")
    path = _resolve_edit_path(ws, rel)
    if old == "":
        # 新建文件：文件不许已经存在
        if path.exists():
            raise CodegenError(
                f"{rel}：old 留空表示新建文件，但这个文件已经存在——"
                "要改已有文件，必须给出要替换的原文片段（old 逐字复制，"
                "且在文件里只出现一次）；只有真正的新文件才把 old 留空"
            )
        return path, old, new
    if not path.exists():
        raise CodegenError(f"edit 指向的文件不存在：{rel}")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise CodegenError(f"{rel}：找不到要替换的原文片段（old 逐字命中 0 次）")
    if count > 1:
        raise CodegenError(f"{rel}：要替换的原文片段出现 {count} 次（必须唯一）")
    return path, old, new


def apply_edits(ws: Path, edits: list[dict[str, Any]]) -> list[str]:
    """把模型给的「精确片段替换」应用到工作区

    机械层只做三件事：文件在不在、old 是不是**只出现一次**、替换后写回。
    任何一条不满足就抛错——**让错误原样回给模型去改**，
    别自己脑补一段「差不多」的代码。

    **先全部校验、再全部写盘**：半途失败留下的半截改动会让重试变成叠加。
    """
    planned: list[tuple[Path, str, str]] = []
    for edit in edits:
        planned.append(_validate_edit(ws, edit))

    applied: list[str] = []
    root = ws.resolve()
    for path, old, new in planned:
        if old == "":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new, encoding="utf-8")
        else:
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
        applied.append(str(path.relative_to(root)))
    return applied


def generate_edits(
    ws: Path,
    expectations: list[dict[str, Any]],
    locations: list[str],
    files: dict[str, str],
    llm_call: Optional[Callable[[str], Any]] = None,
    max_attempts: int = MAX_CODGEN_ATTEMPTS,
    concepts: Optional[list[str]] = None,
    must_implement: Optional[list[tuple[str, tuple[str, ...]]]] = None,
    verify: Optional[Callable[[], list[str]]] = None,
    refresh: Optional[Callable[[], dict[str, str]]] = None,
) -> tuple[list[str], str]:
    """让模型出改动并落下去；落不下去就把错误原样回给它改

    **不降级**：模型连续几轮都改不动 → 履约中止。
    绝不「自己脑补一段差不多的代码」——那是假交付。

    **「返回空 edits」不许是免费的出口**：如果机械查过有概念还没实现
    （must_implement 非空），模型交白卷就是没干活——把这件事原样回给它，
    它得给出实现。这条是上一轮真跑撞出来的：模型说「看板已经实现了」，
    而源码里连 pickAccuracy 都没有。

    **改完还要再核一遍**（verify）：模型说它实现了某个概念不算数——
    源码里得真的出现那个概念的标识符。不出现就把这句话回给它重改。

    **重试时上下文要刷新**（refresh）：上一轮的改动**留在工作区里**，
    而且下一轮看到的源码就是**当前**内容。
    这不是细节——上一轮真跑就卡在这儿：模型用了新 VO 却忘了 import，
    编译器报「找不到符号」（指的是它改过的文件），而它下一轮看到的却是
    **改动前**的文件。它根本看不见自己写错的那一行，自然修不了。
    """
    if llm_call is None:
        from runtime.llm import complete_json

        def llm_call(prompt: str) -> Any:  # type: ignore[misc]
            return complete_json(
                prompt, thinking="disabled", max_tokens=6000, timeout=180,
            )

    feedback = ""
    last_error = ""
    for attempt in range(max_attempts):
        if attempt and refresh is not None:
            files = refresh()
        prompt = build_prompt(
            expectations, locations, files, feedback=feedback,
            concepts=concepts, must_implement=must_implement,
        )
        raw = llm_call(prompt)
        if not isinstance(raw, dict):
            last_error = f"模型输出不是一个对象：{type(raw).__name__}"
            feedback = last_error
            continue
        edits = raw.get("edits")
        summary = str(raw.get("summary") or "")
        if not isinstance(edits, list):
            last_error = "输出里没有 edits 数组"
            feedback = last_error
            continue
        if not edits and must_implement:
            names = "、".join(c for c, _ in must_implement)
            last_error = (
                f"你返回了空的 edits，但这些概念在源码里查不到实现：{names}——"
                "本次的任务就是把它们实现出来"
            )
            feedback = last_error
            continue
        try:
            applied = apply_edits(ws, edits)
        except CodegenError as exc:
            last_error = str(exc)
            feedback = last_error
            continue
        if verify is not None:
            problems = verify()
            if problems:
                last_error = (
                    "你这一轮的改动没通过机械核对：\n"
                    + "\n".join(problems)
                    + "\n\n请按上面的问题修正后，重新给出完整的 edits 数组。"
                    "\n（工作区里已经保留了你上一轮的改动，下面的源码就是**当前**内容——"
                    "请在当前内容上做增量修改，old 要逐字取自当前内容。）"
                )
                feedback = last_error
                continue
        return applied, summary
    raise CodegenError(
        f"模型连续 {max_attempts} 次给出的改动都落不下去：{last_error}"
    )


def changed_files(ws: Path) -> list[str]:
    """工作区里被改动的文件（相对 HEAD）"""
    out = _run(["git", "status", "--porcelain"], cwd=ws)
    if out.returncode != 0:
        return []
    files = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # porcelain 格式：XY path（重命名是 "R  old -> new"）
        path = line.split(" -> ")[-1].strip() if " -> " in line else line[3:].strip()
        if path:
            files.append(path)
    return files


def commit_changes(ws: Path, message: str) -> tuple[Optional[str], Optional[str]]:
    """在工作区里提交，并把提交落到主仓的一个分支上

    **交付物必须能追回去**：制品里的 digest 要能指到一个具体的提交，
    所以提交不能只活在临时工作区里——要挂到主仓的分支上。

    Returns: (commit_sha, branch)；没提交成返回 (None, None)
    """
    _run(["git", "add", "-A"], cwd=ws)
    _run(["git", "-c", "user.name=beeos", "-c", "user.email=beeos@local",
          "commit", "-m", message], cwd=ws)
    sha = _run(["git", "rev-parse", "HEAD"], cwd=ws)
    if sha.returncode != 0:
        return None, None
    commit_sha = sha.stdout.strip()
    repo = repo_path()
    branch = f"beeos/dev-{commit_sha[:8]}"
    created = _run(["git", "branch", "-f", branch, commit_sha], cwd=repo)
    if created.returncode != 0:
        return commit_sha, None
    return commit_sha, branch
