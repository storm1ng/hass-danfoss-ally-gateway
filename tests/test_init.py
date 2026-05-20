"""Tests for integration setup (__init__.py)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.danfoss_ally_gateway.const import (
    BACKEND_Z2M,
    BACKEND_ZHA,
    CONF_BACKEND,
    CONF_MQTT_BASE_TOPIC,
    DOMAIN,
)


def _make_ally_entry(
    hass: HomeAssistant,
    backend: str = BACKEND_Z2M,
    mqtt_base_topic: str = "zigbee2mqtt",
) -> MockConfigEntry:
    """Create and add a Danfoss Ally Gateway config entry."""
    data = {CONF_BACKEND: backend}
    if backend == BACKEND_Z2M:
        data[CONF_MQTT_BASE_TOPIC] = mqtt_base_topic
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    return entry


class TestSetupDependencyValidation:
    """Verify async_setup_entry checks for required backend integrations."""

    async def test_z2m_without_mqtt_not_ready(self, hass: HomeAssistant):
        """Setup with Z2M backend should result in SETUP_RETRY if MQTT is missing."""
        entry = _make_ally_entry(hass, backend=BACKEND_Z2M)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY

    async def test_zha_without_zha_not_ready(self, hass: HomeAssistant):
        """Setup with ZHA backend should result in SETUP_RETRY if ZHA is missing."""
        entry = _make_ally_entry(hass, backend=BACKEND_ZHA)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY
