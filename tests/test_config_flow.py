"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import selector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.danfoss_ally_gateway.config_flow import (
    DanfossAllyGatewayConfigFlow,
    RoomSubentryFlowHandler,
    _build_trv_selector,
    _extract_room_data,
    _get_assigned_trv_ids,
)
from custom_components.danfoss_ally_gateway.const import (
    BACKEND_Z2M,
    BACKEND_ZHA,
    CONF_AREA,
    CONF_AT_HOME_TEMP,
    CONF_AWAY_TEMP,
    CONF_BACKEND,
    CONF_HEAT_SOURCE,
    CONF_HEAT_SOURCE_TYPE,
    CONF_MQTT_BASE_TOPIC,
    CONF_PREHEAT_ENABLED,
    CONF_REMOTE_CLIMATE,
    CONF_ROOM_NAME,
    CONF_SCHEDULE_ENTITY,
    CONF_TEMP_SENSOR,
    CONF_TRV_ENTITIES,
    DEFAULT_AT_HOME_TEMP,
    DEFAULT_AWAY_TEMP,
    DOMAIN,
    SUBENTRY_ROOM,
    SUPPORTED_TRV_DEVICES_Z2M,
    SUPPORTED_TRV_DEVICES_ZHA,
)


def _setup_mock_mqtt(hass: HomeAssistant) -> MockConfigEntry:
    """Add a mock MQTT config entry to hass."""
    entry = MockConfigEntry(domain="mqtt", title="MQTT")
    entry.add_to_hass(hass)
    return entry


def _setup_mock_zha(hass: HomeAssistant) -> MockConfigEntry:
    """Add a mock ZHA config entry to hass."""
    entry = MockConfigEntry(domain="zha", title="ZHA")
    entry.add_to_hass(hass)
    return entry


# ── Main Config Flow ──────────────────────────────────────────────────


class TestMainConfigFlow:
    """Tests for the main config entry flow."""

    async def test_user_step_shows_form(self, hass: HomeAssistant):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["step_id"] == "user"  # type: ignore[typeddict-item]

    async def test_z2m_backend_flow(self, hass: HomeAssistant):
        _setup_mock_mqtt(hass)
        # Step 1: Select Z2M backend
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BACKEND: BACKEND_Z2M},
        )
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["step_id"] == "z2m"  # type: ignore[typeddict-item]

        # Step 2: Configure Z2M topic
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY  # type: ignore[typeddict-item]
        assert result["title"] == "Danfoss Ally Gateway (Z2M: zigbee2mqtt)"  # type: ignore[typeddict-item]
        assert result["data"][CONF_BACKEND] == BACKEND_Z2M  # type: ignore[typeddict-item]
        assert result["data"][CONF_MQTT_BASE_TOPIC] == "zigbee2mqtt"  # type: ignore[typeddict-item]

    async def test_zha_backend_flow(self, hass: HomeAssistant):
        _setup_mock_zha(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BACKEND: BACKEND_ZHA},
        )
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["step_id"] == "zha"  # type: ignore[typeddict-item]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY  # type: ignore[typeddict-item]
        assert result["title"] == "Danfoss Ally Gateway (ZHA)"  # type: ignore[typeddict-item]
        assert result["data"][CONF_BACKEND] == BACKEND_ZHA  # type: ignore[typeddict-item]

    async def test_z2m_custom_topic(self, hass: HomeAssistant):
        _setup_mock_mqtt(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BACKEND: BACKEND_Z2M},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MQTT_BASE_TOPIC: "custom/topic"},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY  # type: ignore[typeddict-item]
        assert result["data"][CONF_MQTT_BASE_TOPIC] == "custom/topic"  # type: ignore[typeddict-item]

    async def test_z2m_duplicate_blocked(self, hass: HomeAssistant):
        """Two entries with the same Z2M topic should be blocked."""
        _setup_mock_mqtt(hass)
        # Create first entry
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BACKEND: BACKEND_Z2M},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY  # type: ignore[typeddict-item]

        # Try to create duplicate
        result2 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_BACKEND: BACKEND_Z2M},
        )
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
        )
        assert result2["type"] == FlowResultType.ABORT  # type: ignore[typeddict-item]
        assert result2["reason"] == "already_configured"  # type: ignore[typeddict-item]

    async def test_zha_duplicate_blocked(self, hass: HomeAssistant):
        _setup_mock_zha(hass)
        # Create first
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BACKEND: BACKEND_ZHA},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY  # type: ignore[typeddict-item]

        # Duplicate
        result2 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_BACKEND: BACKEND_ZHA},
        )
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {},
        )
        assert result2["type"] == FlowResultType.ABORT  # type: ignore[typeddict-item]

    async def test_z2m_rejects_without_mqtt(self, hass: HomeAssistant):
        """Z2M setup should fail if MQTT integration is not configured."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BACKEND: BACKEND_Z2M},
        )
        assert result["step_id"] == "z2m"  # type: ignore[typeddict-item]

        # Submit without MQTT configured
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
        )
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["errors"]["base"] == "mqtt_not_configured"  # type: ignore[typeddict-item]

    async def test_zha_rejects_without_zha(self, hass: HomeAssistant):
        """ZHA setup should fail if ZHA integration is not configured."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BACKEND: BACKEND_ZHA},
        )
        assert result["step_id"] == "zha"  # type: ignore[typeddict-item]

        # Submit without ZHA configured
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["errors"]["base"] == "zha_not_configured"  # type: ignore[typeddict-item]


