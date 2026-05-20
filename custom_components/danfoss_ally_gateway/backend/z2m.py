"""Zigbee2MQTT backend for Danfoss Ally TRV communication.

Communicates with Danfoss Ally TRVs via Z2M's MQTT interface.
All Danfoss manufacturer-specific attributes are exposed as JSON properties
on the Z2M MQTT topic for each device.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from ..const import (
    EXTERNAL_TEMP_DISABLED,
    SETPOINT_TYPE_USER,
    Z2M_ATTR_EXTERNAL_MEASURED_ROOM_SENSOR,
    Z2M_ATTR_EXTERNAL_WINDOW_OPEN,
    Z2M_ATTR_HEAT_AVAILABLE,
    Z2M_ATTR_HEAT_REQUIRED,
    Z2M_ATTR_LOAD_BALANCING_ENABLE,
    Z2M_ATTR_LOAD_ESTIMATE,
    Z2M_ATTR_LOAD_ROOM_MEAN,
    Z2M_ATTR_LOCAL_TEMPERATURE,
    Z2M_ATTR_OCCUPIED_HEATING_SETPOINT,
    Z2M_ATTR_PI_HEATING_DEMAND,
    Z2M_ATTR_PREHEAT_STATUS,
    Z2M_ATTR_PREHEAT_TIME,
    Z2M_ATTR_PROGRAMMING_MODE,
    Z2M_ATTR_RADIATOR_COVERED,
    Z2M_ATTR_SETPOINT_CHANGE_SOURCE,
    Z2M_ATTR_WINDOW_OPEN_DETECTION,
)
from . import DanfossBackend, TRVState

_LOGGER = logging.getLogger(__name__)

# ── Z2M programming_operation_mode mapping ─────────────────────────────
# Z2M expects string values for this attribute; the integration uses
# raw ZCL integer values internally.  This maps the integration's
# integer constants to the Z2M string values.
_PROGRAMMING_MODE_TO_Z2M: dict[int, str] = {
    0: "setpoint",  # SCHEDULE_MODE_MANUAL
    1: "schedule",  # SCHEDULE_MODE_SCHEDULE
    3: "schedule_with_preheat",  # SCHEDULE_MODE_SCHEDULE_PREHEAT
    4: "eco",  # SCHEDULE_MODE_ECO (pause)
}

# ── Z2M enum/string → numeric conversion maps ─────────────────────────
# Z2M's converters translate raw ZCL enum values into human-readable
# strings.  The rest of the codebase expects the numeric originals.

_WINDOW_OPEN_MAP: dict[str, int] = {
    "quarantine": 0,
    "closed": 1,
    "hold": 2,
    "open": 3,
    "external_open": 4,
}

_SETPOINT_SOURCE_MAP: dict[str, int] = {
    "manual": 0,
    "schedule": 1,
    "externally": 2,
}


def _parse_window_open(value: Any) -> int | None:
    """Convert Z2M ``window_open_internal`` to an integer state.

    Z2M sends this as a string (e.g. ``"closed"``, ``"open"``).
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        mapped = _WINDOW_OPEN_MAP.get(value.lower())
        if mapped is not None:
            return mapped
        # Fall back: maybe it's a numeric string
        try:
            return int(value)
        except ValueError:
            _LOGGER.warning("Unknown window_open_internal value: %r", value)
            return None
    return None


def _parse_setpoint_change_source(value: Any) -> int | None:
    """Convert Z2M ``setpoint_change_source`` to an integer.

    Z2M sends this as a string (e.g. ``"manual"``, ``"externally"``).
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        mapped = _SETPOINT_SOURCE_MAP.get(value.lower())
        if mapped is not None:
            return mapped
        try:
            return int(value)
        except ValueError:
            _LOGGER.warning("Unknown setpoint_change_source value: %r", value)
            return None
    return None


def _parse_bool(value: Any) -> bool | None:
    """Coerce a Z2M value to ``bool | None``.

    Z2M normally sends ``true``/``false`` for boolean attributes, but some
    firmware/converter combinations may produce strings.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lower = value.lower()
        if lower in ("true", "1", "on", "yes"):
            return True
        if lower in ("false", "0", "off", "no"):
            return False
        # Catch-all for unexpected strings like "No Heat Available"
        _LOGGER.warning("Unexpected string value for boolean attribute: %r", value)
        return False
    return None


