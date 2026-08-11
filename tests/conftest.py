"""beeOS 测试 conftest - 提供 SQLite in-memory DB + HTTPX AsyncClient fixture。

设计原则：
- 用 SQLite 内存库（避免依赖本机 PG）
- Monkey-patch beeos_core.db 三个全局：engine / session_factory / session_scope
- 每个测试一个全新的 DB（function scope）
- Queen / Bee / Box 测试可独立跑，不依赖本机服务
- 本地 PG 仅在 `make init-db` + `uv run queen` 真实端到端测试时用
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import beeos_core.db as core_db
from beeos_core.models import Base

# 覆盖默认的 PG DSN — 必须在任何 import queen/bee 之前
TEST_DSN = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """每个测试一个新 SQLite 内存引擎，建表后丢弃。"""
    eng = create_async_engine(TEST_DSN, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """提供 session 工厂（绑定到测试 engine）。"""
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def patched_db(monkeypatch: pytest.MonkeyPatch, engine: AsyncEngine, db_session_factory: Any) -> AsyncIterator[None]:
    """Monkey-patch beeos_core.db，让 get_engine / get_session_factory / session_scope 都走测试 engine。

    这样应用代码（queen / bee / box）调 session_scope() 实际用的是 SQLite 内存库。
    """
    monkeypatch.setattr(core_db, "_engine", engine)
    monkeypatch.setattr(core_db, "_session_factory", db_session_factory)

    # 重新定义 session_scope 绑到测试 factory（直接覆盖模块里的函数）
    @asynccontextmanager
    async def _test_session_scope() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(core_db, "session_scope", _test_session_scope)
    yield


@pytest_asyncio.fixture
async def client(patched_db: None) -> AsyncIterator[AsyncClient]:
    """提供 HTTPX AsyncClient，调 Queen FastAPI app。

    关键：ASGITransport 不会自动跑 lifespan，要用 app.router.lifespan_context
    显式包起来。lifespan startup 调 Base.metadata.create_all 建表。
    """
    from queen.api.app import app  # late import：patched_db 必须在 queen 之前

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
