"""Shared test fixtures for Danfoss Ally Gateway tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.danfoss_ally_gateway.backend import DanfossBackend, TRVState
from custom_components.danfoss_ally_gateway.const import (
    BACKEND_Z2M,
    CONF_BACKEND,
    CONF_MQTT_BASE_TOPIC,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


# ── Mock Backend ──────────────────────────────────────────────────────


class MockBackend(DanfossBackend):
    """Mock backend for testing coordinator and entity logic.

    All write/command methods are AsyncMock so tests can assert calls.
    State callbacks can be triggered manually via `fire_state_update()`.
    """

    # Satisfy ABC by defining all abstract methods at class level.
    # These will be replaced by AsyncMock instances in __init__.
    async def async_set_external_temperature(self, trv_id, temperature):
        """Placeholder."""

    async def async_set_occupied_heating_setpoint(self, trv_id, temperature):
        """Placeholder."""

    async def async_set_heat_available(self, trv_id, available):
        """Placeholder."""

    async def async_set_load_room_mean(self, trv_id, value):
        """Placeholder."""

    async def async_set_external_window_open(self, trv_id, is_open):
        """Placeholder."""

    async def async_send_setpoint_command(self, trv_id, temperature, command_type):
        """Placeholder."""

    async def async_send_preheat_command(self, trv_id, timestamp):
        """Placeholder."""

    async def async_sync_time(self, trv_id):
        """Placeholder."""

    async def async_set_weekly_schedule(
        self, trv_id, day_of_week, num_transitions, mode, transitions
    ):
        """Placeholder."""

    async def async_get_weekly_schedule(self, trv_id, day_of_week):
        """Placeholder."""

    async def async_clear_weekly_schedule(self, trv_id):
        """Placeholder."""

    async def async_set_programming_mode(self, trv_id, mode):
        """Placeholder."""

    async def async_read_sw_error_code(self, trv_id):
        """Placeholder."""

    def __init__(self, hass):
        """Initialize mock backend."""
        super().__init__(hass)
        self._subscribed_trvs: set[str] = set()

        # Replace all write/command methods with AsyncMock for assertion
        self.async_set_external_temperature = AsyncMock()
        self.async_set_occupied_heating_setpoint = AsyncMock()
        self.async_set_heat_available = AsyncMock()
        self.async_set_load_room_mean = AsyncMock()
        self.async_set_external_window_open = AsyncMock()
        self.async_send_setpoint_command = AsyncMock()
        self.async_send_preheat_command = AsyncMock()
        self.async_sync_time = AsyncMock()
        self.async_set_weekly_schedule = AsyncMock()
        self.async_get_weekly_schedule = AsyncMock(return_value=None)
        self.async_clear_weekly_schedule = AsyncMock()
        self.async_set_programming_mode = AsyncMock()
        self.async_read_sw_error_code = AsyncMock(return_value=None)

    async def async_setup(self) -> None:
        """No-op setup."""

    async def async_teardown(self) -> None:
        """No-op teardown."""

    async def async_subscribe_trv(self, trv_id: str) -> None:
        """Track subscription."""
        self._subscribed_trvs.add(trv_id)

    async def async_unsubscribe_trv(self, trv_id: str) -> None:
        """Track unsubscription."""
        self._subscribed_trvs.discard(trv_id)

    async def async_get_trv_state(self, trv_id: str) -> TRVState | None:
        """Return None (no cached state in mock)."""
        return None

    def fire_state_update(self, trv_id: str, state: TRVState) -> None:
        """Manually fire a state update as if a TRV reported."""
        self._fire_state_update(trv_id, state)


@pytest.fixture
def mock_backend(hass):
    """Create a mock backend."""
    return MockBackend(hass)


# ── TRVState factory ──────────────────────────────────────────────────


def make_trv_state(
    entity_id: str = "trv_1",
    local_temperature: float | None = 21.0,
    occupied_heating_setpoint: float | None = 22.0,
    pi_heating_demand: int | None = 50,
    load_estimate: int | None = 100,
    window_open_detection: int | None = 0,
    setpoint_change_source: int | None = None,
    preheat_status: bool | None = None,
    preheat_time: int | None = None,
    **kwargs: Any,
) -> TRVState:
    """Create a TRVState with sensible defaults."""
    return TRVState(
        entity_id=entity_id,
        local_temperature=local_temperature,
        occupied_heating_setpoint=occupied_heating_setpoint,
        pi_heating_demand=pi_heating_demand,
        load_estimate=load_estimate,
        window_open_detection=window_open_detection,
        setpoint_change_source=setpoint_change_source,
        preheat_status=preheat_status,
        preheat_time=preheat_time,
        **kwargs,
    )


# ── Config entry helpers ──────────────────────────────────────────────


def make_config_entry_data(
    backend: str = BACKEND_Z2M,
    mqtt_base_topic: str = "zigbee2mqtt",
) -> dict[str, Any]:
    """Build config entry data dict."""
    data: dict[str, Any] = {CONF_BACKEND: backend}
    if backend == BACKEND_Z2M:
        data[CONF_MQTT_BASE_TOPIC] = mqtt_base_topic
    return data
