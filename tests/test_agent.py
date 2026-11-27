"""End-to-end through Home Assistant: label + script -> agent -> service (mocked) -> script."""

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.voice_assistant_ha.const import DOMAIN

URL = "http://voice.test:8300"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def setup(hass: HomeAssistant, aioclient_mock):
    calls: list[ServiceCall] = []

    async def record(call: ServiceCall):
        calls.append(call)

    hass.services.async_register("test", "record", record)
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "script", {"script": {
        "spysaty": {
            "alias": "Списати продукт",
            "description": "Щось витратили. Приклади: взяла борошно",
            "fields": {
                "product": {"name": "Продукт", "required": True, "selector": {"text": {}}},
                "amount": {"name": "Кількість", "selector": {"number": {"step": "any"}}},
            },
            "sequence": [
                {"action": "test.record", "data": {"product": "{{ product }}", "amount": "{{ amount | default(0) }}"}},
                {"variables": {"out": {"speech": "Записала: {{ product }}."}}},
                {"stop": "done", "response_variable": "out"},
            ],
        },
        "unlabelled": {"alias": "Інше", "sequence": [{"action": "test.record"}]},
    }})
    label = lr.async_get(hass).async_create("Голос")
    reg = er.async_get(hass)
    reg.async_update_entity("script.spysaty", labels={label.label_id})

    entry = MockConfigEntry(domain=DOMAIN, data={"url": URL, "token": "t"}, options={
        "label": "Голос", "assistant_name": "Асистент", "min_confidence": 0.6,
        "fallback_agent": "none"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    agent_id = next(e.entity_id for e in reg.entities.values() if e.platform == DOMAIN)
    return calls, agent_id


async def converse(hass, agent_id, text, conv_id=None):
    return await conversation.async_converse(hass, text, conv_id, Context(), "uk", agent_id=agent_id)


async def test_runs_matched_script_and_speaks_its_answer(hass, setup, aioclient_mock):
    calls, agent_id = setup
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": "spysaty", "slots": {"product": "Борошно", "amount": 200}, "confidence": 0.9, "warnings": []}]})
    result = await converse(hass, agent_id, "взяла двісті грам борошна")
    assert result.response.speech["plain"]["speech"] == "Записала: Борошно."
    assert calls[0].data == {"product": "Борошно", "amount": 200}
    sent = aioclient_mock.mock_calls[0][2]
    assert [i["name"] for i in sent["intents"]] == ["spysaty"]  # unlabelled script not sent
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer t"


async def test_missing_required_field_is_asked_and_merged(hass, setup, aioclient_mock):
    calls, agent_id = setup
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": "spysaty", "slots": {"product": None, "amount": 2}, "confidence": 0.9, "warnings": []}]})
    first = await converse(hass, agent_id, "взяла два")
    assert first.continue_conversation is True
    assert "продукт" in first.response.speech["plain"]["speech"]
    assert calls == []

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": "spysaty", "slots": {"product": "Яйця", "amount": None}, "confidence": 0.9, "warnings": []}]})
    second = await converse(hass, agent_id, "яйця", first.conversation_id)
    assert calls[0].data == {"product": "Яйця", "amount": 2}
    assert second.continue_conversation is False
    assert "Асистент спитав" in aioclient_mock.mock_calls[0][2]["context"]


async def test_low_confidence_asks_to_confirm(hass, setup, aioclient_mock):
    calls, agent_id = setup
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": "spysaty", "slots": {"product": "Молоко"}, "confidence": 0.4, "warnings": []}]})
    first = await converse(hass, agent_id, "молоко щось")
    assert first.continue_conversation is True
    assert first.response.speech["plain"]["speech"].startswith("Виконати «Списати продукт»")
    assert calls == []
    second = await converse(hass, agent_id, "так", first.conversation_id)
    assert calls[0].data["product"] == "Молоко"
    assert second.response.speech["plain"]["speech"] == "Записала: Молоко."


async def test_confirm_can_be_declined(hass, setup, aioclient_mock):
    calls, agent_id = setup
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": "spysaty", "slots": {"product": "Молоко"}, "confidence": 0.4, "warnings": []}]})
    first = await converse(hass, agent_id, "молоко щось")
    second = await converse(hass, agent_id, "ні", first.conversation_id)
    assert calls == [] and "скасовано" in second.response.speech["plain"]["speech"]


async def test_nothing_matched_without_fallback(hass, setup, aioclient_mock):
    _, agent_id = setup
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": []})
    result = await converse(hass, agent_id, "яка погода")
    assert result.response.error_code is not None


async def test_service_down_falls_back_to_builtin_agent(hass, setup, aioclient_mock):
    _, agent_id = setup
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(entry, options={**entry.options,
                                                           "fallback_agent": "conversation.home_assistant"})
    await hass.async_block_till_done()
    aioclient_mock.post(f"{URL}/v1/match", status=503, json={"error": {"code": "UNAVAILABLE", "message": "x"}})
    result = await converse(hass, agent_id, "щось незрозуміле зовсім")
    # The built-in agent answered (it does not understand either, but it was asked).
    assert result.response.error_code == "no_intent_match" or result.response.speech
