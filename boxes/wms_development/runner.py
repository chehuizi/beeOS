"""
boxes.wms_development.runner - WMS Development Box 的 4 个 operation 实现

WMS 开发盒——**按设计包改代码**：读方案 → 定位源码 → 改 → 构建 → 制品。

    dev_intake      读设计包，算出「要满足哪些预期 + 这些概念落在哪些源码位置」
    dev_code        在隔离工作区里让模型改代码，机械应用，提交
    dev_build       编译 + 跑仓库自己的测试套件 + 构建两遍比摘要
    build_packager  打包成制品，并给出 review_coverage / artifact_traceable

**这一跳链上没有一个答案来自这只盒子自述**：

| 判据 | 谁给答案 |
|---|---|
| build_failed | maven 退出码 |
| unit_test_green / unit_test_pass_rate | 仓库自己的测试套件（surefire 报告） |
| build_reproducibility | 构建两遍，比编译产物的内容摘要 |
| review_coverage | git 改了哪些文件 × 概念→源码位置表 |
| artifact_traceable | git（提交在不在、分支在不在） |

**「按方案生成代码」的两条硬约束在这里落地**：

1. **改动必须由机械层逐字校验**（old 唯一命中），落不下去就把错误
   原样回给模型改——不许自己脑补一段「差不多」的代码。
2. **判据跑外部神谕**：编译器 + 仓库自带测试套件 + git。
   模型说什么不算数。

**不降级**：模型不可用 / 改不动 / 没 maven → 履约中止（抛错），
不产出「看起来像改了」的制品。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from boxes.wms_development import review
from boxes.wms_development.build import (
    commit_range_files,
    reproducibility,
    run_build,
    run_frontend_build,
    run_verify_build,
    unit_test_stats,
)
from boxes.wms_development.code_map import (
    implementation_state,
    missing_evidence,
    implementation_states,
    implementation_evidence_description,
    locations_for,
    unmapped_concepts,
)
from boxes.wms_development.codegen import (
    CodegenError,
    changed_files,
    collect_all_sources,
    collect_context,
    collect_tree,
    commit_changes,
    generate_edits,
    prepare_workspace,
    prune_stale_workspaces,
    repo_path,
    workspace_root,
)
from boxes.wms_development.mapper_binding import (
    check_duplicate_classes,
    check_mapper_bindings,
    check_tests_not_shrunk,
    check_migration_covers_tables,
    check_migration_filenames,
)
from runtime.runner import HandlerRunner


def _test_sources_for_feedback(ws: Path, test_errors: str) -> str:
    """把出错的**测试文件原文**附在报错里

    **为什么必须附上**：模型要改测试，就得能**逐字抄**出要替换的那一段。
    而测试文件**不在它的上下文里**（上下文只扫 `server/src/main`）——
    抄不出来，它就只能「整篇覆盖」（old 留空），
    机械层会正确地拒绝（那不是改，是重写），于是它一轮轮空转
    （实测连着两轮都是这个错）。

    报错要带能修它的材料——测试文件原文就是那份材料。
    """
    names = set(re.findall(r"([A-Za-z0-9_]+Test)\.java", test_errors))
    names |= set(re.findall(r"([A-Za-z0-9_]+Test)\b", test_errors))
    if not names:
        return ""
    blocks: list[str] = []
    for name in sorted(names)[:3]:
        for path in sorted(
            (ws / "server" / "src" / "test").rglob(f"{name}.java")
        ):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            blocks.append(
                f"\n**出问题���测试文件现在长这样（从这里逐字复制要替换的那一段）**：\n"
                f"===== {path.relative_to(ws)} =====\n{text}"
            )
            break
    return "".join(blocks)


def _base_ref(commit_range: Any) -> str:
    """从 commit_range 里取**基线提交**

    `"c0a7aa2..HEAD"` → `"c0a7aa2"`：前面那截是「改动相对于谁」，
    不是「要改哪一段区间」。空 → HEAD。
    """
    raw = str(commit_range or "").strip()
    if not raw:
        return "HEAD"
    head = raw.split("..")[0].strip()
    return head or "HEAD"


def _concepts_of(design: dict[str, Any], expectations: list[dict[str, Any]]) -> list[str]:
    """这次要落地的 WMS 概念（去重、保序）

    从两头取：预期挂着的概念 + 模型元素绑着的概念。
    只取一头会漏——有的需求只出现在模型元素里，有的只出现在预期里。
    """
    out: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip() and value.strip() not in out:
            out.append(value.strip())

    for exp in expectations:
        add(exp.get("wms_kind"))
    elements = design.get("model_elements") or {}
    if isinstance(elements, dict):
        for group in elements.values():
            if not isinstance(group, list):
                continue
            for el in group:
                if not isinstance(el, dict):
                    continue
                add(el.get("wms_kind"))
                add(el.get("wms_constraint"))
                for binding in el.get("wms_bindings") or []:
                    if isinstance(binding, dict):
                        add(binding.get("kind"))
    return out


def dev_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：读设计包 → 开发计划

    **这一跳只做翻译**：把「业务方要什么」翻成「要满足哪些可判定预期」
    +「这些概念在代码里落在哪几处」。

    没有落点的概念**不猜**，但要看得见（unmapped_concepts）——
    「方案里有个概念代码里不存在」是业务事实，不是噪声。
    """
    design = input_data.get("design_package_ref") or {}
    if not isinstance(design, dict):
        raise CodegenError(
            f"design_package_ref 不是对象（{type(design).__name__}）——"
            "开发盒拿不到设计包就没法按方案改代码"
        )
    expectations = [
        e for e in (design.get("expectations") or []) if isinstance(e, dict)
    ]
    concepts = _concepts_of(design, expectations)
    locations = locations_for(concepts)
    return {
        "dev_task_id": str(input_data.get("dev_task_id") or ""),
        "package_id": str(design.get("package_id") or ""),
        "set_id": str(design.get("set_id") or ""),
        "concepts": concepts,
        "locations": locations,
        "unmapped_concepts": unmapped_concepts(concepts),
        "expectations": expectations,
        "base_ref": _base_ref(input_data.get("commit_range")),
        "target_env": str(input_data.get("target_env") or ""),
    }