# ── TRV Selector Tests ────────────────────────────────────────────────


class TestBuildTrvSelector:
    """Tests for the _build_trv_selector helper."""

    def test_z2m_returns_device_selector(self):
        sel = _build_trv_selector(BACKEND_Z2M)
        assert isinstance(sel, selector.DeviceSelector)

    def test_zha_returns_device_selector(self):
        sel = _build_trv_selector(BACKEND_ZHA)
        assert isinstance(sel, selector.DeviceSelector)

    def test_z2m_selector_uses_mqtt_integration(self):
        sel = _build_trv_selector(BACKEND_Z2M)
        config = sel.config
        for f in config["filter"]:
            assert f["integration"] == "mqtt"

    def test_zha_selector_uses_zha_integration(self):
        sel = _build_trv_selector(BACKEND_ZHA)
        config = sel.config
        for f in config["filter"]:
            assert f["integration"] == "zha"

    def test_z2m_selector_allows_multiple(self):
        sel = _build_trv_selector(BACKEND_Z2M)
        assert sel.config["multiple"] is True

    def test_zha_selector_allows_multiple(self):
        sel = _build_trv_selector(BACKEND_ZHA)
        assert sel.config["multiple"] is True

    def test_z2m_filter_count_matches_supported_devices(self):
        sel = _build_trv_selector(BACKEND_Z2M)
        assert len(sel.config["filter"]) == len(SUPPORTED_TRV_DEVICES_Z2M)

    def test_zha_filter_count_matches_supported_devices(self):
        sel = _build_trv_selector(BACKEND_ZHA)
        assert len(sel.config["filter"]) == len(SUPPORTED_TRV_DEVICES_ZHA)

    def test_z2m_includes_danfoss(self):
        sel = _build_trv_selector(BACKEND_Z2M)
        manufacturers = [f["manufacturer"] for f in sel.config["filter"]]
        assert "Danfoss" in manufacturers

    def test_z2m_includes_popp(self):
        sel = _build_trv_selector(BACKEND_Z2M)
        manufacturers = [f["manufacturer"] for f in sel.config["filter"]]
        assert "Popp" in manufacturers

    def test_z2m_includes_hive(self):
        sel = _build_trv_selector(BACKEND_Z2M)
        manufacturers = [f["manufacturer"] for f in sel.config["filter"]]
        assert "Hive" in manufacturers

    def test_zha_includes_danfoss(self):
        sel = _build_trv_selector(BACKEND_ZHA)
        manufacturers = [f["manufacturer"] for f in sel.config["filter"]]
        assert "Danfoss" in manufacturers

    def test_zha_includes_popp_raw_manufacturer(self):
        """ZHA uses raw Zigbee manufacturer string 'D5X84YU' for Popp."""
        sel = _build_trv_selector(BACKEND_ZHA)
        manufacturers = [f["manufacturer"] for f in sel.config["filter"]]
        assert "D5X84YU" in manufacturers


