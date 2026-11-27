"""Voice Assistant HA: a conversation agent backed by scripts labelled for voice
and the voice-assistant service (Bobojka/voice-assistant)."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import VoiceServiceClient
from .const import CONF_TG_TOKEN, CONF_TG_URL, CONF_TG_USERS, CONF_TOKEN, CONF_URL, DOMAIN
from .telegram import TelegramBridge, UsersError, parse_users

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION, Platform.SENSOR, Platform.STT]

type VoiceConfigEntry = ConfigEntry[VoiceServiceClient]


async def async_setup_entry(hass: HomeAssistant, entry: VoiceConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    entry.runtime_data = VoiceServiceClient(
        session, entry.data[CONF_URL], entry.data.get(CONF_TOKEN, "")
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))

    opts = entry.options
    if opts.get(CONF_TG_URL) and opts.get(CONF_TG_TOKEN):
        try:
            users = parse_users(opts.get(CONF_TG_USERS, ""))
        except UsersError as err:
            _LOGGER.error("telegram users: bad line %r", str(err))
            users = {}
        if users:
            TelegramBridge(hass, entry, entry.runtime_data, session,
                           opts[CONF_TG_URL], opts[CONF_TG_TOKEN], users).start()
    return True


async def _reload(hass: HomeAssistant, entry: VoiceConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: VoiceConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return ok
