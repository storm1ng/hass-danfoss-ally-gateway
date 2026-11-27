"""Radiator covered quirk for rooms with window sensors.

TRVs in Covered Radiator Mode (radiator_covered = true) ignore External Open
Window (0x4003): they never report open and keep heating. To make window
sensors work for them anyway, radiator_covered is switched off while the
room's window sensors report open, and switched back on once they report
closed.

TRVs that had radiator_covered switched off are kept in HA storage, so the
setting is restored even if HA restarts while a window is open. A TRV stays
stored until it reports radiator_covered = true again, so a lost restore
write is retried on the next close or restart.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.storage import Store

from ..backend import DanfossBackend, TRVState
from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.covered_suspended"
STORAGE_VERSION = 1
_DATA_KEY = f"{DOMAIN}_covered_suspended"


class _SuspendedTRVs:
    """Persistent set of TRV IDs with radiator_covered switched off.

    Shared by all rooms: TRV IDs are device registry UUIDs and a TRV belongs
    to one room only.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, list[str]]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self.trv_ids: set[str] = set()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self.trv_ids = set(data.get("trv_ids", []))

    async def async_save(self) -> None:
        await self._store.async_save({"trv_ids": sorted(self.trv_ids)})


@singleton(_DATA_KEY)
async def _async_get_suspended(hass: HomeAssistant) -> _SuspendedTRVs:
    suspended = _SuspendedTRVs(hass)
    await suspended.async_load()
    return suspended


class CoveredQuirkDelegate:
    """Suspends radiator_covered on a room's TRVs while a window is open."""

    def __init__(
        self,
        hass: HomeAssistant,
        backend: DanfossBackend,
        room_name: str,
        trv_ids: list[str],
        get_trv_states: Callable[[], dict[str, TRVState]],
    ) -> None:
        self.hass = hass
        self._backend = backend
        self._room_name = room_name
        self._trv_ids = trv_ids
        self._get_trv_states = get_trv_states
        self._suspended: _SuspendedTRVs | None = None

    async def async_setup(self) -> None:
        """Load the stored set of suspended TRVs."""
        self._suspended = await _async_get_suspended(self.hass)

    def is_suspended(self, trv_id: str) -> bool:
        """Return True if radiator_covered is suspended on the TRV."""
        return self._suspended is not None and trv_id in self._suspended.trv_ids

    async def async_suspend_all(self) -> None:
        """Switch radiator_covered off on every covered TRV in the room.

        Call before writing External Open Window. TRVs already stored as
        suspended get the write again (e.g. after a restart).
        """
        if self._suspended is None:
            return
        trv_states = self._get_trv_states()
        for trv_id in self._trv_ids:
            trv_state = trv_states.get(trv_id)
            if self.is_suspended(trv_id) or (
                trv_state is not None and trv_state.radiator_covered is True
            ):
                await self._async_suspend(trv_id)

    async def async_restore_all(self) -> None:
        """Switch radiator_covered back on for every suspended TRV in the room.

        Call after clearing External Open Window. TRVs stay stored until they
        report radiator_covered = true (see async_check).
        """
        for trv_id in self._trv_ids:
            if not self.is_suspended(trv_id):
                continue
            _LOGGER.info(
                "Restoring radiator_covered on TRV %s in room '%s'",
                trv_id,
                self._room_name,
            )
            try:
                await self._backend.async_set_radiator_covered(trv_id, True)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to restore radiator_covered on TRV %s in room '%s'",
                    trv_id,
                    self._room_name,
                )

    async def async_check(
        self, trv_id: str, trv_state: TRVState, window_open: bool
    ) -> bool:
        """Handle a TRV reporting radiator_covered = true.

        Returns True if radiator_covered was just suspended on the TRV, so the
        caller must write External Open Window to it again.
        """
        if self._suspended is None or trv_state.radiator_covered is not True:
            return False

        if self.is_suspended(trv_id):
            if not window_open:
                # Restore confirmed
                self._suspended.trv_ids.discard(trv_id)
                await self._suspended.async_save()
            return False

        if not window_open:
            return False

        # Covered state arrived after the window opened (e.g. HA start)
        await self._async_suspend(trv_id)
        return True

    async def _async_suspend(self, trv_id: str) -> None:
        """Store the TRV as suspended, then switch radiator_covered off."""
        assert self._suspended is not None
        if trv_id not in self._suspended.trv_ids:
            self._suspended.trv_ids.add(trv_id)
            await self._suspended.async_save()

        _LOGGER.info(
            "Suspending radiator_covered on TRV %s in room '%s' while a window "
            "is open (covered TRVs ignore external window open)",
            trv_id,
            self._room_name,
        )
        try:
            await self._backend.async_set_radiator_covered(trv_id, False)
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Failed to suspend radiator_covered on TRV %s in room '%s'",
                trv_id,
                self._room_name,
            )
