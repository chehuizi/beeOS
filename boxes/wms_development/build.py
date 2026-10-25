"""
boxes.wms_development.build - 构建 + 单测 + 复现性（**外部神谕判定**）

**这一跳的每个判据都由外部工具给出，不是盒子自述**：

| 判据 | 谁给答案 |
|---|---|
| build_failed | maven 进程退出码 |
| unit_test_pass_rate / unit_test_green | 仓库自己的测试套件（surefire 报告） |
| build_reproducibility | **构建两遍**，比较编译产物的摘要 |
| artifact_traceable | 制品摘要 ↔ 提交 sha（git） |

**为什么摘要算编译产物而不是 jar**：Spring Boot 的 repackage 会把
构建时间打进 jar 里 → 同一个提交构建两次，jar 的 sha256 必然不同 →
「可复现」永远判红，而它其实可复现（class 文件是确定的）。
所以摘要取 **target/classes**（编译结果）的内容摘要。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

# maven 可执行：环境变量优先，找不到就用 PATH 里的 mvn
MVN_ENV = "BEEOS_MVN"
DEFAULT_MVN = "mvn"


class BuildError(RuntimeError):
    """构建这一步做不下去（环境不具备 / 命令跑不动）"""


def mvn_cmd() -> list[str]:
    """找到 maven

    环境变量优先；然后找 PATH 里的 `mvn`。**都没有就直接报错**——
    找不到构建工具是「环境不具备」，不是「构建失败」，
    把两者混成同一个结果，看板上就会显示成「代码编译不过」。
    """
    import os
    import shutil
    raw = (os.environ.get(MVN_ENV) or "").strip()
    if raw:
        return [raw]
    found = shutil.which(DEFAULT_MVN)
    if found:
        return [found]
    raise BuildError(
        f"找不到 {DEFAULT_MVN}——用 {MVN_ENV} 指到 maven 可执行文件"
        f"（这是环境不具备，不是构建失败）"
    )


def server_dir(ws: Path) -> Path:
    d = ws / "server"
    if not (d / "pom.xml").exists():
        raise BuildError(f"工作区里没有 server/pom.xml：{ws}")
    return d


def run_build(ws: Path, log_dir: Optional[Path] = None, timeout: int = 900,
              log_name: str = "mvn-package.log") -> dict[str, Any]:
    """跑一次构建 + 单测

    返回 build_failed / build_duration_s / 日志落点 / 摘要。
    **构建失败不算异常**——那是业务结果（判据判红 + exception 规则 no_deliver），
    只有「构建跑不起来」（没有 maven / 没有 pom）才是异常。

    **日志不写在工作区里**：工作区是 git 仓，日志落在里面会被
    `git add -A` 一起提交进交付物——那是把噪音混进交付。
    """
    d = server_dir(ws)
    if log_dir is None:
        log_dir = ws.parent / f"{ws.name}-build"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_name

    cmd = mvn_cmd() + ["-o", "-B", "package"]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(d), capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        rc = proc.returncode
    except FileNotFoundError as exc:
        raise BuildError(f"找不到构建命令 {cmd[0]}：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"构建超时（{timeout}s）") from exc
    duration = round(time.time() - started, 2)
    log_path.write_text(out, encoding="utf-8")

    return {
        "build_failed": rc != 0,
        "build_duration_s": duration,
        "build_log_ref": str(log_path),
        "exit_code": rc,
        "classes_digest": classes_digest(ws),
        "jar_path": str(next((ws / "server" / "target").glob("*.jar"), "")) if (
            (ws / "server" / "target").exists()) else "",
    }


def run_compile(ws: Path, timeout: int = 900) -> dict[str, Any]:
    """只编译（不跑测试）——给 codegen 那一跳当反馈用

    **编译器是最诚实的神谕**：模型说它改对了不算，
    「找不到符号」这句话就是判据。
    """
    d = server_dir(ws)
    cmd = mvn_cmd() + ["-o", "-B", "compile"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(d), capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BuildError(f"找不到构建命令 {cmd[0]}：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"编译超时（{timeout}s）") from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "errors": extract_compile_errors(out),
    }


_ERROR_BLOCK = re.compile(r"\[ERROR\]\s+COMPILATION ERROR.*?(?=\[INFO\] BUILD|\Z)", re.S)


def extract_compile_errors(output: str, limit: int = 40) -> str:
    """从 maven 输出里摘出编译错误那一段

    **只摘错误段**：整份 maven 日志几千行，喂回给模型只会把它淹掉——
    而它真正需要看的就是「哪个文件、哪一行、什么符号」。
    """
    m = _ERROR_BLOCK.search(output)
    block = m.group(0) if m else ""
    if not block:
        lines = [ln for ln in output.splitlines() if "[ERROR]" in ln]
        block = "\n".join(lines[:limit])
    lines = [ln for ln in block.splitlines() if ln.strip()][:limit]
    return "\n".join(lines)


def classes_digest(ws: Path) -> str:
    """编译产物（target/classes）的内容摘要

    **排序后再摘要**——目录遍历顺序不稳定，直接哈希会得到假的不一致。
    """
    classes = ws / "server" / "target" / "classes"
    if not classes.exists():
        return ""
    h = hashlib.sha256()
    for path in sorted(p for p in classes.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(classes)).encode("utf-8"))
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


_SURESTS = re.compile(
    r'tests="(?P<tests>\d+)"\s+errors="(?P<errors>\d+)"\s+skipped="(?P<skipped>\d+)"\s+failures="(?P<failures>\d+)"'
)


def unit_test_stats(ws: Path) -> dict[str, Any]:
    """从 surefire 报告里读单测结果

    **读报告而不是解析日志文本**：日志格式会变，XML 是契约。
    """
    reports = ws / "server" / "target" / "surefire-reports"
    total = failures = errors = skipped = 0
    suites = 0
    if reports.exists():
        for xml in reports.glob("TEST-*.xml"):
            try:
                root = ET.parse(xml).getroot()
            except ET.ParseError:
                continue
            suites += 1
            total += int(root.get("tests") or 0)
            failures += int(root.get("failures") or 0)
            errors += int(root.get("errors") or 0)
            skipped += int(root.get("skipped") or 0)
    passed = max(total - failures - errors - skipped, 0)
    rate = round(passed / total, 4) if total else 0.0
    return {
        "test_count": total,
        "failure_count": failures,
        "error_count": errors,
        "skipped_count": skipped,
        "unit_test_pass_rate": rate,
        "unit_test_green": total > 0 and failures == 0 and errors == 0,
        "suite_count": suites,
    }


def reproducibility(ws: Path, first_digest: str) -> dict[str, Any]:
    """构建两遍，比较摘要

    **只对比摘要，不对比时间戳**（见模块头）。
    第二遍构建失败 = 不可复现（而且这事本身就该判红）。

    第二遍的日志另存一份：第一遍的日志是「这次交付编译成什么样」的证据，
    被第二遍覆盖掉就没了。
    """
    if not first_digest:
        return {"build_reproducible": False, "reproducibility_digest": ""}
    second = run_build(ws, log_name="mvn-package-second.log")
    second_digest = second.get("classes_digest") or ""
    return {
        "build_reproducible": bool(second_digest) and second_digest == first_digest,
        "reproducibility_digest": second_digest,
        "second_build_failed": bool(second.get("build_failed")),
    }


def commit_range_files(ws: Path, base_sha: Optional[str] = None) -> list[str]:
    """相对基线改了哪些文件"""
    cmd = ["git", "diff", "--name-only"]
    if base_sha:
        cmd.append(base_sha)
    proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
