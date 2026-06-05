"""Tests for the Zigbee2MQTT backend."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.danfoss_ally_gateway.backend import TRVState
from custom_components.danfoss_ally_gateway.backend.z2m import (
    Z2MBackend,
    _parse_bool,
    _parse_setpoint_change_source,
    _parse_trv_state,
    _parse_window_open,
)
from custom_components.danfoss_ally_gateway.const import (
    EXTERNAL_TEMP_DISABLED,
    Z2M_ATTR_EXTERNAL_MEASURED_ROOM_SENSOR,
    Z2M_ATTR_EXTERNAL_WINDOW_OPEN,
    Z2M_ATTR_HEAT_AVAILABLE,
    Z2M_ATTR_LOAD_BALANCING_ENABLE,
    Z2M_ATTR_LOAD_ESTIMATE,
    Z2M_ATTR_LOAD_ROOM_MEAN,
    Z2M_ATTR_LOCAL_TEMPERATURE,
    Z2M_ATTR_OCCUPIED_HEATING_SETPOINT,
    Z2M_ATTR_PI_HEATING_DEMAND,
    Z2M_ATTR_PROGRAMMING_MODE,
    Z2M_ATTR_WINDOW_OPEN_DETECTION,
)

# ── State Parsing ─────────────────────────────────────────────────────


class TestParseState:
    """Tests for _parse_trv_state."""

    def test_full_payload(self):
        payload = {
            Z2M_ATTR_LOCAL_TEMPERATURE: 21.5,
            Z2M_ATTR_OCCUPIED_HEATING_SETPOINT: 22.0,
            Z2M_ATTR_PI_HEATING_DEMAND: 50,
            Z2M_ATTR_LOAD_ESTIMATE: 100,
            Z2M_ATTR_WINDOW_OPEN_DETECTION: 0,
        }
        state = _parse_trv_state("living_trv", payload)
        assert state.entity_id == "living_trv"
        assert state.local_temperature == 21.5
        assert state.occupied_heating_setpoint == 22.0
        assert state.pi_heating_demand == 50
        assert state.load_estimate == 100
        assert state.window_open_detection == 0

    def test_minimal_payload(self):
        payload = {Z2M_ATTR_LOCAL_TEMPERATURE: 20.0}
        state = _parse_trv_state("trv1", payload)
        assert state.local_temperature == 20.0
        assert state.occupied_heating_setpoint is None
        assert state.pi_heating_demand is None

    def test_empty_payload(self):
        state = _parse_trv_state("trv1", {})
        assert state.entity_id == "trv1"
        assert state.local_temperature is None

    def test_raw_preserved(self):
        payload = {"custom_field": 123, Z2M_ATTR_LOCAL_TEMPERATURE: 19.0}
        state = _parse_trv_state("trv1", payload)
        assert state.raw == payload
        assert state.raw["custom_field"] == 123


# ── Z2M String Value Conversion ──────────────────────────────────────


class TestZ2MStringConversions:
    """Tests for Z2M enum string → numeric conversion.

    Z2M's converters translate raw ZCL enum values into human-readable
    strings.  These tests verify the parser normalises them correctly.
    """

    # ── window_open_internal ──────────────────────────────────────────

    def test_window_open_string_closed(self):
        assert _parse_window_open("closed") == 1

    def test_window_open_string_open(self):
        assert _parse_window_open("open") == 3

    def test_window_open_string_hold(self):
        assert _parse_window_open("hold") == 2

    def test_window_open_string_quarantine(self):
        assert _parse_window_open("quarantine") == 0

    def test_window_open_string_external_open(self):
        assert _parse_window_open("external_open") == 4

    def test_window_open_int_passthrough(self):
        assert _parse_window_open(3) == 3

    def test_window_open_none(self):
        assert _parse_window_open(None) is None

    def test_window_open_unknown_string(self):
        assert _parse_window_open("something_else") is None

    def test_window_open_numeric_string(self):
        assert _parse_window_open("3") == 3

    # ── setpoint_change_source ────────────────────────────────────────

    def test_setpoint_source_manual(self):
        assert _parse_setpoint_change_source("manual") == 0

    def test_setpoint_source_schedule(self):
        assert _parse_setpoint_change_source("schedule") == 1

    def test_setpoint_source_externally(self):
        assert _parse_setpoint_change_source("externally") == 2

    def test_setpoint_source_int_passthrough(self):
        assert _parse_setpoint_change_source(0) == 0

    def test_setpoint_source_none(self):
        assert _parse_setpoint_change_source(None) is None

    def test_setpoint_source_unknown_string(self):
        assert _parse_setpoint_change_source("unknown") is None

    # ── _parse_bool ───────────────────────────────────────────────────

    def test_bool_true(self):
        assert _parse_bool(True) is True

    def test_bool_false(self):
        assert _parse_bool(False) is False

    def test_bool_none(self):
        assert _parse_bool(None) is None

    def test_bool_int_1(self):
        assert _parse_bool(1) is True

    def test_bool_int_0(self):
        assert _parse_bool(0) is False

    def test_bool_string_true(self):
        assert _parse_bool("true") is True

    def test_bool_string_false(self):
        assert _parse_bool("false") is False

    def test_bool_unexpected_string(self):
        """Unexpected strings like Z2M's 'No Heat Available' → False."""
        assert _parse_bool("No Heat Available") is False

    # ── Full payload with real Z2M string values ──────────────────────

    def test_real_z2m_payload(self):
        """Parse a real Z2M payload with string enum values."""
        payload = {
            "local_temperature": 21.56,
            "occupied_heating_setpoint": 23,
            "pi_heating_demand": 99,
            "position": 30,
            "load_estimate": 842,
            "load_balancing_enable": True,
            "heat_available": True,
            "heat_required": True,
            "preheat_status": False,
            "radiator_covered": False,
            "window_open_internal": "closed",
            "window_open_external": False,
            "setpoint_change_source": "externally",
        }
        state = _parse_trv_state("living_room_trv", payload)
        assert state.local_temperature == 21.56
        assert state.occupied_heating_setpoint == 23
        assert state.pi_heating_demand == 99
        assert state.window_open_detection == 1  # "closed" → 1
        assert state.setpoint_change_source == 2  # "externally" → 2
        assert state.heat_available is True
        assert state.heat_required is True
        assert state.preheat_status is False
        assert state.external_window_open is False
        assert state.radiator_covered is False

    def test_real_z2m_payload_window_open(self):
        """Parse a Z2M payload with window open detected."""
        payload = {
            "window_open_internal": "open",
            "setpoint_change_source": "manual",
            "heat_available": False,
        }
        state = _parse_trv_state("trv1", payload)
        assert state.window_open_detection == 3  # "open" → 3
        assert state.setpoint_change_source == 0  # "manual" → 0
        assert state.heat_available is False


