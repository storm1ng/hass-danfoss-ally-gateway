"""Config flow for Danfoss Ally Gateway integration.

Main config entry: Select backend (Z2M/ZHA), configure connection.
Room subentries: Each room with TRVs, optional temp sensor, optional heat source.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
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
    HEAT_SOURCE_BINARY_SENSOR,
    HEAT_SOURCE_CLIMATE,
    SUBENTRY_ROOM,
    SUPPORTED_TRV_DEVICES_Z2M,
    SUPPORTED_TRV_DEVICES_ZHA,
)

BACKEND_OPTIONS = [
    selector.SelectOptionDict(value=BACKEND_Z2M, label="Zigbee2MQTT"),
    selector.SelectOptionDict(value=BACKEND_ZHA, label="ZHA"),
]

HEAT_SOURCE_TYPE_OPTIONS = [
    selector.SelectOptionDict(value=HEAT_SOURCE_CLIMATE, label="Climate entity"),
    selector.SelectOptionDict(
        value=HEAT_SOURCE_BINARY_SENSOR, label="Binary sensor entity"
    ),
]


def _build_trv_selector(backend: str) -> selector.Selector:
    """Build a DeviceSelector for TRV selection based on backend type.

    For Z2M: filters MQTT devices by supported manufacturers/models.
    For ZHA: filters ZHA devices by supported manufacturers/models.
    Returns device registry IDs which are resolved to backend-specific
    identifiers at coordinator setup time.
    """
    if backend == BACKEND_Z2M:
        devices = SUPPORTED_TRV_DEVICES_Z2M
        integration = "mqtt"
    else:
        devices = SUPPORTED_TRV_DEVICES_ZHA
        integration = "zha"

    return selector.DeviceSelector(
        selector.DeviceSelectorConfig(
            filter=[
                selector.DeviceFilterSelectorConfig(
                    manufacturer=dev["manufacturer"],
                    model=dev["model"],
                    integration=integration,
                )
                for dev in devices
            ],
            multiple=True,
        )
    )


def _get_assigned_trv_ids(
    config_entry: ConfigEntry,
    exclude_subentry_id: str | None = None,
) -> set[str]:
    """Return TRV device IDs already assigned to rooms.

    If exclude_subentry_id is provided, TRVs from that subentry are excluded
    (used during reconfigure so a room can keep its own TRVs).
    """
    assigned: set[str] = set()
    for subentry_id, subentry in config_entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        if subentry_id == exclude_subentry_id:
            continue
        assigned.update(subentry.data.get(CONF_TRV_ENTITIES, []))
    return assigned


class DanfossAllyGatewayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Danfoss Ally Gateway."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - backend selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            backend = user_input[CONF_BACKEND]

            if backend == BACKEND_Z2M:
                # Store backend choice, proceed to Z2M config
                self._backend = backend
                return await self.async_step_z2m()
            if backend == BACKEND_ZHA:
                self._backend = backend
                return await self.async_step_zha()

            errors["base"] = "invalid_backend"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BACKEND, default=BACKEND_Z2M
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=BACKEND_OPTIONS,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_z2m(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure Zigbee2MQTT connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_topic = user_input.get(CONF_MQTT_BASE_TOPIC, "zigbee2mqtt")

            # Validate MQTT integration is set up (required by Z2M)
            if not self.hass.config_entries.async_entries("mqtt"):
                errors["base"] = "mqtt_not_configured"
            else:
                # Set unique ID based on backend + topic to prevent duplicates
                await self.async_set_unique_id(f"{BACKEND_Z2M}_{base_topic}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Danfoss Ally Gateway (Z2M: {base_topic})",
                    data={
                        CONF_BACKEND: BACKEND_Z2M,
                        CONF_MQTT_BASE_TOPIC: base_topic,
                    },
                )

        return self.async_show_form(
            step_id="z2m",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MQTT_BASE_TOPIC, default="zigbee2mqtt"
                    ): selector.TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_zha(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure ZHA connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate ZHA integration is set up
            if not self.hass.config_entries.async_entries("zha"):
                errors["base"] = "zha_not_configured"
            else:
                await self.async_set_unique_id(BACKEND_ZHA)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="Danfoss Ally Gateway (ZHA)",
                    data={
                        CONF_BACKEND: BACKEND_ZHA,
                    },
                )

        # ZHA doesn't need extra config - just confirm
        return self.async_show_form(
            step_id="zha",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {SUBENTRY_ROOM: RoomSubentryFlowHandler}

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the main config entry.

        Allows changing the Z2M MQTT base topic. ZHA has no configurable
        options, so reconfigure just confirms for ZHA.
        """
        entry = self._get_reconfigure_entry()
        backend = entry.data.get(CONF_BACKEND, BACKEND_Z2M)

        if backend == BACKEND_ZHA:
            # ZHA has nothing to reconfigure
            if user_input is not None:
                return self.async_abort(reason="zha_no_options")
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({}),
                description_placeholders={"backend": "ZHA"},
            )

        # Z2M: allow changing the base topic
        errors: dict[str, str] = {}
        if user_input is not None:
            new_topic = user_input.get(CONF_MQTT_BASE_TOPIC, "zigbee2mqtt")
            return self.async_update_reload_and_abort(
                entry,
                title=f"Danfoss Ally Gateway (Z2M: {new_topic})",
                data={
                    **entry.data,
                    CONF_MQTT_BASE_TOPIC: new_topic,
                },
            )

        current_topic = entry.data.get(CONF_MQTT_BASE_TOPIC, "zigbee2mqtt")
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MQTT_BASE_TOPIC, default=current_topic
                    ): selector.TextSelector(),
                }
            ),
            errors=errors,
        )


