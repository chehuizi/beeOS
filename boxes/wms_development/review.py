"""
boxes.wms_development.review - 「改动是不是把方案落到了代码里」

**这两条判据是这只盒子最难的部分，而它们都不能由这只盒子自述。**

| 判据 | 谁给答案 |
|---|---|
| review_coverage | git（改了哪些文件）+ 概念→源码位置表 |
| artifact_traceable | git（提交在不在、分支在不在）+ 构建产物的内容摘要 |

`review_coverage` 的口径（三条，缺一条就蒙混不过去）：

1. **改动文件 ⊆（该设计包覆盖的 WMS 概念对应源码位置 ∪ 测试目录）**
   ——改到方案没覆盖的地方 = 这次交付跟方案无关。
2. **至少一个改动文件落在业务代码位置里**（不是只在测试目录）
   ——只改测试等于「把判据改成绿的」，那不叫落地。
3. **有改动**（改动文件非空）
   ——一条都没改，说不清是「方案已落地」还是「根本没做」。
   而「模型说它已经满足了」是**自述**，不是神谕，不能拿来当证据。

**为什么第 3 条不放过「已实现」**：开发盒说「这条需求代码里已经有了」
是一种**自述**。自述在第 2 号判据里已经被禁过一次（不许只改测试），
在这里再放一次，等于给模型开了一道「我保证不用改」的免检门。
真正该由「已实现」来回答的是**测试盒**——它跑的是真实业务场景。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional

from boxes.wms_development.code_map import is_test_path, path_in_scope


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60,
    )


def coverage(
    changed_files: list[str],
    locations: list[str],
    states: Optional[dict[str, str]] = None,
    expectation_concepts: Optional[list[str]] = None,
) -> tuple[bool, str, dict[str, Any]]:
    """改动有没有把方案落到代码里

    Returns: (通过?, 原因, 明细)

    **两种通过形态**：
    - 改了：改动 ⊆ 方案覆盖的位置，且至少动了一个业务代码文件
    - 没改：**每个可判定概念在源码里都已有实现**（机械核对出来的证据）

    第二种不是「模型说不用改」——那只是自述。证据是源码里
    真的出现了这个概念的标识符（见 code_map 的实现证据表）。
    """
    changed = [f for f in changed_files if f]
    out_of_scope = [f for f in changed if not path_in_scope(f, locations)]
    business = [f for f in changed if not is_test_path(f) and path_in_scope(f, locations)]
    test_only = [f for f in changed if is_test_path(f)]

    state_map = states or {}
    detail: dict[str, Any] = {
        "changed_files": changed,
        "out_of_scope_files": out_of_scope,
        "business_code_files": business,
        "test_files": test_only,
        "allowed_locations": list(locations),
        "concept_states": dict(state_map),
    }

    if not changed:
        # 零改动：只有「每个可判定概念都已实现（有证据）」才算交付
        missing = [c for c, s in state_map.items() if s == "missing"]
        if missing:
            return False, (
                f"没有任何改动，而这些概念在源码里查不到实现：{'、'.join(missing)}——"
                "方案还没落地"
            ), detail
        wanted = list(expectation_concepts or [])
        unknown = [c for c in wanted if state_map.get(c, "unknown") == "unknown"]
        if unknown:
            return False, (
                f"没有任何改动，而这些概念的实现状态判不了（没有登记实现证据）："
                f"{'、'.join(unknown)}——说不出证据就不算落地"
            ), detail
        if wanted:
            return True, (
                f"方案涉及的每个可判定概念在源码里都已有实现"
                f"（{'、'.join(wanted)}），本次无需改动"
            ), detail
        return False, (
            "没有任何改动——方案要么代码里已经有了（模型自述，不可作数），"
            "要么根本没落地。这次交付拿不出「落地」的证据。"
        ), detail
    if out_of_scope:
        return False, (
            f"改了方案没覆盖的文件：{'、'.join(out_of_scope)}——"
            "改动必须落在该设计包覆盖的 WMS 概念对应的源码位置里"
        ), detail
    if not business:
        return False, (
            f"只改了测试文件（{'、'.join(test_only)}），业务代码一个字没动——"
            "把判据改绿不叫落地"
        ), detail
    return True, (
        f"改了 {len(business)} 个业务代码文件（{'、'.join(business)}），"
        "都在方案覆盖的源码位置里"
    ), detail


def traceable(
    commit_sha: Optional[str],
    branch: Optional[str],
    artifact_digest: Optional[str],
    repo: Path,
) -> tuple[bool, str, dict[str, Any]]:
    """这份制品能不能追回到一个具体的提交

    **三条都要成立**：提交对象在仓里、交付分支在仓里、构建产物有内容摘要。
    缺一条就是「追不回去」——读者手上有一份 jar，说不出它是哪次改动编出来的。
    """
    detail: dict[str, Any] = {
        "commit_sha": commit_sha or "",
        "branch": branch or "",
        "artifact_digest": artifact_digest or "",
    }

    if not commit_sha:
        return False, "没有提交——这份产物追不到任何一次改动", detail
    if not artifact_digest:
        return False, "构建产物没有内容摘要——追不到是哪份编译输出", detail

    exists = _git(repo, "cat-file", "-e", f"{commit_sha}^{{commit}}")
    if exists.returncode != 0:
        return False, f"提交 {commit_sha} 不在仓里——这份产物追不回去", detail

    if not branch:
        return False, "没有交付分支——提交只活在临时工作区里，取不回来", detail
    branch_exists = _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
    if branch_exists.returncode != 0:
        return False, f"交付分支 {branch} 不在仓里——提交取不回来", detail

    # 分支指向的提交必须就是这份制品对应的那个提交
    head = _git(repo, "rev-parse", f"refs/heads/{branch}").stdout.strip()
    if head != commit_sha:
        return False, (
            f"交付分支 {branch} 指向 {head[:8]}，但这份制品对应的是 "
            f"{commit_sha[:8]}——追回去追到的不是这份东西"
        ), detail

    return True, (
        f"可追溯到提交 {commit_sha[:8]}（分支 {branch}，产物摘要 "
        f"{artifact_digest[:16]}…）"
    ), detail
