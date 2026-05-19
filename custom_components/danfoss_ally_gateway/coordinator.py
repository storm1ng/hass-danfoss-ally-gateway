"""Room coordinator for Danfoss Ally Gateway.

Implements per-room coordination logic:
- TRV state subscription and aggregation
- Room state computation (temperature, demand, window, availability)
- External temperature forwarding with Danfoss timing specs
- Heat availability signaling
- Setpoint coordination (manual dial forwarding)
- Load balancing (15-minute cycle)
- Window open coordination (force external_window_open on other TRVs)
- Preheat coordination (forward preheat commands to other TRVs)
- Remote climate sync (bidirectional setpoint sync with anti-echo)
- Weekly time synchronization
- Schedule programming and mode control
- Schedule entity watcher (auto-program from HA schedule helpers)
"""

from __future__ import annotations

import asyncio
import contextlib
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
    CONF_AT_HOME_TEMP,
    CONF_AWAY_TEMP,
    CONF_HEAT_SOURCE,
    CONF_HEAT_SOURCE_TYPE,
    CONF_PREHEAT_ENABLED,
    CONF_REMOTE_CLIMATE,
    CONF_ROOM_NAME,
    CONF_SCHEDULE_ENTITY,
    CONF_TEMP_SENSOR,
    CONF_TRV_ENTITIES,
    DEFAULT_AT_HOME_TEMP,
    DEFAULT_AWAY_TEMP,
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
    POWER_CYCLE_CHECK_INTERVAL,
    PROGRAMMING_MODE_FROM_INT,
    PROGRAMMING_MODE_TO_INT,
    REMOTE_CLIMATE_SUPPRESS_SECONDS,
    SCHEDULE_DOW_ALL,
    SCHEDULE_MODE_ECO,
    SCHEDULE_MODE_MANUAL,
    SCHEDULE_MODE_SCHEDULE,
    SCHEDULE_MODE_SCHEDULE_PREHEAT,
    SETPOINT_SOURCE_MANUAL,
    SETPOINT_TYPE_USER,
    SW_ERROR_TIME_LOST,
    TIME_SYNC_INTERVAL,
    TRV_AVAILABILITY_TIMEOUT,
    WINDOW_OPEN_DETECTED,
    WINDOW_OPEN_EXTERNAL_OPEN,
    Z2M_ATTR_LOAD_ROOM_MEAN,
)
from .schedule import (
    WeeklySchedule,
    apply_midnight_crossing,
    build_zcl_set_weekly_payloads,
    from_ha_schedule,
    parse_zcl_get_weekly_response,
    schedules_match,
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
    load_balancing_enabled: bool = False  # Whether load balancing is active
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
        self._remote_climate_id: str = subentry_data.get(CONF_REMOTE_CLIMATE, "")
        self._schedule_entity_id: str = subentry_data.get(CONF_SCHEDULE_ENTITY, "")
        self._at_home_temp: float = subentry_data.get(
            CONF_AT_HOME_TEMP, DEFAULT_AT_HOME_TEMP
        )
        self._away_temp: float = subentry_data.get(CONF_AWAY_TEMP, DEFAULT_AWAY_TEMP)
        self._preheat_enabled: bool = subentry_data.get(CONF_PREHEAT_ENABLED, True)

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
        self._load_balancing_enabled: bool = len(self._trv_ids) > 1

        # Setpoint coordination
        self._setpoint_lock = asyncio.Lock()
        self._programmatic_setpoint: bool = False

        # Window coordination
        self._forced_window_open_trvs: set[str] = set()  # TRVs we forced open

        # Preheat coordination
        self._last_forwarded_preheat: dict[str, int] = {}  # trv_id → preheat_time

        # Remote climate anti-echo
        self._remote_setpoint_suppress_until: float = 0.0  # monotonic timestamp

        self._time_sync_timer: CALLBACK_TYPE | None = None

        self._current_schedule: WeeklySchedule | None = None  # Last programmed schedule
        self._schedule_mode: int = SCHEDULE_MODE_MANUAL  # Current programming mode
        self._power_cycle_timer: CALLBACK_TYPE | None = None

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
            # Send initial temperature
            await self._async_send_initial_ext_temp()

        # Listen to heat source entity
        if self._heat_source_id:
            unsub = async_track_state_change_event(
                self.hass, [self._heat_source_id], self._handle_heat_source_change
            )
            self._unsub_callbacks.append(unsub)
            # Set initial heat availability
            await self._async_update_heat_availability()

        # Listen to remote climate entity for bidirectional setpoint sync
        if self._remote_climate_id:
            unsub = async_track_state_change_event(
                self.hass,
                [self._remote_climate_id],
                self._handle_remote_climate_change,
            )
            self._unsub_callbacks.append(unsub)

        # Listen to schedule helper entity for schedule updates
        if self._schedule_entity_id:
            unsub = async_track_state_change_event(
                self.hass,
                [self._schedule_entity_id],
                self._handle_schedule_entity_change,
            )
            self._unsub_callbacks.append(unsub)
            # Program initial schedule from the helper entity
            await self._async_sync_schedule_from_entity()

        # Start load balancing if enabled (default ON for multi-TRV rooms)
        if self._load_balancing_enabled:
            self.state.load_balancing_enabled = True
            self._schedule_load_balance()
            # Write load_balancing_enable=true to all TRVs to ensure
            # the attribute is set (may be lost after battery change).
            for trv_id in self._trv_ids:
                try:
                    await self._backend.async_set_load_balancing_enable(trv_id, True)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Failed to set load_balancing_enable on %s", trv_id
                    )

        # Start time sync timer
        self._schedule_time_sync()

        # Sync time on setup (required on join/rejoin)
        await self._async_sync_time_all()

        # Register device announce callback for power-cycle recovery
        unsub = self._backend.register_announce_callback(self._handle_device_announce)
        self._unsub_callbacks.append(unsub)

        # Start power-cycle detection timer (fallback for missed announces)
        self._schedule_power_cycle_check()

    async def async_teardown(self) -> None:
        """Tear down the room coordinator."""
        _LOGGER.info("Tearing down room coordinator for '%s'", self._room_name)

        # Cancel all timers
        for ext_state in self._ext_temp_trv.values():
            if ext_state.timer is not None:
                ext_state.timer()
                ext_state.timer = None
        if self._load_balance_timer is not None:
            self._load_balance_timer()
            self._load_balance_timer = None
        if self._time_sync_timer is not None:
            self._time_sync_timer()
            self._time_sync_timer = None
        if self._power_cycle_timer is not None:
            self._power_cycle_timer()
            self._power_cycle_timer = None

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

        # Check for setpoint coordination (manual dial change)
        if old_state is not None:
            self.hass.async_create_task(
                self._async_check_setpoint_coordination(trv_id, old_state, trv_state)
            )

        # Check for window open coordination
        self.hass.async_create_task(
            self._async_check_window_coordination(trv_id, trv_state)
        )

        # Check for preheat coordination
        self.hass.async_create_task(
            self._async_check_preheat_coordination(trv_id, trv_state)
        )

        # Update load estimate tracking
        if trv_state.load_estimate is not None:
            self._load_estimates[trv_id] = LoadEstimateEntry(
                value=trv_state.load_estimate,
                timestamp=time.monotonic(),
            )

        # Update per-TRV radiator_covered from TRV-reported state
        if trv_state.radiator_covered is not None:
            self._ext_temp_trv[trv_id].covered = trv_state.radiator_covered

        # Seed load_room_mean from TRV-reported value if the coordinator
        # hasn't computed its own yet.  This avoids a 15-minute "Unknown"
        # period after startup for multi-TRV rooms.
        # Reject disabled (-8000) and invalid (< -500) values per Danfoss spec.
        if self.state.load_room_mean is None and len(self._trv_ids) > 1:
            raw_mean = trv_state.raw.get(Z2M_ATTR_LOAD_ROOM_MEAN)
            if (
                isinstance(raw_mean, int | float)
                and int(raw_mean) != LOAD_BALANCE_DISABLED_VALUE
                and int(raw_mean) >= LOAD_BALANCE_INVALID_THRESHOLD
            ):
                self.state.load_room_mean = int(raw_mean)
                _LOGGER.debug(
                    "Seeded load_room_mean from TRV %s: %d",
                    trv_id,
                    self.state.load_room_mean,
                )

        self._notify_state_update()

    def _update_room_state(self) -> None:
        """Recalculate aggregated room state from TRV states."""
        states = self.state.trv_states

        if not states:
            return

        # Current temperature: prefer external sensor
        if self._temp_sensor_id:
            sensor_state = self.hass.states.get(self._temp_sensor_id)
            if sensor_state and sensor_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                with contextlib.suppress(ValueError, TypeError):
                    self.state.current_temperature = float(sensor_state.state)
        else:
            # Average of TRV local temperatures
            temps = [
                s.local_temperature
                for s in states.values()
                if s.local_temperature is not None
            ]
            if temps:
                self.state.current_temperature = sum(temps) / len(temps)

        # Target temperature: use the first TRV's setpoint (in configured order)
        # They should all be in sync; using deterministic order avoids fragility.
        for trv_id in self._trv_ids:
            trv = states.get(trv_id)
            if trv is not None and trv.occupied_heating_setpoint is not None:
                self.state.target_temperature = trv.occupied_heating_setpoint
                break

        # Pi heating demand: max across all TRVs
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

        # Sync to remote climate if configured
        await self._async_sync_remote_climate(new_setpoint)

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

        # Sync to remote climate if configured
        await self._async_sync_remote_climate(temperature)

    # ── Remote Climate Sync ────────────────────────────────────────────

    @callback
    def _handle_remote_climate_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle remote climate entity state change for bidirectional sync.

        When the remote climate's setpoint changes (by user or automation),
        forward the new setpoint to all TRVs in the room.
        """
        # Anti-echo: ignore events within the suppression window
        if time.monotonic() < self._remote_setpoint_suppress_until:
            return

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        # Extract setpoint from the remote climate entity
        temperature = self._extract_remote_climate_setpoint(new_state)
        if temperature is None:
            return

        # Only act if the temperature differs from the current room setpoint
        if (
            self.state.target_temperature is not None
            and abs(temperature - self.state.target_temperature) < 0.05
        ):
            return

        _LOGGER.info(
            "Remote climate %s setpoint changed to %.1f°C in room '%s', "
            "syncing to TRVs",
            self._remote_climate_id,
            temperature,
            self._room_name,
        )

        self.hass.async_create_task(self.async_set_room_temperature(temperature))

    @staticmethod
    def _extract_remote_climate_setpoint(state) -> float | None:
        """Extract the heating setpoint from a climate entity state.

        For dual-mode climates (heat/cool with target_temp_low/high),
        uses target_temp_low (the heating setpoint).
        For single-mode climates, uses the temperature attribute.
        """
        attrs = state.attributes

        # Check for dual-mode (target_temp_low/target_temp_high)
        target_temp_low = attrs.get("target_temp_low")
        if target_temp_low is not None:
            try:
                return float(target_temp_low)
            except (ValueError, TypeError):
                pass

        # Single-mode: use temperature attribute
        temperature = attrs.get("temperature")
        if temperature is not None:
            try:
                return float(temperature)
            except (ValueError, TypeError):
                pass

        return None

    async def _async_sync_remote_climate(self, temperature: float) -> None:
        """Sync the given setpoint to the remote climate entity.

        Sets the suppression window before calling the climate service
        to prevent the resulting state_changed event from looping back.
        """
        if not self._remote_climate_id:
            return

        remote_state = self.hass.states.get(self._remote_climate_id)
        if remote_state is None or remote_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            return

        # Set suppression window before calling the service
        self._remote_setpoint_suppress_until = (
            time.monotonic() + REMOTE_CLIMATE_SUPPRESS_SECONDS
        )

        # Determine whether to use dual-mode or single-mode service data
        service_data: dict[str, Any] = {"entity_id": self._remote_climate_id}

        target_temp_low = remote_state.attributes.get("target_temp_low")
        if target_temp_low is not None:
            # Dual-mode: set target_temp_low (heating), preserve target_temp_high
            target_temp_high = remote_state.attributes.get("target_temp_high")
            service_data["target_temp_low"] = temperature
            if target_temp_high is not None:
                service_data["target_temp_high"] = target_temp_high
        else:
            # Single-mode: set temperature directly
            service_data["temperature"] = temperature

        _LOGGER.debug(
            "Syncing setpoint %.1f°C to remote climate %s in room '%s'",
            temperature,
            self._remote_climate_id,
            self._room_name,
        )

        try:
            await self.hass.services.async_call(
                "climate", "set_temperature", service_data
            )
        except Exception:
            _LOGGER.exception(
                "Failed to sync setpoint to remote climate %s in room '%s'",
                self._remote_climate_id,
                self._room_name,
            )

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

        if window_state == WINDOW_OPEN_DETECTED:
            # This TRV locally detected window open (state 3) - force other TRVs
            # If this TRV was already forced by the coordinator, its local
            # detection is redundant - dont' cascade back or we deadlock
            # (all TRVs end up in _forced and nobody can trigger deactivation).
            if trv_id in self._forced_window_open_trvs:
                _LOGGER.debug(
                    "TRV %s is room '%s' reported state 3 but is already forced, "
                    "skipping cascade",
                    trv_id,
                    self._room_name,
                )
                return
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
                    except Exception:
                        _LOGGER.exception(
                            "Failed to set external_window_open on %s", other_trv
                        )
        elif window_state == WINDOW_OPEN_EXTERNAL_OPEN:
            # State 4: this TRV was forced open by the gateway.
            # If it's not tracked in _forced_window_open_trvs (e.g. after HA
            # restart), it's an orphan — clear it so the TRV can resume
            # normal operation and re-detect if the window is still open.
            if trv_id not in self._forced_window_open_trvs:
                _LOGGER.info(
                    "Clearing orphaned window_open_external on %s in room '%s' "
                    "(not tracked after restart)",
                    trv_id,
                    self._room_name,
                )
                try:
                    await self._backend.async_set_external_window_open(trv_id, False)
                except Exception:
                    _LOGGER.exception(
                        "Failed to clear orphaned external_window_open on %s",
                        trv_id,
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
                        (s.window_open_detection or 0) == WINDOW_OPEN_DETECTED
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

    # ── Preheat Coordination ──────────────────────────────────────────

    async def _async_check_preheat_coordination(
        self, trv_id: str, trv_state: TRVState
    ) -> None:
        """Check and coordinate preheat events across room TRVs.

        Per Danfoss spec: When a TRV reports preheat_status=true and a
        preheat_time, forward the preheat_time to other room TRVs via
        PreHeatCommand. Deduplicates by tracking the last forwarded value
        per TRV.
        """
        if len(self._trv_ids) <= 1:
            return

        if not trv_state.preheat_status or trv_state.preheat_time is None:
            return

        # Deduplicate: skip if we already forwarded this exact preheat_time
        # from this TRV
        last_forwarded = self._last_forwarded_preheat.get(trv_id)
        if last_forwarded == trv_state.preheat_time:
            return

        self._last_forwarded_preheat[trv_id] = trv_state.preheat_time

        _LOGGER.debug(
            "Preheat detected on %s in room '%s' (time=%d), forwarding to other TRVs",
            trv_id,
            self._room_name,
            trv_state.preheat_time,
        )

        for other_trv in self._trv_ids:
            if other_trv == trv_id:
                continue
            try:
                await self._backend.async_send_preheat_command(
                    other_trv, trv_state.preheat_time
                )
            except Exception:
                _LOGGER.exception("Failed to forward preheat to TRV %s", other_trv)

    # ── Time Synchronization ──────────────────────────────────────────

    def _schedule_time_sync(self) -> None:
        """Schedule weekly time sync."""

        @callback
        def _run_time_sync(_now: Any) -> None:
            self._time_sync_timer = None
            self.hass.async_create_task(self._async_sync_time_all())
            self._schedule_time_sync()

        self._time_sync_timer = async_call_later(
            self.hass, TIME_SYNC_INTERVAL, _run_time_sync
        )

    async def _async_sync_time_all(self) -> None:
        """Synchronize time to all TRVs in the room."""
        _LOGGER.debug("Syncing time to all TRVs in room '%s'", self._room_name)

        for trv_id in self._trv_ids:
            try:
                await self._backend.async_sync_time(trv_id)
            except Exception:
                _LOGGER.exception(
                    "Failed to sync time to TRV %s in room '%s'",
                    trv_id,
                    self._room_name,
                )

    # ── Schedule Entity Watching ──────────────────────────────────────────

    @callback
    def _handle_schedule_entity_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle schedule helper entity state change.

        When the schedule entity's state or attributes change, re-read
        the schedule and re-program to TRVs.
        """
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        self.hass.async_create_task(self._async_sync_schedule_from_entity())

    async def _async_sync_schedule_from_entity(self) -> None:
        """Read the HA schedule helper entity and program to TRVs.

        Reads the schedule entity's 'schedule' attribute, converts to
        a WeeklySchedule using the configured at-home/away temperatures,
        and programs it to all TRVs.
        """
        if not self._schedule_entity_id:
            return

        state = self.hass.states.get(self._schedule_entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            _LOGGER.debug(
                "Schedule entity %s unavailable for room '%s'",
                self._schedule_entity_id,
                self._room_name,
            )
            return

        # HA schedule helper exposes schedule blocks via the 'schedule' attribute
        # as a list of dicts with "from", "to", "days" keys.
        schedule_blocks = state.attributes.get("schedule")
        if not schedule_blocks:
            _LOGGER.debug(
                "No schedule blocks in %s for room '%s'",
                self._schedule_entity_id,
                self._room_name,
            )
            return

        try:
            schedule = from_ha_schedule(
                schedule_blocks, self._at_home_temp, self._away_temp
            )
        except ValueError as err:
            _LOGGER.error(
                "Failed to convert schedule from %s for room '%s': %s",
                self._schedule_entity_id,
                self._room_name,
                err,
            )
            return

        if schedule.is_empty:
            _LOGGER.debug(
                "Empty schedule from %s for room '%s', skipping",
                self._schedule_entity_id,
                self._room_name,
            )
            return

        _LOGGER.info(
            "Syncing schedule from %s to room '%s' (%d events, "
            "at_home=%.1f°C, away=%.1f°C)",
            self._schedule_entity_id,
            self._room_name,
            schedule.total_events,
            self._at_home_temp,
            self._away_temp,
        )

        try:
            await self.async_program_schedule(schedule)
        except Exception:
            _LOGGER.exception(
                "Failed to program schedule from entity %s for room '%s'",
                self._schedule_entity_id,
                self._room_name,
            )
            return

        # Set programming mode based on preheat config
        mode = (
            SCHEDULE_MODE_SCHEDULE_PREHEAT
            if self._preheat_enabled
            else SCHEDULE_MODE_SCHEDULE
        )
        if self._schedule_mode != mode:
            await self.async_set_programming_mode_value(mode)

    # ── Programming Mode Control ───────────────────────────────────────

    @property
    def schedule_mode(self) -> int:
        """Return the current programming mode integer value."""
        return self._schedule_mode

    @property
    def schedule_mode_option(self) -> str:
        """Return the current programming mode as a string option."""
        return PROGRAMMING_MODE_FROM_INT.get(self._schedule_mode, "manual")

    async def async_set_programming_mode_option(self, option: str) -> None:
        """Set the programming mode from a string option.

        Args:
            option: One of "manual", "schedule", "schedule_with_preheat", "pause".
        """
        mode = PROGRAMMING_MODE_TO_INT.get(option)
        if mode is None:
            raise ValueError(f"Invalid programming mode option: {option}")
        await self.async_set_programming_mode_value(mode)

    async def async_set_programming_mode_value(self, mode: int) -> None:
        """Set the programming mode by integer value on all TRVs."""
        mode_name = PROGRAMMING_MODE_FROM_INT.get(mode, f"unknown({mode})")
        _LOGGER.info(
            "Setting programming mode for room '%s': %s (mode=%d)",
            self._room_name,
            mode_name,
            mode,
        )

        for trv_id in self._trv_ids:
            try:
                await self._backend.async_set_programming_mode(trv_id, mode)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to set programming mode on TRV %s in room '%s'",
                    trv_id,
                    self._room_name,
                )

        self._schedule_mode = mode
        self._notify_state_update()

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

    async def async_enable_load_balancing(self) -> None:
        """Enable load balancing for this room.

        Writes load_balancing_enable=true to all TRVs and starts the
        periodic load balance timer.
        """
        if len(self._trv_ids) <= 1:
            return
        self._load_balancing_enabled = True
        self.state.load_balancing_enabled = True
        for trv_id in self._trv_ids:
            try:
                await self._backend.async_set_load_balancing_enable(trv_id, True)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to set load_balancing_enable on %s", trv_id)
        if self._load_balance_timer is None:
            self._schedule_load_balance()
        self._notify_state_update()

    async def async_disable_load_balancing(self) -> None:
        """Disable load balancing for this room.

        Sends load_room_mean=-8000 (disabled) to all TRVs per Danfoss spec,
        writes load_balancing_enable=false, and stops the timer.
        """
        self._load_balancing_enabled = False
        self.state.load_balancing_enabled = False
        self.state.load_room_mean = None
        # Cancel timer
        if self._load_balance_timer is not None:
            self._load_balance_timer()
            self._load_balance_timer = None
        # Send disabled value to all TRVs
        for trv_id in self._trv_ids:
            try:
                await self._backend.async_set_load_room_mean(
                    trv_id, LOAD_BALANCE_DISABLED_VALUE
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to send disabled load_room_mean to %s", trv_id
                )
            try:
                await self._backend.async_set_load_balancing_enable(trv_id, False)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to set load_balancing_enable on %s", trv_id)
        self._notify_state_update()

    # ── Schedule Programming ───────────────────────────────────────────

    async def async_program_schedule(self, schedule: WeeklySchedule) -> None:
        """Program a weekly schedule to all TRVs in the room.

        Steps:
        1. Validate the schedule.
        2. Clear existing schedule on all TRVs.
        3. Apply midnight crossing logic.
        4. Build per-day ZCL payloads.
        5. Send SetWeeklySchedule to each TRV for each day.
        6. Read back schedule from first TRV for verification.
        7. Store as current schedule.
        """
        # Validate
        errors = schedule.validate()
        if errors:
            _LOGGER.error(
                "Schedule validation failed for room '%s': %s",
                self._room_name,
                errors,
            )
            raise ValueError(f"Invalid schedule: {errors}")

        _LOGGER.info(
            "Programming schedule for room '%s' (%d total events)",
            self._room_name,
            schedule.total_events,
        )

        # Step 1: Clear existing schedule on all TRVs
        for trv_id in self._trv_ids:
            try:
                await self._backend.async_clear_weekly_schedule(trv_id)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to clear schedule on TRV %s in room '%s'",
                    trv_id,
                    self._room_name,
                )

        # Step 2: Apply midnight crossing logic
        processed = apply_midnight_crossing(schedule)

        # Step 3: Build ZCL payloads
        payloads = build_zcl_set_weekly_payloads(processed)

        if not payloads:
            _LOGGER.info("No schedule events to program for room '%s'", self._room_name)
            self._current_schedule = schedule
            return

        # Step 4: Send to each TRV
        for trv_id in self._trv_ids:
            for payload in payloads:
                try:
                    await self._backend.async_set_weekly_schedule(
                        trv_id,
                        day_of_week=payload["day_of_week"],
                        num_transitions=payload["num_transitions"],
                        mode=payload["mode"],
                        transitions=payload["transitions"],
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Failed to program schedule day 0x%02X on TRV %s",
                        payload["day_of_week"],
                        trv_id,
                    )

        # Step 5: Read-back verification from first TRV
        await self._async_verify_schedule(self._trv_ids[0], processed)

        # Store the original (pre-midnight-crossing) schedule
        self._current_schedule = schedule

        _LOGGER.info("Schedule programming complete for room '%s'", self._room_name)

    async def _async_verify_schedule(
        self, trv_id: str, expected: WeeklySchedule
    ) -> bool:
        """Read back schedule from a TRV and compare to expected.

        Returns True if schedules match, False otherwise.
        Logs warnings on mismatch but does not retry.
        """
        _LOGGER.debug(
            "Verifying schedule on TRV %s in room '%s'", trv_id, self._room_name
        )

        actual = WeeklySchedule()

        for day_idx in range(7):
            if expected.days[day_idx].is_empty:
                continue

            try:
                transitions = await self._backend.async_get_weekly_schedule(
                    trv_id, SCHEDULE_DOW_ALL[day_idx]
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to read back schedule for day %d from TRV %s",
                    day_idx,
                    trv_id,
                )
                return False

            if transitions is None:
                _LOGGER.debug(
                    "No schedule data returned for day %d from TRV %s",
                    day_idx,
                    trv_id,
                )
                return False

            parsed = parse_zcl_get_weekly_response(
                SCHEDULE_DOW_ALL[day_idx], 0x01, transitions
            )
            if day_idx in parsed:
                actual.days[day_idx] = parsed[day_idx]

        if schedules_match(expected, actual):
            _LOGGER.debug(
                "Schedule verification passed for TRV %s in room '%s'",
                trv_id,
                self._room_name,
            )
            return True

        _LOGGER.warning(
            "Schedule verification FAILED for TRV %s in room '%s'. "
            "The TRV may not have saved the schedule correctly.",
            trv_id,
            self._room_name,
        )
        return False

    async def async_set_schedule_mode(
        self, enabled: bool, preheat: bool = False, eco: bool = False
    ) -> None:
        """Set the thermostat programming operation mode on all TRVs.

        Args:
            enabled: True to enable schedule mode, False for manual.
            preheat: True to enable preheat (only used when enabled=True).
            eco: True to enable eco/pause mode (overrides enabled/preheat).
        """
        if eco:
            mode = SCHEDULE_MODE_ECO
        elif enabled:
            mode = SCHEDULE_MODE_SCHEDULE_PREHEAT if preheat else SCHEDULE_MODE_SCHEDULE
        else:
            mode = SCHEDULE_MODE_MANUAL

        await self.async_set_programming_mode_value(mode)

    async def async_clear_schedule(self) -> None:
        """Clear schedule on all TRVs and set manual mode."""
        _LOGGER.info("Clearing schedule for room '%s'", self._room_name)

        for trv_id in self._trv_ids:
            try:
                await self._backend.async_clear_weekly_schedule(trv_id)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to clear schedule on TRV %s in room '%s'",
                    trv_id,
                    self._room_name,
                )

        # Set manual mode
        await self.async_set_schedule_mode(enabled=False)
        self._current_schedule = None

    # ── Power Cycle Detection ──────────────────────────────────────────

    @callback
    def _handle_device_announce(self, trv_id: str) -> None:
        """Handle a device announce (rejoin) event from the backend.

        Triggers schedule verification and recovery after a short delay
        to allow upstream time sync to complete first.
        """
        if trv_id not in self._trv_ids:
            return
        _LOGGER.info(
            "Device announce received for TRV %s in room '%s'. "
            "Scheduling power-cycle recovery check.",
            trv_id,
            self._room_name,
        )

        # Delay 5s to let upstream danfossTimeSyncOnAnnounce complete time sync
        async def _rejoin_cb(_now: Any) -> None:
            await self._async_handle_device_rejoin(trv_id)

        async_call_later(self.hass, 5, _rejoin_cb)

    async def _async_handle_device_rejoin(self, trv_id: str) -> None:
        """Handle TRV rejoin: restore settings lost after power cycle.

        Per Danfoss spec: schedule information, load balancing enable,
        and other settings may be lost after battery change / power cycle.
        """
        # ── Schedule recovery ──
        if self._current_schedule is not None:
            # Verify schedule by reading Monday (day_of_week=0x02)
            try:
                transitions = await self._backend.async_get_weekly_schedule(
                    trv_id, 0x02
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to read schedule from TRV %s after rejoin", trv_id
                )
                transitions = None

            if not transitions:
                _LOGGER.warning(
                    "TRV %s in room '%s' lost schedule after power cycle. "
                    "Re-programming schedule and restoring settings.",
                    trv_id,
                    self._room_name,
                )
                try:
                    await self._async_reprogram_single_trv(trv_id)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Failed to re-program schedule on TRV %s after rejoin",
                        trv_id,
                    )

                # Re-apply programming mode
                if self._schedule_mode != SCHEDULE_MODE_MANUAL:
                    try:
                        await self._backend.async_set_programming_mode(
                            trv_id, self._schedule_mode
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "Failed to re-set programming mode on TRV %s after rejoin",
                            trv_id,
                        )

        # Re-send external temperature if configured
        if self._temp_sensor_id:
            temp_state = self.hass.states.get(self._temp_sensor_id)
            if temp_state and temp_state.state not in ("unavailable", "unknown"):
                try:
                    temp = float(temp_state.state)
                    await self._backend.async_set_external_temperature(trv_id, temp)
                except (ValueError, Exception):  # noqa: BLE001
                    _LOGGER.debug(
                        "Failed to re-send external temp to TRV %s after rejoin",
                        trv_id,
                    )

        # Restore load_balancing_enable if load balancing is active
        if self._load_balancing_enabled:
            try:
                await self._backend.async_set_load_balancing_enable(trv_id, True)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to re-set load_balancing_enable on TRV %s after rejoin",
                    trv_id,
                )

    def _schedule_power_cycle_check(self) -> None:
        """Schedule periodic power-cycle detection check (fallback)."""

        @callback
        def _run_power_cycle_check(_now: Any) -> None:
            self._power_cycle_timer = None
            self.hass.async_create_task(self._async_check_power_cycle())
            self._schedule_power_cycle_check()

        self._power_cycle_timer = async_call_later(
            self.hass, POWER_CYCLE_CHECK_INTERVAL, _run_power_cycle_check
        )

    async def _async_check_power_cycle(self) -> None:
        """Fallback: check all TRVs for schedule loss via GetWeeklySchedule.

        This is a fallback for cases where the device_announce event was
        missed (e.g. HA restart during TRV rejoin). Checks if the schedule
        is still present on the TRV; if not, triggers recovery.

        Also checks system_status_code for E10 as an additional indicator.
        """
        if self._current_schedule is None:
            return  # Nothing to verify

        for trv_id in self._trv_ids:
            # First check E10 (fast, no Zigbee traffic if not set)
            try:
                error_code = await self._backend.async_read_sw_error_code(trv_id)
            except Exception:  # noqa: BLE001
                error_code = None

            if error_code and SW_ERROR_TIME_LOST in error_code:
                _LOGGER.warning(
                    "TRV %s in room '%s' reports time lost (E10). "
                    "Triggering power-cycle recovery.",
                    trv_id,
                    self._room_name,
                )
                # Time sync (belt-and-suspenders, upstream should have done it)
                try:
                    await self._backend.async_sync_time(trv_id)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Failed to re-sync time to TRV %s", trv_id)
                await self._async_handle_device_rejoin(trv_id)
                continue

            # Verify schedule is still present (catches silent resets)
            try:
                transitions = await self._backend.async_get_weekly_schedule(
                    trv_id, 0x02
                )
            except Exception:  # noqa: BLE001
                continue  # Can't verify, skip

            if not transitions:
                _LOGGER.warning(
                    "TRV %s in room '%s' has empty schedule (power cycle?). "
                    "Triggering recovery.",
                    trv_id,
                    self._room_name,
                )
                await self._async_handle_device_rejoin(trv_id)

    async def _async_reprogram_single_trv(self, trv_id: str) -> None:
        """Re-program the current schedule on a single TRV.

        Used after power-cycle detection to restore the schedule.
        """
        if self._current_schedule is None:
            return

        # Clear first
        try:
            await self._backend.async_clear_weekly_schedule(trv_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to clear schedule on TRV %s", trv_id)

        # Apply midnight crossing and build payloads
        processed = apply_midnight_crossing(self._current_schedule)
        payloads = build_zcl_set_weekly_payloads(processed)

        for payload in payloads:
            try:
                await self._backend.async_set_weekly_schedule(
                    trv_id,
                    day_of_week=payload["day_of_week"],
                    num_transitions=payload["num_transitions"],
                    mode=payload["mode"],
                    transitions=payload["transitions"],
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to re-program schedule day 0x%02X on TRV %s",
                    payload["day_of_week"],
                    trv_id,
                )

        _LOGGER.info(
            "Re-programmed schedule on TRV %s in room '%s'",
            trv_id,
            self._room_name,
        )
