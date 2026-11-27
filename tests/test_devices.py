"""How the agent speaks about states and which service turns a device on."""

from datetime import timedelta

from homeassistant.core import State
from homeassistant.util import dt as dt_util

from custom_components.voice_assistant_ha import devices as dev


def test_vacuum_and_humidifier_calls():
    assert dev.on_off_call("vacuum.r5042h", "on") == ("vacuum", "start")
    assert dev.on_off_call("vacuum.r5042h", "off") == ("vacuum", "return_to_base")
    assert dev.on_off_call("light.x", "on") == ("homeassistant", "turn_on")
    assert dev.level_call("humidifier.h", 55) == ("humidifier", "set_humidity", {"humidity": 55})


def test_binary_sensor_answers_yes_no():
    assert dev.describe_state("Прокинувся: Влад", State("binary_sensor.v", "on")) == "Прокинувся: Влад — так."
    assert dev.describe_state("Електрика", State("binary_sensor.p", "off", {"device_class": "power"})) == "Електрика: немає."


def test_timestamp_is_spoken_as_local_time():
    soon = (dt_util.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
    text = dev.describe_state("Наступне відключення",
                              State("sensor.n", soon.isoformat(), {"device_class": "timestamp"}))
    assert text == "Наступне відключення: завтра о 9:30."
    assert dev.spoken_time("not a time") is None


def test_weather_in_ukrainian():
    state = State("weather.home", "partlycloudy", {"temperature": 3.5, "humidity": 80})
    assert dev.describe_state("Дім", state) == "Погода: мінлива хмарність, 3.5°, вологість 80%."


def test_vacuum_and_humidifier_states():
    assert dev.describe_state("Пилосос", State("vacuum.r", "docked")) == "Пилосос: на базі."
    h = State("humidifier.h", "on", {"humidity": 55, "current_humidity": 36})
    assert dev.describe_state("Зволожувач", h) == "Зволожувач: увімкнено, задано 55%, зараз 36%."


def test_guess_from_text():
    from custom_components.voice_assistant_ha.devices import Target, guess_from_text
    garland = Target("light.balkon_garland_girlianda", "Гірлянда", "Балкон")
    lamp = Target("light.lamp", "Лампа", "Вітальня")
    scene = Target("scene.girlianda", "Гірлянда святкова", None)
    assert guess_from_text([garland, lamp], "Вимкни гірлянду") == [garland]
    assert guess_from_text([garland, lamp], "вимкни все") == []
    assert guess_from_text([garland, scene, lamp], "вимкни гірлянду", ("scene",)) == [garland]


async def test_spoken_name_prefers_alias(hass):
    from homeassistant.helpers import entity_registry as er
    from custom_components.voice_assistant_ha.devices import spoken_name
    reg = er.async_get(hass)
    e = reg.async_get_or_create("light", "test", "g1", suggested_object_id="garland")
    assert spoken_name(hass, e.entity_id, "garland Гірлянда") == "garland Гірлянда"
    reg.async_update_entity(e.entity_id, aliases={"Гірлянда"})
    assert spoken_name(hass, e.entity_id, "garland Гірлянда") == "Гірлянда"


async def test_spoken_name_drops_model_prefix(hass):
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.voice_assistant_ha.devices import describe_state, spoken_name
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    devs, reg = dr.async_get(hass), er.async_get(hass)
    phone = devs.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("t", "p")},
                                     name="Redmi Note 9 Pro")
    ac = devs.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("t", "a")},
                                  name="Кондиціонер")
    e1 = reg.async_get_or_create("binary_sensor", "t", "1", device_id=phone.id)
    e2 = reg.async_get_or_create("switch", "t", "2", device_id=ac.id)
    assert spoken_name(hass, e1.entity_id, "Redmi Note 9 Pro Прокинувся: Влад") == "Прокинувся: Влад"
    assert spoken_name(hass, e2.entity_id, "Кондиціонер Сон") == "Кондиціонер Сон"
    hass.states.async_set(e1.entity_id, "off")
    assert describe_state("Прокинувся: Влад", hass.states.get(e1.entity_id)) == "Прокинувся: Влад — ні."
