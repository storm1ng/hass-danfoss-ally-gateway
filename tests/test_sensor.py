"""Tests for diagnostic sensor entities."""

from __future__ import annotations

from conftest import make_subentry_data, make_trv_state
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory

from custom_components.danfoss_ally_gateway.const import DOMAIN
from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator
from custom_components.danfoss_ally_gateway.sensor import (
    DanfossAllyHeatingDemand,
    DanfossAllyLoadEstimate,
    DanfossAllyLoadRoomMean,
    create_room_entities,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _make_entities(hass, mock_backend, subentry_data):
    """Create a coordinator and sensor entities."""
    coord = RoomCoordinator(hass, mock_backend, subentry_data)
    entities = create_room_entities(coord, "entry1", "sub1")
    return coord, entities


def _make_single_trv_entities(hass, mock_backend):
    """Create sensor entities for a single-TRV room."""
    data = make_subentry_data(trv_ids=["trv_1"])
    coord = RoomCoordinator(hass, mock_backend, data)
    entities = create_room_entities(coord, "entry1", "sub1")
    return coord, entities


# ── Entity Creation ───────────────────────────────────────────────────


class TestSensorCreation:
    """Tests for sensor entity creation."""

    def test_create_multi_trv_room(self, hass, mock_backend, subentry_data):
        """Multi-TRV room: 2 heating demand + 2 load est + 1 load room mean = 5."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert len(entities) == 5

    def test_create_single_trv_room(self, hass, mock_backend):
        """Single-TRV room: 1 heating demand + 1 load est + NO room mean = 2."""
        _, entities = _make_single_trv_entities(hass, mock_backend)
        assert len(entities) == 2

    def test_no_load_room_mean_for_single_trv(self, hass, mock_backend):
        """LoadRoomMean is only created for multi-TRV rooms."""
        _, entities = _make_single_trv_entities(hass, mock_backend)
        types = [type(e) for e in entities]
        assert DanfossAllyLoadRoomMean not in types

    def test_load_room_mean_present_for_multi_trv(
        self, hass, mock_backend, subentry_data
    ):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        types = [type(e) for e in entities]
        assert DanfossAllyLoadRoomMean in types

    def test_entity_types_multi_trv(self, hass, mock_backend, subentry_data):
        """Multi-TRV creates the correct set of entity types."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        heating_demand = [
            e for e in entities if isinstance(e, DanfossAllyHeatingDemand)
        ]
        load_est = [e for e in entities if isinstance(e, DanfossAllyLoadEstimate)]
        load_mean = [e for e in entities if isinstance(e, DanfossAllyLoadRoomMean)]
        assert len(heating_demand) == 2
        assert len(load_est) == 2
        assert len(load_mean) == 1


# ── Unique IDs ────────────────────────────────────────────────────────


