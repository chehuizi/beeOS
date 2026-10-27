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
