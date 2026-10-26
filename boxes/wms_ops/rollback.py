"""
boxes.wms_ops.rollback - 「回滚目标取得到吗」

**回滚能力不是一个承诺，是一个事实**：回滚目标必须**真的取得到**。

两种目标形态，各有各的查法：

| 目标形态 | 查法 | 依据 |
|---|---|---|
| `sha256:…` 制品摘要 | 制品库里翻 meta.json | 开发盒留下���制品目录 |
| git 引用（sha / 分支 / 标签） | WMS 仓里 `git cat-file -e` | **提交对象真的在仓里** |

**查不到 ≠ 不存在，但也不是「能回滚」**：
回滚能力是「出事时能不能退回去」——目标取不到，就退不回去。
所以查不到就判 False，理由里说清是「环境不具备」还是「目标不存在」，
两者不能混：前者该去装工具链，后者该去修目标。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

# 制品库的环境变量（开发盒往这里放编出来的那份 jar + meta.json）
ARTIFACT_STORE_ENV = "BEEOS_ARTIFACT_STORE"
# 兜底：开发盒的工作区根目录（它把制品放在 <root>/artifacts 下）
DEV_WORKSPACE_ENV = "BEEOS_DEV_WORKSPACE"

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{8,64}$")


def artifact_store() -> Path:
    """制品库在哪"""
    raw = (os.environ.get(ARTIFACT_STORE_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser()
    dev_ws = (os.environ.get(DEV_WORKSPACE_ENV) or "").strip()
    if dev_ws:
        return Path(dev_ws).expanduser() / "artifacts"
    return Path("/tmp/beeos-dev") / "artifacts"


def _find_in_store(target: str, store: Path) -> tuple[bool, str, dict[str, Any]]:
    """在制品库里按摘要找"""
    detail: dict[str, Any] = {"artifact_store": str(store)}
    if not store.exists():
        return False, f"制品库不存在：{store}——开发盒的产物不在手边", detail
    for meta_path in store.glob("*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        digest = str(meta.get("artifact_digest") or "")
        commit = str(meta.get("commit_sha") or "")
        if (digest and digest.startswith(target)) or (commit and commit.startswith(target)):
            detail["artifact"] = meta
            return True, f"回滚目标在制品库里：{meta_path.parent.name}", detail
    return False, f"制品库里没有匹配 {target} 的构建", detail


def _find_in_repo(target: str) -> tuple[bool, str, dict[str, Any]]:
    """在 WMS 仓里查这个 git 引用"""
    detail: dict[str, Any] = {}
    raw = (os.environ.get("BEEOS_WMS_REPO") or "").strip()
    if not raw:
        return False, (
            "没配 BEEOS_WMS_REPO，回滚目标取不到"
            "（这是**环境不具备**，不是「目标不存在」——该去装环境，不是该改目标）"
        ), detail
    repo = Path(raw).expanduser()
    if not (repo / ".git").exists():
        return False, f"BEEOS_WMS_REPO 指向的不是 git 仓：{repo}", detail
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{target}^{{commit}}"],
        cwd=str(repo), capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        return False, f"WMS 仓里没有 {target} 这个提交/引用", detail
    sha = subprocess.run(
        ["git", "rev-parse", f"{target}^{{commit}}"],
        cwd=str(repo), capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    detail["resolved_commit"] = sha
    return True, f"回滚目标在 WMS 仓里：{sha[:8]}", detail


def resolve(target: str) -> tuple[bool, str, dict[str, Any]]:
    """这个回滚目标取得到吗"""
    ref = (target or "").strip()
    detail: dict[str, Any] = {"target": ref}
    if not ref:
        return False, "没给回滚目标——出事时退不回去", detail
    if _DIGEST_RE.match(ref):
        ok, reason, d = _find_in_store(ref, artifact_store())
        detail.update(d)
        return ok, reason, detail
    if _SHA_RE.match(ref) or ref.startswith(("refs/", "branch:", "tag:")):
        ok, reason, d = _find_in_repo(ref.replace("branch:", "").replace("tag:", ""))
        detail.update(d)
        return ok, reason, detail
    # 不是摘要也不是 sha：先当分支/标签查，查不到再当制品目录名查
    ok, reason, d = _find_in_repo(ref)
    if ok:
        detail.update(d)
        return ok, reason, detail
    ok2, reason2, d2 = _find_in_store(ref, artifact_store())
    detail.update(d2)
    if ok2:
        return True, reason2, detail
    return False, f"{reason}；{reason2}", detail
