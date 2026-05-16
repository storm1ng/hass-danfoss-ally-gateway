"""Tests for binary sensor entities."""

from custom_components.danfoss_ally_gateway.binary_sensor import (
    DanfossAllyHeatRequired,
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

    def test_create_room_entities_returns_one(self, hass, mock_backend, subentry_data):
        """One binary sensor is created per room (heat_required only)."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert len(entities) == 1

    def test_entity_type(self, hass, mock_backend, subentry_data):
        """Check entity type is DanfossAllyHeatRequired."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert isinstance(entities[0], DanfossAllyHeatRequired)

    def test_unique_id(self, hass, mock_backend, subentry_data):
        """Entity has correct unique_id."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0].unique_id == f"{DOMAIN}_entry1_sub1_heat_required"

    def test_name(self, hass, mock_backend, subentry_data):
        """Entity name includes the room name."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0].name == "Living Room Heat Required"


# ── Device Info ───────────────────────────────────────────────────────


class TestBinarySensorDeviceInfo:
    """Tests for device info on binary sensor entities."""

    def test_device_info_present(self, hass, mock_backend, subentry_data):
        """Entity has device_info with correct identifiers."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0].device_info is not None
        assert (DOMAIN, "entry1_sub1") in entities[0].device_info["identifiers"]  # type: ignore[typeddict-item]

    def test_device_info_name(self, hass, mock_backend, subentry_data):
        """Device name follows 'Danfoss Ally <room_name>' pattern."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0].device_info["name"] == "Danfoss Ally Living Room"  # type: ignore[typeddict-item]

    def test_subentry_id_stored(self, hass, mock_backend, subentry_data):
        """Entity stores subentry_id for internal use."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0]._subentry_id == "sub1"


# ── Device Classes ────────────────────────────────────────────────────


class TestHeatRequiredDeviceClass:
    """Tests for heat required device class."""

    def test_no_device_class(self, hass, mock_backend, subentry_data):
        """heat_required uses translation_key instead of device_class."""
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0].device_class is None

    def test_translation_key(self, hass, mock_backend, subentry_data):
        _, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0].translation_key == "heat_required"


# ── State ─────────────────────────────────────────────────────────────


class TestHeatRequiredState:
    """Tests for heat required state values."""

    def test_off_by_default(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0].is_on is False

    def test_on(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        coord.state.heat_required = True
        assert entities[0].is_on is True


# ── Availability ──────────────────────────────────────────────────────


class TestBinarySensorAvailability:
    """Tests for binary sensor availability tracking."""

    def test_unavailable_by_default(self, hass, mock_backend, subentry_data):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        assert entities[0].available is False

    def test_available_when_coordinator_available(
        self, hass, mock_backend, subentry_data
    ):
        coord, entities = _make_entities(hass, mock_backend, subentry_data)
        coord.state.available = True
        assert entities[0].available is True
