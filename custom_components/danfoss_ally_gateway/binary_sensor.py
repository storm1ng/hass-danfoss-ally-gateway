"""Binary sensor entities for Danfoss Ally Gateway.

Provides per-room binary sensors:
- Heat Required: any TRV has pi_heating_demand > 0
- Heat Available: heat source is providing heat
- Window Open: any TRV has window_open == 3
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
)

from .const import DOMAIN
from .coordinator import RoomCoordinator
from .entity import DanfossAllyEntityBase

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
        async_add_entities(entities, config_subentry_id=subentry_id)


def create_room_entities(
    coordinator: RoomCoordinator,
    config_entry_id: str,
    subentry_id: str,
) -> list[BinarySensorEntity]:
    """Create binary sensor entities for a single room coordinator."""
    return [
        DanfossAllyHeatRequired(coordinator, config_entry_id, subentry_id),
        DanfossAllyHeatAvailable(coordinator, config_entry_id, subentry_id),
        DanfossAllyWindowOpen(coordinator, config_entry_id, subentry_id),
    ]


class DanfossAllyHeatRequired(DanfossAllyEntityBase, BinarySensorEntity):
    """Binary sensor: heat is required by any TRV in the room."""

    _attr_translation_key = "heat_required"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize heat required sensor."""
        super().__init__(coordinator, config_entry_id, subentry_id)
        self._attr_unique_id = f"{DOMAIN}_{config_entry_id}_{subentry_id}_heat_required"
        self._attr_translation_placeholders = {"room_name": coordinator.room_name}

    @property
    def is_on(self) -> bool:
        """Return True if any TRV demands heat."""
        return self._coordinator.state.heat_required


class DanfossAllyHeatAvailable(DanfossAllyEntityBase, BinarySensorEntity):
    """Binary sensor: heat is available from the heating system."""

    _attr_translation_key = "heat_available"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize heat available sensor."""
        super().__init__(coordinator, config_entry_id, subentry_id)
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry_id}_{subentry_id}_heat_available"
        )
        self._attr_translation_placeholders = {"room_name": coordinator.room_name}

    @property
    def is_on(self) -> bool | None:
        """Return True if heat is available."""
        return self._coordinator.state.heat_available


class DanfossAllyWindowOpen(DanfossAllyEntityBase, BinarySensorEntity):
    """Binary sensor: window open detected in the room."""

    _attr_device_class = BinarySensorDeviceClass.WINDOW
    _attr_translation_key = "window_open"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize window open sensor."""
        super().__init__(coordinator, config_entry_id, subentry_id)
        self._attr_unique_id = f"{DOMAIN}_{config_entry_id}_{subentry_id}_window_open"
        self._attr_translation_placeholders = {"room_name": coordinator.room_name}

    @property
    def is_on(self) -> bool:
        """Return True if any TRV detects window open."""
        return self._coordinator.state.window_open
