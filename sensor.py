"""Level of the last voice request: to see what «quiet» and «loud» are in this
room and set the volume limits accordingly."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .loudness import SIGNAL_LEVEL, Level


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([LevelSensor(entry, "speech"), LevelSensor(entry, "noise")])


class LevelSensor(SensorEntity):
    _attr_native_unit_of_measurement = "dBFS"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, kind: str) -> None:
        self._kind = kind
        self._attr_unique_id = f"{entry.entry_id}_{kind}_level"
        self._attr_name = f"{entry.title} {'рівень голосу' if kind == 'speech' else 'рівень шуму'}"
        self._attr_icon = "mdi:microphone" if kind == "speech" else "mdi:waveform"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_LEVEL, self._update))

    @callback
    def _update(self, level: Level) -> None:
        self._attr_native_value = level.speech_db if self._kind == "speech" else level.noise_db
        self.async_write_ha_state()
