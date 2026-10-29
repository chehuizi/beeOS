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
    # 基线里已有的迁移（用来区分「新增」和「改已发布的」）
    baseline_migrations: set[str] = set()
    if base_ref:
        proc = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", base_ref,
             "--", "server/src/main/resources/db/migration"],
            cwd=str(ws), capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0:
            baseline_migrations = {
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

    for rel in bad_names:
        problems.append(
            f"{rel} 的文件名不符合 Flyway 规范——"
            "必须是 V<版本号>__<描述>.sql（例如 V5__create_wms_zone.sql）"
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
            problems.append(
                f"迁移版本号 V{ver} 重复（{'、'.join(sorted(rels))}）——"
                "Flyway 遇到重复版本会直接拒绝启动"
            )

    # 版本号必须比「本次没改的那些」的最大版本号大
    unchanged = [v for rel, v in versions.items() if rel not in changed]
    if unchanged:
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
                    f"V{top + 1}__<描述>.sql"
                    "（新迁移的版本号必须比仓里已有的最大版本号大）"
                )
                continue
            if ver <= top:
                problems.append(
                    f"{rel} 的版本号 V{ver} 不大于仓里已有的最大版本号 V{top}——"
                    "Flyway 默认不认乱序，版本号撞了或倒退了它会在启动时拒绝："
                    "Detected resolved migration not applied to database。"
                    f"新建迁移的话用 V{top + 1}__<描述>.sql"
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
