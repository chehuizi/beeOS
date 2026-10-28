"""tests.test_wms_development_box - WMS 开发盒的守卫

这只盒子干的事是**按方案生成代码**，所以它最容易出的错不是「写不出来」，
是「写出来了，但没人验它写的是不是方案要的东西」。

这份测试盯住四件事：

1. **判据的答案来自外部神谕**，不是这只盒子自述
   （模型说「已实现」不许把 review_coverage 点亮）
2. **改动的机械校验**：old 必须逐字唯一命中；落不下去就回喂模型，
   不许自己脑补
3. **工作区隔离**：不许动主仓
4. **接线不漂**：开发盒的制品形状必须还是测试盒声明的那份
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from boxes.wms_development import get_beeline, get_definition
from boxes.wms_development import codegen, review
from boxes.wms_development.build import classes_digest, unit_test_stats
from boxes.wms_development.code_map import (
    locations_for,
    unmapped_concepts,
)
from boxes.wms_development.runner import (
    build_packager_handler,
    dev_code_handler,
    dev_intake_handler,
)


# ============================================================
# 接线：开发盒的产出必须还是测试盒要的那份形状
# ============================================================


class TestHandoffShape:
    def test_result_satisfies_the_testing_boxes_artifact_contract(self):
        """开发盒的产出必须满足**测试盒自己声明**的制品形状

        测试盒在 `schema_build_artifact_input` 里声明了它要收的 9 个字段。
        开发盒改一个字段名，两边就接不上了——而这正是
        「schema 归消费者所有」的代价：抄两份会漂，但漂了会**响**。

        这条守卫直接比两份声明的字段名集合。
        """
        from boxes.wms_testing.schemas import BUILD_ARTIFACT_INPUT_SCHEMA

        dev_fields = {
            f.name for f in get_definition().get_schema("schema_build_artifact").fields
        }
        needed = {f.name for f in BUILD_ARTIFACT_INPUT_SCHEMA.fields if f.required}
        missing = needed - dev_fields
        assert not missing, (
            f"开发盒的产出缺了测试盒要的字段：{sorted(missing)}——"
            "交接会当场失败（这比接过去了但读不到好，但仍然是漂）"
        )

    def test_beeline_is_the_four_jump_real_leg(self):
        """beeline 必须是 intake → code → build → packager 四跳

        压成一跳的后果是「红在哪一步」说不清——
        模型改砸了、编译不过、单测没过、评审不通过，四种红长得一样。
        """
        ops = [(op.op_id, op.type) for op in get_beeline().operations]
        assert ops == [
            ("dev_intake", "dev_intake"),
            ("dev_code", "dev_code"),
            ("dev_build", "dev_build"),
            ("build_packager", "build_packager"),
        ], f"开发盒的作业路线变了：{ops}"

    def test_the_box_no_longer_claims_to_be_a_skeleton(self):
        """腿在了，徽章就得摘掉

        「未实现」和「实现了但不达标」必须分得开。
        """
        assert get_definition().result.maturity == "live"


# ============================================================
# 判据的答案来自外部神谕，不来自自述
# ============================================================


class TestReviewCoverageIsMechanical:
    def test_a_file_outside_the_plans_scope_is_rejected(self):
        ok, reason, detail = review.coverage(
            ["server/src/main/java/com/wms/module/customer/CustomerServiceImpl.java"],
            locations_for(["fefo"]),
        )
        assert ok is False
        assert "CustomerServiceImpl" in reason
        assert detail["out_of_scope_files"]

    def test_changing_only_tests_does_not_count_as_landing_the_plan(self):
        """只改测试 = 把判据改成绿的，不叫落地

        测试文件永远「在范围内」（开发盒要能补回归测试），
        但**只**改测试不算落地——所以这一条要单独判。
        """
        ok, reason, _detail = review.coverage(
            ["server/src/test/java/com/wms/module/outbound/PickServiceImplTest.java"],
            locations_for(["fefo"]),
        )
        assert ok is False
        assert "测试" in reason

    def test_no_change_at_all_is_not_landing_the_plan(self):
        """一条都没改 = 拿不出落地的证据

        模型说「这条需求代码里已经有了」是**自述**。
        自述在「只改测试」那条上已经被禁过一次，这里再放一次
        就等于给模型开了一道免检门。
        """
        ok, reason, _detail = review.coverage([], locations_for(["fefo"]))
        assert ok is False
        assert "没有任何改动" in reason

    def test_a_business_file_inside_the_scope_passes(self):
        ok, _reason, detail = review.coverage(
            ["server/src/main/resources/mapper/InventoryMapper.xml"],
            locations_for(["fefo"]),
        )
        assert ok is True
        assert detail["business_code_files"]

    def test_the_models_own_word_cannot_light_up_the_verdict(self):
        """模型说「已实现」不许把 review_coverage 点亮

        这是「自己判自己 = 永远绿」那族病在开发盒上的形态：
        如果 verdict 能被 codegen 的自述影响，那这条判据就等于没写。
        """
        result = build_packager_handler({
            "dev_task_id": "t1",
            "commit_count": 0,
            "workspace_path": "",
            "changed_files": [],
            "locations": locations_for(["fefo"]),
            "commit_sha": "",
            "branch": "",
            "artifact_digest": "sha256:abc",
            "codegen_summary": "这条需求代码里已经满足了，不用改",
        })
        assert result["review_coverage"] is False


class TestTraceabilityIsGitBacked:
    @pytest.fixture()
    def repo(self, tmp_path, monkeypatch):
        """一个真的 git 仓（临时目录，跟任何真实仓无关）"""
        root = tmp_path / "fake-wms"
        root.mkdir()
        run = lambda *a: subprocess.run(  # noqa: E731
            ["git", *a], cwd=str(root), capture_output=True, text=True, check=True,
        )
        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        (root / "a.txt").write_text("hello\n", encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", "init")
        sha = run("rev-parse", "HEAD").stdout.strip()
        monkeypatch.setenv(codegen.WMS_REPO_ENV, str(root))
        return root, sha

    def test_a_commit_on_a_branch_is_traceable(self, repo):
        root, sha = repo
        ok, reason, _detail = review.traceable(
            sha, "beeos/dev-test", "sha256:deadbeef", root,
        )
        assert ok is False  # 分支还没建
        assert "分支" in reason

        subprocess.run(["git", "branch", "-f", "beeos/dev-test", sha],
                       cwd=str(root), check=True, capture_output=True)
        ok, reason, _detail = review.traceable(
            sha, "beeos/dev-test", "sha256:deadbeef", root,
        )
        assert ok is True, reason

    def test_a_missing_commit_is_not_traceable(self, repo):
        root, _sha = repo
        ok, reason, _detail = review.traceable(
            None, "beeos/dev-x", "sha256:deadbeef", root,
        )
        assert ok is False
        assert "提交" in reason

    def test_a_branch_pointing_elsewhere_is_not_traceable(self, repo):
        """分支指向别的提交 = 追回去追到的不是这份东西"""
        root, sha = repo
        subprocess.run(["git", "branch", "-f", "beeos/dev-x", sha],
                       cwd=str(root), check=True, capture_output=True)
        (root / "b.txt").write_text("second\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=str(root),
                       check=True, capture_output=True)
        other = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                               capture_output=True, text=True, check=True).stdout.strip()
        # 问的是第二个提交，但分支指向第一个 → 追回去追到的不是这份东西
        ok, reason, _detail = review.traceable(
            other, "beeos/dev-x", "sha256:deadbeef", root,
        )
        assert ok is False
        assert other[:8] in reason
        assert sha[:8] in reason


# ============================================================
# 代码落地的机械校验
# ============================================================


class TestEditsAreAppliedMechanically:
    def test_old_must_match_verbatim(self, ws):
        with pytest.raises(codegen.CodegenError, match="命中 0 次"):
            codegen.apply_edits(ws, [{
                "file": "server/src/main/java/A.java",
                "old": "int a = 3;", "new": "int a = 1;",
            }])

    def test_old_must_be_unique(self, ws):
        with pytest.raises(codegen.CodegenError, match="必须唯一"):
            codegen.apply_edits(ws, [{
                "file": "server/src/main/java/A.java",
                "old": "int dup = 3;", "new": "int dup = 9;",
            }])

    def test_edits_may_not_escape_the_workspace(self, ws):
        with pytest.raises(codegen.CodegenError, match="工作区外"):
            codegen.apply_edits(ws, [{
                "file": "../../../etc/passwd", "old": "x", "new": "y",
            }])

    def test_a_failed_edit_leaves_nothing_behind(self, ws):
        """半途失败不留半截改动

        否则重试会把上一轮的残留再叠一遍——
        「三条改动里第二条落不下去」，结果第一条已经写进去了。
        """
        with pytest.raises(codegen.CodegenError):
            codegen.apply_edits(ws, [
                {"file": "server/src/main/java/A.java",
                 "old": "int b = 2;", "new": "int b = 20;"},
                {"file": "server/src/main/java/A.java",
                 "old": "int c = 3;", "new": "int c = 30;"},
            ])
        text = (ws / "server" / "src" / "main" / "java" / "A.java").read_text(encoding="utf-8")
        assert "int b = 2;" in text, "失败的那一轮不该留下任何改动"

    def test_empty_old_creates_a_new_file(self, ws):
        applied = codegen.apply_edits(ws, [{
            "file": "server/src/main/java/New.java", "old": "", "new": "class New {}\n",
        }])
        assert applied == ["server/src/main/java/New.java"]
        assert (ws / "server" / "src" / "main" / "java" / "New.java").exists()

    def test_empty_old_may_not_overwrite_an_existing_file(self, ws):
        with pytest.raises(codegen.CodegenError, match="已经存在"):
            codegen.apply_edits(ws, [{
                "file": "server/src/main/java/A.java", "old": "", "new": "x",
            }])

    def test_the_error_carries_the_material_the_model_needs_to_fix_it(self, ws):
        """报错必须带**文件内容**——不然模型只能瞎猜

        2026-10-27 实测：模型要往 ReportMapper.xml 里加一条 <select>，
        抄的 old 片段和原文差了一点缩进 → 命中 0 次 → 报错只说
        「old 逐字命中 0 次」。它看不见文件长什么样，
        三轮都在瞎抄，最后整个履约中止。

        跟「编译器的错误是最好的回喂材料」同一条：
        **报错要带着能修它的材料**，不是只说「错了」。
        """
        with pytest.raises(codegen.CodegenError) as err:
            codegen.apply_edits(ws, [{
                "file": "server/src/main/java/A.java",
                "old": "int nope = 0;", "new": "x",
            }])
        message = str(err.value)
        assert "server/src/main/java/A.java" in message
        assert "int b = 2;" in message, (
            "报错里必须带上文件里的真实内容，模型才有材料照着抄"
        )

    def test_indentation_slips_are_tolerated_when_the_match_is_unique(self, ws):
        """抄歪一格缩进 ≠ 改错地方

        模型是照着文件抄的，缩进抄歪很常见。
        归一化后**仍然要求唯一命中**——多处命中照样拒绝（不许瞎改）。
        """
        applied = codegen.apply_edits(ws, [{
            "file": "server/src/main/java/A.java",
            # 原文是顶格的，这里抄成了带缩进的
            "old": "    int b = 2;", "new": "    int b = 20;",
        }])
        assert applied == ["server/src/main/java/A.java"]
        text = (ws / "server" / "src" / "main" / "java" / "A.java").read_text(encoding="utf-8")
        assert "int b = 20;" in text
        assert "int b = 2;" not in text

    def test_an_ambiguous_indentation_slip_is_still_rejected(self, ws):
        """归一化命中多处 → 照旧拒绝（不许瞎改）"""
        (ws / "server" / "src" / "main" / "java" / "B.java").write_text(
            "class B {\n    int a = 3;\n    int a = 3;\n}\n", encoding="utf-8",
        )
        with pytest.raises(codegen.CodegenError, match="必须唯一"):
            codegen.apply_edits(ws, [{
                "file": "server/src/main/java/B.java",
                "old": "        int a = 3;", "new": "        int a = 1;",
            }])


class TestModelErrorsAreFedBack:
    def test_the_error_is_handed_back_to_the_model_and_it_can_fix_itself(self, ws):
        """落不下去就把错误原样回给它改——它改对了就成

        机械层不许自己脑补一段「差不多」的代码；
        但**把错误说清楚再问一次**是允许的，也是必须的。
        """
        prompts: list[str] = []

        def llm(prompt: str):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {"summary": "改一下", "edits": [{
                    "file": "server/src/main/java/A.java",
                    "old": "int zzz = 0;", "new": "int a = 5;",
                }]}
            return {"summary": "改好了", "edits": [{
                "file": "server/src/main/java/A.java",
                "old": "int a = 1;", "new": "int a = 5;",
            }]}

        applied, summary = codegen.generate_edits(
            ws, [], [], {}, llm_call=llm,
        )
        assert applied == ["server/src/main/java/A.java"]
        assert summary == "改好了"
        assert len(prompts) == 2
        assert "命中 0 次" in prompts[1], "第二遍必须把失败原因说给模型听"

    def test_a_model_that_never_gets_it_right_stops_the_run(self, ws):
        """连续几轮都落不下去 → 履约中止（不降级成「改了点别的」）"""
        calls = {"n": 0}

        def llm(_prompt: str):
            calls["n"] += 1
            return {"summary": "x", "edits": [{
                "file": "server/src/main/java/A.java",
                "old": "int nope = 0;", "new": "x",
            }]}

        with pytest.raises(codegen.CodegenError, match="落不下去"):
            codegen.generate_edits(ws, [], [], {}, llm_call=llm, max_attempts=2)
        assert calls["n"] == 2


# ============================================================
# 概念 → 源码位置：并集，不猜
# ============================================================


class TestCodeMap:
    def test_fefo_covers_both_sides_of_the_chain(self):
        """fefo 的落点是**并集**：分配在 outbound，效期得在 inbound 传得下去

        只映射一处，就会把「在传递链那一侧修 bug」判成红——
        而那恰恰是修对了。
        """
        locs = locations_for(["fefo"])
        assert any("outbound" in l for l in locs), locs
        assert any("inbound" in l for l in locs), locs

    def test_an_unimplemented_concept_gets_no_location(self):
        """WMS 仓里没有货主 → owner 没有落点（不猜）"""
        assert locations_for(["owner"]) == []
        assert unmapped_concepts(["owner", "fefo"]) == ["owner"]

    def test_an_unknown_concept_is_never_guessed(self):
        assert locations_for(["totally_made_up"]) == []
        assert unmapped_concepts(["totally_made_up"]) == ["totally_made_up"]


class TestChangedFilesAreRealPaths:
    """改动清单里的路径必须是**真的路径**

    2026-10-27 实测撞出来的：porcelain 每行是 `XY path`，
    解析时先 `strip()` 再取 `line[3:]` —— strip 把前导空格也去了，
    于是路径少了第一个字符（"erver/src/..."）。

    后果极其隐蔽：路径里仍然含 "mapper/ReportMapper" 这类位置片段，
    「改动在不在范围内」的判断照样过，**没有任何东西报错**；
    但拿路径去打开文件的检查会拿到不存在的路径，然后静默跳过——
    本轮那条「新增 Mapper 方法必须有对应 SQL」的校验就是这么被废掉的。
    """

    def test_changed_files_returns_paths_that_exist(self, tmp_path):
        import subprocess

        from boxes.wms_development.codegen import changed_files

        repo = tmp_path / "repo"
        (repo / "server/src/main/java/com/wms/mapper").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        target = repo / "server/src/main/java/com/wms/mapper/ReportMapper.java"
        target.write_text("class ReportMapper {}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        target.write_text("class ReportMapper { int x; }\n", encoding="utf-8")

        files = changed_files(repo)
        assert files, "改动了文件却没列出来"
        for rel in files:
            assert rel.startswith("server/"), (
                f"路径被切错了（少了开头）：{rel!r}"
            )
            assert (repo / rel).exists(), (
                f"列出来的路径在仓里不存在：{rel!r}"
            )


class TestMultiRoundDelivery:
    """一次要写多个文件时，模型分轮写（2026-10-28 实测撞出来的）

    实测：开发盒要新建一个模块（十几个文件），模型一次吐十个文件 →
    **输出超上限** → JSON 截断 → 解析失败 → 整个履约中止。
    所以流程改成：每轮写一两个文件，机械层落盘后再问下一个。
    """

    def test_the_model_is_asked_for_the_next_file_after_each_round(self, ws):
        """写完一个文件后，机械层会带着「已完成的文件」再问一次"""
        prompts: list[str] = []

        def llm(prompt: str):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {"summary": "写实体", "edits": [{
                    "file": "server/src/main/java/Zone.java", "old": "",
                    "new": "class Zone {}\n",
                }]}
            if len(prompts) == 2:
                return {"summary": "写控制器", "edits": [{
                    "file": "server/src/main/java/ZoneController.java", "old": "",
                    "new": "class ZoneController {}\n",
                }]}
            return {"summary": "写完了", "edits": []}

        applied, _summary = codegen.generate_edits(
            ws, [], [], {}, llm_call=llm,
            concepts=["zone"],
            must_implement=[("zone", "entity/Zone 里的 class Zone")],
            # 证据在第二轮就满足了（Zone.java 有了）
            verify_evidence=lambda: [],
            # 说「写完了」时的完整核对：两个文件都在就过
            verify=lambda: [] if (
                (ws / "server/src/main/java/Zone.java").exists()
                and (ws / "server/src/main/java/ZoneController.java").exists()
            ) else ["还没写完"],
        )
        assert applied == [
            "server/src/main/java/Zone.java",
            "server/src/main/java/ZoneController.java",
        ]
        # 第二轮是在**第一轮的基础上**继续（带着「已经写完的文件」）
        assert len(prompts) == 2
        assert "Zone.java" in prompts[1]
        assert "已经写完的文件" in prompts[1]

    def test_a_model_that_says_it_is_done_gets_the_full_check(self, ws):
        """模型说「写完了」→ 机械层才跑完整核对

        不通过就把问题原样回给它，它接着改。
        """
        prompts: list[str] = []

        def llm(prompt: str):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {"summary": "写完了", "edits": []}
            return {"summary": "补上了", "edits": [{
                "file": "server/src/main/java/Zone.java", "old": "",
                "new": "class Zone {}\n",
            }]}

        def verify():
            return [] if (ws / "server/src/main/java/Zone.java").exists() else ["还缺 Zone"]

        applied, _summary = codegen.generate_edits(
            ws, [], [], {}, llm_call=llm,
            concepts=["zone"],
            must_implement=[("zone", "entity/Zone 里的 class Zone")],
            verify=verify,
        )
        assert applied == ["server/src/main/java/Zone.java"]
        assert len(prompts) == 2
        assert "还没落地" in prompts[1] or "机械核对" in prompts[1]


class TestMapperBindingsAreChecked:
    """新增的 Mapper 方法必须有对应的 SQL（MyBatis 是运行期绑定）

    2026-10-27 实测撞出来的：开发盒给 `ReportMapper` 加了
    `selectPickAccuracy()`，XML 里没有对应语句。编译过、单测过、
    判据全绿——而仓里**一个报表测试都没有**，没人能发现它。
    上线后一调看板接口就 500。
    """

    def _repo(self, tmp_path):
        """造一个最小的仓：接口 + XML"""
        java = tmp_path / "server/src/main/java/com/wms/mapper"
        res = tmp_path / "server/src/main/resources/mapper"
        java.mkdir(parents=True)
        res.mkdir(parents=True)
        return java, res

    def test_a_new_mapper_method_without_sql_is_reported(self, tmp_path):
        from boxes.wms_development.mapper_binding import check_mapper_bindings

        java, res = self._repo(tmp_path)
        (java / "ReportMapper.java").write_text(
            "package com.wms.mapper;\n"
            "public interface ReportMapper {\n"
            "    Long countSku();\n"
            "    java.math.BigDecimal selectPickAccuracy();\n"
            "}\n",
            encoding="utf-8",
        )
        (res / "ReportMapper.xml").write_text(
            '<mapper namespace="com.wms.mapper.ReportMapper">\n'
            '  <select id="countSku" resultType="Long">SELECT count(*) FROM wms_sku</select>\n'
            '</mapper>\n',
            encoding="utf-8",
        )
        problems = check_mapper_bindings(
            tmp_path,
            ["server/src/main/java/com/wms/mapper/ReportMapper.java"],
        )
        assert problems, "缺 SQL 的新增方法必须被报出来"
        assert "selectPickAccuracy" in problems[0]

    def test_a_new_mapper_method_with_sql_is_not_reported(self, tmp_path):
        from boxes.wms_development.mapper_binding import check_mapper_bindings

        java, res = self._repo(tmp_path)
        (java / "ReportMapper.java").write_text(
            "package com.wms.mapper;\n"
            "public interface ReportMapper {\n"
            "    java.math.BigDecimal selectPickAccuracy();\n"
            "}\n",
            encoding="utf-8",
        )
        (res / "ReportMapper.xml").write_text(
            '<mapper namespace="com.wms.mapper.ReportMapper">\n'
            '  <select id="selectPickAccuracy" resultType="java.math.BigDecimal">'
            "SELECT 1</select>\n"
            '</mapper>\n',
            encoding="utf-8",
        )
        assert check_mapper_bindings(
            tmp_path,
            ["server/src/main/java/com/wms/mapper/ReportMapper.java"],
        ) == []

    def test_an_annotated_method_needs_no_xml(self, tmp_path):
        """@Select 注解式 SQL 不需要 XML 里有同名语句"""
        from boxes.wms_development.mapper_binding import check_mapper_bindings

        java, _res = self._repo(tmp_path)
        (java / "ReportMapper.java").write_text(
            "package com.wms.mapper;\n"
            "import org.apache.ibatis.annotations.Select;\n"
            "public interface ReportMapper {\n"
            "    @Select(\"SELECT 1\")\n"
            "    Long countSku();\n"
            "}\n",
            encoding="utf-8",
        )
        assert check_mapper_bindings(
            tmp_path,
            ["server/src/main/java/com/wms/mapper/ReportMapper.java"],
        ) == []


class TestZoneEvidenceCoversTheOwnershipLink:
    """库区概念的证据必须覆盖「库位归属在库区上」

    2026-10-28 实测：开发盒把库区做齐了（实体/Mapper/Service/Controller），
    可需求里还有一句「库位归属在库区上」——
    那句话**没有被任何证据覆盖**，于是它既没实现，也**没人发现它没实现**。
    判据全绿，交付物里少了一半业务价值。
    """

    def test_证据要求库位这一侧引用库区(self):
        """落点要精确到**服务**——写成「module/location 里有 Zone」的话，
        模型把 Zone 实体复制一份到 location 包里也就算过了
        （实测真这么干：两份重名实体，编译过、单测过，坑留在仓里）。
        """
        from boxes.wms_development.code_map import implementation_evidence

        pairs = implementation_evidence("zone")
        assert any(
            "LocationServiceImpl" in h and t == "Zone" for h, t, *_ in pairs
        ), "库位这一侧不引用库区 = 归属关系是断的"

    def test_库位侧的证据要求真用上_不能只import(self):
        """**一个没用到的 import 骗过了证据**（实测）

        模型为了满足证据，`import` 了 ZoneMapper、注入成字段，
        **一行都没用**——证据是绿的，
        可「库位挂到不存在的库区要被拒」压根没实现。
        所以这条证据是 `usage` 模式：只 import / 只注入字段不算数。
        """
        from boxes.wms_development.code_map import implementation_evidence

        pairs = implementation_evidence("zone")
        modes = [
            (h, t, rest[0] if rest else "any") for h, t, *rest in pairs
        ]
        assert any(
            "LocationServiceImpl" in h and mode == "usage" for h, _t, mode in modes
        ), "库位侧的证据必须是 usage 模式——只 import 不算数"

    def test_证据要求库区这一侧引用库位(self):
        from boxes.wms_development.code_map import implementation_evidence

        pairs = implementation_evidence("zone")
        assert any(
            "module/zone" in h and "Location" in t for h, t, *_ in pairs
        ), "库区下还有库位时不允许删除——库区这一侧必须看得到库位"

    def test_只有库区自己那三样_判不了归属(self):
        """证据少一条 → 归属这件事就没被任何判据看着"""
        from boxes.wms_development.code_map import implementation_evidence

        assert len(implementation_evidence("zone")) >= 5


class TestTheUsageModeEvidence:
    """`usage` 模式的证据：只 import / 只注入字段**不算数**

    实测：模型为了满足「库位这一侧要引用库区」的证据，
    import 了 ZoneMapper、注入成字段，**一行都没用**——
    证据是绿的，可「库位挂到不存在的库区要被拒」压根没实现。
    这种假实现比没实现更坏：判据说落地了，业务其实没落地。
    """

    def _svc(self, body: str) -> str:
        return (
            "package com.wms.module.location.service;\n"
            "import com.wms.entity.Zone;\n"
            "import com.wms.mapper.ZoneMapper;\n"
            "public class LocationServiceImpl {\n"
            "    private final ZoneMapper zoneMapper;\n"
            + body +
            "}\n"
        )

    def test_an_unused_import_does_not_count(self):
        from boxes.wms_development.code_map import _token_found

        text = self._svc("")
        assert _token_found(text, "Zone", "usage") is False, (
            "只 import、只注入字段就判「用上了」——那是假实现"
        )

    def test_an_injected_field_does_not_count(self):
        from boxes.wms_development.code_map import _token_found

        text = self._svc(
            "    public LocationServiceImpl(ZoneMapper zoneMapper) {\n"
            "        this.zoneMapper = zoneMapper;\n"
            "    }\n"
        )
        assert _token_found(text, "Zone", "usage") is False

    def test_a_real_call_counts(self):
        from boxes.wms_development.code_map import _token_found

        text = self._svc(
            "    private void requireZone(String zoneCode) {\n"
            "        Zone zone = zoneMapper.selectByCode(zoneCode);\n"
            "        if (zone == null) { throw new IllegalArgumentException(\"库区不存在\"); }\n"
            "    }\n"
        )
        assert _token_found(text, "Zone", "usage") is True

    def test_any_mode_still_accepts_an_import(self):
        """默认模式不变——有的概念要的只是「文件里提到它」"""
        from boxes.wms_development.code_map import _token_found

        text = self._svc("")
        assert _token_found(text, "Zone", "any") is True


class TestTheTestFailureReachesTheModel:
    """测试挂了要**回到模型手里**

    实测：服务多了一个 Mapper 依赖 → 仓里 @InjectMocks 的老测试 NPE。
    核对只跑编译的话，模型以为「我写完了」，
    连着几轮都在改业务代码——因为没人告诉它错在测试上。
    """

    def test_测试错误能被摘出来(self):
        from boxes.wms_development.build import extract_test_errors

        log = (
            "[INFO] Results:\n"
            "[ERROR] Errors: \n"
            "[ERROR]   LocationServiceImplTest.shouldCreate:91 » NullPointer "
            'because "this.zoneMapper" is null\n'
            "[INFO] \n"
            "[INFO] Tests run: 62, Failures: 0, Errors: 1, Skipped: 0\n"
        )
        errors = extract_test_errors(log)
        assert "LocationServiceImplTest" in errors, (
            "测试失败没摘出来——模型看不到自己挂在哪儿"
        )
        assert "zoneMapper" in errors

    def test_编译错误和测试错误是分开的两段(self):
        from boxes.wms_development.build import (
            extract_compile_errors,
            extract_test_errors,
        )

        log = (
            "[ERROR] COMPILATION ERROR : \n"
            "[ERROR] /wms/Foo.java:[3,5] cannot find symbol\n"
            "[INFO] 1 error\n"
            "[INFO] BUILD FAILURE\n"
        )
        assert "cannot find symbol" in extract_compile_errors(log)
        assert extract_test_errors(log) == ""


class TestDuplicateClassesAreRejected:
    """同一个类只能有一份

    2026-10-28 实测：模型为了表达「库位归属在库区上」，
    在 `module/location/` 下又建了一份 Zone 实体，
    在 `module/zone/` 下又建了一份 Location 实体——
    跟 `com/wms/entity/` 里原有的那份重名。
    编译过、单测过（没人碰这两个类），可仓里从此有两份「库区实体」：
    改一处、另一处不变，没人说得清数据从哪来。
    """

    def _repo(self, tmp_path):
        java = tmp_path / "server" / "src" / "main" / "java" / "com" / "wms"
        (java / "entity").mkdir(parents=True)
        (java / "module" / "location").mkdir(parents=True)
        (java / "module" / "zone").mkdir(parents=True)
        return java

    def test_a_copied_entity_is_reported(self, tmp_path):
        from boxes.wms_development.mapper_binding import check_duplicate_classes

        java = self._repo(tmp_path)
        (java / "entity" / "Location.java").write_text(
            "package com.wms.entity;\npublic class Location {}\n", encoding="utf-8",
        )
        (java / "module" / "zone" / "Location.java").write_text(
            "package com.wms.module.zone;\npublic class Location {}\n",
            encoding="utf-8",
        )
        problems = check_duplicate_classes(tmp_path, [
            "server/src/main/java/com/wms/module/zone/Location.java",
        ])
        assert problems, "复制一份实体必须被报出来"
        assert "Location" in problems[0]

    def test_a_new_class_is_not_a_duplicate(self, tmp_path):
        from boxes.wms_development.mapper_binding import check_duplicate_classes

        java = self._repo(tmp_path)
        (java / "entity" / "Location.java").write_text(
            "package com.wms.entity;\npublic class Location {}\n", encoding="utf-8",
        )
        (java / "entity" / "Zone.java").write_text(
            "package com.wms.entity;\npublic class Zone {}\n", encoding="utf-8",
        )
        assert check_duplicate_classes(tmp_path, [
            "server/src/main/java/com/wms/entity/Zone.java",
        ]) == []


class TestTheModelSeesTheFilesItNeeds:
    """上下文要收得齐、收得公平

    2026-10-28 实测：开发盒连着 **12 轮**改动都落不下去，
    原因是它「按记忆」去改一个迁移文件——
    而那个文件**从来没出现在它的上下文里**：
    1. `.sql` 根本不收（只收 .java/.xml）→ 模型看不到仓里已有的 V1~V4，
       自己起名 `V1__create_wms_core_tables.sql`（版本号撞了）
    2. 也看不到**自己上一轮刚建**的那个文件 → old 逐字命中 0 次
    """

    def _ws(self, tmp_path):
        base = tmp_path / "server" / "src" / "main"
        (base / "java" / "com" / "wms" / "module" / "location").mkdir(parents=True)
        (base / "resources" / "mapper").mkdir(parents=True)
        (base / "resources" / "db" / "migration").mkdir(parents=True)
        return base

    def test_迁移脚本要进上下文(self, tmp_path):
        from boxes.wms_development.codegen import collect_context

        base = self._ws(tmp_path)
        (base / "resources" / "db" / "migration" / "V4__putaway.sql").write_text(
            "CREATE TABLE t (id BIGINT);\n", encoding="utf-8",
        )
        files = collect_context(tmp_path, ["db/migration"])
        assert any("V4__putaway.sql" in rel for rel in files), (
            "迁移脚本不在上下文里 → 模型只能凭记忆改它，old 永远命中 0 次"
        )

    def test_每个落点都先分到一份(self, tmp_path):
        """额度不许被前面的落点吃光

        java 目录先扫，location/warehouse 就把额度占满了，
        resources 里的 Mapper XML 和迁移脚本一个都进不来——
        而它们恰恰是新建模块时最要紧的参考。
        """
        from boxes.wms_development.codegen import collect_context

        base = self._ws(tmp_path)
        for i in range(30):
            (base / "java" / "com" / "wms" / "module" / "location"
             / f"Loc{i}.java").write_text("class A {}\n", encoding="utf-8")
        (base / "resources" / "mapper" / "ZoneMapper.xml").write_text(
            "<mapper/>\n", encoding="utf-8",
        )
        (base / "resources" / "db" / "migration" / "V5__zone.sql").write_text(
            "CREATE TABLE z (id BIGINT);\n", encoding="utf-8",
        )
        files = collect_context(tmp_path, [
            "module/location", "mapper/ZoneMapper", "db/migration",
        ])
        assert any("ZoneMapper.xml" in rel for rel in files), (
            "Mapper XML 被 java 目录挤掉了"
        )
        assert any("V5__zone.sql" in rel for rel in files), (
            "迁移脚本被 java 目录挤掉了"
        )

    def test_下一个迁移版本号(self, tmp_path):
        from boxes.wms_development.codegen import next_migration_version

        assert next_migration_version({
            "server/src/main/resources/db/migration/V1__init.sql": "",
            "server/src/main/resources/db/migration/V4__putaway.sql": "",
        }) == 5
        assert next_migration_version({}) == 1

    def test_prompt_里写明下一个迁移版本号(self, tmp_path):
        """版本号直接给它——它自己起名是「看着像就行」"""
        from boxes.wms_development.codegen import build_prompt

        prompt = build_prompt(
            expectations=[{"wms_kind": "zone", "statement": "s", "evidence": "e"}],
            locations=["db/migration"],
            files={
                "server/src/main/resources/db/migration/V4__putaway.sql": "x",
            },
        )
        assert "V5__" in prompt, "prompt 没告诉它该用哪个版本号"


class TestMigrationFilenamesAreChecked:
    """迁移脚本的文件名必须符合 Flyway 规范

    2026-10-28 实测：开发盒建表时写出 `V__create_wms_zone.sql`——
    **没有版本号**。编译过、单测绿、判据全绿，
    可 Flyway 启动时直接拒绝（Invalid version separator），
    **整个服务起不来**。
    """

    def _mig(self, tmp_path):
        mig = tmp_path / "server" / "src" / "main" / "resources" / "db" / "migration"
        mig.mkdir(parents=True)
        return mig

    def test_a_migration_without_a_version_is_reported(self, tmp_path):
        from boxes.wms_development.mapper_binding import check_migration_filenames

        mig = self._mig(tmp_path)
        (mig / "V1__init.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (mig / "V__create_wms_zone.sql").write_text(
            "CREATE TABLE wms_zone (id BIGSERIAL PRIMARY KEY);\n", encoding="utf-8",
        )
        problems = check_migration_filenames(tmp_path, [
            "server/src/main/resources/db/migration/V__create_wms_zone.sql",
        ])
        assert problems, "没有版本号的迁移文件必须被报出来"
        assert any("V__create_wms_zone.sql" in p for p in problems)
        assert any("V5__create_wms_zone.sql" in p for p in problems), (
            "报错要带能修它的材料：给出正确的文件名长什么样"
        )

    def test_a_version_below_the_existing_max_is_reported(self, tmp_path):
        """版本号撞了/倒退了，Flyway 启动时也会拒绝"""
        from boxes.wms_development.mapper_binding import check_migration_filenames

        mig = self._mig(tmp_path)
        (mig / "V1__init.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (mig / "V4__putaway.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (mig / "V3__zone.sql").write_text("SELECT 1;\n", encoding="utf-8")
        problems = check_migration_filenames(tmp_path, [
            "server/src/main/resources/db/migration/V3__zone.sql",
        ])
        assert problems, "版本号倒退必须被报出来"
        assert any("V3__zone.sql" in p for p in problems)

    def test_a_correct_migration_is_not_reported(self, tmp_path):
        from boxes.wms_development.mapper_binding import check_migration_filenames

        mig = self._mig(tmp_path)
        (mig / "V1__init.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (mig / "V4__putaway.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (mig / "V5__create_wms_zone.sql").write_text(
            "CREATE TABLE wms_zone (id BIGSERIAL PRIMARY KEY);\n", encoding="utf-8",
        )
        assert check_migration_filenames(tmp_path, [
            "server/src/main/resources/db/migration/V5__create_wms_zone.sql",
        ]) == []

    def test_duplicate_versions_are_reported(self, tmp_path):
        from boxes.wms_development.mapper_binding import check_migration_filenames

        mig = self._mig(tmp_path)
        (mig / "V1__init.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (mig / "V5__a.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (mig / "V5__b.sql").write_text("SELECT 1;\n", encoding="utf-8")
        problems = check_migration_filenames(tmp_path, [
            "server/src/main/resources/db/migration/V5__b.sql",
        ])
        assert any("重复" in p for p in problems), problems

    def test_repeatable_migration_is_allowed(self, tmp_path):
        """R__ 开头的是可重复脚本，本来就没有版本号"""
        from boxes.wms_development.mapper_binding import check_migration_filenames

        mig = self._mig(tmp_path)
        (mig / "V1__init.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (mig / "R__views.sql").write_text("SELECT 1;\n", encoding="utf-8")
        assert check_migration_filenames(tmp_path, [
            "server/src/main/resources/db/migration/R__views.sql",
        ]) == []


class TestMigrationCoverageIsChecked:
    """SQL 里用到的表，必须有迁移建出来

    2026-10-27 实测：开发盒新建模块时漏了建表脚本——
    编译过、单测过（没碰那张表），上线一调就
    `relation does not exist`，接口直接 500。
    """

    def _repo(self, tmp_path):
        res = tmp_path / "server" / "src" / "main" / "resources" / "mapper"
        mig = tmp_path / "server" / "src" / "main" / "resources" / "db" / "migration"
        res.mkdir(parents=True)
        mig.mkdir(parents=True)
        return res, mig

    def test_a_table_without_a_migration_is_reported(self, tmp_path):
        from boxes.wms_development.mapper_binding import check_migration_covers_tables

        res, mig = self._repo(tmp_path)
        (mig / "V1__init.sql").write_text(
            "CREATE TABLE wms_location (id BIGSERIAL PRIMARY KEY);\n", encoding="utf-8",
        )
        (res / "ZoneMapper.xml").write_text(
            '<mapper namespace="com.wms.mapper.ZoneMapper">\n'
            '  <select id="selectPage" resultType="X">\n'
            '    SELECT id FROM wms_zone\n'
            '  </select>\n'
            '</mapper>\n',
            encoding="utf-8",
        )
        problems = check_migration_covers_tables(
            tmp_path, ["server/src/main/resources/mapper/ZoneMapper.xml"],
        )
        assert problems, "用了没有迁移的表必须被报出来"
        assert "wms_zone" in problems[0]

    def test_a_table_with_a_migration_is_not_reported(self, tmp_path):
        from boxes.wms_development.mapper_binding import check_migration_covers_tables

        res, mig = self._repo(tmp_path)
        (mig / "V1__init.sql").write_text(
            "CREATE TABLE wms_zone (id BIGSERIAL PRIMARY KEY);\n", encoding="utf-8",
        )
        (res / "ZoneMapper.xml").write_text(
            '<mapper namespace="com.wms.mapper.ZoneMapper">\n'
            '  <select id="selectPage" resultType="X">\n'
            '    SELECT id FROM wms_zone\n'
            '  </select>\n'
            '</mapper>\n',
            encoding="utf-8",
        )
        assert check_migration_covers_tables(
            tmp_path, ["server/src/main/resources/mapper/ZoneMapper.xml"],
        ) == []

    def test_an_xml_attribute_is_not_mistaken_for_a_table(self, tmp_path):
        """`<update id="...">` 里的 `id` 是属性名，不是表名

        踩过：把「表 id」报成「缺建表迁移」，
        于是模型收到一条**根本不成立**的错误，
        三轮都在追一个不存在的问题。
        """
        from boxes.wms_development.mapper_binding import check_migration_covers_tables

        res, mig = self._repo(tmp_path)
        (mig / "V1__init.sql").write_text(
            "CREATE TABLE wms_location (id BIGSERIAL PRIMARY KEY);\n", encoding="utf-8",
        )
        (res / "LocationMapper.xml").write_text(
            '<mapper namespace="com.wms.mapper.LocationMapper">\n'
            '  <update id="softDeleteById">\n'
            '    UPDATE wms_location SET deleted = 1 WHERE id = #{id}\n'
            '  </update>\n'
            '</mapper>\n',
            encoding="utf-8",
        )
        assert check_migration_covers_tables(
            tmp_path, ["server/src/main/resources/mapper/LocationMapper.xml"],
        ) == []


class TestImplementedIsMechanical:
    """「已实现」这句话得由源码说了算，不由模型说

    上一轮真跑撞出来的：模型看着已有的看板聚合，说
    「拣货准确率已由 module/report…实现，无需修改」，交了白卷——
    而源码里连 pickAccuracy 都没有。
    """

    def test_a_concept_without_its_identifier_in_the_source_is_missing(self):
        from boxes.wms_development.code_map import implementation_state

        files = {"server/src/main/java/com/wms/vo/DashboardVO.java":
                 "class DashboardVO { private Long skuCount; }"}
        assert implementation_state("wms_pick_accuracy", files=files) == "missing"

    def test_a_concept_whose_identifier_is_in_the_source_is_implemented(self):
        from boxes.wms_development.code_map import implementation_state

        files = {"server/src/main/java/com/wms/vo/DashboardVO.java":
                 "class DashboardVO { private BigDecimal pickAccuracy; }"}
        assert implementation_state("wms_pick_accuracy", files=files) == "implemented"

    def test_a_same_named_method_elsewhere_is_not_the_dashboard_field(self):
        """同名方法 ≠ 看板上有这个数

        2026-10-27 实测：开发盒把拣货准确率做成了独立接口
        `/reports/pick-accuracy`，里面有个方法叫 `pickAccuracy()`。
        全仓搜「pickAccuracy」是搜得到的 → 旧的证据判定判成「已实现」
        就放行了。可业务方要的是**在库存看板看得到**——
        那个字段在看板 VO 上，它压根不在看板 VO 上。

        所以证据必须带**在哪个文件里**：字段在别处 = 业务方看不到。
        """
        from boxes.wms_development.code_map import implementation_state

        files = {
            "server/src/main/java/com/wms/module/report/service/ReportServiceImpl.java":
                "public PickAccuracyVO pickAccuracy() { return null; }",
            "server/src/main/java/com/wms/vo/PickAccuracyVO.java":
                "class PickAccuracyVO { private BigDecimal accuracyRate; }",
        }
        assert implementation_state("wms_pick_accuracy", files=files) == "missing"

    def test_a_concept_without_registered_evidence_cannot_be_called_implemented(self):
        """判不了 ≠ 已实现

        说不出证据就不能算落地——跟「认不出就判红」同一条纪律。
        """
        from boxes.wms_development.code_map import implementation_state

        assert implementation_state("owner", "class X {}") == "unknown"

    def test_fefo_is_judged_by_its_mechanism_not_by_its_name(self):
        """fefo 这个业务词在代码里根本不存在

        它是靠「按 expire_date 排序」实现的——所以证据是那段 SQL，
        不是「fefo」这三个字母。
        """
        from boxes.wms_development.code_map import implementation_state

        files = {"server/src/main/resources/mapper/InventoryMapper.xml":
                 "ORDER BY expire_date ASC NULLS LAST"}
        assert implementation_state("fefo", files=files) == "implemented"
        files_bad = {"server/src/main/resources/mapper/InventoryMapper.xml":
                     "ORDER BY created_at ASC"}
        assert implementation_state("fefo", files=files_bad) == "missing"

    def test_zero_changes_with_a_missing_concept_is_red(self):
        ok, reason, _detail = review.coverage(
            [], locations_for(["wms_pick_accuracy"]),
            states={"wms_pick_accuracy": "missing"},
            expectation_concepts=["wms_pick_accuracy"],
        )
        assert ok is False
        assert "wms_pick_accuracy" in reason

    def test_zero_changes_with_every_concept_implemented_is_green(self):
        """零改动也可以是交付——但得有证据

        方案要的每个概念源码里都已有实现时，「不用改」就是结论，
        而且这个结论是机械核对出来的，不是模型说的。
        """
        ok, reason, _detail = review.coverage(
            [], locations_for(["wms_pick_accuracy"]),
            states={"wms_pick_accuracy": "implemented"},
            expectation_concepts=["wms_pick_accuracy"],
        )
        assert ok is True, reason

    def test_zero_changes_with_an_unjudgeable_concept_is_red(self):
        ok, _reason, _detail = review.coverage(
            [], locations_for(["owner"]),
            states={"owner": "unknown"},
            expectation_concepts=["owner"],
        )
        assert ok is False


class TestEmptyEditsAreNotAFreeExit:
    def test_an_empty_answer_is_fed_back_when_a_concept_is_missing(self, ws):
        """模型交白卷不算「已实现」——把这件事回给它

        真跑撞过：模型说「看板已经实现了」，而源码里没有那个数。
        """
        prompts: list[str] = []

        def llm(prompt: str):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {"summary": "已经实现了", "edits": []}
            return {"summary": "补上了", "edits": [{
                "file": "server/src/main/java/A.java",
                "old": "int a = 1;", "new": "int a = 5;",
            }]}

        applied, _summary = codegen.generate_edits(
            ws, [], [], {},
            llm_call=llm,
            concepts=["wms_pick_accuracy"],
            must_implement=[("wms_pick_accuracy", "vo/DashboardVO 里的 pickAccuracy")],
        )
        assert applied == ["server/src/main/java/A.java"]
        assert len(prompts) == 2
        assert "wms_pick_accuracy" in prompts[1]
        assert "空的 edits" in prompts[1]

    def test_a_change_that_still_does_not_implement_the_concept_is_fed_back(self, ws):
        """改完了还得再核一遍：标识符没出现 = 没实现

        模型说它实现了不算数——源码里得真的有那个标识符。
        """
        prompts: list[str] = []

        def llm(prompt: str):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {"summary": "改了", "edits": [{
                    "file": "server/src/main/java/A.java",
                    "old": "int a = 1;", "new": "int accuracy = 5;",
                }]}
            # 第二轮是在**当前内容**上增量改（第一轮的改动留在工作区里）
            return {"summary": "按约定的名字改了", "edits": [{
                "file": "server/src/main/java/A.java",
                "old": "int accuracy = 5;", "new": "int pickAccuracy = 5;",
            }]}

        applied, _summary = codegen.generate_edits(
            ws, [], [], {},
            llm_call=llm,
            concepts=["wms_pick_accuracy"],
            must_implement=[("wms_pick_accuracy", "vo/DashboardVO 里的 pickAccuracy")],
            verify=lambda: [] if "pickAccuracy" in (
                ws / "server" / "src" / "main" / "java" / "A.java"
            ).read_text(encoding="utf-8") else ["wms_pick_accuracy"],
            # 重试时的上下文是**当前**工作区（含上一轮的改动）
            refresh=lambda: {
                "server/src/main/java/A.java": (
                    ws / "server" / "src" / "main" / "java" / "A.java"
                ).read_text(encoding="utf-8"),
            },
        )
        assert applied == ["server/src/main/java/A.java"]
        assert len(prompts) == 2
        assert "没通过机械核对" in prompts[1]
        assert "wms_pick_accuracy" in prompts[1]
        # 第二轮看到的源码是**当前**内容（含第一轮的改动）
        assert "int accuracy = 5;" in prompts[1]


# ============================================================
# 编排：每一跳都把该带的东西带下去
# ============================================================


class TestRunnerOrchestration:
    def test_intake_translates_the_design_package_into_a_plan(self):
        plan = dev_intake_handler({
            "dev_task_id": "t1",
            "design_package_ref": {
                "package_id": "p1",
                "set_id": "s1",
                "expectations": [
                    {"expectation_id": "e1", "wms_kind": "fefo", "statement": "s"},
                ],
                "model_elements": {"entities": [{"wms_kind": "lot"}]},
            },
            "commit_range": "abc123..HEAD",
        })
        assert plan["concepts"] == ["fefo", "lot"]
        assert "module/outbound" in plan["locations"]
        assert plan["base_ref"] == "abc123"

    def test_intake_reports_concepts_that_have_nowhere_to_land(self):
        plan = dev_intake_handler({
            "dev_task_id": "t1",
            "design_package_ref": {
                "package_id": "p1",
                "set_id": "s1",
                "expectations": [{"expectation_id": "e1", "wms_kind": "owner"}],
                "model_elements": {},
            },
        })
        assert plan["unmapped_concepts"] == ["owner"]
        assert plan["locations"] == []

    def test_code_stops_when_the_plan_has_nowhere_to_land(self):
        """没有落点 → 履约中止，不许改点别的充数"""
        with pytest.raises(codegen.CodegenError, match="没有落点"):
            dev_code_handler({
                "dev_task_id": "t1",
                "locations": [],
                "unmapped_concepts": ["owner"],
                "expectations": [],
            })

    def test_packager_reports_both_verdicts_with_reasons(self):
        result = build_packager_handler({
            "dev_task_id": "t1",
            "commit_count": 1,
            "workspace_path": "",
            "changed_files": ["server/src/main/resources/mapper/InventoryMapper.xml"],
            "locations": locations_for(["fefo"]),
            "commit_sha": "deadbeefdeadbeef",
            "branch": "beeos/dev-deadbeef",
            "artifact_digest": "sha256:abc",
            "unit_test_pass_rate": 1.0,
            "unit_test_green": True,
            "build_failed": False,
            "build_reproducibility": True,
            "build_duration_s": 12.0,
        })
        assert result["review_coverage"] is True
        assert result["review_reason"]
        # 追不回去（仓里没这个提交）→ 判红 + 说清为什么
        assert result["artifact_traceable"] is False
        assert result["trace_reason"]


# ============================================================
# 外部神谕：编译产物摘要与单测报告
# ============================================================


class TestBuildOracles:
    def test_the_digest_is_stable_for_identical_content(self, tmp_path):
        a = tmp_path / "a" / "server" / "target" / "classes"
        b = tmp_path / "b" / "server" / "target" / "classes"
        for root in (a, b):
            root.mkdir(parents=True)
            (root / "X.class").write_bytes(b"\x01\x02")
            (root / "sub").mkdir()
            (root / "sub" / "Y.class").write_bytes(b"\x03")
        assert classes_digest(tmp_path / "a") == classes_digest(tmp_path / "b")
        assert classes_digest(tmp_path / "a") != ""

    def test_the_digest_changes_when_the_content_changes(self, tmp_path):
        a = tmp_path / "a" / "server" / "target" / "classes"
        b = tmp_path / "b" / "server" / "target" / "classes"
        for root in (a, b):
            root.mkdir(parents=True)
        (a / "X.class").write_bytes(b"\x01")
        (b / "X.class").write_bytes(b"\x02")
        assert classes_digest(tmp_path / "a") != classes_digest(tmp_path / "b")

    def test_unit_test_stats_are_read_from_the_surefire_reports(self, tmp_path):
        """读 XML 报告，不解析日志文本

        日志格式会变，XML 是契约。
        """
        reports = tmp_path / "server" / "target" / "surefire-reports"
        reports.mkdir(parents=True)
        (reports / "TEST-A.xml").write_text(
            '<testsuite name="A" tests="4" errors="0" skipped="1" failures="1"/>',
            encoding="utf-8",
        )
        stats = unit_test_stats(tmp_path)
        assert stats["test_count"] == 4
        assert stats["failure_count"] == 1
        assert stats["skipped_count"] == 1
        assert stats["unit_test_pass_rate"] == 0.5
        assert stats["unit_test_green"] is False

    def test_no_tests_at_all_is_not_green(self, tmp_path):
        """一条测试都没跑 ≠ 全绿

        `unit_test_green` 要求 total > 0：没跑过的东西不能算通过。
        """
        stats = unit_test_stats(tmp_path)
        assert stats["unit_test_green"] is False
        assert stats["unit_test_pass_rate"] == 0.0

    def test_compile_errors_are_cut_out_of_the_maven_output(self):
        """只把错误段喂回模型

        整份 maven 日志几千行，喂回去只会把模型淹掉——
        它真正需要看的就是「哪个文件、哪一行、什么符号」。
        """
        from boxes.wms_development.build import extract_compile_errors

        log = (
            "[INFO] Scanning...\n"
            "[INFO] 1000 行无关日志\n"
            "[ERROR] COMPILATION ERROR :\n"
            "[ERROR] /wms/server/src/main/java/com/wms/mapper/ReportMapper.java:[30,5] 找不到符号\n"
            "[ERROR]   符号:   类 PickAccuracyRowVO\n"
            "[INFO] BUILD FAILURE\n"
        )
        errors = extract_compile_errors(log)
        assert "找不到符号" in errors
        assert "PickAccuracyRowVO" in errors
        assert "1000 行无关日志" not in errors


# ============================================================
# 工作区隔离
# ============================================================


@pytest.fixture()
def ws(tmp_path):
    """一个假的隔离工作区（有源码文件，内容里有真有假的重复片段）"""
    root = tmp_path / "ws"
    (root / "server" / "src" / "main" / "java").mkdir(parents=True)
    f = root / "server" / "src" / "main" / "java" / "A.java"
    f.write_text(
        "int a = 1;\nint b = 2;\nint dup = 3;\nint dup = 3;\n", encoding="utf-8",
    )
    return root


class TestWorkspaceIsolation:
    def test_the_repo_path_must_be_configured(self, monkeypatch):
        """没配仓路径 = 没地方改代码（中止，不是猜一个默认路径）"""
        monkeypatch.delenv(codegen.WMS_REPO_ENV, raising=False)
        with pytest.raises(codegen.CodegenError, match="不知道去哪儿改代码"):
            codegen.repo_path()

    def test_a_configured_path_must_really_be_a_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.setenv(codegen.WMS_REPO_ENV, str(tmp_path))
        with pytest.raises(codegen.CodegenError, match="不是 git 仓"):
            codegen.repo_path()
