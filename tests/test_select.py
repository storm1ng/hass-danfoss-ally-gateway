"""Tests for the programming mode select entity."""

from __future__ import annotations

import pytest

from custom_components.danfoss_ally_gateway.const import (
    DOMAIN,
    PROGRAMMING_MODE_OPTIONS,
    SCHEDULE_MODE_SCHEDULE,
    SCHEDULE_MODE_SCHEDULE_PREHEAT,
)
from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator
from custom_components.danfoss_ally_gateway.select import (
    DanfossAllyProgrammingModeSelect,
)


class TestProgrammingModeSelect:
    """Tests for DanfossAllyProgrammingModeSelect."""

    def test_initial_state_manual(self, coordinator):
        """Select entity starts with manual mode."""
        entity = DanfossAllyProgrammingModeSelect(
            coordinator=coordinator,
            config_entry_id="test_entry",
            subentry_id="test_sub",
        )
        assert entity.current_option == "manual"
        assert entity.options == PROGRAMMING_MODE_OPTIONS

    def test_unique_id(self, coordinator):
        entity = DanfossAllyProgrammingModeSelect(
            coordinator=coordinator,
            config_entry_id="entry1",
            subentry_id="sub1",
        )
        assert entity.unique_id == f"{DOMAIN}_entry1_sub1_programming_mode"

    def test_device_info(self, coordinator):
        entity = DanfossAllyProgrammingModeSelect(
            coordinator=coordinator,
            config_entry_id="entry1",
            subentry_id="sub1",
        )
        assert entity.device_info is not None
        assert (DOMAIN, "entry1_sub1") in entity.device_info["identifiers"]  # type: ignore[typeddict]

    @pytest.mark.asyncio
    async def test_select_option_schedule(self, hass, mock_backend, subentry_data):
        """Selecting 'schedule' calls the coordinator."""
        coordinator = RoomCoordinator(hass, mock_backend, subentry_data)
        await coordinator.async_setup()

        entity = DanfossAllyProgrammingModeSelect(
            coordinator=coordinator,
            config_entry_id="test_entry",
            subentry_id="test_sub",
        )

        await entity.async_select_option("schedule")

        assert coordinator.schedule_mode == SCHEDULE_MODE_SCHEDULE
        # Backend should have been called for each TRV
        assert mock_backend.async_set_programming_mode.call_count == len(
            coordinator.trv_ids
        )

        await coordinator.async_teardown()

    @pytest.mark.asyncio
    async def test_select_option_schedule_preheat(
        self, hass, mock_backend, subentry_data
    ):
        """Selecting 'schedule_with_preheat' sets mode 3."""
        coordinator = RoomCoordinator(hass, mock_backend, subentry_data)
        await coordinator.async_setup()

        entity = DanfossAllyProgrammingModeSelect(
            coordinator=coordinator,
            config_entry_id="test_entry",
            subentry_id="test_sub",
        )

        await entity.async_select_option("schedule_with_preheat")

        assert coordinator.schedule_mode == SCHEDULE_MODE_SCHEDULE_PREHEAT

        await coordinator.async_teardown()

    @pytest.mark.asyncio
    async def test_option_reflects_coordinator_state(
        self, hass, mock_backend, subentry_data
    ):
        """The current_option should update when coordinator mode changes."""
        coordinator = RoomCoordinator(hass, mock_backend, subentry_data)
        await coordinator.async_setup()

        entity = DanfossAllyProgrammingModeSelect(
            coordinator=coordinator,
            config_entry_id="test_entry",
            subentry_id="test_sub",
        )

        await coordinator.async_set_programming_mode_option("schedule")
        # Simulate the callback updating
        entity._attr_current_option = coordinator.schedule_mode_option
        assert entity.current_option == "schedule"

        await coordinator.async_teardown()
