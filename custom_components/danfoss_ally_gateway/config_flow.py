"""Config flow for Danfoss Ally Gateway integration.

Main config entry: Select backend (Z2M/ZHA), configure connection.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.helpers import selector

from .const import (
    BACKEND_Z2M,
    BACKEND_ZHA,
    CONF_BACKEND,
    CONF_MQTT_BASE_TOPIC,
    DOMAIN,
)

BACKEND_OPTIONS = [
    selector.SelectOptionDict(value=BACKEND_Z2M, label="Zigbee2MQTT"),
    selector.SelectOptionDict(value=BACKEND_ZHA, label="ZHA"),
]


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

        return self.async_show_form(
            step_id="zha",
            data_schema=vol.Schema({}),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the main config entry."""
        entry = self._get_reconfigure_entry()
        backend = entry.data.get(CONF_BACKEND, BACKEND_Z2M)

        if backend == BACKEND_ZHA:
            if user_input is not None:
                return self.async_abort(reason="zha_no_options")
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({}),
                description_placeholders={"backend": "ZHA"},
            )

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