# ── Lifecycle ─────────────────────────────────────────────────────────


class TestZ2MLifecycle:
    """Tests for Z2M backend setup and teardown."""

    @pytest.fixture
    def backend(self, hass):
        return Z2MBackend(hass, base_topic="zigbee2mqtt")

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_setup(self, mock_mqtt, backend):
        """Setup completes without error and subscribes to bridge events."""
        mock_mqtt.async_subscribe = AsyncMock(return_value=MagicMock())
        await backend.async_setup()
        # Should subscribe to bridge/event topic
        mock_mqtt.async_subscribe.assert_called_once()
        topic = mock_mqtt.async_subscribe.call_args[0][1]
        assert topic == "zigbee2mqtt/bridge/event"

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_teardown_clears_state(self, mock_mqtt, backend):
        """Teardown clears all cached state."""
        mock_mqtt.async_subscribe = AsyncMock(return_value=MagicMock())
        await backend.async_setup()
        backend._trv_states["trv1"] = TRVState(entity_id="trv1")
        await backend.async_teardown()
        assert len(backend._trv_states) == 0
        assert len(backend._subscriptions) == 0

    async def test_base_topic_stripping(self, hass):
        """Trailing slash is stripped from base topic."""
        b = Z2MBackend(hass, base_topic="zigbee2mqtt/")
        assert b._base_topic == "zigbee2mqtt"


# ── Subscriptions ─────────────────────────────────────────────────────


