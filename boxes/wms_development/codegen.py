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
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

# 环境变量
WMS_REPO_ENV = "BEEOS_WMS_REPO"        # WMS 仓路径（没配 = 没地方改代码）
WORKSPACE_ENV = "BEEOS_DEV_WORKSPACE"   # 隔离工作区根目录

# 给模型看的源码上下文字数上限
#
# **为什么从 12 提到 20**（2026-10-27 实测）：
# 原来 12 是「改一处」的量级——改一个指标只要看 DashboardVO + Mapper。
# 但**新建一个模块**要同时看到：service 实现、controller、mapper 接口、
# mapper XML、VO、DTO、迁移脚本，一整套模板。
# 而 collect_context 是 `java` 先扫、`resources` **后扫**——
# 文件数一超限，被挤掉的就是**最后**进来的那批：
# XML 模板和迁移模板恰恰在里面。
# 于是模型拿着「照着谁写」的答案写不出 SQL 和建表脚本——
# 编译能过（SQL 是字符串），上线才炸（表根本不存在）。
MAX_CONTEXT_FILES = 40
MAX_CONTEXT_BYTES = 180_000
MAX_FILE_BYTES = 20_000

#: 仓里的**源码根**及各自的后缀
#:
#: **为什么要加 web/src**：前端也是仓的一部分。
#: 开发盒以前只看后端 → 它**看不见也改不了**前端，
#: 于是「库区管理」这类需求交出来的永远只有接口、没有页面——
#: **业务方点的是页面**。
#:
#: 三个根分别扫，是因为后缀不同（java / xml+sql / vue+ts），
#: 但**采集的规矩对两边一样**：按落点分桶、轮转着取。
SOURCE_ROOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("server/src/main/java", (".java",)),
    ("server/src/main/resources", (".xml", ".sql")),
    ("web/src", (".vue", ".ts")),
)

# 模型最多试几轮（每一轮失败都把错误原样喂回去）
MAX_CODGEN_ATTEMPTS = 3

# 一次交付要写多个文件时，模型分几轮写（每轮落盘后机械层再问下一个）
#
# **为什么不是一轮写完**：一次要交付的东西一多（新建一个模块 = 十几个文件），
# 模型的输出会**超上限** → JSON 截断 → 解析失败 → 整个履约中止。
# 分轮写，每轮输出都小；而且机械层能知道「写到哪个文件了」。
# **轮数要够写完一个模块**：新建一个模块 = 实体 + Mapper + XML + 迁移 +
# DTO + VO + Service + Controller + 库位那一侧的改动 = 十来个文件，
# 按「一次 1-2 个文件」就是七八轮，再加上编译失败返工、证据不满足返工，
# 12 轮不够用（实测：模型写完 11 个文件，轮数用完了，
# 库位那一侧的需求**还没轮到它写**）。
# 每轮只花几秒（LLM 调用 2-5 秒），轮数多一点不心疼。
MAX_CODEGEN_ROUNDS = 30

# 单次 LLM 调用的输出上限（token）
#
# 从 6000 提到 12000：一轮写 1-2 个文件用不了这么多，
# 但**模型偶尔会一次吐很多**（规则里写了「最多 2 个文件」它也可能不听）。
# 上限留够余量，它就不会在 JSON 中间被截断。
MAX_CODGEN_TOKENS = 12_000

# 残留工作区多久算过期（小时）——上一次崩在中间的工作区不许一直堆着
STALE_WORKSPACE_HOURS = 12


class CodegenError(RuntimeError):
    """改代码这一步做不下去（工作区/模型/应用任一环节失败）"""


