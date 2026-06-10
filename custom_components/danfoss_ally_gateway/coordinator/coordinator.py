"""Room coordinator for Danfoss Ally Gateway.

Implements per-room coordination logic by delegating to specialized
sub-modules:
- ext_temp: External temperature forwarding with Danfoss timing specs
- load_balancer: 15-minute load balancing cycle
- window: Window open coordination across TRVs
- preheat: Preheat command forwarding
- setpoint: Manual dial forwarding, remote climate sync
- schedule_manager: Schedule programming, mode control, power-cycle recovery
- time_sync: Weekly time synchronization
"""

from __future__ import annotations

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
    split_entity_id,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)

from ..backend import DanfossBackend, TRVState
from ..backend.z2m import Z2MBackend
from ..backend.zha import ZHABackend
from ..const import (
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
    HEAT_SOURCE_BINARY_SENSOR,
    HEAT_SOURCE_CLIMATE,
    LOAD_BALANCE_DISABLED_VALUE,
    LOAD_BALANCE_INVALID_THRESHOLD,
    SW_ERROR_TIME_LOST,
    TRV_AVAILABILITY_TIMEOUT,
    WINDOW_OPEN_DETECTED,
    Z2M_ATTR_LOAD_ROOM_MEAN,
)
from ..schedule import WeeklySchedule
from .ext_temp import ExtTempDelegate
from .load_balancer import LoadBalanceDelegate
from .preheat import PreheatDelegate
from .schedule_manager import ScheduleDelegate
from .setpoint import SetpointDelegate
from .time_sync import TimeSyncDelegate
from .window import WindowDelegate

_LOGGER = logging.getLogger(__name__)


@dataclass
class RoomState:
    """Aggregated state for a room, derived from TRV states."""

    room_name: str = ""
    current_temperature: float | None = None
    target_temperature: float | None = None
    max_pi_heating_demand: int = 0
    heat_required: bool = False
    heat_available: bool | None = None
    window_open: bool = False
    load_room_mean: int | None = None
    load_balancing_enabled: bool = False
    available: bool = False
    trv_states: dict[str, TRVState] = field(default_factory=dict)


# Type for room state update listeners
RoomStateCallback = Callable[[RoomState], None]


