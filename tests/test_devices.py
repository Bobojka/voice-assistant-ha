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
    assert dev.describe_state("Прокинувся: Влад", State("binary_sensor.v", "on")) == "Прокинувся: Влад: так."
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