class TestSensorUniqueIds:
    """Tests for sensor unique_id construction."""

    def test_heating_demand_unique_id(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        heating_demand = [
            e for e in entities if isinstance(e, DanfossAllyHeatingDemand)
        ]
        uids = {e.unique_id for e in heating_demand}
        assert f"{DOMAIN}_entry1_sub1_trv_1_heating_demand" in uids
        assert f"{DOMAIN}_entry1_sub1_trv_2_heating_demand" in uids

    def test_load_estimate_unique_id(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        load_est = [e for e in entities if isinstance(e, DanfossAllyLoadEstimate)]
        uids = {e.unique_id for e in load_est}
        assert f"{DOMAIN}_entry1_sub1_trv_1_load_estimate" in uids
        assert f"{DOMAIN}_entry1_sub1_trv_2_load_estimate" in uids

    def test_load_room_mean_unique_id(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        load_mean = next(e for e in entities if isinstance(e, DanfossAllyLoadRoomMean))
        assert load_mean.unique_id == f"{DOMAIN}_entry1_sub1_load_room_mean"

    def test_all_unique_ids_distinct(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        uids = [e.unique_id for e in entities]
        assert len(uids) == len(set(uids))


# ── Names ─────────────────────────────────────────────────────────────


class TestSensorNames:
    """Tests for sensor entity names."""

    def test_heating_demand_name(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        heating_demand = [
            e for e in entities if isinstance(e, DanfossAllyHeatingDemand)
        ]
        for e in heating_demand:
            assert e.translation_key == "heating_demand"
        placeholders = {
            e._attr_translation_placeholders["trv_name"] for e in heating_demand
        }
        assert "trv_1" in placeholders
        assert "trv_2" in placeholders

    def test_load_estimate_name(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        load_est = [e for e in entities if isinstance(e, DanfossAllyLoadEstimate)]
        for e in load_est:
            assert e.translation_key == "load_estimate"
        placeholders = {e._attr_translation_placeholders["trv_name"] for e in load_est}
        assert "trv_1" in placeholders
        assert "trv_2" in placeholders

    def test_load_room_mean_name(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        load_mean = next(e for e in entities if isinstance(e, DanfossAllyLoadRoomMean))
        assert load_mean.translation_key == "load_room_mean"
        assert load_mean._attr_translation_placeholders == {"room_name": "Living Room"}


# ── Device Info ───────────────────────────────────────────────────────


class TestSensorDeviceInfo:
    """Tests for device info on sensor entities."""

    def test_device_info_present(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        for entity in entities:
            assert entity.device_info is not None
            assert (DOMAIN, "entry1_sub1") in entity.device_info["identifiers"]  # type: ignore[typeddict-item]

    def test_subentry_id_stored(self, hass, mock_backend, subentry_data):
        """All entities store subentry_id for internal use."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        for entity in entities:
            assert entity._subentry_id == "sub1"  # type: ignore[misc]


# ── Entity Category / Attributes ─────────────────────────────────────


class TestSensorAttributes:
    """Tests for sensor entity category and measurement attributes."""

    def test_all_diagnostic_category(self, hass, mock_backend, subentry_data):
        """All sensors are categorized as DIAGNOSTIC."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        for entity in entities:
            assert entity.entity_category == EntityCategory.DIAGNOSTIC

    def test_heating_demand_unit(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        heating_demand = next(
            e for e in entities if isinstance(e, DanfossAllyHeatingDemand)
        )
        assert heating_demand.native_unit_of_measurement == PERCENTAGE

    def test_heating_demand_state_class(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        heating_demand = next(
            e for e in entities if isinstance(e, DanfossAllyHeatingDemand)
        )
        assert heating_demand.state_class == SensorStateClass.MEASUREMENT

    def test_load_estimate_state_class(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        load_est = next(e for e in entities if isinstance(e, DanfossAllyLoadEstimate))
        assert load_est.state_class == SensorStateClass.MEASUREMENT

    def test_load_room_mean_state_class(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        load_mean = next(e for e in entities if isinstance(e, DanfossAllyLoadRoomMean))
        assert load_mean.state_class == SensorStateClass.MEASUREMENT


# ── State Values ──────────────────────────────────────────────────────


class TestSensorStateValues:
    """Tests for sensor native_value from coordinator state."""

    def test_heating_demand_none_without_trv_state(
        self, hass, mock_backend, subentry_data
    ):
        """Heating demand is None when no TRV state is available."""
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        heating_demand = next(
            e for e in entities if isinstance(e, DanfossAllyHeatingDemand)
        )
        assert heating_demand.native_value is None

    def test_heating_demand_returns_value(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        trv_state = make_trv_state(entity_id="trv_1", pi_heating_demand=75)
        coord.state.trv_states["trv_1"] = trv_state
        # Find the heating demand entity for trv_1
        heating_demand = next(
            e
            for e in entities
            if isinstance(e, DanfossAllyHeatingDemand) and e._trv_id == "trv_1"
        )
        assert heating_demand.native_value == 75

    def test_load_estimate_none_without_trv_state(
        self, hass, mock_backend, subentry_data
    ):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        load_est = next(e for e in entities if isinstance(e, DanfossAllyLoadEstimate))
        assert load_est.native_value is None

    def test_load_estimate_returns_value(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        trv_state = make_trv_state(entity_id="trv_1", load_estimate=200)
        coord.state.trv_states["trv_1"] = trv_state
        load_est = next(
            e
            for e in entities
            if isinstance(e, DanfossAllyLoadEstimate) and e._trv_id == "trv_1"
        )
        assert load_est.native_value == 200

    def test_load_room_mean_none_by_default(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        load_mean = next(e for e in entities if isinstance(e, DanfossAllyLoadRoomMean))
        assert load_mean.native_value is None

    def test_load_room_mean_returns_value(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        coord.state.load_room_mean = 150
        load_mean = next(e for e in entities if isinstance(e, DanfossAllyLoadRoomMean))
        assert load_mean.native_value == 150


# ── Availability ──────────────────────────────────────────────────────


class TestSensorAvailability:
    """Tests for sensor availability tracking."""

    def test_unavailable_by_default(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        for entity in entities:
            assert entity.available is False

    def test_available_when_coordinator_available(
        self, hass, mock_backend, subentry_data
    ):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        coord.state.available = True
        for entity in entities:
            assert entity.available is True
