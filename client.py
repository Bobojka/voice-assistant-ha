"""HTTP client for the voice-assistant service (transcribe-nlp)."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT_S


class ServiceError(Exception):
    """The service could not answer (network, auth, provider)."""


class VoiceServiceClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def health(self) -> None:
        try:
            async with self._session.get(
                f"{self._url}/health", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    raise ServiceError(f"HTTP {resp.status}")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ServiceError(str(err)) from err

    async def transcribe(
        self, wav: bytes, language: str, vocabulary: list[str], request_id: str = ""
    ) -> str:
        """POST /v1/transcribe -> recognised text ('' when there was no speech)."""
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
        form.add_field("language", language)
        form.add_field("vocabulary", ",".join(vocabulary))
        form.add_field("request_id", request_id)
        try:
            async with self._session.post(
                f"{self._url}/v1/transcribe",
                data=form,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
                    raise ServiceError(
                        f"HTTP {resp.status} {err.get('code', '')}: {err.get('message', '')}".strip()
                    )
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ServiceError(str(err)) from err
        return str(data.get("text") or "").strip() if data.get("ok") else ""

    async def match(
        self, text: str, intents: list[dict[str, Any]], context: str = "", request_id: str = ""
    ) -> list[dict[str, Any]]:
        """POST /v1/match -> list of {intent, slots, confidence, warnings}."""
        body = {"text": text, "intents": intents, "context": context, "request_id": request_id}
        try:
            async with self._session.post(
                f"{self._url}/v1/match",
                json=body,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
                    raise ServiceError(
                        f"HTTP {resp.status} {err.get('code', '')}: {err.get('message', '')}".strip()
                    )
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ServiceError(str(err)) from err
        return list(data.get("matches", []))
