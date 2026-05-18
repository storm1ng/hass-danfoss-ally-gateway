"""Tests for schedule management services."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.danfoss_ally_gateway.const import DOMAIN
from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator
from custom_components.danfoss_ally_gateway.services import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_ENABLED,
    ATTR_PREHEAT,
    ATTR_SCHEDULE,
    ATTR_SUBENTRY_ID,
    SERVICE_CLEAR_ROOM_SCHEDULE,
    SERVICE_SET_ROOM_SCHEDULE,
    SERVICE_SET_SCHEDULE_MODE,
    _get_coordinator,
    async_handle_clear_room_schedule,
    async_handle_set_room_schedule,
    async_handle_set_schedule_mode,
    async_register_services,
    async_unregister_services,
)


@pytest.fixture
def setup_domain_data(hass, mock_backend, subentry_data):
    """Set up hass.data with a coordinator for service tests."""
    coordinator = RoomCoordinator(hass, mock_backend, subentry_data)
    hass.data[DOMAIN] = {
        "test_entry_id": {
            "backend": mock_backend,
            "coordinators": {
                "test_subentry_id": coordinator,
            },
        }
    }
    return coordinator


# ── _get_coordinator ──────────────────────────────────────────────────


class TestGetCoordinator:
    """Tests for the _get_coordinator lookup helper."""

    def test_success(self, hass, setup_domain_data):
        coord = _get_coordinator(hass, "test_entry_id", "test_subentry_id")
        assert coord is setup_domain_data

    def test_domain_not_setup(self, hass):
        with pytest.raises(ServiceValidationError, match="not set up"):
            _get_coordinator(hass, "test_entry_id", "test_subentry_id")

    def test_entry_not_found(self, hass, setup_domain_data):
        with pytest.raises(ServiceValidationError, match="not found"):
            _get_coordinator(hass, "wrong_entry_id", "test_subentry_id")

    def test_subentry_not_found(self, hass, setup_domain_data):
        with pytest.raises(ServiceValidationError, match="not found"):
            _get_coordinator(hass, "test_entry_id", "wrong_subentry_id")


# ── Service Registration ──────────────────────────────────────────────


class TestServiceRegistration:
    """Tests for service registration and unregistration."""

    def test_register_services(self, hass, setup_domain_data):
        async_register_services(hass)
        assert hass.services.has_service(DOMAIN, SERVICE_SET_ROOM_SCHEDULE)
        assert hass.services.has_service(DOMAIN, SERVICE_CLEAR_ROOM_SCHEDULE)
        assert hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE_MODE)

    def test_double_register_is_idempotent(self, hass, setup_domain_data):
        async_register_services(hass)
        async_register_services(hass)
        assert hass.services.has_service(DOMAIN, SERVICE_SET_ROOM_SCHEDULE)

    def test_unregister_with_entries_remaining(self, hass, setup_domain_data):
        async_register_services(hass)
        # Domain data still has entries, so services should NOT be removed
        async_unregister_services(hass)
        assert hass.services.has_service(DOMAIN, SERVICE_SET_ROOM_SCHEDULE)

    def test_unregister_when_empty(self, hass, setup_domain_data):
        async_register_services(hass)
        hass.data[DOMAIN].clear()
        async_unregister_services(hass)
        assert not hass.services.has_service(DOMAIN, SERVICE_SET_ROOM_SCHEDULE)


# ── set_room_schedule service ─────────────────────────────────────────


class TestSetRoomScheduleService:
    """Tests for the set_room_schedule service handler."""

    async def test_valid_schedule(self, hass, setup_domain_data):
        coordinator = setup_domain_data
        await coordinator.async_setup()

        schedule_data = {
            "days": [
                [],  # Sunday
                [{"time": 360, "temp": 21.0}, {"time": 1320, "temp": 18.0}],  # Monday
                [],
                [],
                [],
                [],
                [],
            ]
        }

        call = MagicMock()
        call.hass = hass
        call.data = {
            ATTR_CONFIG_ENTRY_ID: "test_entry_id",
            ATTR_SUBENTRY_ID: "test_subentry_id",
            ATTR_SCHEDULE: schedule_data,
        }

        await async_handle_set_room_schedule(call)

        assert coordinator._current_schedule is not None
        await coordinator.async_teardown()

    async def test_invalid_schedule_data(self, hass, setup_domain_data):
        call = MagicMock()
        call.hass = hass
        call.data = {
            ATTR_CONFIG_ENTRY_ID: "test_entry_id",
            ATTR_SUBENTRY_ID: "test_subentry_id",
            ATTR_SCHEDULE: {"invalid": "data"},
        }

        # from_dict with missing keys may raise or produce empty schedule
        # which is valid, so this test checks the handler doesn't crash
        await async_handle_set_room_schedule(call)

    async def test_wrong_entry_id(self, hass, setup_domain_data):
        call = MagicMock()
        call.hass = hass
        call.data = {
            ATTR_CONFIG_ENTRY_ID: "wrong_id",
            ATTR_SUBENTRY_ID: "test_subentry_id",
            ATTR_SCHEDULE: {"days": [[] for _ in range(7)]},
        }

        with pytest.raises(ServiceValidationError):
            await async_handle_set_room_schedule(call)


# ── clear_room_schedule service ───────────────────────────────────────


class TestClearRoomScheduleService:
    """Tests for the clear_room_schedule service handler."""

    async def test_clear_schedule(self, hass, mock_backend, setup_domain_data):
        coordinator = setup_domain_data
        await coordinator.async_setup()

        call = MagicMock()
        call.hass = hass
        call.data = {
            ATTR_CONFIG_ENTRY_ID: "test_entry_id",
            ATTR_SUBENTRY_ID: "test_subentry_id",
        }

        await async_handle_clear_room_schedule(call)

        assert mock_backend.async_clear_weekly_schedule.call_count == 2
        assert coordinator._current_schedule is None
        await coordinator.async_teardown()


# ── set_schedule_mode service ─────────────────────────────────────────


class TestSetScheduleModeService:
    """Tests for the set_schedule_mode service handler."""

    async def test_enable_schedule(self, hass, mock_backend, setup_domain_data):
        coordinator = setup_domain_data
        await coordinator.async_setup()

        call = MagicMock()
        call.hass = hass
        call.data = {
            ATTR_CONFIG_ENTRY_ID: "test_entry_id",
            ATTR_SUBENTRY_ID: "test_subentry_id",
            ATTR_ENABLED: True,
            ATTR_PREHEAT: False,
        }

        await async_handle_set_schedule_mode(call)

        assert mock_backend.async_set_programming_mode.call_count == 2
        for c in mock_backend.async_set_programming_mode.call_args_list:
            assert c[0][1] == 1  # SCHEDULE_MODE_SCHEDULE
        await coordinator.async_teardown()

    async def test_enable_schedule_with_preheat(
        self, hass, mock_backend, setup_domain_data
    ):
        coordinator = setup_domain_data
        await coordinator.async_setup()

        call = MagicMock()
        call.hass = hass
        call.data = {
            ATTR_CONFIG_ENTRY_ID: "test_entry_id",
            ATTR_SUBENTRY_ID: "test_subentry_id",
            ATTR_ENABLED: True,
            ATTR_PREHEAT: True,
        }

        await async_handle_set_schedule_mode(call)

        for c in mock_backend.async_set_programming_mode.call_args_list:
            assert c[0][1] == 3  # SCHEDULE_MODE_SCHEDULE_PREHEAT
        await coordinator.async_teardown()

    async def test_disable_schedule(self, hass, mock_backend, setup_domain_data):
        coordinator = setup_domain_data
        await coordinator.async_setup()

        call = MagicMock()
        call.hass = hass
        call.data = {
            ATTR_CONFIG_ENTRY_ID: "test_entry_id",
            ATTR_SUBENTRY_ID: "test_subentry_id",
            ATTR_ENABLED: False,
        }

        await async_handle_set_schedule_mode(call)

        for c in mock_backend.async_set_programming_mode.call_args_list:
            assert c[0][1] == 0  # SCHEDULE_MODE_MANUAL
        await coordinator.async_teardown()
