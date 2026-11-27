"""How loud the request was, to answer as loud: shouted in a noisy kitchen -> loud,
whispered at night -> quiet.

STT measures every request (voice and background noise, dBFS) and keeps the last
one; the agent turns it into the volume of the satellite's media player before
the answer is spoken.
"""

from __future__ import annotations

import math
import time
from array import array
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN

SIGNAL_LEVEL = f"{DOMAIN}_audio_level"
FRAME = 480  # 30 ms at 16 kHz
FLOOR_DB = -90.0
FRESH_S = 30  # a level older than this belongs to another request


@dataclass
class Level:
    speech_db: float  # the loud part of the request: the voice
    noise_db: float  # the quiet part: what is around
    at: float


def measure(pcm: bytes) -> tuple[float, float]:
    """(voice, noise) in dBFS for 16-bit mono PCM: 90th / 10th percentile of 30 ms frames."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    frames: list[float] = []
    for i in range(0, len(samples) - FRAME + 1, FRAME):
        chunk = samples[i:i + FRAME]
        rms = math.sqrt(sum(s * s for s in chunk) / FRAME)
        frames.append(20 * math.log10(rms / 32768) if rms > 0 else FLOOR_DB)
    if not frames:
        return FLOOR_DB, FLOOR_DB
    frames.sort()

    def pct(p: float) -> float:
        return round(max(FLOOR_DB, frames[min(len(frames) - 1, int(p * len(frames)))]), 1)

    return pct(0.9), pct(0.1)


def remember(hass: HomeAssistant, pcm: bytes) -> Level:
    speech, noise = measure(pcm)
    level = Level(speech, noise, time.monotonic())
    hass.data.setdefault(DOMAIN, {})["level"] = level
    async_dispatcher_send(hass, SIGNAL_LEVEL, level)
    return level


def recent(hass: HomeAssistant) -> Level | None:
    level = hass.data.get(DOMAIN, {}).get("level")
    if isinstance(level, Level) and time.monotonic() - level.at < FRESH_S:
        return level
    return None


def volume_for(level: Level, quiet_db: float, loud_db: float, vmin: float, vmax: float) -> float:
    """Linear between quiet_db -> vmin and loud_db -> vmax. A noisy room counts as
    loud too: noise 10 dB below the loud mark already asks for the loudest answer."""
    loudness = max(level.speech_db, level.noise_db + 10)
    if loud_db <= quiet_db:
        return vmax
    x = (loudness - quiet_db) / (loud_db - quiet_db)
    return round(vmin + (vmax - vmin) * min(1.0, max(0.0, x)), 2)
