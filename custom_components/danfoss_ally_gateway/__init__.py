"""Danfoss Ally Gateway - HACS Integration.

Replaces the Danfoss Ally Gateway's Zigbee coordination functionality
locally in Home Assistant for TRVs paired to ZHA or Zigbee2MQTT.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .backend import DanfossBackend
from .backend.z2m import Z2MBackend
from .const import (
    BACKEND_Z2M,
    BACKEND_ZHA,
    CONF_AREA,
    CONF_BACKEND,
    CONF_MQTT_BASE_TOPIC,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_ROOM,
)
from .coordinator import RoomCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)


def _assign_room_area(
    hass: HomeAssistant, entry_id: str, subentry_id: str, area_id: str
) -> None:
    """Assign a room's virtual device to an HA area."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(
        identifiers={(DOMAIN, f"{entry_id}_{subentry_id}")}
    )
    if device is not None:
        dev_reg.async_update_device(device.id, area_id=area_id)


def _create_backend(hass: HomeAssistant, entry: ConfigEntry) -> DanfossBackend:
    """Create the appropriate backend based on config entry data."""
    backend_type = entry.data[CONF_BACKEND]
    if backend_type == BACKEND_Z2M:
        base_topic = entry.data.get(CONF_MQTT_BASE_TOPIC, "zigbee2mqtt")
        return Z2MBackend(hass, base_topic)
    # ZHA Backend is not implemented yet
    raise ValueError(f"Unknown backend type: {backend_type}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Danfoss Ally Gateway from a config entry."""
    _LOGGER.info("Setting up Danfoss Ally Gateway: %s", entry.title)

    # Validate required backend integration is available
    backend_type = entry.data[CONF_BACKEND]
    if backend_type == BACKEND_Z2M and not hass.config_entries.async_entries("mqtt"):
        raise ConfigEntryNotReady(
            "MQTT integration is not set up. Zigbee2MQTT requires MQTT."
        )
    if backend_type == BACKEND_ZHA and not hass.config_entries.async_entries("zha"):
        raise ConfigEntryNotReady("ZHA integration is not set up.")

    hass.data.setdefault(DOMAIN, {})

    # Create backend
    backend = _create_backend(hass, entry)
    await backend.async_setup()

    # Store integration data
    entry_data: dict[str, Any] = {
        "backend": backend,
        "coordinators": {},
        "platform_add_entities": {},
    }
    hass.data[DOMAIN][entry.entry_id] = entry_data

    # Set up coordinators for existing room subentries
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_ROOM:
            coordinator = RoomCoordinator(hass, backend, deepcopy(dict(subentry.data)))
            entry_data["coordinators"][subentry_id] = coordinator
            await coordinator.async_setup()

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Assign areas to room devices (devices are created during platform setup)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_ROOM:
            area_id = subentry.data.get(CONF_AREA, "")
            if area_id:
                _assign_room_area(hass, entry.entry_id, subentry_id, area_id)

    # Register schedule management services
    async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Danfoss Ally Gateway config entry."""
    _LOGGER.info("Unloading Danfoss Ally Gateway: %s", entry.title)

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, {})

        # Tear down coordinators
        coordinators: dict[str, RoomCoordinator] = entry_data.get("coordinators", {})
        for coordinator in coordinators.values():
            await coordinator.async_teardown()

        # Tear down backend
        backend: DanfossBackend | None = entry_data.get("backend")
        if backend is not None:
            await backend.async_teardown()

        # Unregister services if no other entries remain
        async_unregister_services(hass)

    return unload_ok


async def async_setup_subentry(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
) -> bool:
    """Set up a room subentry."""
    if subentry.subentry_type != SUBENTRY_ROOM:
        return False

    _LOGGER.info("Setting up room subentry: %s", subentry.title)

    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data is None:
        _LOGGER.error("Config entry data not found for %s", entry.entry_id)
        return False

    backend: DanfossBackend = entry_data["backend"]
    coordinator = RoomCoordinator(hass, backend, deepcopy(dict(subentry.data)))
    entry_data["coordinators"][subentry.subentry_id] = coordinator
    await coordinator.async_setup()

    # Add entities for the new coordinator using stored platform callbacks
    platform_callbacks: dict = entry_data.get("platform_add_entities", {})

    from .binary_sensor import create_room_entities as create_binary_sensor_entities
    from .climate import create_room_entities as create_climate_entities
    from .select import create_room_entities as create_select_entities
    from .sensor import create_room_entities as create_sensor_entities
    from .switch import create_room_entities as create_switch_entities

    creators = {
        "climate": create_climate_entities,
        "binary_sensor": create_binary_sensor_entities,
        "sensor": create_sensor_entities,
        "select": create_select_entities,
        "switch": create_switch_entities,
    }

    for platform, creator in creators.items():
        add_entities = platform_callbacks.get(platform)
        if add_entities is not None:
            entities = creator(coordinator, entry.entry_id, subentry.subentry_id)
            add_entities(entities, config_subentry_id=subentry.subentry_id)

    # Assign area to the room's virtual device
    area_id = subentry.data.get(CONF_AREA, "")
    if area_id:
        _assign_room_area(hass, entry.entry_id, subentry.subentry_id, area_id)

    return True


async def async_unload_subentry(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
) -> bool:
    """Unload a room subentry."""
    if subentry.subentry_type != SUBENTRY_ROOM:
        return False

    _LOGGER.info("Unloading room subentry: %s", subentry.title)

    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data is None:
        return True

    coordinator = entry_data["coordinators"].pop(subentry.subentry_id, None)
    if coordinator is not None:
        await coordinator.async_teardown()

    return True
