"""Binary sensor entities for Danfoss Ally Gateway.

Provides per-room binary sensors:
- Heat Required: any TRV has pi_heating_demand > 0
- Heat Available: heat source is providing heat
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
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
    """Set up binary sensor entities from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[str, RoomCoordinator] = data.get("coordinators", {})

    # Store the callback so async_setup_subentry can add entities later
    data.setdefault("platform_add_entities", {})["binary_sensor"] = async_add_entities

    for subentry_id, coordinator in coordinators.items():
        entities = create_room_entities(coordinator, config_entry.entry_id, subentry_id)
        async_add_entities(entities, False, config_subentry_id=subentry_id)


def create_room_entities(
    coordinator: RoomCoordinator,
    config_entry_id: str,
    subentry_id: str,
) -> list[_DanfossAllyBinarySensorBase]:
    """Create binary sensor entities for a single room coordinator."""
    return [
        DanfossAllyHeatRequired(coordinator, config_entry_id, subentry_id),
        DanfossAllyHeatAvailable(coordinator, config_entry_id, subentry_id),
    ]


class _DanfossAllyBinarySensorBase(BinarySensorEntity):
    """Base class for Danfoss Ally binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
        key: str,
    ) -> None:
        """Initialize the binary sensor."""
        self._coordinator = coordinator
        self._config_entry_id = config_entry_id
        self._subentry_id = subentry_id
        self._attr_unique_id = f"{DOMAIN}_{config_entry_id}_{subentry_id}_{key}"

        # Device grouping: one virtual device per room
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


class DanfossAllyHeatRequired(_DanfossAllyBinarySensorBase):
    """Binary sensor: heat is required by any TRV in the room."""

    _attr_translation_key = "heat_required"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize heat required sensor."""
        super().__init__(coordinator, config_entry_id, subentry_id, "heat_required")
        self._attr_name = f"{coordinator.room_name} Heat Required"

    @property
    def is_on(self) -> bool:
        """Return True if any TRV demands heat."""
        return self._coordinator.state.heat_required


class DanfossAllyHeatAvailable(_DanfossAllyBinarySensorBase):
    """Binary sensor: heat is available from the heating system."""

    _attr_translation_key = "heat_available"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize heat available sensor."""
        super().__init__(coordinator, config_entry_id, subentry_id, "heat_available")
        self._attr_name = f"{coordinator.room_name} Heat Available"

    @property
    def is_on(self) -> bool | None:
        """Return True if heat is available."""
        return self._coordinator.state.heat_available
