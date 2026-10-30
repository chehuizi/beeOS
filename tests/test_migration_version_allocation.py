"""迁移的版本号由机械层分配（2026-10-30 实测撞出来的）

**这个盒子解决的是什么问题**：
模型建了 `V5__seed_zone.sql`，而 V5 已经被占用了。
它想改名 —— 但 **edits 机制只能新建和改内容，不能删文件**，
所以它**既改不了名也删不掉**，那个版本号冲突就永远消不掉：
实测从第 20 轮到第 30 轮，每一轮的机械核对都报同一条
「迁移版本号 V5 重复」，18 个文件都写完了，轮数却耗尽。

**版本号是纯机械的事**（下一个可用号 = 仓里最大版本号 + 1），
模型算不明白、也删不掉自己写错的名字 —— 那就机械层替它算，
**但必须告诉它新名字**，否则它下一轮还在用老路径。

所以这里守三件事：
1. 机械层真的会重新分配版本号（而不是只报个错）
2. 重新分配这件事**要回喂给模型**（它得知道新路径）
3. 机械层**不许借改名偷换语义**（重建已发布的迁移 / 同名迁移建两遍）
"""

from __future__ import annotations

import pytest

from boxes.wms_development import codegen
from boxes.wms_development.mapper_binding import check_migration_filenames

MIG = "server/src/main/resources/db/migration"


@pytest.fixture()
def ws(tmp_path):
    """一个假的隔离工作区：已有 V1 和 V5（V5 是已发布的建库区表迁移）"""
    root = tmp_path / "ws"
    mig = root / MIG
    mig.mkdir(parents=True)
    (mig / "V1__init_schema.sql").write_text(
        "CREATE TABLE wms_sku(id int);\n", encoding="utf-8",
    )
    (mig / "V5__create_zone_table.sql").write_text(
        "CREATE TABLE wms_zone(code varchar, warehouse_id int);\n", encoding="utf-8",
    )
    return root


def _new_edit(rel: str, content: str) -> dict:
    return {"file": rel, "old": "", "new": content}


class TestVersionIsAllocatedMechanically:
    def test_a_taken_version_is_moved_to_the_next_free_one(self, ws):
        """版本号被占 → 落成下一个可用号，老路径**不留下文件**"""
        created, renames, notes = set(), {}, []
        applied = codegen.apply_edits(ws, [
            _new_edit(f"{MIG}/V5__seed_zone.sql", "INSERT INTO wms_zone VALUES ('A');\n"),
        ], created, renames, notes)

        assert applied == [f"{MIG}/V6__seed_zone.sql"]
        assert (ws / MIG / "V6__seed_zone.sql").exists()
        assert not (ws / MIG / "V5__seed_zone.sql").exists(), \
            "老路径不能留下文件——Flyway 遇到重复版本号直接拒绝启动"

    def test_a_backwards_version_is_moved_too(self, ws):
        """版本号**倒退**（V3 < 仓里最大的 V5）也不行 → 一样重新分配"""
        created, renames, notes = set(), {}, []
        applied = codegen.apply_edits(ws, [
            _new_edit(f"{MIG}/V3__seed_zone.sql", "INSERT INTO wms_zone VALUES ('A');\n"),
        ], created, renames, notes)

        assert applied == [f"{MIG}/V6__seed_zone.sql"]

    def test_the_allocated_version_passes_the_migration_guard(self, ws):
        """分完版本号之后，那条一直报「版本号重复」的守卫必须不再报"""
        created, renames, notes = set(), {}, []
        codegen.apply_edits(ws, [
            _new_edit(f"{MIG}/V5__seed_zone.sql", "INSERT INTO wms_zone VALUES ('A');\n"),
        ], created, renames, notes)

        problems = check_migration_filenames(
            ws, [f"{MIG}/V6__seed_zone.sql"], base_ref="",
        )
        assert problems == [], f"守卫还在报：{problems}"

    def test_writing_the_old_path_again_lands_on_the_same_file(self, ws):
        """模型下一轮又写老名字 → 落到**同一个**文件，不攒出第二份迁移"""
        created, renames, notes = set(), {}, []
        codegen.apply_edits(ws, [
            _new_edit(f"{MIG}/V5__seed_zone.sql", "INSERT ... 'A';\n"),
        ], created, renames, notes)
        applied = codegen.apply_edits(ws, [
            _new_edit(f"{MIG}/V5__seed_zone.sql", "INSERT ... 'A', 'B';\n"),
        ], created, renames, notes)

        assert applied == [f"{MIG}/V6__seed_zone.sql"]
        assert len(list((ws / MIG).glob("V*.sql"))) == 3, \
            "同一个迁移只能有一个文件（两个同名迁移会被执行两遍）"
        assert (ws / MIG / "V6__seed_zone.sql").read_text(encoding="utf-8") \
            == "INSERT ... 'A', 'B';\n"


