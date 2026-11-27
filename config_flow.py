"""Config flow: service address and token; options: label, name, confidence, fallback."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ServiceError, VoiceServiceClient
from .habits import HomeAreasError, parse_home_areas, unknown_areas
from .telegram import UsersError, parse_users
from .const import (
    CONF_ASSISTANT_NAME,
    CONF_DEVICES,
    CONF_FALLBACK_AGENT,
    CONF_FOLLOW_UP,
    CONF_HOME_AREAS,
    CONF_LABEL,
    CONF_LOUD_DB,
    CONF_MIN_CONFIDENCE,
    CONF_QUIET_DB,
    CONF_RESET_HABITS,
    CONF_TG_FALLBACK,
    CONF_TG_TOKEN,
    CONF_TG_URL,
    CONF_TG_USERS,
    CONF_TOKEN,
    CONF_URL,
    CONF_VOLUME_ADAPT,
    CONF_VOLUME_MAX,
    CONF_VOLUME_MIN,
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_FALLBACK_AGENT,
    DEFAULT_FOLLOW_UP,
    DEFAULT_LABEL,
    DEFAULT_LOUD_DB,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_QUIET_DB,
    DEFAULT_URL,
    DEFAULT_VOLUME_ADAPT,
    DEFAULT_VOLUME_MAX,
    DEFAULT_VOLUME_MIN,
    DOMAIN,
    NO_FALLBACK,
)


def _slider(lo: float, hi: float, step: float) -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=lo, max=hi, step=step, mode=selector.NumberSelectorMode.SLIDER))


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
        vol.Required(CONF_FOLLOW_UP, default=values.get(CONF_FOLLOW_UP, DEFAULT_FOLLOW_UP)): bool,
        vol.Optional(CONF_HOME_AREAS, default=values.get(CONF_HOME_AREAS, "")): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)),
        vol.Optional(CONF_RESET_HABITS, default=False): bool,
        vol.Required(CONF_VOLUME_ADAPT,
                     default=values.get(CONF_VOLUME_ADAPT, DEFAULT_VOLUME_ADAPT)): bool,
        vol.Required(CONF_VOLUME_MIN, default=values.get(CONF_VOLUME_MIN, DEFAULT_VOLUME_MIN)):
            _slider(0, 1, 0.05),
        vol.Required(CONF_VOLUME_MAX, default=values.get(CONF_VOLUME_MAX, DEFAULT_VOLUME_MAX)):
            _slider(0, 1, 0.05),
        vol.Required(CONF_QUIET_DB, default=values.get(CONF_QUIET_DB, DEFAULT_QUIET_DB)):
            _slider(-80, 0, 1),
        vol.Required(CONF_LOUD_DB, default=values.get(CONF_LOUD_DB, DEFAULT_LOUD_DB)):
            _slider(-80, 0, 1),
        vol.Optional(CONF_TG_URL, default=values.get(CONF_TG_URL, "")): str,
        vol.Optional(CONF_TG_TOKEN, default=values.get(CONF_TG_TOKEN, "")): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Optional(CONF_TG_USERS, default=values.get(CONF_TG_USERS, "")): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)),
        vol.Required(CONF_TG_FALLBACK, default=values.get(CONF_TG_FALLBACK, False)): bool,
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
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_FALLBACK_AGENT):
                user_input[CONF_FALLBACK_AGENT] = NO_FALLBACK
            try:
                users = parse_users(user_input.get(CONF_TG_USERS, ""))
            except UsersError:
                errors[CONF_TG_USERS] = "bad_users"
            else:
                if any(self.hass.states.get(p) is None for p in users.values()):
                    errors[CONF_TG_USERS] = "unknown_person"
            try:
                homes = parse_home_areas(user_input.get(CONF_HOME_AREAS, ""))
            except HomeAreasError:
                errors[CONF_HOME_AREAS] = "bad_home_areas"
            else:
                if any(self.hass.states.get(p) is None for p in homes):
                    errors[CONF_HOME_AREAS] = "unknown_person"
                elif unknown_areas(self.hass, homes):
                    errors[CONF_HOME_AREAS] = "unknown_area"
            if not errors:
                if user_input.pop(CONF_RESET_HABITS, False):
                    agent = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
                    if agent is not None:
                        agent.habits.reset()
                return self.async_create_entry(data=user_input)
            values = user_input
        else:
            values = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_options_schema(values),
                                    errors=errors)
