from custom_components.voice_assistant_ha.catalog import build_catalog, describe_slots, yes_no

SCRIPTS = {
    "spysaty_produkt": {
        "name": "Списати продукт",
        "description": "Щось витратили під час готування.\nПриклади: взяла двісті грам борошна; трохи кориці",
        "fields": {
            "product": {"name": "Продукт", "description": "Назва з Комори", "required": True,
                        "selector": {"text": {}}},
            "amount": {"name": "Кількість", "selector": {"number": {"min": 0, "step": "any"}}},
            "unit": {"name": "Одиниця", "selector": {"select": {"options": ["g", "kg", "pcs"]}}},
            "approx": {"name": "Приблизно", "selector": {"boolean": {}}},
            "room": {"name": "Кімната", "selector": {"area": {}}},
        },
    },
    "bad name": {"name": "skip"},
}


def test_build_catalog():
    intents, commands = build_catalog(SCRIPTS)
    assert [i["name"] for i in intents] == ["spysaty_produkt"]
    intent = intents[0]
    assert intent["examples"] == ["взяла двісті грам борошна", "трохи кориці"]
    assert "Приклади" not in intent["description"]
    slots = {s["name"]: s for s in intent["slots"]}
    assert slots["product"]["required"] is True and slots["product"]["type"] == "string"
    assert slots["amount"]["type"] == "number"
    assert slots["unit"] == {**slots["unit"], "type": "enum", "values": ["g", "kg", "pcs"]}
    assert slots["approx"]["type"] == "boolean"
    assert slots["room"]["type"] == "string" and "area" in slots["room"]["description"]
    cmd = commands["spysaty_produkt"]
    assert cmd.required == ["product"] and cmd.fields["amount"] == "Кількість"
    assert describe_slots(cmd, {"product": "Борошно", "amount": 200, "unit": None}) == \
        "Продукт: Борошно, Кількість: 200"


def test_select_with_label_value_options_and_integer_step():
    _, _ = build_catalog({})
    intents, _ = build_catalog({"s": {"fields": {
        "mode": {"selector": {"select": {"options": [{"value": "eco", "label": "Еко"}, {"value": "max", "label": "Макс"}]}}},
        "count": {"selector": {"number": {"step": 1}}},
    }}})
    slots = {s["name"]: s for s in intents[0]["slots"]}
    assert slots["mode"]["values"] == ["eco", "max"]
    assert slots["count"]["type"] == "integer"


def test_yes_no():
    assert yes_no("Так.") is True
    assert yes_no("так, давай") is True
    assert yes_no("Ні") is False
    assert yes_no("не треба") is False
    assert yes_no("ні, не треба") is False
    assert yes_no("увімкни світло") is None
    assert yes_no("") is None
