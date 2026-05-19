"""Virtual room climate entity for Danfoss Ally Gateway.

Provides a single climate entity per room that:
- Shows current temperature from external sensor or TRV average
- Shows/controls target temperature synced to all TRVs
- Shows hvac_action based on pi_heating_demand
- Exposes extra state attributes (demand, heat available, etc.)
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
)

from .const import DOMAIN
from .coordinator import RoomCoordinator, RoomState

_LOGGER = logging.getLogger(__name__)

# Danfoss Ally TRV temperature range
MIN_TEMP = 5.0
MAX_TEMP = 35.0
TEMP_STEP = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up climate entities from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[str, RoomCoordinator] = data.get("coordinators", {})

    # Store the callback so async_setup_subentry can add entities later
    data.setdefault("platform_add_entities", {})["climate"] = async_add_entities

    for subentry_id, coordinator in coordinators.items():
        entities = create_room_entities(coordinator, config_entry.entry_id, subentry_id)
        async_add_entities(entities, config_subentry_id=subentry_id)


def create_room_entities(
    coordinator: RoomCoordinator,
    config_entry_id: str,
    subentry_id: str,
) -> list[DanfossAllyRoomClimate]:
    """Create climate entities for a single room coordinator."""
    return [
        DanfossAllyRoomClimate(
            coordinator=coordinator,
            config_entry_id=config_entry_id,
            subentry_id=subentry_id,
        )
    ]


class DanfossAllyRoomClimate(ClimateEntity):
    """Virtual climate entity for a Danfoss Ally room."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = TEMP_STEP

    def __init__(
        self,
        coordinator: RoomCoordinator,
        config_entry_id: str,
        subentry_id: str,
    ) -> None:
        """Initialize the room climate entity."""
        self._coordinator = coordinator
        self._config_entry_id = config_entry_id
        self._subentry_id = subentry_id
        # Entity IDs
        self._attr_unique_id = f"{DOMAIN}_{config_entry_id}_{subentry_id}_climate"
        self._attr_name = coordinator.room_name

        # Device grouping: one virtual device per room
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry_id}_{subentry_id}")},
            name=f"Danfoss Ally {coordinator.room_name}",
            manufacturer="Danfoss",
            model="Ally Virtual Room",
            entry_type=None,
        )

        # Initial state
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_current_temperature = coordinator.state.current_temperature
        self._attr_target_temperature = coordinator.state.target_temperature

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
        self._attr_current_temperature = state.current_temperature
        self._attr_target_temperature = state.target_temperature
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if the room has at least one responsive TRV."""
        return self._coordinator.state.available

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current HVAC action."""
        if self._coordinator.state.max_pi_heating_demand > 0:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        state = self._coordinator.state
        attrs: dict[str, Any] = {
            "pi_heating_demand": state.max_pi_heating_demand,
            "heat_required": state.heat_required,
            "window_open": state.window_open,
            "trv_count": len(self._coordinator.trv_ids),
        }

        if state.heat_available is not None:
            attrs["heat_available"] = state.heat_available

        if state.load_room_mean is not None:
            attrs["load_room_mean"] = state.load_room_mean

        # Per-TRV details
        trv_details = {}
        for trv_id, trv_state in state.trv_states.items():
            trv_details[trv_id] = {
                "local_temperature": trv_state.local_temperature,
                "setpoint": trv_state.occupied_heating_setpoint,
                "demand": trv_state.pi_heating_demand,
            }
        if trv_details:
            attrs["trv_states"] = trv_details

        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature for the room."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        await self._coordinator.async_set_room_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode - only HEAT is supported."""
        if hvac_mode != HVACMode.HEAT:
            _LOGGER.warning(
                "Unsupported HVAC mode %s for room '%s' - only HEAT is supported",
                hvac_mode,
                self._coordinator.room_name,
            )
