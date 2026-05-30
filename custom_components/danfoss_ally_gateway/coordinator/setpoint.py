"""Setpoint coordination delegate.

Manages setpoint synchronization across room TRVs:
- Manual dial change detection and forwarding
- Room-level setpoint writes
- Remote climate bidirectional sync with anti-echo
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from ..backend import DanfossBackend, TRVState
from ..const import (
    REMOTE_CLIMATE_SUPPRESS_SECONDS,
    SETPOINT_SOURCE_MANUAL,
    SETPOINT_TYPE_USER,
)

_LOGGER = logging.getLogger(__name__)


class SetpointDelegate:
    """Manages setpoint coordination for a room."""

    def __init__(
        self,
        hass,
        backend: DanfossBackend,
        room_name: str,
        trv_ids: list[str],
        remote_climate_id: str,
    ) -> None:
        self.hass = hass
        self._backend = backend
        self._room_name = room_name
        self._trv_ids = trv_ids
        self._remote_climate_id = remote_climate_id

        self._lock = asyncio.Lock()
        self._programmatic: bool = False
        self._remote_suppress_until: float = 0.0

    @property
    def is_programmatic(self) -> bool:
        """Return True if a programmatic setpoint write is in progress."""
        return self._programmatic

    def is_remote_suppressed(self) -> bool:
        """Return True if remote climate events should be ignored (anti-echo)."""
        return time.monotonic() < self._remote_suppress_until

    async def async_check_manual_change(
        self,
        trv_id: str,
        old_state: TRVState,
        new_state: TRVState,
    ) -> float | None:
        """Check if a TRV's setpoint changed due to manual dial turn.

        Returns the new setpoint if forwarded, None otherwise.
        """
        new_setpoint = new_state.occupied_heating_setpoint
        old_setpoint = old_state.occupied_heating_setpoint

        if new_setpoint is None or new_setpoint == old_setpoint:
            return None

        if new_state.setpoint_change_source != SETPOINT_SOURCE_MANUAL:
            return None

        _LOGGER.info(
            "Manual setpoint change on %s in room '%s': %.1f → %.1f°C",
            trv_id,
            self._room_name,
            old_setpoint or 0,
            new_setpoint,
        )

        # Forward to other TRVs.  The _programmatic guard is checked inside
        # the lock so that a second manual change arriving while the first is
        # still being forwarded will wait and re-evaluate rather than being
        # silently dropped.
        if len(self._trv_ids) > 1:
            async with self._lock:
                if self._programmatic:
                    return None
                self._programmatic = True
                try:
                    for other_trv in self._trv_ids:
                        if other_trv == trv_id:
                            continue
                        try:
                            await self._backend.async_send_setpoint_command(
                                other_trv, new_setpoint, SETPOINT_TYPE_USER
                            )
                        except Exception:  # noqa: BLE001
                            _LOGGER.exception(
                                "Failed to forward setpoint to TRV %s", other_trv
                            )
                finally:
                    self._programmatic = False

        # Sync to remote climate
        await self._async_sync_remote_climate(new_setpoint)

        return new_setpoint

    async def async_set_room_temperature(self, temperature: float) -> None:
        """Set target temperature for the entire room."""
        _LOGGER.debug(
            "Setting room '%s' temperature to %.1f°C", self._room_name, temperature
        )

        async with self._lock:
            self._programmatic = True
            try:
                for trv_id in self._trv_ids:
                    try:
                        await self._backend.async_set_occupied_heating_setpoint(
                            trv_id, temperature
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("Failed to set setpoint on TRV %s", trv_id)
            finally:
                self._programmatic = False

        await self._async_sync_remote_climate(temperature)

    # ── Remote climate helpers ────────────────────────────────────────

    @staticmethod
    def extract_remote_setpoint(state) -> float | None:
        """Extract heating setpoint from a climate entity state."""
        attrs = state.attributes

        target_temp_low = attrs.get("target_temp_low")
        if target_temp_low is not None:
            try:
                return float(target_temp_low)
            except (ValueError, TypeError):
                pass

        temperature = attrs.get("temperature")
        if temperature is not None:
            try:
                return float(temperature)
            except (ValueError, TypeError):
                pass

        return None

    async def _async_sync_remote_climate(self, temperature: float) -> None:
        """Sync setpoint to the remote climate entity."""
        if not self._remote_climate_id:
            return

        remote_state = self.hass.states.get(self._remote_climate_id)
        if remote_state is None or remote_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            return

        self._remote_suppress_until = time.monotonic() + REMOTE_CLIMATE_SUPPRESS_SECONDS

        service_data: dict[str, Any] = {"entity_id": self._remote_climate_id}

        target_temp_low = remote_state.attributes.get("target_temp_low")
        if target_temp_low is not None:
            target_temp_high = remote_state.attributes.get("target_temp_high")
            service_data["target_temp_low"] = temperature
            if target_temp_high is not None:
                service_data["target_temp_high"] = target_temp_high
        else:
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
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Failed to sync setpoint to remote climate %s in room '%s'",
                self._remote_climate_id,
                self._room_name,
            )
