"""新迁移不许再建一遍别的迁移已经建过的表

**为什么要有这条**（2026-10-30 实测撞出来的）：
模型被拦住「不许改已发布的 V5__create_zone_table.sql」之后，
转头在新建的 V6 里**又写了一遍 `CREATE TABLE wms_zone`**。

结果：Flyway 跑到 V6 时报
`relation "wms_zone" already exists` → **服务启动时直接拒绝**。

**而那一轮链跑报了「六盒全 accepted、落差 0」** ——
因为新构建启动就挂了，测试盒量的是**还没退干净的旧实例**。
所以这条检查和「部署就绪检查」是一对：一条不让它写错，一条不让它蒙混过关。

`CREATE TABLE IF NOT EXISTS` **不算错**（幂等，撞了也不炸）——
只判「不带 IF NOT EXISTS 的重复建表」。
"""

from __future__ import annotations

import pytest

from boxes.wms_development.mapper_binding import (
    check_migration_creates_existing_table,
)

MIG = "server/src/main/resources/db/migration"


@pytest.fixture()
def ws(tmp_path):
    """一个假的隔离工作区：V5 已经建过 wms_zone 了"""
    root = tmp_path / "ws"
    mig = root / MIG
    mig.mkdir(parents=True)
    (mig / "V1__init_schema.sql").write_text(
        "CREATE TABLE wms_sku(id int);\n", encoding="utf-8",
    )
    (mig / "V5__create_zone_table.sql").write_text(
        "CREATE TABLE wms_zone(id bigserial primary key, code varchar);\n",
        encoding="utf-8",
    )
    return root


class TestDuplicateCreateTableIsCaught:
    def test_creating_an_existing_table_again_is_reported(self, ws):
        (ws / MIG / "V6__seed_zone.sql").write_text(
            "CREATE TABLE wms_zone(id bigserial primary key, code varchar);\n"
            "INSERT INTO wms_zone(code) VALUES ('A');\n",
            encoding="utf-8",
        )
        problems = check_migration_creates_existing_table(
            ws, [f"{MIG}/V6__seed_zone.sql"],
        )
        assert problems, "重复建表必须判红（Flyway 会报 relation already exists）"
        text = "\n".join(problems)
        assert "wms_zone" in text
        assert "V5__create_zone_table.sql" in text, \
            "报错要指名**哪个迁移已经建过它**（模型得知道该往哪儿加）"

    def test_the_error_says_how_to_write_it_instead(self, ws):
        """报错要带能修它的材料：只写 ALTER / INSERT，不要重复建表"""
        (ws / MIG / "V6__seed_zone.sql").write_text(
            "CREATE TABLE wms_zone(id bigserial);\n", encoding="utf-8",
        )
        problems = check_migration_creates_existing_table(
            ws, [f"{MIG}/V6__seed_zone.sql"],
        )
        text = "\n".join(problems)
        assert "INSERT" in text and "ALTER" in text, \
            f"要告诉它该怎么写：\n{text}"

    def test_if_not_exists_is_allowed(self, ws):
        """`CREATE TABLE IF NOT EXISTS` 是幂等的 → 不算错"""
        (ws / MIG / "V6__seed_zone.sql").write_text(
            "CREATE TABLE IF NOT EXISTS wms_zone(id bigserial);\n"
            "INSERT INTO wms_zone(code) VALUES ('A');\n",
            encoding="utf-8",
        )
        problems = check_migration_creates_existing_table(
            ws, [f"{MIG}/V6__seed_zone.sql"],
        )
        assert problems == [], f"IF NOT EXISTS 不该被判红：{problems}"

    def test_a_brand_new_table_is_allowed(self, ws):
        (ws / MIG / "V6__add_area.sql").write_text(
            "CREATE TABLE wms_area(id bigserial);\n", encoding="utf-8",
        )
        problems = check_migration_creates_existing_table(
            ws, [f"{MIG}/V6__add_area.sql"],
        )
        assert problems == [], f"新表不该被判红：{problems}"

    def test_creating_the_same_table_twice_in_one_file_is_reported(self, ws):
        (ws / MIG / "V6__twice.sql").write_text(
            "CREATE TABLE wms_area(id bigserial);\n"
            "CREATE TABLE wms_area(id bigserial);\n",
            encoding="utf-8",
        )
        problems = check_migration_creates_existing_table(
            ws, [f"{MIG}/V6__twice.sql"],
        )
        assert problems, "同一个迁移里建两次同一个表，第二次就炸"

    def test_only_changed_migrations_are_judged(self, ws):
        """没被改的迁移不该被重判（老文件早跑过了）"""
        problems = check_migration_creates_existing_table(ws, [])
        assert problems == []