# ── _get_assigned_trv_ids Tests ───────────────────────────────────────


class TestGetAssignedTrvIds:
    """Tests for the _get_assigned_trv_ids helper."""

    def _make_entry(self, subentries: dict) -> MagicMock:
        entry = MagicMock()
        entry.subentries = subentries
        return entry

    def _make_subentry(
        self, trv_ids: list[str], subentry_type: str = SUBENTRY_ROOM
    ) -> MagicMock:
        sub = MagicMock()
        sub.subentry_type = subentry_type
        sub.data = {CONF_TRV_ENTITIES: trv_ids}
        return sub

    def test_empty_subentries(self):
        entry = self._make_entry({})
        assert _get_assigned_trv_ids(entry) == set()

    def test_collects_all_trv_ids(self):
        entry = self._make_entry(
            {
                "sub1": self._make_subentry(["trv_a", "trv_b"]),
                "sub2": self._make_subentry(["trv_c"]),
            }
        )
        assert _get_assigned_trv_ids(entry) == {"trv_a", "trv_b", "trv_c"}

    def test_excludes_subentry(self):
        entry = self._make_entry(
            {
                "sub1": self._make_subentry(["trv_a", "trv_b"]),
                "sub2": self._make_subentry(["trv_c"]),
            }
        )
        assert _get_assigned_trv_ids(entry, exclude_subentry_id="sub1") == {"trv_c"}

    def test_ignores_non_room_subentries(self):
        entry = self._make_entry(
            {
                "sub1": self._make_subentry(["trv_a"], subentry_type="other"),
                "sub2": self._make_subentry(["trv_b"]),
            }
        )
        assert _get_assigned_trv_ids(entry) == {"trv_b"}


# ── _extract_room_data Tests ──────────────────────────────────────────


class TestExtractRoomData:
    """Tests for the _extract_room_data helper."""

    def test_minimal_input_returns_defaults(self):
        """Only required fields provided; optional fields get defaults."""
        result = _extract_room_data(
            {CONF_ROOM_NAME: "Kitchen", CONF_TRV_ENTITIES: ["trv_1"]}
        )
        assert result[CONF_ROOM_NAME] == "Kitchen"
        assert result[CONF_TRV_ENTITIES] == ["trv_1"]
        assert result[CONF_AREA] == ""
        assert result[CONF_TEMP_SENSOR] == ""
        assert result[CONF_HEAT_SOURCE] == ""
        assert result[CONF_HEAT_SOURCE_TYPE] == ""
        assert result[CONF_REMOTE_CLIMATE] == ""
        assert result[CONF_SCHEDULE_ENTITY] == ""
        # Temps always stored with defaults
        assert result[CONF_AT_HOME_TEMP] == DEFAULT_AT_HOME_TEMP
        assert result[CONF_AWAY_TEMP] == DEFAULT_AWAY_TEMP
        assert result[CONF_PREHEAT_ENABLED] is True

    def test_full_input_preserves_all_values(self):
        """All fields provided; every value is preserved in the result."""
        user_input = {
            CONF_ROOM_NAME: "Living Room",
            CONF_AREA: "area_123",
            CONF_TRV_ENTITIES: ["trv_a", "trv_b"],
            CONF_TEMP_SENSOR: "sensor.living_room_temp",
            CONF_HEAT_SOURCE: "climate.boiler",
            CONF_HEAT_SOURCE_TYPE: "climate",
            CONF_REMOTE_CLIMATE: "climate.remote",
            CONF_SCHEDULE_ENTITY: "schedule.weekday",
            CONF_AT_HOME_TEMP: 23.0,
            CONF_AWAY_TEMP: 15.0,
            CONF_PREHEAT_ENABLED: False,
        }
        result = _extract_room_data(user_input)
        assert result == user_input

    def test_multiple_trvs_preserved(self):
        """TRV list with multiple entries is kept intact."""
        trvs = ["trv_1", "trv_2", "trv_3"]
        result = _extract_room_data(
            {CONF_ROOM_NAME: "Bedroom", CONF_TRV_ENTITIES: trvs}
        )
        assert result[CONF_TRV_ENTITIES] == trvs


