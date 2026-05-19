"""Switch entities for Danfoss Ally Gateway.

Provides per-room switches:
- Load Balancing: enable/disable load balancing across TRVs in the room
  (only created for rooms with more than one TRV).
"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
)

from .const import DOMAIN
from .coordinator import RoomCoordinator, RoomState

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[str, RoomCoordinator] = data.get("coordinators", {})

    # Store the callback so async_setup_subentry can add entities later
    data.setdefault("platform_add_entities", {})["switch"] = async_add_entities

    for subentry_id, coordinator in coordinators.items():
        entities = create_room_entities(coordinator, config_entry.entry_id, subentry_id)
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


def create_room_entities(
    coordinator: RoomCoordinator,
    config_entry_id: str,
    subentry_id: str,
) -> list[SwitchEntity]:
    """Create switch entities for a single room coordinator."""
    entities: list[SwitchEntity] = []
    # Only create load balancing switch for multi-TRV rooms
    if len(coordinator.trv_ids) > 1:
        entities.append(
            DanfossAllyLoadBalancingSwitch(coordinator, config_entry_id, subentry_id)
        )
    return entities


class DanfossAllyLoadBalancingSwitch(SwitchEntity):
    """Switch to enable/disable load balancing for a room.

    Per Danfoss spec (AU417130778872en-000102, §2.2):
    Load balancing distributes the room's heating load across multiple
    TRVs.  This switch controls whether the gateway calculates and
    distributes load_room_mean to the TRVs.  Only applicable for rooms
    with more than one TRV.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:scale-balance"
    _attr_translation_key = "load_balancing"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize load balancing switch."""
        self._coordinator = coordinator
        self._config_entry_id = config_entry_id
        self._subentry_id = subentry_id
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry_id}_{subentry_id}_load_balancing"
        )
        self._attr_translation_placeholders = {"room_name": coordinator.room_name}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry_id}_{subentry_id}")},
            name=f"Danfoss Ally {coordinator.room_name}",
            manufacturer="Danfoss",
            model="Ally Virtual Room",
        )

    async def async_added_to_hass(self) -> None:
        """Register for coordinator updates."""
        self._unsub = self._coordinator.register_state_callback(
            self._handle_coordinator_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from coordinator."""
        if hasattr(self, "_unsub"):
            self._unsub()

    @callback
    def _handle_coordinator_update(self, state: RoomState) -> None:
        """Handle updated room state."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if the room has at least one responsive TRV."""
        return self._coordinator.state.available

    @property
    def is_on(self) -> bool:
        """Return True if load balancing is enabled."""
        return self._coordinator.state.load_balancing_enabled

    async def async_turn_on(self, **kwargs) -> None:
        """Enable load balancing."""
        await self._coordinator.async_enable_load_balancing()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable load balancing."""
        await self._coordinator.async_disable_load_balancing()
