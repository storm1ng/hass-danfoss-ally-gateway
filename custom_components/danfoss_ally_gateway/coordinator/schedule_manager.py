"""Schedule management delegate.

Manages TRV schedule programming, verification, mode control,
schedule entity watching, and power-cycle recovery.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_call_later

from ..backend import DanfossBackend
from ..const import (
    POWER_CYCLE_CHECK_INTERVAL,
    PROGRAMMING_MODE_FROM_INT,
    PROGRAMMING_MODE_TO_INT,
    SCHEDULE_DOW_ALL,
    SCHEDULE_MODE_ECO,
    SCHEDULE_MODE_MANUAL,
    SCHEDULE_MODE_SCHEDULE,
    SCHEDULE_MODE_SCHEDULE_PREHEAT,
    SW_ERROR_TIME_LOST,
)
from ..schedule import (
    WeeklySchedule,
    apply_midnight_crossing,
    build_zcl_set_weekly_payloads,
    from_ha_schedule,
    parse_zcl_get_weekly_response,
    schedules_match,
)

_LOGGER = logging.getLogger(__name__)


class ScheduleDelegate:
    """Manages schedule programming and mode control for a room."""

    def __init__(
        self,
        hass,
        backend: DanfossBackend,
        room_name: str,
        trv_ids: list[str],
        schedule_entity_id: str,
        at_home_temp: float,
        away_temp: float,
        preheat_enabled: bool,
        notify_fn: Any,
    ) -> None:
        self.hass = hass
        self._backend = backend
        self._room_name = room_name
        self._trv_ids = trv_ids
        self._schedule_entity_id = schedule_entity_id
        self._at_home_temp = at_home_temp
        self._away_temp = away_temp
        self._preheat_enabled = preheat_enabled
        self._notify_fn = notify_fn

        self._current_schedule: WeeklySchedule | None = None
        self._mode: int = SCHEDULE_MODE_MANUAL
        self._power_cycle_timer: CALLBACK_TYPE | None = None

        # Power-cycle recovery dedup guard: TRVs currently being recovered
        self._recovering_trvs: set[str] = set()

    @property
    def current_schedule(self) -> WeeklySchedule | None:
        """Return the currently programmed schedule."""
        return self._current_schedule

    @property
    def mode(self) -> int:
        """Return the current programming mode integer."""
        return self._mode

    @property
    def mode_option(self) -> str:
        """Return the current programming mode as a string."""
        return PROGRAMMING_MODE_FROM_INT.get(self._mode, "manual")

    # ── Programming mode ──────────────────────────────────────────────

    async def async_set_mode_option(self, option: str) -> None:
        """Set programming mode from a string option."""
        mode = PROGRAMMING_MODE_TO_INT.get(option)
        if mode is None:
            raise ValueError(f"Invalid programming mode option: {option}")
        await self.async_set_mode_value(mode)

    async def async_set_mode_value(self, mode: int) -> None:
        """Set programming mode by integer value on all TRVs."""
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

        self._mode = mode
        self._notify_fn()

    async def async_set_schedule_mode(
        self, enabled: bool, preheat: bool = False, eco: bool = False
    ) -> None:
        """Set thermostat programming operation mode on all TRVs."""
        if eco:
            mode = SCHEDULE_MODE_ECO
        elif enabled:
            mode = SCHEDULE_MODE_SCHEDULE_PREHEAT if preheat else SCHEDULE_MODE_SCHEDULE
        else:
            mode = SCHEDULE_MODE_MANUAL
        await self.async_set_mode_value(mode)

    # ── Schedule programming ──────────────────────────────────────────

    async def async_program_schedule(self, schedule: WeeklySchedule) -> None:
        """Program a weekly schedule to all TRVs in the room."""
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

        # Clear existing
        for trv_id in self._trv_ids:
            try:
                await self._backend.async_clear_weekly_schedule(trv_id)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to clear schedule on TRV %s in room '%s'",
                    trv_id,
                    self._room_name,
                )

        processed = apply_midnight_crossing(schedule)
        payloads = build_zcl_set_weekly_payloads(processed)

        if not payloads:
            _LOGGER.info("No schedule events to program for room '%s'", self._room_name)
            self._current_schedule = schedule
            return

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

        # Read-back verification
        await self._async_verify_schedule(self._trv_ids[0], processed)
        self._current_schedule = schedule
        _LOGGER.info("Schedule programming complete for room '%s'", self._room_name)

    async def _async_verify_schedule(
        self, trv_id: str, expected: WeeklySchedule
    ) -> bool:
        """Read back schedule from a TRV and compare."""
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

        await self.async_set_schedule_mode(enabled=False)
        self._current_schedule = None

    # ── Schedule entity watching ──────────────────────────────────────

    @callback
    def handle_schedule_entity_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle schedule helper entity state change."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        self.hass.async_create_task(self.async_sync_from_entity())

    async def async_sync_from_entity(self) -> None:
        """Read HA schedule helper entity and program to TRVs."""
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
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Failed to program schedule from entity %s for room '%s'",
                self._schedule_entity_id,
                self._room_name,
            )
            return

        mode = (
            SCHEDULE_MODE_SCHEDULE_PREHEAT
            if self._preheat_enabled
            else SCHEDULE_MODE_SCHEDULE
        )
        if self._mode != mode:
            await self.async_set_mode_value(mode)

    # ── Power cycle detection & recovery ──────────────────────────────

    def schedule_power_cycle_check(self) -> None:
        """Schedule periodic power-cycle detection check."""

        @callback
        def _run(_now: Any) -> None:
            self._power_cycle_timer = None
            self.hass.async_create_task(self._async_check_power_cycle())
            self.schedule_power_cycle_check()

        self._power_cycle_timer = async_call_later(
            self.hass, POWER_CYCLE_CHECK_INTERVAL, _run
        )

    async def _async_check_power_cycle(self) -> None:
        """Safety-net fallback for power-cycle detection (runs every 6 hours).

        Primary detection is reactive: the ``_handle_trv_state_update``
        callback inspects ``system_status_code`` in every pushed state
        update for E10 (time lost).  The ``_handle_device_announce``
        callback handles explicit Zigbee rejoins.

        This method is a rare fallback for edge cases where both the
        device_announce event and the pushed E10 flag were missed (e.g.
        HA restart overlapping with a TRV power cycle, or Z2M not
        including system_status_code in its retained state).

        It reads ``sw_error_code`` and ``GetWeeklySchedule`` from the
        TRV via Zigbee, which wakes the device.  The 6-hour interval
        keeps radio traffic to ~8 messages/day/TRV.
        """
        if self._current_schedule is None:
            return  # Nothing to verify

        for trv_id in self._trv_ids:
            # Check E10 via Zigbee read (fallback — normally caught by push data)
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
                try:
                    await self._backend.async_sync_time(trv_id)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Failed to re-sync time to TRV %s", trv_id)
                await self.async_handle_rejoin(trv_id)
                continue

            try:
                transitions = await self._backend.async_get_weekly_schedule(
                    trv_id, 0x02
                )
            except Exception:  # noqa: BLE001
                continue

            if not transitions:
                _LOGGER.warning(
                    "TRV %s in room '%s' has empty schedule (power cycle?). "
                    "Triggering recovery.",
                    trv_id,
                    self._room_name,
                )
                await self.async_handle_rejoin(trv_id)

    async def async_handle_rejoin(self, trv_id: str) -> None:
        """Re-program schedule on a single TRV after power cycle."""
        if self._current_schedule is None:
            return

        try:
            transitions = await self._backend.async_get_weekly_schedule(trv_id, 0x02)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Failed to read schedule from TRV %s after rejoin", trv_id)
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

            if self._mode != SCHEDULE_MODE_MANUAL:
                try:
                    await self._backend.async_set_programming_mode(trv_id, self._mode)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Failed to re-set programming mode on TRV %s after rejoin",
                        trv_id,
                    )

    async def _async_reprogram_single_trv(self, trv_id: str) -> None:
        """Re-program the current schedule on a single TRV."""
        if self._current_schedule is None:
            return

        try:
            await self._backend.async_clear_weekly_schedule(trv_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to clear schedule on TRV %s", trv_id)

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

    def cancel_timer(self) -> None:
        """Cancel the power-cycle check timer."""
        if self._power_cycle_timer is not None:
            self._power_cycle_timer()
            self._power_cycle_timer = None
