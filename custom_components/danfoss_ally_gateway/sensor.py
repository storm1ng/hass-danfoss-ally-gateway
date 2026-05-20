"""Diagnostic sensor entities for Danfoss Ally Gateway.

Provides:
- Per-TRV: PI Heating Demand (%), Load Estimate
- Per-room: Load Room Mean
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
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
    """Set up sensor entities from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[str, RoomCoordinator] = data.get("coordinators", {})

    # Store the callback so async_setup_subentry can add entities later
    data.setdefault("platform_add_entities", {})["sensor"] = async_add_entities

    for subentry_id, coordinator in coordinators.items():
        entities = create_room_entities(coordinator, config_entry.entry_id, subentry_id)
        async_add_entities(entities, config_subentry_id=subentry_id)


def create_room_entities(
    coordinator: RoomCoordinator,
    config_entry_id: str,
    subentry_id: str,
) -> list[SensorEntity]:
    """Create sensor entities for a single room coordinator."""
    entities: list[SensorEntity] = []

    # Per-TRV sensors
    for trv_id in coordinator.trv_ids:
        entities.extend(
            [
                DanfossAllyHeatingDemand(
                    coordinator, config_entry_id, subentry_id, trv_id
                ),
                DanfossAllyLoadEstimate(
                    coordinator, config_entry_id, subentry_id, trv_id
                ),
            ]
        )

    # Per-room sensors (only for multi-TRV rooms)
    if len(coordinator.trv_ids) > 1:
        entities.append(
            DanfossAllyLoadRoomMean(coordinator, config_entry_id, subentry_id)
        )

    return entities


class DanfossAllyHeatingDemand(DanfossAllyEntityBase, SensorEntity):
    """Sensor: TRV PI heating demand in percent."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:radiator"
    _attr_translation_key = "heating_demand"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
        trv_id: str,
    ) -> None:
        """Initialize heating demand sensor."""
        super().__init__(coordinator, config_entry_id, subentry_id)
        self._trv_id = trv_id
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry_id}_{subentry_id}_{trv_id}_heating_demand"
        )
        self._attr_translation_placeholders = {"trv_name": trv_id}

    @property
    def native_value(self) -> int | None:
        """Return the PI heating demand percentage."""
        trv_state = self._coordinator.state.trv_states.get(self._trv_id)
        if trv_state is None:
            return None
        return trv_state.pi_heating_demand


class DanfossAllyLoadEstimate(DanfossAllyEntityBase, SensorEntity):
    """Sensor: TRV load estimate value."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:scale-balance"
    _attr_translation_key = "load_estimate"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
        trv_id: str,
    ) -> None:
        """Initialize load estimate sensor."""
        super().__init__(coordinator, config_entry_id, subentry_id)
        self._trv_id = trv_id
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry_id}_{subentry_id}_{trv_id}_load_estimate"
        )
        self._attr_translation_placeholders = {"trv_name": trv_id}

    @property
    def native_value(self) -> int | None:
        """Return the load estimate."""
        trv_state = self._coordinator.state.trv_states.get(self._trv_id)
        if trv_state is None:
            return None
        return trv_state.load_estimate


class DanfossAllyLoadRoomMean(DanfossAllyEntityBase, SensorEntity):
    """Sensor: calculated load room mean for the room."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:scale-balance"
    _attr_translation_key = "load_room_mean"

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize load room mean sensor."""
        super().__init__(coordinator, config_entry_id, subentry_id)
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry_id}_{subentry_id}_load_room_mean"
        )
        self._attr_translation_placeholders = {"room_name": coordinator.room_name}

    @property
    def native_value(self) -> int | None:
        """Return the load room mean."""
        return self._coordinator.state.load_room_mean
