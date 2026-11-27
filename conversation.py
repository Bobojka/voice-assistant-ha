"""Conversation agent: scripts labelled for voice + the voice-assistant service.

Every phrase: collect the labelled scripts -> catalog -> POST /v1/match ->
run the matched scripts -> speak their answer. Nothing matched or the service
is down -> the fallback agent (Home Assistant's built-in one by default).

Dialog: the agent asks back when a required field is missing or the model is
not sure, and a script can ask its own question by returning
{"speech": "...", "continue_conversation": true, "context": "..."}.
The satellite then listens again without the wake word.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers import service as service_helper
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import ulid

from .catalog import Command, build_catalog, describe_slots, yes_no
from .client import ServiceError, VoiceServiceClient
from .const import (
    CONF_ASSISTANT_NAME,
    CONF_FALLBACK_AGENT,
    CONF_LABEL,
    CONF_MIN_CONFIDENCE,
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_FALLBACK_AGENT,
    DEFAULT_LABEL,
    DEFAULT_MIN_CONFIDENCE,
    DOMAIN,
    NO_FALLBACK,
    PENDING_TTL_S,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class Pending:
    """What the assistant asked and is waiting for."""

    kind: Literal["confirm", "slot", "context"]
    question: str
    matches: list[dict[str, Any]] = field(default_factory=list)  # first one is the current
    slot: str | None = None
    context: str = ""
    expires: float = 0.0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([VoiceAgent(hass, entry)])


class VoiceAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._pending: dict[str, Pending] = {}

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    @property
    def _client(self) -> VoiceServiceClient:
        return self.entry.runtime_data

    def _opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, self.entry.data.get(key, default))

    # --- catalog -----------------------------------------------------------------

    async def _catalog(self) -> tuple[list[dict[str, Any]], dict[str, Command]]:
        label_name = self._opt(CONF_LABEL, DEFAULT_LABEL)
        label = next((lb for lb in lr.async_get(self.hass).async_list_labels()
                      if lb.name.casefold() == label_name.casefold()), None)
        if label is None:
            return [], {}
        entries = er.async_entries_for_label(er.async_get(self.hass), label.label_id)
        keys = {e.unique_id for e in entries if e.domain == "script" and e.unique_id}
        if not keys:
            return [], {}
        descriptions = (await service_helper.async_get_all_descriptions(self.hass)).get("script", {})
        return build_catalog({k: descriptions[k] for k in keys if k in descriptions})

    # --- entry point -------------------------------------------------------------

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        conv_id = user_input.conversation_id or ulid.ulid_now()
        text = user_input.text.strip()
        now = time.monotonic()
        self._pending = {k: p for k, p in self._pending.items() if p.expires > now}
        pending = self._pending.pop(conv_id, None)

        try:
            intents, commands = await self._catalog()
        except Exception:  # noqa: BLE001 - never break the pipeline on our side
            _LOGGER.exception("building the catalog failed")
            intents, commands = [], {}

        reply = _Reply(user_input.language)

        # 1. A yes / no to our own confirmation question.
        if pending and pending.kind == "confirm":
            answer = yes_no(text)
            if answer is True:
                await self._run_queue(user_input, conv_id, pending.matches, commands, reply,
                                      confirmed=True)
                return reply.result(conv_id)
            if answer is False:
                reply.say("Добре, скасовано.")
                return reply.result(conv_id)
            pending = None  # something else: a new command

        # 2. The value of a missing field.
        if pending and pending.kind == "slot" and pending.matches:
            current = pending.matches[0]
            command = commands.get(current["intent"])
            intent_spec = next((i for i in intents if i["name"] == current["intent"]), None)
            if command and intent_spec:
                ctx = (f"Асистент спитав: «{pending.question}» щодо команди «{command.title}»; "
                       f"людина відповідає на це питання. Уже відомо: "
                       f"{describe_slots(command, current['slots']) or 'нічого'}.")
                try:
                    found = await self._client.match(text, [intent_spec], ctx, conv_id)
                except ServiceError as err:
                    _LOGGER.warning("voice service: %s", err)
                    found = []
                if found:
                    merged = dict(current["slots"])
                    merged.update({k: v for k, v in found[0]["slots"].items() if v is not None})
                    current = {**current, "slots": merged, "confidence": 1.0}
                    await self._run_queue(user_input, conv_id, [current, *pending.matches[1:]],
                                          commands, reply)
                    return reply.result(conv_id)
            pending = None

        # 3. A new phrase (or an answer to a script's own question, via context).
        context = pending.context if pending and pending.kind == "context" else ""
        if pending and pending.kind == "context" and pending.question:
            context = f"Асистент щойно сказав: «{pending.question}». {context}".strip()

        matches: list[dict[str, Any]] = []
        if intents:
            try:
                matches = await self._client.match(text, intents, context, conv_id)
            except ServiceError as err:
                _LOGGER.warning("voice service unavailable, using the fallback agent: %s", err)
                return await self._fallback(user_input, conv_id, reply)
        matches = [m for m in matches if m.get("intent") in commands]
        if not matches:
            return await self._fallback(user_input, conv_id, reply)

        await self._run_queue(user_input, conv_id, matches, commands, reply)
        return reply.result(conv_id)

    # --- running commands ----------------------------------------------------------

    async def _run_queue(
        self,
        user_input: conversation.ConversationInput,
        conv_id: str,
        queue: list[dict[str, Any]],
        commands: dict[str, Command],
        reply: _Reply,
        confirmed: bool = False,
    ) -> None:
        min_conf = float(self._opt(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE))
        for i, match in enumerate(queue):
            command = commands.get(match["intent"])
            if command is None:
                continue
            slots = {k: v for k, v in (match.get("slots") or {}).items() if v is not None}

            missing = [f for f in command.required if f not in slots]
            if missing:
                question = f"{command.title}: уточніть, {command.fields.get(missing[0], missing[0]).lower()}?"
                self._ask(conv_id, Pending("slot", question, [match, *queue[i + 1:]], missing[0]))
                reply.ask(question)
                return

            if not confirmed and float(match.get("confidence", 1.0)) < min_conf:
                details = describe_slots(command, slots)
                question = f"Виконати «{command.title}»{': ' + details if details else ''}?"
                self._ask(conv_id, Pending("confirm", question, queue[i:]))
                reply.ask(question)
                return

            response = await self._call_script(user_input, command, slots, reply)
            if response is None:
                continue
            speech = str(response.get("speech") or "").strip()
            reply.say(speech or f"Готово: {command.title}.")
            if response.get("continue_conversation"):
                self._ask(conv_id, Pending("context", speech, context=str(response.get("context") or "")))
                reply.keep_listening()
                return

    async def _call_script(
        self, user_input: conversation.ConversationInput, command: Command,
        slots: dict[str, Any], reply: _Reply,
    ) -> dict[str, Any] | None:
        try:
            result = await self.hass.services.async_call(
                "script", command.script, slots, blocking=True,
                context=user_input.context, return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("script %s failed: %s", command.script, err)
            reply.say(f"Не вдалося виконати «{command.title}»: {err}")
            return None
        return result if isinstance(result, dict) else {}

    def _ask(self, conv_id: str, pending: Pending) -> None:
        pending.expires = time.monotonic() + PENDING_TTL_S
        self._pending[conv_id] = pending

    async def _fallback(
        self, user_input: conversation.ConversationInput, conv_id: str, reply: _Reply
    ) -> conversation.ConversationResult:
        agent_id = self._opt(CONF_FALLBACK_AGENT, DEFAULT_FALLBACK_AGENT)
        if agent_id and agent_id != NO_FALLBACK and agent_id != self.entity_id:
            try:
                return await conversation.async_converse(
                    self.hass, user_input.text, conv_id, user_input.context,
                    language=user_input.language, agent_id=agent_id,
                    device_id=user_input.device_id, satellite_id=user_input.satellite_id,
                )
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.warning("fallback agent %s failed: %s", agent_id, err)
        name = self._opt(CONF_ASSISTANT_NAME, DEFAULT_ASSISTANT_NAME)
        reply.error(f"{name} не зрозумів команду.")
        return reply.result(conv_id)


class _Reply:
    """Collects speech for one turn."""

    def __init__(self, language: str) -> None:
        self.response = intent.IntentResponse(language=language)
        self._parts: list[str] = []
        self._continue = False
        self._error = False

    def say(self, text: str) -> None:
        if text:
            self._parts.append(text)

    def ask(self, question: str) -> None:
        self.say(question)
        self._continue = True

    def keep_listening(self) -> None:
        self._continue = True

    def error(self, text: str) -> None:
        self._error = True
        self.say(text)

    def result(self, conv_id: str) -> conversation.ConversationResult:
        speech = " ".join(self._parts)
        if self._error:
            self.response.async_set_error(intent.IntentResponseErrorCode.NO_INTENT_MATCH, speech)
        else:
            self.response.async_set_speech(speech or "Готово.")
        return conversation.ConversationResult(
            response=self.response, conversation_id=conv_id,
            continue_conversation=self._continue,
        )