class RoomCoordinator:
    """Coordinates all gateway logic for a single room.

    One instance is created per room subentry. It delegates domain-specific
    logic to specialized delegates and handles cross-cutting concerns like
    TRV state subscription, room state aggregation, and lifecycle management.
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

        # Current room state
        self.state = RoomState(room_name=self._room_name)

        # Room state listeners
        self._state_callbacks: list[RoomStateCallback] = []

        # Cleanup callbacks
        self._unsub_callbacks: list[CALLBACK_TYPE] = []

        # TRV availability tracking
        self._last_trv_update_time: dict[str, float] = {}

        # ── Delegates ─────────────────────────────────────────────────
        self._ext_temp = ExtTempDelegate(
            hass, backend, self._room_name, self._trv_ids, self._temp_sensor_id
        )
        self._load_balance = LoadBalanceDelegate(
            hass, backend, self._room_name, self._trv_ids
        )
        self._window = WindowDelegate(
            backend,
            self._room_name,
            self._trv_ids,
            lambda: self.state.trv_states,
        )
        self._preheat = PreheatDelegate(backend, self._room_name, self._trv_ids)
        self._setpoint = SetpointDelegate(
            hass, backend, self._room_name, self._trv_ids, self._remote_climate_id
        )
        self._schedule = ScheduleDelegate(
            hass,
            backend,
            self._room_name,
            self._trv_ids,
            subentry_data.get(CONF_SCHEDULE_ENTITY, ""),
            subentry_data.get(CONF_AT_HOME_TEMP, DEFAULT_AT_HOME_TEMP),
            subentry_data.get(CONF_AWAY_TEMP, DEFAULT_AWAY_TEMP),
            subentry_data.get(CONF_PREHEAT_ENABLED, True),
            self._notify_state_update,
        )
        self._time_sync = TimeSyncDelegate(
            hass, backend, self._room_name, self._trv_ids
        )

    @property
    def room_name(self) -> str:
        """Return the room name."""
        return self._room_name

    @property
    def trv_ids(self) -> list[str]:
        """Return the list of TRV IDs in this room."""
        return self._trv_ids

    # ── Delegate-forwarded properties ─────────────────────────────────

    @property
    def schedule_mode(self) -> int:
        """Return the current programming mode integer value."""
        return self._schedule.mode

    @property
    def schedule_mode_option(self) -> str:
        """Return the current programming mode as a string option."""
        return self._schedule.mode_option

    # ── State callbacks ───────────────────────────────────────────────

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

    # ── Lifecycle ─────────────────────────────────────────────────────

    def _resolve_trv_id(self, trv_id: str) -> str:
        """Resolve a device registry ID to a backend-specific TRV identifier."""
        device_reg = dr.async_get(self.hass)
        device = device_reg.async_get(trv_id)
        if device is None:
            return trv_id

        if isinstance(self._backend, Z2MBackend):
            return device.name or trv_id

        if isinstance(self._backend, ZHABackend):
            # ZHA: find the climate entity for this device
            entity_reg = er.async_get(self.hass)
            entries = er.async_entries_for_device(entity_reg, device.id)
            for entry in entries:
                if entry.domain == "climate":
                    return entry.entity_id

        _LOGGER.warning(
            "No climate entity found for device %s, using device ID as-is",
            trv_id,
        )
        return trv_id

    async def async_setup(self) -> None:
        """Set up the room coordinator."""
        _LOGGER.info("Setting up room coordinator for '%s'", self._room_name)

        # Resolve device registry IDs
        self._trv_ids[:] = [self._resolve_trv_id(tid) for tid in self._trv_ids]

        # Rebuild delegate TRV tracking with resolved IDs
        self._ext_temp.rebuild_trv_ids(self._trv_ids)

        # Subscribe to TRV state changes
        unsub = self._backend.register_state_callback(self._handle_trv_state_update)
        self._unsub_callbacks.append(unsub)

        for trv_id in self._trv_ids:
            await self._backend.async_subscribe_trv(trv_id)

        # External temperature sensor
        if self._temp_sensor_id:
            unsub = async_track_state_change_event(
                self.hass,
                [self._temp_sensor_id],
                self._ext_temp.handle_temp_sensor_change,
            )
            self._unsub_callbacks.append(unsub)
            await self._ext_temp.async_send_initial()

        # Heat source entity
        if self._heat_source_id:
            unsub = async_track_state_change_event(
                self.hass, [self._heat_source_id], self._handle_heat_source_change
            )
            self._unsub_callbacks.append(unsub)
            await self._async_update_heat_availability()

        # Remote climate entity
        if self._remote_climate_id:
            unsub = async_track_state_change_event(
                self.hass,
                [self._remote_climate_id],
                self._handle_remote_climate_change,
            )
            self._unsub_callbacks.append(unsub)

        # Schedule helper entity
        if self._schedule._schedule_entity_id:
            unsub = async_track_state_change_event(
                self.hass,
                [self._schedule._schedule_entity_id],
                self._schedule.handle_schedule_entity_change,
            )
            self._unsub_callbacks.append(unsub)
            await self._schedule.async_sync_from_entity()

        # Load balancing
        if self._load_balance.enabled:
            self.state.load_balancing_enabled = True
            self._load_balance.schedule_cycle()
            await self._load_balance.async_setup_trvs()

        # Time sync
        self._time_sync.schedule_sync()
        await self._time_sync.async_sync_all()

        # Device announce callback for power-cycle recovery
        unsub = self._backend.register_announce_callback(self._handle_device_announce)
        self._unsub_callbacks.append(unsub)

        # Power-cycle detection timer
        self._schedule.schedule_power_cycle_check()

    async def async_teardown(self) -> None:
        """Tear down the room coordinator."""
        _LOGGER.info("Tearing down room coordinator for '%s'", self._room_name)

        # Cancel all delegate timers
        self._ext_temp.cancel_timers()
        self._load_balance.cancel_timer()
        self._time_sync.cancel_timer()
        self._schedule.cancel_timer()

        # Unsubscribe from everything
        for unsub in self._unsub_callbacks:
            unsub()
        self._unsub_callbacks.clear()

        # Unsubscribe from TRVs
        for trv_id in self._trv_ids:
            await self._backend.async_unsubscribe_trv(trv_id)

        # Disable external temp on TRVs
        if self._temp_sensor_id:
            await self._ext_temp.async_disable_all()

    # ── TRV State Handling ────────────────────────────────────────────

    @callback
    def _handle_trv_state_update(self, trv_id: str, trv_state: TRVState) -> None:
        """Handle a TRV state update from the backend."""
        if trv_id not in self._trv_ids:
            return

        old_state = self.state.trv_states.get(trv_id)
        self.state.trv_states[trv_id] = trv_state

        # Track availability
        self._last_trv_update_time[trv_id] = time.monotonic()

        # Update aggregated room state
        self._update_room_state()

        # Setpoint coordination
        if old_state is not None:
            self.hass.async_create_task(
                self._async_check_setpoint_coordination(trv_id, old_state, trv_state)
            )

        # Window coordination
        self.hass.async_create_task(
            self._window.async_check_coordination(trv_id, trv_state)
        )

        # Preheat coordination
        self.hass.async_create_task(
            self._preheat.async_check_coordination(trv_id, trv_state)
        )

        # Load estimate tracking
        if trv_state.load_estimate is not None:
            self._load_balance.update_estimate(trv_id, trv_state.load_estimate)

        # Update radiator_covered
        if trv_state.radiator_covered is not None:
            self._ext_temp.update_covered(trv_id, trv_state.radiator_covered)

        # Reactive power-cycle detection: check for E10 (time lost) in
        # pushed state data.  This avoids polling the TRV and catches
        # power cycles within one check-in interval (~5 min).
        system_status = trv_state.raw.get("system_status_code")
        if system_status and SW_ERROR_TIME_LOST in str(system_status):
            if trv_id not in self._recovering_trvs:
                _LOGGER.warning(
                    "TRV %s in room '%s' reports time lost (E10) via push data. "
                    "Triggering power-cycle recovery.",
                    trv_id,
                    self._room_name,
                )
                self._recovering_trvs.add(trv_id)
                self.hass.async_create_task(
                    self._async_handle_device_rejoin_and_clear(trv_id)
                )

        # Seed load_room_mean from TRV-reported value
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
            temps = [
                s.local_temperature
                for s in states.values()
                if s.local_temperature is not None
            ]
            if temps:
                self.state.current_temperature = sum(temps) / len(temps)

        # Target temperature: first TRV in configured order
        for trv_id in self._trv_ids:
            trv = states.get(trv_id)
            if trv is not None and trv.occupied_heating_setpoint is not None:
                self.state.target_temperature = trv.occupied_heating_setpoint
                break

        # Pi heating demand: max
        demands = [
            s.pi_heating_demand
            for s in states.values()
            if s.pi_heating_demand is not None
        ]
        self.state.max_pi_heating_demand = max(demands) if demands else 0

        # Heat required
        self.state.heat_required = any(s.heat_required is True for s in states.values())

        # Window open
        self.state.window_open = any(
            s.window_open_detection is not None
            and s.window_open_detection >= WINDOW_OPEN_DETECTED
            for s in states.values()
        )

        # Availability
        now = time.monotonic()
        self.state.available = any(
            (now - self._last_trv_update_time.get(trv_id, 0.0))
            < TRV_AVAILABILITY_TIMEOUT
            for trv_id in self._trv_ids
        )

    # ── Setpoint coordination (thin wrapper) ──────────────────────────

    async def _async_check_setpoint_coordination(
        self, trv_id: str, old_state: TRVState, new_state: TRVState
    ) -> None:
        """Check for manual dial change and coordinate."""
        new_setpoint = await self._setpoint.async_check_manual_change(
            trv_id, old_state, new_state
        )
        if new_setpoint is not None:
            self.state.target_temperature = new_setpoint
            self._notify_state_update()

    async def async_set_room_temperature(self, temperature: float) -> None:
        """Set target temperature for the entire room."""
        await self._setpoint.async_set_room_temperature(temperature)
        self.state.target_temperature = temperature
        self._notify_state_update()

    # ── Heat Availability ─────────────────────────────────────────────

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
            if split_entity_id(entity_state.entity_id)[0] == "climate":
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
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to set heat_available on TRV %s in room '%s'",
                    trv_id,
                    self._room_name,
                )

        self._notify_state_update()

    # ── Remote Climate Sync ───────────────────────────────────────────

    @callback
    def _handle_remote_climate_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle remote climate entity state change."""
        if self._setpoint.is_remote_suppressed():
            return

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        temperature = SetpointDelegate.extract_remote_setpoint(new_state)
        if temperature is None:
            return

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

    # ── Device Announce / Rejoin ──────────────────────────────────────

    @callback
    def _handle_device_announce(self, trv_id: str) -> None:
        """Handle a device announce (rejoin) event from the backend."""
        if trv_id not in self._trv_ids:
            return
        _LOGGER.info(
            "Device announce received for TRV %s in room '%s'. "
            "Scheduling power-cycle recovery check.",
            trv_id,
            self._room_name,
        )

        @callback
        def _rejoin_cb(_now: Any) -> None:
            self.hass.async_create_task(self._async_handle_device_rejoin(trv_id))

        async_call_later(self.hass, 5, _rejoin_cb)

    async def _async_handle_device_rejoin_and_clear(self, trv_id: str) -> None:
        """Run device rejoin recovery and clear the dedup guard.

        Used by the reactive E10 detection path so that the
        ``_recovering_trvs`` guard is released after recovery completes
        (or fails), allowing future E10 reports to trigger recovery again.
        """
        try:
            await self._async_handle_device_rejoin(trv_id)
        finally:
            self._recovering_trvs.discard(trv_id)

    async def _async_handle_device_rejoin(self, trv_id: str) -> None:
        """Handle TRV rejoin: restore settings lost after power cycle."""
        # Schedule recovery
        await self._schedule.async_handle_rejoin(trv_id)

        # Re-send external temperature
        if self._temp_sensor_id:
            temp_state = self.hass.states.get(self._temp_sensor_id)
            if temp_state and temp_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                try:
                    temp = float(temp_state.state)
                    await self._ext_temp.async_send_to_single(trv_id, temp)
                except (ValueError, Exception):  # noqa: BLE001
                    _LOGGER.debug(
                        "Failed to re-send external temp to TRV %s after rejoin",
                        trv_id,
                    )

        # Restore load balancing
        if self._load_balance.enabled:
            try:
                await self._load_balance.async_restore_single(trv_id)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to re-set load_balancing_enable on TRV %s after rejoin",
                    trv_id,
                )

    # ── Delegate-forwarded public API ─────────────────────────────────

    async def async_set_programming_mode_option(self, option: str) -> None:
        """Set the programming mode from a string option."""
        await self._schedule.async_set_mode_option(option)

    async def async_set_programming_mode_value(self, mode: int) -> None:
        """Set the programming mode by integer value on all TRVs."""
        await self._schedule.async_set_mode_value(mode)

    async def async_program_schedule(self, schedule: WeeklySchedule) -> None:
        """Program a weekly schedule to all TRVs in the room."""
        await self._schedule.async_program_schedule(schedule)

    async def async_set_schedule_mode(
        self, enabled: bool, preheat: bool = False
    ) -> None:
        """Set thermostat programming operation mode on all TRVs."""
        await self._schedule.async_set_schedule_mode(enabled, preheat)

    async def async_clear_schedule(self) -> None:
        """Clear schedule on all TRVs and set manual mode."""
        await self._schedule.async_clear_schedule()

    async def async_enable_load_balancing(self) -> None:
        """Enable load balancing for this room."""
        await self._load_balance.async_enable()
        self.state.load_balancing_enabled = True
        self._notify_state_update()

    async def async_disable_load_balancing(self) -> None:
        """Disable load balancing for this room."""
        await self._load_balance.async_disable()
        self.state.load_balancing_enabled = False
        self.state.load_room_mean = None
        self._notify_state_update()

    # ── Test-compatibility proxies ────────────────────────────────────
    # These expose delegate internals so existing tests can assert on them
    # without importing delegates directly. Prefixed with _ to signal they
    # are not part of the stable public API.

    @property
    def _ext_temp_trv(self) -> dict:
        """Proxy: per-TRV external temperature tracking state."""
        return self._ext_temp._trv_state

    @property
    def _forced_window_open_trvs(self) -> set[str]:
        """Proxy: set of TRVs forced open by window coordination."""
        return self._window._forced_trvs

    @property
    def _load_balance_timer(self) -> Any:
        """Proxy: load balance periodic timer."""
        return self._load_balance._timer

    @property
    def _power_cycle_timer(self) -> Any:
        """Proxy: power-cycle detection periodic timer."""
        return self._schedule._power_cycle_timer

    @property
    def _recovering_trvs(self) -> set[str]:
        """Proxy: set of TRVs currently recovering from power cycles."""
        return self._schedule._recovering_trvs

    @property
    def _time_sync_timer(self) -> Any:
        """Proxy: time sync periodic timer."""
        return self._time_sync._timer

    @property
    def _current_schedule(self) -> WeeklySchedule | None:
        """Proxy: currently programmed schedule."""
        return self._schedule.current_schedule

    async def _async_run_load_balance(self) -> None:
        """Proxy: execute one load balancing cycle."""
        room_mean = await self._load_balance._async_run()
        if room_mean is not None:
            self.state.load_room_mean = room_mean
            self._notify_state_update()

    async def _async_check_power_cycle(self) -> None:
        """Proxy: run power-cycle detection check."""
        await self._schedule._async_check_power_cycle()

    async def _async_sync_time_all(self) -> None:
        """Proxy: synchronize time to all TRVs."""
        await self._time_sync.async_sync_all()

    @staticmethod
    def _extract_remote_climate_setpoint(state) -> float | None:
        """Proxy: extract setpoint from a climate entity state."""
        return SetpointDelegate.extract_remote_setpoint(state)
