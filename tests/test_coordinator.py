"""Tests for the RoomCoordinator."""

from __future__ import annotations

import time
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
    SCHEDULE_MODE_ECO,
    SCHEDULE_MODE_MANUAL,
    SCHEDULE_MODE_SCHEDULE,
    SCHEDULE_MODE_SCHEDULE_PREHEAT,
    SETPOINT_SOURCE_MANUAL,
    SETPOINT_TYPE_USER,
    WINDOW_OPEN_DETECTED,
    WINDOW_OPEN_EXTERNAL_OPEN,
)
from custom_components.danfoss_ally_gateway.coordinator import (
    RoomCoordinator,
    RoomState,
)
from custom_components.danfoss_ally_gateway.schedule import (
    DaySchedule,
    ScheduleEvent,
    WeeklySchedule,
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

    async def test_enable_load_balancing(self, hass, mock_backend, subentry_data):
        """async_enable_load_balancing writes to all TRVs and starts timer."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Disable first, then re-enable
        await coord.async_disable_load_balancing()
        assert coord._load_balance_timer is None
        assert coord.state.load_balancing_enabled is False

        mock_backend.async_set_load_balancing_enable.reset_mock()
        await coord.async_enable_load_balancing()
        assert coord.state.load_balancing_enabled is True
        assert coord._load_balance_timer is not None
        assert mock_backend.async_set_load_balancing_enable.call_count == 2
        for call in mock_backend.async_set_load_balancing_enable.call_args_list:
            assert call[0][1] is True
        await coord.async_teardown()

    async def test_disable_load_balancing(self, hass, mock_backend, subentry_data):
        """async_disable_load_balancing sends -8000 and stops timer."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        await coord.async_disable_load_balancing()

        assert coord.state.load_balancing_enabled is False
        assert coord.state.load_room_mean is None
        assert coord._load_balance_timer is None
        # Should have sent -8000 to both TRVs
        assert mock_backend.async_set_load_room_mean.call_count == 2
        for call in mock_backend.async_set_load_room_mean.call_args_list:
            assert call[0][1] == LOAD_BALANCE_DISABLED_VALUE
        await coord.async_teardown()

    async def test_setup_writes_load_balancing_enable_to_trvs(
        self, hass, mock_backend, subentry_data
    ):
        """On setup, multi-TRV rooms write load_balancing_enable=true to all TRVs."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        assert mock_backend.async_set_load_balancing_enable.call_count == 2
        calls = mock_backend.async_set_load_balancing_enable.call_args_list
        trv_ids_called = {c[0][0] for c in calls}
        assert trv_ids_called == {"trv_1", "trv_2"}
        for call in calls:
            assert call[0][1] is True
        assert coord.state.load_balancing_enabled is True
        await coord.async_teardown()

    async def test_single_trv_no_load_balancing_enabled(
        self, hass, mock_backend, single_trv_subentry_data
    ):
        """Single-TRV rooms should not enable load balancing."""
        coord = RoomCoordinator(hass, mock_backend, single_trv_subentry_data)
        await coord.async_setup()

        mock_backend.async_set_load_balancing_enable.assert_not_called()
        assert coord.state.load_balancing_enabled is False
        await coord.async_teardown()


# ── Window Coordination ──────────────────────────────────────────────


class TestWindowCoordination:
    """Tests for window open coordination across room TRVs."""

    async def test_window_detected_forces_other_trvs(
        self, hass, mock_backend, subentry_data
    ):
        """When one TRV detects window open, force external_window_open on others."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED),
        )
        await hass.async_block_till_done()

        mock_backend.async_set_external_window_open.assert_called_once_with(
            "trv_2", True
        )
        assert "trv_2" in coord._forced_window_open_trvs
        await coord.async_teardown()

    async def test_skips_single_trv_room(
        self, hass, mock_backend, single_trv_subentry_data
    ):
        """Window coordination is skipped for single-TRV rooms."""
        coord = RoomCoordinator(hass, mock_backend, single_trv_subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED),
        )
        await hass.async_block_till_done()

        mock_backend.async_set_external_window_open.assert_not_called()
        await coord.async_teardown()

    async def test_no_duplicate_forcing(self, hass, mock_backend, subentry_data):
        """Already-forced TRVs are not forced again."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # First detection
        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED),
        )
        await hass.async_block_till_done()
        assert mock_backend.async_set_external_window_open.call_count == 1

        # Second detection from same TRV - should not force again
        mock_backend.async_set_external_window_open.reset_mock()
        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED),
        )
        await hass.async_block_till_done()
        mock_backend.async_set_external_window_open.assert_not_called()
        await coord.async_teardown()

    async def test_deactivate_when_window_closed(
        self, hass, mock_backend, subentry_data
    ):
        """Forced TRVs are deactivated when detecting TRV reports window closed."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # TRV1 detects window open → forces TRV2
        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED),
        )
        await hass.async_block_till_done()
        assert "trv_2" in coord._forced_window_open_trvs

        # TRV2 confirms external open (state 4)
        mock_backend.fire_state_update(
            "trv_2",
            make_trv_state("trv_2", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN),
        )
        await hass.async_block_till_done()

        # TRV1 reports window closed (state 0)
        mock_backend.async_set_external_window_open.reset_mock()
        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", window_open_detection=0),
        )
        await hass.async_block_till_done()

        mock_backend.async_set_external_window_open.assert_called_once_with(
            "trv_2", False
        )
        assert len(coord._forced_window_open_trvs) == 0
        await coord.async_teardown()

    async def test_no_deactivate_while_still_open(
        self, hass, mock_backend, subentry_data
    ):
        """Don't deactivate if detecting TRV still reports window open."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # TRV1 detects window open
        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED),
        )
        await hass.async_block_till_done()

        # TRV2 confirms external open
        mock_backend.fire_state_update(
            "trv_2",
            make_trv_state("trv_2", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN),
        )
        await hass.async_block_till_done()

        # TRV2 reports state 4 but TRV1 is still open (>= 3)
        # The deactivation check happens when a TRV reports state < 3
        # Since TRV1 never went below 3, forced TRVs remain
        assert "trv_2" in coord._forced_window_open_trvs
        await coord.async_teardown()

    async def test_window_none_state_ignored(self, hass, mock_backend, subentry_data):
        """TRV with window_open_detection=None is ignored."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", window_open_detection=None),
        )
        await hass.async_block_till_done()

        mock_backend.async_set_external_window_open.assert_not_called()
        await coord.async_teardown()


