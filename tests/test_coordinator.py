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
    CONF_TRV_ENTITIES,
    LOAD_BALANCE_DISABLED_VALUE,
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

        assert mock_backend.async_set_external_temperature.call_count == 2  # 2 TRVs
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

        # Should send -80.0 (disabled) to each TRV
        assert mock_backend.async_set_external_temperature.call_count == 2
        for call in mock_backend.async_set_external_temperature.call_args_list:
            assert call[0][1] == -80.0  # EXTERNAL_TEMP_DISABLED / 100

    async def test_ext_temp_uses_trv_covered_state(self, hass, mock_backend):
        """radiator_covered from TRV state updates per-TRV ext temp tracking."""
        hass.states.async_set("sensor.temp", "21.5")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Initially all TRVs default to covered=False
        assert coord._ext_temp_trv["trv_1"].covered is False
        assert coord._ext_temp_trv["trv_2"].covered is False

        # Simulate TRV reporting radiator_covered=True
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", radiator_covered=True)
        )
        assert coord._ext_temp_trv["trv_1"].covered is True
        assert coord._ext_temp_trv["trv_2"].covered is False  # unchanged

        await coord.async_teardown()

    async def test_ext_temp_mixed_room_tracking(self, hass, mock_backend):
        """Mixed covered/exposed room: each TRV tracks independently."""
        hass.states.async_set("sensor.temp", "21.5")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Mark trv_1 as covered, trv_2 stays exposed
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", radiator_covered=True)
        )
        mock_backend.fire_state_update(
            "trv_2", make_trv_state("trv_2", radiator_covered=False)
        )

        # Both TRVs should have received the initial temp
        assert coord._ext_temp_trv["trv_1"].last_temp_sent == 21.5
        assert coord._ext_temp_trv["trv_2"].last_temp_sent == 21.5

        # Verify covered flags are independent
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
            # Max interval timer should be scheduled
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

        # Verify timers exist
        for ext_state in coord._ext_temp_trv.values():
            assert ext_state.timer is not None

        await coord.async_teardown()

        # After teardown, all timers should be cancelled
        for ext_state in coord._ext_temp_trv.values():
            assert ext_state.timer is None

    async def test_ext_temp_covered_change_at_runtime(self, hass, mock_backend):
        """TRV changing radiator_covered at runtime updates tracking."""
        hass.states.async_set("sensor.temp", "21.5")
        data = make_subentry_data(temp_sensor="sensor.temp")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Initially exposed
        assert coord._ext_temp_trv["trv_1"].covered is False

        # TRV reports covered=True
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", radiator_covered=True)
        )
        assert coord._ext_temp_trv["trv_1"].covered is True

        # TRV reports covered=False again
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


# ── Power Cycle Detection ─────────────────────────────────────────────


