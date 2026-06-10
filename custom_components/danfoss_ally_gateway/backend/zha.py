"""ZHA backend for Danfoss Ally TRV communication.

Communicates with Danfoss Ally TRVs via ZHA service calls for
manufacturer-specific Zigbee cluster attributes.
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event

from ..const import (
    ATTR_EXTERNAL_MEASURED_ROOM_SENSOR,
    ATTR_EXTERNAL_WINDOW_OPEN,
    ATTR_HEAT_AVAILABLE,
    ATTR_LOAD_BALANCING_ENABLE,
    ATTR_LOAD_ROOM_MEAN,
    ATTR_OCCUPIED_HEATING_SETPOINT,
    ATTR_SW_ERROR_CODE,
    ATTR_THERMOSTAT_PROGRAMMING_MODE,
    CLUSTER_DIAGNOSTICS,
    CLUSTER_THERMOSTAT,
    CLUSTER_TIME,
    CMD_PREHEAT_COMMAND,
    DANFOSS_ALLY_SYSTEM_STATUS_CODES,
    DANFOSS_MANUFACTURER_CODE,
    EXTERNAL_TEMP_DISABLED,
)
from . import DanfossBackend, TRVState

try:
    from homeassistant.components.zha.helpers import (
        get_zha_gateway,
    )
    from zigpy.types.named import EUI64
except ImportError:  # ZHA not installed
    get_zha_gateway = None  # type: ignore[assignment, misc]
    EUI64 = None  # type: ignore[assignment, misc]

_LOGGER = logging.getLogger(__name__)


class ZHABackend(DanfossBackend):
    """ZHA service call-based backend for Danfoss Ally TRVs.

    TRV IDs are HA climate entity IDs (e.g., ``climate.living_room_trv``).
    Uses ``zha.set_zigbee_cluster_attribute`` for manufacturer-specific writes
    and reads state from ZHA entity attributes/state changes.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the ZHA backend."""
        super().__init__(hass)
        self._subscriptions: dict[str, Any] = {}  # entity_id -> unsubscribe
        self._trv_states: dict[str, TRVState] = {}
        self._unsub_dispatcher: Callable[[], None] | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        """Set up the ZHA backend."""
        _LOGGER.debug("ZHA backend setup")
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass, "zha_gateway_message", self._handle_zha_gateway_message
        )

    async def async_teardown(self) -> None:
        """Tear down the ZHA backend."""
        if self._unsub_dispatcher is not None:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None
        for entity_id in list(self._subscriptions):
            await self.async_unsubscribe_trv(entity_id)
        self._trv_states.clear()
        _LOGGER.debug("ZHA backend teardown complete")

    @callback
    def _handle_zha_gateway_message(self, data: dict) -> None:
        """Handle ZHA gateway dispatcher messages (device_joined = announce)."""
        if data.get("type") != "device_joined":
            return
        ieee = data.get("device_info", {}).get("ieee")
        if not ieee:
            return
        trv_id = self._find_trv_by_ieee(ieee)
        if trv_id is not None:
            _LOGGER.debug(
                "Device announce for subscribed TRV: %s (ieee=%s)", trv_id, ieee
            )
            self._fire_device_announce(trv_id)

    def _find_trv_by_ieee(self, ieee: str) -> str | None:
        """Find the trv_id (entity_id) for a given IEEE address."""
        entity_registry = er.async_get(self.hass)
        for trv_id in self._subscriptions:
            entry = entity_registry.async_get(trv_id)
            if entry is None:
                continue
            entry_ieee = (
                entry.unique_id.split("-")[0]
                if "-" in entry.unique_id
                else entry.unique_id
            )
            if entry_ieee.lower() == ieee.lower():
                return trv_id
        return None

    # ── Subscriptions ──────────────────────────────────────────────────

    async def async_subscribe_trv(self, trv_id: str) -> None:
        """Subscribe to state changes for a ZHA climate entity."""
        if trv_id in self._subscriptions:
            return

        @callback
        def _handle_state_change(event: Event[EventStateChangedData]) -> None:
            """Handle ZHA entity state change."""
            new_state = event.data.get("new_state")
            if new_state is None or new_state.state in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                return

            trv_state = self._parse_zha_state(trv_id, new_state)
            self._trv_states[trv_id] = trv_state
            self._fire_state_update(trv_id, trv_state)

        unsub = async_track_state_change_event(
            self.hass, [trv_id], _handle_state_change
        )
        self._subscriptions[trv_id] = unsub
        _LOGGER.debug("Subscribed to ZHA entity: %s", trv_id)

        # Read initial state
        state = self.hass.states.get(trv_id)
        if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            trv_state = self._parse_zha_state(trv_id, state)
            self._trv_states[trv_id] = trv_state

    async def async_unsubscribe_trv(self, trv_id: str) -> None:
        """Unsubscribe from a ZHA entity."""
        unsub = self._subscriptions.pop(trv_id, None)
        if unsub is not None:
            unsub()
            _LOGGER.debug("Unsubscribed from ZHA entity: %s", trv_id)
        self._trv_states.pop(trv_id, None)

    async def async_get_trv_state(self, trv_id: str) -> TRVState | None:
        """Return cached TRV state."""
        return self._trv_states.get(trv_id)

    @staticmethod
    def _parse_zha_state(entity_id: str, state: Any) -> TRVState:
        """Parse ZHA entity state into TRVState."""
        attrs = state.attributes if state else {}
        return TRVState(
            entity_id=entity_id,
            local_temperature=attrs.get("current_temperature"),
            occupied_heating_setpoint=attrs.get("temperature"),
            pi_heating_demand=attrs.get("pi_heating_demand"),
            heat_required=attrs.get("heat_required"),
            load_estimate=attrs.get("load_estimate"),
            load_balancing_enable=attrs.get("load_balancing_enable"),
            heat_available=attrs.get("heat_available"),
            preheat_status=attrs.get("preheat_status"),
            preheat_time=attrs.get("preheat_time"),
            window_open_detection=attrs.get("window_open_internal"),
            external_window_open=attrs.get("window_open_external"),
            setpoint_change_source=attrs.get("setpoint_change_source"),
            radiator_covered=attrs.get("radiator_covered"),
            raw=dict(attrs),
        )

    # ── ZHA cluster attribute helper ───────────────────────────────────

    async def _async_set_cluster_attribute(
        self,
        trv_id: str,
        cluster_id: int,
        attribute: int,
        value: Any,
        manufacturer: int | None = DANFOSS_MANUFACTURER_CODE,
    ) -> None:
        """Write a Zigbee cluster attribute via ZHA service call."""
        entity_registry = er.async_get(self.hass)
        entry = entity_registry.async_get(trv_id)
        if entry is None:
            _LOGGER.error("ZHA entity not found: %s", trv_id)
            return

        ieee = (
            entry.unique_id.split("-")[0] if "-" in entry.unique_id else entry.unique_id
        )

        service_data: dict[str, Any] = {
            "ieee": ieee,
            "endpoint_id": 1,
            "cluster_id": cluster_id,
            "cluster_type": "in",
            "attribute": attribute,
            "value": value,
        }
        if manufacturer is not None:
            service_data["manufacturer"] = manufacturer

        await self.hass.services.async_call(
            "zha",
            "set_zigbee_cluster_attribute",
            service_data,
            blocking=True,
        )
        _LOGGER.debug(
            "ZHA set attribute 0x%04X=0x%04X on %s (ieee=%s): %s",
            cluster_id,
            attribute,
            trv_id,
            ieee,
            value,
        )

    # ── Attribute writes ───────────────────────────────────────────────

    async def async_set_external_temperature(
        self, trv_id: str, temperature: float
    ) -> None:
        """Write external measured room sensor to TRV via ZHA."""
        if temperature <= -80.0:
            raw_value = EXTERNAL_TEMP_DISABLED
        else:
            raw_value = int(temperature * 100)

        await self._async_set_cluster_attribute(
            trv_id,
            CLUSTER_THERMOSTAT,
            ATTR_EXTERNAL_MEASURED_ROOM_SENSOR,
            raw_value,
        )

    async def async_set_occupied_heating_setpoint(
        self, trv_id: str, temperature: float
    ) -> None:
        """Write OccupiedHeatingSetpoint to TRV."""
        raw_value = int(temperature * 100)
        await self._async_set_cluster_attribute(
            trv_id,
            CLUSTER_THERMOSTAT,
            ATTR_OCCUPIED_HEATING_SETPOINT,
            raw_value,
            manufacturer=None,  # Standard attribute, no manufacturer code
        )

    async def async_set_heat_available(self, trv_id: str, available: bool) -> None:
        """Write HeatAvailable to TRV."""
        await self._async_set_cluster_attribute(
            trv_id,
            CLUSTER_THERMOSTAT,
            ATTR_HEAT_AVAILABLE,
            available,
        )

    async def async_set_load_room_mean(self, trv_id: str, value: int) -> None:
        """Write LoadRoomMean to TRV."""
        await self._async_set_cluster_attribute(
            trv_id,
            CLUSTER_THERMOSTAT,
            ATTR_LOAD_ROOM_MEAN,
            value,
        )

    async def async_set_load_balancing_enable(self, trv_id: str, enable: bool) -> None:
        """Write LoadBalancingEnable to TRV."""
        await self._async_set_cluster_attribute(
            trv_id,
            CLUSTER_THERMOSTAT,
            ATTR_LOAD_BALANCING_ENABLE,
            enable,
        )

    async def async_set_external_window_open(self, trv_id: str, is_open: bool) -> None:
        """Write ExternalOpenWindowDetected to TRV."""
        await self._async_set_cluster_attribute(
            trv_id,
            CLUSTER_THERMOSTAT,
            ATTR_EXTERNAL_WINDOW_OPEN,
            is_open,
        )

    # ── Commands ───────────────────────────────────────────────────────

    async def async_send_setpoint_command(
        self, trv_id: str, temperature: float, command_type: int
    ) -> None:
        """Send SetpointCommand via ZHA.

        For Type 0 (gentle): write OccupiedHeatingSetpoint directly.
        For Type 1 (aggressive): ideally use the cluster command, but
        ZHA may not expose it directly, so we fall back to setpoint write.
        """
        raw_value = int(temperature * 100)
        await self._async_set_cluster_attribute(
            trv_id,
            CLUSTER_THERMOSTAT,
            ATTR_OCCUPIED_HEATING_SETPOINT,
            raw_value,
            manufacturer=None,
        )

    async def async_send_preheat_command(self, trv_id: str, timestamp: int) -> None:
        """Send PreHeatCommand (0x42) to force TRV to preheat to a timestamp.

        Per Danfoss spec (AU417130778872en-000102, §3.2):
        Command 0x42 on cluster 0x0201 (hvacThermostat) with parameters:
          - enum8 = 0x00 (force preheat)
          - uint32 = timestamp from source TRV's preheat_time attribute
        """
        await self._async_issue_cluster_command(
            trv_id,
            CLUSTER_THERMOSTAT,
            CMD_PREHEAT_COMMAND,
            [0x00, timestamp],
            manufacturer=DANFOSS_MANUFACTURER_CODE,
        )

    async def async_sync_time(self, trv_id: str) -> None:
        """Synchronize time cluster attributes to TRV via ZHA.

        Writes all 6 time cluster attributes as per Danfoss spec:
        Time, TimeStatus, TimeZone, DstStart, DstEnd, DstShift.
        """
        now = datetime.now(UTC)

        # Zigbee epoch is 2000-01-01 00:00:00 UTC
        zigbee_epoch = datetime(2000, 1, 1, tzinfo=UTC)
        zigbee_time = int((now - zigbee_epoch).total_seconds())

        # Time status: bit 0 = master, bit 1 = synchronized
        time_status = 0x03  # Master clock, synchronized

        # Timezone offset in seconds from UTC
        tz_offset = -_time.timezone if not _time.daylight else -_time.altzone

        # DST attributes
        dst_start = 0x00000000  # No DST by default
        dst_end = 0x00000000
        dst_shift = 0

        if _time.daylight:
            dst_shift = _time.timezone - _time.altzone
            year = now.year
            import calendar as _calendar

            last_is_dst = False
            for month in range(1, 13):
                days_in_month = _calendar.monthrange(year, month)[1]
                for day in (1, days_in_month):
                    local_time = _time.mktime((year, month, day, 12, 0, 0, 0, 0, -1))
                    is_dst = _time.localtime(local_time).tm_isdst
                    if month == 1 and day == 1:
                        last_is_dst = is_dst
                        continue
                    if is_dst and not last_is_dst:
                        dt = datetime(year, month, day, tzinfo=UTC)
                        dst_start = int((dt - zigbee_epoch).total_seconds())
                    elif not is_dst and last_is_dst:
                        dt = datetime(year, month, day, tzinfo=UTC)
                        dst_end = int((dt - zigbee_epoch).total_seconds())
                    last_is_dst = is_dst

        _LOGGER.debug(
            "Syncing time to ZHA TRV %s: zigbee_time=%d, tz_offset=%d, "
            "dst_start=%d, dst_end=%d, dst_shift=%d",
            trv_id,
            zigbee_time,
            tz_offset,
            dst_start,
            dst_end,
            dst_shift,
        )

        time_attrs = [
            (0x0000, zigbee_time),  # Time
            (0x0001, time_status),  # TimeStatus
            (0x0002, tz_offset),  # TimeZone
            (0x0003, dst_start),  # DstStart
            (0x0004, dst_end),  # DstEnd
            (0x0005, dst_shift),  # DstShift
        ]

        for attr_id, value in time_attrs:
            try:
                await self._async_set_cluster_attribute(
                    trv_id,
                    CLUSTER_TIME,
                    attr_id,
                    value,
                    manufacturer=None,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to write time attribute 0x%04X to %s", attr_id, trv_id
                )

    # ── Schedule ───────────────────────────────────────────────────────

    async def _async_issue_cluster_command(
        self,
        trv_id: str,
        cluster_id: int,
        command: int,
        args: list[Any],
        manufacturer: int | None = None,
    ) -> dict[str, Any] | None:
        """Issue a Zigbee cluster command via ZHA service call."""
        entity_registry = er.async_get(self.hass)
        entry = entity_registry.async_get(trv_id)
        if entry is None:
            _LOGGER.error("ZHA entity not found: %s", trv_id)
            return None

        ieee = (
            entry.unique_id.split("-")[0] if "-" in entry.unique_id else entry.unique_id
        )

        service_data: dict[str, Any] = {
            "ieee": ieee,
            "endpoint_id": 1,
            "cluster_id": cluster_id,
            "cluster_type": "in",
            "command": command,
            "args": args,
        }
        if manufacturer is not None:
            service_data["manufacturer"] = manufacturer

        await self.hass.services.async_call(
            "zha",
            "issue_zigbee_cluster_command",
            service_data,
            blocking=True,
        )
        _LOGGER.debug(
            "ZHA cluster command 0x%04X cmd 0x%02X on %s (ieee=%s): %s",
            cluster_id,
            command,
            trv_id,
            ieee,
            args,
        )
        return None

    async def _async_read_cluster_attribute(
        self,
        trv_id: str,
        cluster_id: int,
        attribute: int,
        manufacturer: int | None = DANFOSS_MANUFACTURER_CODE,
    ) -> Any:
        """Read a Zigbee cluster attribute via the ZHA gateway."""
        entity_registry = er.async_get(self.hass)
        entry = entity_registry.async_get(trv_id)
        if entry is None:
            _LOGGER.error("ZHA entity not found: %s", trv_id)
            return None

        ieee_str = (
            entry.unique_id.split("-")[0] if "-" in entry.unique_id else entry.unique_id
        )

        _get_gateway = get_zha_gateway
        if _get_gateway is None:
            _LOGGER.debug("ZHA integration not available for attribute read")
            return None

        try:
            zha_gw = _get_gateway(self.hass)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("ZHA gateway not available for attribute read")
            return None

        if EUI64 is None:
            return None

        try:
            ieee = EUI64.convert(ieee_str)
        except (ValueError, TypeError):
            _LOGGER.error("Invalid IEEE address for %s: %s", trv_id, ieee_str)
            return None

        zha_device = zha_gw.get_device(ieee)
        if zha_device is None:
            _LOGGER.debug("ZHA device not found for %s (ieee=%s)", trv_id, ieee_str)
            return None

        cluster = zha_device.async_get_cluster(
            endpoint_id=1, cluster_id=cluster_id, cluster_type="in"
        )
        if cluster is None:
            _LOGGER.debug("Cluster 0x%04X not found on %s", cluster_id, trv_id)
            return None

        mfr = manufacturer if manufacturer is not None else None
        success, failure = await cluster.read_attributes(
            [attribute], allow_cache=False, only_cache=False, manufacturer=mfr
        )

        if failure:
            _LOGGER.debug(
                "Failed to read attribute 0x%04X from cluster 0x%04X on %s: %s",
                attribute,
                cluster_id,
                trv_id,
                failure,
            )
            return None

        value = success.get(attribute)
        _LOGGER.debug(
            "ZHA read attribute 0x%04X:0x%04X on %s = %s",
            cluster_id,
            attribute,
            trv_id,
            value,
        )
        return value

    async def _async_write_cluster_attribute(
        self,
        trv_id: str,
        cluster_id: int,
        attribute: int,
        value: Any,
        manufacturer: int | None = DANFOSS_MANUFACTURER_CODE,
    ) -> None:
        """Write a Zigbee cluster attribute via the ZHA gateway."""
        entity_registry = er.async_get(self.hass)
        entry = entity_registry.async_get(trv_id)
        if entry is None:
            _LOGGER.error("ZHA entity not found: %s", trv_id)
            return

        ieee_str = (
            entry.unique_id.split("-")[0] if "-" in entry.unique_id else entry.unique_id
        )

        _get_gateway = get_zha_gateway
        if _get_gateway is None:
            _LOGGER.debug("ZHA integration not available for attribute write")
            return

        try:
            zha_gw = _get_gateway(self.hass)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("ZHA gateway not available for attribute write")
            return

        if EUI64 is None:
            return

        try:
            ieee = EUI64.convert(ieee_str)
        except (ValueError, TypeError):
            _LOGGER.error("Invalid IEEE address for %s: %s", trv_id, ieee_str)
            return

        zha_device = zha_gw.get_device(ieee)
        if zha_device is None:
            _LOGGER.debug("ZHA device not found for %s (ieee=%s)", trv_id, ieee_str)
            return

        cluster = zha_device.async_get_cluster(
            endpoint_id=1, cluster_id=cluster_id, cluster_type="in"
        )
        if cluster is None:
            _LOGGER.debug("Cluster 0x%04X not found on %s", cluster_id, trv_id)
            return

        mfr = manufacturer if manufacturer is not None else None
        result = await cluster.write_attributes({attribute: value}, manufacturer=mfr)

        _LOGGER.debug(
            "ZHA write attribute 0x%04X:0x%04X on %s = %s, result: %s",
            cluster_id,
            attribute,
            trv_id,
            value,
            result,
        )

    async def async_set_weekly_schedule(
        self,
        trv_id: str,
        day_of_week: int,
        num_transitions: int,
        mode: int,
        transitions: list[tuple[int, int]],
    ) -> None:
        """Send SetWeeklySchedule (0x01) via ZHA cluster command."""
        args: list[int] = [num_transitions, day_of_week, mode]
        for minutes, setpoint in transitions:
            args.append(minutes)
            args.append(setpoint)

        await self._async_issue_cluster_command(
            trv_id,
            CLUSTER_THERMOSTAT,
            0x01,  # SetWeeklySchedule
            args,
        )
        _LOGGER.debug(
            "Set weekly schedule for ZHA TRV %s (dow=0x%02X, %d transitions)",
            trv_id,
            day_of_week,
            num_transitions,
        )

    async def async_get_weekly_schedule(
        self, trv_id: str, day_of_week: int
    ) -> list[tuple[int, int]] | None:
        """Send GetWeeklySchedule (0x02) via ZHA.

        Returns None; callers should listen for the response event.
        """
        args = [day_of_week, 0x01]  # days_to_return, mode (heat)
        await self._async_issue_cluster_command(
            trv_id,
            CLUSTER_THERMOSTAT,
            0x02,  # GetWeeklySchedule
            args,
        )
        _LOGGER.debug(
            "Requested weekly schedule from ZHA TRV %s (dow=0x%02X)",
            trv_id,
            day_of_week,
        )
        return None

    async def async_clear_weekly_schedule(self, trv_id: str) -> None:
        """Send ClearWeeklySchedule (0x03) via ZHA."""
        await self._async_issue_cluster_command(
            trv_id,
            CLUSTER_THERMOSTAT,
            0x03,  # ClearWeeklySchedule
            [],
        )
        _LOGGER.debug("Cleared weekly schedule for ZHA TRV %s", trv_id)

    async def async_set_programming_mode(self, trv_id: str, mode: int) -> None:
        """Write Thermostat Programming Operation Mode (0x0025) via ZHA."""
        await self._async_set_cluster_attribute(
            trv_id,
            CLUSTER_THERMOSTAT,
            ATTR_THERMOSTAT_PROGRAMMING_MODE,
            mode,
            manufacturer=None,  # Standard attribute
        )
        _LOGGER.debug("Set programming mode for ZHA TRV %s: %d", trv_id, mode)

    async def async_read_sw_error_code(self, trv_id: str) -> str | None:
        """Read SW Error Code from Diagnostics cluster via ZHA.

        Reads attribute 0x4000 (danfossSystemStatusCode) from the
        Diagnostics cluster (0x0B05) and decodes the BITMAP16 to a
        comma-separated string of active error names.
        """
        value = await self._async_read_cluster_attribute(
            trv_id,
            CLUSTER_DIAGNOSTICS,
            ATTR_SW_ERROR_CODE,
        )
        if value is not None:
            try:
                code = int(value)
            except (ValueError, TypeError):
                return None
            errors: list[str] = []
            for bit, name in DANFOSS_ALLY_SYSTEM_STATUS_CODES.items():
                if code & (1 << bit):
                    errors.append(name)
            return ",".join(errors) if errors else "ok"
        return None
