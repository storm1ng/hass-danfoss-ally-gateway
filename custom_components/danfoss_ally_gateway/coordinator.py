"""Room coordinator for Danfoss Ally Gateway.

Implements per-room coordination logic:
- TRV state subscription and aggregation
- Room state computation (temperature, demand, window, availability)
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
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

from .backend import DanfossBackend, TRVState
from .backend.z2m import Z2MBackend
from .const import (
    CONF_HEAT_SOURCE,
    CONF_HEAT_SOURCE_TYPE,
    CONF_ROOM_NAME,
    CONF_TEMP_SENSOR,
    CONF_TRV_ENTITIES,
    EXT_TEMP_CHANGE_THRESHOLD,
    EXT_TEMP_COVERED_MAX_INTERVAL,
    EXT_TEMP_COVERED_MIN_INTERVAL,
    EXT_TEMP_EXPOSED_MAX_INTERVAL,
    EXT_TEMP_EXPOSED_MIN_INTERVAL,
    EXTERNAL_TEMP_DISABLED,
    HEAT_SOURCE_BINARY_SENSOR,
    HEAT_SOURCE_CLIMATE,
    LOAD_BALANCE_DISABLED_VALUE,
    LOAD_BALANCE_INTERVAL,
    LOAD_BALANCE_INVALID_THRESHOLD,
    LOAD_BALANCE_MAX_AGE,
    SETPOINT_SOURCE_MANUAL,
    SETPOINT_TYPE_USER,
    TRV_AVAILABILITY_TIMEOUT,
    WINDOW_OPEN_DETECTED,
    WINDOW_OPEN_EXTERNAL_OPEN,
    Z2M_ATTR_LOAD_ROOM_MEAN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ExtTempTRVState:
    """Per-TRV tracking state for external temperature forwarding."""

    covered: bool = False  # from TRVState.radiator_covered
    last_temp_sent: float | None = None
    last_send_time: float = 0.0
    timer: CALLBACK_TYPE | None = None  # per-TRV max-interval resend timer


@dataclass
class LoadEstimateEntry:
    """A timestamped load estimate from a TRV."""

    value: int
    timestamp: float  # time.monotonic()


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
        self._heat_source_id: str = subentry_data.get(CONF_HEAT_SOURCE, "")
        self._heat_source_type: str = subentry_data.get(CONF_HEAT_SOURCE_TYPE, "")

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

        # Load balancing tracking
        self._load_estimates: dict[str, LoadEstimateEntry] = {}
        self._load_balance_timer: CALLBACK_TYPE | None = None

        # Setpoint coordination
        self._setpoint_lock = asyncio.Lock()
        self._programmatic_setpoint: bool = False

        # Window coordination
        self._forced_window_open_trvs: set[str] = set()  # TRVs we forced open

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

        # Listen to heat source entity
        if self._heat_source_id:
            unsub = async_track_state_change_event(
                self.hass, [self._heat_source_id], self._handle_heat_source_change
            )
            self._unsub_callbacks.append(unsub)
            await self._async_update_heat_availability()

        # Start load balancing timer (only for multi-TRV rooms)
        if len(self._trv_ids) > 1:
            self._schedule_load_balance()

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
                except Exception:
                    _LOGGER.debug(
                        "Failed to disable ext temp on %s during teardown", trv_id
                    )

    # ── TRV State Handling ─────────────────────────────────────────────

    @callback
    def _handle_trv_state_update(self, trv_id: str, trv_state: TRVState) -> None:
        """Handle a TRV state update from the backend."""
        if trv_id not in self._trv_ids:
            return  # Not one of our TRVs

        old_state = self.state.trv_states.get(trv_id)
        self.state.trv_states[trv_id] = trv_state

        # Track last update time for availability
        self._last_trv_update_time[trv_id] = time.monotonic()

        # Update aggregated room state
        self._update_room_state()

        # Update per-TRV radiator_covered from TRV-reported state
        if trv_state.radiator_covered is not None:
            self._ext_temp_trv[trv_id].covered = trv_state.radiator_covered

        # Update load estimate tracking
        if trv_state.load_estimate is not None:
            self._load_estimates[trv_id] = LoadEstimateEntry(
                value=trv_state.load_estimate,
                timestamp=time.monotonic(),
            )

        # Seed load_room_mean from TRV-reported value if the coordinator
        # hasn't computed its own yet.
        if self.state.load_room_mean is None and len(self._trv_ids) > 1:
            raw_mean = trv_state.raw.get(Z2M_ATTR_LOAD_ROOM_MEAN)
            if isinstance(raw_mean, int | float):
                self.state.load_room_mean = int(raw_mean)
                _LOGGER.debug(
                    "Seeded load_room_mean from TRV %s: %d",
                    trv_id,
                    self.state.load_room_mean,
                )

        # Check for setpoint coordination (manual dial change)
        if old_state is not None:
            self.hass.async_create_task(
                self._async_check_setpoint_coordination(trv_id, old_state, trv_state)
            )

        # Check for window open coordination
        self.hass.async_create_task(
            self._async_check_window_coordination(trv_id, trv_state)
        )

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
        except Exception:
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

    # ── Heat Availability ──────────────────────────────────────────────

    @callback
    def _handle_heat_source_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle heat source entity state change."""
        self.hass.async_create_task(self._async_update_heat_availability())

    async def _async_update_heat_availability(self) -> None:
        """Read heat source entity and write heat_available to TRVs."""
        if not self._heat_source_id:
            return

        entity_state = self.hass.states.get(self._heat_source_id)
        if entity_state is None or entity_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            return

        heat_available: bool

        if self._heat_source_type == HEAT_SOURCE_CLIMATE:
            hvac_action = entity_state.attributes.get("hvac_action", "")
            heat_available = hvac_action == "heating"
        elif self._heat_source_type == HEAT_SOURCE_BINARY_SENSOR:
            heat_available = entity_state.state == STATE_ON
        else:
            if entity_state.domain == "climate":
                hvac_action = entity_state.attributes.get("hvac_action", "")
                heat_available = hvac_action == "heating"
            else:
                heat_available = entity_state.state == STATE_ON

        if self.state.heat_available == heat_available:
            return

        self.state.heat_available = heat_available
        _LOGGER.debug(
            "Heat available for room '%s': %s", self._room_name, heat_available
        )

        for trv_id in self._trv_ids:
            try:
                await self._backend.async_set_heat_available(trv_id, heat_available)
            except Exception:
                _LOGGER.exception(
                    "Failed to set heat_available on TRV %s in room '%s'",
                    trv_id,
                    self._room_name,
                )

        self._notify_state_update()

    # ── Setpoint Coordination ──────────────────────────────────────────

    async def _async_check_setpoint_coordination(
        self, trv_id: str, old_state: TRVState, new_state: TRVState
    ) -> None:
        """Check if a TRV's setpoint changed due to manual dial turn.

        Per Danfoss spec: When setpoint_change_source == 0x00 (manual),
        forward the new setpoint to other room TRVs as Type 1 (aggressive).
        """
        if self._programmatic_setpoint:
            return  # We're the ones writing, ignore echo

        new_setpoint = new_state.occupied_heating_setpoint
        old_setpoint = old_state.occupied_heating_setpoint

        if new_setpoint is None or new_setpoint == old_setpoint:
            return  # No change

        # Check if the change was manual
        if new_state.setpoint_change_source != SETPOINT_SOURCE_MANUAL:
            return

        _LOGGER.info(
            "Manual setpoint change on %s in room '%s': %.1f → %.1f°C",
            trv_id,
            self._room_name,
            old_setpoint or 0,
            new_setpoint,
        )

        # Forward to other TRVs as Type 1 (user interaction / aggressive)
        if len(self._trv_ids) > 1:
            async with self._setpoint_lock:
                self._programmatic_setpoint = True
                try:
                    for other_trv in self._trv_ids:
                        if other_trv == trv_id:
                            continue
                        try:
                            await self._backend.async_send_setpoint_command(
                                other_trv, new_setpoint, SETPOINT_TYPE_USER
                            )
                        except Exception:
                            _LOGGER.exception(
                                "Failed to forward setpoint to TRV %s", other_trv
                            )
                finally:
                    self._programmatic_setpoint = False

        # Update room state
        self.state.target_temperature = new_setpoint
        self._notify_state_update()

    async def async_set_room_temperature(self, temperature: float) -> None:
        """Set target temperature for the entire room (from climate entity).

        Uses OccupiedHeatingSetpoint write (Type 0 / gentle motor).
        """
        _LOGGER.debug(
            "Setting room '%s' temperature to %.1f°C", self._room_name, temperature
        )

        async with self._setpoint_lock:
            self._programmatic_setpoint = True
            try:
                for trv_id in self._trv_ids:
                    try:
                        await self._backend.async_set_occupied_heating_setpoint(
                            trv_id, temperature
                        )
                    except Exception:
                        _LOGGER.exception("Failed to set setpoint on TRV %s", trv_id)
            finally:
                self._programmatic_setpoint = False

        self.state.target_temperature = temperature
        self._notify_state_update()

    # ── Window Open Coordination ───────────────────────────────────────

    async def _async_check_window_coordination(
        self, trv_id: str, trv_state: TRVState
    ) -> None:
        """Check and coordinate window open events across room TRVs.

        Per Danfoss spec:
        - When a TRV detects window open (state >= 3), force
          external_window_open=true on other room TRVs.
        - Deactivate (set false) when all forced TRVs report state 4.
        """
        if len(self._trv_ids) <= 1:
            return

        window_state = trv_state.window_open_detection
        if window_state is None:
            return

        if (
            window_state >= WINDOW_OPEN_DETECTED
            and trv_id not in self._forced_window_open_trvs
        ):
            # This TRV detected window open - force other TRVs
            other_trvs = [t for t in self._trv_ids if t != trv_id]
            newly_forced = [
                t for t in other_trvs if t not in self._forced_window_open_trvs
            ]

            if newly_forced:
                _LOGGER.info(
                    "Window open detected on %s in room '%s', forcing %d other TRVs",
                    trv_id,
                    self._room_name,
                    len(newly_forced),
                )
                for other_trv in newly_forced:
                    try:
                        await self._backend.async_set_external_window_open(
                            other_trv, True
                        )
                        self._forced_window_open_trvs.add(other_trv)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "Failed to set external_window_open on %s", other_trv
                        )
        else:
            # Check if all forced TRVs have reached state 4 (open confirmed)
            # and we can deactivate. Only check TRVs that have reported state;
            # skip forced TRVs not yet in trv_states to avoid creating
            # phantom entries with None fields.
            if self._forced_window_open_trvs:
                forced_with_state = [
                    t
                    for t in self._forced_window_open_trvs
                    if t in self.state.trv_states
                ]
                # Only proceed if we have state for at least one forced TRV
                all_confirmed = len(forced_with_state) > 0 and all(
                    self.state.trv_states[t].window_open_detection
                    == WINDOW_OPEN_EXTERNAL_OPEN
                    for t in forced_with_state
                )
                if all_confirmed:
                    # Check if the detecting TRV(s) have closed
                    any_still_open = any(
                        (s.window_open_detection or 0) >= WINDOW_OPEN_DETECTED
                        for tid, s in self.state.trv_states.items()
                        if tid not in self._forced_window_open_trvs
                    )
                    if not any_still_open:
                        _LOGGER.info(
                            "Window closed in room '%s', deactivating forced open",
                            self._room_name,
                        )
                        for forced_trv in list(self._forced_window_open_trvs):
                            try:
                                await self._backend.async_set_external_window_open(
                                    forced_trv, False
                                )
                            except Exception:  # noqa: BLE001
                                _LOGGER.exception(
                                    "Failed to clear external_window_open on %s",
                                    forced_trv,
                                )
                        self._forced_window_open_trvs.clear()

    # ── Load Balancing ─────────────────────────────────────────────────

    def _schedule_load_balance(self) -> None:
        """Schedule the next load balance cycle."""

        @callback
        def _run_load_balance(_now: Any) -> None:
            self._load_balance_timer = None
            self.hass.async_create_task(self._async_run_load_balance())
            self._schedule_load_balance()

        self._load_balance_timer = async_call_later(
            self.hass, LOAD_BALANCE_INTERVAL, _run_load_balance
        )

    async def _async_run_load_balance(self) -> None:
        """Execute one load balancing cycle.

        Per Danfoss spec:
        - Collect load_estimate from all TRVs
        - Discard values < -500 or == -8000
        - Discard values older than 90 minutes
        - Calculate mean of valid values
        - Write load_room_mean to all TRVs
        - Skip for single-TRV rooms
        """
        if len(self._trv_ids) <= 1:
            return

        now = time.monotonic()
        valid_estimates: list[int] = []

        for trv_id in self._trv_ids:
            entry = self._load_estimates.get(trv_id)
            if entry is None:
                continue

            # Check age
            age = now - entry.timestamp
            if age > LOAD_BALANCE_MAX_AGE:
                _LOGGER.debug(
                    "Discarding stale load estimate from %s (age: %.0fs)",
                    trv_id,
                    age,
                )
                continue

            # Check validity
            if entry.value == LOAD_BALANCE_DISABLED_VALUE:
                continue
            if entry.value < LOAD_BALANCE_INVALID_THRESHOLD:
                _LOGGER.debug(
                    "Discarding invalid load estimate from %s: %d",
                    trv_id,
                    entry.value,
                )
                continue

            valid_estimates.append(entry.value)

        if not valid_estimates:
            _LOGGER.debug(
                "No valid load estimates for room '%s', skipping", self._room_name
            )
            return

        # Calculate mean
        room_mean = round(sum(valid_estimates) / len(valid_estimates))
        self.state.load_room_mean = room_mean

        _LOGGER.debug(
            "Load balance for room '%s': mean=%d (from %d TRVs)",
            self._room_name,
            room_mean,
            len(valid_estimates),
        )

        # Write to all TRVs
        for trv_id in self._trv_ids:
            try:
                await self._backend.async_set_load_room_mean(trv_id, room_mean)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to set load_room_mean on TRV %s", trv_id)

        self._notify_state_update()