# ── Implicit Schedule Mode Tests ──────────────────────────────────────


class TestImplicitScheduleMode:
    """Tests for implicit schedule mode based on schedule entity selection."""

    def test_no_schedule_temps_use_defaults(self):
        """When no schedule selected, temperatures still stored with defaults."""
        result = _extract_room_data(
            {
                CONF_ROOM_NAME: "Kitchen",
                CONF_TRV_ENTITIES: ["trv_1"],
                CONF_SCHEDULE_ENTITY: "",
            }
        )
        assert result[CONF_SCHEDULE_ENTITY] == ""
        # Temps always get defaults
        assert result[CONF_AT_HOME_TEMP] == DEFAULT_AT_HOME_TEMP
        assert result[CONF_AWAY_TEMP] == DEFAULT_AWAY_TEMP

    def test_no_schedule_custom_temps_preserved(self):
        """When no schedule but user filled temps, preserve them."""
        result = _extract_room_data(
            {
                CONF_ROOM_NAME: "Kitchen",
                CONF_TRV_ENTITIES: ["trv_1"],
                CONF_SCHEDULE_ENTITY: "",
                CONF_AT_HOME_TEMP: 22.0,
                CONF_AWAY_TEMP: 18.0,
            }
        )
        assert result[CONF_SCHEDULE_ENTITY] == ""
        # User's values preserved
        assert result[CONF_AT_HOME_TEMP] == 22.0
        assert result[CONF_AWAY_TEMP] == 18.0

    def test_schedule_selected_empty_temps_use_defaults(self):
        """When schedule selected with empty temps, use defaults."""
        result = _extract_room_data(
            {
                CONF_ROOM_NAME: "Kitchen",
                CONF_TRV_ENTITIES: ["trv_1"],
                CONF_SCHEDULE_ENTITY: "schedule.weekday",
                CONF_AT_HOME_TEMP: "",
                CONF_AWAY_TEMP: "",
            }
        )
        assert result[CONF_SCHEDULE_ENTITY] == "schedule.weekday"
        assert result[CONF_AT_HOME_TEMP] == DEFAULT_AT_HOME_TEMP
        assert result[CONF_AWAY_TEMP] == DEFAULT_AWAY_TEMP

    def test_schedule_selected_none_temps_use_defaults(self):
        """When schedule selected with None temps, use defaults."""
        result = _extract_room_data(
            {
                CONF_ROOM_NAME: "Kitchen",
                CONF_TRV_ENTITIES: ["trv_1"],
                CONF_SCHEDULE_ENTITY: "schedule.weekday",
                CONF_AT_HOME_TEMP: None,
                CONF_AWAY_TEMP: None,
            }
        )
        assert result[CONF_AT_HOME_TEMP] == DEFAULT_AT_HOME_TEMP
        assert result[CONF_AWAY_TEMP] == DEFAULT_AWAY_TEMP

    def test_schedule_selected_valid_temps_preserved(self):
        """When schedule selected with valid temps, preserve values."""
        result = _extract_room_data(
            {
                CONF_ROOM_NAME: "Kitchen",
                CONF_TRV_ENTITIES: ["trv_1"],
                CONF_SCHEDULE_ENTITY: "schedule.weekday",
                CONF_AT_HOME_TEMP: 23.5,
                CONF_AWAY_TEMP: 16.0,
            }
        )
        assert result[CONF_SCHEDULE_ENTITY] == "schedule.weekday"
        assert result[CONF_AT_HOME_TEMP] == 23.5
        assert result[CONF_AWAY_TEMP] == 16.0

    def test_deselecting_schedule_preserves_temps(self):
        """When user deselects schedule, temperature values are preserved."""
        # First: had schedule with temps
        initial = _extract_room_data(
            {
                CONF_ROOM_NAME: "Kitchen",
                CONF_TRV_ENTITIES: ["trv_1"],
                CONF_SCHEDULE_ENTITY: "schedule.weekday",
                CONF_AT_HOME_TEMP: 23.0,
                CONF_AWAY_TEMP: 16.0,
            }
        )
        assert initial[CONF_AT_HOME_TEMP] == 23.0

        # Now: user clears schedule but temps remain
        updated = _extract_room_data(
            {
                CONF_ROOM_NAME: "Kitchen",
                CONF_TRV_ENTITIES: ["trv_1"],
                CONF_SCHEDULE_ENTITY: "",  # Deselected
                CONF_AT_HOME_TEMP: 23.0,  # Still in form
                CONF_AWAY_TEMP: 16.0,  # Still in form
            }
        )
        assert updated[CONF_SCHEDULE_ENTITY] == ""
        # Temps preserved for future use
        assert updated[CONF_AT_HOME_TEMP] == 23.0
        assert updated[CONF_AWAY_TEMP] == 16.0


