"""Telegram as a source: text and voice messages to the same agent, text replies.

Talks to tg-proxy (Bobojka/tg-proxy) or Telegram itself with getUpdates, so
Home Assistant needs no open port. Only people listed in the options are
heard; the command runs as that person. Nothing recognised -> no answer, so
other services on the same bot can take the message.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant

from .client import ServiceError, VoiceServiceClient
from .const import CONF_DEVICES, CONF_LABEL, DEFAULT_LABEL
from .vocabulary import build_vocabulary

_LOGGER = logging.getLogger(__name__)

POLL_TIMEOUT_S = 50
RETRY_S = 10
MAX_VOICE_BYTES = 20 * 1024 * 1024
LINE_RE = re.compile(r"^\s*(-?\d+)\s*[=:]\s*(person\.[a-z0-9_]+)\s*$")


class UsersError(ValueError):
    """A line of the users list is not «<telegram id> = person.<name>»."""


def parse_users(text: str) -> dict[int, str]:
    users: dict[int, str] = {}
    for line in (text or "").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = LINE_RE.match(line)
        if not m:
            raise UsersError(line.strip())
        users[int(m.group(1))] = m.group(2)
    return users


async def to_wav(hass: HomeAssistant, data: bytes) -> bytes:
    """Any audio Telegram sends (ogg/opus voice, mp3...) -> 16 kHz mono WAV."""
    from homeassistant.components.ffmpeg import get_ffmpeg_manager  # noqa: PLC0415

    proc = await asyncio.create_subprocess_exec(
        get_ffmpeg_manager(hass).binary, "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-f", "wav", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(data), timeout=30)
    if proc.returncode != 0 or not out:
        raise ServiceError(f"ffmpeg: {err.decode(errors='replace').strip()[:200]}")
    return out


class TelegramBridge:
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: VoiceServiceClient,
        session: aiohttp.ClientSession, api_url: str, token: str, users: dict[int, str],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.session = session
        self.base = api_url.rstrip("/")
        self.token = token
        self.users = users

    def start(self) -> None:
        self.entry.async_create_background_task(self.hass, self._run(), "voice_assistant_ha telegram")

    async def api(self, method: str, params: dict[str, Any], timeout: float = 30) -> Any:
        try:
            async with self.session.post(
                f"{self.base}/bot{self.token}/{method}", json=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ServiceError(f"{method}: {err}".replace(self.token, "<token>")) from err
        if not isinstance(data, dict) or not data.get("ok"):
            desc = data.get("description") if isinstance(data, dict) else data
            raise ServiceError(f"{method}: {desc}")
        return data.get("result")

    async def _run(self) -> None:
        offset: int | None = None
        while True:
            try:
                updates = await self.api(
                    "getUpdates",
                    {"offset": offset, "timeout": POLL_TIMEOUT_S, "allowed_updates": ["message"]},
                    timeout=POLL_TIMEOUT_S + 15,
                )
            except ServiceError as err:
                _LOGGER.warning("telegram: %s; retrying in %ss", err, RETRY_S)
                await asyncio.sleep(RETRY_S)
                continue
            for update in updates or []:
                offset = int(update["update_id"]) + 1
                try:
                    await self.handle(update)
                except Exception:  # noqa: BLE001 - one bad message must not stop the loop
                    _LOGGER.exception("telegram: handling update %s", update.get("update_id"))

    async def handle(self, update: dict[str, Any]) -> None:
        msg = update.get("message") or {}
        sender = (msg.get("from") or {}).get("id")
        person = self.users.get(sender) if sender is not None else None
        if not person:
            return  # not one of ours: someone else's service may answer
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            return  # bot commands belong to other services
        audio = msg.get("voice") or msg.get("audio")
        heard = False
        if not text and audio:
            text = await self._transcribe(chat_id, audio)
            heard = True
        if not text:
            return

        agent = self.hass.data.get("voice_assistant_ha", {}).get(self.entry.entry_id)
        if agent is None:
            return
        state = self.hass.states.get(person)
        user_id = state.attributes.get("user_id") if state else None
        await self._typing(chat_id)
        result = await agent.process(
            conversation.ConversationInput(
                text=text, context=Context(user_id=user_id),
                conversation_id=f"tg-{chat_id}-{sender}", device_id=None, satellite_id=None,
                language=self.hass.config.language or "uk", agent_id=agent.entity_id,
            ),
            silent=True,
        )
        speech = (result.response.speech.get("plain") or {}).get("speech", "").strip()
        if not speech:
            _LOGGER.debug("telegram: nothing recognised in %r", text)
            return
        await self.api("sendMessage", {
            "chat_id": chat_id,
            "text": f"🎙 {text}\n\n{speech}" if heard else speech,
            "reply_parameters": {"message_id": msg["message_id"], "allow_sending_without_reply": True},
        })

    async def _typing(self, chat_id: int) -> None:
        try:
            await self.api("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=5)
        except ServiceError:
            pass

    async def _transcribe(self, chat_id: int, audio: dict[str, Any]) -> str:
        if int(audio.get("file_size") or 0) > MAX_VOICE_BYTES:
            return ""
        await self._typing(chat_id)
        info = await self.api("getFile", {"file_id": audio["file_id"]})
        url = f"{self.base}/file/bot{self.token}/{info['file_path']}"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise ServiceError(f"file download: HTTP {resp.status}")
                data = await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ServiceError(f"file download: {err}".replace(self.token, "<token>")) from err
        wav = await to_wav(self.hass, data)
        opts = self.entry.options
        vocabulary = build_vocabulary(
            self.hass, opts.get(CONF_LABEL, DEFAULT_LABEL), opts.get(CONF_DEVICES, True))
        lang = (self.hass.config.language or "uk").split("-")[0]
        return await self.client.transcribe(wav, lang, vocabulary, f"tg-{chat_id}")
