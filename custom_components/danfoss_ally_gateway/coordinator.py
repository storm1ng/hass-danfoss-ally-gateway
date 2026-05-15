"""Room coordinator for Danfoss Ally Gateway.

Implements per-room coordination logic:
- TRV state subscription and aggregation
- Room state computation (temperature, demand, window, availability)
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from custom_components.danfoss_ally_gateway.const import (
    CONF_ROOM_NAME,
    CONF_TRV_ENTITIES,
    TRV_AVAILABILITY_TIMEOUT,
    WINDOW_OPEN_DETECTED,
)

from .backend import DanfossBackend, TRVState
from .backend.z2m import Z2MBackend

_LOGGER = logging.getLogger(__name__)


@dataclass
class RoomState:
    """Aggregated state for a room, derived from TRV states."""

    room_name: str = ""
    current_temperature: float | None = None  # From ext sensor or TRV average
    target_temperature: float | None = None  # Room setpoint (synced)
    max_pi_heating_demand: int = 0
    heat_required: bool = False
    heat_available: bool | None = None
    window_open: bool = False
    load_room_mean: int | None = None
    available: bool = False  # True when at least one TRV has reported recently
    trv_states: dict[str, TRVState] = field(default_factory=dict)


# Type for room state update listeners
RoomStateCallback = Callable[[RoomState], None]


class RoomCoordinator:
    """Coordinates all gateway logic for a single room.

    One instance is created per room subentry. It manages:
    - Subscribing to TRV state changes via the backend
    - Aggregating TRV state into a room-level view
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

        # Current room state
        self.state = RoomState(room_name=self._room_name)

        # Room state listeners (for entities to subscribe)
        self._state_callbacks: list[RoomStateCallback] = []

        # Cleanup callbacks
        self._unsub_callbacks: list[CALLBACK_TYPE] = []

        # TRV availability tracking: last update time per TRV
        self._last_trv_update_time: dict[str, float] = {}

    @property
    def room_name(self) -> str:
        """Return the room name."""
        return self._room_name

    @property
    def trv_ids(self) -> list[str]:
        """Return the list of TRV IDs in this room."""
        return self._trv_ids

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

    # ── Lifecycle ──────────────────────────────────────────────────────

    def _resolve_trv_id(self, trv_id: str) -> str:
        """Resolve a device registry ID to a backend-specific TRV identifier.

        For Z2M: returns the device name (= Z2M friendly name = MQTT topic).
        For ZHA: returns the climate entity_id for the device.
        Falls back to returning trv_id unchanged for backwards compatibility
        with subentries that stored friendly names or entity IDs directly.
        """
        device_reg = dr.async_get(self.hass)
        device = device_reg.async_get(trv_id)
        if device is None:
            # Not a device registry ID — assume it's already a resolved
            # identifier (backwards compat: old subentries stored friendly
            # names for Z2M or entity IDs for ZHA).
            return trv_id

        if isinstance(self._backend, Z2MBackend):
            # Z2M: device.name is the Z2M friendly name (MQTT topic segment).
            # Do NOT use name_by_user — HA-side renames don't change the
            # Z2M topic.
            return device.name

        # ZHA Implementation would go here (not implemented yet)

        _LOGGER.warning(
            "No climate entity found for device %s, using device ID as-is",
            trv_id,
        )
        return trv_id

    async def async_setup(self) -> None:
        """Set up the room coordinator."""
        _LOGGER.info("Setting up room coordinator for '%s'", self._room_name)

        # Resolve device registry IDs to backend-specific identifiers
        self._trv_ids = [self._resolve_trv_id(tid) for tid in self._trv_ids]

        # Subscribe to TRV state changes
        unsub = self._backend.register_state_callback(self._handle_trv_state_update)
        self._unsub_callbacks.append(unsub)

        # Subscribe to each TRV
        for trv_id in self._trv_ids:
            await self._backend.async_subscribe_trv(trv_id)

    async def async_teardown(self) -> None:
        """Tear down the room coordinator."""
        _LOGGER.info("Tearing down room coordinator for '%s'", self._room_name)

        # Unsubscribe from everything
        for unsub in self._unsub_callbacks:
            unsub()
        self._unsub_callbacks.clear()

        # Unsubscribe from TRVs
        for trv_id in self._trv_ids:
            await self._backend.async_unsubscribe_trv(trv_id)

    # ── TRV State Handling ─────────────────────────────────────────────

    @callback
    def _handle_trv_state_update(self, trv_id: str, trv_state: TRVState) -> None:
        """Handle a TRV state update from the backend."""
        if trv_id not in self._trv_ids:
            return  # Not one of our TRVs

        self.state.trv_states[trv_id] = trv_state

        # Track last update time for availability
        self._last_trv_update_time[trv_id] = time.monotonic()

        # Update aggregated room state
        self._update_room_state()

        self._notify_state_update()

    def _update_room_state(self) -> None:
        """Recalculate aggregated room state from TRV states."""
        states = self.state.trv_states

        if not states:
            return  # No TRV states yet

        # Current temperature: average of TRV local temperatures
        temps = [
            s.local_temperature
            for s in states.values()
            if s.local_temperature is not None
        ]
        if temps:
            self.state.current_temperature = sum(temps) / len(temps)

        # Target temperature: use the first TRV's setpoint (in configured order)
        for trv_id in self._trv_ids:
            trv = states.get(trv_id)
            if trv is not None and trv.occupied_heating_setpoint is not None:
                self.state.target_temperature = trv.occupied_heating_setpoint
                break

        # Pi heating demand: max accross all TRVs
        demands = [
            s.pi_heating_demand
            for s in states.values()
            if s.pi_heating_demand is not None
        ]
        self.state.max_pi_heating_demand = max(demands) if demands else 0

        # Heat required: any TRV reports heat_required (0x4031 Heat Supply Request)
        self.state.heat_required = any(s.heat_required is True for s in states.values())

        # Window open: any TRV has window_open_detection >= 3
        self.state.window_open = any(
            s.window_open_detection is not None
            and s.window_open_detection >= WINDOW_OPEN_DETECTED
            for s in states.values()
        )

        # Availability: room is available if at least one TRV has reported
        # within the staleness timeout
        now = time.monotonic()
        self.state.available = any(
            (now - self._last_trv_update_time.get(trv_id, 0.0))
            < TRV_AVAILABILITY_TIMEOUT
            for trv_id in self._trv_ids
        )
