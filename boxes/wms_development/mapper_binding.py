"""
boxes.wms_development.mapper_binding - 「新增的 Mapper 方法有没有对应的 SQL」

**为什么要有这个检查**：MyBatis 的 SQL 绑定是**运行期**的。

接口里声明了方法、XML 里没有同名语句时：
- 编译**照过**（方法签名合法）
- 仓里的单测**照过**（只要没调到那个方法）
- 仓里**根本没有报表/看板的测试**（实测：server/src/test 下 11 个测试类，
  一个都没碰 ReportService / ReportMapper）
- 一上线、有人调那个接口 → `Invalid bound statement (not found)` →
  **整个看板接口 500**

2026-10-27 实测撞到的：开发盒给 `ReportMapper` 加了 `selectPickAccuracy()`，
XML 里没有对应语句。编译过、单测绿、判据全绿——**差一步就上线**。

所以这一条是**机械核对**：不需要跑起来，只需要读接口声明和 XML 的 id。

**只查「本次新增」的方法**：老方法早就跑过（或者早该炸了），
把老代码翻出来重判一遍只会制造噪声。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

# 接口里声明的方法：`public`/无修饰符 + 返回值 + 名字 + `(`
_METHOD_RE = re.compile(
    r"^\s*(?:public\s+|default\s+|static\s+|abstract\s+)*"
    r"(?:<[^>]+>\s*)?"
    r"[\w.<>\[\], ?]+"
    r"\s+(\w+)\s*\(",
    re.MULTILINE,
)

# 注解式 SQL：@Select / @Insert / @Update / @Delete —— 有注解就不需要 XML
_ANNOTATED_SQL_RE = re.compile(r"@(Select|Insert|Update|Delete)\b")

# XML 里的语句 id
_XML_ID_RE = re.compile(
    r"<(select|insert|update|delete)\b[^>]*\bid\s*=\s*\"([^\"]+)\"",
    re.IGNORECASE,
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _declared_methods(source: str) -> list[str]:
    """接口里**自己声明**的方法名

    - 跳过 default 方法（不是 SQL 绑定）
    - 跳过构造器（类名开头）
    """
    out: list[str] = []
    for match in _METHOD_RE.finditer(source):
        name = match.group(1)
        if name in out:
            continue
        if name == source.split("{")[0].strip().split()[-1]:
            continue  # 构造器
        out.append(name)
    return out


def _annotated_methods(source: str) -> set[str]:
    """带 @Select/@Insert/... 注解的方法名"""
    out: set[str] = set()
    lines = source.splitlines()
    for i, line in enumerate(lines):
        m = _ANNOTATED_SQL_RE.search(line)
        if not m:
            continue
        for follow in lines[i + 1:i + 4]:
            mm = _METHOD_RE.match(follow) or re.match(r"\s*[\w.<>\[\], ?]+\s+(\w+)\s*\(", follow)
            if mm:
                out.add(mm.group(1))
                break
    return out


def _xml_ids(ws: Path, mapper_name: str) -> set[str]:
    xml_path = ws / "server" / "src" / "main" / "resources" / "mapper" / f"{mapper_name}.xml"
    source = _read(xml_path)
    return {mid for _tag, mid in _XML_ID_RE.findall(source)}


def _baseline_source(ws: Path, base_ref: str, rel: str) -> str:
    """基线版本的那份文件（新增方法 = 现在有、基线没有）"""
    if not base_ref:
        return ""
    proc = subprocess.run(
        ["git", "show", f"{base_ref}:{rel}"],
        cwd=str(ws), capture_output=True, text=True, timeout=60,
    )
    return proc.stdout if proc.returncode == 0 else ""


def _migration_tables(ws: Path) -> set[str]:
    """迁移脚本里建出来的表"""
    out: set[str] = set()
    mig_dir = ws / "server" / "src" / "main" / "resources" / "db" / "migration"
    if not mig_dir.exists():
        return out
    for path in sorted(mig_dir.glob("*.sql")):
        source = _read(path)
        for match in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w]*)",
            source,
            re.IGNORECASE,
        ):
            out.add(match.group(1).lower())
    return out


# XML 里出现的表名（FROM / JOIN / INTO / UPDATE）
_TABLE_RE = re.compile(
    r"\b(?:from|join|into|update)\s+([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)

# Flyway 的迁移文件命名规范
_FLYWAY_VERSIONED_RE = re.compile(r"^V(\d+)__.+\.sql$")
_FLYWAY_REPEATABLE_RE = re.compile(r"^R__.+\.sql$")


def check_migration_covers_tables(
    ws: Path,
    changed_files: list[str],
) -> list[str]:
    """SQL 里用到的表，必须有迁移建出来

    **为什么要有这条**（2026-10-27 实测）：
    开发盒新建一个模块时，最容易漏掉**建表脚本**——
    SQL 是字符串，编译过；单测没碰那张表，也过。
    上线之后 Flyway 没建那张表 → 接口一调就
    `relation "wms_xxx" does not exist` → 整个功能 500。

    这跟「新增 Mapper 方法必须有对应 SQL」是同一族：
    **运行期才炸的东西，必须在交付前机械核一遍。**

    只查本次改动碰到的 XML 里用到的表；
    已经存在的表（迁移里有）不算问题。
    """
    problems: list[str] = []
    tables = _migration_tables(ws)
    if not tables:
        return problems
    for rel in changed_files:
        if not rel.endswith(".xml"):
            continue
        if "resources/mapper/" not in rel:
            continue
        path = ws / rel
        if not path.exists():
            continue
        source = _read(path)
        # **先把 XML 标签剥掉再找表名**：
        # `<update id="softDeleteById">` 里的 `id` 是**属性名**，
        # 不是表名——不剥标签就会把「表 id」报成「缺建表迁移」。
        sql_text = re.sub(r"<[^>]*>", " ", source)
        used = {
            m.group(1).lower()
            for m in _TABLE_RE.finditer(sql_text)
            if not m.group(1).lower() in ("select", "dual")
        }
        missing = sorted(t for t in used if t not in tables)
        if missing:
            problems.append(
                f"{rel} 里用到的表 {('、'.join(missing))} "
                f"没有对应的建表迁移（server/src/main/resources/db/migration/*.sql "
                f"里没有 CREATE TABLE {missing[0]}）——"
                "编译和单测都发现不了（SQL 是字符串），"
                "上线后一调这个接口就 relation does not exist，接口直接 500"
            )
    return problems


def check_migration_creates_existing_table(
    ws: Path,
    changed_files: list[str],
    base_ref: str = "",
) -> list[str]:
    """**新建的迁移不许再建一遍已经建过的表**

    **为什么要有这条**（2026-10-30 实测撞出来的）：
    模型被拦住「不许改已发布的 V5__create_zone_table.sql」之后，
    转头在新建的 V6 里**又写了一遍 `CREATE TABLE wms_zone`** ——
    结果 Flyway 跑到 V6 时报
    `relation "wms_zone" already exists` → **服务根本起不来**。

    **编译过、单测绿、判据全绿 —— 因为它根本没起来过**：
    那一轮链跑报了「六盒全 accepted、落差 0」，实际测的是**旧实例**
    （新构建启动就挂了，端口检查看到的是还没退干净的旧进程）。

    `CREATE TABLE IF NOT EXISTS` **不算错**（幂等，撞了也不炸）——
    只判「不带 IF NOT EXISTS 的重复建表」。

    **报错要带能修它的材料**：说清哪个表已经建过了（哪个迁移建的），
    以及该怎么写（只写 ALTER / INSERT 种子数据）。
    """
    mig_dir = ws / "server" / "src" / "main" / "resources" / "db" / "migration"
    if not mig_dir.exists():
        return []

    # 表 → 建它的迁移（rel → 表名集合）
    creates: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(mig_dir.glob("*.sql")):
        rel = str(path.relative_to(ws))
        for table, guarded in _created_tables(_read(path)):
            creates.setdefault(table, []).append((rel, guarded))

    problems: list[str] = []
    for rel in changed_files:
        if not rel.startswith("server/src/main/resources/db/migration"):
            continue
        if not rel.endswith(".sql"):
            continue
        path = ws / rel
        if not path.exists():
            continue
        text = _read(path)
        tables = _created_tables(text)
        for table, guarded in tables:
            if guarded:
                continue  # IF NOT EXISTS：撞了也不炸
            # **同一个迁移里建两次同一个表** → 第二次就炸
            if sum(1 for name, _ in tables if name == table) > 1:
                problems.append(
                    f"{rel} 里建了两次表 {table} —— 第二次就会报 "
                    f"relation \"{table}\" already exists，服务起不来"
                )
                continue
            others = [
                (other_rel, other_guarded)
                for other_rel, other_guarded in creates.get(table, [])
                if other_rel != rel
            ]
            if not others:
                continue
            built_by = "、".join(
                other_rel for other_rel, other_guarded in others
                if not other_guarded
            ) or "、".join(other_rel for other_rel, _ in others)
            problems.append(
                f"{rel} 里又建了一次表 {table}，但 {built_by} 已经建过它了 ——\n"
                f"Flyway 跑到这个迁移时会报 relation \"{table}\" already exists，"
                "**服务启动时直接拒绝**（编译和单测都发现不了，"
                "要等它真的起来才炸）。\n"
                f"**要加东西就只写 ALTER / INSERT**（比如种子数据就写 "
                f"INSERT INTO {table} ...），**不要重复建表**；\n"
                "确实要建一个新表，就换一个仓里还没有的表名。"
            )
    return problems


# 建表语句：`CREATE TABLE [IF NOT EXISTS] <表名>`
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:(IF\s+NOT\s+EXISTS)\s+)?([\w.\"]+)",
    re.IGNORECASE,
)


def _created_tables(sql: str) -> list[tuple[str, bool]]:
    """这个迁移建了哪些表 → [(表名, 是否 IF NOT EXISTS)]"""
    out: list[tuple[str, bool]] = []
    for m in _CREATE_TABLE_RE.finditer(sql):
        name = m.group(2).strip('"').lower()
        out.append((name, bool(m.group(1))))
    return out


def check_migration_filenames(
    ws: Path,
    changed_files: list[str],
    base_ref: str = "",
) -> list[str]:
    """迁移脚本的文件名必须符合 Flyway 规范，版本号必须比已有的大

    **为什么要有这条**（2026-10-28 实测）：
    开发盒建表时写出 `V__create_wms_zone.sql`——**没有版本号**。
    编译过、单测绿、判据全绿，可 Flyway 启动时直接拒绝：
    `Invalid version separator` → **整个服务起不来**。

    跟「迁移覆盖」是同一族：**运行期才炸的东西，必须在交付前机械核一遍。**

    三条规则：
    1. 文件名必须是 `V<版本号>__<描述>.sql`（可重复脚本是 `R__<描述>.sql`）
    2. **新增**的迁移，版本号必须**大于仓里已有的最大版本号**——
       Flyway 默认不认乱序（out-of-order=false），版本号撞了/倒退了，
       启动时 `Detected resolved migration not applied to database`。
    3. **不许改已发布的迁移**（基线里已有的那些）——
       Flyway 会比对 checksum，改了就是 checksum mismatch，
       **服务起不来**。要加东西就**新建一个迁移**。

    **第 3 条为什么单列**（2026-10-29 实测撞出来的）：
    原来只有第 2 条，于是模型改 `V2__seed_demo_data.sql`（已发布）时，
    机械层报的是「版本号 V2 不大于最大 V5」——
    **理由完全不对**：V2 本来就不该大于 V5。
    模型被这个理由误导，在同一个文件上**打了 30 轮**，
    每一轮都在改它、每一轮都被同一条理由拦回去。

    **报错要带能修它的材料**：直接告诉它「新建 V6」，
    模型的下一轮就会走对路。
    """
    # **已发布的迁移**（= 不许改、不许删的那些）
    #
    # **判据要同时看两处**（2026-10-30 实测踩出来的）：
    # 1. **当前仓里已跟踪的**（`git ls-files`）——
    #    这些是会被发布出去的，改了就是 checksum mismatch。
    #    单看基线会漏掉一类：**基线之后主线才加的迁移**，
    #    开发盒起跑时它还不存在，但它现在就在仓里。
    # 2. **基线里有的**（`git ls-tree`）——
    #    万一有人把已发布的迁移**删了**，它已经不在 ls-files 里了，
    #    但删掉一样起不来（Flyway 报 checksum mismatch / missing migration）。
    #
    # 两个集合取并集：改 + 删，两种破坏方式都拦住。
    baseline_migrations: set[str] = set()
    mig_rel = "server/src/main/resources/db/migration"
    proc = subprocess.run(
        ["git", "ls-files", "--", mig_rel],
        cwd=str(ws), capture_output=True, text=True, timeout=60,
    )
    if proc.returncode == 0:
        baseline_migrations = {
            line.strip() for line in proc.stdout.splitlines() if line.strip()
        }
    if base_ref:
        proc = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", base_ref, "--", mig_rel],
            cwd=str(ws), capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0:
            baseline_migrations |= {
                line.strip() for line in proc.stdout.splitlines() if line.strip()
            }
    problems: list[str] = []
    mig_dir = ws / "server" / "src" / "main" / "resources" / "db" / "migration"
    if not mig_dir.exists():
        return problems

    changed = {rel for rel in changed_files}
    # 仓里所有迁移（按文件名解析版本号）
    versions: dict[str, int] = {}
    bad_names: list[str] = []
    for path in sorted(mig_dir.glob("*.sql")):
        name = path.name
        rel = str(path.relative_to(ws))
        m = _FLYWAY_VERSIONED_RE.match(name)
        if m:
            versions[rel] = int(m.group(1))
            continue
        if _FLYWAY_REPEATABLE_RE.match(name):
            continue  # 可重复脚本没有版本号，正常
        bad_names.append(rel)

    # **仓里所有迁移的最大版本号**（建议新迁移时用）
    # —— 必须在下面用它之前算好。
    top_all = max(versions.values()) if versions else 0

    for rel in bad_names:
        problems.append(
            f"{rel} 的文件名不符合 Flyway 规范——"
            f"必须是 V<版本号>__<描述>.sql（例如 V{top_all + 1}__create_wms_zone.sql）"
            "或 R__<描述>.sql。"
            "写成 V__xxx.sql（没有版本号）的话，Flyway 启动时会直接拒绝："
            "Invalid version separator —— 服务根本起不来"
        )

    # 版本号重复（Flyway 也会拒绝）
    seen: dict[int, list[str]] = {}
    for rel, ver in versions.items():
        seen.setdefault(ver, []).append(rel)
    for ver, rels in sorted(seen.items()):
        if len(rels) > 1:
            # **说清楚该用哪个版本号**——只说「重复」的话，
            # 模型会反复挑同一个被占用的号（实测挑了 V5，改了又撞，25 轮打转）。
            problems.append(
                f"迁移版本号 V{ver} 重复（{'、'.join(sorted(rels))}）——"
                "Flyway 遇到重复版本会直接拒绝启动。"
                f"**改成 V{max(top_all + 1, ver + 1)}__<描述>.sql**"
                "（版本号必须比仓里已有的最大版本号大）"
            )

    # 版本号必须比「本次没改的那些」的最大版本号大
    unchanged = [v for rel, v in versions.items() if rel not in changed]
    if unchanged:
        # **版本上限取「当前工作区里所有迁移」的最大版本号**，
        # 不取基线里那些（2026-10-30 实测踩出来的）：
        # 基线是开发盒起跑时的那个提交，可能**早于后来主线上的迁移**。
        # 那时仓里已经有 V5__create_zone_table.sql，
        # 而基线里最大只有 V4 → 反馈一直建议「新建 V5」→
        # 模型照做 → 又被同一条理由拦回去 → **打了 30 轮**。
        #
        # **报错给的建议版本号必须是真的能用的那个**——
        # 给一个已被占用的版本号，比不给方向更坏：
        # 模型会**照着做**，然后被同一条理由再拦一次。
        # **判据的版本上限 = 「本次没改的那些」的最大版本号**（不含自己）——
        # 新增迁移只要比**别的**迁移大就行，拿自己比自己会误伤。
        top = max(unchanged)
        for rel, ver in sorted(versions.items(), key=lambda kv: kv[1]):
            if rel not in changed:
                continue
            # **已发布的迁移被改了** → 单独一条理由（不是「版本号要更大」）
            if rel in baseline_migrations:
                problems.append(
                    f"{rel} 是**已经发布过的迁移**，不许改它——"
                    "Flyway 会比对每个迁移的 checksum，改了内容就是 "
                    "checksum mismatch，**服务启动时直接拒绝**。"
                    f"要加东西（表、列、种子数据）就**新建一个迁移**："
                    f"V{top_all + 1}__<描述>.sql"
                    "（新迁移的版本号必须比仓里已有的最大版本号大）"
                )
                continue
            if ver <= top:
                problems.append(
                    f"{rel} 的版本号 V{ver} 不大于仓里已有的最大版本号 V{top}——"
                    "Flyway 默认不认乱序，版本号撞了或倒退了它会在启动时拒绝："
                    "Detected resolved migration not applied to database。"
                    f"新建迁移的话用 V{top_all + 1}__<描述>.sql"
                )
    return problems


def check_tests_not_shrunk(
    ws: Path,
    changed_files: list[str],
    base_ref: str = "",
) -> list[str]:
    """测试方法**只许增不许减**

    **为什么允许改测试**（2026-10-28 实测撞出来的）：
    服务多了一个依赖（比如库位服务要用 ZoneMapper 校验库区），
    仓里那个用 `@InjectMocks` 的老测试就会 NPE——
    Mockito 找不到对应 mock 就塞 null 进去。
    这时候**必须**给测试补一个 mock，否则整个构建挂在测试上。

    但「不许改测试」这条规矩不能因此破掉：
    模型完全可以把一个红测试改成不红。**分界线是「测试方法只能增」**：
    补 mock、加断言都行；把测例删掉、把断言改松不行。
    """
    problems: list[str] = []
    for rel in changed_files:
        if not rel.startswith("server/src/test") or not rel.endswith(".java"):
            continue
        path = ws / rel
        if not path.exists():
            continue
        current = set(_test_methods(_read(path)))
        baseline: set[str] = set()
        if base_ref:
            proc = subprocess.run(
                ["git", "show", f"{base_ref}:{rel}"],
                cwd=str(ws), capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                baseline = set(_test_methods(proc.stdout))
        lost = sorted(baseline - current)
        if lost:
            problems.append(
                f"{rel} 少了测试方法：{'、'.join(lost)}——"
                "测试只许增不许减（补 mock、加断言可以；删测例、改松断言不行）"
            )
    return problems


# 测试方法：@Test / @ParameterizedTest 标注的方法
_TEST_METHOD_RE = re.compile(
    r"@(?:Test|ParameterizedTest|RepeatedTest)\b[^\n]*\n\s*"
    r"(?:public\s+|private\s+|protected\s+)?[\w<>\[\], .?]+\s+(\w+)\s*\(",
)


def _test_methods(source: str) -> list[str]:
    """这份测试文件里有哪些测试方法"""
    return [m.group(1) for m in _TEST_METHOD_RE.finditer(source)]


def check_duplicate_classes(
    ws: Path,
    changed_files: list[str],
) -> list[str]:
    """同一份代码里不许有两个同名类

    **为什么要有这条**（2026-10-28 实测撞出来的）：
    模型为了表达「库位归属在库区上」，在 `module/location/` 下**又建了一份
    Zone 实体**，在 `module/zone/` 下**又建了一份 Location 实体**——
    跟 `com/wms.entity` 里原有的那份重名。

    编译过、单测过（没人碰这两个类）、判据也过了大半，
    可代码里从此有两份「库区实体」：改一处、另一处不变，
    **数据从哪来、到哪去，没人说得清**。

    这跟「迁移覆盖」「SQL 绑定」是同一族：
    **运行期才暴露的东西，要在交付前机械核一遍。**
    """
    problems: list[str] = []
    base = ws / "server" / "src" / "main" / "java"
    if not base.exists():
        return problems

    # 类名 → 声明它的文件
    declared: dict[str, list[str]] = {}
    for path in sorted(base.rglob("*.java")):
        rel = str(path.relative_to(ws))
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name in _declared_class_names(source):
            declared.setdefault(name, []).append(rel)

    changed = {f for f in changed_files if f.endswith(".java")}
    for name, files in sorted(declared.items()):
        if len(files) < 2:
            continue
        touched = [f for f in files if f in changed]
        if not touched:
            continue
        problems.append(
            f"类 {name} 在仓里被声明了 {len(files)} 次：{'、'.join(sorted(files))}——"
            "同一个类只能有一份。"
            "（要表达库位与库区的归属关系，用**外键字段**"
            "（例如库位实体上的库区编码/库区 id），"
            "不要把实体复制一份到别的包里）"
        )
    return problems


# Java 里声明的类名（class X / interface X / enum X / record X）
_CLASS_DECL_RE = re.compile(
    r"\b(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)",
)


def _declared_class_names(source: str) -> list[str]:
    """这份源码里声明了哪些类（只认顶格的声明，避免把方法里的 new 算进来）"""
    out: list[str] = []
    for line in source.splitlines():
        if not line[:1].strip():
            # **行首有空白 = 缩进 = 不是顶层声明**
            #（内部类、方法体里的东西都不算）
            continue
        match = re.match(
            r"(?:public\s+|abstract\s+|final\s+|static\s+)*"
            r"(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)",
            line,
        )
        if match:
            out.append(match.group(1))
    return out


def check_mapper_bindings(
    ws: Path,
    changed_files: list[str],
    base_ref: str = "",
) -> list[str]:
    """本次改动里新增的 Mapper 方法，有没有对应的 SQL 语句

    Returns: 问题清单（空 = 没问题）
    """
    problems: list[str] = []
    for rel in changed_files:
        if not rel.endswith("Mapper.java"):
            continue
        if "src/main/java" not in rel:
            continue
        path = ws / rel
        if not path.exists():
            continue
        source = _read(path)
        if "interface" not in source:
            continue
        # 接口继承了别的东西 → 分不清哪些方法是继承来的，跳过（不猜）
        if re.search(r"interface\s+\w+\s+extends\s+", source):
            continue

        old_source = _baseline_source(ws, base_ref, rel)
        old_methods = set(_declared_methods(old_source)) if old_source else set()
        declared = _declared_methods(source)
        annotated = _annotated_methods(source)
        mapper_name = path.stem
        ids = _xml_ids(ws, mapper_name)
        if not ids:
            xml_path = ws / "server" / "src" / "main" / "resources" / "mapper" / f"{mapper_name}.xml"
            if not xml_path.exists():
                # 没有 XML：这个接口也许全是注解式 SQL
                if annotated:
                    continue

        for name in declared:
            if name in old_methods:
                continue  # 老方法，不是这次新增的
            if name in annotated:
                continue
            if name in ids:
                continue
            problems.append(
                f"{rel} 里新增的 `{name}()` 在 "
                f"server/src/main/resources/mapper/{mapper_name}.xml "
                f"里没有对应的 <select id=\"{name}\">（或 @Select 注解）——"
                "MyBatis 是运行期绑定：编译和单测都发现不了，"
                "上线后一调这个接口就 Invalid bound statement（看板直接 500）"
            )
    return problems