# ── async_get_supported_subentry_types Tests ──────────────────────────


class TestAsyncGetSupportedSubentryTypes:
    """Tests for the async_get_supported_subentry_types classmethod."""

    def test_returns_room_subentry_type(self):
        """Returned dict maps SUBENTRY_ROOM to RoomSubentryFlowHandler."""
        mock_entry = MagicMock()
        result = DanfossAllyGatewayConfigFlow.async_get_supported_subentry_types(
            mock_entry
        )
        assert result == {SUBENTRY_ROOM: RoomSubentryFlowHandler}

    def test_returned_keys(self):
        """Only the room subentry type is returned."""
        mock_entry = MagicMock()
        result = DanfossAllyGatewayConfigFlow.async_get_supported_subentry_types(
            mock_entry
        )
        assert set(result.keys()) == {SUBENTRY_ROOM}


class TestRoomSubentryReconfigure:
    """Tests for the room subentry reconfigure flow."""

    async def test_reconfigure_prefills_trv_selector(self, hass: HomeAssistant):
        _setup_mock_mqtt(hass)

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_BACKEND: BACKEND_Z2M, CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
            title="Danfoss Ally Gateway (Z2M: zigbee2mqtt)",
            subentries_data=(
                {
                    "subentry_id": "room_1",
                    "subentry_type": SUBENTRY_ROOM,
                    "title": "Bedroom",
                    "data": {
                        CONF_ROOM_NAME: "Bedroom",
                        CONF_TRV_ENTITIES: ["device-uuid-123"],
                    },
                },
            ),
        )
        entry.add_to_hass(hass)

        result = await entry.start_subentry_reconfigure_flow(hass, "room_1")

        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["step_id"] == "reconfigure"  # type: ignore[typeddict-item]

        schema = result["data_schema"]
        step_key = next(
            key
            for key in schema.schema
            if getattr(key, "schema", None) == CONF_TRV_ENTITIES
        )
        assert step_key.description["suggested_value"] == ["device-uuid-123"]


# ── Invalid backend guard Tests ───────────────────────────────────────


class TestInvalidBackendGuard:
    """Tests for the invalid_backend error path in async_step_user.

    The SelectSelector schema rejects unknown values before the flow handler
    runs, so we call async_step_user directly to exercise the guard clause.
    """

    async def test_invalid_backend_shows_error(self, hass: HomeAssistant):
        """Unrecognized backend value triggers invalid_backend error."""
        # Initialise the flow so it is registered with hass
        init_result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        flow_id = init_result["flow_id"]
        flow = hass.config_entries.flow._progress[flow_id]

        # Call the step handler directly, bypassing schema validation
        result = await flow.async_step_user({CONF_BACKEND: "totally_unknown"})
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["step_id"] == "user"  # type: ignore[typeddict-item]
        assert result["errors"]["base"] == "invalid_backend"  # type: ignore[typeddict-item]


# ── Room Subentry Validation Error Tests ──────────────────────────────


