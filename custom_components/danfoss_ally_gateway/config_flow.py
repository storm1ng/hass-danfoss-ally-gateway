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
        if user_input is not None:
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


class RoomSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and modifying a room."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle adding a new room."""
        errors: dict[str, str] = {}

        if user_input is not None:
            room_name = user_input[CONF_ROOM_NAME]
            trv_entities = user_input[CONF_TRV_ENTITIES]

            if not trv_entities:
                errors[CONF_TRV_ENTITIES] = "no_trvs_selected"
            else:
                return self.async_create_entry(
                    title=room_name,
                    data={
                        CONF_ROOM_NAME: room_name,
                        CONF_AREA: user_input.get(CONF_AREA, ""),
                        CONF_TRV_ENTITIES: trv_entities,
                        CONF_TEMP_SENSOR: user_input.get(CONF_TEMP_SENSOR, ""),
                        CONF_HEAT_SOURCE: user_input.get(CONF_HEAT_SOURCE, ""),
                        CONF_HEAT_SOURCE_TYPE: user_input.get(
                            CONF_HEAT_SOURCE_TYPE, ""
                        ),
                        CONF_REMOTE_CLIMATE: user_input.get(CONF_REMOTE_CLIMATE, ""),
                        CONF_SCHEDULE_ENTITY: user_input.get(CONF_SCHEDULE_ENTITY, ""),
                        CONF_AT_HOME_TEMP: user_input.get(
                            CONF_AT_HOME_TEMP, DEFAULT_AT_HOME_TEMP
                        ),
                        CONF_AWAY_TEMP: user_input.get(
                            CONF_AWAY_TEMP, DEFAULT_AWAY_TEMP
                        ),
                        CONF_PREHEAT_ENABLED: user_input.get(
                            CONF_PREHEAT_ENABLED, True
                        ),
                    },
                )

        config_entry = self._get_entry()
        backend = config_entry.data.get(CONF_BACKEND, BACKEND_Z2M)

        # Build TRV device selector based on backend
        trv_selector = _build_trv_selector(backend)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOM_NAME): selector.TextSelector(),
                    vol.Optional(CONF_AREA): selector.AreaSelector(),
                    vol.Required(CONF_TRV_ENTITIES): trv_selector,
                    vol.Optional(CONF_TEMP_SENSOR): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor",
                            device_class=SensorDeviceClass.TEMPERATURE,
                        )
                    ),
                    vol.Optional(CONF_HEAT_SOURCE_TYPE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=HEAT_SOURCE_TYPE_OPTIONS,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                    vol.Optional(CONF_HEAT_SOURCE): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=["climate", "binary_sensor"],
                        )
                    ),
                    vol.Optional(CONF_REMOTE_CLIMATE): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="climate",
                        )
                    ),
                    vol.Optional(CONF_SCHEDULE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="schedule",
                        )
                    ),
                    vol.Optional(
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
                    vol.Optional(
                        CONF_AWAY_TEMP, default=DEFAULT_AWAY_TEMP
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5.0,
                            max=35.0,
                            step=0.5,
                            unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_PREHEAT_ENABLED, default=True
                    ): selector.BooleanSelector(),
                }
            ),
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
            room_name = user_input[CONF_ROOM_NAME]
            trv_entities = user_input[CONF_TRV_ENTITIES]

            if not trv_entities:
                errors[CONF_TRV_ENTITIES] = "no_trvs_selected"
            else:
                return self.async_update_and_abort(
                    config_entry,
                    subentry,
                    title=room_name,
                    data={
                        CONF_ROOM_NAME: room_name,
                        CONF_AREA: user_input.get(CONF_AREA, ""),
                        CONF_TRV_ENTITIES: trv_entities,
                        CONF_TEMP_SENSOR: user_input.get(CONF_TEMP_SENSOR, ""),
                        CONF_HEAT_SOURCE: user_input.get(CONF_HEAT_SOURCE, ""),
                        CONF_HEAT_SOURCE_TYPE: user_input.get(
                            CONF_HEAT_SOURCE_TYPE, ""
                        ),
                        CONF_REMOTE_CLIMATE: user_input.get(CONF_REMOTE_CLIMATE, ""),
                        CONF_SCHEDULE_ENTITY: user_input.get(CONF_SCHEDULE_ENTITY, ""),
                        CONF_AT_HOME_TEMP: user_input.get(
                            CONF_AT_HOME_TEMP, DEFAULT_AT_HOME_TEMP
                        ),
                        CONF_AWAY_TEMP: user_input.get(
                            CONF_AWAY_TEMP, DEFAULT_AWAY_TEMP
                        ),
                        CONF_PREHEAT_ENABLED: user_input.get(
                            CONF_PREHEAT_ENABLED, True
                        ),
                    },
                )

        backend = config_entry.data.get(CONF_BACKEND, BACKEND_Z2M)

        trv_selector = _build_trv_selector(backend)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ROOM_NAME,
                        default=existing.get(CONF_ROOM_NAME, ""),
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_AREA,
                        description={"suggested_value": existing.get(CONF_AREA, "")},
                    ): selector.AreaSelector(),
                    vol.Required(
                        CONF_TRV_ENTITIES,
                        default=existing.get(CONF_TRV_ENTITIES, []),
                    ): trv_selector,
                    vol.Optional(
                        CONF_TEMP_SENSOR,
                        description={
                            "suggested_value": existing.get(CONF_TEMP_SENSOR, "")
                        },
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor",
                            device_class=SensorDeviceClass.TEMPERATURE,
                        )
                    ),
                    vol.Optional(
                        CONF_HEAT_SOURCE_TYPE,
                        description={
                            "suggested_value": existing.get(CONF_HEAT_SOURCE_TYPE, "")
                        },
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=HEAT_SOURCE_TYPE_OPTIONS,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                    vol.Optional(
                        CONF_HEAT_SOURCE,
                        description={
                            "suggested_value": existing.get(CONF_HEAT_SOURCE, "")
                        },
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=["climate", "binary_sensor"],
                        )
                    ),
                    vol.Optional(
                        CONF_REMOTE_CLIMATE,
                        description={
                            "suggested_value": existing.get(CONF_REMOTE_CLIMATE, "")
                        },
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="climate",
                        )
                    ),
                    vol.Optional(
                        CONF_SCHEDULE_ENTITY,
                        description={
                            "suggested_value": existing.get(CONF_SCHEDULE_ENTITY, "")
                        },
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="schedule",
                        )
                    ),
                    vol.Optional(
                        CONF_AT_HOME_TEMP,
                        default=existing.get(CONF_AT_HOME_TEMP, DEFAULT_AT_HOME_TEMP),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5.0,
                            max=35.0,
                            step=0.5,
                            unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_AWAY_TEMP,
                        default=existing.get(CONF_AWAY_TEMP, DEFAULT_AWAY_TEMP),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5.0,
                            max=35.0,
                            step=0.5,
                            unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_PREHEAT_ENABLED,
                        default=existing.get(CONF_PREHEAT_ENABLED, True),
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )
