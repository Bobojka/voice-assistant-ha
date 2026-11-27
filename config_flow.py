"""Config flow: service address and token; options: label, name, confidence, fallback."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ServiceError, VoiceServiceClient
from .const import (
    CONF_ASSISTANT_NAME,
    CONF_DEVICES,
    CONF_FALLBACK_AGENT,
    CONF_LABEL,
    CONF_MIN_CONFIDENCE,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_FALLBACK_AGENT,
    DEFAULT_LABEL,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_URL,
    DOMAIN,
    NO_FALLBACK,
)


def _options_schema(values: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_LABEL, default=values.get(CONF_LABEL, DEFAULT_LABEL)): str,
        vol.Required(CONF_ASSISTANT_NAME,
                     default=values.get(CONF_ASSISTANT_NAME, DEFAULT_ASSISTANT_NAME)): str,
        vol.Required(CONF_MIN_CONFIDENCE,
                     default=values.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE)):
            selector.NumberSelector(selector.NumberSelectorConfig(
                min=0, max=1, step=0.05, mode=selector.NumberSelectorMode.SLIDER)),
        vol.Required(CONF_FALLBACK_AGENT,
                     default=values.get(CONF_FALLBACK_AGENT, DEFAULT_FALLBACK_AGENT)): str,
        vol.Required(CONF_DEVICES, default=values.get(CONF_DEVICES, True)): bool,
    })


class VoiceAssistantConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = VoiceServiceClient(async_get_clientsession(self.hass),
                                        user_input[CONF_URL], user_input.get(CONF_TOKEN, ""))
            try:
                await client.health()
            except ServiceError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_URL].rstrip("/"))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Voice Assistant", data=user_input)
        schema = vol.Schema({
            vol.Required(CONF_URL, default=DEFAULT_URL): str,
            vol.Optional(CONF_TOKEN, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VoiceAssistantOptionsFlow()


class VoiceAssistantOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if not user_input.get(CONF_FALLBACK_AGENT):
                user_input[CONF_FALLBACK_AGENT] = NO_FALLBACK
            return self.async_create_entry(data=user_input)
        values = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_options_schema(values))
