"""Tests for the RoomCoordinator."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import make_subentry_data, make_trv_state
from homeassistant import config_entries
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr

from custom_components.danfoss_ally_gateway.backend.z2m import Z2MBackend
from custom_components.danfoss_ally_gateway.const import (
    LOAD_BALANCE_DISABLED_VALUE,
    SETPOINT_SOURCE_MANUAL,
    SETPOINT_TYPE_USER,
    WINDOW_OPEN_DETECTED,
)
from custom_components.danfoss_ally_gateway.coordinator import (
    RoomCoordinator,
    RoomState,
)

# ── Setup / Teardown ──────────────────────────────────────────────────


class TestCoordinatorLifecycle:
    """Tests for coordinator setup and teardown."""

    async def test_setup_subscribes_to_trvs(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        assert "trv_1" in mock_backend._subscribed_trvs
        assert "trv_2" in mock_backend._subscribed_trvs
        await coord.async_teardown()

    async def test_teardown_unsubscribes_trvs(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        await coord.async_teardown()
        assert "trv_1" not in mock_backend._subscribed_trvs
        assert "trv_2" not in mock_backend._subscribed_trvs

    async def test_setup_registers_state_callback(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        # Backend should have one callback registered
        assert len(mock_backend._state_callbacks) == 1
        await coord.async_teardown()

    async def test_properties(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        assert coord.room_name == "Living Room"
        assert coord.trv_ids == ["trv_1", "trv_2"]


# ── TRV State Handling ────────────────────────────────────────────────


class TestTRVStateHandling:
    """Tests for TRV state update processing."""

    async def test_state_update_populates_trv_states(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        state = make_trv_state(
            "trv_1", local_temperature=21.5, occupied_heating_setpoint=22.0
        )
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        assert "trv_1" in coord.state.trv_states
        assert coord.state.trv_states["trv_1"].local_temperature == 21.5
        await coord.async_teardown()

    async def test_ignores_unknown_trv(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        state = make_trv_state("trv_unknown")
        mock_backend.fire_state_update("trv_unknown", state)
        await hass.async_block_till_done()

        assert "trv_unknown" not in coord.state.trv_states
        await coord.async_teardown()

    async def test_target_temp_from_first_trv(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", occupied_heating_setpoint=22.0)
        )
        mock_backend.fire_state_update(
            "trv_2", make_trv_state("trv_2", occupied_heating_setpoint=23.0)
        )
        await hass.async_block_till_done()

        # Should use first TRV in order (trv_1)
        assert coord.state.target_temperature == 22.0
        await coord.async_teardown()

    async def test_max_pi_heating_demand(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", pi_heating_demand=30)
        )
        mock_backend.fire_state_update(
            "trv_2", make_trv_state("trv_2", pi_heating_demand=60)
        )
        await hass.async_block_till_done()

        assert coord.state.max_pi_heating_demand == 60
        assert coord.state.heat_required is True
        await coord.async_teardown()

    async def test_window_open_detection(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED)
        )
        await hass.async_block_till_done()

        assert coord.state.window_open is True
        await coord.async_teardown()

    async def test_availability_tracking(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Initially unavailable (no updates yet)
        assert coord.state.available is False

        mock_backend.fire_state_update("trv_1", make_trv_state("trv_1"))
        await hass.async_block_till_done()

        # Now available
        assert coord.state.available is True
        await coord.async_teardown()

    async def test_state_callback_notified(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        callback_states = []
        coord.register_state_callback(lambda s: callback_states.append(s))

        mock_backend.fire_state_update("trv_1", make_trv_state("trv_1"))
        await hass.async_block_till_done()

        assert len(callback_states) == 1
        assert isinstance(callback_states[0], RoomState)
        await coord.async_teardown()

    async def test_unregister_callback(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        callback_states = []
        unsub = coord.register_state_callback(lambda s: callback_states.append(s))
        unsub()

        mock_backend.fire_state_update("trv_1", make_trv_state("trv_1"))
        await hass.async_block_till_done()

        assert len(callback_states) == 0
        await coord.async_teardown()


# ── External Temperature ──────────────────────────────────────────────


class TestExternalTemperature:
    """Tests for external temperature forwarding."""

    async def test_sends_initial_ext_temp(self, hass, mock_backend):
        """External temp is sent on setup when sensor has a valid state."""
        hass.states.async_set("sensor.temp", "21.5")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        assert mock_backend.async_set_external_temperature.call_count == 2
        call_args = mock_backend.async_set_external_temperature.call_args_list
        assert call_args[0][0] == ("trv_1", 21.5)
        assert call_args[1][0] == ("trv_2", 21.5)
        await coord.async_teardown()

    async def test_skips_unavailable_sensor(self, hass, mock_backend):
        hass.states.async_set("sensor.temp", STATE_UNAVAILABLE)
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        assert mock_backend.async_set_external_temperature.call_count == 0
        await coord.async_teardown()

    async def test_no_sensor_configured(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        assert mock_backend.async_set_external_temperature.call_count == 0
        await coord.async_teardown()

    async def test_teardown_disables_ext_temp(self, hass, mock_backend):
        hass.states.async_set("sensor.temp", "21.5")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()
        mock_backend.async_set_external_temperature.reset_mock()

        await coord.async_teardown()

        assert mock_backend.async_set_external_temperature.call_count == 2
        for call in mock_backend.async_set_external_temperature.call_args_list:
            assert call[0][1] == -80.0  # EXTERNAL_TEMP_DISABLED / 100

    async def test_ext_temp_uses_trv_covered_state(self, hass, mock_backend):
        """radiator_covered from TRV state updates per-TRV ext temp tracking."""
        hass.states.async_set("sensor.temp", "21.5")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        assert coord._ext_temp_trv["trv_1"].covered is False
        assert coord._ext_temp_trv["trv_2"].covered is False

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", radiator_covered=True)
        )
        assert coord._ext_temp_trv["trv_1"].covered is True
        assert coord._ext_temp_trv["trv_2"].covered is False

        await coord.async_teardown()

    async def test_ext_temp_mixed_room_tracking(self, hass, mock_backend):
        """Mixed covered/exposed room: each TRV tracks independently."""
        hass.states.async_set("sensor.temp", "21.5")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", radiator_covered=True)
        )
        mock_backend.fire_state_update(
            "trv_2", make_trv_state("trv_2", radiator_covered=False)
        )

        assert coord._ext_temp_trv["trv_1"].last_temp_sent == 21.5
        assert coord._ext_temp_trv["trv_2"].last_temp_sent == 21.5
        assert coord._ext_temp_trv["trv_1"].covered is True
        assert coord._ext_temp_trv["trv_2"].covered is False

        await coord.async_teardown()

    async def test_ext_temp_per_trv_initial_send_sets_tracking(
        self, hass, mock_backend
    ):
        """Initial send populates per-TRV tracking for all TRVs."""
        hass.states.async_set("sensor.temp", "20.0")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        for trv_id in ["trv_1", "trv_2"]:
            ext_state = coord._ext_temp_trv[trv_id]
            assert ext_state.last_temp_sent == 20.0
            assert ext_state.last_send_time > 0.0
            assert ext_state.timer is not None

        await coord.async_teardown()

    async def test_ext_temp_teardown_cancels_all_per_trv_timers(
        self, hass, mock_backend
    ):
        """Teardown cancels all per-TRV timers."""
        hass.states.async_set("sensor.temp", "20.0")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        for ext_state in coord._ext_temp_trv.values():
            assert ext_state.timer is not None

        await coord.async_teardown()

        for ext_state in coord._ext_temp_trv.values():
            assert ext_state.timer is None

    async def test_ext_temp_covered_change_at_runtime(self, hass, mock_backend):
        """TRV changing radiator_covered at runtime updates tracking."""
        hass.states.async_set("sensor.temp", "21.5")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        assert coord._ext_temp_trv["trv_1"].covered is False

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", radiator_covered=True)
        )
        assert coord._ext_temp_trv["trv_1"].covered is True

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", radiator_covered=False)
        )
        assert coord._ext_temp_trv["trv_1"].covered is False

        await coord.async_teardown()


# ── Heat Availability ─────────────────────────────────────────────────


class TestHeatAvailability:
    """Tests for heat availability signaling."""

    async def test_climate_heat_source(self, hass, mock_backend):
        hass.states.async_set("climate.boiler", "heat", {"hvac_action": "heating"})
        data = make_subentry_data(
            heat_source="climate.boiler",
            heat_source_type="climate",
        )
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        assert mock_backend.async_set_heat_available.call_count == 2
        for call in mock_backend.async_set_heat_available.call_args_list:
            assert call[0][1] is True
        assert coord.state.heat_available is True
        await coord.async_teardown()

    async def test_binary_sensor_heat_source(self, hass, mock_backend):
        hass.states.async_set("binary_sensor.boiler", STATE_ON)
        data = make_subentry_data(
            heat_source="binary_sensor.boiler",
            heat_source_type="binary_sensor",
        )
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        assert mock_backend.async_set_heat_available.call_count == 2
        for call in mock_backend.async_set_heat_available.call_args_list:
            assert call[0][1] is True
        await coord.async_teardown()

    async def test_climate_idle_means_no_heat(self, hass, mock_backend):
        hass.states.async_set("climate.boiler", "heat", {"hvac_action": "idle"})
        data = make_subentry_data(
            heat_source="climate.boiler",
            heat_source_type="climate",
        )
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        assert mock_backend.async_set_heat_available.call_count == 2
        for call in mock_backend.async_set_heat_available.call_args_list:
            assert call[0][1] is False
        await coord.async_teardown()


# ── Setpoint Coordination ─────────────────────────────────────────────


class TestSetpointCoordination:
    """Tests for setpoint coordination between TRVs."""

    async def test_manual_dial_forwards_to_others(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Initial state
        old = make_trv_state("trv_1", occupied_heating_setpoint=20.0)
        mock_backend.fire_state_update("trv_1", old)
        await hass.async_block_till_done()

        # Manual dial change
        new = make_trv_state(
            "trv_1",
            occupied_heating_setpoint=22.0,
            setpoint_change_source=SETPOINT_SOURCE_MANUAL,
        )
        mock_backend.fire_state_update("trv_1", new)
        await hass.async_block_till_done()

        # Should forward to trv_2 as Type 1
        mock_backend.async_send_setpoint_command.assert_called_once_with(
            "trv_2", 22.0, SETPOINT_TYPE_USER
        )
        await coord.async_teardown()

    async def test_no_forward_for_single_trv(
        self, hass, mock_backend, single_trv_subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, single_trv_subentry_data)
        await coord.async_setup()

        old = make_trv_state("trv_1", occupied_heating_setpoint=20.0)
        mock_backend.fire_state_update("trv_1", old)
        await hass.async_block_till_done()

        new = make_trv_state(
            "trv_1",
            occupied_heating_setpoint=22.0,
            setpoint_change_source=SETPOINT_SOURCE_MANUAL,
        )
        mock_backend.fire_state_update("trv_1", new)
        await hass.async_block_till_done()

        mock_backend.async_send_setpoint_command.assert_not_called()
        await coord.async_teardown()

    async def test_no_forward_for_schedule_source(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        old = make_trv_state("trv_1", occupied_heating_setpoint=20.0)
        mock_backend.fire_state_update("trv_1", old)
        await hass.async_block_till_done()

        # Schedule-initiated change (source=1), should NOT forward
        new = make_trv_state(
            "trv_1",
            occupied_heating_setpoint=22.0,
            setpoint_change_source=1,
        )
        mock_backend.fire_state_update("trv_1", new)
        await hass.async_block_till_done()

        mock_backend.async_send_setpoint_command.assert_not_called()
        await coord.async_teardown()

    async def test_set_room_temperature(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        await coord.async_set_room_temperature(23.0)

        assert mock_backend.async_set_occupied_heating_setpoint.call_count == 2
        calls = mock_backend.async_set_occupied_heating_setpoint.call_args_list
        assert calls[0][0] == ("trv_1", 23.0)
        assert calls[1][0] == ("trv_2", 23.0)
        assert coord.state.target_temperature == 23.0
        await coord.async_teardown()


# ── Device ID Resolution ──────────────────────────────────────────────


class TestDeviceIdResolution:
    """Tests for resolving device registry IDs to backend-specific identifiers."""

    @pytest.fixture
    async def mock_config_entry(self, hass):
        """Create a mock config entry for device registration."""
        entry = config_entries.ConfigEntry(
            data={},
            discovery_keys=MappingProxyType({}),
            domain="mqtt",
            minor_version=1,
            options={},
            source="test",
            subentries_data={},
            title="MQTT",
            unique_id="mqtt_test",
            version=1,
        )
        entry.hass = hass
        hass.config_entries._entries[entry.entry_id] = entry
        return entry

    async def test_unknown_id_falls_back(self, hass, mock_backend, subentry_data):
        """IDs not in device registry are returned unchanged (backwards compat)."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        result = coord._resolve_trv_id("trv_1")
        assert result == "trv_1"

    async def test_z2m_resolves_to_device_name(self, hass, mock_config_entry):
        """Z2M backend resolves device ID to device.name (Z2M friendly name)."""
        device_reg = dr.async_get(hass)

        device = device_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={("mqtt", "zigbee2mqtt_0x00158d0001234567")},
            name="Living Room TRV",
            manufacturer="Danfoss",
            model="Ally thermostat",
        )

        z2m_backend = MagicMock(spec=Z2MBackend)
        z2m_backend.register_state_callback = MagicMock(return_value=lambda: None)

        data = make_subentry_data(trv_ids=[device.id])
        coord = RoomCoordinator(hass, z2m_backend, data)

        result = coord._resolve_trv_id(device.id)
        assert result == "Living Room TRV"

    async def test_z2m_ignores_user_renamed_name(self, hass, mock_config_entry):
        """Z2M resolution uses device.name, not name_by_user."""
        device_reg = dr.async_get(hass)

        device = device_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={("mqtt", "zigbee2mqtt_0x00158d0001234567")},
            name="Living Room TRV",
            manufacturer="Danfoss",
            model="Ally thermostat",
        )
        device_reg.async_update_device(device.id, name_by_user="My Custom Name")

        z2m_backend = MagicMock(spec=Z2MBackend)
        z2m_backend.register_state_callback = MagicMock(return_value=lambda: None)

        data = make_subentry_data(trv_ids=[device.id])
        coord = RoomCoordinator(hass, z2m_backend, data)

        result = coord._resolve_trv_id(device.id)
        assert result == "Living Room TRV"

    async def test_setup_resolves_all_trv_ids(self, hass, mock_config_entry):
        """async_setup resolves all TRV device IDs before subscribing."""
        device_reg = dr.async_get(hass)

        dev1 = device_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={("mqtt", "zigbee2mqtt_0x001")},
            name="TRV One",
            manufacturer="Danfoss",
            model="Ally thermostat",
        )
        dev2 = device_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={("mqtt", "zigbee2mqtt_0x002")},
            name="TRV Two",
            manufacturer="Popp",
            model="Smart thermostat",
        )

        z2m_backend = MagicMock(spec=Z2MBackend)
        z2m_backend.register_state_callback = MagicMock(return_value=lambda: None)
        z2m_backend.async_subscribe_trv = AsyncMock()

        data = make_subentry_data(trv_ids=[dev1.id, dev2.id])
        coord = RoomCoordinator(hass, z2m_backend, data)
        await coord.async_setup()

        assert coord.trv_ids == ["TRV One", "TRV Two"]
        z2m_backend.async_subscribe_trv.assert_any_call("TRV One")
        z2m_backend.async_subscribe_trv.assert_any_call("TRV Two")
        await coord.async_teardown()

    async def test_backwards_compat_old_friendly_names(self, hass, mock_backend):
        """Old subentries storing friendly names directly still work."""
        data = make_subentry_data(trv_ids=["Living Room TRV", "Kitchen TRV"])
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        assert coord.trv_ids == ["Living Room TRV", "Kitchen TRV"]
        assert "Living Room TRV" in mock_backend._subscribed_trvs
        assert "Kitchen TRV" in mock_backend._subscribed_trvs
        await coord.async_teardown()


