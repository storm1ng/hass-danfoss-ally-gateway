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
    """Set up sensor entities from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[str, RoomCoordinator] = data.get("coordinators", {})

    # Store the callback so async_setup_subentry can add entities later
    data.setdefault("platform_add_entities", {})["sensor"] = async_add_entities

    for subentry_id, coordinator in coordinators.items():
        entities = list(
            create_room_entities(coordinator, config_entry.entry_id, subentry_id)
        )
        async_add_entities(entities, False, config_subentry_id=subentry_id)


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


class _DanfossAllySensorBase(SensorEntity):
    """Base class for Danfoss Ally sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._config_entry_id = config_entry_id
        self._subentry_id = subentry_id

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


class DanfossAllyHeatingDemand(_DanfossAllySensorBase):
    """Sensor: TRV PI heating demand in percent."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:radiator"

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
        self._attr_name = f"{trv_id} Heating Demand"

    @property
    def native_value(self) -> int | None:
        """Return the PI heating demand percentage."""
        trv_state = self._coordinator.state.trv_states.get(self._trv_id)
        if trv_state is None:
            return None
        return trv_state.pi_heating_demand


class DanfossAllyLoadEstimate(_DanfossAllySensorBase):
    """Sensor: TRV load estimate value."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:scale-balance"

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
        self._attr_name = f"{trv_id} Load Estimate"

    @property
    def native_value(self) -> int | None:
        """Return the load estimate."""
        trv_state = self._coordinator.state.trv_states.get(self._trv_id)
        if trv_state is None:
            return None
        return trv_state.load_estimate


class DanfossAllyLoadRoomMean(_DanfossAllySensorBase):
    """Sensor: calculated load room mean for the room."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:scale-balance"

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
        self._attr_name = f"{coordinator.room_name} Load Room Mean"

    @property
    def native_value(self) -> int | None:
        """Return the load room mean."""
        return self._coordinator.state.load_room_mean
