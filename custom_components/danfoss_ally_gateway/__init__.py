"""Danfoss Ally Gateway - HACS Integration.

Replaces the Danfoss Ally Gateway's Zigbee coordination functionality
locally in Home Assistant for TRVs paired to ZHA or Zigbee2MQTT.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Danfoss Ally Gateway from a config entry."""
    _LOGGER.info("Setting up Danfoss Ally Gateway: %s", entry.title)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Danfoss Ally Gateway config entry."""
    _LOGGER.info("Unloading Danfoss Ally Gateway: %s", entry.title)

    hass.data[DOMAIN].pop(entry.entry_id, None)

    return True