# ── Load Balancing ────────────────────────────────────────────────────


class TestLoadBalancing:
    """Tests for load balancing logic."""

    async def test_skips_single_trv_room(
        self, hass, mock_backend, single_trv_subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, single_trv_subentry_data)
        await coord.async_setup()

        # Load balance timer should NOT be scheduled
        assert coord._load_balance_timer is None
        await coord.async_teardown()

    async def test_schedules_timer_for_multi_trv(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        assert coord._load_balance_timer is not None
        await coord.async_teardown()

    async def test_run_load_balance_calculates_mean(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Simulate load estimates from both TRVs
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", load_estimate=100)
        )
        mock_backend.fire_state_update(
            "trv_2", make_trv_state("trv_2", load_estimate=200)
        )
        await hass.async_block_till_done()

        # Manually run load balance
        await coord._async_run_load_balance()

        assert mock_backend.async_set_load_room_mean.call_count == 2
        # Mean of 100, 200 = 150
        for call in mock_backend.async_set_load_room_mean.call_args_list:
            assert call[0][1] == 150
        assert coord.state.load_room_mean == 150
        await coord.async_teardown()

    async def test_discards_invalid_estimates(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", load_estimate=LOAD_BALANCE_DISABLED_VALUE)
        )
        mock_backend.fire_state_update(
            "trv_2", make_trv_state("trv_2", load_estimate=200)
        )
        await hass.async_block_till_done()

        await coord._async_run_load_balance()

        # Only trv_2's estimate should be used; mean = 200
        for call in mock_backend.async_set_load_room_mean.call_args_list:
            assert call[0][1] == 200
        await coord.async_teardown()

    async def test_discards_below_threshold(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", load_estimate=-600)
        )
        mock_backend.fire_state_update(
            "trv_2", make_trv_state("trv_2", load_estimate=200)
        )
        await hass.async_block_till_done()

        await coord._async_run_load_balance()
        # Only trv_2 valid
        for call in mock_backend.async_set_load_room_mean.call_args_list:
            assert call[0][1] == 200
        await coord.async_teardown()

    async def test_no_valid_estimates_skips(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # No state updates, so no estimates
        await coord._async_run_load_balance()
        assert mock_backend.async_set_load_room_mean.call_count == 0
        await coord.async_teardown()

    async def test_seeds_load_room_mean_from_trv(
        self, hass, mock_backend, subentry_data
    ):
        """load_room_mean is seeded from the TRV's raw payload immediately."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        assert coord.state.load_room_mean is None  # Not yet seeded

        # TRV reports load_room_mean in its raw MQTT payload
        trv_state = make_trv_state("trv_1", load_estimate=100)
        trv_state.raw = {"load_room_mean": 861}
        mock_backend.fire_state_update("trv_1", trv_state)
        await hass.async_block_till_done()

        assert coord.state.load_room_mean == 861
        await coord.async_teardown()

    async def test_seed_does_not_overwrite_calculated_mean(
        self, hass, mock_backend, subentry_data
    ):
        """Once load_room_mean is set, seeding from TRV raw should not overwrite."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Simulate a load balance cycle producing a value
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", load_estimate=100)
        )
        mock_backend.fire_state_update(
            "trv_2", make_trv_state("trv_2", load_estimate=200)
        )
        await hass.async_block_till_done()
        await coord._async_run_load_balance()
        assert coord.state.load_room_mean == 150

        # Now a TRV reports a different load_room_mean in raw
        trv_state = make_trv_state("trv_1", load_estimate=100)
        trv_state.raw = {"load_room_mean": 999}
        mock_backend.fire_state_update("trv_1", trv_state)
        await hass.async_block_till_done()

        # Should NOT have been overwritten
        assert coord.state.load_room_mean == 150
        await coord.async_teardown()

    async def test_seed_skipped_for_single_trv(
        self, hass, mock_backend, single_trv_subentry_data
    ):
        """Seeding load_room_mean is skipped for single-TRV rooms."""
        coord = RoomCoordinator(hass, mock_backend, single_trv_subentry_data)
        await coord.async_setup()

        trv_state = make_trv_state("trv_1", load_estimate=100)
        trv_state.raw = {"load_room_mean": 500}
        mock_backend.fire_state_update("trv_1", trv_state)
        await hass.async_block_till_done()

        assert coord.state.load_room_mean is None
        await coord.async_teardown()
