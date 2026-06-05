"""Tests for the load balancing switch entity."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from conftest import make_subentry_data

from custom_components.danfoss_ally_gateway.const import DOMAIN
from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator
from custom_components.danfoss_ally_gateway.switch import (
    DanfossAllyLoadBalancingSwitch,
    async_setup_entry,
    create_room_entities,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _make_entities(hass, mock_backend, subentry_data):
    """Create a coordinator and switch entities for a multi-TRV room."""
    coord = RoomCoordinator(hass, mock_backend, subentry_data)
    entities = create_room_entities(coord, "test_entry", "test_sub")
    return coord, entities


def _make_single_trv_entities(hass, mock_backend):
    """Create a coordinator and switch entities for a single-TRV room."""
    data = make_subentry_data(trv_ids=["trv_1"])
    coord = RoomCoordinator(hass, mock_backend, data)
    entities = create_room_entities(coord, "test_entry", "test_sub")
    return coord, entities


# ── Entity creation ───────────────────────────────────────────────────


class TestSwitchCreation:
    """Tests for create_room_entities factory."""

    def test_multi_trv_creates_switch(self, hass, mock_backend, subentry_data):
        """A room with multiple TRVs gets a load balancing switch."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert len(entities) == 1
        assert isinstance(entities[0], DanfossAllyLoadBalancingSwitch)

    def test_single_trv_no_switch(self, hass, mock_backend):
        """A room with a single TRV does not get a load balancing switch."""
        _, entities = _make_single_trv_entities(hass, mock_backend)
        assert len(entities) == 0


# ── Entity attributes ────────────────────────────────────────────────


class TestSwitchAttributes:
    """Tests for switch entity attributes."""

    def test_unique_id(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        switch = entities[0]
        assert switch.unique_id == f"{DOMAIN}_test_entry_test_sub_load_balancing"

    def test_translation_placeholders(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        switch = entities[0]
        assert switch._attr_translation_placeholders == {"room_name": "Living Room"}

    def test_translation_key(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        switch = entities[0]
        assert switch.translation_key == "load_balancing"

    def test_icon(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        switch = entities[0]
        assert switch.icon == "mdi:scale-balance"

    def test_device_info(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        switch = entities[0]
        assert switch.device_info is not None
        assert (DOMAIN, "test_entry_test_sub") in switch.device_info["identifiers"]


# ── State ─────────────────────────────────────────────────────────────


class TestSwitchState:
    """Tests for switch state and availability."""

    def test_is_on_reflects_coordinator(self, hass, mock_backend, subentry_data):
        """is_on reads load_balancing_enabled from coordinator state."""
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        switch = entities[0]

        coord.state.load_balancing_enabled = False
        assert switch.is_on is False

        coord.state.load_balancing_enabled = True
        assert switch.is_on is True

    def test_available_reflects_coordinator(self, hass, mock_backend, subentry_data):
        """available tracks coordinator state."""
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        switch = entities[0]

        assert switch.available is False

        coord.state.available = True
        assert switch.available is True


# ── Actions ───────────────────────────────────────────────────────────


class TestSwitchActions:
    """Tests for async_turn_on / async_turn_off."""

    @pytest.mark.asyncio
    async def test_turn_on(self, hass, mock_backend, subentry_data):
        """async_turn_on delegates to coordinator.async_enable_load_balancing."""
        coordinator = RoomCoordinator(hass, mock_backend, subentry_data)
        await coordinator.async_setup()

        switch = DanfossAllyLoadBalancingSwitch(coordinator, "test_entry", "test_sub")

        await switch.async_turn_on()

        assert coordinator.state.load_balancing_enabled is True
        await coordinator.async_teardown()

    @pytest.mark.asyncio
    async def test_turn_off(self, hass, mock_backend, subentry_data):
        """async_turn_off delegates to coordinator.async_disable_load_balancing."""
        coordinator = RoomCoordinator(hass, mock_backend, subentry_data)
        await coordinator.async_setup()

        # Enable first so we can verify the switch turns it off
        coordinator.state.load_balancing_enabled = True

        switch = DanfossAllyLoadBalancingSwitch(coordinator, "test_entry", "test_sub")

        await switch.async_turn_off()

        assert coordinator.state.load_balancing_enabled is False
        await coordinator.async_teardown()


# ── Platform setup ────────────────────────────────────────────────────


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_stores_callback_and_creates_entities(
        self, hass, mock_backend, subentry_data
    ):
        """async_setup_entry stores the add_entities callback and creates entities."""
        coordinator = RoomCoordinator(hass, mock_backend, subentry_data)
        entry_id = "cfg_entry_1"
        sub_id = "sub_1"

        # Set up hass.data as the integration would
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry_id] = {
            "coordinators": {sub_id: coordinator},
        }

        # Mock config entry and add_entities callback
        config_entry = MagicMock()
        config_entry.entry_id = entry_id

        async_add_entities = MagicMock()

        await async_setup_entry(hass, config_entry, async_add_entities)

        # Callback was stored for later subentry additions
        assert (
            hass.data[DOMAIN][entry_id]["platform_add_entities"]["switch"]
            is async_add_entities
        )

        # Entities were added (multi-TRV room produces entities)
        async_add_entities.assert_called_once()
        args, kwargs = async_add_entities.call_args
        assert len(args[0]) == 1
        assert isinstance(args[0][0], DanfossAllyLoadBalancingSwitch)
        assert kwargs["config_subentry_id"] == sub_id

    @pytest.mark.asyncio
    async def test_no_entities_for_single_trv(self, hass, mock_backend):
        """async_setup_entry does not add entities for single-TRV rooms."""
        data = make_subentry_data(trv_ids=["trv_1"])
        coordinator = RoomCoordinator(hass, mock_backend, data)
        entry_id = "cfg_entry_2"
        sub_id = "sub_2"

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry_id] = {
            "coordinators": {sub_id: coordinator},
        }

        config_entry = MagicMock()
        config_entry.entry_id = entry_id

        async_add_entities = MagicMock()

        await async_setup_entry(hass, config_entry, async_add_entities)

        # Callback stored but no entities added (single TRV)
        assert (
            hass.data[DOMAIN][entry_id]["platform_add_entities"]["switch"]
            is async_add_entities
        )
        # Only the storage call happened, no entity-adding call
        async_add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_coordinators(self, hass):
        """async_setup_entry handles missing coordinators gracefully."""
        entry_id = "cfg_entry_3"

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry_id] = {}

        config_entry = MagicMock()
        config_entry.entry_id = entry_id

        async_add_entities = MagicMock()

        await async_setup_entry(hass, config_entry, async_add_entities)

        # Callback stored, no entities added
        assert (
            hass.data[DOMAIN][entry_id]["platform_add_entities"]["switch"]
            is async_add_entities
        )
        async_add_entities.assert_not_called()