class TestTheRenameIsToldToTheModel:
    def test_the_next_prompt_carries_the_new_path(self):
        """机械层替它改了名字 → **必须告诉它**，否则它下一轮还在用老路径"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            mig = root / MIG
            mig.mkdir(parents=True)
            (mig / "V5__create_zone_table.sql").write_text(
                "CREATE TABLE wms_zone(code varchar);\n", encoding="utf-8",
            )

            prompts: list[str] = []

            def fake_llm(prompt: str):
                prompts.append(prompt)
                if len(prompts) == 1:
                    return {
                        "edits": [_new_edit(
                            f"{MIG}/V5__seed_zone.sql",
                            "INSERT INTO wms_zone VALUES ('A');\n",
                        )],
                        "summary": "写了种子迁移",
                    }
                return {"edits": [], "summary": "写完了"}

            calls = {"n": 0}

            def verify():
                calls["n"] += 1
                return ["还差一个东西"] if calls["n"] == 1 else []

            codegen.generate_edits(
                root, [], [], {}, llm_call=fake_llm,
                max_attempts=3, verify=verify,
            )

            assert len(prompts) == 2
            assert "V6__seed_zone.sql" in prompts[1], \
                "下一轮的提示里必须出现新文件名"
            assert "请用这个新路径" in prompts[1], \
                "改名这件事要说明白（它得知道该用哪个路径）"


class TestTheMechanicalLayerMayNotChangeMeaning:
    def test_recreating_a_published_migration_is_not_silently_renamed(self, ws):
        """重建一个**已发布**的迁移 → 走「文件已存在」那条报错。

        机械层要是擅自改名，「重建已发布的迁移」就会被偷偷变成
        「新建一个同义迁移」——那条迁移会被执行两遍。
        """
        created, renames, notes = set(), {}, []
        with pytest.raises(codegen.CodegenError, match="已经存在"):
            codegen.apply_edits(ws, [
                _new_edit(f"{MIG}/V5__create_zone_table.sql", "CREATE TABLE x();\n"),
            ], created, renames, notes)
        assert not (ws / MIG / "V6__create_zone_table.sql").exists()

    def test_a_second_migration_with_the_same_description_is_refused(self, ws):
        """同名迁移建两遍 → 拒绝（Flyway 会执行两遍，第二次撞唯一约束）"""
        created, renames, notes = set(), {}, []
        with pytest.raises(codegen.CodegenError, match="同名的迁移"):
            codegen.apply_edits(ws, [
                _new_edit(f"{MIG}/V6__create_zone_table.sql", "CREATE TABLE x();\n"),
            ], created, renames, notes)
        assert not (ws / MIG / "V6__create_zone_table.sql").exists()

    def test_editing_a_published_migration_is_still_reported(self, tmp_path):
        """改已发布的迁移 → 守卫照旧报「不许改」（改名机制不许吞掉这条）"""
        import subprocess

        root = tmp_path / "gitws"
        mig = root / MIG
        mig.mkdir(parents=True)
        (mig / "V1__init_schema.sql").write_text("CREATE TABLE a();\n", encoding="utf-8")
        (mig / "V5__create_zone_table.sql").write_text(
            "CREATE TABLE wms_zone(code varchar);\n", encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "base"],
            cwd=str(root), check=True, capture_output=True,
        )
        # **改了已发布的迁移**（加一行）
        (mig / "V5__create_zone_table.sql").write_text(
            "CREATE TABLE wms_zone(code varchar, name varchar);\n", encoding="utf-8",
        )

        problems = check_migration_filenames(
            root, [f"{MIG}/V5__create_zone_table.sql"], base_ref="HEAD",
        )
        assert any("已经发布过" in p for p in problems), \
            f"守卫该报的没报：{problems}"
