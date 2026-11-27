"""Speech-to-text through the voice-assistant service, with this home's words as hints.

Same service as the Wyoming STT, but here every request carries the names of
rooms, exposed devices and voice scripts, so Whisper hears «кондиціонер»
instead of something close to it.
"""

from __future__ import annotations

import io
import logging
import wave
from collections.abc import AsyncIterable

from homeassistant.components import stt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import ulid

from .client import ServiceError, VoiceServiceClient
from .const import CONF_DEVICES, CONF_LABEL, DEFAULT_LABEL
from .vocabulary import build_vocabulary

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16000
MAX_AUDIO_S = 30


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([VoiceSTT(entry)])


def to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class VoiceSTT(stt.SpeechToTextEntity):
    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_stt"
        self._attr_name = f"{entry.title} STT"

    @property
    def supported_languages(self) -> list[str]:
        return ["uk", "en", "ru"]

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        return [stt.AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        limit = MAX_AUDIO_S * SAMPLE_RATE * 2
        pcm = bytearray()
        async for chunk in stream:
            if len(pcm) < limit:
                pcm.extend(chunk[: limit - len(pcm)])

        opts = self.entry.options
        vocabulary = build_vocabulary(
            self.hass, opts.get(CONF_LABEL, DEFAULT_LABEL), opts.get(CONF_DEVICES, True))
        client: VoiceServiceClient = self.entry.runtime_data
        request_id = ulid.ulid_now()
        try:
            text = await client.transcribe(
                to_wav(bytes(pcm)), metadata.language.split("-")[0], vocabulary, request_id)
        except ServiceError as err:
            _LOGGER.warning("STT failed (%s): %s", request_id, err)
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
        _LOGGER.debug("STT %s: %r (hints: %s)", request_id, text, ", ".join(vocabulary))
        return stt.SpeechResult(text, stt.SpeechResultState.SUCCESS)
