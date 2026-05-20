"""Window open coordination delegate.

Manages window open detection coordination across room TRVs per Danfoss spec:
- When a TRV detects window open (state >= 3), force external_window_open on others
- Deactivate when detecting TRV(s) close and forced TRVs confirm (state 4)
- Clear orphaned external_window_open after HA restart
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..backend import DanfossBackend, TRVState
from ..const import WINDOW_OPEN_DETECTED, WINDOW_OPEN_EXTERNAL_OPEN

_LOGGER = logging.getLogger(__name__)


class WindowDelegate:
    """Manages window open coordination for a room."""

    def __init__(
        self,
        backend: DanfossBackend,
        room_name: str,
        trv_ids: list[str],
        get_trv_states: Callable[[], dict[str, TRVState]],
    ) -> None:
        self._backend = backend
        self._room_name = room_name
        self._trv_ids = trv_ids
        self._get_trv_states = get_trv_states
        self._forced_trvs: set[str] = set()

    async def async_check_coordination(self, trv_id: str, trv_state: TRVState) -> None:
        """Check and coordinate window open events across room TRVs."""
        if len(self._trv_ids) <= 1:
            return

        window_state = trv_state.window_open_detection
        if window_state is None:
            return

        if window_state == WINDOW_OPEN_DETECTED:
            # Local detection — don't cascade if this TRV was already forced
            if trv_id in self._forced_trvs:
                _LOGGER.debug(
                    "TRV %s in room '%s' reported state 3 but is already forced, "
                    "skipping cascade",
                    trv_id,
                    self._room_name,
                )
                return
            other_trvs = [t for t in self._trv_ids if t != trv_id]
            newly_forced = [t for t in other_trvs if t not in self._forced_trvs]

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
                        self._forced_trvs.add(other_trv)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "Failed to set external_window_open on %s", other_trv
                        )
        elif window_state == WINDOW_OPEN_EXTERNAL_OPEN:
            # State 4: forced open by gateway. Clear orphans after restart.
            if trv_id not in self._forced_trvs:
                _LOGGER.info(
                    "Clearing orphaned window_open_external on %s in room '%s' "
                    "(not tracked after restart)",
                    trv_id,
                    self._room_name,
                )
                try:
                    await self._backend.async_set_external_window_open(trv_id, False)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Failed to clear orphaned external_window_open on %s",
                        trv_id,
                    )
        else:
            # Check if all forced TRVs confirmed and detecting TRV(s) closed
            if self._forced_trvs:
                trv_states = self._get_trv_states()
                forced_with_state = [t for t in self._forced_trvs if t in trv_states]
                all_confirmed = len(forced_with_state) > 0 and all(
                    trv_states[t].window_open_detection == WINDOW_OPEN_EXTERNAL_OPEN
                    for t in forced_with_state
                )
                if all_confirmed:
                    any_still_open = any(
                        (s.window_open_detection or 0) == WINDOW_OPEN_DETECTED
                        for tid, s in trv_states.items()
                        if tid not in self._forced_trvs
                    )
                    if not any_still_open:
                        _LOGGER.info(
                            "Window closed in room '%s', deactivating forced open",
                            self._room_name,
                        )
                        for forced_trv in list(self._forced_trvs):
                            try:
                                await self._backend.async_set_external_window_open(
                                    forced_trv, False
                                )
                            except Exception:  # noqa: BLE001
                                _LOGGER.exception(
                                    "Failed to clear external_window_open on %s",
                                    forced_trv,
                                )
                        self._forced_trvs.clear()
