"""Voice Assistant HA: a conversation agent backed by scripts labelled for voice
and the voice-assistant service (Bobojka/voice-assistant)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import VoiceServiceClient
from .const import CONF_TOKEN, CONF_URL

PLATFORMS = [Platform.CONVERSATION, Platform.STT]

type VoiceConfigEntry = ConfigEntry[VoiceServiceClient]


async def async_setup_entry(hass: HomeAssistant, entry: VoiceConfigEntry) -> bool:
    entry.runtime_data = VoiceServiceClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data.get(CONF_TOKEN, "")
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: VoiceConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: VoiceConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
