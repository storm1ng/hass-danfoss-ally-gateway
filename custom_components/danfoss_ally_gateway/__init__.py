"""Danfoss Ally Gateway - HACS Integration.

Replaces the Danfoss Ally Gateway's Zigbee coordination functionality
locally in Home Assistant for TRVs paired to ZHA or Zigbee2MQTT.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.danfoss_ally_gateway.backend import DanfossBackend
from custom_components.danfoss_ally_gateway.backend.z2m import Z2MBackend

from .const import BACKEND_Z2M, CONF_BACKEND, CONF_MQTT_BASE_TOPIC, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _create_backend(hass: HomeAssistant, entry: ConfigEntry) -> DanfossBackend:
    """Create the appropriate backend based on config entry data."""
    backend_type = entry.data[CONF_BACKEND]
    if backend_type == BACKEND_Z2M:
        base_topic = entry.data.get(CONF_MQTT_BASE_TOPIC, "zigbee2mqtt")
        return Z2MBackend(hass, base_topic)
    raise ValueError(f"Unknown backend type: {backend_type}")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Danfoss Ally Gateway config entry."""
    _LOGGER.info("Unloading Danfoss Ally Gateway: %s", entry.title)

    hass.data[DOMAIN].pop(entry.entry_id, None)

    return True
