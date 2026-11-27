"""Telegram source: people from the list, text and voice, silent when nothing matched."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.voice_assistant_ha.const import DOMAIN
from custom_components.voice_assistant_ha.telegram import TelegramBridge, UsersError, parse_users

URL = "http://voice.test:8300"
TG = "http://tg.test:8310"
TOKEN = "7000000001:abc"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def bridge(hass: HomeAssistant, aioclient_mock):
    calls: list[ServiceCall] = []

    async def record(call: ServiceCall):
        calls.append(call)

    hass.services.async_register("test", "record", record)
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "script", {"script": {
        "svitlo": {
            "alias": "Світло в коридорі",
            "sequence": [
                {"action": "test.record"},
                {"variables": {"out": {"speech": "Увімкнула світло."}}},
                {"stop": "done", "response_variable": "out"},
            ],
        },
    }})
    label = lr.async_get(hass).async_create("Голос")
    er.async_get(hass).async_update_entity("script.svitlo", labels={label.label_id})
    user = await hass.auth.async_create_user("Діма")
    hass.states.async_set("person.dima", "home", {"user_id": user.id})

    entry = MockConfigEntry(domain=DOMAIN, data={"url": URL, "token": "t"}, options={
        "label": "Голос", "assistant_name": "Асистент", "min_confidence": 0.6,
        "fallback_agent": "none", "devices": False})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    b = TelegramBridge(hass, entry, entry.runtime_data, async_get_clientsession(hass),
                       TG, TOKEN, {983470091: "person.dima"})
    return b, calls, user


def message(sender: int, **body):
    return {"update_id": 1, "message": {"message_id": 10, "from": {"id": sender},
                                        "chat": {"id": sender}, **body}}


def sent(aioclient_mock, method: str):
    return [c[2] for c in aioclient_mock.mock_calls if str(c[1]).endswith(f"/{method}")]


def test_parse_users():
    assert parse_users("983470091 = person.dima\n# коментар\n\n42: person.vlad") == {
        983470091: "person.dima", 42: "person.vlad"}
    with pytest.raises(UsersError):
        parse_users("dima = 42")


async def test_text_runs_as_that_person_and_replies(hass, bridge, aioclient_mock):
    b, calls, user = bridge
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": "svitlo", "slots": {}, "confidence": 0.95, "warnings": []}]})
    aioclient_mock.post(f"{TG}/bot{TOKEN}/sendChatAction", json={"ok": True, "result": True})
    aioclient_mock.post(f"{TG}/bot{TOKEN}/sendMessage", json={"ok": True, "result": {}})
    await b.handle(message(983470091, text="увімкни світло в коридорі"))
    assert calls and calls[0].context.user_id == user.id
    [reply] = sent(aioclient_mock, "sendMessage")
    assert reply["text"] == "Увімкнула світло."
    assert reply["chat_id"] == 983470091
    assert reply["reply_parameters"]["message_id"] == 10


async def test_strangers_and_commands_are_ignored(hass, bridge, aioclient_mock):
    b, calls, _ = bridge
    await b.handle(message(1, text="увімкни світло"))
    await b.handle(message(983470091, text="/komora молоко"))
    assert not aioclient_mock.mock_calls
    assert not calls


async def test_silent_when_nothing_matched(hass, bridge, aioclient_mock):
    b, _, _ = bridge
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": []})
    aioclient_mock.post(f"{TG}/bot{TOKEN}/sendChatAction", json={"ok": True, "result": True})
    aioclient_mock.post(f"{TG}/bot{TOKEN}/setMessageReaction", json={"ok": True, "result": True})
    await b.handle(message(983470091, text="купи молоко"))
    assert not sent(aioclient_mock, "sendMessage")
    [reaction] = sent(aioclient_mock, "setMessageReaction")  # a quiet «didn't get it»
    assert reaction["reaction"] == [{"type": "emoji", "emoji": "🤷"}]
    assert reaction["message_id"] == 10


async def test_voice_is_transcribed_with_hints(hass, bridge, aioclient_mock):
    b, calls, _ = bridge
    aioclient_mock.post(f"{TG}/bot{TOKEN}/sendChatAction", json={"ok": True, "result": True})
    aioclient_mock.post(f"{TG}/bot{TOKEN}/getFile", json={"ok": True, "result": {"file_path": "voice/1.oga"}})
    aioclient_mock.get(f"{TG}/file/bot{TOKEN}/voice/1.oga", content=b"OggS...")
    aioclient_mock.post(f"{URL}/v1/transcribe", json={"ok": True, "text": "увімкни світло"})
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": "svitlo", "slots": {}, "confidence": 0.95, "warnings": []}]})
    aioclient_mock.post(f"{TG}/bot{TOKEN}/sendMessage", json={"ok": True, "result": {}})
    with patch("custom_components.voice_assistant_ha.telegram.to_wav", return_value=b"RIFFxxxx") as conv:
        await b.handle(message(983470091, voice={"file_id": "F1", "file_size": 3000}))
    assert conv.await_args.args[1] == b"OggS..."
    transcribe = sent(aioclient_mock, "transcribe")[0]
    fields = {f[0]["name"]: f[2] for f in transcribe._fields}
    assert "Світло в коридорі" in fields["vocabulary"]
    assert calls
    [reply] = sent(aioclient_mock, "sendMessage")
    assert reply["text"] == "🎙 увімкни світло\n\nУвімкнула світло."


def tg_ok(aioclient_mock, *methods, result=True):
    for m in methods:
        aioclient_mock.post(f"{TG}/bot{TOKEN}/{m}", json={"ok": True, "result": result})


def button(sender: int, data: str, message_id: int = 55):
    return {"update_id": 2, "callback_query": {
        "id": "cb1", "from": {"id": sender}, "data": data,
        "message": {"message_id": message_id, "chat": {"id": 983470091}}}}


async def test_confirmation_comes_with_buttons_and_a_press_answers_it(hass, bridge, aioclient_mock):
    b, calls, _ = bridge
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": "svitlo", "slots": {}, "confidence": 0.3, "warnings": []}]})
    tg_ok(aioclient_mock, "sendChatAction", "answerCallbackQuery", "editMessageReplyMarkup")
    aioclient_mock.post(f"{TG}/bot{TOKEN}/sendMessage", json={"ok": True, "result": {"message_id": 55}})
    await b.handle(message(983470091, text="світло в коридорі"))
    assert not calls  # low confidence: asks first
    [question] = sent(aioclient_mock, "sendMessage")
    rows = question["reply_markup"]["inline_keyboard"]
    assert [[btn["text"] for btn in row] for row in rows] == [["Так", "Ні"]]
    assert rows[0][0]["callback_data"] == "va:0"

    await b.handle(button(983470091, "va:0"))
    assert calls  # «Так» ran the script
    edits = sent(aioclient_mock, "editMessageReplyMarkup")
    assert edits[0]["reply_markup"]["inline_keyboard"][0][0]["text"] == "✔ Так"
    assert sent(aioclient_mock, "sendMessage")[-1]["text"] == "Увімкнула світло."
    assert not b.questions


async def test_buttons_of_others_and_strangers(hass, bridge, aioclient_mock):
    b, calls, _ = bridge
    tg_ok(aioclient_mock, "answerCallbackQuery")
    await b.handle(button(983470091, "komora:1"))  # another service's button
    assert not aioclient_mock.mock_calls
    b.questions[(983470091, 55)] = __import__(
        "custom_components.voice_assistant_ha.telegram", fromlist=["OpenQuestion"]).OpenQuestion(983470091, ["Так", "Ні"])
    await b.handle(button(42, "va:0"))  # someone else presses
    [answer] = sent(aioclient_mock, "answerCallbackQuery")
    assert answer["text"] == "Це питання не вам."
    assert (983470091, 55) in b.questions and not calls



async def test_group_chats_are_ignored(hass, bridge, aioclient_mock):
    b, calls, _ = bridge
    await b.handle({"update_id": 1, "message": {
        "message_id": 5, "from": {"id": 983470091}, "text": "іди",
        "chat": {"id": -100123, "type": "supergroup"}}})
    assert calls == []
    assert not [c for c in aioclient_mock.mock_calls if "/v1/match" in str(c[1])]
    assert sent(aioclient_mock, "setMessageReaction") == []
