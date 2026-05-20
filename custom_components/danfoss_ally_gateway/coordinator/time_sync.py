"""Time synchronization delegate.

Manages periodic time synchronization to all TRVs in a room.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later

from ..backend import DanfossBackend
from ..const import TIME_SYNC_INTERVAL

_LOGGER = logging.getLogger(__name__)


class TimeSyncDelegate:
    """Manages time synchronization for a room's TRVs."""

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

        self._timer: CALLBACK_TYPE | None = None

    def schedule_sync(self) -> None:
        """Schedule the next time sync."""

        @callback
        def _run(_now: Any) -> None:
            self._timer = None
            self.hass.async_create_task(self.async_sync_all())
            self.schedule_sync()

        self._timer = async_call_later(self.hass, TIME_SYNC_INTERVAL, _run)

    async def async_sync_all(self) -> None:
        """Synchronize time to all TRVs in the room."""
        _LOGGER.debug("Syncing time to all TRVs in room '%s'", self._room_name)

        for trv_id in self._trv_ids:
            try:
                await self._backend.async_sync_time(trv_id)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to sync time to TRV %s in room '%s'",
                    trv_id,
                    self._room_name,
                )

    def cancel_timer(self) -> None:
        """Cancel the periodic timer."""
        if self._timer is not None:
            self._timer()
            self._timer = None