class TestZ2MSubscriptions:
    """Tests for MQTT subscription management."""

    @pytest.fixture
    def backend(self, hass):
        return Z2MBackend(hass, base_topic="zigbee2mqtt")

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_subscribe_trv(self, mock_mqtt, backend):
        mock_unsub = MagicMock()
        mock_mqtt.async_subscribe = AsyncMock(return_value=mock_unsub)
        mock_mqtt.async_publish = AsyncMock()

        await backend.async_subscribe_trv("living_trv")

        mock_mqtt.async_subscribe.assert_called_once()
        call_args = mock_mqtt.async_subscribe.call_args
        assert call_args[0][1] == "zigbee2mqtt/living_trv"  # topic
        assert "living_trv" in backend._subscriptions

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_subscribe_duplicate_ignored(self, mock_mqtt, backend):
        mock_unsub = MagicMock()
        mock_mqtt.async_subscribe = AsyncMock(return_value=mock_unsub)
        mock_mqtt.async_publish = AsyncMock()

        await backend.async_subscribe_trv("trv1")
        await backend.async_subscribe_trv("trv1")

        # Only subscribed once
        assert mock_mqtt.async_subscribe.call_count == 1

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_unsubscribe_trv(self, mock_mqtt, backend):
        mock_unsub = MagicMock()
        mock_mqtt.async_subscribe = AsyncMock(return_value=mock_unsub)
        mock_mqtt.async_publish = AsyncMock()

        await backend.async_subscribe_trv("trv1")
        await backend.async_unsubscribe_trv("trv1")

        mock_unsub.assert_called_once()
        assert "trv1" not in backend._subscriptions

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_unsubscribe_nonexistent(self, mock_mqtt, backend):
        """Unsubscribing a non-subscribed TRV does not raise."""
        await backend.async_unsubscribe_trv("nonexistent")

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_get_trv_state_none(self, mock_mqtt, backend):
        """Returns None for unknown TRV."""
        assert await backend.async_get_trv_state("unknown") is None

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_subscribe_sends_get_request(self, mock_mqtt, backend):
        """Subscribing also sends a get request to refresh state."""
        mock_mqtt.async_subscribe = AsyncMock(return_value=MagicMock())
        mock_mqtt.async_publish = AsyncMock()

        await backend.async_subscribe_trv("trv1")

        # Should publish a get request
        mock_mqtt.async_publish.assert_called_once()
        call_args = mock_mqtt.async_publish.call_args
        assert call_args[0][1] == "zigbee2mqtt/trv1/get"


# ── MQTT Message Handling ─────────────────────────────────────────────


class TestZ2MMessageHandling:
    """Tests for incoming MQTT message processing."""

    @pytest.fixture
    def backend(self, hass):
        return Z2MBackend(hass, base_topic="zigbee2mqtt")

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_message_updates_state(self, mock_mqtt, backend):
        """Valid MQTT message updates cached TRV state."""
        handler = None

        async def capture_subscribe(hass, topic, callback):
            nonlocal handler
            handler = callback
            return MagicMock()

        mock_mqtt.async_subscribe = capture_subscribe
        mock_mqtt.async_publish = AsyncMock()

        await backend.async_subscribe_trv("trv1")
        assert handler is not None

        # Simulate incoming message
        msg = MagicMock()
        msg.topic = "zigbee2mqtt/trv1"
        msg.payload = json.dumps(
            {
                Z2M_ATTR_LOCAL_TEMPERATURE: 21.5,
                Z2M_ATTR_OCCUPIED_HEATING_SETPOINT: 22.0,
            }
        )
        handler(msg)

        state = backend._trv_states.get("trv1")
        assert state is not None
        assert state.local_temperature == 21.5
        assert state.occupied_heating_setpoint == 22.0

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_invalid_json_ignored(self, mock_mqtt, backend):
        """Invalid JSON messages are ignored without error."""
        handler = None

        async def capture_subscribe(hass, topic, callback):
            nonlocal handler
            handler = callback
            return MagicMock()

        mock_mqtt.async_subscribe = capture_subscribe
        mock_mqtt.async_publish = AsyncMock()

        await backend.async_subscribe_trv("trv1")

        msg = MagicMock()
        msg.topic = "zigbee2mqtt/trv1"
        msg.payload = "not valid json"

        # Should not raise
        handler(msg)  # type: ignore[misc]

        assert "trv1" not in backend._trv_states

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_non_dict_payload_ignored(self, mock_mqtt, backend):
        """Non-dict JSON payloads are ignored."""
        handler = None

        async def capture_subscribe(hass, topic, callback):
            nonlocal handler
            handler = callback
            return MagicMock()

        mock_mqtt.async_subscribe = capture_subscribe
        mock_mqtt.async_publish = AsyncMock()

        await backend.async_subscribe_trv("trv1")

        msg = MagicMock()
        msg.topic = "zigbee2mqtt/trv1"
        msg.payload = json.dumps([1, 2, 3])
        handler(msg)  # type: ignore[misc]

        assert "trv1" not in backend._trv_states

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_state_callback_fired(self, mock_mqtt, backend):
        """State callbacks are fired on valid message."""
        handler = None
        received = []

        async def capture_subscribe(hass, topic, callback):
            nonlocal handler
            handler = callback
            return MagicMock()

        mock_mqtt.async_subscribe = capture_subscribe
        mock_mqtt.async_publish = AsyncMock()

        backend.register_state_callback(lambda tid, s: received.append((tid, s)))
        await backend.async_subscribe_trv("trv1")

        msg = MagicMock()
        msg.topic = "zigbee2mqtt/trv1"
        msg.payload = json.dumps({Z2M_ATTR_LOCAL_TEMPERATURE: 20.0})
        handler(msg)  # type: ignore[misc]

        assert len(received) == 1
        assert received[0][0] == "trv1"
        assert received[0][1].local_temperature == 20.0


