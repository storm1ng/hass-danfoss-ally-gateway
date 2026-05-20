"""Base entity for Danfoss Ally Gateway."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import RoomCoordinator, RoomState


class DanfossAllyEntityBase(Entity):
    """Shared base for all Danfoss Ally Gateway entities.

    Provides:
    - Coordinator reference and config/subentry IDs
    - Virtual room DeviceInfo construction
    - Lifecycle: subscribe/unsubscribe to coordinator state updates
    - Availability based on TRV responsiveness
    - Default coordinator update handler (calls async_write_ha_state)
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize the entity."""
        self._coordinator = coordinator
        self._config_entry_id = config_entry_id
        self._subentry_id = subentry_id
        self._unsub: Callable[[], None] | None = None

        # Device grouping: one virtual device per room
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry_id}_{subentry_id}")},
            name=f"Danfoss Ally {coordinator.room_name}",
            manufacturer="Danfoss",
            model="Ally Virtual Room",
        )

    async def async_added_to_hass(self) -> None:
        """Register for coordinator updates when added to HA."""
        self._unsub = self._coordinator.register_state_callback(
            self._handle_coordinator_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from coordinator when removed."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_coordinator_update(self, state: RoomState) -> None:
        """Handle updated room state from coordinator."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if the room has at least one responsive TRV."""
        return self._coordinator.state.available
