"""What each person (or satellite) usually means.

«Вимкни підсвітку» without a room: the room this person usually picks for
«підсвітка», else their own room, else the satellite's room, else ask.
Every time a room is named or picked from the question, it is counted; a
correction («ні, на кухні») counts against the wrong guess.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.storage import Store

from .const import DOMAIN

MIN_COUNT = 2  # picked at least this many times...
MIN_SHARE = 0.7  # ...and this share of all picks for the key
LINE_RE = re.compile(r"^\s*(person\.[a-z0-9_]+)\s*[=:]\s*(.+?)\s*$")


class HomeAreasError(ValueError):
    """A line is not «person.<name> = <room>»."""


def parse_home_areas(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = LINE_RE.match(line)
        if not m:
            raise HomeAreasError(line.strip())
        out[m.group(1)] = m.group(2)
    return out


def unknown_areas(hass: HomeAssistant, areas: dict[str, str]) -> list[str]:
    names = {a.name.casefold() for a in ar.async_get(hass).async_list_areas()}
    return [a for a in areas.values() if a.casefold() not in names]


class Habits:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.habits")
        self.counts: dict[str, dict[str, dict[str, float]]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.counts = data.get("counts", {})

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"counts": self.counts}, 10)

    def choose(self, who: str, key: str, areas: list[str], default: str | None) -> str | None:
        counts = {a: n for a, n in self.counts.get(who, {}).get(key, {}).items() if a in areas and n > 0}
        if counts:
            top = max(counts, key=lambda a: counts[a])
            total = sum(counts.values())
            if counts[top] >= MIN_COUNT and counts[top] / total >= MIN_SHARE:
                return top
        if default and default in areas:
            return default
        return None

    def record(self, who: str, key: str, area: str, weight: float = 1.0) -> None:
        if not (who and key and area):
            return
        by_key = self.counts.setdefault(who, {}).setdefault(key, {})
        by_key[area] = max(0.0, by_key.get(area, 0.0) + weight)
        self._save()

    def reset(self) -> None:
        self.counts = {}
        self._save()
