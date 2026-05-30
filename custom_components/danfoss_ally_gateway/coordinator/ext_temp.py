"""External temperature forwarding delegate.

Manages per-TRV external temperature forwarding with Danfoss timing specs:
- Minimum/maximum intervals per covered/exposed mode
- Change threshold filtering
- Deferred sends when rate-limited
- Max-interval resend timers to prevent TRV timeout
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_call_later

from ..backend import DanfossBackend
from ..const import (
    EXT_TEMP_CHANGE_THRESHOLD,
    EXT_TEMP_COVERED_MAX_INTERVAL,
    EXT_TEMP_COVERED_MIN_INTERVAL,
    EXT_TEMP_EXPOSED_MAX_INTERVAL,
    EXT_TEMP_EXPOSED_MIN_INTERVAL,
    EXTERNAL_TEMP_DISABLED,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ExtTempTRVState:
    """Per-TRV tracking state for external temperature forwarding."""

    covered: bool = False  # from TRVState.radiator_covered
    last_temp_sent: float | None = None
    last_send_time: float = 0.0
    timer: CALLBACK_TYPE | None = None  # per-TRV max-interval resend timer


class ExtTempDelegate:
    """Manages external temperature forwarding for a room's TRVs."""

    def __init__(
        self,
        hass,
        backend: DanfossBackend,
        room_name: str,
        trv_ids: list[str],
        temp_sensor_id: str,
    ) -> None:
        self.hass = hass
        self._backend = backend
        self._room_name = room_name
        self._trv_ids = trv_ids
        self._temp_sensor_id = temp_sensor_id

        # Per-TRV tracking
        self._trv_state: dict[str, ExtTempTRVState] = {
            trv_id: ExtTempTRVState() for trv_id in trv_ids
        }

    def rebuild_trv_ids(self, trv_ids: list[str]) -> None:
        """Rebuild tracking after TRV IDs are resolved."""
        self._trv_state = {trv_id: ExtTempTRVState() for trv_id in trv_ids}

    def update_covered(self, trv_id: str, covered: bool) -> None:
        """Update the radiator_covered state for a TRV."""
        if trv_id in self._trv_state:
            self._trv_state[trv_id].covered = covered

    async def async_send_initial(self) -> None:
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
                await self.async_send_all(temp)
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid initial temperature from %s: %s",
                    self._temp_sensor_id,
                    sensor_state.state,
                )

    @callback
    def handle_temp_sensor_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle external temperature sensor state change."""
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        try:
            new_temp = float(new_state.state)
        except (ValueError, TypeError):
            return

        now = time.monotonic()

        for trv_id, ext_state in self._trv_state.items():
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
                    self._schedule_deferred_send(trv_id, new_temp, delay)
                continue

            self.hass.async_create_task(self._async_send_to_trv(trv_id, new_temp))

    def _schedule_deferred_send(
        self, trv_id: str, temperature: float, delay: float
    ) -> None:
        """Schedule a deferred ext temp send for a single TRV."""
        ext_state = self._trv_state[trv_id]

        @callback
        def _delayed_send(_now: Any) -> None:
            ext_state.timer = None
            self.hass.async_create_task(self._async_send_to_trv(trv_id, temperature))

        ext_state.timer = async_call_later(self.hass, delay, _delayed_send)

    async def _async_send_to_trv(self, trv_id: str, temperature: float) -> None:
        """Send external temperature to a single TRV and update tracking."""
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

        ext_state = self._trv_state[trv_id]
        ext_state.last_temp_sent = temperature
        ext_state.last_send_time = time.monotonic()

        self._schedule_max_interval(trv_id, temperature)

    async def async_send_all(self, temperature: float) -> None:
        """Send external temperature to all room TRVs."""
        _LOGGER.debug(
            "Sending external temp %.1f°C to room '%s'",
            temperature,
            self._room_name,
        )
        for trv_id in self._trv_ids:
            await self._async_send_to_trv(trv_id, temperature)

    async def async_send_to_single(self, trv_id: str, temperature: float) -> None:
        """Send external temperature to a specific TRV (for rejoin recovery)."""
        await self._backend.async_set_external_temperature(trv_id, temperature)

    def _schedule_max_interval(self, trv_id: str, temperature: float) -> None:
        """Schedule a resend at the max interval to prevent TRV timeout."""
        ext_state = self._trv_state[trv_id]

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
            self.hass.async_create_task(self._async_send_to_trv(trv_id, current_temp))

        ext_state.timer = async_call_later(self.hass, max_interval, _resend)

    async def async_disable_all(self) -> None:
        """Disable external temp on all TRVs (send -8000) during teardown."""
        for trv_id in self._trv_ids:
            try:
                await self._backend.async_set_external_temperature(
                    trv_id, EXTERNAL_TEMP_DISABLED / 100
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to disable ext temp on %s during teardown", trv_id
                )

    def cancel_timers(self) -> None:
        """Cancel all per-TRV timers."""
        for ext_state in self._trv_state.values():
            if ext_state.timer is not None:
                ext_state.timer()
                ext_state.timer = None
