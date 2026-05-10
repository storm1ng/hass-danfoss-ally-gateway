"""Tests for the config flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.danfoss_ally_gateway.const import (
    BACKEND_Z2M,
    BACKEND_ZHA,
    CONF_BACKEND,
    CONF_MQTT_BASE_TOPIC,
    DOMAIN,
)

# ── Main Config Flow ──────────────────────────────────────────────────


class TestMainConfigFlow:
    """Tests for the main config entry flow."""

    async def test_user_step_shows_form(self, hass: HomeAssistant):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

    async def test_z2m_backend_flow(self, hass: HomeAssistant):
        # Step 1: Select Z2M backend
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BACKEND: BACKEND_Z2M},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "z2m"

        # Step 2: Configure Z2M topic
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Danfoss Ally Gateway (Z2M: zigbee2mqtt)"
        assert result["data"][CONF_BACKEND] == BACKEND_Z2M
        assert result["data"][CONF_MQTT_BASE_TOPIC] == "zigbee2mqtt"

    async def test_zha_backend_flow(self, hass: HomeAssistant):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BACKEND: BACKEND_ZHA},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "zha"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Danfoss Ally Gateway (ZHA)"
        assert result["data"][CONF_BACKEND] == BACKEND_ZHA

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
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_MQTT_BASE_TOPIC] == "custom/topic"

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
        assert result["type"] == FlowResultType.CREATE_ENTRY

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
        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "already_configured"

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
        assert result["type"] == FlowResultType.CREATE_ENTRY

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
        assert result2["type"] == FlowResultType.ABORT
