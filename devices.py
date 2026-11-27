"""Built-in universal commands for devices exposed to Assist.

No script per device: the catalog gets the names of all entities exposed to
Assist (Налаштування → Голосові асистенти → Відкрити доступ) and of the areas,
and four generic commands: turn on/off, set a level, ask the state, activate a
scene or script. The agent resolves names to entities and calls the services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

PREFIX = "ha."
ON_OFF = f"{PREFIX}turn_on_off"
SET_LEVEL = f"{PREFIX}set_level"
STATE = f"{PREFIX}state"
ACTIVATE = f"{PREFIX}activate"

TITLES = {ON_OFF: "Увімкнути / вимкнути", SET_LEVEL: "Задати рівень",
          STATE: "Стан пристрою", ACTIVATE: "Запустити сцену"}

KINDS = {  # spoken kind -> domains
    "світло": ["light"],
    "розетки й перемикачі": ["switch", "input_boolean"],
    "кондиціонери й обігрівачі": ["climate"],
    "штори й ворота": ["cover"],
    "вентилятори": ["fan"],
    "медіа": ["media_player"],
    "пилосос": ["vacuum"],
    "зволожувачі": ["humidifier"],
    "усе": ["light", "switch", "input_boolean", "climate", "cover", "fan", "media_player"],
}
ACTIVATABLE = ("scene", "script")


@dataclass
class Target:
    entity_id: str
    name: str
    area: str | None


def _area_of(hass: HomeAssistant, entry: er.RegistryEntry | None) -> str | None:
    if entry is None:
        return None
    area_id = entry.area_id
    if area_id is None and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        area_id = device.area_id if device else None
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return area.name if area else None


def exposed_targets(hass: HomeAssistant, skip: set[str]) -> list[Target]:
    reg = er.async_get(hass)
    targets = []
    for state in hass.states.async_all():
        eid = state.entity_id
        if eid in skip or not async_should_expose(hass, "conversation", eid):
            continue
        entry = reg.async_get(eid)
        name = str(state.attributes.get(ATTR_FRIENDLY_NAME) or eid).strip()
        area = _area_of(hass, entry)
        targets.append(Target(eid, name, area))
        # Newer HA keeps a placeholder (not a string) for "the entity's own name" among aliases.
        for alias in (entry.aliases if entry else ()) or ():
            if isinstance(alias, str) and alias.strip() and alias.strip() != name:
                targets.append(Target(eid, alias.strip(), area))
    return targets


def _names(targets: list[Target], domains: tuple[str, ...] | None = None,
           exclude: tuple[str, ...] = ()) -> list[str]:
    out: list[str] = []
    for t in targets:
        d = t.entity_id.split(".")[0]
        if (domains and d not in domains) or d in exclude:
            continue
        label = f"{t.name} ({t.area})" if t.area else str(t.name)
        if label and label not in out:
            out.append(label)
    return out[:1000]


def build_device_intents(targets: list[Target], areas: list[str]) -> list[dict[str, Any]]:
    devices = _names(targets, exclude=ACTIVATABLE)
    activatable = _names(targets, domains=ACTIVATABLE)
    area_slot = ({"name": "area", "type": "enum", "values": areas,
                  "description": "Кімната, якщо названа"} if areas else
                 {"name": "area", "type": "string", "description": "Кімната, якщо названа"})
    kind_slot = {"name": "kind", "type": "enum", "values": list(KINDS), "stt_hint": False,
                 "description": "Група пристроїв, якщо назвали групу, а не конкретний пристрій "
                                "(«все світло на кухні» -> світло, area кухня)"}
    intents: list[dict[str, Any]] = []
    if devices:
        device_slot = {"name": "device", "type": "enum", "values": devices,
                       "description": "Конкретний пристрій, як його назвали (у дужках — кімната)"}
        intents += [
            {"name": ON_OFF,
             "description": "Увімкнути, вимкнути або перемкнути пристрій чи групу пристроїв у кімнаті; "
                            "відкрити/закрити штори, замкнути/відімкнути замок теж сюди.",
             "slots": [device_slot, area_slot, kind_slot,
                       {"name": "action", "type": "enum", "required": True, "stt_hint": False,
                        "values": ["on", "off", "toggle"],
                        "description": "увімкни/відкрий/замкни -> on, вимкни/закрий/відімкни -> off, перемкни -> toggle"}],
             "examples": ["увімкни кондиціонер", "вимкни все світло на кухні", "закрий штори в спальні"]},
            {"name": SET_LEVEL,
             "description": "Задати рівень: яскравість, температуру, гучність, положення штор, швидкість вентилятора, значення.",
             "slots": [device_slot, area_slot, kind_slot,
                       {"name": "value", "type": "number", "required": True,
                        "description": "Число: відсотки для яскравості/гучності/положення, градуси для температури"}],
             "examples": ["постав кондиціонер на 23", "яскравість світла у вітальні 40 відсотків",
                          "гучність телевізора на 20"]},
            {"name": STATE,
             "description": "Запитати стан чи показник пристрою або датчика (чи увімкнено, яка температура, вологість).",
             "slots": [device_slot, area_slot],
             "examples": ["чи увімкнений кондиціонер", "яка температура у спальні"]},
        ]
    if activatable:
        intents.append(
            {"name": ACTIVATE,
             "description": "Запустити сцену або скрипт за назвою (режим кіно, вечір...).",
             "slots": [{"name": "name", "type": "enum", "required": True, "values": activatable,
                        "description": "Назва сцени чи скрипту"}],
             "examples": ["увімкни режим кіно", "запусти сцену вечір"]})
    return intents


def _match_label(targets: list[Target], label: str) -> list[Target]:
    return [t for t in targets
            if (f"{t.name} ({t.area})" if t.area else str(t.name)) == label]


def resolve(targets: list[Target], slots: dict[str, Any],
            domains: tuple[str, ...] | None = None) -> list[Target]:
    """Device by its label, else area + kind; filtered to domains if given."""
    if slots.get("device"):
        found = _match_label(targets, slots["device"])
    else:
        kind_domains = KINDS.get(slots.get("kind") or "", None)
        area = slots.get("area")
        if not area and not kind_domains:
            return []
        found = [t for t in targets
                 if (not area or t.area == area)
                 and (not kind_domains or t.entity_id.split(".")[0] in kind_domains)
                 and t.entity_id.split(".")[0] not in ACTIVATABLE]
    if domains:
        found = [t for t in found if t.entity_id.split(".")[0] in domains]
    seen, out = set(), []
    for t in found:
        if t.entity_id not in seen:
            seen.add(t.entity_id)
            out.append(t)
    return out


def on_off_call(entity_id: str, action: str) -> tuple[str, str]:
    domain = entity_id.split(".")[0]
    if domain == "cover":
        return "cover", {"on": "open_cover", "off": "close_cover", "toggle": "toggle"}[action]
    if domain == "lock":
        return "lock", {"on": "lock", "off": "unlock", "toggle": "lock"}[action]
    if domain in ("button", "input_button"):
        return domain, "press"
    if domain in ACTIVATABLE:
        return domain, "turn_on"
    if domain == "vacuum":
        return "vacuum", {"on": "start", "off": "return_to_base", "toggle": "start_pause"}[action]
    return "homeassistant", {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}[action]


def level_call(entity_id: str, value: float) -> tuple[str, str, dict[str, Any]] | None:
    domain = entity_id.split(".")[0]
    if domain == "light":
        return "light", "turn_on", {"brightness_pct": max(0, min(100, value))}
    if domain == "climate":
        return "climate", "set_temperature", {"temperature": value}
    if domain == "media_player":
        return "media_player", "volume_set", {"volume_level": max(0, min(100, value)) / 100}
    if domain == "cover":
        return "cover", "set_cover_position", {"position": int(max(0, min(100, value)))}
    if domain == "fan":
        return "fan", "set_percentage", {"percentage": int(max(0, min(100, value)))}
    if domain in ("number", "input_number"):
        return domain, "set_value", {"value": value}
    if domain == "humidifier":
        return "humidifier", "set_humidity", {"humidity": int(max(0, min(100, value)))}
    return None


_STATES = {"on": "увімкнено", "off": "вимкнено", "open": "відкрито", "closed": "закрито",
           "locked": "замкнено", "unlocked": "відімкнено", "unavailable": "недоступний",
           "unknown": "невідомо",
           "heat": "обігрів", "cool": "охолодження", "auto": "авто", "dry": "осушення",
           "fan_only": "вентиляція", "heat_cool": "авто", "playing": "грає", "paused": "пауза",
           "idle": "очікує", "home": "вдома", "not_home": "не вдома",
           "docked": "на базі", "cleaning": "прибирає", "returning": "повертається на базу",
           "error": "помилка"}

_WEATHER = {"clear-night": "ясно", "cloudy": "хмарно", "exceptional": "незвичайна погода",
            "fog": "туман", "hail": "град", "lightning": "гроза", "lightning-rainy": "гроза з дощем",
            "partlycloudy": "мінлива хмарність", "pouring": "злива", "rainy": "дощ", "snowy": "сніг",
            "snowy-rainy": "мокрий сніг", "sunny": "сонячно", "windy": "вітряно",
            "windy-variant": "вітряно з хмарами"}

# binary_sensor: what «on» / «off» means for a device class
_BINARY = {"power": ("є", "немає"), "plug": ("підключено", "відключено"),
           "problem": ("є проблема", "все гаразд"), "safety": ("небезпека", "безпечно"),
           "moisture": ("волого", "сухо"), "motion": ("є рух", "руху немає"),
           "occupancy": ("хтось є", "нікого"), "presence": ("вдома", "немає"),
           "door": ("відчинені", "зачинені"), "window": ("відчинене", "зачинене"),
           "opening": ("відчинено", "зачинено"), "battery": ("низький заряд", "заряд у нормі"),
           "battery_charging": ("заряджається", "не заряджається"),
           "connectivity": ("на зв'язку", "не на зв'язку"), "running": ("працює", "не працює")}

_MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня",
           "вересня", "жовтня", "листопада", "грудня"]


def spoken_time(value: str) -> str | None:
    """ISO timestamp -> «сьогодні о 9:30» / «завтра о 15:00» / «3 грудня о 8:00»."""
    when = dt_util.parse_datetime(value) if isinstance(value, str) else None
    if when is None:
        return None
    when = dt_util.as_local(when)
    days = (when.date() - dt_util.now().date()).days
    day = {0: "сьогодні", 1: "завтра", -1: "вчора"}.get(days, f"{when.day} {_MONTHS[when.month - 1]}")
    return f"{day} о {when.hour}:{when.minute:02d}"


def describe_state(name: str, state: State | None) -> str:
    if state is None:
        return f"{name}: невідомо."
    if state.domain == "weather":
        a = state.attributes
        parts = [_WEATHER.get(state.state, state.state)]
        if a.get("temperature") is not None:
            parts.append(f"{a['temperature']:g}°" if isinstance(a["temperature"], (int, float))
                         else f"{a['temperature']}°")
        if a.get("humidity") is not None:
            parts.append(f"вологість {a['humidity']}%")
        if a.get("wind_speed") is not None:
            parts.append(f"вітер {a['wind_speed']} {a.get('wind_speed_unit', '')}".strip())
        return f"Погода: {', '.join(parts)}."
    if state.domain == "binary_sensor" and state.state in ("on", "off"):
        yes, no = _BINARY.get(state.attributes.get("device_class") or "", ("так", "ні"))
        return f"{name}: {yes if state.state == 'on' else no}."
    if state.attributes.get("device_class") == "timestamp" or state.domain in ("input_datetime",):
        spoken = spoken_time(state.state)
        if spoken:
            return f"{name}: {spoken}."
    value = _STATES.get(state.state, state.state)
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    text = f"{name}: {value}{' ' + unit if unit else ''}"
    if state.domain == "climate":
        cur = state.attributes.get("current_temperature")
        target = state.attributes.get("temperature")
        if target is not None:
            text += f", задано {target}°"
        if cur is not None:
            text += f", зараз {cur}°"
    elif state.domain == "humidifier":
        if state.attributes.get("humidity") is not None:
            text += f", задано {state.attributes['humidity']}%"
        if state.attributes.get("current_humidity") is not None:
            text += f", зараз {state.attributes['current_humidity']}%"
    elif state.domain == "light" and state.state == "on" and state.attributes.get("brightness"):
        text += f", яскравість {round(state.attributes['brightness'] / 2.55)}%"
    return text + "."