# ── Preheat Coordination ─────────────────────────────────────────────


class TestPreheatCoordination:
    """Tests for preheat coordination across room TRVs."""

    async def test_preheat_forwards_to_other_trvs(
        self, hass, mock_backend, subentry_data
    ):
        """Preheat event on one TRV is forwarded to others."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", preheat_status=True, preheat_time=1234),
        )
        await hass.async_block_till_done()

        mock_backend.async_send_preheat_command.assert_called_once_with("trv_2", 1234)
        await coord.async_teardown()

    async def test_skips_single_trv_room(
        self, hass, mock_backend, single_trv_subentry_data
    ):
        """Preheat coordination is skipped for single-TRV rooms."""
        coord = RoomCoordinator(hass, mock_backend, single_trv_subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", preheat_status=True, preheat_time=1234),
        )
        await hass.async_block_till_done()

        mock_backend.async_send_preheat_command.assert_not_called()
        await coord.async_teardown()

    async def test_deduplicates_same_preheat_time(
        self, hass, mock_backend, subentry_data
    ):
        """Same preheat_time from same TRV is not forwarded twice."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", preheat_status=True, preheat_time=1234),
        )
        await hass.async_block_till_done()
        assert mock_backend.async_send_preheat_command.call_count == 1

        # Same preheat_time again
        mock_backend.async_send_preheat_command.reset_mock()
        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", preheat_status=True, preheat_time=1234),
        )
        await hass.async_block_till_done()
        mock_backend.async_send_preheat_command.assert_not_called()
        await coord.async_teardown()

    async def test_different_preheat_time_is_forwarded(
        self, hass, mock_backend, subentry_data
    ):
        """Different preheat_time from same TRV is forwarded."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", preheat_status=True, preheat_time=1234),
        )
        await hass.async_block_till_done()

        mock_backend.async_send_preheat_command.reset_mock()
        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", preheat_status=True, preheat_time=5678),
        )
        await hass.async_block_till_done()

        mock_backend.async_send_preheat_command.assert_called_once_with("trv_2", 5678)
        await coord.async_teardown()

    async def test_no_preheat_when_status_false(
        self, hass, mock_backend, subentry_data
    ):
        """No forwarding when preheat_status is False."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", preheat_status=False, preheat_time=1234),
        )
        await hass.async_block_till_done()

        mock_backend.async_send_preheat_command.assert_not_called()
        await coord.async_teardown()

    async def test_no_preheat_when_time_none(self, hass, mock_backend, subentry_data):
        """No forwarding when preheat_time is None."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", preheat_status=True, preheat_time=None),
        )
        await hass.async_block_till_done()

        mock_backend.async_send_preheat_command.assert_not_called()
        await coord.async_teardown()


# ── Remote Climate Sync ──────────────────────────────────────────────


class TestRemoteClimateSync:
    """Tests for remote climate entity synchronization."""

    async def test_remote_climate_change_syncs_to_trvs(self, hass, mock_backend):
        """When remote climate entity changes setpoint, it syncs to TRVs."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 22.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Change the remote climate setpoint
        hass.states.async_set("climate.remote", "heat", {"temperature": 24.0})
        await hass.async_block_till_done()

        assert mock_backend.async_set_occupied_heating_setpoint.call_count == 2
        calls = mock_backend.async_set_occupied_heating_setpoint.call_args_list
        assert calls[0][0] == ("trv_1", 24.0)
        assert calls[1][0] == ("trv_2", 24.0)
        await coord.async_teardown()

    async def test_remote_climate_no_sync_when_not_configured(
        self, hass, mock_backend, subentry_data
    ):
        """No remote climate configured means no calls to set_temperature service."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # No remote climate, so no setpoint sync should happen
        mock_backend.async_set_occupied_heating_setpoint.assert_not_called()
        await coord.async_teardown()

    async def test_anti_echo_suppression(self, hass, mock_backend):
        """After async_set_room_temperature, remote climate changes within suppression window are ignored."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 22.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Simulate that we just synced to remote, so suppression is active
        coord._remote_setpoint_suppress_until = time.monotonic() + 10

        # Change the remote climate setpoint during suppression window
        hass.states.async_set("climate.remote", "heat", {"temperature": 23.0})
        await hass.async_block_till_done()

        # Should NOT have synced to TRVs because suppression is active
        mock_backend.async_set_occupied_heating_setpoint.assert_not_called()
        await coord.async_teardown()

    async def test_remote_climate_dual_mode(self, hass, mock_backend):
        """Remote climate with target_temp_low/target_temp_high extracts from target_temp_low."""
        hass.states.async_set(
            "climate.remote",
            "heat",
            {"target_temp_low": 21.0, "target_temp_high": 25.0},
        )
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Change the remote climate with dual-mode temps
        hass.states.async_set(
            "climate.remote",
            "heat",
            {"target_temp_low": 23.0, "target_temp_high": 27.0},
        )
        await hass.async_block_till_done()

        assert mock_backend.async_set_occupied_heating_setpoint.call_count == 2
        calls = mock_backend.async_set_occupied_heating_setpoint.call_args_list
        assert calls[0][0] == ("trv_1", 23.0)
        assert calls[1][0] == ("trv_2", 23.0)
        await coord.async_teardown()

    async def test_remote_climate_ignores_same_setpoint(self, hass, mock_backend):
        """If remote climate reports same setpoint as current room setpoint, no sync happens."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 22.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Set the room target temperature to 22.0 via TRV state
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", occupied_heating_setpoint=22.0)
        )
        await hass.async_block_till_done()
        mock_backend.async_set_occupied_heating_setpoint.reset_mock()

        # Remote climate reports same setpoint
        hass.states.async_set("climate.remote", "heat", {"temperature": 22.0})
        await hass.async_block_till_done()

        mock_backend.async_set_occupied_heating_setpoint.assert_not_called()
        await coord.async_teardown()

    async def test_set_room_temp_syncs_to_remote(self, hass, mock_backend):
        """Calling async_set_room_temperature also calls climate.set_temperature on the remote."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 22.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Register a dummy climate.set_temperature service
        service_calls = []

        async def mock_set_temp(call):
            service_calls.append(call)

        hass.services.async_register("climate", "set_temperature", mock_set_temp)

        await coord.async_set_room_temperature(25.0)

        # Verify suppression timestamp was set (proves sync to remote was attempted)
        assert coord._remote_setpoint_suppress_until > time.monotonic()

        # Verify the service was called
        assert len(service_calls) == 1
        assert service_calls[0].data["entity_id"] == "climate.remote"
        assert service_calls[0].data["temperature"] == 25.0
        await coord.async_teardown()

    async def test_manual_dial_syncs_to_remote(self, hass, mock_backend):
        """Manual setpoint change on TRV syncs to remote climate."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 20.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Register a dummy climate.set_temperature service
        service_calls = []

        async def mock_set_temp(call):
            service_calls.append(call)

        hass.services.async_register("climate", "set_temperature", mock_set_temp)

        # Initial state
        old = make_trv_state("trv_1", occupied_heating_setpoint=20.0)
        mock_backend.fire_state_update("trv_1", old)
        await hass.async_block_till_done()

        # Manual dial change on TRV
        new = make_trv_state(
            "trv_1",
            occupied_heating_setpoint=22.0,
            setpoint_change_source=SETPOINT_SOURCE_MANUAL,
        )
        mock_backend.fire_state_update("trv_1", new)
        await hass.async_block_till_done()

        # Verify suppression timestamp was set (proves _async_sync_remote_climate was called)
        assert coord._remote_setpoint_suppress_until > time.monotonic()

        # Verify the service was called to sync to remote
        assert len(service_calls) == 1
        assert service_calls[0].data["entity_id"] == "climate.remote"
        assert service_calls[0].data["temperature"] == 22.0
        await coord.async_teardown()


# ── Time Sync ─────────────────────────────────────────────────────────


class TestTimeSync:
    """Tests for weekly time synchronization."""

    async def test_time_sync_timer_scheduled(self, hass, mock_backend, subentry_data):
        """Time sync timer is scheduled on setup."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        assert coord._time_sync_timer is not None
        await coord.async_teardown()

    async def test_time_sync_timer_cancelled_on_teardown(
        self, hass, mock_backend, subentry_data
    ):
        """Time sync timer is cancelled on teardown."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        assert coord._time_sync_timer is not None

        await coord.async_teardown()
        assert coord._time_sync_timer is None

    async def test_sync_time_all_calls_backend(self, hass, mock_backend, subentry_data):
        """_async_sync_time_all calls async_sync_time for each TRV."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        await coord._async_sync_time_all()

        assert mock_backend.async_sync_time.call_count == 2
        mock_backend.async_sync_time.assert_any_call("trv_1")
        mock_backend.async_sync_time.assert_any_call("trv_2")
        await coord.async_teardown()

    async def test_sync_time_handles_exception(self, hass, mock_backend, subentry_data):
        """Exception on one TRV does not prevent syncing others."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_sync_time.side_effect = [Exception("fail"), None]
        await coord._async_sync_time_all()

        assert mock_backend.async_sync_time.call_count == 2
        await coord.async_teardown()


# ── Schedule Programming ──────────────────────────────────────────────


class TestScheduleProgramming:
    """Tests for schedule programming and mode control."""

    async def test_program_schedule(self, hass, mock_backend, subentry_data):
        """Programming a schedule should clear, send, and store."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1320, 18.0),
            ]
        )

        await coord.async_program_schedule(ws)

        # Should have cleared schedule on both TRVs
        assert mock_backend.async_clear_weekly_schedule.call_count == 2

        # Should have sent schedule to both TRVs
        assert mock_backend.async_set_weekly_schedule.call_count >= 2

        # Should have stored the schedule
        assert coord._current_schedule is not None
        assert coord._current_schedule.total_events == 2

        await coord.async_teardown()

    async def test_program_invalid_schedule_raises(
        self, hass, mock_backend, subentry_data
    ):
        """Invalid schedule should raise ValueError."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        ws = WeeklySchedule()
        # Add too many events to one day
        ws.days[0] = DaySchedule(
            events=[ScheduleEvent(i * 100, 20.0) for i in range(7)]
        )

        with pytest.raises(ValueError, match="Invalid schedule"):
            await coord.async_program_schedule(ws)

        await coord.async_teardown()

    async def test_clear_schedule(self, hass, mock_backend, subentry_data):
        """Clearing schedule should clear TRVs and set manual mode."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # First program a schedule
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        await coord.async_program_schedule(ws)
        assert coord._current_schedule is not None

        # Clear it
        mock_backend.async_clear_weekly_schedule.reset_mock()
        await coord.async_clear_schedule()

        assert mock_backend.async_clear_weekly_schedule.call_count == 2
        assert coord._current_schedule is None
        assert coord.schedule_mode == SCHEDULE_MODE_MANUAL

        await coord.async_teardown()

    async def test_set_programming_mode_schedule(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        await coord.async_set_programming_mode_option("schedule")
        assert coord.schedule_mode == SCHEDULE_MODE_SCHEDULE
        assert coord.schedule_mode_option == "schedule"
        assert mock_backend.async_set_programming_mode.call_count == 2

        await coord.async_teardown()

    async def test_set_programming_mode_preheat(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        await coord.async_set_programming_mode_option("schedule_with_preheat")
        assert coord.schedule_mode == SCHEDULE_MODE_SCHEDULE_PREHEAT

        await coord.async_teardown()

    async def test_set_programming_mode_pause(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        await coord.async_set_programming_mode_option("pause")
        assert coord.schedule_mode == SCHEDULE_MODE_ECO

        await coord.async_teardown()

    async def test_set_programming_mode_invalid(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        with pytest.raises(ValueError, match="Invalid"):
            await coord.async_set_programming_mode_option("invalid_mode")

        await coord.async_teardown()

    async def test_set_schedule_mode_helper(self, hass, mock_backend, subentry_data):
        """async_set_schedule_mode should map enabled/preheat/eco to mode values."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        await coord.async_set_schedule_mode(enabled=True)
        assert coord.schedule_mode == SCHEDULE_MODE_SCHEDULE

        await coord.async_set_schedule_mode(enabled=True, preheat=True)
        assert coord.schedule_mode == SCHEDULE_MODE_SCHEDULE_PREHEAT

        await coord.async_set_schedule_mode(enabled=False)
        assert coord.schedule_mode == SCHEDULE_MODE_MANUAL

        await coord.async_set_schedule_mode(enabled=True, eco=True)
        assert coord.schedule_mode == SCHEDULE_MODE_ECO

        await coord.async_teardown()

    async def test_initial_schedule_mode(self, hass, mock_backend, subentry_data):
        """Schedule mode should start as manual."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        assert coord.schedule_mode == SCHEDULE_MODE_MANUAL
        assert coord.schedule_mode_option == "manual"
        assert coord._current_schedule is None