def dev_code_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：在隔离工作区里按方案改代码

    **隔离工作区是硬要求**：这里真的会动文件，动的是 WMS 主仓的话，
    一次跑失败的履约就把别人的工作现场弄脏了。
    """
    dev_task_id = str(input_data.get("dev_task_id") or "")
    expectations = input_data.get("expectations") or []
    locations = input_data.get("locations") or []
    concepts = input_data.get("concepts") or []
    base_ref = str(input_data.get("base_ref") or "HEAD")

    if not locations:
        unmapped = input_data.get("unmapped_concepts") or []
        raise CodegenError(
            "方案里的 WMS 概念在代码里没有落点"
            f"（{'、'.join(unmapped) or '（没抽到任何概念）'}）——"
            "没有落点就改不了（履约中止，不降级成『改了点别的』）"
        )

    prune_stale_workspaces()
    ws = prepare_workspace(base_ref, dev_task_id)
    files = collect_context(ws, locations)
    if not files:
        raise CodegenError(
            f"按位置 {locations} 在仓里一个源码文件都没找到——"
            "落点表跟仓库对不上（改不了就是改不了，不许瞎改）"
        )

    # **这次要不要验前端**：看方案有没有前端落点。
    # 后端需求就不跑 npm（跑一次几十秒，还要 node_modules），
    # 前端需求必须跑（后端编译发现不了 vue-tsc 的类型错误）。
    frontend_expected = any(str(loc).startswith("web/") for loc in locations)

    # ---- 机械核对：哪些概念在源码里还没有实现 ----
    #
    # **这一步是「已实现」这句话的神谕**：模型说「看板已经有了」不算，
    # 源码里有没有那个标识符、且那个标识符**在对的文件里**才算。
    #
    # **为什么必须带文件视图**（2026-10-27 实测）：上一轮开发盒把拣货准确率
    # 做成了独立接口，里面有个方法叫 `pickAccuracy()` → 全仓搜得到
    # 「pickAccuracy」→ 判成「已实现」就放行了。可业务方要的是
    # **「在库存看板看得到」**——那个字段在看板 VO 上，它压根不在。
    # 全仓匹配分不出「看板上有这个数」和「别处有个同名方法」。
    source_text = "\n".join(files.values())
    states = implementation_states(concepts, source_text, files)
    # **只算预期挂着的概念**：模型元素里的实体（sku/lot…）不是这次要验的东西，
    # 它们没有实现证据也不该拖垮这次交付的判定。
    expectation_concepts = [
        c for c in dict.fromkeys(
            str(e.get("wms_kind") or "") for e in expectations
        ) if c
    ]
    missing = [
        (c, implementation_evidence_description(c))
        for c, s in states.items() if s == "missing"
    ]

    def _refresh() -> dict[str, str]:
        """重试时的上下文：**当前**工作区的内容（含上一轮的改动）

        模型下一轮必须看得见自己写坏的那一行，否则它修不了。
        """
        return collect_context(ws, locations)

    def _verify() -> list[str]:
        """完整核对：概念实现了没 + 代码编译得过吗 + SQL 绑得上吗 + 表建了没

        **四样都是外部神谕**：
        - 概念的实现证据（源码里有没有那个标识符，且在对的文件里）
        - 编译器（找不到符号就说明没写对）
        - MyBatis 的 SQL 绑定（接口方法在 XML 里有没有对应语句）
        - 迁移覆盖（SQL 里的表有没有建表脚本）

        上一轮真跑就撞上了：模型把新 VO 用在了 Mapper 里，
        却忘了 import → 编译器说「找不到符号」。
        这段错误原样回给它，它自己会改。

        **SQL 绑定为什么也要查**（2026-10-27 实测）：
        MyBatis 的绑定是**运行期**的——接口加了方法、XML 里没有同名语句，
        编译过、单测过（仓里根本没有报表测试），上线一调就 500。
        """
        problems: list[str] = []
        problems.extend(_verify_evidence())
        # **编译 + 测试都跑**：只编译的话测试挂了没人告诉模型，
        # 它以为写完了，最后打包那步才炸——那时已经没有轮次可改了。
        comp = run_verify_build(ws)
        if not comp.get("ok"):
            if comp.get("errors"):
                problems.append(
                    "代码编译不过（编译器说的，不是我说的）：\n"
                    f"{comp.get('errors')}"
                )
            if comp.get("test_errors"):
                problems.append(
                    "仓里的测试跑不过（测试说的，不是我说的）：\n"
                    f"{comp.get('test_errors')}\n"
                    "——如果是代码的依赖变了导致老测试缺依赖"
                    "（服务多了一个 Mapper，@InjectMocks 的老测试会 NPE），"
                    "**给那个测试补上对应的 @Mock**（测试方法只许增不许减，"
                    "不许删测例、不许削弱断言）"
                    + _test_sources_for_feedback(ws, comp.get("test_errors") or "")
                )
            if not comp.get("errors") and not comp.get("test_errors"):
                problems.append("构建没过，但没摘到错误详情——去看构建日志")
        # **前端构建**：这次方案涉及前端才跑。
        # vue-tsc 的类型错误后端编译**发现不了**（前端不进 maven），
        # 但页面就是坏的——所以前端也要有外部神谕。
        if frontend_expected:
            fe = run_frontend_build(ws)
            if not fe.get("ok"):
                problems.append(
                    "前端构建不过（vue-tsc / vite 说的，不是我说的）：\n"
                    + (fe.get("errors") or "（没摘到错误详情——去看前端构建输出）")
                    + "\n——前端文件在 web/src 下："
                    "页面 .vue、接口封装 api/*.ts、类型 types/*.ts、"
                    "路由在 web/src/router.ts、菜单在 web/src/layouts/BasicLayout.vue"
                )
        problems.extend(_verify_fast())
        return problems

    def _verify_fast() -> list[str]:
        """便宜的那部分检查：每写完一个文件就跑一次

        **为什么这��不用等「写完了」**：SQL 绑定和迁移覆盖跟
        「模块写完没有」无关——缺了就永远缺，早发现早回喂。
        而**编译**必须等写完：模块分批写的时候中间状态本来就编译不过
        （Controller 引用的类还没建），每写一个文件就编译等于逼模型
        倒着写顺序——那是机械层在替它做设计决策。
        """
        problems: list[str] = []
        # **SQL 绑定**：新增的 Mapper 方法必须有对应的语句
        problems.extend(check_mapper_bindings(
            ws, changed_files(ws), base_ref=base_ref,
        ))
        # **迁移覆盖**：SQL 里用到的表必须由迁移建出来
        problems.extend(check_migration_covers_tables(ws, changed_files(ws)))
        # **迁移文件名**：必须符合 Flyway 规范（V<n>__xxx.sql），
        # 版本号还得比已有的大——写错了 Flyway 启动时直接拒绝，
        # **服务根本起不来**（编译和单测都发现不了）。
        # **带基线**：这样才能分清「新建的迁移」（版本号要比已有的大）
        # 和「改了已发布的迁移」（不许改，要新建）——
        # 两种错误要给**不同的理由**，理由不对模型就会原地打转。
        problems.extend(check_migration_filenames(
            ws, changed_files(ws), base_ref=base_ref,
        ))
        # **重名类**：模型为了表达归属关系，会把实体复制一份到别的包里
        # （实测：module/location/Zone.java + module/zone/Location.java）。
        # 编译得过，坑却留在仓里——改一处、另一处不变，没人说得清数据从哪来。
        problems.extend(check_duplicate_classes(ws, changed_files(ws)))
        # **测试方法只许增不许减**——允许模型给老测试补 mock
        # （服务加了依赖，@InjectMocks 的老测试会 NPE），
        # 但不许它把红测试改成不红。
        problems.extend(check_tests_not_shrunk(
            ws, changed_files(ws), base_ref=base_ref,
        ))
        return problems

    def _verify_evidence() -> list[str]:
        """概念的实现证据（源码里有没有那个标识符，且在对的文件里）

        **看整个工作区**，不是只看喂给模型的那份上下文——
        上下文是截断的，模型新写的文件挤不进上下文的话，
        机械层就看不见它 → 判「没实现」→ 模型被反复要求写同一个东西，
        一轮轮空转到轮数上限（实测连着 8 轮）。

        **报错要点名到具体哪一处**（哪个文件里缺哪个标识符）：
        只说「zone 找不到」，模型不知道该写什么。
        """
        after_files = collect_all_sources(ws)
        after = "\n".join(after_files.values())
        gaps: list[str] = []
        for concept, _desc in missing:
            lacking = missing_evidence(concept, after_files, after)
            if not lacking:
                continue
            detail = "、".join(
                f"{hint} 里的 {tok}" for hint, tok in lacking
            )
            gaps.append(f"- {concept}：还缺 {detail}")
        if not gaps:
            return []
        return [
            "这些概念的标识符在源码里仍然找不到：\n"
            + "\n".join(gaps)
            + "\n——请按上面点名的位置补上（看板类指标必须落在**看板 VO**上："
            "业务方是在看板的字段里看到它，不是在你新加的接口里看到它）"
        ]

    applied, summary = generate_edits(
        ws, expectations, locations, files,
        concepts=concepts, must_implement=missing,
        verify=_verify, refresh=_refresh,
        verify_fast=_verify_fast, verify_evidence=_verify_evidence,
        # **路径清单**：模型得知道仓里的布局，
        # 否则它会把实体写到 module/location/Location.java（实际在 entity/ 下）
        tree=collect_tree(ws),
    )
    dirty = changed_files(ws)
    commit_sha: str | None = None
    branch: str | None = None
    if dirty:
        commit_sha, branch = commit_changes(
            ws,
            f"beeos: 实现 {input_data.get('set_id') or dev_task_id} 的"
            f" {len(concepts)} 个 WMS 概念（{'、'.join(concepts) or '-'}）",
        )

    # **以基线为准的权威改动清单**：git diff（基线…工作区）。
    # 用它而不是「git status」——后者在提交后就空了。
    final_changed = commit_range_files(ws, base_ref) or dirty

    return {
        "dev_task_id": dev_task_id,
        "commit_sha": commit_sha or "",
        "branch": branch or "",
        "commit_count": 1 if commit_sha else 0,
        "changed_files": final_changed,
        "codegen_summary": summary,
        "workspace_path": str(ws),
        # **机械核对出来的实现状态**（不是模型说的）
        "concept_states": [
            {"concept": c, "state": st} for c, st in states.items()
        ],
        "expectation_concepts": expectation_concepts,
        # 往下带
        "concepts": concepts,
        "locations": locations,
        "expectations": expectations,
    }


def dev_build_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：编译 + 跑仓库自己的测试套件 + 构建两遍比摘要

    **答案全部来自外部工具**：maven 的退出码、surefire 的 XML 报告、
    两遍构建的产物摘要。这只盒子一句自述都没掺。
    """
    dev_task_id = str(input_data.get("dev_task_id") or "")
    workspace_path = str(input_data.get("workspace_path") or "")
    if not workspace_path:
        raise CodegenError("没有隔离工作区——构建无从下手")
    ws = Path(workspace_path)
    log_dir = workspace_root() / "logs" / (dev_task_id or "dev")

    first = run_build(ws, log_dir=log_dir)
    stats = unit_test_stats(ws)
    if first.get("build_failed"):
        # 编译没过就别谈可复现了——第二遍必然也编译不过，
        # 跑它只是白等一遍。
        repro = {"build_reproducible": False, "reproducibility_digest": "",
                 "second_build_failed": False}
    else:
        repro = reproducibility(ws, str(first.get("classes_digest") or ""))

    digest = str(first.get("classes_digest") or "")
    # **前端构建**：这次方案涉及前端才跑（后端需求不浪费时间在 npm 上）。
    # **答案来自 npm 的退出码**，跟 maven 一样是外部神谕。
    locations = list(input_data.get("locations") or [])
    frontend_expected = any(str(loc).startswith("web/") for loc in locations)
    frontend: dict[str, Any] = {}
    if frontend_expected:
        frontend = run_frontend_build(ws)
    return {
        "dev_task_id": dev_task_id,
        "commit_sha": str(input_data.get("commit_sha") or ""),
        "branch": str(input_data.get("branch") or ""),
        "workspace_path": workspace_path,
        "commit_count": int(input_data.get("commit_count") or 0),
        "changed_files": input_data.get("changed_files") or [],
        "build_failed": bool(first.get("build_failed")),
        "build_duration_s": float(first.get("build_duration_s") or 0.0),
        "unit_test_pass_rate": float(stats.get("unit_test_pass_rate") or 0.0),
        "unit_test_green": bool(stats.get("unit_test_green")),
        "test_count": int(stats.get("test_count") or 0),
        "failure_count": int(stats.get("failure_count") or 0),
        "build_reproducibility": bool(repro.get("build_reproducible")),
        "reproducibility_digest": str(repro.get("reproducibility_digest") or ""),
        "artifact_digest": f"sha256:{digest}" if digest else "",
        "test_run_ref": str(ws / "server" / "target" / "surefire-reports"),
        "build_log_ref": str(first.get("build_log_ref") or ""),
        "jar_path": str(first.get("jar_path") or ""),
        # ---- 前端 ----
        # **frontend_expected=false 时不判红**：这次压根没碰前端。
        # 但也不能说「前端构建通过了」——那是从没跑过。
        "frontend_expected": frontend_expected,
        "frontend_build_failed": bool(frontend.get("ok") is False),
        "frontend_build_ok": bool(frontend.get("ok")) if frontend_expected else False,
        "frontend_dist_dir": str(frontend.get("dist_dir") or ""),
        "frontend_build_errors": str(frontend.get("errors") or ""),
        "codegen_summary": str(input_data.get("codegen_summary") or ""),
        "concept_states": input_data.get("concept_states") or [],
        "expectation_concepts": input_data.get("expectation_concepts") or [],
        # 往下带
        "concepts": input_data.get("concepts") or [],
        "locations": locations,
        "expectations": input_data.get("expectations") or [],
    }


