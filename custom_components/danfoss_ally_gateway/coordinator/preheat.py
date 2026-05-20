"""Preheat coordination delegate.

Forwards preheat commands to other TRVs in the room when one TRV
reports preheat_status=true, with deduplication.
"""

from __future__ import annotations

import logging

from ..backend import DanfossBackend, TRVState

_LOGGER = logging.getLogger(__name__)


class PreheatDelegate:
    """Manages preheat coordination for a room."""

    def __init__(
        self,
        backend: DanfossBackend,
        room_name: str,
        trv_ids: list[str],
    ) -> None:
        self._backend = backend
        self._room_name = room_name
        self._trv_ids = trv_ids
        self._last_forwarded: dict[str, int] = {}

    async def async_check_coordination(self, trv_id: str, trv_state: TRVState) -> None:
        """Check and coordinate preheat events across room TRVs."""
        if len(self._trv_ids) <= 1:
            return

        if not trv_state.preheat_status or trv_state.preheat_time is None:
            return

        # Deduplicate
        if self._last_forwarded.get(trv_id) == trv_state.preheat_time:
            return

        self._last_forwarded[trv_id] = trv_state.preheat_time

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
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to forward preheat to TRV %s", other_trv)
