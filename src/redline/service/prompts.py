"""Instructions injected by the Messenger service.

`CLAUDE.md` owns coaching policy and vault rules. This module adds channel style,
per-turn reading hints and the workflows the service starts. Their words live in
`src/redline/coach/fr/`; this module chooses which ones a turn gets and makes every
command they name runnable.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

from redline import cli
from redline.coach.fr import fill, messages, note, prompt
from redline.model import agent_input, session_kinds, typography
from redline.model import ledger as ledger_model

#: Re-exported so the check that no injected text uses them reads the same list as the
#: guard that refuses to write them.
BANNED_GLYPHS = typography.BANNED_GLYPHS

AGENT_CLI = "/usr/local/bin/redline-agent-cli"
_COMMAND = re.compile(r"\bredline (?=(?:" + "|".join(map(re.escape, cli.COMMANDS)) + r")\b)")


def agent_cli(text: str) -> str:
    """Rewrite every command an injected text names to the only path the agent may run.

    The texts spell commands as an operator types them (`redline week-plan`); the agent
    may only run them through its launcher, and a form it is shown is the form it copies.
    """
    return _COMMAND.sub(f"{AGENT_CLI} ", text)


def turn_input_note(turn_id: str) -> str:
    """The file through which this turn hands a JSON to a CLI (`agent_input.py`)."""
    return messages.turn_input(str(agent_input.turn_path(turn_id)),
                               str(agent_input.turn_path(turn_id, "carte")))


def with_turn_input(message: str, turn_id: str | None) -> str:
    """The turn's message, opened on the file of THIS turn.

    It travels in the message, never in the system prompt: the agent CLI freezes the
    system prompt when the session opens and replays it on every resume, so later turns
    would read the first turn's path. Put first, it lets the message end on the
    athlete's words.
    """
    return f"{turn_input_note(turn_id)}\n\n{message}" if turn_id else message


def strava_snapshot_note(snapshot: dict) -> str:
    """The capture the service made, handed over without a new network read."""
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return fill(prompt("strava-snapshot"), payload=payload)


#: The one text allowed to name the banned glyphs; the glyph check removes it first.
TYPOGRAPHY_RULE = prompt("typography-rule")

STYLE_PROMPT = agent_cli(fill(prompt("style"), typography_rule=TYPOGRAPHY_RULE) + "\n")

#: A session is derived from its prompt: when the style changes, the session no longer
#: holds. The style is the only block baked in at session level (reading notes are
#: injected on every turn), and a model imitates its own earlier turns before its
#: instructions, so a fixed behaviour only takes once the old examples are gone.
SESSION_CONTRACT = "constitution=CLAUDE.md"
STYLE_DIGEST = hashlib.sha256(
    (STYLE_PROMPT + "\n" + SESSION_CONTRACT).encode("utf-8")).hexdigest()[:16]

#: The normative files read from disk when a thread opens; their content is part of the
#: session's identity. `AGENTS.md` is loaded natively by Codex when it exists;
#: `redline.toml` carries the athlete's name, rendered into the baked style.
INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md", "redline.toml")


def instruction_manifest(vault_path: str | Path) -> dict[str, str]:
    """{file: digest} of the instruction files on disk, never their text."""
    manifest = {}
    for name in INSTRUCTION_FILES:
        try:
            content = (Path(vault_path) / name).read_bytes()
        except OSError:
            continue
        manifest[name] = hashlib.sha256(content).hexdigest()[:16]
    return manifest


def instruction_digest(vault_path: str | Path) -> str:
    """The full identity of a thread's instructions: baked style plus files read.

    It moves only when a normative file changes, so a commit of notes, journal or
    hot-cache restarts no conversation. Without a constitution on disk it is the style.
    """
    manifest = instruction_manifest(vault_path)
    if not manifest:
        return STYLE_DIGEST
    return hashlib.sha256((STYLE_DIGEST + "\n" + json.dumps(
        manifest, sort_keys=True)).encode("utf-8")).hexdigest()[:16]


STALE_VAULT_NOTE = note("stale-vault")

LABELS = messages.LABELS

#: Set when a notebook is connected: it collects RPE, pain and enjoyment in one tap,
#: so Messenger does not ask for them.
NOTEBOOK_NOTE = note("notebook")

#: Cells computed from Strava: fetched by the coach, never asked of the athlete.
DERIVABLE_COLUMNS = ("D+", "D-")


def _split_cells(entries: list) -> tuple[list, list]:
    """(what is asked of the athlete, what is fetched)."""
    asked: list = []
    fetched: list = []
    for entry in entries:
        for bucket, columns in (
            (asked, [c for c in entry["missing"] if c not in DERIVABLE_COLUMNS]),
            (fetched, [c for c in entry["missing"] if c in DERIVABLE_COLUMNS]),
        ):
            if columns:
                bucket.append({**entry, "missing": columns})
    return asked, fetched


def _label(column: str) -> str:
    """`Dlr:knee` reads as the pain label followed by its zone."""
    zone = ledger_model.column_zone(column)
    base = LABELS.get(ledger_model.column_base(column), column)
    return f"{base} ({zone})" if zone else base


def missing_cells_note(pending: list, lapsed: list, *, notebook: bool = False) -> str:
    """Build the invisible instruction for declarative ledger cells."""
    # A wake trace is qualitative and asked on waking; it is not a figure to chase.
    pending = [entry for entry in pending if entry.get("type") != session_kinds.WAKE_TRACE]
    lapsed = [entry for entry in lapsed if entry.get("type") != session_kinds.WAKE_TRACE]
    if not pending and not lapsed:
        return ""
    to_ask, to_fetch = _split_cells(pending)
    lapsed_ask, lapsed_fetch = _split_cells(lapsed)
    to_fetch += lapsed_fetch
    keys = ", ".join(f"{entry['date']}:{entry['type']}:{column}"
                     for entry in to_ask for column in entry["missing"])
    return agent_cli(messages.missing_cells(
        messages.cell_listing(to_ask, _label), keys, messages.cell_listing(to_fetch),
        messages.cell_listing(lapsed_ask), notebook=notebook))


def _held_sessions(gates: Sequence) -> str:
    return ", ".join(dict.fromkeys(
        session_id for gate in gates for session_id in gate["holds"]))


def gate_note(blocking: Sequence, observing: Sequence = (), *,
              closing: bool = False) -> str:
    """The invisible instruction of a day with a dated check.

    A due check is the plan waiting for an answer, obtained by asking the question. A
    precondition (`blocking`) holds the sessions it names; an observation only exists
    once the session is done, so it holds nothing. `closing`: the athlete's message is
    social or ends the exchange; a blocking check still travels whole, an observation
    does not become a reason to change the subject.
    """
    parts = []
    if blocking:
        parts.append(fill(note("gate-blocking"), held=_held_sessions(blocking),
                          asks=messages.gate_asks(blocking)))
    if observing:
        name = "gate-observing-closing" if closing else "gate-observing"
        parts.append(fill(note(name), asks=messages.gate_asks(observing)))
    return "\n\n".join(parts)


def plan_change_note() -> str:
    """What a turn that touches the plan owes, invisible to the athlete.

    The triage flags such a message, but only the classifier reads the triage rules;
    this note is how the answering model learns the message may change the plan.
    """
    return note("plan-change")


def stale_plan_note(week: str) -> str:
    """An unplanned load waits for a decision; put first, while the coach can act."""
    return agent_cli(messages.stale_plan(week))


def today_note(moment) -> str:
    """The date anchor, in front of every turn: the agent's own date is frozen when
    its session opens and names no weekday."""
    return messages.today(moment)


def canonical_plan_note(context: str) -> str:
    """The canonical plan, put in front of a turn allowed to prescribe."""
    return messages.canonical_plan(context)


def active_revision_note(context: str) -> str:
    """The same for an initiative, which starts without a message from the athlete."""
    return messages.active_revision(context)


def deep_handoff_note(handoff: str) -> str:
    """A previous `/deep` exchange, named as history and not as an order."""
    return messages.deep_handoff(handoff)


PATIENCE_LINES = messages.PATIENCE_LINES
DEEP_PATIENCE_LINES = messages.DEEP_PATIENCE_LINES

#: Read by the triage model on every message; the escalation bias lives in the rules.
TRIAGE_RULES = prompt("triage-rules") + "\n"

#: For the text engine only: under a JSON schema these lines would describe an output
#: the model may not produce.
EXEMPLES_TRIAGE = "\n" + prompt("triage-examples") + "\n"

TRIAGE_PROMPT = (messages.TRIAGE_HEADER + TRIAGE_RULES + EXEMPLES_TRIAGE + "\n\n"
                 + messages.TRIAGE_FOOTER)

TRIAGE_STRUCTURED_PROMPT = (messages.TRIAGE_STRUCTURED_HEADER + TRIAGE_RULES + "\n"
                            + messages.TRIAGE_FOOTER)

CLASSIFY_PREAMBLE = messages.CLASSIFY_PREAMBLE

EMOTION_NOTE = note("emotion")

#: The rating scales travel in the reading notes, injected on every turn, rather than
#: in the style baked into the session.
PAIN_SCALE_POINTER = messages.PAIN_SCALE_POINTER
STRENGTH_CARD_POINTER = messages.STRENGTH_CARD_POINTER
SESSION_TIME_POINTER = messages.SESSION_TIME_POINTER
SESSION_CYCLE_POINTER = agent_cli(messages.SESSION_CYCLE_POINTER)

_POINTERS = " ".join((SESSION_TIME_POINTER, SESSION_CYCLE_POINTER, PAIN_SCALE_POINTER,
                      STRENGTH_CARD_POINTER))
DEPTH_NOTES = {level: agent_cli(fill(note(f"depth-{level.lower()}"), pointers=_POINTERS))
               for level in ("N0", "N1", "N2", "N3")}

STRENGTH_REPORT_NOTE = agent_cli(note("strength-report"))

CELLS_UNREADABLE_NOTE = messages.CELLS_UNREADABLE
BATTERY_NOTE = messages.BATTERY_NOTE
EMPTY_REPLY_NOTE = messages.EMPTY_REPLY_NOTE

DEFAULT_DEPTH = "N1"

HELP_TEXT = prompt("help")

PROACTIVE_TRIGGERS = {
    kind: agent_cli(prompt("triggers/" + kind.replace("_", "-")))
    for kind in ("strava_debrief", "strava_debrief_differe", "morning", "daily_signal",
                 "premortem", "correction", "decision_challenge", "race_countdown",
                 "weekly_repair", "weekly", "monthly")
}

#: Initiatives that may not stay silent: they must produce a text.
MUST_SPEAK_KINDS = frozenset({"weekly", "weekly_repair"})


def debrief_in_reply(detail: str, *, notebook: bool) -> str:
    """The debrief of an arrived activity, absorbed by the reply to the athlete's message.

    Two messages (the reply, then the debrief) would repeat each other.
    """
    instructions = PROACTIVE_TRIGGERS["strava_debrief"].replace("{detail}", detail)
    return agent_cli(messages.debrief_in_reply(detail, NOTEBOOK_NOTE if notebook else "",
                                               instructions))


def build_proactive_prompt(kind: str, detail: str) -> str:
    instructions = PROACTIVE_TRIGGERS[kind].replace("{detail}", detail)
    if kind == "weekly":
        ending = messages.WEEKLY_ENDING
    elif kind in MUST_SPEAK_KINDS:
        ending = messages.MUST_SPEAK_ENDING
    else:
        ending = messages.MAY_STAY_SILENT_ENDING
    return agent_cli(messages.initiative(instructions, ending))


def timing_note(age_seconds: float, received_at, *, overtaken: bool) -> str:
    """The real delay between the athlete's message and this reply, when it matters."""
    return messages.timing(age_seconds, received_at, overtaken=overtaken)


def attachment_prompt(files: Sequence, caption: str, *, ignored_video: bool) -> str:
    """The read-only turn that looks at files the athlete sent."""
    return messages.attachment(
        files, json.dumps(caption or messages.NO_CAPTION, ensure_ascii=False),
        ignored_video)
