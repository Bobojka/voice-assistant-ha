"""Scripts labelled for voice -> command catalog for the voice-assistant service.

Script -> command: the script key is the command name, alias + description
describe it ("Приклади: a; b" lines become example phrases), fields become
slots, the field selector decides the slot type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
EXAMPLES_RE = re.compile(r"^\s*(приклади|examples)\s*:\s*(.*)$", re.IGNORECASE)


@dataclass
class Command:
    """What the agent needs to run a matched command and talk about it."""

    script: str  # service name in the script domain
    title: str  # alias, for speech
    fields: dict[str, str] = field(default_factory=dict)  # slot -> human label
    required: list[str] = field(default_factory=list)


def _split_description(text: str) -> tuple[str, list[str]]:
    """Separates "Приклади: a; b" lines from the description."""
    lines, examples = [], []
    for line in (text or "").splitlines():
        m = EXAMPLES_RE.match(line)
        if m:
            examples += [e.strip() for e in re.split(r"[;\n]", m.group(2)) if e.strip()]
        else:
            lines.append(line)
    return "\n".join(lines).strip(), examples


def _slot(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    selector = spec.get("selector") or {}
    kind = next(iter(selector), "text")
    options = selector.get(kind) or {}
    label = spec.get("name") or name
    desc = " ".join(x for x in (label if label != name else "", spec.get("description") or "") if x)
    if spec.get("example") not in (None, ""):
        desc = f"{desc} (приклад: {spec['example']})".strip()
    slot: dict[str, Any] = {"name": name, "type": "string", "description": desc,
                            "required": bool(spec.get("required"))}

    if kind == "number":
        step = options.get("step")
        slot["type"] = "integer" if isinstance(step, int) and step >= 1 else "number"
    elif kind == "boolean":
        slot["type"] = "boolean"
    elif kind == "select":
        values = []
        for opt in options.get("options") or []:
            value = opt.get("value") if isinstance(opt, dict) else opt
            if value is not None and str(value) and str(value) not in values:
                values.append(str(value))
        if values and not options.get("custom_value"):
            slot["type"] = "enum"
            slot["values"] = values
        elif values:
            slot["description"] = f"{desc} Варіанти: {', '.join(values)}".strip()
    elif kind not in ("text", "template"):
        # entity, area, duration... are passed as text; the description says what is expected.
        slot["description"] = f"{desc} (тип: {kind})".strip()
    return slot


def build_catalog(scripts: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Command]]:
    """scripts: script key -> its service description ({name, description, fields})."""
    intents: list[dict[str, Any]] = []
    commands: dict[str, Command] = {}
    for key, desc in sorted(scripts.items()):
        if not NAME_RE.match(key):
            continue
        title = desc.get("name") or key
        text, examples = _split_description(desc.get("description") or "")
        slots = []
        command = Command(script=key, title=title)
        for fname, fspec in (desc.get("fields") or {}).items():
            if not isinstance(fspec, dict) or not NAME_RE.match(fname) or len(slots) >= 20:
                continue
            slots.append(_slot(fname, fspec))
            command.fields[fname] = fspec.get("name") or fname
            if fspec.get("required"):
                command.required.append(fname)
        intents.append({
            "name": key,
            "description": f"{title}. {text}".strip(" ."),
            "slots": slots,
            "examples": examples,
        })
        commands[key] = command
    return intents, commands


# --- short answers in a dialog -------------------------------------------------

_YES = {"так", "да", "ага", "угу", "звісно", "звичайно", "давай", "підтверджую", "виконуй",
        "ок", "окей", "добре", "yes", "yep", "ok"}
_NO = {"ні", "нє", "не", "не треба", "не потрібно", "скасуй", "скасувати", "відміна", "відміни",
       "стоп", "no", "cancel"}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.casefold()).split())


def yes_no(text: str) -> bool | None:
    """True / False for a short yes / no answer, None for anything else."""
    t = _norm(text)
    words = t.split()
    if not words:
        return None
    if t in _YES or (words[0] in _YES and len(words) <= 3):
        return True
    if t in _NO or (words[0] in {"ні", "нє"} and len(words) <= 3):
        return False
    return None


def describe_slots(command: Command, slots: dict[str, Any]) -> str:
    parts = [f"{command.fields.get(k, k)}: {v}" for k, v in slots.items() if v not in (None, "")]
    return ", ".join(parts)
