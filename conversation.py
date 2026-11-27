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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers import service as service_helper
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import ulid

from . import devices as dev
from . import loudness
from .catalog import Command, build_catalog, describe_slots, yes_no
from .client import ServiceError, VoiceServiceClient
from .const import (
    CONF_ASSISTANT_NAME,
    CONF_DEVICES,
    CONF_FALLBACK_AGENT,
    CONF_FOLLOW_UP,
    CONF_LABEL,
    CONF_LOUD_DB,
    CONF_MIN_CONFIDENCE,
    CONF_QUIET_DB,
    CONF_VOLUME_ADAPT,
    CONF_VOLUME_MAX,
    CONF_VOLUME_MIN,
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_FALLBACK_AGENT,
    DEFAULT_FOLLOW_UP,
    DEFAULT_LABEL,
    DEFAULT_LOUD_DB,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_QUIET_DB,
    DEFAULT_VOLUME_ADAPT,
    DEFAULT_VOLUME_MAX,
    DEFAULT_VOLUME_MIN,
    DOMAIN,
    FOLLOW_UP_SILENCE,
    FOLLOW_UP_WINDOW_S,
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
    options: list[str] = field(default_factory=list)  # answers a script offers
    follow: bool = False  # not a question: listening on after a done command


THANKS = {"дякую", "спасибі", "дякс", "дякую тобі", "дуже дякую"}
STOP = THANKS | {"все", "усе", "стоп", "ні", "нічого", "досить", "не треба", "відбій", "забудь"}


def _norm(text: str) -> str:
    return " ".join("".join(c for c in text.lower() if c.isalnum() or c.isspace()).split())


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    agent = VoiceAgent(hass, entry)
    # The Telegram source calls the agent directly (silent when nothing matched).
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = agent
    async_add_entities([agent])


class VoiceAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._pending: dict[str, Pending] = {}
        self._targets: list[dev.Target] = []
        self._intents: list[dict[str, Any]] = []
        self._turn_ok = False  # this turn ran a command to the end

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
        """Scripts with the voice label + (optionally) universal device commands."""
        intents: list[dict[str, Any]] = []
        commands: dict[str, Command] = {}
        labelled: set[str] = set()

        label_name = self._opt(CONF_LABEL, DEFAULT_LABEL)
        label = next((lb for lb in lr.async_get(self.hass).async_list_labels()
                      if lb.name.casefold() == label_name.casefold()), None)
        if label is not None:
            entries = er.async_entries_for_label(er.async_get(self.hass), label.label_id)
            scripts = {e.unique_id: e.entity_id for e in entries
                       if e.domain == "script" and e.unique_id}
            labelled = set(scripts.values())
            if scripts:
                # Scripts register their own descriptions: no need to load every integration's.
                descriptions = {k: d for k in scripts if (
                    d := service_helper.async_get_cached_service_description(self.hass, "script", k))}
                if len(descriptions) < len(scripts):
                    every = (await service_helper.async_get_all_descriptions(self.hass)).get("script", {})
                    descriptions = {k: every[k] for k in scripts if k in every}
                intents, commands = build_catalog(descriptions)

        self._targets = []
        if self._opt(CONF_DEVICES, True):
            self._targets = dev.exposed_targets(self.hass, skip=labelled)
            areas = sorted({t.area for t in self._targets if t.area})
            for spec in dev.build_device_intents(self._targets, areas):
                intents.append(spec)
                commands[spec["name"]] = Command(
                    script=spec["name"], title=dev.TITLES[spec["name"]],
                    fields={"device": "пристрій", "area": "кімната", "action": "дія",
                            "value": "значення", "name": "назва", "kind": "група"},
                    required=[sl["name"] for sl in spec["slots"] if sl.get("required")])
        return intents, commands

    # --- entry point -------------------------------------------------------------

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        result = await self.process(user_input)
        await self._adapt_volume(user_input)
        from_satellite = bool(user_input.device_id or user_input.satellite_id)
        if (from_satellite and self._turn_ok and not result.continue_conversation
                and self._opt(CONF_FOLLOW_UP, DEFAULT_FOLLOW_UP) and result.conversation_id):
            # Done: keep listening a few seconds without the wake word for «і ще ...».
            self._ask(result.conversation_id, Pending(
                "context", "", follow=True,
                context=(f"Попередня команда людини: «{user_input.text.strip()}». Нова фраза "
                         "може її доповнювати («і ще ...», «а тепер на 22»).")))
            result.continue_conversation = True
        if from_satellite and result.continue_conversation:
            self.hass.data.setdefault(DOMAIN, {})["follow_up_until"] = (
                time.monotonic() + FOLLOW_UP_WINDOW_S)
        return result

    async def _adapt_volume(self, user_input: conversation.ConversationInput) -> None:
        """Answer as loud as the request was: volume of the satellite's media player."""
        if not self._opt(CONF_VOLUME_ADAPT, DEFAULT_VOLUME_ADAPT):
            return
        level = loudness.recent(self.hass)
        if level is None:
            return
        reg = er.async_get(self.hass)
        device_id = user_input.device_id
        if not device_id and user_input.satellite_id:
            sat = reg.async_get(user_input.satellite_id)
            device_id = sat.device_id if sat else None
        if not device_id or dr.async_get(self.hass).async_get(device_id) is None:
            return
        players = [e.entity_id for e in er.async_entries_for_device(reg, device_id)
                   if e.domain == "media_player" and not e.disabled_by]
        if not players:
            return
        volume = loudness.volume_for(
            level,
            float(self._opt(CONF_QUIET_DB, DEFAULT_QUIET_DB)),
            float(self._opt(CONF_LOUD_DB, DEFAULT_LOUD_DB)),
            float(self._opt(CONF_VOLUME_MIN, DEFAULT_VOLUME_MIN)),
            float(self._opt(CONF_VOLUME_MAX, DEFAULT_VOLUME_MAX)),
        )
        _LOGGER.debug("volume %s for voice %s / noise %s dBFS -> %s",
                      volume, level.speech_db, level.noise_db, players)
        try:
            await self.hass.services.async_call(
                "media_player", "volume_set",
                {"entity_id": players, "volume_level": volume}, blocking=True,
                context=user_input.context)
        except HomeAssistantError as err:
            _LOGGER.warning("volume_set failed: %s", err)

    async def process(
        self, user_input: conversation.ConversationInput, silent: bool = False
    ) -> conversation.ConversationResult:
        """silent: nothing matched -> empty answer, no fallback agent (Telegram:
        the message may be for another service on the same bot)."""
        conv_id = user_input.conversation_id or ulid.ulid_now()
        text = user_input.text.strip()
        now = time.monotonic()
        self._pending = {k: p for k, p in self._pending.items() if p.expires > now}
        pending = self._pending.pop(conv_id, None)
        self._turn_ok = False
        follow = bool(pending and pending.follow)
        quiet = silent or follow  # nothing recognised -> just stop listening

        reply = _Reply(user_input.language)
        if not text or text == FOLLOW_UP_SILENCE:
            reply.error("")  # silence: end without a word
            return reply.result(conv_id)
        if follow and _norm(text) in STOP:
            if _norm(text) in THANKS:
                reply.say("Будь ласка.")
            else:
                reply.error("")
            return reply.result(conv_id)

        try:
            intents, commands = await self._catalog()
        except Exception:  # noqa: BLE001 - never break the pipeline on our side
            _LOGGER.exception("building the catalog failed")
            intents, commands = [], {}
        self._intents = intents

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
                return await self._fallback(user_input, conv_id, reply, quiet)
        _LOGGER.debug("catalog: %d commands, %d devices; matches: %s",
                      len(intents), len(self._targets), matches)
        matches = [m for m in matches if m.get("intent") in commands]
        if not matches:
            return await self._fallback(user_input, conv_id, reply, quiet)

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

            # Asking the state changes nothing: no need to confirm it.
            if (not confirmed and command.script != dev.STATE
                    and float(match.get("confidence", 1.0)) < min_conf):
                details = describe_slots(command, slots)
                question = f"Виконати «{command.title}»{': ' + details if details else ''}?"
                self._ask(conv_id, Pending("confirm", question, queue[i:]))
                reply.ask(question)
                return

            if command.script.startswith(dev.PREFIX):
                question = await self._run_builtin(user_input, command, slots, reply)
                if question:
                    self._ask(conv_id, Pending("slot", question, [match, *queue[i + 1:]], "device"))
                    reply.ask(question)
                    return
                continue

            response = await self._call_script(user_input, command, slots, reply)
            if response is None:
                continue
            speech = str(response.get("speech") or "").strip()
            reply.say(speech or f"Готово: {command.title}.")
            if response.get("continue_conversation"):
                options = [str(o) for o in (response.get("options") or []) if str(o).strip()]
                self._ask(conv_id, Pending("context", speech, context=str(response.get("context") or ""),
                                           options=options[:10]))
                reply.keep_listening()
                return
        self._turn_ok = True

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

    async def _run_builtin(
        self, user_input: conversation.ConversationInput, command: Command,
        slots: dict[str, Any], reply: _Reply,
    ) -> str | None:
        """Runs a universal device command; returns a question if the target is unclear."""
        name = command.script
        if name == dev.ACTIVATE:
            targets = dev.resolve(self._targets, {"device": slots.get("name")}, dev.ACTIVATABLE)
        else:
            targets = dev.resolve(self._targets, slots)
        if not targets:
            return "Який саме пристрій?"
        services = self.hass.services
        done: list[str] = []
        try:
            if name == dev.STATE:
                reply.say(" ".join(dev.describe_state(t.name, self.hass.states.get(t.entity_id))
                                   for t in targets[:5]))
                return None
            for t in targets:
                if name in (dev.ON_OFF, dev.ACTIVATE):
                    domain, service = dev.on_off_call(t.entity_id, slots.get("action", "on"))
                    data: dict[str, Any] = {}
                else:
                    call = dev.level_call(t.entity_id, float(slots["value"]))
                    if call is None:
                        continue
                    domain, service, data = call
                await services.async_call(domain, service, {"entity_id": t.entity_id, **data},
                                          blocking=True, context=user_input.context)
                done.append(t.name)
        except (HomeAssistantError, ValueError, KeyError) as err:
            _LOGGER.warning("%s failed: %s", name, err)
            reply.say(f"Не вдалося: {err}")
            return None
        if not done:
            reply.say("Цей пристрій так не вміє.")
            return None
        what = ", ".join(done[:5]) + (f" і ще {len(done) - 5}" if len(done) > 5 else "")
        if name == dev.ACTIVATE:
            reply.say(f"Запустила: {what}.")
        elif name == dev.SET_LEVEL:
            reply.say(f"Встановила {slots['value']:g}: {what}.")
        else:
            verb = {"on": "Увімкнула", "off": "Вимкнула", "toggle": "Перемкнула"}.get(
                slots.get("action", "on"), "Готово")
            reply.say(f"{verb}: {what}.")
        return None

    def choices(self, conv_id: str) -> list[str]:
        """Possible answers to the question asked in this conversation, for buttons."""
        pending = self._pending.get(conv_id)
        if pending is None or pending.expires < time.monotonic():
            return []
        if pending.kind == "confirm":
            return ["Так", "Ні"]
        if pending.kind == "context":
            return list(pending.options)
        if pending.kind != "slot" or not pending.matches:
            return []
        current = pending.matches[0]
        if pending.slot == "device" and current["intent"].startswith(dev.PREFIX):
            slots = current.get("slots") or {}
            kind_domains = dev.KINDS.get(slots.get("kind") or "")
            labels: list[str] = []
            for t in self._targets:
                domain = t.entity_id.split(".")[0]
                if domain in dev.ACTIVATABLE or (kind_domains and domain not in kind_domains):
                    continue
                if slots.get("area") and t.area != slots["area"]:
                    continue
                label = f"{t.name} ({t.area})" if t.area else t.name
                if label not in labels:
                    labels.append(label)
            return labels if 0 < len(labels) <= 10 else []
        spec = next((i for i in self._intents if i["name"] == current["intent"]), None)
        slot = next((sl for sl in (spec or {}).get("slots", []) if sl["name"] == pending.slot), None)
        if slot and slot.get("type") == "enum" and 0 < len(slot.get("values", [])) <= 10:
            return list(slot["values"])
        if slot and slot.get("type") == "boolean":
            return ["Так", "Ні"]
        return []

    def _ask(self, conv_id: str, pending: Pending) -> None:
        pending.expires = time.monotonic() + PENDING_TTL_S
        self._pending[conv_id] = pending

    async def _fallback(
        self, user_input: conversation.ConversationInput, conv_id: str, reply: _Reply,
        silent: bool = False,
    ) -> conversation.ConversationResult:
        if silent:
            reply.error("")
            return reply.result(conv_id)
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