def _parse_trv_state(trv_id: str, payload: dict[str, Any]) -> TRVState:
    """Parse a Z2M JSON payload into a TRVState.

    Z2M's converters translate several ZCL enum/numeric fields into
    human-readable strings.  This function normalises them back to the
    numeric types the rest of the codebase expects.
    """
    return TRVState(
        entity_id=trv_id,
        local_temperature=payload.get(Z2M_ATTR_LOCAL_TEMPERATURE),
        occupied_heating_setpoint=payload.get(Z2M_ATTR_OCCUPIED_HEATING_SETPOINT),
        pi_heating_demand=payload.get(Z2M_ATTR_PI_HEATING_DEMAND),
        heat_required=_parse_bool(payload.get(Z2M_ATTR_HEAT_REQUIRED)),
        load_estimate=payload.get(Z2M_ATTR_LOAD_ESTIMATE),
        load_balancing_enable=payload.get(Z2M_ATTR_LOAD_BALANCING_ENABLE),
        heat_available=_parse_bool(payload.get(Z2M_ATTR_HEAT_AVAILABLE)),
        preheat_status=_parse_bool(payload.get(Z2M_ATTR_PREHEAT_STATUS)),
        preheat_time=payload.get(Z2M_ATTR_PREHEAT_TIME),
        window_open_detection=_parse_window_open(
            payload.get(Z2M_ATTR_WINDOW_OPEN_DETECTION)
        ),
        external_window_open=_parse_bool(payload.get(Z2M_ATTR_EXTERNAL_WINDOW_OPEN)),
        setpoint_change_source=_parse_setpoint_change_source(
            payload.get(Z2M_ATTR_SETPOINT_CHANGE_SOURCE)
        ),
        radiator_covered=_parse_bool(payload.get(Z2M_ATTR_RADIATOR_COVERED)),
        raw=payload,
    )