def _log(message: str) -> None:
    """把进度打到 stderr——**一次开发盒要跑十几分钟**，不看进度就只能干等

    不打 stdout：那是履约结果的通道，混进日志会污染 JSON 输出。
    """
    print(f"[codegen {time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


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
    _link_frontend_deps(ws, repo)
    return ws


def _link_frontend_deps(ws: Path, repo: Path) -> None:
    """把主仓的 ``web/node_modules`` 软链进隔离工作区

    **为什么必须做**：worktree 里只有 git 跟踪的文件，
    ``node_modules`` 是 gitignore 的 → 工作区里**没有依赖**，
    ``npm run build`` 直接失败（vite 找不到）。
    而 ``npm ci`` 要联网、下一次几分钟——链每跑一轮就装一次，
    既慢又不稳（本机没网/registry 不通时会直接把链打断）。

    软链一份主仓现成的依赖：**只读共享**，不复制、不污染主仓。

    **主仓没有依赖就不链**——那种情况下前端构建这一跳会报
    「环境不具备」，而不是假装构建过了。
    """
    src = repo / "web" / "node_modules"
    dst = ws / "web" / "node_modules"
    if not src.exists() or dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.symlink_to(src, target_is_directory=True)
    except OSError:
        # 软链失败不是致命的：前端构建那一跳会自己报「环境不具备」
        pass


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

    **两个必须做对的点**（2026-10-28 实测撞出来的）：

    1. **`.sql` 也要收**。迁移脚本以前根本不在上下文里 →
       模型既**看不到仓里已有的 V1~V4**（于是自己起名 `V1__create_wms_core_tables.sql`，
       版本号跟已有文件撞了，Flyway 启动时直接拒绝），
       也**看不到自己上一轮刚建的那个迁移文件** →
       它按记忆里的内容去替换，`old` 逐字命中 0 次 → **连着 12 轮都落不下去**。
    2. **按落点轮转着收**，不能按目录顺序从头收到尾。
       java 目录先扫，location/warehouse 这些落点就把额度占满了，
       resources 里的 Mapper XML 和迁移脚本**一个都进不来**——
       而它们恰恰是新建模块时最要紧的参考。
       轮转着收 = 每个落点先各分到一份，再回头拿第二份。
    """
    buckets: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    total = 0
    for root_rel, suffixes in SOURCE_ROOTS:
        base = ws / root_rel
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            rel = str(path.relative_to(ws))
            # **归给第一个命中的落点**——一个文件只放一份，
            # 否则它会在好几个落点的桶里各占一格，白吃额度。
            bucket_key = next((loc for loc in locations if loc in rel), "")
            if not bucket_key:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if len(text) > MAX_FILE_BYTES:
                text = text[:MAX_FILE_BYTES] + "\n…（文件过长已截断）\n"
            if bucket_key not in buckets:
                buckets[bucket_key] = []
                order.append(bucket_key)
            buckets[bucket_key].append((rel, text))

    # ---- 轮转着取：每个落点先各分一份，再回头拿第二份 ----
    out: dict[str, str] = {}
    cursors = {key: 0 for key in order}
    while len(out) < MAX_CONTEXT_FILES and total < MAX_CONTEXT_BYTES:
        progressed = False
        for key in order:
            if len(out) >= MAX_CONTEXT_FILES or total >= MAX_CONTEXT_BYTES:
                break
            items = buckets.get(key) or []
            index = cursors.get(key, 0)
            if index >= len(items):
                continue
            cursors[key] = index + 1
            rel, text = items[index]
            out[rel] = text
            total += len(text)
            progressed = True
        if not progressed:
            break
    return out


def collect_tree(ws: Path) -> list[str]:
    """仓里现有源码的**路径清单**（只有路径，没有内容）

    **为什么要有它**（2026-10-28 实测撞出来的）：
    模型想把库位实体改一下，写成了
    `server/src/main/java/com/wms/module/location/Location.java`——
    而仓里它其实在 `com/wms/entity/Location.java`。
    机械层回「edit 指向的文件不存在」，模型**不知道正确的路径在哪**，
    于是连着好几轮在同一个错路径上打转。

    上下文里给的是**内容**，可它只覆盖一部分文件；
    **布局**得靠全量路径清单给——那点字数很便宜（只有路径，没有正文）。
    """
    out: list[str] = []
    for root_rel, suffixes in SOURCE_ROOTS:
        base = ws / root_rel
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            out.append(str(path.relative_to(ws)))
    return out


def collect_all_sources(ws: Path) -> dict[str, str]:
    """工作区里**全部**源码（给证据核对用，不给模型看）

    **为什么证据核对不能只看「喂给模型的上下文」**：
    上下文是**截断**的（文件数/字节数有上限，优先给各落点各一份）。
    模型新写的文件如果挤不进上下文，机械层就**看不见它** →
    证据判据判「没实现」→ 模型被反复要求写同一个东西 →
    一轮轮空转到 12 轮上限。

    证据核对要的是「代码里到底有没有」，那就该看整个工作区。
    """
    out: dict[str, str] = {}
    for root_rel, suffixes in SOURCE_ROOTS:
        base = ws / root_rel
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            try:
                out[str(path.relative_to(ws))] = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
    return out


_MIGRATION_DIR_REL = "server/src/main/resources/db/migration"
_MIGRATION_FILE_RE = re.compile(r"^V(\d+)__(.+)\.sql$")


def _ws_rel(ws: Path, path: Path) -> str:
    """工作区内的相对路径（统一用 resolve 后的根，避开 /tmp 软链差异）"""
    return str(path.relative_to(ws.resolve()))


def _migration_files(ws: Path) -> list[Path]:
    mig_dir = (ws / _MIGRATION_DIR_REL).resolve()
    if not mig_dir.exists():
        return []
    return sorted(p.resolve() for p in mig_dir.glob("*.sql"))


def _migration_versions(ws: Path) -> dict[int, tuple[str, str]]:
    """工作区里所有迁移：版本号 → (相对路径, 文件名)"""
    out: dict[int, tuple[str, str]] = {}
    for path in _migration_files(ws):
        m = _MIGRATION_FILE_RE.match(path.name)
        if m:
            out[int(m.group(1))] = (_ws_rel(ws, path), path.name)
    return out


def _resolve_migration_target(
    ws: Path,
    path: Path,
    created: Optional[set[str]],
    renames: Optional[dict[str, str]],
    notes: Optional[list[tuple[str, str]]],
    is_new: bool = True,
) -> Path:
    """模型给的新建迁移路径 → 真正该写进去的路径

    **为什么机械层要接管版本号**（2026-10-30 实测撞出来的）：
    模型建了 `V5__seed_zone.sql`，而 V5 已经被占用了。
    它想改名 —— 但 **edits 机制只能新建和改内容，不能删文件**，
    所以它**既改不了名也删不掉**，那个版本号冲突就永远消不掉：
    实测从第 20 轮到第 30 轮，每一轮的机械核对都报同一条
    「迁移版本号 V5 重复」，18 个文件都写完了，轮数却耗尽。

    **版本号是纯机械的事**（下一个可用号 = 仓里最大版本号 + 1），
    模型算不明白、也删不掉自己写错的名字 —— 那就机械层替它算，
    **但必须告诉它新名字**，否则它下一轮还在用旧路径。

    **三条规则**（Flyway 的硬要求）：
    1. 新迁移的版本号必须**大于仓里已有的最大版本号**
       （Flyway 默认 out-of-order=false，倒着来直接拒绝启动）
    2. 版本号**不许重复**
    3. 同一个描述只许有一个文件 —— 两个同名迁移会被执行两遍

    **不会攒出一堆同义迁移**：模型第 20 轮写 V5、第 25 轮又写 V5，
    两次都落到**同一个**文件上。
    """
    if renames is None:
        return path
    mig_dir = (ws / _MIGRATION_DIR_REL).resolve()
    if path.parent != mig_dir:
        return path
    m = _MIGRATION_FILE_RE.match(path.name)
    if not m:
        return path
    version = int(m.group(1))
    desc = m.group(2)
    requested = _ws_rel(ws, path)
    # **已经改过名了** → 稳定地落到同一个文件（不攒垃圾）
    if requested in renames:
        return (ws / renames[requested]).resolve()
    # **这个路径已经有文件了** → 别动它。
    # 机械层要是擅自改名，「重建一个已发布的迁移」就会被偷偷变成
    # 「新建一个同义迁移」——那条迁移会被执行两遍。
    # 该怎么处理是老规矩：交给「文件已存在」那条报错（它带着文件内容）。
    if path.exists():
        return path
    # **不是新建** → 不做版本分配。
    # 「改一个不存在的迁移文件」该由「文件不存在」那条报错说清楚，
    # 机械层擅自改名只会把模型带偏。
    if not is_new:
        return path

    versions = _migration_versions(ws)
    max_version = max(versions) if versions else 0

    # **同描述的迁移**：两个同名迁移会被执行两遍 → 不许
    for _v, (rel, filename) in versions.items():
        if not filename.endswith(f"__{desc}.sql"):
            continue
        if created and rel in created:
            # 本次会话建的那一个 → 落到它身上（**不攒出第二份**）
            renames[requested] = rel
            if notes is not None:
                notes.append((
                    f"迁移文件名：{path.name} 和本次会话已经建好的 {filename} "
                    f"是同一个迁移 → 我把它落到 {filename} 上了（请用这个路径）",
                    rel,
                ))
            return (ws / rel).resolve()
        raise CodegenError(
            f"{path.name}：仓里已经有同名的迁移 {filename}——"
            "**同名的迁移会被执行两遍**，第二次就撞主键/唯一约束，服务起不来。\n"
            "**要加东西就写进一个新的、描述不同的迁移**"
            f"（下一个可用版本号是 V{max_version + 1}）；\n"
            "**要改它就改那个文件本身**（old 给要替换的原文，new 给改好的）。"
        )

    # **版本号被占 / 倒退** → 重新分配下一个可用号
    if version in versions or version <= max_version:
        new_version = max_version + 1
        new_name = f"V{new_version}__{desc}.sql"
        while (mig_dir / new_name).exists():
            new_version += 1
            new_name = f"V{new_version}__{desc}.sql"
        new_rel = _ws_rel(ws, mig_dir / new_name)
        renames[requested] = new_rel
        if notes is not None:
            if version in versions:
                reason = f"版本号 V{version} 已经被占用"
            else:
                reason = f"版本号 V{version} 不大于仓里最大的 V{max_version}"
            notes.append((
                f"迁移文件名：{reason} → 我把它改名成 **{new_name}**"
                "（**请用这个新路径**，不要再写老名字；"
                "老路径在工作区里并不存在）",
                new_rel,
            ))
        return (ws / new_rel).resolve()
    return path


def next_migration_version(files: dict[str, str]) -> int:
    """仓里现有迁移的最大版本号 + 1（新建迁移该用的版本号）

    **为什么要算它**：模型自己起名是「看着像就行」——
    实测它起过 `V1__create_wms_core_tables.sql`，
    而仓里已经有 V1~V4，版本号撞了 Flyway 启动时直接拒绝。
    把下一个版本号**直接告诉它**，比事后判红再回喂省一轮。
    """
    versions = []
    for rel in files:
        match = re.search(r"db/migration/V(\d+)__", rel)
        if match:
            versions.append(int(match.group(1)))
    return (max(versions) + 1) if versions else 1


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
{migration_note}
**相关源码**：

{files}

**仓里现有源码的路径清单**（**只有路径，没有正文**——
你要动某个文件时，先在这里确认它到底在哪个目录；
上面的「相关源码」只覆盖一部分文件，其余的布局看这里）：

{tree}
{feedback}
**你要做的**：按上面的需求改代码，让系统满足它。

规则：
1. 只用上面给出的源码里真实存在的片段。**不要编造 import、类名、方法名**
2. 改动要**最小**：只动实现这条需求必需的地方
3. **不许删测试、不许削弱测试的断言**——测试是判据的一部分。
   但**代码的依赖变了，测试要跟着补**：比如服务多了一个 Mapper 依赖，
   仓里用 `@InjectMocks` 的老测试就会因为拿不到 mock 而 NPE，
   这时要给测试补一个对应的 `@Mock`（**测试方法只许增不许减**）
4. 不许改与这条需求无关的文件
5. **「必须新实现的概念」一条都不许漏**：每个都要在 edits 里给出它的实现
   （SQL / 字段 / 映射 / 装配，缺哪一环补哪一环）
6. 「已经实现」不是你的结论，是**机械查出来的事实**：
   只要「必须新实现的概念」清单非空，edits 就不能是空的。
   你可以额外说明哪些概念已有实现（给出文件名 + 标识符），但那只是补充说明
7. **你在 Mapper 接口里新增的方法，必须同时在对应的 XML 里加同名语句**
   （`server/src/main/resources/mapper/XxxMapper.xml` 里的
   `<select id="方法名">`）。MyBatis 是**运行期**绑定：
   少了 XML 语句，编译过、单测过（仓里没有报表测试），
   上线后一调那个接口就 `Invalid bound statement`，看板直接 500。
   用注解式 SQL（`@Select`）也可以，但那样就不需要 XML。
8. **一次最多输出 2 个文件**，并且**按「先建骨架、后填内容」的顺序**：
   实体 → Mapper 接口 → Mapper XML → 建表迁移 → Service → Controller。
   机械层会把你的改动落盘，然后**再问你要下一个文件**——
   所以这一轮只写你手上最该写的那个（别一次吐十个文件）。
9. **全部写完了就交白卷**：edits 给空数组。机械层会跑完整核对
   （实现证据 / 编译 / SQL 绑定 / 迁移覆盖），
   不通过的话它会把问题原样回给你，你再改。
10. **迁移脚本只能新建，不能改**（详见上面「迁移」那段）：
    新建的文件名必须是 `V{migration_version}__<描述>.sql`，
    **绝对不许改仓里已有的任何迁移**。
    建表迁移**只建你这一个模块要的表**，别把别的模块的表也搬进去。
11. **实体类放 `server/src/main/java/com/wms/entity/`**（跟仓里其他实体一致），
    **不要在业务模块目录里另建一份**。
    新建模块时**照着 `module/warehouse` 那一套写**：
    controller / service / serviceImpl + dto + vo + mapper + mapper XML + 迁移。
12. **不要复制仓里已有的实体**（Location、Warehouse、Sku…）：
    要表达归属关系，就用**外键字段**
    （例如库位实体上放一个指向库区的编码/主键），
    不要为了「让两边互相引用」把实体复制一份到别的包里——
    同名类出现两份，机械核对会直接判红。
13. **不许动方案没覆盖的文件**（例如库存看板的 VO `vo/DashboardVO`）：
    这次需求跟看板无关，改到那儿会被判红。
14. **前端在 `web/src/` 下**（跟后端一样是仓的一部分）：
    页面 `web/src/views/<模块>/index.vue`、接口封装 `web/src/api/<模块>.ts`、
    类型 `web/src/types/<模块>.ts`、路由 `web/src/router.ts`、
    侧边栏菜单 `web/src/layouts/BasicLayout.vue`。
    **新建前端模块照着现有的写**：页面照 `web/src/views/warehouse/index.vue`、
    接口封装照 `web/src/api/warehouse.ts`、类型照 `web/src/types/warehouse.ts`。
    **前端也是「一次最多 2 个文件」**：先写类型和接口封装，再写页面，
    最后改路由和菜单。
15. **前端也会被机械核对**（不是你说写好了就算）：
    - `vue-tsc` 的类型错误会被判红（接口返回类型、props 类型都要对）
    - **只 import 接口却一次都没调用 = 判红**（页面得真的用它）
    - **路由必须真的注册**（`router.ts` 里 `path: '...'`），
      菜单项也要真的加上（`BasicLayout.vue`）——
      页面建好了但没挂到界面上，业务方一样找不到
{done_files}
输出格式（只输出 JSON，不要解释、不要复述）：

{{"summary": "一句话说明改了什么",
  "edits": [{{"file": "server/src/main/java/.../Xxx.java",
             "old": "要被替换的原文片段（**逐字**复制，含缩进）",
             "new": "替换后的新片段"}}]}}

**old 必须在文件里逐字出现且只出现一次**——不唯一或找不到都会被机械层拒绝。
old 留空字符串**只**用于真正的新建文件（那个文件此刻还不存在）；
要改一个已经存在的文件，必须给出 old 片段（逐字复制）。
直接输出这段 JSON。"""


def migration_note_for(locations: list[str], files: dict[str, str]) -> str:
    """这次涉及迁移时，给模型的一段**具体指引**

    **为什么要单独给一段**（2026-10-30 实测撞出来的）：
    契约里写「库位已用的库区要在库区管理里存在」时，
    模型理解成「在种子迁移里加上」——它从第 6 轮起一直想**改/重建**
    V5（zone 表那个迁移），被「文件已存在」「已发布不许改」拦了 25 轮，
    **从没试过新建一个迁移**。

    根因：**它不知道「新建」长什么样**。
    规则里写「迁移只能新建不能改」它读到了，
    但规则 8 又说「按实体 → Mapper → Mapper XML → **建表迁移** → Service
    → Controller 的顺序」，它就以为该动**已有的那个建表迁移**。

    修法：**把下一步写具体**——哪个目录、什么文件名、edits 里怎么写
    （file 写新路径 / old 留空 / new 写完整内容）。
    「报错和提示都要带能修它的材料」，这里同理。
    """
    if not any(str(loc).startswith("db/migration") for loc in locations):
        return ""
    version = next_migration_version(files)
    # **仓里已有的迁移文件名，一个不漏地列出来**——
    # 模型看不见全貌就会自己挑一个版本号（实测挑了已被占用的 V5，
    # 撞版本号 → 反复改名 → 25 轮打转）。
    existing = sorted(
        Path(rel).name for rel in files
        if "db/migration/" in rel and rel.endswith(".sql")
    )
    existing_text = (
        "\n".join(f"  - {name}" for name in existing)
        if existing else "  （仓里还没有迁移文件）"
    )
    return (
        "\n**迁移**（这次的落点里有 `db/migration`，所以下面这段跟你有关）：\n"
        "\n仓里**已经有**的迁移（**这些都已经发布过，一个都不许动**——\n"
        "Flyway 比对每个迁移的 checksum，改了内容就是 checksum mismatch，\n"
        "**服务启动时直接拒绝**）：\n"
        f"{existing_text}\n"
        "\n你要加的表、列、**种子数据**，写进一个**全新的迁移文件**：\n"
        f"`server/src/main/resources/db/migration/V{version}__<描述>.sql`\n"
        f"**版本号就是 {version}，不许自己另选**（上面每一个版本号都被占了，\n"
        "Flyway 遇到重复版本会直接拒绝启动）。描述部分自己起名，\n"
        "例如 `seed_zone`、`add_zone_index`。\n"
        "\n**新建文件的写法**：`edits` 里 `file` 写这个新路径、\n"
        "**`old` 留空字符串**、`new` 写这个文件的完整 SQL 内容。\n"
        "`old` 留空 = 这个文件此刻还不存在 → 新建。\n"
        "\n**别去改那个「看起来该改」的迁移**（比如建库区表的那个）：\n"
        "它已经发布过了，改了就是 checksum mismatch。\n"
    )


def build_prompt(expectations: list[dict[str, Any]], locations: list[str],
                 files: dict[str, str], feedback: str = "",
                 concepts: Optional[list[str]] = None,
                 must_implement: Optional[list[tuple[str, str]]] = None,
                 done_files: Optional[list[str]] = None,
                 tree: Optional[list[str]] = None,
                 migration_note: str = "",
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
    for concept, description in (must_implement or []):
        what = description or "（没有登记实现证据）"
        missing_lines.append(
            f"- {concept}：源码里找不到 {what} → **这次要新增实现**"
            "（看板类指标：这个标识符必须出现在**看板 VO**上——"
            "业务方是在看板的字段里看到它，不是在你新加的接口里看到它）"
        )
    missing_text = "\n".join(missing_lines) or "（没有：本次涉及的每个概念源码里都已有实现）"
    done = list(done_files or [])
    done_text = (
        "\n**已经写完的文件**（别重写它们，除非你要改）：\n"
        + "\n".join(f"- {d}" for d in done)
        + "\n\n"
        if done else ""
    )
    return _CODE_PROMPT.format(
        expectations="\n".join(exp_lines) or "（没有预期）",
        concepts="\n".join(concept_lines),
        missing=missing_text,
        locations="\n".join(loc_lines),
        files="\n".join(file_blocks) or "（没有相关源码）",
        tree="\n".join(tree or []) or "（没有源码文件）",
        feedback=fb,
        done_files=done_text,
        # **下一个迁移版本号直接给它**——它自己起名是「看着像就行」，
        # 实测起过 V1__create_wms_core_tables.sql，
        # 而仓里已经有 V1~V4，版本号撞了 Flyway 启动时直接拒绝。
        migration_version=next_migration_version(files),
        migration_note=migration_note,
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


def _near_miss_hint(ws: Path, rel: str) -> str:
    """文件不存在时，**告诉它正确的路径在哪**

    实测：模型把库位实体改成了
    `server/src/main/java/com/wms/module/location/Location.java`——
    而仓里它在 `com/wms/entity/Location.java`。
    机械层只回一句「文件不存在」，模型**无从知道正确路径**，
    于是连着几轮在同一个错路径上打转。
    """
    name = Path(rel).name
    hits: list[str] = []
    for root_rel, suffixes in SOURCE_ROOTS:
        base = ws / root_rel
        if not base.exists():
            continue
        for path in base.rglob(name):
            hits.append(str(path.relative_to(ws)))
            if len(hits) >= 5:
                break
    if not hits:
        return ""
    return (
        "——仓里同名文件在："
        + "、".join(hits)
        + "（路径写错的话，按这个改；确实要新建就换一个仓里没有的文件名）"
    )


def _validate_edit(
    ws: Path,
    edit: Any,
    created: Optional[set[str]] = None,
    renames: Optional[dict[str, str]] = None,
    notes: Optional[list[str]] = None,
) -> tuple[Path, str, str]:
    """校验一处替换（不写盘）

    校验和写盘**分成两步**：这样「三条改动里第二条落不下去」时，
    第一条不会留在盘上——重试才是干净的。

    `created` 是**本次会话已经建出来的文件**（相对工作区的路径）。
    它解决的是重试时的死结：模型每一轮重出整份计划，
    于是它会再次「新建」上一轮已经建好的文件——
    机械层一看「文件已存在」就拒绝，三轮下来它改不完。

    分两种：
    - 文件是我们**这一轮之前**建的 → 允许整篇写回
      （模型要的就是「这个文件应该是这个内容」）
    - 文件本来就在（仓库里的老文件）→ 拒绝
      （整篇写回会把老文件冲掉，那不是「改」，是「重写」）
    """
    if not isinstance(edit, dict):
        raise CodegenError(f"edit 不是对象：{edit!r}")
    rel = str(edit.get("file") or "").strip()
    old = edit.get("old")
    new = edit.get("new")
    if not rel or not isinstance(old, str) or not isinstance(new, str):
        raise CodegenError(f"edit 缺字段：{edit!r}")
    path = _resolve_edit_path(ws, rel)
    # **迁移的版本号由机械层分配**（模型算不明白、也删不掉自己写错的名字）
    path = _resolve_migration_target(
        ws, path, created, renames, notes, is_new=(old == ""),
    )
    rel = _ws_rel(ws, path)
    if old == "":
        if path.exists():
            rel_key = str(path.relative_to(ws.resolve()))
            if not (created and rel_key in created):
                # **报错要带能修它的材料**（2026-10-30 实测撞出来的）：
                # 只说「文件已存在、要给 old」的话，模型会拿 old="" 反复试
                # ——实测在同一个文件上打了 25 轮，每轮都被同一条理由拦回去。
                # 它不是不知道要改，是**不知道「改已有文件」长什么样**。
                #
                # 所以这里把两条路都写清楚，并把**文件现在的内容**给它：
                # 1. 局部改：old 给要替换的那几行（逐字复制，唯一命中）
                # 2. 整体重写：old 给**整个文件现在的内容**，new 给改好的
                #    （整篇替换在机械层是合法的：old 唯一命中一次）
                try:
                    current = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    current = ""
                raise CodegenError(
                    f"{rel}：old 留空表示新建文件，但这个文件已经存在"
                    "（而且不是本次会话建的）。\n"
                    "**要改它，二选一**：\n"
                    "1. 局部改：`old` 给**要替换的那几行原文**"
                    "（逐字复制，含缩进，在文件里只出现一次），"
                    "`new` 给替换后的内容；\n"
                    "2. 整体重写：`old` 给**这个文件现在的完整内容**"
                    "（逐字复制），`new` 给改好的完整内容。\n"
                    "**文件现在长这样（从这里逐字复制）**：\n"
                    f"{_tail_for_error(current, lines=40)}"
                )
        return path, old, new
    if not path.exists():
        raise CodegenError(
            f"edit 指向的文件不存在：{rel}" + _near_miss_hint(ws, rel)
        )
    text = path.read_text(encoding="utf-8")
    # **删除**：`new` 是空字符串、且 `old` 命中全文 → 删掉这个文件
    #
    # **为什么要有删除能力**（2026-10-30 实测）：
    # 模型建错了文件（版本号撞了），edits 只能新建和改内容，
    # 它**删不掉** → 冲突永远消不掉 → 30 轮全耗在这上面。
    if new == "" and text.strip() == old.strip():
        return delete_file_for_edit(ws, rel)
    count = text.count(old)
    if count == 0:
        # **缩进/行尾差异的容错**：模型是照着文件抄的，
        # 抄歪一格缩进很常见——那不是「改错地方」，是「抄错」，
        # 直接判它失败等于让它瞎猜。
        # 归一化后**仍然要求唯一**：多处命中照样拒绝（不许瞎改）。
        norm_old = _normalize(old)
        norm_text = _normalize(text)
        norm_count = norm_text.count(norm_old) if norm_old else 0
        if norm_count == 1:
            # 唯一命中：用原文里**真实的那一段**替换（保住原缩进）
            span = _find_normalized_span(text, old)
            if span is not None:
                return path, text[span[0]:span[1]], new
        if norm_count > 1:
            # **多处命中 = 不许瞎改**：归一化之后仍不唯一，
            # 说明模型挑的锚点本身就有歧义——那该换锚点，不是换地方。
            raise CodegenError(
                f"{rel}：要替换的原文片段（忽略缩进后）出现 {norm_count} 次（必须唯一）。\n"
                f"**请换一个只出现一次的短锚点**（文件里长这样）：\n"
                f"{_tail_for_error(text)}"
                f"{_unique_line_hints(text)}"
            )
        raise CodegenError(
            f"{rel}：找不到要替换的原文片段（old 逐字命中 0 次）。\n"
            f"**你要替换的那段原文，文件里现在长这样（从这里逐字复制）：**\n"
            f"{_tail_for_error(text)}\n"
            f"（old 必须逐字取自上面这段；抄不准就用一个**短锚点**，"
            f"比如 `</mapper>` 这种只出现一次的行）"
            f"{_unique_line_hints(text)}"
        )
    if count > 1:
        raise CodegenError(
            f"{rel}：要替换的原文片段出现 {count} 次（必须唯一）。\n"
            f"**请换一个只出现一次的短锚点**（文件里长这样）：\n"
            f"{_tail_for_error(text)}"
            f"{_unique_line_hints(text)}"
        )
    return path, old, new


def _unique_line_hints(text: str, limit: int = 8) -> str:
    """这个文件里**只出现一次**的行（可以拿其中一行当锚点）

    **为什么给这个**：模型挑的锚点常常出现多次（`}`、`return vo;` 这种），
    机械层只能拒绝。回一句「换个唯一锚点」它无从下手——
    它不知道哪些行是唯一的。**报错要带能修它的材料**。
    """
    counts: dict[str, int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            counts[stripped] = counts.get(stripped, 0) + 1
    unique = [line for line, n in counts.items() if n == 1]
    if not unique:
        return ""
    # 长一点的行更像有意义的锚点（`}` 这种太短，没有定位力）
    unique.sort(key=lambda line: -len(line))
    shown = unique[:limit]
    return (
        "\n**这个文件里只出现一次的行（拿其中一行当锚点）：**\n"
        + "\n".join(f"- {line}" for line in shown)
    )


def _normalize(text: str) -> str:
    """归一化：逐行去掉首尾空白（只用于「抄歪了缩进」的容错比对）"""
    return "\n".join(line.strip() for line in text.splitlines())


def _find_normalized_span(text: str, old: str) -> Optional[tuple[int, int]]:
    """在原文里找到「归一化后等于 old」的那一段的真实位置

    逐行扫描：用归一化后的行序列去比，命中后再把原文那一段切出来。
    """
    old_lines = [line.strip() for line in old.splitlines()]
    if not old_lines:
        return None
    src_lines = text.splitlines(keepends=True)
    norm_src = [line.strip() for line in src_lines]
    n = len(old_lines)
    for i in range(len(norm_src) - n + 1):
        if norm_src[i:i + n] == old_lines:
            start = sum(len(l) for l in src_lines[:i])
            end = sum(len(l) for l in src_lines[:i + n])
            return start, end
    return None


def _tail_for_error(text: str, lines: int = 25) -> str:
    """给模型看的文件末尾（报错要带材料，否则它只能瞎猜）"""
    body = text.splitlines()
    tail = body[-lines:]
    prefix = "…（前面省略）\n" if len(body) > lines else ""
    return prefix + "\n".join(tail)


def rename_migration_file(ws: Path, rel: str, new_name: str) -> str:
    """把工作区里的迁移文件改名（内容不变），返回新的相对路径

    **为什么需要它**（2026-10-30 实测撞出来的）：
    模型新建了 `V5__seed_zone.sql`，而 V5 已经被占用了。
    它想改名 —— 但 **edits 机制只能新建和改内容，不能删文件**，
    所以它**既改不了名也删不掉**，那个版本号冲突就永远消不掉：
    实测从第 20 轮到第 30 轮，每一轮的机械核对都报同一条
    「迁移版本号 V5 重复」，18 个文件都写完了，轮数却耗尽。

    **改名是纯机械操作**（版本号 → 下一个可用版本号），
    模型做不了（它没有删除能力），那就机械层替它做 ——
    但**必须告诉模型新路径**，否则它下一轮还在用旧路径。

    **只改「本次新建的」那些**：仓里已发布的迁移不许动。
    """
    src = ws / rel
    if not src.exists():
        return rel
    dest = src.with_name(new_name)
    if dest.exists():
        return rel
    src.rename(dest)
    return str(dest.relative_to(ws))


def delete_file_for_edit(ws: Path, rel: str) -> str:
    """删掉工作区里的一个文件，返回它原来的相对路径

    **为什么要给 edits 加删除能力**：
    模型建错了文件（比如版本号撞了、或者建了不该建的文件），
    没有删除能力就**永远清不掉** —— 它只能一遍遍重建、覆盖，
    冲突一直在，轮数耗尽。

    删文件是纯机械操作，模型表达「old = 文件全文、new = 空」就行。
    """
    path = ws / rel
    if path.exists():
        path.unlink()
    return rel


def apply_edits(
    ws: Path,
    edits: list[dict[str, Any]],
    created: Optional[set[str]] = None,
    renames: Optional[dict[str, str]] = None,
    notes: Optional[list[str]] = None,
) -> list[str]:
    """把模型给的「精确片段替换」应用到工作区

    机械层只做三件事：文件在不在、old 是不是**只出现一次**、替换后写回。
    任何一条不满足就抛错——**让错误原样回给模型去改**，
    别自己脑补一段「差不多」的代码。

    **先全部校验、再全部写盘**：半途失败留下的半截改动会让重试变成叠加。
    """
    planned: list[tuple[Path, str, str]] = []
    for edit in edits:
        planned.append(_validate_edit(ws, edit, created, renames, notes))

    applied: list[str] = []
    root = ws.resolve()
    for path, old, new in planned:
        if old == "":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new, encoding="utf-8")
            if created is not None:
                created.add(str(path.relative_to(root)))
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
    max_attempts: int = MAX_CODEGEN_ROUNDS,
    concepts: Optional[list[str]] = None,
    must_implement: Optional[list[tuple[str, str]]] = None,
    verify: Optional[Callable[[], list[str]]] = None,
    refresh: Optional[Callable[[], dict[str, str]]] = None,
    verify_fast: Optional[Callable[[], list[str]]] = None,
    verify_evidence: Optional[Callable[[], list[str]]] = None,
    tree: Optional[list[str]] = None,
    migration_note: str = "",
) -> tuple[list[str], str]:
    """让模型出改动并落下去；落不下去就把错误原样回给它改

    **不降级**：模型连续几轮都改不动 → 履约中止。
    绝不「自己脑补一段差不多的代码」——那是假交付。

    **「返回空 edits」是「我说写完了」的意思**（2026-10-28 实测撞出来的）：
    一次要交付的东西多的时候（新建一个模块 = 十几个文件），
    模型一次吐十个文件会**超出输出上限** → JSON 截断 → 解析失败 → 履约中止。
    所以流程改成**多轮推进**：每轮写一两个文件，机械层落盘后
    再问它下一个；它说「写完了」（edits 空数组）时，
    机械层才跑**完整核对**（实现证据 + 编译 + SQL 绑定 + 迁移覆盖），
    不通过就把问题原样回给它，它接着改。

    **三层检查，各管一段**：
    - `verify_fast`（每轮）：SQL 绑定 / 迁移覆盖。
      跟「模块写完没有」无关，缺了就永远缺，早发现早回喂。
    - `verify_evidence`（每轮）：概念的实现证据。
      证据不足 = **还有文件没写** → 告诉它「接着写下一个」。
    - `verify`（收尾）：完整核对（含编译）。
      **编译必须等证据满足后才跑**：模块分批写的时候中间状态
      本来就编译不过（Controller 引用的类还没建）；
      每写一个文件就编译，等于逼着模型把顺序倒过来——
      那是机械层在替它做设计决策。

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
                prompt, thinking="disabled", max_tokens=MAX_CODGEN_TOKENS, timeout=180,
            )

    feedback = ""
    last_error = ""
    # **本次会话建出来的文件**（跨轮次记着）：
    # 模型每一轮重出整份计划，它会再次「新建」上一轮建好的文件——
    # 机械层要认得出那些是它自己建的（见 _validate_edit）。
    created: set[str] = set()
    # **机械层替模型分配的迁移版本号**（requested 路径 → 真正落盘的路径）
    # 跨轮次记着：模型下一轮又写老名字时，落到同一个文件上，
    # **不会攒出一堆同义迁移**。
    renames: dict[str, str] = {}
    # **要告诉模型的事**（改名/落点变更）—— 每轮清空
    notes: list[tuple[str, str]] = []
    applied_all: list[str] = []
    done_files: list[str] = []
    summary = ""
    for round_no in range(max_attempts):
        if round_no and refresh is not None:
            files = refresh()
        _log(
            f"第 {round_no + 1}/{max_attempts} 轮："
            f"已写 {len(done_files)} 个文件，"
            f"上次的问题：{(feedback or '（没有）')[:120]}"
        )
        prompt = build_prompt(
            expectations, locations, files, feedback=feedback,
            concepts=concepts, must_implement=must_implement,
            done_files=done_files, tree=tree,
            migration_note=migration_note,
        )
        _log(f"第 {round_no + 1} 轮：问模型（上下文 {len(files)} 个文件）")
        raw = llm_call(prompt)
        _log(f"第 {round_no + 1} 轮：模型答了")
        if not isinstance(raw, dict):
            last_error = f"模型输出不是一个对象：{type(raw).__name__}"
            feedback = last_error
            continue
        edits = raw.get("edits")
        summary = str(raw.get("summary") or summary)
        if not isinstance(edits, list):
            last_error = "输出里没有 edits 数组"
            feedback = last_error
            continue
        if not edits:
            # **模型说「我写完了」** → 跑完整核对
            problems = verify() if verify is not None else None
            if problems:
                last_error = (
                    "你说你写完了，但机械核对没过：\n"
                    + "\n".join(problems)
                    + "\n\n请按上面的问题继续改（工作区里已经保留了你之前的改动，"
                    "下面的源码就是**当前**内容——old 要逐字取自当前内容）。"
                )
                feedback = last_error
                continue
            if must_implement:
                # 没有核对能力可证明它做完了 → 按「交白卷」处理
                names = "、".join(c for c, _ in must_implement)
                last_error = (
                    f"你返回了空的 edits，但这些概念在源码里查不到实现：{names}——"
                    "本次的任务就是把它们实现出来"
                )
                feedback = last_error
                continue
            return applied_all, summary
        notes.clear()
        try:
            applied = apply_edits(ws, edits, created, renames, notes)
        except CodegenError as exc:
            last_error = str(exc)
            feedback = last_error
            continue
        # **机械层替模型做的事，必须告诉它**（否则它下一轮还在用老路径）
        rename_notes = [
            text for text, target in notes if (ws / target).exists()
        ]
        notes_text = ""
        if rename_notes:
            notes_text = (
                "**我替你改了一件事**（文件名/版本号是机械的事，不该你操心）：\n"
                + "\n".join(rename_notes)
                + "\n\n"
            )
        for rel in applied:
            # 同一个文件被改多次只记一次——
            # 「改动清单」是**文件集合**，不是修改次数
            if rel not in applied_all:
                applied_all.append(rel)
            if rel not in done_files:
                done_files.append(rel)
        # **便宜的检查每轮都跑**：它们跟「模块写完没有」无关
        if verify_fast is not None:
            problems = verify_fast()
            if problems:
                last_error = (
                    "你这一轮的改动没通过机械核对：\n"
                    + "\n".join(problems)
                    + "\n\n请按上面的问题修正后，重新给出完整的 edits 数组。"
                    "\n（工作区里已经保留了你上一轮的改动，下面的源码就是**当前**内容——"
                    "请在当前内容上做增量修改，old 要逐字取自当前内容。）"
                )
                feedback = notes_text + last_error
                continue
        # **证据不足 = 还有文件没写**：告诉它接着写下一个
        evidence_problems = verify_evidence() if verify_evidence is not None else []
        if evidence_problems:
            last_error = (
                "你这一轮写完了，但这次要落地的概念**还没落地**：\n"
                + "\n".join(evidence_problems)
                + "\n\n**接着写下一个文件**（一次 1-2 个）："
                "还没写的部分就是上面点名的那几处。"
            )
            feedback = notes_text + last_error
            continue
        # **证据齐了 → 跑完整核对（含编译）**。
        # 编译不过时，编译器会指名缺什么（"找不到符号 ZoneQuery"），
        # 模型照着补就行——**这是编译器当神谕**，不是机械层替它做设计。
        problems = verify() if verify is not None else []
        if problems:
            last_error = (
                "你这一轮的改动没通过机械核对：\n"
                + "\n".join(problems)
                + "\n\n请按上面的问题修正后，重新给出完整的 edits 数组。"
                "\n（工作区里已经保留了你上一轮的改动，下面的源码就是**当前**内容——"
                "请在当前内容上做增量修改，old 要逐字取自当前内容。）"
            )
            feedback = notes_text + last_error
            continue
        return applied_all, summary
    raise CodegenError(
        f"模型连续 {max_attempts} 轮给出的改动都落不下去：{last_error}"
    )


def changed_files(ws: Path) -> list[str]:
    """工作区里被改动的文件（相对 HEAD）

    **别把路径切错**（2026-10-27 实测）：porcelain 每行是
    `XY path`（两个状态位 + 一个空格），路径从**第 4 个字符**开始。
    之前这里先 `line.strip()` 再取 `line[3:]` ——
    strip 把**前导空格**也去了（" M path" → "M path"），
    于是 `line[3:]` 切掉了 "M p"，路径少了第一个字符
    （"erver/src/main/..."）。

    后果很隐蔽：路径里仍然含 "mapper/ReportMapper" 这类位置片段，
    所以「改动在不在范围内」的判断照样过，**没有报错**。
    但任何拿路径去**打开文件**的检查都会拿到一个不存在的路径，
    然后静默跳过——本轮那条「新增 Mapper 方法必须��对应 SQL」的校验
    就是这么被废掉的：它看见的是 `erver/src/...`，文件不存在 → 跳过。
    """
    out = _run(["git", "status", "--porcelain"], cwd=ws)
    if out.returncode != 0:
        return []
    files = []
    for raw in out.stdout.splitlines():
        # **只去尾部空白**：前导空白是状态位的一部分，去了就切错位置
        line = raw.rstrip()
        if len(line) < 4:
            continue
        # porcelain 格式：XY path（重命名是 "R  old -> new"）
        path = line.split(" -> ")[-1].strip() if " -> " in line else line[3:].strip()
        if path:
            files.append(path)
    return files


def _drop_symlinks_from_index(ws: Path) -> list[str]:
    """把**符号链接**从暂存区里剔掉（并返回剔掉哪些）

    **为什么必须剔**：开发盒在隔离工作区里给前端挂了一个
    `web/node_modules` 软链（worktree 里没有 gitignore 的依赖，
    不挂就没法跑 `npm run build`）。
    那个软链是**为了让构建能跑**临时挂上去的，
    **不是这次需求的产出**——它进了提交，就会被评审判成
    「改了方案没覆盖的文件」（实测：开发盒因此被判 rejected）。

    **为什么不用 .gitignore 解决**：worktree 专属的
    `$GIT_DIR/info/exclude` **git 不读**（info/exclude 属于 common dir），
    而改 WMS 主仓的 .gitignore 是**动方案外的文件**——
    机械层本来就该拦下来的那种改动。

    **通用规则**：符号链接不进交付物。
    不针对 node_modules 打补丁——任何符号链接都剔。
    """
    proc = _run(["git", "ls-files", "-s", "-z"], cwd=ws)
    if proc.returncode != 0:
        return []
    dropped: list[str] = []
    for entry in proc.stdout.split("\0"):
        if not entry.strip():
            continue
        meta, _, path = entry.partition("\t")
        # mode 120000 = 符号链接（100644 文件 / 100755 可执行 / 040000 树）
        if not path or not meta.startswith("120000"):
            continue
        rm = _run(["git", "rm", "--cached", "--quiet", "--", path], cwd=ws)
        if rm.returncode == 0:
            dropped.append(path)
    return dropped


def commit_changes(ws: Path, message: str) -> tuple[Optional[str], Optional[str]]:
    """在工作区里提交，并把提交落到主仓的一个分支上

    **交付物必须能追回去**：制品里的 digest 要能指到一个具体的提交，
    所以提交不能只活在临时工作区里——要挂到主仓的分支上。

    Returns: (commit_sha, branch)；没提交成返回 (None, None)
    """
    _run(["git", "add", "-A"], cwd=ws)
    _drop_symlinks_from_index(ws)
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
