"""Service registration for Danfoss Ally Gateway schedule management."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN
from .coordinator import RoomCoordinator
from .schedule import WeeklySchedule

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_ROOM_SCHEDULE = "set_room_schedule"
SERVICE_CLEAR_ROOM_SCHEDULE = "clear_room_schedule"
SERVICE_SET_SCHEDULE_MODE = "set_schedule_mode"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_SUBENTRY_ID = "subentry_id"
ATTR_SCHEDULE = "schedule"
ATTR_ENABLED = "enabled"
ATTR_PREHEAT = "preheat"
ATTR_ECO = "eco"

SET_ROOM_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required(ATTR_SUBENTRY_ID): str,
        vol.Required(ATTR_SCHEDULE): dict,
    }
)

CLEAR_ROOM_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required(ATTR_SUBENTRY_ID): str,
    }
)

SET_SCHEDULE_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required(ATTR_SUBENTRY_ID): str,
        vol.Required(ATTR_ENABLED): bool,
        vol.Optional(ATTR_PREHEAT, default=False): bool,
        vol.Optional(ATTR_ECO, default=False): bool,
    }
)


def _get_coordinator(
    hass: HomeAssistant, config_entry_id: str, subentry_id: str
) -> RoomCoordinator:
    """Look up a RoomCoordinator by config entry and subentry IDs.

    Raises ServiceValidationError if not found.
    """
    domain_data = hass.data.get(DOMAIN)
    if domain_data is None:
        raise ServiceValidationError(
            f"Integration {DOMAIN} is not set up",
            translation_domain=DOMAIN,
            translation_key="integration_not_setup",
        )

    entry_data = domain_data.get(config_entry_id)
    if entry_data is None:
        raise ServiceValidationError(
            f"Config entry {config_entry_id} not found",
            translation_domain=DOMAIN,
            translation_key="config_entry_not_found",
        )

    coordinators: dict[str, RoomCoordinator] = entry_data.get("coordinators", {})
    coordinator = coordinators.get(subentry_id)
    if coordinator is None:
        raise ServiceValidationError(
            f"Room subentry {subentry_id} not found",
            translation_domain=DOMAIN,
            translation_key="subentry_not_found",
        )

    return coordinator


async def async_handle_set_room_schedule(call: ServiceCall) -> None:
    """Handle the set_room_schedule service call."""
    hass = call.hass
    config_entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    subentry_id = call.data[ATTR_SUBENTRY_ID]
    schedule_data = call.data[ATTR_SCHEDULE]

    coordinator = _get_coordinator(hass, config_entry_id, subentry_id)

    # Parse the schedule data
    try:
        schedule = WeeklySchedule.from_dict(schedule_data)
    except (KeyError, TypeError, ValueError) as err:
        raise ServiceValidationError(
            f"Invalid schedule data: {err}",
            translation_domain=DOMAIN,
            translation_key="invalid_schedule_data",
        ) from err

    # Validate
    errors = schedule.validate()
    if errors:
        raise ServiceValidationError(
            f"Schedule validation failed: {'; '.join(errors)}",
            translation_domain=DOMAIN,
            translation_key="schedule_validation_failed",
        )

    try:
        await coordinator.async_program_schedule(schedule)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except Exception as err:
        raise HomeAssistantError(f"Failed to program schedule: {err}") from err


async def async_handle_clear_room_schedule(call: ServiceCall) -> None:
    """Handle the clear_room_schedule service call."""
    hass = call.hass
    config_entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    subentry_id = call.data[ATTR_SUBENTRY_ID]

    coordinator = _get_coordinator(hass, config_entry_id, subentry_id)

    try:
        await coordinator.async_clear_schedule()
    except Exception as err:
        raise HomeAssistantError(f"Failed to clear schedule: {err}") from err


async def async_handle_set_schedule_mode(call: ServiceCall) -> None:
    """Handle the set_schedule_mode service call."""
    hass = call.hass
    config_entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    subentry_id = call.data[ATTR_SUBENTRY_ID]
    enabled = call.data[ATTR_ENABLED]
    preheat = call.data.get(ATTR_PREHEAT, False)
    eco = call.data.get(ATTR_ECO, False)

    coordinator = _get_coordinator(hass, config_entry_id, subentry_id)

    try:
        await coordinator.async_set_schedule_mode(
            enabled=enabled, preheat=preheat, eco=eco
        )
    except Exception as err:
        raise HomeAssistantError(f"Failed to set schedule mode: {err}") from err


def async_register_services(hass: HomeAssistant) -> None:
    """Register all schedule management services."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_ROOM_SCHEDULE):
        return  # Already registered (e.g., multiple config entries)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ROOM_SCHEDULE,
        async_handle_set_room_schedule,
        schema=SET_ROOM_SCHEDULE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_ROOM_SCHEDULE,
        async_handle_clear_room_schedule,
        schema=CLEAR_ROOM_SCHEDULE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE_MODE,
        async_handle_set_schedule_mode,
        schema=SET_SCHEDULE_MODE_SCHEMA,
    )

    _LOGGER.debug("Registered Danfoss Ally Gateway schedule services")


def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister schedule management services.

    Only unregisters if no other config entries are still loaded.
    """
    domain_data = hass.data.get(DOMAIN, {})
    if domain_data:
        return  # Other entries still loaded, keep services

    hass.services.async_remove(DOMAIN, SERVICE_SET_ROOM_SCHEDULE)
    hass.services.async_remove(DOMAIN, SERVICE_CLEAR_ROOM_SCHEDULE)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SCHEDULE_MODE)

    _LOGGER.debug("Unregistered Danfoss Ally Gateway schedule services")