class Z2MBackend(DanfossBackend):
    """Zigbee2MQTT MQTT-based backend for Danfoss Ally TRVs.

    TRV IDs are the Z2M friendly_name (the MQTT topic name for the device).
    For example, if the device is at ``zigbee2mqtt/Living Room TRV``, the
    trv_id is ``Living Room TRV``.
    """

    def __init__(self, hass: HomeAssistant, base_topic: str = "zigbee2mqtt") -> None:
        """Initialize the Z2M backend.

        Args:
            hass: Home Assistant instance.
            base_topic: Z2M MQTT base topic (default: ``zigbee2mqtt``).
        """
        super().__init__(hass)
        self._base_topic = base_topic.rstrip("/")
        self._subscriptions: dict[str, Any] = {}  # trv_id -> unsubscribe callable
        self._trv_states: dict[str, TRVState] = {}  # trv_id -> latest state
        self._bridge_event_unsub: Any = None  # bridge/event subscription

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        """Set up the Z2M backend."""
        _LOGGER.debug("Z2M backend setup with base topic: %s", self._base_topic)
        # Subscribe to bridge events for device announce detection
        bridge_topic = f"{self._base_topic}/bridge/event"

        @callback
        def _handle_bridge_event(msg: mqtt.ReceiveMessage) -> None:
            """Handle Z2M bridge events (device_announce)."""
            try:
                payload = json.loads(msg.payload)
            except (json.JSONDecodeError, TypeError):
                return
            if not isinstance(payload, dict):
                return
            if payload.get("type") != "device_announce":
                return
            friendly_name = payload.get("data", {}).get("friendly_name")
            if friendly_name and friendly_name in self._subscriptions:
                _LOGGER.debug("Device announce for subscribed TRV: %s", friendly_name)
                self._fire_device_announce(friendly_name)

        self._bridge_event_unsub = await mqtt.async_subscribe(
            self.hass, bridge_topic, _handle_bridge_event
        )

    async def async_teardown(self) -> None:
        """Tear down the Z2M backend - unsubscribe all TRVs."""
        if self._bridge_event_unsub is not None:
            self._bridge_event_unsub()
            self._bridge_event_unsub = None
        for trv_id in list(self._subscriptions):
            await self.async_unsubscribe_trv(trv_id)
        self._trv_states.clear()
        _LOGGER.debug("Z2M backend teardown complete")

    # ── Subscriptions ──────────────────────────────────────────────────

    async def async_subscribe_trv(self, trv_id: str) -> None:
        """Subscribe to MQTT topic for a TRV."""
        if trv_id in self._subscriptions:
            _LOGGER.debug("Already subscribed to TRV: %s", trv_id)
            return

        topic = f"{self._base_topic}/{trv_id}"

        @callback
        def _handle_message(msg: mqtt.ReceiveMessage) -> None:
            """Handle incoming MQTT message for a TRV."""
            try:
                payload = json.loads(msg.payload)
            except (json.JSONDecodeError, TypeError):
                _LOGGER.warning("Invalid JSON from %s: %s", msg.topic, msg.payload)
                return

            if not isinstance(payload, dict):
                return

            state = _parse_trv_state(trv_id, payload)
            self._trv_states[trv_id] = state
            self._fire_state_update(trv_id, state)

        unsub = await mqtt.async_subscribe(self.hass, topic, _handle_message)
        self._subscriptions[trv_id] = unsub
        _LOGGER.debug("Subscribed to TRV: %s (topic: %s)", trv_id, topic)

        # Request current state by publishing a get request
        await self._async_get(trv_id)

    async def async_unsubscribe_trv(self, trv_id: str) -> None:
        """Unsubscribe from MQTT topic for a TRV."""
        unsub = self._subscriptions.pop(trv_id, None)
        if unsub is not None:
            unsub()
            _LOGGER.debug("Unsubscribed from TRV: %s", trv_id)
        self._trv_states.pop(trv_id, None)

    async def async_get_trv_state(self, trv_id: str) -> TRVState | None:
        """Return cached TRV state."""
        return self._trv_states.get(trv_id)

    # ── Attribute writes ───────────────────────────────────────────────

    async def async_set_external_temperature(
        self, trv_id: str, temperature: float
    ) -> None:
        """Write external measured room sensor value to TRV via Z2M."""
        # Z2M expects the raw Int16 value (temp × 100) for this attribute.
        # e.g. 20.5°C → 2050, -80.0°C → -8000 (disabled)
        if temperature <= -80.0:
            value = EXTERNAL_TEMP_DISABLED  # -8000
        else:
            value = int(temperature * 100)
        await self._async_set(trv_id, {Z2M_ATTR_EXTERNAL_MEASURED_ROOM_SENSOR: value})

    async def async_set_occupied_heating_setpoint(
        self, trv_id: str, temperature: float
    ) -> None:
        """Write OccupiedHeatingSetpoint to TRV (gentle/Type 0)."""
        await self._async_set(trv_id, {Z2M_ATTR_OCCUPIED_HEATING_SETPOINT: temperature})

    async def async_set_heat_available(self, trv_id: str, available: bool) -> None:
        """Write HeatAvailable attribute to TRV."""
        await self._async_set(trv_id, {Z2M_ATTR_HEAT_AVAILABLE: available})

    async def async_set_load_room_mean(self, trv_id: str, value: int) -> None:
        """Write LoadRoomMean to TRV."""
        await self._async_set(trv_id, {Z2M_ATTR_LOAD_ROOM_MEAN: value})

    async def async_set_load_balancing_enable(self, trv_id: str, enable: bool) -> None:
        """Write LoadBalancingEnable to TRV."""
        await self._async_set(trv_id, {Z2M_ATTR_LOAD_BALANCING_ENABLE: enable})

    async def async_set_external_window_open(self, trv_id: str, is_open: bool) -> None:
        """Write ExternalOpenWindowDetected to TRV."""
        await self._async_set(trv_id, {Z2M_ATTR_EXTERNAL_WINDOW_OPEN: is_open})

    # ── Commands ───────────────────────────────────────────────────────

    async def async_send_setpoint_command(
        self, trv_id: str, temperature: float, command_type: int
    ) -> None:
        """Send SetpointCommand via Z2M.

        Z2M exposes this as writing occupied_heating_setpoint for Type 0.
        For Type 1 (user/aggressive), we need a different approach via Z2M's
        setpoint command support if available, otherwise fall back to writing
        the setpoint attribute directly.
        """
        if command_type == SETPOINT_TYPE_USER:
            # Type 1: User interaction / aggressive motor
            # Z2M doesn't have a direct "setpoint command type 1" interface,
            # so we write the setpoint normally. The receiving TRV treats any
            # external write similarly. For true Type 1, the coordinator should
            # note that the original change was manual (source=0x00).
            _LOGGER.debug(
                "Setpoint command Type 1 (user) to %s: %.1f°C", trv_id, temperature
            )
        await self._async_set(trv_id, {Z2M_ATTR_OCCUPIED_HEATING_SETPOINT: temperature})

    async def async_send_preheat_command(self, trv_id: str, timestamp: int) -> None:
        """Send PreHeatCommand (0x42) via Z2M.

        Per Danfoss spec (AU417130778872en-000102, §3.2), the command takes:
          - enum8 = 0x00 (force preheat)
          - uint32 = timestamp from source TRV's preheat_time attribute

        Requires zigbee-herdsman-converters >= 26.40.0 (PR #12026).
        """
        await self._async_set(trv_id, {"preheat_command": {"timestamp": timestamp}})

    async def async_sync_time(self, trv_id: str) -> None:
        """No-op for Z2M -- time sync is handled natively.

        Z2M automatically synchronizes the Time cluster (0x000A) attributes
        during device interview and rejoin.  The ``/set`` MQTT endpoint does
        not have ``toZigbee`` converters for the raw Time cluster attributes
        (``time``, ``time_status``, ``time_zone``, ``dst_start``,
        ``dst_end``, ``dst_shift``), so attempting to write them produces
        "No converter available" errors in the Z2M log.
        """
        _LOGGER.debug(
            "Skipping time sync for Z2M TRV %s (handled by Z2M natively)",
            trv_id,
        )

    # ── MQTT helpers ───────────────────────────────────────────────────

    async def _async_set(self, trv_id: str, payload: dict[str, Any]) -> None:
        """Publish a set command to a TRV via Z2M."""
        topic = f"{self._base_topic}/{trv_id}/set"
        message = json.dumps(payload)
        await mqtt.async_publish(self.hass, topic, message)
        _LOGGER.debug("Published to %s: %s", topic, message)

    async def _async_get(
        self, trv_id: str, attributes: dict[str, str] | None = None
    ) -> None:
        """Publish a get request to a TRV via Z2M to refresh state."""
        topic = f"{self._base_topic}/{trv_id}/get"
        # Z2M expects {"<attribute>": ""} to request specific attributes,
        # or just a get to the topic to refresh all.
        payload = attributes or {Z2M_ATTR_LOCAL_TEMPERATURE: ""}
        message = json.dumps(payload)
        await mqtt.async_publish(self.hass, topic, message)
        _LOGGER.debug("Published get to %s: %s", topic, message)

    # ── Schedule ───────────────────────────────────────────────────────

    async def async_set_weekly_schedule(
        self,
        trv_id: str,
        day_of_week: int,
        num_transitions: int,
        mode: int,
        transitions: list[tuple[int, int]],
    ) -> None:
        """Send SetWeeklySchedule command via Z2M.

        Z2M exposes the weekly_schedule attribute as a writable JSON object.
        The format expected by Z2M for Danfoss devices is a JSON payload
        with the schedule data sent through the set endpoint.
        """
        # Z2M expects schedule as a JSON object with day/transitions
        # Build the schedule payload in Z2M's expected format
        schedule_payload = {
            "weekly_schedule": {
                "dayofweek": day_of_week,
                "numoftrans": num_transitions,
                "mode": mode,
                "transitions": [
                    {"transitionTime": t[0], "heatSetpoint": t[1]} for t in transitions
                ],
            }
        }
        await self._async_set(trv_id, schedule_payload)
        _LOGGER.debug(
            "Set weekly schedule for TRV %s (dow=0x%02X, %d transitions)",
            trv_id,
            day_of_week,
            num_transitions,
        )

    async def async_get_weekly_schedule(
        self, trv_id: str, day_of_week: int
    ) -> list[tuple[int, int]] | None:
        """Request GetWeeklySchedule from TRV via Z2M.

        Z2M doesn't have a clean request/response model for this over MQTT.
        We send a get request and the response arrives as a state update.
        Returns None as we rely on state subscription for the response.
        """
        # Trigger a get request for the schedule
        await self._async_get(trv_id, {"weekly_schedule": ""})
        _LOGGER.debug(
            "Requested weekly schedule from TRV %s (dow=0x%02X)",
            trv_id,
            day_of_week,
        )
        # The response will come through the MQTT subscription
        # Callers should read the cached state after a short delay
        return None

    async def async_clear_weekly_schedule(self, trv_id: str) -> None:
        """Send ClearWeeklySchedule command via Z2M.

        Z2M exposes this through the clear_weekly_schedule attribute.
        """
        await self._async_set(trv_id, {"clear_weekly_schedule": ""})
        _LOGGER.debug("Cleared weekly schedule for TRV %s", trv_id)

    async def async_set_programming_mode(self, trv_id: str, mode: int) -> None:
        """Write Thermostat Programming Operation Mode via Z2M.

        Z2M expects string values for this attribute (e.g. "setpoint",
        "schedule", "schedule_with_preheat") rather than raw ZCL integers.
        """
        z2m_value = _PROGRAMMING_MODE_TO_Z2M.get(mode)
        if z2m_value is None:
            _LOGGER.warning(
                "Unknown programming mode %d for TRV %s, skipping", mode, trv_id
            )
            return
        await self._async_set(trv_id, {Z2M_ATTR_PROGRAMMING_MODE: z2m_value})
        _LOGGER.debug(
            "Set programming mode for TRV %s: %d (%s)", trv_id, mode, z2m_value
        )

    async def async_read_sw_error_code(self, trv_id: str) -> str | None:
        """Read SW Error Code via Z2M.

        Requests the system_status_code attribute from the TRV and returns
        the cached string value (e.g. "invalid_clock_information" or "ok").
        Requires zigbee-herdsman-converters >= 26.41.0.
        """
        await self._async_get(trv_id, {"system_status_code": ""})
        state = self._trv_states.get(trv_id)
        if state and state.raw:
            sw_error = state.raw.get("system_status_code")
            if sw_error is not None:
                return str(sw_error)
        return None