class TestPowerCycleDetection:
    """Tests for power-cycle detection and schedule recovery."""

    async def test_power_cycle_timer_scheduled(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        assert coord._power_cycle_timer is not None
        await coord.async_teardown()

    async def test_power_cycle_timer_cancelled_on_teardown(
        self, hass, mock_backend, subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        await coord.async_teardown()
        assert coord._power_cycle_timer is None

    async def test_power_cycle_e10_triggers_recovery(
        self, hass, mock_backend, subentry_data
    ):
        """E10 detected via poll triggers time sync and schedule recovery."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Program a schedule first
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        await coord.async_program_schedule(ws)

        mock_backend.async_clear_weekly_schedule.reset_mock()
        mock_backend.async_set_weekly_schedule.reset_mock()
        mock_backend.async_sync_time.reset_mock()

        # Simulate E10 + empty schedule on TRV
        mock_backend.async_read_sw_error_code.return_value = "invalid_clock_information"
        mock_backend.async_get_weekly_schedule.return_value = None

        await coord._async_check_power_cycle()

        # Should re-sync time for both TRVs
        assert mock_backend.async_sync_time.call_count == 2
        # Should clear and re-program for each TRV
        assert mock_backend.async_clear_weekly_schedule.call_count == 2
        assert mock_backend.async_set_weekly_schedule.call_count >= 2
        await coord.async_teardown()

    async def test_power_cycle_schedule_empty_triggers_recovery(
        self, hass, mock_backend, subentry_data
    ):
        """Empty schedule on TRV (without E10) triggers recovery."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Program a schedule first
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        await coord.async_program_schedule(ws)

        mock_backend.async_clear_weekly_schedule.reset_mock()
        mock_backend.async_set_weekly_schedule.reset_mock()

        # No E10, but schedule is empty on TRV
        mock_backend.async_read_sw_error_code.return_value = "ok"
        mock_backend.async_get_weekly_schedule.return_value = None

        await coord._async_check_power_cycle()

        # Should re-program for each TRV
        assert mock_backend.async_clear_weekly_schedule.call_count == 2
        assert mock_backend.async_set_weekly_schedule.call_count >= 2
        await coord.async_teardown()

    async def test_no_power_cycle_schedule_present(
        self, hass, mock_backend, subentry_data
    ):
        """No action when schedule is still present on TRV."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Program a schedule
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        await coord.async_program_schedule(ws)

        mock_backend.async_clear_weekly_schedule.reset_mock()
        mock_backend.async_set_weekly_schedule.reset_mock()
        mock_backend.async_sync_time.reset_mock()

        # No E10, schedule still present
        mock_backend.async_read_sw_error_code.return_value = "ok"
        mock_backend.async_get_weekly_schedule.return_value = [(360, 2100)]

        await coord._async_check_power_cycle()

        # Should not re-sync or re-program
        mock_backend.async_sync_time.assert_not_called()
        mock_backend.async_clear_weekly_schedule.assert_not_called()
        await coord.async_teardown()

    async def test_no_schedule_stored_skips_check(
        self, hass, mock_backend, subentry_data
    ):
        """No verification when no schedule is stored."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        mock_backend.async_sync_time.reset_mock()

        assert coord._current_schedule is None
        mock_backend.async_read_sw_error_code.return_value = "ok"

        await coord._async_check_power_cycle()

        # Should not call anything
        mock_backend.async_sync_time.assert_not_called()
        mock_backend.async_get_weekly_schedule.assert_not_called()
        await coord.async_teardown()

    async def test_device_announce_triggers_recovery(
        self, hass, mock_backend, subentry_data
    ):
        """Device announce fires recovery after delay."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Program a schedule
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        await coord.async_program_schedule(ws)

        mock_backend.async_clear_weekly_schedule.reset_mock()
        mock_backend.async_set_weekly_schedule.reset_mock()

        # Simulate empty schedule on TRV after rejoin
        mock_backend.async_get_weekly_schedule.return_value = None

        # Directly call the rejoin handler (skip the 5s delay for testing)
        trv_id = coord._trv_ids[0]
        await coord._async_handle_device_rejoin(trv_id)

        # Should re-program
        assert mock_backend.async_clear_weekly_schedule.call_count == 1
        assert mock_backend.async_set_weekly_schedule.call_count >= 1
        await coord.async_teardown()

    async def test_device_announce_no_recovery_if_schedule_present(
        self, hass, mock_backend, subentry_data
    ):
        """No recovery if schedule is still present after announce."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Program a schedule
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        await coord.async_program_schedule(ws)

        mock_backend.async_clear_weekly_schedule.reset_mock()
        mock_backend.async_set_weekly_schedule.reset_mock()

        # Schedule still present on TRV
        mock_backend.async_get_weekly_schedule.return_value = [(360, 2100)]

        trv_id = coord._trv_ids[0]
        await coord._async_handle_device_rejoin(trv_id)

        # Should NOT re-program
        mock_backend.async_clear_weekly_schedule.assert_not_called()
        mock_backend.async_set_weekly_schedule.assert_not_called()
        await coord.async_teardown()

    async def test_device_announce_callback_registered(
        self, hass, mock_backend, subentry_data
    ):
        """Announce callback is registered during setup."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Backend should have announce callbacks registered
        assert len(mock_backend._announce_callbacks) > 0
        await coord.async_teardown()

    async def test_rejoin_restores_load_balancing_enable(
        self, hass, mock_backend, subentry_data
    ):
        """Device rejoin re-writes load_balancing_enable if enabled."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_set_load_balancing_enable.reset_mock()
        mock_backend.async_get_weekly_schedule.return_value = None

        trv_id = coord._trv_ids[0]
        await coord._async_handle_device_rejoin(trv_id)

        # Should have re-written load_balancing_enable=true
        mock_backend.async_set_load_balancing_enable.assert_called_once_with(
            trv_id, True
        )
        await coord.async_teardown()

    async def test_reactive_e10_triggers_recovery(
        self, hass, mock_backend, subentry_data
    ):
        """E10 in pushed state data triggers power-cycle recovery."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Program a schedule so recovery has something to restore
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        await coord.async_program_schedule(ws)

        mock_backend.async_clear_weekly_schedule.reset_mock()
        mock_backend.async_set_weekly_schedule.reset_mock()

        # Simulate empty schedule on TRV after power cycle
        mock_backend.async_get_weekly_schedule.return_value = None

        # Push a state update with E10 flag
        trv_id = coord._trv_ids[0]
        state = make_trv_state(
            entity_id=trv_id,
            raw={"system_status_code": "invalid_clock_information"},
        )
        coord._handle_trv_state_update(trv_id, state)

        # Let the async task run
        await hass.async_block_till_done()

        # Should trigger recovery (schedule reprogram)
        mock_backend.async_clear_weekly_schedule.assert_called()
        assert mock_backend.async_set_weekly_schedule.call_count >= 1

        # Dedup guard should be cleared after recovery
        assert trv_id not in coord._recovering_trvs
        await coord.async_teardown()

    async def test_reactive_e10_dedup_prevents_double_recovery(
        self, hass, mock_backend, subentry_data
    ):
        """Second E10 push while recovery is running does not trigger again."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Program a schedule
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        await coord.async_program_schedule(ws)

        mock_backend.async_clear_weekly_schedule.reset_mock()
        mock_backend.async_get_weekly_schedule.return_value = None

        trv_id = coord._trv_ids[0]

        # Manually set the dedup guard (simulate recovery in progress)
        coord._recovering_trvs.add(trv_id)

        # Push a state update with E10 flag
        state = make_trv_state(
            entity_id=trv_id,
            raw={"system_status_code": "invalid_clock_information"},
        )
        coord._handle_trv_state_update(trv_id, state)
        await hass.async_block_till_done()

        # Should NOT trigger recovery because dedup guard is active
        mock_backend.async_clear_weekly_schedule.assert_not_called()

        # Clean up
        coord._recovering_trvs.discard(trv_id)
        await coord.async_teardown()

    async def test_reactive_e10_no_trigger_without_error(
        self, hass, mock_backend, subentry_data
    ):
        """Normal state push (no E10) does not trigger recovery."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        await coord.async_program_schedule(ws)

        mock_backend.async_clear_weekly_schedule.reset_mock()

        trv_id = coord._trv_ids[0]
        state = make_trv_state(
            entity_id=trv_id,
            raw={"system_status_code": "ok"},
        )
        coord._handle_trv_state_update(trv_id, state)
        await hass.async_block_till_done()

        # Should NOT trigger recovery
        mock_backend.async_clear_weekly_schedule.assert_not_called()
        assert trv_id not in coord._recovering_trvs
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

    async def test_setup_resolves_trv_ids_in_all_delegates(
        self, hass, mock_config_entry
    ):
        """async_setup propagates resolved TRV IDs to all delegates."""
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

        expected = ["TRV One", "TRV Two"]
        assert coord._schedule._trv_ids == expected
        assert coord._setpoint._trv_ids == expected
        assert coord._window._trv_ids == expected
        assert coord._preheat._trv_ids == expected
        assert coord._load_balance._trv_ids == expected
        assert coord._time_sync._trv_ids == expected
        assert coord._ext_temp._trv_ids == expected
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

    async def test_setup_does_not_mutate_original_data(self, hass, mock_config_entry):
        """Coordinator setup should not mutate the original subentry data dict."""
        device_reg = dr.async_get(hass)

        # Create devices with registry IDs
        dev1 = device_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={("mqtt", "zigbee2mqtt_0x001")},
            name="Living Room TRV",
            manufacturer="Danfoss",
            model="Ally thermostat",
        )
        dev2 = device_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={("mqtt", "zigbee2mqtt_0x002")},
            name="Kitchen TRV",
            manufacturer="Danfoss",
            model="Ally thermostat",
        )

        # Create subentry data with device registry IDs
        original_trv_ids = [dev1.id, dev2.id]
        data = make_subentry_data(trv_ids=original_trv_ids.copy())

        # Create coordinator and setup (which resolves IDs to friendly names)
        z2m_backend = MagicMock(spec=Z2MBackend)
        z2m_backend.register_state_callback = MagicMock(return_value=lambda: None)
        z2m_backend.async_subscribe_trv = AsyncMock()

        coord = RoomCoordinator(hass, z2m_backend, data)
        await coord.async_setup()

        # Verify the original data dict still has device registry IDs
        assert data[CONF_TRV_ENTITIES] == original_trv_ids
        assert data[CONF_TRV_ENTITIES][0] == dev1.id
        assert data[CONF_TRV_ENTITIES][1] == dev2.id

        # Verify coordinator has resolved friendly names
        assert coord.trv_ids == ["Living Room TRV", "Kitchen TRV"]

        await coord.async_teardown()


## ── Load Balancing ────────────────────────────────────────────────────


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

    async def test_seed_rejects_disabled_value(self, hass, mock_backend, subentry_data):
        """Seeding should reject -8000 (disabled sentinel)."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        trv_state = make_trv_state("trv_1", load_estimate=100)
        trv_state.raw = {"load_room_mean": -8000}
        mock_backend.fire_state_update("trv_1", trv_state)
        await hass.async_block_till_done()

        assert coord.state.load_room_mean is None
        await coord.async_teardown()

    async def test_seed_rejects_below_threshold(
        self, hass, mock_backend, subentry_data
    ):
        """Seeding should reject values below -500."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        trv_state = make_trv_state("trv_1", load_estimate=100)
        trv_state.raw = {"load_room_mean": -600}
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


# ── Window Open Coordination ──────────────────────────────────────────


class TestWindowCoordination:
    """Tests for window open coordination."""

    async def test_window_open_forces_others(self, hass, mock_backend, subentry_data):
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        state = make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        mock_backend.async_set_external_window_open.assert_called_once_with(
            "trv_2", True
        )
        assert "trv_2" in coord._forced_window_open_trvs
        await coord.async_teardown()

    async def test_no_window_coordination_single_trv(
        self, hass, mock_backend, single_trv_subentry_data
    ):
        coord = RoomCoordinator(hass, mock_backend, single_trv_subentry_data)
        await coord.async_setup()

        state = make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        mock_backend.async_set_external_window_open.assert_not_called()
        await coord.async_teardown()

    async def test_state4_does_not_trigger_forcing(
        self, hass, mock_backend, subentry_data
    ):
        """State 4 (external_open) should NOT force other TRVs."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        state = make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        # Should NOT force trv_2 — state 4 is not a local detection.
        # The orphan recovery will clear trv_1 itself (which is correct),
        # but trv_2 must never be forced.
        for call in mock_backend.async_set_external_window_open.call_args_list:
            assert call[0][0] != "trv_2" or call[0][1] is not True
        assert "trv_2" not in coord._forced_window_open_trvs
        await coord.async_teardown()

    async def test_orphan_state4_cleared_after_restart(
        self, hass, mock_backend, subentry_data
    ):
        """TRV in state 4 not in _forced_window_open_trvs gets cleared."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Simulate post-restart: _forced_window_open_trvs is empty,
        # but TRV reports state 4 (orphan from previous session)
        assert len(coord._forced_window_open_trvs) == 0

        state = make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        # Should clear the orphaned state
        mock_backend.async_set_external_window_open.assert_called_once_with(
            "trv_1", False
        )
        await coord.async_teardown()

    async def test_state4_tracked_trv_not_cleared(
        self, hass, mock_backend, subentry_data
    ):
        """TRV in state 4 that IS tracked should not be cleared as orphan."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Simulate: trv_1 detected window open, trv_2 was forced
        coord._forced_window_open_trvs.add("trv_1")

        state = make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        # Should NOT clear — it's tracked, not an orphan
        mock_backend.async_set_external_window_open.assert_not_called()
        await coord.async_teardown()

    async def test_deactivation_ignores_state4_in_still_open_check(
        self, hass, mock_backend, subentry_data
    ):
        """State 4 on non-forced TRVs should not block deactivation."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Set up: trv_2 was forced, and has confirmed state 4
        coord._forced_window_open_trvs.add("trv_2")
        coord.state.trv_states["trv_2"] = make_trv_state(
            "trv_2", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN
        )

        # trv_1 (the detector) now reports state 1 (closed)
        state = make_trv_state("trv_1", window_open_detection=1)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        # Should deactivate trv_2
        mock_backend.async_set_external_window_open.assert_called_once_with(
            "trv_2", False
        )
        assert len(coord._forced_window_open_trvs) == 0
        await coord.async_teardown()

    async def test_window_open_both_trvs_detect_no_deadlock(
        self, hass, mock_backend, subentry_data
    ):
        """Both TRVs independently detect window open — no deadlock.

        TRV_1 detects first (state 3), forcing TRV_2. Then TRV_2 also reports
        state 3. The guard must prevent TRV_2 from forcing TRV_1 back, which
        would deadlock (all TRVs in _forced, nobody can trigger deactivation).
        """
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # 1. TRV_1 detects window open (state 3) → forces TRV_2
        state = make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        assert "trv_2" in coord._forced_window_open_trvs
        assert "trv_1" not in coord._forced_window_open_trvs
        mock_backend.async_set_external_window_open.assert_called_once_with(
            "trv_2", True
        )
        mock_backend.async_set_external_window_open.reset_mock()

        # 2. TRV_2 also reports state 3 — must NOT force TRV_1 back
        state = make_trv_state("trv_2", window_open_detection=WINDOW_OPEN_DETECTED)
        mock_backend.fire_state_update("trv_2", state)
        await hass.async_block_till_done()

        assert "trv_1" not in coord._forced_window_open_trvs
        mock_backend.async_set_external_window_open.assert_not_called()

        # 3. TRV_2 acknowledges external open (state 4) — tracked, no orphan clear
        coord.state.trv_states["trv_2"] = make_trv_state(
            "trv_2", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN
        )
        state = make_trv_state("trv_2", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN)
        mock_backend.fire_state_update("trv_2", state)
        await hass.async_block_till_done()

        mock_backend.async_set_external_window_open.assert_not_called()

        # 4. Window closes — TRV_1 goes to state 1 → deactivation fires
        coord.state.trv_states["trv_1"] = make_trv_state(
            "trv_1", window_open_detection=1
        )
        state = make_trv_state("trv_1", window_open_detection=1)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        mock_backend.async_set_external_window_open.assert_called_once_with(
            "trv_2", False
        )
        assert len(coord._forced_window_open_trvs) == 0
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

        assert mock_backend.async_sync_time.call_count == 4
        mock_backend.async_sync_time.assert_any_call("trv_1")
        mock_backend.async_sync_time.assert_any_call("trv_2")
        await coord.async_teardown()

    async def test_sync_time_handles_exception(self, hass, mock_backend, subentry_data):
        """Exception on one TRV does not prevent syncing others."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_sync_time.side_effect = [Exception("fail"), None]
        await coord._async_sync_time_all()

        assert mock_backend.async_sync_time.call_count == 4
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

    async def test_set_programming_mode_pause_raises(
        self, hass, mock_backend, subentry_data
    ):
        """'pause' is no longer a valid option and should raise ValueError."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        with pytest.raises(ValueError, match="Invalid"):
            await coord.async_set_programming_mode_option("pause")

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

        await coord.async_teardown()

    async def test_initial_schedule_mode(self, hass, mock_backend, subentry_data):
        """Schedule mode should start as manual."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        assert coord.schedule_mode == SCHEDULE_MODE_MANUAL
        assert coord.schedule_mode_option == "manual"
        assert coord._current_schedule is None


# ── Remote Climate Sync ───────────────────────────────────────────────


class TestRemoteClimateSync:
    """Tests for bidirectional remote climate setpoint synchronization."""

    @pytest.fixture(autouse=True)
    async def _register_climate_service(self, hass):
        """Register a mock climate.set_temperature service for tests."""
        self.climate_service_calls: list[dict] = []

        async def mock_set_temperature(call):
            self.climate_service_calls.append(dict(call.data))

        hass.services.async_register("climate", "set_temperature", mock_set_temperature)

    async def test_remote_climate_change_syncs_to_trvs(self, hass, mock_backend):
        """When remote climate setpoint changes, it should sync to all TRVs."""
        # Set up remote climate entity (single-mode)
        hass.states.async_set("climate.remote", "heat", {"temperature": 22.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Establish initial room setpoint so the change detection works
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", occupied_heating_setpoint=20.0)
        )
        await hass.async_block_till_done()

        # Now change the remote climate setpoint
        mock_backend.async_set_occupied_heating_setpoint.reset_mock()
        hass.states.async_set("climate.remote", "heat", {"temperature": 23.0})
        await hass.async_block_till_done()

        # Should write 23.0 to both TRVs
        assert mock_backend.async_set_occupied_heating_setpoint.call_count == 2
        calls = mock_backend.async_set_occupied_heating_setpoint.call_args_list
        assert calls[0][0] == ("trv_1", 23.0)
        assert calls[1][0] == ("trv_2", 23.0)
        assert coord.state.target_temperature == 23.0
        await coord.async_teardown()

    async def test_remote_climate_dual_mode_uses_temp_low(self, hass, mock_backend):
        """Dual-mode remote climate should use target_temp_low (heating)."""
        hass.states.async_set(
            "climate.remote",
            "heat_cool",
            {"target_temp_low": 22.0, "target_temp_high": 26.0},
        )
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Set initial TRV state
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", occupied_heating_setpoint=20.0)
        )
        await hass.async_block_till_done()

        # Change remote climate target_temp_low
        mock_backend.async_set_occupied_heating_setpoint.reset_mock()
        hass.states.async_set(
            "climate.remote",
            "heat_cool",
            {"target_temp_low": 23.5, "target_temp_high": 26.0},
        )
        await hass.async_block_till_done()

        # Should write 23.5 to TRVs
        assert mock_backend.async_set_occupied_heating_setpoint.call_count == 2
        calls = mock_backend.async_set_occupied_heating_setpoint.call_args_list
        assert calls[0][0] == ("trv_1", 23.5)
        assert calls[1][0] == ("trv_2", 23.5)
        await coord.async_teardown()

    async def test_trv_manual_dial_syncs_to_remote_climate(self, hass, mock_backend):
        """Manual TRV dial change should sync to remote climate."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 20.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
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

        # Should have called climate.set_temperature on the remote
        assert len(self.climate_service_calls) == 1
        assert self.climate_service_calls[0]["entity_id"] == "climate.remote"
        assert self.climate_service_calls[0]["temperature"] == 22.0
        await coord.async_teardown()

    async def test_trv_manual_dial_syncs_remote_dual_mode(self, hass, mock_backend):
        """Manual TRV dial change should use target_temp_low for dual-mode remote."""
        hass.states.async_set(
            "climate.remote",
            "heat_cool",
            {"target_temp_low": 20.0, "target_temp_high": 26.0},
        )
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
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

        assert len(self.climate_service_calls) == 1
        assert self.climate_service_calls[0]["target_temp_low"] == 22.0
        assert self.climate_service_calls[0]["target_temp_high"] == 26.0
        await coord.async_teardown()

    async def test_set_room_temperature_syncs_to_remote(self, hass, mock_backend):
        """Setting room temperature via virtual climate should sync to remote."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 20.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        await coord.async_set_room_temperature(23.0)
        await hass.async_block_till_done()

        # TRVs should be written
        assert mock_backend.async_set_occupied_heating_setpoint.call_count == 2

        # Remote climate should also be synced
        assert len(self.climate_service_calls) == 1
        assert self.climate_service_calls[0]["entity_id"] == "climate.remote"
        assert self.climate_service_calls[0]["temperature"] == 23.0
        await coord.async_teardown()

    async def test_anti_echo_suppresses_remote_event(self, hass, mock_backend):
        """After syncing to remote, the resulting state event should be suppressed."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 20.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Set room temp (this syncs to remote and sets suppression window)
        await coord.async_set_room_temperature(23.0)
        await hass.async_block_till_done()

        # Now simulate the remote climate echoing back the same temp
        mock_backend.async_set_occupied_heating_setpoint.reset_mock()
        hass.states.async_set("climate.remote", "heat", {"temperature": 23.0})
        await hass.async_block_till_done()

        # Should NOT have written to TRVs again (suppressed by anti-echo)
        mock_backend.async_set_occupied_heating_setpoint.assert_not_called()
        await coord.async_teardown()

    async def test_no_sync_when_same_setpoint(self, hass, mock_backend):
        """Remote climate change matching current setpoint should be ignored."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 22.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        # Set up TRV state matching the remote climate
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", occupied_heating_setpoint=22.0)
        )
        await hass.async_block_till_done()
        mock_backend.async_set_occupied_heating_setpoint.reset_mock()

        # "Change" remote climate to same temperature
        hass.states.async_set("climate.remote", "heat", {"temperature": 22.0})
        await hass.async_block_till_done()

        # Should not write to TRVs
        mock_backend.async_set_occupied_heating_setpoint.assert_not_called()
        await coord.async_teardown()

    async def test_remote_unavailable_ignored(self, hass, mock_backend):
        """Remote climate going unavailable should be ignored."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 22.0})
        data = make_subentry_data(remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
        await coord.async_setup()

        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", occupied_heating_setpoint=22.0)
        )
        await hass.async_block_till_done()
        mock_backend.async_set_occupied_heating_setpoint.reset_mock()

        # Set remote climate to unavailable
        hass.states.async_set("climate.remote", STATE_UNAVAILABLE)
        await hass.async_block_till_done()

        # Should not write to TRVs
        mock_backend.async_set_occupied_heating_setpoint.assert_not_called()
        await coord.async_teardown()

    async def test_no_remote_climate_configured(
        self, hass, mock_backend, subentry_data
    ):
        """No remote climate configured should not cause issues."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # async_set_room_temperature should work without remote climate
        await coord.async_set_room_temperature(23.0)
        assert mock_backend.async_set_occupied_heating_setpoint.call_count == 2

        # No climate service calls should have been made
        assert len(self.climate_service_calls) == 0
        await coord.async_teardown()

    async def test_single_trv_manual_dial_syncs_to_remote(self, hass, mock_backend):
        """Single-TRV room: manual dial change should still sync to remote climate."""
        hass.states.async_set("climate.remote", "heat", {"temperature": 20.0})
        data = make_subentry_data(trv_ids=["trv_1"], remote_climate="climate.remote")
        coord = RoomCoordinator(hass, mock_backend, data)
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

        # Should NOT forward to other TRVs (there are none)
        mock_backend.async_send_setpoint_command.assert_not_called()

        # But SHOULD sync to remote climate
        assert len(self.climate_service_calls) == 1
        assert self.climate_service_calls[0]["entity_id"] == "climate.remote"
        assert self.climate_service_calls[0]["temperature"] == 22.0

        # Room state should still be updated
        assert coord.state.target_temperature == 22.0
        await coord.async_teardown()

    async def test_extract_setpoint_single_mode(self, hass):
        """_extract_remote_climate_setpoint with single-mode climate."""
        state = MagicMock()
        state.attributes = {"temperature": 22.5}
        result = RoomCoordinator._extract_remote_climate_setpoint(state)
        assert result == 22.5

    async def test_extract_setpoint_dual_mode(self, hass):
        """_extract_remote_climate_setpoint with dual-mode climate."""
        state = MagicMock()
        state.attributes = {"target_temp_low": 21.0, "target_temp_high": 25.0}
        result = RoomCoordinator._extract_remote_climate_setpoint(state)
        assert result == 21.0

    async def test_extract_setpoint_dual_mode_prefers_temp_low(self, hass):
        """Dual-mode should use target_temp_low even if temperature is also set."""
        state = MagicMock()
        state.attributes = {
            "target_temp_low": 21.0,
            "target_temp_high": 25.0,
            "temperature": 23.0,
        }
        result = RoomCoordinator._extract_remote_climate_setpoint(state)
        assert result == 21.0

    async def test_extract_setpoint_no_temp(self, hass):
        """_extract_remote_climate_setpoint returns None when no temp attributes."""
        state = MagicMock()
        state.attributes = {}
        result = RoomCoordinator._extract_remote_climate_setpoint(state)
        assert result is None


# ── Window Delegate Exception Handling ────────────────────────────────


class TestWindowExceptionHandling:
    """Tests for exception handler branches in WindowDelegate."""

    async def test_window_state_none_early_return(
        self, hass, mock_backend, subentry_data
    ):
        """window_open_detection=None should cause early return (no coordination)."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        state = make_trv_state("trv_1", window_open_detection=None)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        mock_backend.async_set_external_window_open.assert_not_called()
        await coord.async_teardown()

    async def test_force_open_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception in async_set_external_window_open(True) is caught and logged."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_set_external_window_open.side_effect = RuntimeError(
            "test error"
        )

        state = make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_DETECTED)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        assert "Failed to set external_window_open on trv_2" in caplog.text
        # TRV should NOT have been added to forced set since the call failed
        assert "trv_2" not in coord._forced_window_open_trvs
        await coord.async_teardown()

    async def test_orphan_clear_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception clearing orphaned external_window_open is caught and logged."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_set_external_window_open.side_effect = RuntimeError(
            "test error"
        )

        # TRV reports state 4 but is not tracked -> orphan
        assert len(coord._forced_window_open_trvs) == 0
        state = make_trv_state("trv_1", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        assert "Failed to clear orphaned external_window_open on trv_1" in caplog.text
        await coord.async_teardown()

    async def test_deactivate_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception during deactivation (clearing forced TRVs) is caught and logged."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Force trv_2 via window detection on trv_1
        coord._forced_window_open_trvs.add("trv_2")
        coord.state.trv_states["trv_2"] = make_trv_state(
            "trv_2", window_open_detection=WINDOW_OPEN_EXTERNAL_OPEN
        )

        # Now set up the exception for the deactivation call
        mock_backend.async_set_external_window_open.side_effect = RuntimeError(
            "test error"
        )

        # trv_1 reports window closed -> deactivation fires
        state = make_trv_state("trv_1", window_open_detection=1)
        mock_backend.fire_state_update("trv_1", state)
        await hass.async_block_till_done()

        assert "Failed to clear external_window_open on trv_2" in caplog.text
        # forced_trvs should still be cleared after the loop
        assert len(coord._forced_window_open_trvs) == 0
        await coord.async_teardown()


# ── Preheat Delegate Exception Handling ───────────────────────────────


class TestPreheatExceptionHandling:
    """Tests for exception handler branches in PreheatDelegate."""

    async def test_preheat_forward_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception in async_send_preheat_command is caught and logged."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_send_preheat_command.side_effect = RuntimeError("test error")

        mock_backend.fire_state_update(
            "trv_1",
            make_trv_state("trv_1", preheat_status=True, preheat_time=1234),
        )
        await hass.async_block_till_done()

        assert "Failed to forward preheat to TRV trv_2" in caplog.text
        await coord.async_teardown()


# ── Load Balancer Exception Handling ──────────────────────────────────


class TestLoadBalancerExceptionHandling:
    """Tests for exception handler branches and early returns in LoadBalanceDelegate."""

    async def test_async_run_early_return_single_trv(
        self, hass, mock_backend, single_trv_subentry_data
    ):
        """_async_run() returns None early when only one TRV."""
        coord = RoomCoordinator(hass, mock_backend, single_trv_subentry_data)
        await coord.async_setup()

        result = await coord._load_balance._async_run()
        assert result is None
        mock_backend.async_set_load_room_mean.assert_not_called()
        await coord.async_teardown()

    async def test_async_enable_early_return_single_trv(
        self, hass, mock_backend, single_trv_subentry_data
    ):
        """async_enable() returns early when only one TRV."""
        coord = RoomCoordinator(hass, mock_backend, single_trv_subentry_data)
        await coord.async_setup()

        await coord._load_balance.async_enable()
        mock_backend.async_set_load_balancing_enable.assert_not_called()
        await coord.async_teardown()

    async def test_async_run_set_load_room_mean_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception in async_set_load_room_mean during _async_run is caught."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Populate valid load estimates
        mock_backend.fire_state_update(
            "trv_1", make_trv_state("trv_1", load_estimate=100)
        )
        mock_backend.fire_state_update(
            "trv_2", make_trv_state("trv_2", load_estimate=200)
        )
        await hass.async_block_till_done()

        mock_backend.async_set_load_room_mean.side_effect = RuntimeError("test error")

        result = await coord._load_balance._async_run()
        # Should still return the computed mean
        assert result == 150
        assert "Failed to set load_room_mean on TRV" in caplog.text
        await coord.async_teardown()

    async def test_async_enable_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception in async_set_load_balancing_enable during enable is caught."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        # Disable first so we can re-enable
        await coord.async_disable_load_balancing()
        mock_backend.async_set_load_balancing_enable.reset_mock()

        mock_backend.async_set_load_balancing_enable.side_effect = RuntimeError(
            "test error"
        )

        # Should not raise despite the exception
        await coord._load_balance.async_enable()
        assert "Failed to set load_balancing_enable on" in caplog.text
        await coord.async_teardown()

    async def test_async_disable_set_load_room_mean_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception in async_set_load_room_mean during disable is caught."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_set_load_room_mean.side_effect = RuntimeError("test error")

        await coord.async_disable_load_balancing()
        assert "Failed to send disabled load_room_mean to" in caplog.text
        await coord.async_teardown()

    async def test_async_disable_set_load_balancing_enable_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception in async_set_load_balancing_enable(False) during disable is caught."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_set_load_balancing_enable.side_effect = RuntimeError(
            "test error"
        )

        await coord.async_disable_load_balancing()
        assert "Failed to set load_balancing_enable on" in caplog.text
        await coord.async_teardown()

    async def test_async_setup_trvs_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception in async_set_load_balancing_enable during setup_trvs is caught."""
        mock_backend.async_set_load_balancing_enable.side_effect = RuntimeError(
            "test error"
        )

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        # async_setup calls async_setup_trvs internally
        await coord.async_setup()

        assert "Failed to set load_balancing_enable on" in caplog.text
        await coord.async_teardown()


# ── Setpoint Delegate Exception Handling ──────────────────────────────


class TestSetpointExceptionHandling:
    """Tests for exception handler branches in SetpointDelegate."""

    async def test_is_programmatic_property(self, hass, mock_backend, subentry_data):
        """is_programmatic property returns False by default."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        assert coord._setpoint.is_programmatic is False
        await coord.async_teardown()

    async def test_forward_setpoint_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception in async_send_setpoint_command is caught and logged."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_send_setpoint_command.side_effect = RuntimeError(
            "test error"
        )

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

        assert "Failed to forward setpoint to TRV trv_2" in caplog.text
        # _programmatic should be reset to False after the exception
        assert coord._setpoint.is_programmatic is False
        await coord.async_teardown()

    async def test_set_room_temperature_exception_logged(
        self, hass, mock_backend, subentry_data, caplog
    ):
        """Exception in async_set_occupied_heating_setpoint is caught and logged."""
        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()

        mock_backend.async_set_occupied_heating_setpoint.side_effect = RuntimeError(
            "test error"
        )

        await coord.async_set_room_temperature(23.0)

        assert "Failed to set setpoint on TRV" in caplog.text
        # _programmatic should be reset to False after the exception
        assert coord._setpoint.is_programmatic is False
        await coord.async_teardown()