def _extract_room_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Extract room configuration data from user input.

    Temperature values are always validated and stored with defaults.
    The coordinator will only use them when a schedule entity is configured.
    """
    schedule_entity = user_input.get(CONF_SCHEDULE_ENTITY, "")

    # Always process and validate temperature values
    at_home_temp = user_input.get(CONF_AT_HOME_TEMP)
    if at_home_temp is None or at_home_temp == "":
        at_home_temp = DEFAULT_AT_HOME_TEMP

    away_temp = user_input.get(CONF_AWAY_TEMP)
    if away_temp is None or away_temp == "":
        away_temp = DEFAULT_AWAY_TEMP

    return {
        CONF_ROOM_NAME: user_input[CONF_ROOM_NAME],
        CONF_AREA: user_input.get(CONF_AREA, ""),
        CONF_TRV_ENTITIES: user_input[CONF_TRV_ENTITIES],
        CONF_TEMP_SENSOR: user_input.get(CONF_TEMP_SENSOR, ""),
        CONF_HEAT_SOURCE: user_input.get(CONF_HEAT_SOURCE, ""),
        CONF_HEAT_SOURCE_TYPE: user_input.get(CONF_HEAT_SOURCE_TYPE, ""),
        CONF_REMOTE_CLIMATE: user_input.get(CONF_REMOTE_CLIMATE, ""),
        CONF_SCHEDULE_ENTITY: schedule_entity,
        CONF_AT_HOME_TEMP: at_home_temp,
        CONF_AWAY_TEMP: away_temp,
        CONF_PREHEAT_ENABLED: user_input.get(CONF_PREHEAT_ENABLED, True),
    }


def _build_room_schema(
    backend: str,
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the room configuration schema.

    Args:
        backend: The backend type (Z2M or ZHA).
        defaults: Existing room data for pre-populating fields during
            reconfigure. When None, fields use their initial defaults.

    """
    trv_selector = _build_trv_selector(backend)
    is_reconfigure = defaults is not None

    def _field(
        key: str,
        required: bool = False,
        default: Any = vol.UNDEFINED,
    ) -> vol.Optional | vol.Required:
        """Build a schema field, using suggested_value for reconfigure.

        For required fields and optional fields with an explicit default
        (numbers, booleans), uses ``default=`` to pre-fill the value.
        For purely optional fields (entities, areas), uses
        ``suggested_value`` so the field can be left empty.
        """
        cls = vol.Required if required else vol.Optional
        if is_reconfigure and defaults is not None:
            existing_val = defaults.get(key, "" if not required else vol.UNDEFINED)
            if required or default is not vol.UNDEFINED:
                return cls(key, default=existing_val)
            return cls(key, description={"suggested_value": existing_val})
        if default is not vol.UNDEFINED:
            return cls(key, default=default)
        return cls(key)

    return vol.Schema(
        {
            _field(CONF_ROOM_NAME, required=True): selector.TextSelector(),
            _field(CONF_AREA): selector.AreaSelector(),
            _field(CONF_TRV_ENTITIES, required=True): trv_selector,
            _field(CONF_TEMP_SENSOR): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor",
                    device_class=SensorDeviceClass.TEMPERATURE,
                )
            ),
            _field(CONF_HEAT_SOURCE_TYPE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=HEAT_SOURCE_TYPE_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                ),
            ),
            _field(CONF_HEAT_SOURCE): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["climate", "binary_sensor"],
                )
            ),
            _field(CONF_REMOTE_CLIMATE): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="climate",
                )
            ),
            _field(CONF_SCHEDULE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="schedule",
                )
            ),
            _field(
                CONF_AT_HOME_TEMP, default=DEFAULT_AT_HOME_TEMP
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5.0,
                    max=35.0,
                    step=0.5,
                    unit_of_measurement="°C",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            _field(CONF_AWAY_TEMP, default=DEFAULT_AWAY_TEMP): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5.0,
                    max=35.0,
                    step=0.5,
                    unit_of_measurement="°C",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            _field(CONF_PREHEAT_ENABLED, default=True): selector.BooleanSelector(),
        }
    )


class RoomSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and modifying a room."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle adding a new room."""
        errors: dict[str, str] = {}

        if user_input is not None:
            trv_entities = user_input[CONF_TRV_ENTITIES]

            if not trv_entities:
                errors[CONF_TRV_ENTITIES] = "no_trvs_selected"
            elif set(trv_entities) & _get_assigned_trv_ids(self._get_entry()):
                errors[CONF_TRV_ENTITIES] = "trv_already_assigned"
            else:
                data = _extract_room_data(user_input)
                return self.async_create_entry(
                    title=data[CONF_ROOM_NAME],
                    data=data,
                )

        config_entry = self._get_entry()
        backend = config_entry.data.get(CONF_BACKEND, BACKEND_Z2M)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_room_schema(backend),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguring an existing room."""
        config_entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        existing = subentry.data

        errors: dict[str, str] = {}

        if user_input is not None:
            trv_entities = user_input[CONF_TRV_ENTITIES]

            if not trv_entities:
                errors[CONF_TRV_ENTITIES] = "no_trvs_selected"
            elif set(trv_entities) & _get_assigned_trv_ids(
                config_entry, subentry.subentry_id
            ):
                errors[CONF_TRV_ENTITIES] = "trv_already_assigned"
            else:
                data = _extract_room_data(user_input)
                return self.async_update_and_abort(
                    config_entry,
                    subentry,
                    title=data[CONF_ROOM_NAME],
                    data=data,
                )

        backend = config_entry.data.get(CONF_BACKEND, BACKEND_Z2M)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_build_room_schema(backend, defaults=dict(existing)),
            errors=errors,
        )