class TestRoomSubentryValidationErrors:
    """Tests for room subentry validation error handling and form data preservation."""

    async def test_user_step_no_trvs_selected_preserves_form_data(
        self, hass: HomeAssistant
    ):
        """When no TRVs selected in add room flow, form data is preserved."""
        _setup_mock_mqtt(hass)

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_BACKEND: BACKEND_Z2M, CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
            title="Danfoss Ally Gateway (Z2M: zigbee2mqtt)",
        )
        entry.add_to_hass(hass)

        # Create flow handler and call step directly
        flow = RoomSubentryFlowHandler()
        flow.hass = hass
        flow._get_entry = lambda: entry

        # Submit with no TRVs selected
        user_input = {
            CONF_ROOM_NAME: "Living Room",
            CONF_AREA: "area_123",
            CONF_TRV_ENTITIES: [],  # No TRVs selected
            CONF_AT_HOME_TEMP: 22.5,
            CONF_AWAY_TEMP: 17.0,
        }
        result = await flow.async_step_user(user_input)

        # Verify error is shown
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["errors"][CONF_TRV_ENTITIES] == "no_trvs_selected"  # type: ignore[typeddict-item]

        # Verify form data is preserved via suggested_values
        schema = result["data_schema"]
        for key in schema.schema:
            field_name = getattr(key, "schema", None)
            if field_name == CONF_ROOM_NAME:
                assert key.description["suggested_value"] == "Living Room"
            elif field_name == CONF_AREA:
                assert key.description["suggested_value"] == "area_123"
            elif field_name == CONF_AT_HOME_TEMP:
                assert key.description["suggested_value"] == 22.5
            elif field_name == CONF_AWAY_TEMP:
                assert key.description["suggested_value"] == 17.0

    async def test_user_step_trv_already_assigned_preserves_form_data(
        self, hass: HomeAssistant
    ):
        """When TRV already assigned in add room flow, form data is preserved."""
        _setup_mock_mqtt(hass)

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_BACKEND: BACKEND_Z2M, CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
            title="Danfoss Ally Gateway (Z2M: zigbee2mqtt)",
            subentries_data=(
                {
                    "subentry_id": "room_1",
                    "subentry_type": SUBENTRY_ROOM,
                    "title": "Bedroom",
                    "data": {
                        CONF_ROOM_NAME: "Bedroom",
                        CONF_TRV_ENTITIES: ["device-uuid-123"],
                    },
                },
            ),
        )
        entry.add_to_hass(hass)

        # Create flow handler and call step directly
        flow = RoomSubentryFlowHandler()
        flow.hass = hass
        flow._get_entry = lambda: entry

        # Submit with a TRV that's already assigned to room_1
        user_input = {
            CONF_ROOM_NAME: "Kitchen",
            CONF_AREA: "area_456",
            CONF_TRV_ENTITIES: ["device-uuid-123"],  # Already assigned
            CONF_TEMP_SENSOR: "sensor.kitchen_temp",
            CONF_AT_HOME_TEMP: 21.0,
        }
        result = await flow.async_step_user(user_input)

        # Verify error is shown
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["errors"][CONF_TRV_ENTITIES] == "trv_already_assigned"  # type: ignore[typeddict-item]

        # Verify form data is preserved
        schema = result["data_schema"]
        for key in schema.schema:
            field_name = getattr(key, "schema", None)
            if field_name == CONF_ROOM_NAME:
                assert key.description["suggested_value"] == "Kitchen"
            elif field_name == CONF_AREA:
                assert key.description["suggested_value"] == "area_456"
            elif field_name == CONF_TEMP_SENSOR:
                assert key.description["suggested_value"] == "sensor.kitchen_temp"
            elif field_name == CONF_AT_HOME_TEMP:
                assert key.description["suggested_value"] == 21.0

    async def test_reconfigure_no_trvs_selected_preserves_form_data(
        self, hass: HomeAssistant
    ):
        """When no TRVs selected in reconfigure flow, form data is preserved."""
        _setup_mock_mqtt(hass)

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_BACKEND: BACKEND_Z2M, CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
            title="Danfoss Ally Gateway (Z2M: zigbee2mqtt)",
            subentries_data=(
                {
                    "subentry_id": "room_1",
                    "subentry_type": SUBENTRY_ROOM,
                    "title": "Bedroom",
                    "data": {
                        CONF_ROOM_NAME: "Bedroom",
                        CONF_TRV_ENTITIES: ["device-uuid-123"],
                        CONF_AT_HOME_TEMP: 20.0,
                    },
                },
            ),
        )
        entry.add_to_hass(hass)

        # Create flow handler and call step directly
        flow = RoomSubentryFlowHandler()
        flow.hass = hass
        flow._get_entry = lambda: entry
        flow._get_reconfigure_subentry = lambda: entry.subentries["room_1"]

        # Submit with modified data but no TRVs
        user_input = {
            CONF_ROOM_NAME: "Master Bedroom",
            CONF_AREA: "upstairs",
            CONF_TRV_ENTITIES: [],  # Deselected all TRVs
            CONF_AT_HOME_TEMP: 22.0,
        }
        result = await flow.async_step_reconfigure(user_input)

        # Verify error is shown
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["errors"][CONF_TRV_ENTITIES] == "no_trvs_selected"  # type: ignore[typeddict-item]

        # Verify form data is preserved from user_input, not reset to original
        schema = result["data_schema"]
        for key in schema.schema:
            field_name = getattr(key, "schema", None)
            if field_name == CONF_ROOM_NAME:
                assert key.description["suggested_value"] == "Master Bedroom"
            elif field_name == CONF_AREA:
                assert key.description["suggested_value"] == "upstairs"
            elif field_name == CONF_AT_HOME_TEMP:
                assert key.description["suggested_value"] == 22.0

    async def test_reconfigure_trv_already_assigned_preserves_form_data(
        self, hass: HomeAssistant
    ):
        """When TRV already assigned in reconfigure flow, form data is preserved."""
        _setup_mock_mqtt(hass)

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_BACKEND: BACKEND_Z2M, CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
            title="Danfoss Ally Gateway (Z2M: zigbee2mqtt)",
            subentries_data=(
                {
                    "subentry_id": "room_1",
                    "subentry_type": SUBENTRY_ROOM,
                    "title": "Bedroom",
                    "data": {
                        CONF_ROOM_NAME: "Bedroom",
                        CONF_TRV_ENTITIES: ["device-uuid-123"],
                    },
                },
                {
                    "subentry_id": "room_2",
                    "subentry_type": SUBENTRY_ROOM,
                    "title": "Kitchen",
                    "data": {
                        CONF_ROOM_NAME: "Kitchen",
                        CONF_TRV_ENTITIES: ["device-uuid-456"],
                    },
                },
            ),
        )
        entry.add_to_hass(hass)

        # Create flow handler and call step directly
        flow = RoomSubentryFlowHandler()
        flow.hass = hass
        flow._get_entry = lambda: entry
        flow._get_reconfigure_subentry = lambda: entry.subentries["room_1"]

        # Try to assign a TRV that's already in room_2
        user_input = {
            CONF_ROOM_NAME: "Bedroom Updated",
            CONF_TRV_ENTITIES: ["device-uuid-456"],  # Already in room_2
            CONF_TEMP_SENSOR: "sensor.bedroom_temp",
            CONF_AWAY_TEMP: 16.5,
        }
        result = await flow.async_step_reconfigure(user_input)

        # Verify error is shown
        assert result["type"] == FlowResultType.FORM  # type: ignore[typeddict-item]
        assert result["errors"][CONF_TRV_ENTITIES] == "trv_already_assigned"  # type: ignore[typeddict-item]

        # Verify form data is preserved
        schema = result["data_schema"]
        for key in schema.schema:
            field_name = getattr(key, "schema", None)
            if field_name == CONF_ROOM_NAME:
                assert key.description["suggested_value"] == "Bedroom Updated"
            elif field_name == CONF_TEMP_SENSOR:
                assert key.description["suggested_value"] == "sensor.bedroom_temp"
            elif field_name == CONF_AWAY_TEMP:
                assert key.description["suggested_value"] == 16.5
