"""Words that help speech recognition: what this home actually has.

Whisper takes a short prompt (the service trims it to ~400 chars), so the order
matters: command verbs, rooms, devices, then voice scripts.
"""

from __future__ import annotations

from homeassistant.const import ATTR_FRIENDLY_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from . import devices as dev

VERBS = ["увімкни", "вимкни", "постав", "зроби", "температура", "яскравість", "гучність"]


def build_vocabulary(hass: HomeAssistant, label_name: str, with_devices: bool) -> list[str]:
    scripts: list[str] = []
    labelled: set[str] = set()
    label = next((lb for lb in lr.async_get(hass).async_list_labels()
                  if lb.name.casefold() == label_name.casefold()), None)
    if label is not None:
        for entry in er.async_entries_for_label(er.async_get(hass), label.label_id):
            if entry.domain != "script":
                continue
            labelled.add(entry.entity_id)
            state = hass.states.get(entry.entity_id)
            name = state.attributes.get(ATTR_FRIENDLY_NAME) if state else None
            if name:
                scripts.append(str(name))

    areas: list[str] = []
    devices: list[str] = []
    if with_devices:
        targets = dev.exposed_targets(hass, skip=labelled)
        areas = sorted({t.area for t in targets if t.area})
        devices = [t.name for t in targets]

    out: dict[str, None] = {}
    for word in (*VERBS, *areas, *devices, *scripts):
        word = " ".join(str(word).replace(",", " ").split())
        if word and word.casefold() not in {w.casefold() for w in out}:
            out[word] = None
    return list(out)
