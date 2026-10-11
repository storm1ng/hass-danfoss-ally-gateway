"""Window open coordination delegate.

Manages window open detection coordination across room TRVs per Danfoss spec
(AU417130778872en, section 3.3):

Activation:
- When any TRV reports local open detection (0x4000 == 3), the gateway writes
  External Open Window (0x4003) = 1 to *all* TRVs in the room, including the
  detecting TRV.
- Each TRV that is locally closed then immediately reports 0x4000 == 4
  ("external open, detected closed locally"). A TRV that is itself locally
  open stays at state 3 until its own local detection clears, then moves to 4.

Deactivation:
- When *all* forced TRVs report 0x4000 == 4 (i.e. every window in the room is
  closed again), the gateway writes 0x4003 = 0 to all forced TRVs, which
  returns them to 0x4000 == 0/1.

Orphan recovery:
- A TRV reporting state 4 while nothing is tracked (e.g. after an HA restart)
  has its external_window_open cleared.
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
            # Local detection on any TRV forces External Open on *all* room TRVs
            # (including the detector), per spec. Once every TRV is forced this
            # is a no-op, so a second TRV also reporting state 3 cannot deadlock.
            await self._async_activate()
            return

        if (
            window_state == WINDOW_OPEN_EXTERNAL_OPEN
            and trv_id not in self._forced_trvs
        ):
            # State 4 on a TRV we are not tracking is an orphan (e.g. left over
            # after an HA restart). Clear it so the TRV is not stuck.
            await self._async_clear_orphan(trv_id)
            return

        # Any other update (including a forced TRV settling into state 4) may be
        # the point where the whole room has closed again. Re-evaluate.
        await self._async_deactivate_if_closed()

    async def _async_activate(self) -> None:
        """Force External Open Window on every TRV in the room."""
        newly_forced = [t for t in self._trv_ids if t not in self._forced_trvs]
        if not newly_forced:
            return

        _LOGGER.info(
            "Window open detected in room '%s', forcing external open on %d TRVs",
            self._room_name,
            len(newly_forced),
        )
        for trv in newly_forced:
            try:
                await self._backend.async_set_external_window_open(trv, True)
                self._forced_trvs.add(trv)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to set external_window_open on %s", trv)

    async def _async_clear_orphan(self, trv_id: str) -> None:
        """Clear an orphaned external_window_open (untracked state 4)."""
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
                "Failed to clear orphaned external_window_open on %s", trv_id
            )

    async def _async_deactivate_if_closed(self) -> None:
        """Clear External Open on all forced TRVs once the room has closed.

        The room is considered closed again only when *every* forced TRV reports
        state 4 (external open, detected closed locally). As long as any forced
        TRV is still locally detecting (state 3), at least one window in the room
        is open, so we keep the external open active.
        """
        if not self._forced_trvs:
            return

        trv_states = self._get_trv_states()
        forced_with_state = [t for t in self._forced_trvs if t in trv_states]
        all_confirmed = len(forced_with_state) == len(self._forced_trvs) and all(
            trv_states[t].window_open_detection == WINDOW_OPEN_EXTERNAL_OPEN
            for t in forced_with_state
        )
        if not all_confirmed:
            return

        _LOGGER.info(
            "Window closed in room '%s', deactivating forced external open",
            self._room_name,
        )
        for forced_trv in list(self._forced_trvs):
            try:
                await self._backend.async_set_external_window_open(forced_trv, False)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to clear external_window_open on %s", forced_trv
                )
        self._forced_trvs.clear()
