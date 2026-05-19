"""Tests for binary sensor entities."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.danfoss_ally_gateway.binary_sensor import (
    DanfossAllyHeatAvailable,
    DanfossAllyHeatRequired,
    DanfossAllyWindowOpen,
    create_room_entities,
)
from custom_components.danfoss_ally_gateway.const import DOMAIN
from custom_components.danfoss_ally_gateway.coordinator import RoomCoordinator

# ── Helpers ───────────────────────────────────────────────────────────


def _make_entities(hass, mock_backend, subentry_data):
    """Create a coordinator and binary sensor entities."""
    coord = RoomCoordinator(hass, mock_backend, subentry_data)
    entities = create_room_entities(coord, "entry1", "sub1")
    return coord, entities


# ── Entity Creation ───────────────────────────────────────────────────


class TestBinarySensorCreation:
    """Tests for binary sensor entity creation."""

    def test_create_room_entities_returns_three(
        self, hass, mock_backend, subentry_data
    ):
        """Three binary sensors are created per room."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert len(entities) == 3

    def test_entity_types(self, hass, mock_backend, subentry_data):
        """Check all three entity types are present."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        types = {type(e) for e in entities}
        assert types == {
            DanfossAllyHeatRequired,
            DanfossAllyHeatAvailable,
            DanfossAllyWindowOpen,
        }

    def test_unique_ids(self, hass, mock_backend, subentry_data):
        """Each entity has a distinct unique_id."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        uids = {e.unique_id for e in entities}
        assert len(uids) == 3
        expected_suffixes = {"heat_required", "heat_available", "window_open"}
        for suffix in expected_suffixes:
            assert f"{DOMAIN}_entry1_sub1_{suffix}" in uids

    def test_names(self, hass, mock_backend, subentry_data):
        """Entity translation keys and placeholders are set correctly."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        for e in entities:
            assert e.translation_key is not None
            assert e._attr_translation_placeholders == {"room_name": "Living Room"}
        heat_req = next(e for e in entities if isinstance(e, DanfossAllyHeatRequired))
        assert heat_req.translation_key == "heat_required"
        heat_avail = next(
            e for e in entities if isinstance(e, DanfossAllyHeatAvailable)
        )
        assert heat_avail.translation_key == "heat_available"
        win_open = next(e for e in entities if isinstance(e, DanfossAllyWindowOpen))
        assert win_open.translation_key == "window_open"


# ── Device Info ───────────────────────────────────────────────────────


class TestBinarySensorDeviceInfo:
    """Tests for device info on binary sensor entities."""

    def test_device_info_present(self, hass, mock_backend, subentry_data):
        """All entities have device_info with correct identifiers."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        for entity in entities:
            assert entity.device_info is not None
            assert (DOMAIN, "entry1_sub1") in entity.device_info["identifiers"]  # type: ignore[typeddict-item]

    def test_device_info_name(self, hass, mock_backend, subentry_data):
        """Device name follows 'Danfoss Ally <room_name>' pattern."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0].device_info["name"] == "Danfoss Ally Living Room"  # type: ignore[typeddict-item]

    def test_subentry_id_stored(self, hass, mock_backend, subentry_data):
        """All entities store subentry_id for internal use."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        for entity in entities:
            assert entity._subentry_id == "sub1"


# ── Device Classes ────────────────────────────────────────────────────


class TestBinarySensorDeviceClasses:
    """Tests for binary sensor device classes."""

    def test_heat_required_no_device_class(self, hass, mock_backend, subentry_data):
        """heat_required uses translation_key instead of device_class."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        heat_req = next(e for e in entities if isinstance(e, DanfossAllyHeatRequired))
        assert heat_req.device_class is None

    def test_heat_required_translation_key(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        heat_req = next(e for e in entities if isinstance(e, DanfossAllyHeatRequired))
        assert heat_req.translation_key == "heat_required"

    def test_heat_available_no_device_class(self, hass, mock_backend, subentry_data):
        """heat_available uses translation_key instead of device_class."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        heat_avail = next(
            e for e in entities if isinstance(e, DanfossAllyHeatAvailable)
        )
        assert heat_avail.device_class is None

    def test_heat_available_translation_key(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        heat_avail = next(
            e for e in entities if isinstance(e, DanfossAllyHeatAvailable)
        )
        assert heat_avail.translation_key == "heat_available"

    def test_window_open_device_class(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        win_open = next(e for e in entities if isinstance(e, DanfossAllyWindowOpen))
        assert win_open.device_class == BinarySensorDeviceClass.WINDOW


# ── State ─────────────────────────────────────────────────────────────


class TestBinarySensorState:
    """Tests for binary sensor state values."""

    def test_heat_required_off_by_default(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        heat_req = next(e for e in entities if isinstance(e, DanfossAllyHeatRequired))
        assert heat_req.is_on is False

    def test_heat_required_on(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        coord.state.heat_required = True
        heat_req = next(e for e in entities if isinstance(e, DanfossAllyHeatRequired))
        assert heat_req.is_on is True

    def test_heat_available_none_by_default(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        heat_avail = next(
            e for e in entities if isinstance(e, DanfossAllyHeatAvailable)
        )
        assert heat_avail.is_on is None

    def test_heat_available_on(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        coord.state.heat_available = True
        heat_avail = next(
            e for e in entities if isinstance(e, DanfossAllyHeatAvailable)
        )
        assert heat_avail.is_on is True

    def test_heat_available_off(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        coord.state.heat_available = False
        heat_avail = next(
            e for e in entities if isinstance(e, DanfossAllyHeatAvailable)
        )
        assert heat_avail.is_on is False

    def test_window_open_off_by_default(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        win_open = next(e for e in entities if isinstance(e, DanfossAllyWindowOpen))
        assert win_open.is_on is False

    def test_window_open_on(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        coord.state.window_open = True
        win_open = next(e for e in entities if isinstance(e, DanfossAllyWindowOpen))
        assert win_open.is_on is True


# ── Availability ──────────────────────────────────────────────────────


class TestBinarySensorAvailability:
    """Tests for binary sensor availability tracking."""

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
