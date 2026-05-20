"""Load balancing delegate.

Manages the 15-minute load balancing cycle per Danfoss spec:
- Collects load_estimate from each TRV
- Discards stale/invalid values
- Computes room mean and writes to all TRVs
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later

from ..backend import DanfossBackend
from ..const import (
    LOAD_BALANCE_DISABLED_VALUE,
    LOAD_BALANCE_INTERVAL,
    LOAD_BALANCE_INVALID_THRESHOLD,
    LOAD_BALANCE_MAX_AGE,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class LoadEstimateEntry:
    """A timestamped load estimate from a TRV."""

    value: int
    timestamp: float  # time.monotonic()


class LoadBalanceDelegate:
    """Manages load balancing for a multi-TRV room."""

    def __init__(
        self,
        hass,
        backend: DanfossBackend,
        room_name: str,
        trv_ids: list[str],
    ) -> None:
        self.hass = hass
        self._backend = backend
        self._room_name = room_name
        self._trv_ids = trv_ids

        self._estimates: dict[str, LoadEstimateEntry] = {}
        self._timer: CALLBACK_TYPE | None = None
        self._enabled: bool = len(trv_ids) > 1

    @property
    def enabled(self) -> bool:
        """Return whether load balancing is enabled."""
        return self._enabled

    def update_estimate(self, trv_id: str, value: int) -> None:
        """Record a new load estimate from a TRV."""
        self._estimates[trv_id] = LoadEstimateEntry(
            value=value, timestamp=time.monotonic()
        )

    def schedule_cycle(self) -> None:
        """Schedule the next load balance cycle."""

        @callback
        def _run(_now: Any) -> None:
            self._timer = None
            self.hass.async_create_task(self._async_run())
            self.schedule_cycle()

        self._timer = async_call_later(self.hass, LOAD_BALANCE_INTERVAL, _run)

    async def _async_run(self) -> int | None:
        """Execute one load balancing cycle. Returns computed mean or None."""
        if len(self._trv_ids) <= 1:
            return None

        now = time.monotonic()
        valid: list[int] = []

        for trv_id in self._trv_ids:
            entry = self._estimates.get(trv_id)
            if entry is None:
                continue
            if now - entry.timestamp > LOAD_BALANCE_MAX_AGE:
                _LOGGER.debug(
                    "Discarding stale load estimate from %s (age: %.0fs)",
                    trv_id,
                    now - entry.timestamp,
                )
                continue
            if entry.value == LOAD_BALANCE_DISABLED_VALUE:
                continue
            if entry.value < LOAD_BALANCE_INVALID_THRESHOLD:
                _LOGGER.debug(
                    "Discarding invalid load estimate from %s: %d",
                    trv_id,
                    entry.value,
                )
                continue
            valid.append(entry.value)

        if not valid:
            _LOGGER.debug(
                "No valid load estimates for room '%s', skipping", self._room_name
            )
            return None

        room_mean = round(sum(valid) / len(valid))

        _LOGGER.debug(
            "Load balance for room '%s': mean=%d (from %d TRVs)",
            self._room_name,
            room_mean,
            len(valid),
        )

        for trv_id in self._trv_ids:
            try:
                await self._backend.async_set_load_room_mean(trv_id, room_mean)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to set load_room_mean on TRV %s", trv_id)

        return room_mean

    async def async_enable(self) -> None:
        """Enable load balancing."""
        if len(self._trv_ids) <= 1:
            return
        self._enabled = True
        for trv_id in self._trv_ids:
            try:
                await self._backend.async_set_load_balancing_enable(trv_id, True)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to set load_balancing_enable on %s", trv_id)
        if self._timer is None:
            self.schedule_cycle()

    async def async_disable(self) -> None:
        """Disable load balancing."""
        self._enabled = False
        if self._timer is not None:
            self._timer()
            self._timer = None
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

    async def async_restore_single(self, trv_id: str) -> None:
        """Restore load_balancing_enable on a single TRV (after rejoin)."""
        if self._enabled:
            await self._backend.async_set_load_balancing_enable(trv_id, True)

    async def async_setup_trvs(self) -> None:
        """Write load_balancing_enable=true to all TRVs on setup."""
        for trv_id in self._trv_ids:
            try:
                await self._backend.async_set_load_balancing_enable(trv_id, True)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to set load_balancing_enable on %s", trv_id)

    def cancel_timer(self) -> None:
        """Cancel the periodic timer."""
        if self._timer is not None:
            self._timer()
            self._timer = None
