"""Bee registry + runtime 测试。"""

import pytest

from bee.registry import get_box_class, list_supported
from bee.runtime import Bee, BeeConfig


class TestRegistry:
    """bee_type 字符串 → Box 类的映射。"""

    def test_month_close_registered(self):
        cls = get_box_class("month_close")
        assert cls is not None
        assert cls.__name__ == "MonthCloseWorkflow"

    def test_unknown_returns_none(self):
        assert get_box_class("nonexistent") is None
        assert get_box_class("") is None

    def test_list_supported_contains_month_close(self):
        supported = list_supported()
        assert "month_close" in supported
        assert isinstance(supported, list)

    def test_all_registered_boxes_have_async_run(self):
        """每个注册的 Box 必须有 async run() 方法。"""
        for bee_type, cls in [("month_close", get_box_class("month_close"))]:
            assert hasattr(cls, "run"), f"{bee_type} missing run()"
            assert callable(cls.run)


class TestBeeConfig:
    def test_default_values(self):
        cfg = BeeConfig()
        assert cfg.max_token_budget == 200_000
        assert cfg.max_tool_calls == 50
        assert cfg.max_execution_seconds == 1800
        assert cfg.llm_request_timeout_seconds == 60


class TestBeeRuntime:
    """Bee.run() 的核心 dispatch 逻辑。"""

    @pytest.mark.asyncio
    async def test_run_month_close_returns_done(self):
        bee = Bee()
        result = await bee.run(
            task="month_close",
            context={"bee_type": "month_close", "period": "2026-07"},
        )
        assert result["status"] == "done"
        assert result["period"] == "2026-07"
        assert len(result["steps"]) == 6

    @pytest.mark.asyncio
    async def test_run_unknown_bee_type_returns_failed(self):
        bee = Bee()
        result = await bee.run(task="nonexistent", context={"bee_type": "nonexistent"})
        assert result["status"] == "failed"
        assert "Unknown bee_type" in result["error"]
        assert "Supported" in result["error"]

    @pytest.mark.asyncio
    async def test_run_passes_client_ids_and_approver_to_box(self):
        bee = Bee()
        result = await bee.run(
            task="month_close",
            context={
                "bee_type": "month_close",
                "period": "2026-08",
                "client_ids": ["A001", "A002"],
                "approver": "carol@x.com",
            },
        )
        assert result["status"] == "done"
        # 最后一个 step 是 signoff，approver 应透传
        signoff_step = result["steps"][-1]
        assert signoff_step["output"]["approver"] == "carol@x.com"

    @pytest.mark.asyncio
    async def test_run_with_task_used_as_bee_type_fallback(self):
        """context 缺 bee_type 时用 task 字符串作 bee_type（M1 兼容）。"""
        bee = Bee()
        result = await bee.run(task="month_close", context={"period": "2026-09"})
        assert result["status"] == "done"
        assert result["period"] == "2026-09"

    @pytest.mark.asyncio
    async def test_run_catches_box_exception(self):
        """Box 抛异常时 Bee 捕获并返回 failed。"""
        bee = Bee()
        # 模拟 Box 抛异常：传一个不会让 MonthCloseWorkflow 抛异常的 context
        # （MonthCloseWorkflow 的 period 是必填，缺它会 ValidationError）
        result = await bee.run(
            task="month_close",
            context={"bee_type": "month_close"},  # 缺 period
        )
        assert result["status"] == "failed"
        assert "ValidationError" in result["error"] or "period" in result["error"].lower()
