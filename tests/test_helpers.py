"""Tests for coordinator helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.danfoss_ally_gateway.coordinator.helpers import (
    async_for_each_trv,
)


class TestAsyncForEachTrv:
    """Tests for async_for_each_trv helper."""

    @pytest.mark.asyncio
    async def test_calls_func_for_each_trv(self):
        """Each TRV ID is passed to the async callable."""
        func = AsyncMock()
        await async_for_each_trv(["trv_1", "trv_2", "trv_3"], func)

        assert func.call_count == 3
        func.assert_any_await("trv_1")
        func.assert_any_await("trv_2")
        func.assert_any_await("trv_3")

    @pytest.mark.asyncio
    async def test_forwards_extra_args(self):
        """Extra positional args are forwarded after the TRV ID."""
        func = AsyncMock()
        await async_for_each_trv(["trv_1"], func, 42, "extra")

        func.assert_awaited_once_with("trv_1", 42, "extra")

    @pytest.mark.asyncio
    async def test_empty_trv_list(self):
        """An empty TRV list results in no calls."""
        func = AsyncMock()
        await async_for_each_trv([], func)

        func.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_logged_and_continues(self, caplog):
        """If one TRV raises, the error is logged and remaining TRVs still run."""
        func = AsyncMock(side_effect=[RuntimeError("boom"), None])
        await async_for_each_trv(
            ["trv_1", "trv_2"],
            func,
            room_name="Kitchen",
            action="set temperature",
        )

        # Both TRVs were still called
        assert func.call_count == 2
        # Error was logged
        assert "Failed to set temperature on TRV trv_1 in room 'Kitchen'" in caplog.text

    @pytest.mark.asyncio
    async def test_exception_default_action_message(self, caplog):
        """When action is empty, the default 'perform action' text is logged."""
        func = AsyncMock(side_effect=RuntimeError("fail"))
        await async_for_each_trv(["trv_1"], func, room_name="Bedroom")

        assert "Failed to perform action on TRV trv_1 in room 'Bedroom'" in caplog.text
