"""Комора by voice: commands appear with the komora integration and call its actions."""

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError

from custom_components.voice_assistant_ha import komora

from test_agent import URL, auto_enable_custom_integrations, converse, setup  # noqa: F401

FOUND = [
    {"item_id": 24, "name": "Потенціометр WL B10K", "unit": "pcs", "qty": 2, "amount": "2 pcs",
     "locations": ["Балкон / Діма тумбочка"]},
    {"item_id": 18, "name": "Резистор 10 кОм", "unit": "pcs", "qty": 10, "amount": "10 pcs",
     "locations": ["Балкон / Діма тумбочка"]},
    {"item_id": 16, "name": "Резистор 1 кОм", "unit": "pcs", "qty": 20, "amount": "20 pcs",
     "locations": ["Балкон / Діма тумбочка"]},
]


def fake_komora(hass: HomeAssistant) -> list[ServiceCall]:
    calls: list[ServiceCall] = []

    async def find(call: ServiceCall):
        calls.append(call)
        return {"items": FOUND if "резист" in call.data["query"] else []}

    async def act(call: ServiceCall):
        calls.append(call)
        if call.data["item"] == "єдиноріг":
            raise ServiceValidationError("not found", translation_domain="komora",
                                         translation_key="item_not_found")
        if call.data["item"].startswith("оптопара"):
            return {"item_id": 30, "name": "Оптопара EL817CF", "created": True,
                    "categories": ["Електроніка / Оптрони"]}
        return {"item_id": 7, "name": "Молоко 2,5%"}

    hass.services.async_register("komora", "find", find, supports_response=SupportsResponse.ONLY)
    for name in ("write_off", "receive", "add_to_shopping"):
        hass.services.async_register("komora", name, act, supports_response=SupportsResponse.OPTIONAL)
    return calls


def match(aioclient_mock, intent, slots, confidence=0.9):
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{URL}/v1/match", json={"matches": [
        {"intent": intent, "slots": slots, "confidence": confidence, "warnings": []}]})


async def test_no_komora_no_commands(hass, setup, aioclient_mock):
    _, agent_id = setup
    match(aioclient_mock, "spysaty", {"product": "x"})
    await converse(hass, agent_id, "щось")
    sent = [i["name"] for i in aioclient_mock.mock_calls[0][2]["intents"]]
    assert not any(n.startswith("komora.") for n in sent)


async def test_how_much_lists_close_items_without_confirmation(hass, setup, aioclient_mock):
    _, agent_id = setup
    calls = fake_komora(hass)
    match(aioclient_mock, komora.STOCK, {"item": "резистор"}, confidence=0.3)
    result = await converse(hass, agent_id, "скільки резисторів залишилось")
    sent = [i["name"] for i in aioclient_mock.mock_calls[0][2]["intents"]]
    assert komora.STOCK in sent and komora.WRITE_OFF in sent
    assert calls[0].data["query"] == "резистор"
    assert result.response.speech["plain"]["speech"] == \
        "Є: Резистор 10 кОм — 10 шт; Резистор 1 кОм — 20 шт."


async def test_one_item_says_where(hass, setup, aioclient_mock):
    assert komora.stock_speech("потенціометр", FOUND[:1]) == \
        "Потенціометр WL B10K: 2 шт (Балкон / Діма тумбочка)."
    assert komora.stock_speech("молоко", []) == "У Коморі немає «молоко»."


async def test_write_off_and_receive(hass, setup, aioclient_mock):
    _, agent_id = setup
    calls = fake_komora(hass)
    match(aioclient_mock, komora.WRITE_OFF, {"item": "молоко", "qty": 0.5, "unit": "l"})
    result = await converse(hass, agent_id, "використав пів літра молока")
    assert calls[-1].service == "write_off"
    assert calls[-1].data == {"item": "молоко", "qty": 0.5, "unit": "l"}
    assert result.response.speech["plain"]["speech"] == "Списала: Молоко 2,5%, 0.5 л."

    match(aioclient_mock, komora.RECEIVE, {"item": "молоко"})
    result = await converse(hass, agent_id, "купив молоко")
    assert calls[-1].service == "receive" and calls[-1].data == {"item": "молоко"}
    assert result.response.speech["plain"]["speech"] == "Записала надходження: Молоко 2,5%."


async def test_unknown_item(hass, setup, aioclient_mock):
    _, agent_id = setup
    fake_komora(hass)
    match(aioclient_mock, komora.SHOPPING_ADD, {"item": "єдиноріг"})
    result = await converse(hass, agent_id, "додай єдинорога в покупки")
    assert result.response.speech["plain"]["speech"] == "Не знайшла в Коморі «єдиноріг»."


async def test_new_item_is_announced(hass, setup, aioclient_mock):
    _, agent_id = setup
    fake_komora(hass)
    match(aioclient_mock, komora.RECEIVE, {"item": "оптопара EL817CF", "qty": 10, "unit": "pcs"})
    result = await converse(hass, agent_id, "створи позицію оптопара EL817CF 10 штук")
    assert result.response.speech["plain"]["speech"] == \
        "Нова позиція «Оптопара EL817CF» (Оптрони). Записала надходження: Оптопара EL817CF, 10 шт."