# ── Attribute Writes ──────────────────────────────────────────────────


class TestZ2MAttributeWrites:
    """Tests for Z2M attribute write methods."""

    @pytest.fixture
    def backend(self, hass):
        return Z2MBackend(hass, base_topic="zigbee2mqtt")

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_external_temperature(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_external_temperature("trv1", 21.5)
        mock_mqtt.async_publish.assert_called_once()
        call_args = mock_mqtt.async_publish.call_args
        assert call_args[0][1] == "zigbee2mqtt/trv1/set"
        payload = json.loads(call_args[0][2])
        assert payload[Z2M_ATTR_EXTERNAL_MEASURED_ROOM_SENSOR] == 2150

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_external_temperature_disabled(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_external_temperature("trv1", -80.0)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_EXTERNAL_MEASURED_ROOM_SENSOR] == EXTERNAL_TEMP_DISABLED

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_occupied_heating_setpoint(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_occupied_heating_setpoint("trv1", 22.0)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_OCCUPIED_HEATING_SETPOINT] == 22.0

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_heat_available(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_heat_available("trv1", True)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_HEAT_AVAILABLE] is True

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_heat_available_false(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_heat_available("trv1", False)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_HEAT_AVAILABLE] is False

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_load_room_mean(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_load_room_mean("trv1", 150)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_LOAD_ROOM_MEAN] == 150

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_load_balancing_enable(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_load_balancing_enable("trv1", True)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_LOAD_BALANCING_ENABLE] is True

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_external_window_open(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_external_window_open("trv1", True)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_EXTERNAL_WINDOW_OPEN] is True


# ── Commands ──────────────────────────────────────────────────────────


class TestZ2MCommands:
    """Tests for Z2M command methods."""

    @pytest.fixture
    def backend(self, hass):
        return Z2MBackend(hass, base_topic="zigbee2mqtt")

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_send_setpoint_command(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_send_setpoint_command("trv1", 23.0, 0)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_OCCUPIED_HEATING_SETPOINT] == 23.0

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_send_setpoint_command_type1(self, mock_mqtt, backend):
        """Type 1 (user/aggressive) also writes setpoint in Z2M."""
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_send_setpoint_command("trv1", 24.0, 1)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_OCCUPIED_HEATING_SETPOINT] == 24.0

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_send_preheat_command(self, mock_mqtt, backend):
        """Preheat command sends correct payload via Z2M."""
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_send_preheat_command("trv1", 1700000000)
        mock_mqtt.async_publish.assert_called_once()
        topic = mock_mqtt.async_publish.call_args[0][1]
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert topic == "zigbee2mqtt/trv1/set"
        assert payload == {"preheat_command": {"timestamp": 1700000000}}

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_sync_time_is_noop(self, mock_mqtt, backend):
        """Time sync is a no-op for Z2M (handled natively by Z2M)."""
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_sync_time("trv1")
        mock_mqtt.async_publish.assert_not_called()


# ── Schedule ──────────────────────────────────────────────────────────


