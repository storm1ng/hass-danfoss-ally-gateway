"""Tests for the ZHA backend."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE

from custom_components.danfoss_ally_gateway.backend import TRVState
from custom_components.danfoss_ally_gateway.backend.zha import ZHABackend
from custom_components.danfoss_ally_gateway.const import (
    ATTR_EXTERNAL_MEASURED_ROOM_SENSOR,
    ATTR_EXTERNAL_WINDOW_OPEN,
    ATTR_HEAT_AVAILABLE,
    ATTR_LOAD_BALANCING_ENABLE,
    ATTR_LOAD_ROOM_MEAN,
    ATTR_OCCUPIED_HEATING_SETPOINT,
    ATTR_THERMOSTAT_PROGRAMMING_MODE,
    CLUSTER_THERMOSTAT,
    CLUSTER_TIME,
    CMD_PREHEAT_COMMAND,
    DANFOSS_MANUFACTURER_CODE,
    EXTERNAL_TEMP_DISABLED,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _make_state(
    state: str = "heat",
    attributes: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock HA State object."""
    mock = MagicMock()
    mock.state = state
    mock.attributes = attributes or {}
    return mock


def _mock_entity_registry_entry(unique_id: str = "00:11:22:33:44:55:66:77-1"):
    """Create a mock entity registry entry."""
    entry = MagicMock()
    entry.unique_id = unique_id
    return entry


# ── State Parsing ─────────────────────────────────────────────────────


class TestZHAStateParsing:
    """Tests for ZHA state parsing."""

    def test_parse_full_state(self):
        attrs = {
            "current_temperature": 21.0,
            "temperature": 22.0,
            "pi_heating_demand": 40,
            "load_estimate": 80,
            "load_balancing_enable": True,
            "heat_available": True,
            "heat_required": True,
            "preheat_status": False,
            "preheat_time": None,
            "window_open_internal": 0,
            "window_open_external": False,
            "setpoint_change_source": 1,
            "radiator_covered": False,
        }
        state = _make_state(attributes=attrs)
        trv_state = ZHABackend._parse_zha_state("climate.trv1", state)

        assert trv_state.entity_id == "climate.trv1"
        assert trv_state.local_temperature == 21.0
        assert trv_state.occupied_heating_setpoint == 22.0
        assert trv_state.pi_heating_demand == 40
        assert trv_state.load_estimate == 80
        assert trv_state.heat_available is True
        assert trv_state.heat_required is True
        assert trv_state.window_open_detection == 0
        assert trv_state.setpoint_change_source == 1

    def test_parse_minimal_state(self):
        state = _make_state(attributes={"current_temperature": 20.0})
        trv_state = ZHABackend._parse_zha_state("climate.trv1", state)
        assert trv_state.local_temperature == 20.0
        assert trv_state.occupied_heating_setpoint is None
        assert trv_state.pi_heating_demand is None

    def test_parse_empty_state(self):
        state = _make_state(attributes={})
        trv_state = ZHABackend._parse_zha_state("climate.trv1", state)
        assert trv_state.entity_id == "climate.trv1"
        assert trv_state.local_temperature is None

    def test_raw_preserved(self):
        attrs = {"current_temperature": 19.0, "custom_field": "test"}
        state = _make_state(attributes=attrs)
        trv_state = ZHABackend._parse_zha_state("climate.trv1", state)
        assert trv_state.raw["custom_field"] == "test"


# ── Lifecycle ─────────────────────────────────────────────────────────


class TestZHALifecycle:
    """Tests for ZHA backend setup and teardown."""

    @pytest.fixture
    def backend(self, hass):
        return ZHABackend(hass)

    async def test_setup(self, backend):
        await backend.async_setup()

    async def test_teardown_clears_state(self, backend):
        backend._trv_states["trv1"] = TRVState(entity_id="trv1")
        await backend.async_teardown()
        assert len(backend._trv_states) == 0
        assert len(backend._subscriptions) == 0


# ── Subscriptions ─────────────────────────────────────────────────────


