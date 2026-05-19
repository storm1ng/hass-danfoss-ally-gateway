"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import selector

from custom_components.danfoss_ally_gateway.config_flow import (
    _build_trv_selector,
    _get_assigned_trv_ids,
)
from custom_components.danfoss_ally_gateway.const import (
    BACKEND_Z2M,
    BACKEND_ZHA,
    CONF_BACKEND,
    CONF_MQTT_BASE_TOPIC,
    CONF_TRV_ENTITIES,
    DOMAIN,
    SUBENTRY_ROOM,
    SUPPORTED_TRV_DEVICES_Z2M,
    SUPPORTED_TRV_DEVICES_ZHA,
)

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
