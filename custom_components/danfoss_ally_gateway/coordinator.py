"""Room coordinator for Danfoss Ally Gateway.

Implements per-room coordination logic:
- TRV state subscription and aggregation
- Room state computation (temperature, demand, window, availability)
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)

from custom_components.danfoss_ally_gateway.const import (
    CONF_ROOM_NAME,
    CONF_TEMP_SENSOR,
    CONF_TRV_ENTITIES,
    EXT_TEMP_CHANGE_THRESHOLD,
    EXT_TEMP_COVERED_MAX_INTERVAL,
    EXT_TEMP_COVERED_MIN_INTERVAL,
    EXT_TEMP_EXPOSED_MAX_INTERVAL,
    EXT_TEMP_EXPOSED_MIN_INTERVAL,
    EXTERNAL_TEMP_DISABLED,
    TRV_AVAILABILITY_TIMEOUT,
    WINDOW_OPEN_DETECTED,
)

from .backend import DanfossBackend, TRVState
from .backend.z2m import Z2MBackend

_LOGGER = logging.getLogger(__name__)


@dataclass
class ExtTempTRVState:
    """Per-TRV tracking state for external temperature forwarding."""

    covered: bool = False  # from TRVState.radiator_covered
    last_temp_sent: float | None = None
    last_send_time: float = 0.0
    timer: CALLBACK_TYPE | None = None  # per-TRV max-interval resend timer


@dataclass
class RoomState:
    """Aggregated state for a room, derived from TRV states."""

    room_name: str = ""
    current_temperature: float | None = None  # From ext sensor or TRV average
    target_temperature: float | None = None  # Room setpoint (synced)
    max_pi_heating_demand: int = 0
    heat_required: bool = False
    heat_available: bool | None = None
    window_open: bool = False
    load_room_mean: int | None = None
    available: bool = False  # True when at least one TRV has reported recently
    trv_states: dict[str, TRVState] = field(default_factory=dict)


# Type for room state update listeners
RoomStateCallback = Callable[[RoomState], None]


class RoomCoordinator:
    """Coordinates all gateway logic for a single room.

    One instance is created per room subentry. It manages:
    - Subscribing to TRV state changes via the backend
    - Aggregating TRV state into a room-level view
    """

    def __init__(
        self,
        hass: HomeAssistant,
        backend: DanfossBackend,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the room coordinator."""
        self.hass = hass
        self._backend = backend

        # Room configuration from subentry
        self._room_name: str = subentry_data[CONF_ROOM_NAME]
        self._trv_ids: list[str] = subentry_data[CONF_TRV_ENTITIES]
        self._temp_sensor_id: str = subentry_data.get(CONF_TEMP_SENSOR, "")

        # Current room state
        self.state = RoomState(room_name=self._room_name)

        # Room state listeners (for entities to subscribe)
        self._state_callbacks: list[RoomStateCallback] = []

        # Cleanup callbacks
        self._unsub_callbacks: list[CALLBACK_TYPE] = []

        # TRV availability tracking: last update time per TRV
        self._last_trv_update_time: dict[str, float] = {}

        # Per-TRV external temperature tracking
        self._ext_temp_trv: dict[str, ExtTempTRVState] = {
            trv_id: ExtTempTRVState() for trv_id in self._trv_ids
        }

    @property
    def room_name(self) -> str:
        """Return the room name."""
        return self._room_name

    @property
    def trv_ids(self) -> list[str]:
        """Return the list of TRV IDs in this room."""
        return self._trv_ids

    def register_state_callback(
        self, callback: RoomStateCallback
    ) -> Callable[[], None]:
        """Register a callback for room state changes. Returns unregister callable."""
        self._state_callbacks.append(callback)

        def _unregister() -> None:
            self._state_callbacks.remove(callback)

        return _unregister

    def _notify_state_update(self) -> None:
        """Notify all listeners of a room state change."""
        for cb in self._state_callbacks:
            cb(self.state)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def _resolve_trv_id(self, trv_id: str) -> str:
        """Resolve a device registry ID to a backend-specific TRV identifier.

        For Z2M: returns the device name (= Z2M friendly name = MQTT topic).
        For ZHA: returns the climate entity_id for the device.
        Falls back to returning trv_id unchanged for backwards compatibility
        with subentries that stored friendly names or entity IDs directly.
        """
        device_reg = dr.async_get(self.hass)
        device = device_reg.async_get(trv_id)
        if device is None:
            # Not a device registry ID — assume it's already a resolved
            # identifier (backwards compat: old subentries stored friendly
            # names for Z2M or entity IDs for ZHA).
            return trv_id

        if isinstance(self._backend, Z2MBackend):
            # Z2M: device.name is the Z2M friendly name (MQTT topic segment).
            # Do NOT use name_by_user — HA-side renames don't change the
            # Z2M topic.
            return device.name or trv_id

        # ZHA Implementation would go here (not implemented yet)

        _LOGGER.warning(
            "No climate entity found for device %s, using device ID as-is",
            trv_id,
        )
        return trv_id

    async def async_setup(self) -> None:
        """Set up the room coordinator."""
        _LOGGER.info("Setting up room coordinator for '%s'", self._room_name)

        # Resolve device registry IDs to backend-specific identifiers
        self._trv_ids = [self._resolve_trv_id(tid) for tid in self._trv_ids]

        # Rebuild per-TRV ext temp tracking with resolved IDs
        self._ext_temp_trv = {trv_id: ExtTempTRVState() for trv_id in self._trv_ids}

        # Subscribe to TRV state changes
        unsub = self._backend.register_state_callback(self._handle_trv_state_update)
        self._unsub_callbacks.append(unsub)

        # Subscribe to each TRV
        for trv_id in self._trv_ids:
            await self._backend.async_subscribe_trv(trv_id)

        # Listen to external temperature sensor
        if self._temp_sensor_id:
            unsub = async_track_state_change_event(
                self.hass, [self._temp_sensor_id], self._handle_temp_sensor_change
            )
            self._unsub_callbacks.append(unsub)
            await self._async_send_initial_ext_temp()

    async def async_teardown(self) -> None:
        """Tear down the room coordinator."""
        _LOGGER.info("Tearing down room coordinator for '%s'", self._room_name)

        # Cancel all per-TRV ext temp timers
        for ext_state in self._ext_temp_trv.values():
            if ext_state.timer is not None:
                ext_state.timer()
                ext_state.timer = None

        # Unsubscribe from everything
        for unsub in self._unsub_callbacks:
            unsub()
        self._unsub_callbacks.clear()

        # Unsubscribe from TRVs
        for trv_id in self._trv_ids:
            await self._backend.async_unsubscribe_trv(trv_id)

        # Disable external temp on TRVs (send -8000)
        if self._temp_sensor_id:
            for trv_id in self._trv_ids:
                try:
                    await self._backend.async_set_external_temperature(
                        trv_id, EXTERNAL_TEMP_DISABLED / 100
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "Failed to disable ext temp on %s during teardown", trv_id
                    )

    # ── TRV State Handling ─────────────────────────────────────────────

    @callback
    def _handle_trv_state_update(self, trv_id: str, trv_state: TRVState) -> None:
        """Handle a TRV state update from the backend."""
        if trv_id not in self._trv_ids:
            return  # Not one of our TRVs

        self.state.trv_states[trv_id] = trv_state

        # Track last update time for availability
        self._last_trv_update_time[trv_id] = time.monotonic()

        # Update aggregated room state
        self._update_room_state()

        # Update per-TRV radiator_covered from TRV-reported state
        if trv_state.radiator_covered is not None:
            self._ext_temp_trv[trv_id].covered = trv_state.radiator_covered

        self._notify_state_update()

    def _update_room_state(self) -> None:
        """Recalculate aggregated room state from TRV states."""
        states = self.state.trv_states

        if not states:
            return  # No TRV states yet

        # Current temperature: average of TRV local temperatures
        temps = [
            s.local_temperature
            for s in states.values()
            if s.local_temperature is not None
        ]
        if temps:
            self.state.current_temperature = sum(temps) / len(temps)

        # Target temperature: use the first TRV's setpoint (in configured order)
        for trv_id in self._trv_ids:
            trv = states.get(trv_id)
            if trv is not None and trv.occupied_heating_setpoint is not None:
                self.state.target_temperature = trv.occupied_heating_setpoint
                break

        # Pi heating demand: max accross all TRVs
        demands = [
            s.pi_heating_demand
            for s in states.values()
            if s.pi_heating_demand is not None
        ]
        self.state.max_pi_heating_demand = max(demands) if demands else 0

        # Heat required: any TRV reports heat_required (0x4031 Heat Supply Request)
        self.state.heat_required = any(s.heat_required is True for s in states.values())

        # Window open: any TRV has window_open_detection >= 3
        self.state.window_open = any(
            s.window_open_detection is not None
            and s.window_open_detection >= WINDOW_OPEN_DETECTED
            for s in states.values()
        )

        # Availability: room is available if at least one TRV has reported
        # within the staleness timeout
        now = time.monotonic()
        self.state.available = any(
            (now - self._last_trv_update_time.get(trv_id, 0.0))
            < TRV_AVAILABILITY_TIMEOUT
            for trv_id in self._trv_ids
        )

    # ── External Temperature Forwarding ────────────────────────────────

    async def _async_send_initial_ext_temp(self) -> None:
        """Send initial external temperature on setup."""
        if not self._temp_sensor_id:
            return

        sensor_state = self.hass.states.get(self._temp_sensor_id)
        if sensor_state and sensor_state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            try:
                temp = float(sensor_state.state)
                await self._async_send_ext_temp_all(temp)
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid initial temperature from %s: %s",
                    self._temp_sensor_id,
                    sensor_state.state,
                )

    @callback
    def _handle_temp_sensor_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle external temperature sensor state change."""
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        try:
            new_temp = float(new_state.state)
        except (ValueError, TypeError):
            return

        now = time.monotonic()

        for trv_id, ext_state in self._ext_temp_trv.items():
            if ext_state.last_temp_sent is not None:
                delta = abs(new_temp - ext_state.last_temp_sent)
                if delta < EXT_TEMP_CHANGE_THRESHOLD:
                    continue

            min_interval = (
                EXT_TEMP_COVERED_MIN_INTERVAL
                if ext_state.covered
                else EXT_TEMP_EXPOSED_MIN_INTERVAL
            )
            elapsed = now - ext_state.last_send_time

            if elapsed < min_interval:
                if ext_state.timer is None:
                    delay = min_interval - elapsed
                    self._schedule_deferred_ext_temp_send(trv_id, new_temp, delay)
                continue

            self.hass.async_create_task(
                self._async_send_ext_temp_to_trv(trv_id, new_temp)
            )

    def _schedule_deferred_ext_temp_send(
        self, trv_id: str, temperature: float, delay: float
    ) -> None:
        """Schedule a deferred ext temp send for a single TRV."""
        ext_state = self._ext_temp_trv[trv_id]

        @callback
        def _delayed_send(_now: Any) -> None:
            ext_state.timer = None
            self.hass.async_create_task(
                self._async_send_ext_temp_to_trv(trv_id, temperature)
            )

        ext_state.timer = async_call_later(self.hass, delay, _delayed_send)

    async def _async_send_ext_temp_to_trv(
        self, trv_id: str, temperature: float
    ) -> None:
        """Send external temperature to a single TRV and update its tracking."""
        _LOGGER.debug(
            "Sending external temp %.1f°C to TRV %s in room '%s'",
            temperature,
            trv_id,
            self._room_name,
        )

        try:
            await self._backend.async_set_external_temperature(trv_id, temperature)
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Failed to send ext temp to TRV %s in room '%s'",
                trv_id,
                self._room_name,
            )
            return

        ext_state = self._ext_temp_trv[trv_id]
        ext_state.last_temp_sent = temperature
        ext_state.last_send_time = time.monotonic()

        self._schedule_ext_temp_max_interval(trv_id, temperature)

    async def _async_send_ext_temp_all(self, temperature: float) -> None:
        """Send external temperature to all room TRVs."""
        _LOGGER.debug(
            "Sending external temp %.1f°C to room '%s'",
            temperature,
            self._room_name,
        )
        for trv_id in self._trv_ids:
            await self._async_send_ext_temp_to_trv(trv_id, temperature)

    def _schedule_ext_temp_max_interval(self, trv_id: str, temperature: float) -> None:
        """Schedule a resend at the max interval to prevent TRV timeout."""
        ext_state = self._ext_temp_trv[trv_id]

        if ext_state.timer is not None:
            ext_state.timer()
            ext_state.timer = None

        max_interval = (
            EXT_TEMP_COVERED_MAX_INTERVAL
            if ext_state.covered
            else EXT_TEMP_EXPOSED_MAX_INTERVAL
        )

        @callback
        def _resend(_now: Any) -> None:
            ext_state.timer = None
            if self._temp_sensor_id:
                sensor_state = self.hass.states.get(self._temp_sensor_id)
                if sensor_state and sensor_state.state not in (
                    STATE_UNAVAILABLE,
                    STATE_UNKNOWN,
                ):
                    try:
                        current_temp = float(sensor_state.state)
                    except (ValueError, TypeError):
                        current_temp = temperature
                else:
                    current_temp = temperature
            else:
                current_temp = temperature
            self.hass.async_create_task(
                self._async_send_ext_temp_to_trv(trv_id, current_temp)
            )

        ext_state.timer = async_call_later(self.hass, max_interval, _resend)

    # ── Setpoint control ──────────────────────────────────────────────

    async def async_set_room_temperature(self, temperature: float) -> None:
        """Set the target temperature for all TRVs in the room."""
        for trv_id in self._trv_ids:
            await self._backend.async_set_occupied_heating_setpoint(trv_id, temperature)