class TestZHASubscriptions:
    """Tests for ZHA entity state subscription."""

    @pytest.fixture
    def backend(self, hass):
        return ZHABackend(hass)

    @patch(
        "custom_components.danfoss_ally_gateway.backend.zha.async_track_state_change_event"
    )
    async def test_subscribe_trv(self, mock_track, backend, hass):
        mock_unsub = MagicMock()
        mock_track.return_value = mock_unsub
        hass.states = MagicMock()
        hass.states.get.return_value = None

        await backend.async_subscribe_trv("climate.trv1")

        mock_track.assert_called_once()
        assert "climate.trv1" in backend._subscriptions

    @patch(
        "custom_components.danfoss_ally_gateway.backend.zha.async_track_state_change_event"
    )
    async def test_subscribe_duplicate_ignored(self, mock_track, backend, hass):
        mock_track.return_value = MagicMock()
        hass.states = MagicMock()
        hass.states.get.return_value = None

        await backend.async_subscribe_trv("climate.trv1")
        await backend.async_subscribe_trv("climate.trv1")

        assert mock_track.call_count == 1

    @patch(
        "custom_components.danfoss_ally_gateway.backend.zha.async_track_state_change_event"
    )
    async def test_subscribe_reads_initial_state(self, mock_track, backend, hass):
        mock_track.return_value = MagicMock()
        mock_state = _make_state(
            state="heat",
            attributes={"current_temperature": 21.0, "temperature": 22.0},
        )
        hass.states = MagicMock()
        hass.states.get.return_value = mock_state

        await backend.async_subscribe_trv("climate.trv1")

        cached = backend._trv_states.get("climate.trv1")
        assert cached is not None
        assert cached.local_temperature == 21.0

    @patch(
        "custom_components.danfoss_ally_gateway.backend.zha.async_track_state_change_event"
    )
    async def test_subscribe_skips_unavailable_initial(self, mock_track, backend, hass):
        mock_track.return_value = MagicMock()
        mock_state = _make_state(state=STATE_UNAVAILABLE)
        hass.states = MagicMock()
        hass.states.get.return_value = mock_state

        await backend.async_subscribe_trv("climate.trv1")

        assert "climate.trv1" not in backend._trv_states

    @patch(
        "custom_components.danfoss_ally_gateway.backend.zha.async_track_state_change_event"
    )
    async def test_unsubscribe(self, mock_track, backend, hass):
        mock_unsub = MagicMock()
        mock_track.return_value = mock_unsub
        hass.states = MagicMock()
        hass.states.get.return_value = None

        await backend.async_subscribe_trv("climate.trv1")
        await backend.async_unsubscribe_trv("climate.trv1")

        mock_unsub.assert_called_once()
        assert "climate.trv1" not in backend._subscriptions

    async def test_get_trv_state_none(self, backend):
        assert await backend.async_get_trv_state("climate.unknown") is None


# ── Cluster Attribute Writes ──────────────────────────────────────────


