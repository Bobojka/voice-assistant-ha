"""Who is speaking, their room, learned habits, corrections."""

import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.voice_assistant_ha.const import DOMAIN
from custom_components.voice_assistant_ha.habits import Habits, HomeAreasError, parse_home_areas

URL = "http://voice.test:8300"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def make(hass, aioclient_mock, homes=""):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {
        "pidsvitka_kukhnia": {"name": "Підсвітка"},
        "pidsvitka_dima": {"name": "Підсвітка"},
    }})
    await hass.async_block_till_done()
    areas = ar.async_get(hass)
    kitchen, bedroom = areas.async_create("Кухня"), areas.async_create("Спальня Діма")
    reg = er.async_get(hass)
    reg.async_update_entity("input_boolean.pidsvitka_kukhnia", area_id=kitchen.id)
    reg.async_update_entity("input_boolean.pidsvitka_dima", area_id=bedroom.id)
    for eid in ("input_boolean.pidsvitka_kukhnia", "input_boolean.pidsvitka_dima"):
        async_expose_entity(hass, "conversation", eid, True)
    await hass.services.async_call("input_boolean", "turn_on", {"entity_id": "all"}, blocking=True)
    user = await hass.auth.async_create_user("Діма")
    hass.states.async_set("person.dima", "home", {"user_id": user.id, "friendly_name": "Діма"})
    entry = MockConfigEntry(domain=DOMAIN, data={"url": URL, "token": "t"}, options={
        "label": "Голос", "fallback_agent": "none", "devices": True, "home_areas": homes})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    agent_id = next(e.entity_id for e in reg.entities.values()
                    if e.platform == DOMAIN and e.domain == "conversation")

    async def say(text, slots, conv_id=None):
        aioclient_mock.clear_requests()
        aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
            {"intent": "ha.turn_on_off", "slots": slots, "confidence": 0.95, "warnings": []}]})
        return await conversation.async_converse(hass, text, conv_id, Context(user_id=user.id), "uk",
                                                 agent_id=agent_id)
    return say


def state(hass, eid):
    return hass.states.get(f"input_boolean.{eid}").state


def test_parse_home_areas():
    assert parse_home_areas("person.dima = Спальня Діма\n# x\n") == {"person.dima": "Спальня Діма"}
    with pytest.raises(HomeAreasError):
        parse_home_areas("Діма — спальня")


def test_habit_needs_repeats_and_a_clear_majority(hass):
    h = Habits(hass)
    rooms = ["Кухня", "Спальня Діма"]
    h.record("p", "підсвітка", "Кухня")
    assert h.choose("p", "підсвітка", rooms, None) is None  # once is not a habit
    h.record("p", "підсвітка", "Кухня")
    assert h.choose("p", "підсвітка", rooms, "Спальня Діма") == "Кухня"
    h.record("p", "підсвітка", "Спальня Діма", 2)
    assert h.choose("p", "підсвітка", rooms, "Спальня Діма") == "Спальня Діма"  # no clear majority


async def test_own_room_by_default_and_said_aloud(hass, aioclient_mock):
    say = await make(hass, aioclient_mock, homes="person.dima = Спальня Діма")
    result = await say("вимкни підсвітку", {"device": "Підсвітка", "action": "off"})
    assert state(hass, "pidsvitka_dima") == "off" and state(hass, "pidsvitka_kukhnia") == "on"
    assert result.response.speech["plain"]["speech"] == "Вимкнула: Підсвітка (Спальня Діма)."
    assert "Говорить Діма" in aioclient_mock.mock_calls[-1][2]["context"]


async def test_asks_with_options_then_learns(hass, aioclient_mock):
    say = await make(hass, aioclient_mock)
    result = await say("вимкни підсвітку", {"device": "Підсвітка", "action": "off"})
    assert result.continue_conversation and "Де саме" in result.response.speech["plain"]["speech"]
    agent = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    assert agent.choices(result.conversation_id) == ["Підсвітка (Кухня)", "Підсвітка (Спальня Діма)"]
    assert state(hass, "pidsvitka_dima") == "on"

    # twice said «на кухні» -> next time the kitchen without asking
    await say("вимкни підсвітку на кухні", {"device": "Підсвітка (Кухня)", "action": "off"})
    await say("увімкни підсвітку на кухні", {"device": "Підсвітка", "area": "Кухня", "action": "on"})
    result = await say("вимкни підсвітку", {"device": "Підсвітка", "action": "off"})
    assert result.response.speech["plain"]["speech"] == "Вимкнула: Підсвітка (Кухня)."
    assert state(hass, "pidsvitka_kukhnia") == "off" and state(hass, "pidsvitka_dima") == "on"


async def test_correction_undoes_the_guess(hass, aioclient_mock):
    say = await make(hass, aioclient_mock, homes="person.dima = Спальня Діма")
    first = await say("вимкни підсвітку", {"device": "Підсвітка", "action": "off"})
    assert state(hass, "pidsvitka_dima") == "off"
    await say("ні, на кухні", {"device": "Підсвітка (Кухня)", "action": "off"}, first.conversation_id)
    assert state(hass, "pidsvitka_kukhnia") == "off"
    assert state(hass, "pidsvitka_dima") == "on"  # the wrong guess is turned back on
    agent = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    assert agent.habits.counts["person.dima"]["підсвітка"]["Кухня"] == 2


async def test_all_means_everywhere(hass, aioclient_mock):
    say = await make(hass, aioclient_mock, homes="person.dima = Спальня Діма")
    await say("вимкни всю підсвітку", {"device": "Підсвітка", "all": True, "action": "off"})
    assert state(hass, "pidsvitka_dima") == "off" and state(hass, "pidsvitka_kukhnia") == "off"