class TestZ2MSchedule:
    """Tests for Z2M schedule methods."""

    @pytest.fixture
    def backend(self, hass):
        return Z2MBackend(hass, base_topic="zigbee2mqtt")

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_weekly_schedule(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        transitions = [(0, 2000), (480, 2200)]
        await backend.async_set_weekly_schedule(
            "trv1",
            day_of_week=0x02,  # Monday
            num_transitions=2,
            mode=0x01,
            transitions=transitions,
        )
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert "weekly_schedule" in payload
        ws = payload["weekly_schedule"]
        assert ws["dayofweek"] == 0x02
        assert ws["numoftrans"] == 2
        assert ws["mode"] == 0x01
        assert len(ws["transitions"]) == 2
        assert ws["transitions"][0]["transitionTime"] == 0
        assert ws["transitions"][0]["heatSetpoint"] == 2000
        assert ws["transitions"][1]["transitionTime"] == 480
        assert ws["transitions"][1]["heatSetpoint"] == 2200

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_get_weekly_schedule(self, mock_mqtt, backend):
        """Get schedule sends a get request and returns None."""
        mock_mqtt.async_publish = AsyncMock()
        result = await backend.async_get_weekly_schedule("trv1", 0x02)
        assert result is None
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert "weekly_schedule" in payload

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_clear_weekly_schedule(self, mock_mqtt, backend):
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_clear_weekly_schedule("trv1")
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert "clear_weekly_schedule" in payload

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_programming_mode_schedule_with_preheat(self, mock_mqtt, backend):
        """Mode 3 maps to Z2M string 'schedule_with_preheat'."""
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_programming_mode("trv1", 3)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_PROGRAMMING_MODE] == "schedule_with_preheat"

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_programming_mode_setpoint(self, mock_mqtt, backend):
        """Mode 0 maps to Z2M string 'setpoint'."""
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_programming_mode("trv1", 0)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_PROGRAMMING_MODE] == "setpoint"

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_programming_mode_schedule(self, mock_mqtt, backend):
        """Mode 1 maps to Z2M string 'schedule'."""
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_programming_mode("trv1", 1)
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert payload[Z2M_ATTR_PROGRAMMING_MODE] == "schedule"

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_set_programming_mode_unknown(self, mock_mqtt, backend):
        """Unknown mode value is skipped with no MQTT publish."""
        mock_mqtt.async_publish = AsyncMock()
        await backend.async_set_programming_mode("trv1", 99)
        mock_mqtt.async_publish.assert_not_called()

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_read_sw_error_code(self, mock_mqtt, backend):
        """sw_error_code read requests attribute and returns cached string."""
        mock_mqtt.async_publish = AsyncMock()
        # Pre-populate cached state with system_status_code (string from upstream converter)
        backend._trv_states["trv1"] = TRVState(
            entity_id="trv1",
            raw={"system_status_code": "invalid_clock_information"},
        )
        result = await backend.async_read_sw_error_code("trv1")
        assert result == "invalid_clock_information"
        # Verify it published a get request
        topic = mock_mqtt.async_publish.call_args[0][1]
        payload = json.loads(mock_mqtt.async_publish.call_args[0][2])
        assert topic == "zigbee2mqtt/trv1/get"
        assert payload == {"system_status_code": ""}

    @patch("custom_components.danfoss_ally_gateway.backend.z2m.mqtt")
    async def test_read_sw_error_code_no_cached_state(self, mock_mqtt, backend):
        """sw_error_code returns None when no cached state exists."""
        mock_mqtt.async_publish = AsyncMock()
        result = await backend.async_read_sw_error_code("trv1")
        assert result is None


# ── Device Announce Callbacks ─────────────────────────────────────────


class TestDeviceAnnounceCallbacks:
    """Tests for _fire_device_announce and announce callback registration."""

    @pytest.fixture
    def backend(self, hass):
        return Z2MBackend(hass, base_topic="zigbee2mqtt")

    def test_announce_callback_fired(self, backend):
        """Registered announce callback is called with the correct trv_id."""
        received = []
        backend.register_announce_callback(lambda tid: received.append(tid))
        backend._fire_device_announce("living_trv")
        assert received == ["living_trv"]

    def test_announce_callback_multiple(self, backend):
        """Multiple announce callbacks are all invoked."""
        received_a = []
        received_b = []
        backend.register_announce_callback(lambda tid: received_a.append(tid))
        backend.register_announce_callback(lambda tid: received_b.append(tid))
        backend._fire_device_announce("trv1")
        assert received_a == ["trv1"]
        assert received_b == ["trv1"]

    def test_announce_callback_unregister(self, backend):
        """Unregistered announce callback is no longer called."""
        received = []
        unregister = backend.register_announce_callback(
            lambda tid: received.append(tid)
        )
        unregister()
        backend._fire_device_announce("trv1")
        assert received == []

    def test_announce_callback_no_callbacks(self, backend):
        """Firing device announce with no callbacks does not raise."""
        backend._fire_device_announce("trv1")


# ── Parser Unexpected Type Edge Cases ─────────────────────────────────


class TestParserUnexpectedTypes:
    """Tests for parser functions receiving unexpected (non-handled) types."""

    def test_window_open_unexpected_type_list(self):
        assert _parse_window_open([]) is None

    def test_window_open_unexpected_type_dict(self):
        assert _parse_window_open({}) is None

    def test_setpoint_source_unexpected_type_list(self):
        assert _parse_setpoint_change_source([]) is None

    def test_setpoint_source_unexpected_type_dict(self):
        assert _parse_setpoint_change_source({}) is None

    def test_bool_unexpected_type_list(self):
        assert _parse_bool([]) is None

    def test_bool_unexpected_type_dict(self):
        assert _parse_bool({}) is None