class TestZHAClusterWrites:
    """Tests for ZHA cluster attribute write methods."""

    @pytest.fixture
    def backend(self, hass):
        return ZHABackend(hass)

    @pytest.fixture
    def mock_er(self):
        """Mock entity registry."""
        with patch(
            "custom_components.danfoss_ally_gateway.backend.zha.er"
        ) as mock_er_mod:
            mock_registry = MagicMock()
            mock_er_mod.async_get.return_value = mock_registry
            mock_registry.async_get.return_value = _mock_entity_registry_entry()
            yield mock_registry

    async def test_set_external_temperature(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_external_temperature("climate.trv1", 21.5)

        hass.services.async_call.assert_called_once()
        call_args = hass.services.async_call.call_args
        assert call_args[0][0] == "zha"
        assert call_args[0][1] == "set_zigbee_cluster_attribute"
        svc_data = call_args[0][2]
        assert svc_data["cluster_id"] == CLUSTER_THERMOSTAT
        assert svc_data["attribute"] == ATTR_EXTERNAL_MEASURED_ROOM_SENSOR
        assert svc_data["value"] == 2150  # 21.5 * 100

    async def test_set_external_temperature_disabled(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_external_temperature("climate.trv1", -80.0)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["value"] == EXTERNAL_TEMP_DISABLED

    async def test_set_occupied_heating_setpoint(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_occupied_heating_setpoint("climate.trv1", 22.0)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["attribute"] == ATTR_OCCUPIED_HEATING_SETPOINT
        assert svc_data["value"] == 2200  # 22.0 * 100
        # Standard attribute - no manufacturer code
        assert "manufacturer" not in svc_data or svc_data["manufacturer"] is None

    async def test_set_heat_available_true(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_heat_available("climate.trv1", True)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["attribute"] == ATTR_HEAT_AVAILABLE
        assert svc_data["value"] is True

    async def test_set_heat_available_false(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_heat_available("climate.trv1", False)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["value"] is False

    async def test_set_load_room_mean(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_load_room_mean("climate.trv1", 150)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["attribute"] == ATTR_LOAD_ROOM_MEAN
        assert svc_data["value"] == 150

    async def test_set_load_balancing_enable(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_load_balancing_enable("climate.trv1", True)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["attribute"] == ATTR_LOAD_BALANCING_ENABLE
        assert svc_data["value"] is True

    async def test_set_external_window_open(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_external_window_open("climate.trv1", True)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["attribute"] == ATTR_EXTERNAL_WINDOW_OPEN
        assert svc_data["value"] is True

    async def test_entity_not_found(self, backend, hass, mock_er):
        """When entity not in registry, the write is skipped."""
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()
        mock_er.async_get.return_value = None

        await backend.async_set_external_temperature("climate.unknown", 21.0)

        hass.services.async_call.assert_not_called()

    async def test_ieee_extracted_from_unique_id(self, backend, hass, mock_er):
        """IEEE address is extracted from unique_id format 'ieee-endpoint'."""
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()
        mock_er.async_get.return_value = _mock_entity_registry_entry(
            unique_id="00:11:22:33:44:55:66:77-1"
        )

        await backend.async_set_heat_available("climate.trv1", True)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["ieee"] == "00:11:22:33:44:55:66:77"

    async def test_manufacturer_code_for_danfoss_attrs(self, backend, hass, mock_er):
        """Danfoss manufacturer-specific attributes include manufacturer code."""
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_heat_available("climate.trv1", True)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["manufacturer"] == DANFOSS_MANUFACTURER_CODE


# ── Commands ──────────────────────────────────────────────────────────


class TestZHACommands:
    """Tests for ZHA command methods."""

    @pytest.fixture
    def backend(self, hass):
        return ZHABackend(hass)

    @pytest.fixture
    def mock_er(self):
        with patch(
            "custom_components.danfoss_ally_gateway.backend.zha.er"
        ) as mock_er_mod:
            mock_registry = MagicMock()
            mock_er_mod.async_get.return_value = mock_registry
            mock_registry.async_get.return_value = _mock_entity_registry_entry()
            yield mock_registry

    async def test_send_setpoint_command(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_send_setpoint_command("climate.trv1", 23.0, 0)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["attribute"] == ATTR_OCCUPIED_HEATING_SETPOINT
        assert svc_data["value"] == 2300

    async def test_send_preheat_command(self, backend, hass, mock_er):
        """Sends PreHeatCommand (0x42) via ZHA cluster command."""
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_send_preheat_command("climate.trv1", 1700000000)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["cluster_id"] == CLUSTER_THERMOSTAT
        assert svc_data["command"] == CMD_PREHEAT_COMMAND
        assert svc_data["args"] == [0x00, 1700000000]
        assert svc_data["manufacturer"] == DANFOSS_MANUFACTURER_CODE

    async def test_sync_time_writes_six_attrs(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_sync_time("climate.trv1")

        # Should write 6 time attributes
        assert hass.services.async_call.call_count == 6

        # Verify attribute IDs for all 6 calls
        attr_ids = set()
        for c in hass.services.async_call.call_args_list:
            svc_data = c[0][2]
            assert svc_data["cluster_id"] == CLUSTER_TIME
            attr_ids.add(svc_data["attribute"])

        expected = {0x0000, 0x0001, 0x0002, 0x0003, 0x0004, 0x0005}
        assert attr_ids == expected

    async def test_sync_time_no_manufacturer_code(self, backend, hass, mock_er):
        """Time cluster attributes are standard (no manufacturer code)."""
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_sync_time("climate.trv1")

        for c in hass.services.async_call.call_args_list:
            svc_data = c[0][2]
            # manufacturer should be None or absent
            assert (
                svc_data.get("manufacturer") is None or "manufacturer" not in svc_data
            )


# ── Schedule ──────────────────────────────────────────────────────────


class TestZHASchedule:
    """Tests for ZHA schedule methods."""

    @pytest.fixture
    def backend(self, hass):
        return ZHABackend(hass)

    @pytest.fixture
    def mock_er(self):
        with patch(
            "custom_components.danfoss_ally_gateway.backend.zha.er"
        ) as mock_er_mod:
            mock_registry = MagicMock()
            mock_er_mod.async_get.return_value = mock_registry
            mock_registry.async_get.return_value = _mock_entity_registry_entry()
            yield mock_registry

    async def test_set_weekly_schedule(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        transitions = [(0, 2000), (480, 2200)]
        await backend.async_set_weekly_schedule(
            "climate.trv1",
            day_of_week=0x02,
            num_transitions=2,
            mode=0x01,
            transitions=transitions,
        )

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["command"] == 0x01  # SetWeeklySchedule
        assert svc_data["cluster_id"] == CLUSTER_THERMOSTAT
        # args: [num_transitions, dow, mode, t1_min, t1_sp, t2_min, t2_sp]
        args = svc_data["args"]
        assert args[0] == 2  # num transitions
        assert args[1] == 0x02  # dow
        assert args[2] == 0x01  # mode
        assert args[3] == 0  # first transition time
        assert args[4] == 2000  # first setpoint
        assert args[5] == 480  # second transition time
        assert args[6] == 2200  # second setpoint

    async def test_get_weekly_schedule(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        result = await backend.async_get_weekly_schedule("climate.trv1", 0x02)

        assert result is None
        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["command"] == 0x02  # GetWeeklySchedule
        args = svc_data["args"]
        assert args[0] == 0x02  # dow
        assert args[1] == 0x01  # mode (heat)

    async def test_clear_weekly_schedule(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_clear_weekly_schedule("climate.trv1")

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["command"] == 0x03  # ClearWeeklySchedule
        assert svc_data["args"] == []

    async def test_set_programming_mode(self, backend, hass, mock_er):
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        await backend.async_set_programming_mode("climate.trv1", 3)

        svc_data = hass.services.async_call.call_args[0][2]
        assert svc_data["attribute"] == ATTR_THERMOSTAT_PROGRAMMING_MODE
        assert svc_data["value"] == 3
        # Standard attribute - no manufacturer code
        assert svc_data.get("manufacturer") is None or "manufacturer" not in svc_data

    async def test_read_sw_error_code_success(self, backend, hass, mock_er):
        """Returns decoded string when ZHA gateway read succeeds."""
        mock_cluster = MagicMock()
        mock_cluster.read_attributes = AsyncMock(return_value=({0x4000: 512}, {}))

        mock_device = MagicMock()
        mock_device.async_get_cluster.return_value = mock_cluster

        mock_gateway = MagicMock()
        mock_gateway.get_device.return_value = mock_device

        mock_eui64 = MagicMock()
        mock_eui64.convert.return_value = "00:11:22:33:44:55:66:77"

        with (
            patch(
                "custom_components.danfoss_ally_gateway.backend.zha.get_zha_gateway",
                return_value=mock_gateway,
            ),
            patch(
                "custom_components.danfoss_ally_gateway.backend.zha.EUI64",
                mock_eui64,
            ),
        ):
            result = await backend.async_read_sw_error_code("climate.trv1")
        assert result == "invalid_clock_information"

    async def test_read_sw_error_code_no_device(self, backend, hass, mock_er):
        """Returns None when ZHA device is not found."""
        mock_gateway = MagicMock()
        mock_gateway.get_device.return_value = None

        mock_eui64 = MagicMock()
        mock_eui64.convert.return_value = "00:11:22:33:44:55:66:77"

        with (
            patch(
                "custom_components.danfoss_ally_gateway.backend.zha.get_zha_gateway",
                return_value=mock_gateway,
            ),
            patch(
                "custom_components.danfoss_ally_gateway.backend.zha.EUI64",
                mock_eui64,
            ),
        ):
            result = await backend.async_read_sw_error_code("climate.trv1")
        assert result is None

    async def test_read_sw_error_code_gateway_unavailable(self, backend, hass, mock_er):
        """Returns None when ZHA gateway is not available."""
        with patch(
            "custom_components.danfoss_ally_gateway.backend.zha.get_zha_gateway",
            side_effect=RuntimeError("ZHA not loaded"),
        ):
            result = await backend.async_read_sw_error_code("climate.trv1")
        assert result is None
