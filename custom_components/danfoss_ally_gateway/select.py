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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, PROGRAMMING_MODE_OPTIONS
from .coordinator import RoomCoordinator, RoomState

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
        async_add_entities(entities, False, config_subentry_id=subentry_id)


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


class DanfossAllyProgrammingModeSelect(SelectEntity):
    """Select entity for Danfoss Ally programming mode."""

    _attr_has_entity_name = True
    _attr_options = PROGRAMMING_MODE_OPTIONS
    _attr_translation_key = "programming_mode"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize the programming mode select entity."""
        self._coordinator = coordinator
        self._config_entry_id = config_entry_id
        self._subentry_id = subentry_id

        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry_id}_{subentry_id}_programming_mode"
        )

        # Device grouping: same virtual device as the climate entity
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry_id}_{subentry_id}")},
            name=f"Danfoss Ally {coordinator.room_name}",
            manufacturer="Danfoss",
            model="Ally Virtual Room",
            entry_type=None,
        )

        # Initial state
        self._attr_current_option = coordinator.schedule_mode_option

    async def async_added_to_hass(self) -> None:
        """Register for coordinator updates when added to HA."""
        self._unsub = self._coordinator.register_state_callback(
            self._handle_coordinator_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from coordinator when removed."""
        if hasattr(self, "_unsub"):
            self._unsub()

    @callback
    def _handle_coordinator_update(self, state: RoomState) -> None:
        """Handle updated room state from coordinator."""
        self._attr_current_option = self._coordinator.schedule_mode_option
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if the room has at least one responsive TRV."""
        return self._coordinator.state.available

    async def async_select_option(self, option: str) -> None:
        """Handle the user selecting a new programming mode."""
        _LOGGER.debug(
            "Setting programming mode to '%s' for room '%s'",
            option,
            self._coordinator.room_name,
        )
        await self._coordinator.async_set_programming_mode_option(option)
