"""Programming mode select entity for Danfoss Ally Gateway.

Provides a select entity per room to control the TRV programming mode:
- Manual: TRVs hold current setpoint
- Schedule: TRVs follow programmed weekly schedule
- Schedule + Preheat: Schedule mode with preheat enabled
- Pause: Eco mode (holds away/minimum temperature)
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, PROGRAMMING_MODE_OPTIONS
from .coordinator import RoomCoordinator, RoomState
from .entity import DanfossAllyEntityBase

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up select entities from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[str, RoomCoordinator] = data.get("coordinators", {})

    # Store the callback so async_setup_subentry can add entities later
    data.setdefault("platform_add_entities", {})["select"] = async_add_entities

    for subentry_id, coordinator in coordinators.items():
        entities = create_room_entities(coordinator, config_entry.entry_id, subentry_id)
        async_add_entities(entities, config_subentry_id=subentry_id)


def create_room_entities(
    coordinator: RoomCoordinator,
    config_entry_id: str,
    subentry_id: str,
) -> list[DanfossAllyProgrammingModeSelect]:
    """Create select entities for a single room coordinator."""
    return [
        DanfossAllyProgrammingModeSelect(
            coordinator=coordinator,
            config_entry_id=config_entry_id,
            subentry_id=subentry_id,
        )
    ]


class DanfossAllyProgrammingModeSelect(DanfossAllyEntityBase, SelectEntity):
    """Select entity for Danfoss Ally programming mode."""

    _attr_options = PROGRAMMING_MODE_OPTIONS
    _attr_translation_key = "programming_mode"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize the programming mode select entity."""
        super().__init__(coordinator, config_entry_id, subentry_id)
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry_id}_{subentry_id}_programming_mode"
        )
        self._attr_translation_placeholders = {"room_name": coordinator.room_name}

        # Initial state
        self._attr_current_option = coordinator.schedule_mode_option

    @callback
    def _handle_coordinator_update(self, state: RoomState) -> None:
        """Handle updated room state from coordinator."""
        self._attr_current_option = self._coordinator.schedule_mode_option
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Handle the user selecting a new programming mode."""
        _LOGGER.debug(
            "Setting programming mode to '%s' for room '%s'",
            option,
            self._coordinator.room_name,
        )
        await self._coordinator.async_set_programming_mode_option(option)
