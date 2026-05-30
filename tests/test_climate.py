"""Tests for the climate entity."""

from __future__ import annotations

from homeassistant.components.climate.const import (
    PRESET_NONE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE

from custom_components.danfoss_ally_gateway.climate import (
    DanfossAllyRoomClimate,
    create_room_entities,
)
from custom_components.danfoss_ally_gateway.const import (
    DOMAIN,
    SCHEDULE_MODE_MANUAL,
    SCHEDULE_MODE_SCHEDULE,
    SCHEDULE_MODE_SCHEDULE_PREHEAT,
)


class TestClimateEntityCreation:
    """Tests for climate entity creation."""

    def test_create_room_entities(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entities = create_room_entities(coord, "entry1", "sub1")
        assert len(entities) == 1
        assert isinstance(entities[0], DanfossAllyRoomClimate)

    def test_unique_id(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.unique_id == f"{DOMAIN}_entry1_sub1_climate"

    def test_name(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.translation_key == "room"
        assert entity._attr_translation_placeholders == {"room_name": "Living Room"}

    def test_device_info(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.device_info is not None
        assert (DOMAIN, "entry1_sub1") in entity.device_info["identifiers"]  # type: ignore[typeddict-item]

    def test_subentry_id_stored(self, hass, mock_backend, subentry_data):
        """Entity stores subentry_id for internal use."""
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity._subentry_id == "sub1"


class TestClimateEntityState:
    """Tests for climate entity state and actions."""

    def test_hvac_mode(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.hvac_mode == HVACMode.HEAT

    def test_hvac_action_idle(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.hvac_action == HVACAction.IDLE

    def test_hvac_action_heating(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        coord.state.max_pi_heating_demand = 50
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.hvac_action == HVACAction.HEATING

    def test_available_false_initially(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.available is False

    def test_available_true_when_trv_reports(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        coord.state.available = True
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.available is True

    def test_extra_state_attributes(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        coord.state.max_pi_heating_demand = 40
        coord.state.heat_required = True
        coord.state.heat_available = True
        coord.state.load_room_mean = 150
        entity = create_room_entities(coord, "entry1", "sub1")[0]

        attrs = entity.extra_state_attributes
        assert attrs["pi_heating_demand"] == 40
        assert attrs["heat_required"] is True
        assert attrs["heat_available"] is True
        assert attrs["load_room_mean"] == 150
        assert attrs["trv_count"] == 2

    async def test_set_temperature(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        entity = create_room_entities(coord, "entry1", "sub1")[0]

        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.0})

        assert mock_backend.async_set_occupied_heating_setpoint.call_count == 2
        await coord.async_teardown()

    async def test_set_temperature_none_ignored(
        self, hass, mock_backend, subentry_data
    ):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        entity = create_room_entities(coord, "entry1", "sub1")[0]

        await entity.async_set_temperature()  # No ATTR_TEMPERATURE

        mock_backend.async_set_occupied_heating_setpoint.assert_not_called()
        await coord.async_teardown()

    def test_temperature_limits(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.min_temp == 5.0
        assert entity.max_temp == 35.0
        assert entity.target_temperature_step == 0.5


class TestClimatePresetMode:
    """Tests for climate entity preset mode."""

    def test_supported_features_include_preset(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.supported_features & ClimateEntityFeature.PRESET_MODE

    def test_preset_modes_list(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.preset_modes == [PRESET_NONE, "schedule", "schedule_with_preheat"]

    def test_preset_mode_default_none(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.preset_mode == PRESET_NONE

    def test_preset_mode_schedule(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        coord._schedule._mode = SCHEDULE_MODE_SCHEDULE
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.preset_mode == "schedule"

    def test_preset_mode_schedule_with_preheat(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        coord._schedule._mode = SCHEDULE_MODE_SCHEDULE_PREHEAT
        entity = create_room_entities(coord, "entry1", "sub1")[0]
        assert entity.preset_mode == "schedule_with_preheat"

    async def test_set_preset_mode_schedule(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        entity = create_room_entities(coord, "entry1", "sub1")[0]

        await entity.async_set_preset_mode("schedule")
        assert coord.schedule_mode == SCHEDULE_MODE_SCHEDULE
        await coord.async_teardown()

    async def test_set_preset_mode_none(self, hass, mock_backend, subentry_data):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        entity = create_room_entities(coord, "entry1", "sub1")[0]

        await entity.async_set_preset_mode(PRESET_NONE)
        assert coord.schedule_mode == SCHEDULE_MODE_MANUAL
        await coord.async_teardown()

    async def test_set_preset_mode_schedule_with_preheat(
        self, hass, mock_backend, subentry_data
    ):
        from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

        coord = RoomCoordinator(hass, mock_backend, subentry_data)
        await coord.async_setup()
        entity = create_room_entities(coord, "entry1", "sub1")[0]

        await entity.async_set_preset_mode("schedule_with_preheat")
        assert coord.schedule_mode == SCHEDULE_MODE_SCHEDULE_PREHEAT
        await coord.async_teardown()
