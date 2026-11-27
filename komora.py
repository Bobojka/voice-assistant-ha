"""Комора (home inventory) by voice, through the komora integration's actions.

Commands appear in the catalog only when the komora integration is loaded:
how much is there, used up, bought, add to shopping, what to buy.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

DOMAIN = "komora"
PREFIX = "komora."
STOCK = f"{PREFIX}stock"
WRITE_OFF = f"{PREFIX}write_off"
RECEIVE = f"{PREFIX}receive"
SHOPPING_ADD = f"{PREFIX}shopping_add"
SHOPPING_LIST = f"{PREFIX}shopping_list"

TITLES = {STOCK: "Скільки є в Коморі", WRITE_OFF: "Списати з Комори",
          RECEIVE: "Надійшло в Комору", SHOPPING_ADD: "Додати до покупок",
          SHOPPING_LIST: "Що купити"}
FIELDS = {"item": "що саме", "qty": "скільки", "unit": "одиниця"}

UNITS = {"pcs": "шт", "g": "г", "kg": "кг", "ml": "мл", "l": "л", "pack": "пач."}

_ITEM = {"name": "item", "type": "string", "required": True,
         "description": "Що саме, у називному відмінку однини, як назва товару "
                        "(«резисторів» -> «резистор», «молока» -> «молоко»)"}
_QTY = {"name": "qty", "type": "number",
        "description": "Скільки, якщо назвали (пів -> 0.5, пара -> 2)"}
_UNIT = {"name": "unit", "type": "enum", "values": list(UNITS), "stt_hint": False,
         "description": "Одиниця, лише якщо назвали: штук -> pcs, грам -> g, кілограм -> kg, "
                        "мілілітр -> ml, літр -> l, пачка -> pack"}


def available(hass: HomeAssistant) -> bool:
    return hass.services.has_service(DOMAIN, "find")


def build_intents() -> list[dict[str, Any]]:
    return [
        {"name": STOCK,
         "description": "Скільки чогось є вдома (в Коморі: продукти, хімія, ліки, деталі), чи є взагалі "
                        "і де лежить. Не для датчиків і пристроїв.",
         "slots": [_ITEM],
         "examples": ["скільки резисторів залишилось", "чи є молоко", "де лежать батарейки",
                      "скільки в нас рису"]},
        {"name": WRITE_OFF,
         "description": "Щось використали, з'їли, витратили або викинули — списати з Комори.",
         "slots": [_ITEM, {**_QTY, "required": True}, _UNIT],
         "examples": ["списати два резистори", "я використав пів літра молока",
                      "викинула пачку сиру", "з'їли три яйця"]},
        {"name": RECEIVE,
         "description": "Щось купили або принесли додому — записати надходження в Комору.",
         "slots": [_ITEM, _QTY, _UNIT],
         "examples": ["купив молоко", "надійшло десять резисторів", "принесла два кілограми картоплі"]},
        {"name": SHOPPING_ADD,
         "description": "Додати щось до списку покупок Комори (треба купити).",
         "slots": [_ITEM, _QTY],
         "examples": ["додай хліб у покупки", "треба купити молоко", "запиши купити батарейки"]},
        {"name": SHOPPING_LIST,
         "description": "Що треба купити: прочитати список покупок Комори.",
         "slots": [],
         "examples": ["що купити", "що в списку покупок", "що треба в магазині"]},
    ]


def _unit(code: str | None) -> str:
    return UNITS.get(code or "", code or "")


def _amount(text: str) -> str:
    """«20 pcs» -> «20 шт»."""
    num, _, unit = text.partition(" ")
    return f"{num} {_unit(unit)}".strip() if unit in UNITS else text


def _qty(qty: Any, unit: str | None) -> str:
    try:
        return f"{float(qty):g} {_unit(unit)}".strip()
    except (TypeError, ValueError):
        return ""


def _relevant(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Fuzzy search also brings near names («резистор» -> транзистор): keep the ones
    that contain the word's stem when there are any."""
    stem = query.strip().casefold()[:5]
    close = [i for i in items if stem and stem in str(i.get("name", "")).casefold()]
    return close or items


def stock_speech(query: str, items: list[dict[str, Any]]) -> str:
    items = _relevant(items, query)
    if not items:
        return f"У Коморі немає «{query}»."
    if len(items) == 1:
        it = items[0]
        where = f" ({', '.join(it['locations'][:2])})" if it.get("locations") else ""
        return f"{it['name']}: {_amount(it['amount'])}{where}."
    parts = [f"{it['name']} — {_amount(it['amount'])}" for it in items[:5]]
    return "Є: " + "; ".join(parts) + "."


def _todo_entity(hass: HomeAssistant) -> str | None:
    return next((e.entity_id for e in er.async_get(hass).entities.values()
                 if e.platform == DOMAIN and e.domain == "todo" and not e.disabled_by), None)


async def run(hass: HomeAssistant, name: str, slots: dict[str, Any], context: Context) -> str:
    """Runs a Komora command; returns what to say."""
    services = hass.services
    item = str(slots.get("item") or "").strip()
    try:
        if name == STOCK:
            res = await services.async_call(DOMAIN, "find", {"query": item, "limit": 8},
                                            blocking=True, context=context, return_response=True)
            return stock_speech(item, (res or {}).get("items") or [])

        if name == SHOPPING_LIST:
            todo = _todo_entity(hass)
            if todo is None:
                return "Не бачу списку покупок Комори."
            res = await services.async_call("todo", "get_items",
                                            {"entity_id": todo, "status": "needs_action"},
                                            blocking=True, context=context, return_response=True)
            names = [str(i.get("summary")) for i in ((res or {}).get(todo) or {}).get("items", [])]
            if not names:
                return "Купувати нічого не треба."
            more = f" і ще {len(names) - 10}" if len(names) > 10 else ""
            return "Купити: " + ", ".join(names[:10]) + more + "."

        data: dict[str, Any] = {"item": item}
        if slots.get("qty") is not None:
            data["qty"] = float(slots["qty"])
        if slots.get("unit") and name != SHOPPING_ADD:
            data["unit"] = str(slots["unit"])
        service = {WRITE_OFF: "write_off", RECEIVE: "receive", SHOPPING_ADD: "add_to_shopping"}[name]
        res = await services.async_call(DOMAIN, service, data, blocking=True, context=context,
                                        return_response=True)
    except ServiceValidationError as err:
        if getattr(err, "translation_key", None) == "item_not_found":
            return f"Не знайшла в Коморі «{item}»."
        return f"Комора: {err}"
    except HomeAssistantError as err:
        return f"Комора не відповіла: {err}"

    what = str((res or {}).get("name") or item)
    amount = _qty(slots.get("qty"), slots.get("unit")) if slots.get("qty") is not None else ""
    suffix = f", {amount}" if amount else ""
    if name == WRITE_OFF:
        return f"Списала: {what}{suffix}."
    if name == RECEIVE:
        return f"Записала надходження: {what}{suffix}."
    return f"Додала до покупок: {what}{suffix}."
