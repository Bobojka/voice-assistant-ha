"""Answer as loud as the request: measuring, mapping, setting the satellite volume."""

import math
from array import array

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.voice_assistant_ha import loudness
from custom_components.voice_assistant_ha.const import DOMAIN

URL = "http://voice.test:8300"


def tone(amplitude: int, seconds: float = 1.0) -> bytes:
    n = int(16000 * seconds)
    return array("h", (int(amplitude * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n))).tobytes()


def test_measure_voice_and_noise():
    quiet = tone(300, 0.5)  # background
    loud = tone(12000, 0.5)  # voice
    speech, noise = loudness.measure(quiet + loud)
    assert -12 < speech < -8  # ~ -11 dBFS for a 12000 sine
    assert -44 < noise < -40  # ~ -43 dBFS for a 300 sine
    assert loudness.measure(b"") == (-90.0, -90.0)
    assert loudness.measure(bytes(32000)) == (-90.0, -90.0)


def test_volume_mapping():
    L = loudness.Level
    assert loudness.volume_for(L(-50, -80, 0), -45, -20, 0.3, 1.0) == 0.3
    assert loudness.volume_for(L(-10, -80, 0), -45, -20, 0.3, 1.0) == 1.0
    assert loudness.volume_for(L(-32.5, -80, 0), -45, -20, 0.3, 1.0) == 0.65
    # quiet voice in a noisy room is answered loud
    assert loudness.volume_for(L(-40, -33, 0), -45, -20, 0.3, 1.0) == 0.92


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def satellite(hass: HomeAssistant, aioclient_mock):
    volumes: list[ServiceCall] = []

    async def record(call: ServiceCall):
        volumes.append(call)

    hass.services.async_register("media_player", "volume_set", record)
    hass.services.async_register("test", "record", lambda call: None)
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "script", {"script": {"svitlo": {
        "alias": "Світло", "sequence": [{"action": "test.record"}]}}})
    label = lr.async_get(hass).async_create("Голос")
    er.async_get(hass).async_update_entity("script.svitlo", labels={label.label_id})

    sat_entry = MockConfigEntry(domain="esphome")
    sat_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=sat_entry.entry_id, identifiers={("esphome", "headset")}, name="headset")
    er.async_get(hass).async_get_or_create(
        "media_player", "esphome", "headset_player", device_id=device.id,
        config_entry=sat_entry, suggested_object_id="headset")

    entry = MockConfigEntry(domain=DOMAIN, data={"url": URL, "token": "t"}, options={
        "label": "Голос", "assistant_name": "Асистент", "min_confidence": 0.6,
        "fallback_agent": "none", "devices": False})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    agent_id = next(e.entity_id for e in er.async_get(hass).entities.values()
                    if e.platform == DOMAIN and e.domain == "conversation")
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": "svitlo", "slots": {}, "confidence": 0.95, "warnings": []}]})
    return volumes, agent_id, device


async def test_satellite_volume_follows_the_request(hass, satellite):
    volumes, agent_id, device = satellite
    loudness.remember(hass, tone(300, 0.5) + tone(12000, 0.5))  # loud voice
    await conversation.async_converse(hass, "увімкни світло", None, Context(), "uk",
                                      agent_id=agent_id, device_id=device.id)
    assert volumes[-1].data == {"entity_id": ["media_player.headset"], "volume_level": 1.0}

    loudness.remember(hass, tone(50, 0.5) + tone(200, 0.5))  # whisper at night
    await conversation.async_converse(hass, "увімкни світло", None, Context(), "uk",
                                      agent_id=agent_id, device_id=device.id)
    assert volumes[-1].data["volume_level"] == 0.3

    # sensors show the last level
    assert float(hass.states.get("sensor.mock_title_riven_golosu").state) < -30


async def test_no_volume_without_satellite_or_fresh_level(hass, satellite):
    volumes, agent_id, device = satellite
    await conversation.async_converse(hass, "увімкни світло", None, Context(), "uk",
                                      agent_id=agent_id, device_id=device.id)  # no level yet
    loudness.remember(hass, tone(12000))
    await conversation.async_converse(hass, "увімкни світло", None, Context(), "uk",
                                      agent_id=agent_id)  # typed in the UI: no device
    assert not volumes