def build_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 4：打包成制品 + 给出两条「链外」的判定

    **review_coverage**：改动 ⊆ 方案覆盖的源码位置，且至少动了一个业务代码文件。
    **artifact_traceable**：这份制品能追回到一个具体的提交 + 一个可取的分支。

    两条都答「否」的时候，**必须写清为什么**——看板上只看到「红了」，
    读者是没法查的。
    """
    workspace_path = str(input_data.get("workspace_path") or "")
    try:
        changed = list(input_data.get("changed_files") or [])
        locations = list(input_data.get("locations") or [])
        commit_sha = str(input_data.get("commit_sha") or "")
        branch = str(input_data.get("branch") or "")
        digest = str(input_data.get("artifact_digest") or "")

        review_ok, review_reason, _detail = review.coverage(
            changed, locations,
            states={
                row.get("concept"): row.get("state")
                for row in (input_data.get("concept_states") or [])
                if isinstance(row, dict)
            },
            expectation_concepts=input_data.get("expectation_concepts") or [],
        )
        try:
            trace_ok, trace_reason, _tdetail = review.traceable(
                commit_sha or None, branch or None, digest or None, repo_path(),
            )
        except CodegenError as exc:
            trace_ok, trace_reason = False, str(exc)

        expectations = input_data.get("expectations") or []

        # **jar 留下来**：工作区一会儿就拆了，编出来的那份 jar
        # 是这次交付的实体——「部署到验证实例」指的就是它。
        artifact_path = ""
        jar_path = str(input_data.get("jar_path") or "")
        if jar_path:
            src = Path(jar_path)
            if src.exists():
                import json
                import shutil
                dest_dir = workspace_root() / "artifacts" / (commit_sha[:8] or "nocommit")
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / src.name
                shutil.copy2(src, dest)
                artifact_path = str(dest)
                # **meta.json：制品库要能自己说明自己**
                # 运维盒的回滚判定要按摘要/提交找到这一份构建——
                # 只有一个没有说明的 jar 目录，它找不到。
                (dest_dir / "meta.json").write_text(json.dumps({
                    "commit_sha": commit_sha,
                    "branch": branch,
                    "artifact_digest": digest,
                    "jar": dest.name,
                    "build_log_ref": str(input_data.get("build_log_ref") or ""),
                }, ensure_ascii=False, indent=2), encoding="utf-8")

        # **前端产物也留下来**（跟 jar 同一个道理）：
        # 工作区一会儿就拆了，dist 里那一份是**这次交付的前端**。
        # 测试盒的前端探针要验的就是它——验的是「开发盒交付的那份」，
        # 不是别处随便一份（同一族：测的必须是交付物本身）。
        frontend_dist_path = ""
        if bool(input_data.get("frontend_expected")):
            dist_src = Path(str(input_data.get("frontend_dist_dir") or ""))
            if not dist_src.exists():
                dist_src = Path(workspace_path) / "web" / "dist"
            if dist_src.exists():
                import shutil
                dest_dir = workspace_root() / "artifacts" / (commit_sha[:8] or "nocommit")
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / "web-dist"
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(dist_src, dest)
                frontend_dist_path = str(dest)

        return {
            "commit_count": int(input_data.get("commit_count") or 0),
            "build_duration_s": float(input_data.get("build_duration_s") or 0.0),
            "unit_test_pass_rate": float(input_data.get("unit_test_pass_rate") or 0.0),
            "artifact_digest": digest,
            "build_failed": bool(input_data.get("build_failed")),
            "build_reproducibility": bool(input_data.get("build_reproducibility")),
            "unit_test_green": bool(input_data.get("unit_test_green")),
            "review_coverage": review_ok,
            "artifact_traceable": trace_ok,
            # ---- 证据 ----
            "commit_sha": commit_sha,
            "branch": branch,
            "changed_files": changed,
            # **这次改动瞄准了哪些预期**——不是「实现了哪些预期」：
            # 「实现了」得由测试盒跑真实场景来回答，这儿只能声明目标。
            "targeted_expectations": [
                {
                    "expectation_id": e.get("expectation_id", ""),
                    "requirement_id": e.get("requirement_id", ""),
                    "wms_kind": e.get("wms_kind", ""),
                    "statement": e.get("statement", ""),
                }
                for e in expectations
                if isinstance(e, dict)
            ],
            "review_reason": review_reason,
            "trace_reason": trace_reason,
            "codegen_summary": str(input_data.get("codegen_summary") or ""),
            "artifact_path": artifact_path,
            # ---- 前端 ----
            "frontend_expected": bool(input_data.get("frontend_expected")),
            "frontend_build_ok": bool(input_data.get("frontend_build_ok")),
            "frontend_build_failed": bool(input_data.get("frontend_build_failed")),
            "frontend_dist_path": frontend_dist_path,
        }
    finally:
        # 工作区拆掉——**不留残渣在别人的仓里**。
        # 提交已经落在主仓分支上，证据不会因为拆工作区而丢。
        if workspace_path:
            from boxes.wms_development.codegen import cleanup_workspace
            cleanup_workspace(Path(workspace_path))


WMS_DEVELOPMENT_HANDLERS: dict[str, Any] = {
    "dev_intake": dev_intake_handler,
    "dev_code": dev_code_handler,
    "dev_build": dev_build_handler,
    "build_packager": build_packager_handler,
}

RUNNER = HandlerRunner(WMS_DEVELOPMENT_HANDLERS, owner="wms_development_box")
