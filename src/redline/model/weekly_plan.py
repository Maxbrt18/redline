"""Validation and rendering of the canonical weekly plans stored in PostgreSQL.

The Obsidian journals are read-only projections of them.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from itertools import pairwise

from redline.coach import dates
from redline.coach.fr import plan as text

from . import paths, rulebook, session_kinds, transitions, typography
from . import program as strength_program
from .notes import NoteError
from .program import GATE_SCHEMA as STRENGTH_GATE_SCHEMA
from .schema import Schema, SchemaError, field
from .vocabulary import Decider

WEEK_RE = r"20\d{2}-W(?:0[1-9]|[1-4]\d|5[0-3])"
DATE_RE = r"20\d{2}-\d{2}-\d{2}"
TIME_RE = r"(?:[01]\d|2[0-3]):[0-5]\d"
STAMP_RE = r"20\d{2}-\d{2}-\d{2}T.+"
BLOCK = "WEEK_PLAN"

KINDS = tuple(dict.fromkeys(session_kinds.family(slug) for slug in session_kinds.PLANNED))
WORKOUTS = session_kinds.PLANNED
INTENT_STATUSES = ("planned", "cancelled")
# ``done`` exists in the read model rendered from ``session_cycles`` and in historical
# revisions. A new revision can no longer write it.
STATUSES = (*INTENT_STATUSES, "done")
# States that SETTLE a session: it happened or it never will. Either way it belongs to the
# week's story and is never prescribable again.
# Written negatively ("not yet settled") rather than positively ("planned") so that a
# status added later stays prescribable by default: forgetting a session to do is worse
# than showing one too many.
SETTLED_STATUSES = frozenset({"done", "cancelled"})
HARD_WORKOUTS = session_kinds.DEMANDING


STRENGTH_SPEC_SCHEMA = Schema("strength modulation", [
    field("loads", list, required=False, maximum=20,
          item=field("load", dict, schema=strength_program.PRESCRIBED_LOAD_SCHEMA)),
    field("variant", str, required=False,
          note="a variant declared by the workout note"),
    field("half_dose", bool, required=False),
    field("drop", list, required=False, item=field("slug", str)),
    field("note", str, required=False, maximum=300),
    field("duration_min", int, required=False, minimum=5, maximum=120),
    field("gate", dict, required=False, schema=STRENGTH_GATE_SCHEMA),
])

SESSION_SCHEMA = Schema("planned session", [
    field("id", str, pattern=r"[a-z0-9][a-z0-9-]+"),
    # Human-facing text is part of the persisted transaction. Renderers must
    # never maintain a second, incomplete taxonomy of workout labels.
    field("title", str, minimum=1),
    field("date", str, pattern=DATE_RE, note="YYYY-MM-DD"),
    field("start_time", str, required=False, pattern=TIME_RE,
          note="HH:MM, the athlete's local time; absent when no slot is set"),
    field("kind", str, choices=KINDS),
    field("workout", str, choices=WORKOUTS),
    field("routine", str, required=False, pattern=r"[a-z0-9][a-z0-9-]{0,39}",
          note="the strength routine, as its note declares it; strength only"),
    field("status", str, choices=STATUSES),
    field("distance_target_km", float, required=False, minimum=0),
    field("distance_cap_km", float, required=False, minimum=0),
    field("duration_cap_min", int, required=False, minimum=1),
    field("rpe_min", int, required=False, minimum=0, maximum=10),
    field("rpe_max", int, required=False, minimum=0, maximum=10),
    field("hr_zone", str, required=False),
    field("hr_cap_bpm", int, required=False, minimum=80, maximum=220,
          note="cap on the session's AVERAGE heart rate, in bpm: the figure hr_zone "
               "states in words; required on write with hr_zone"),
    field("cadence_target", int, required=False, minimum=120, maximum=220),
    field("demanding", bool),
    field("transition", str, required=False, pattern=rulebook.SLUG,
          note="a kind absent from the window of rule n8, one of the rulebook's; "
               "absent for a session already known"),
    field("location_label", str, required=False),
    field("carbs_target_g_h", int, required=False, minimum=0, maximum=150,
          note="experimental fuelling target in grams of carbohydrate per hour"),
    field("strength", dict, required=False, schema=STRENGTH_SPEC_SCHEMA,
          note="typed modulations; the session and its exercises come from the workout"),
    field("note", str, required=False),
    field("sources", list, minimum=1, item=field("source", str)),
])

GATE_WHEN = ("before", "after")


# A dated condition is typed so it cannot be prescribed around once due.
#
# `when` is the central question and is asked at write time. A dated gate is one of two
# very different things:
#
#   before -- a PRECONDITION. Its answer exists before the sessions it holds, and while
#             it is missing those sessions are not prescribed.
#   after  -- an OBSERVATION. Its answer exists only once the session is done. It
#             informs what follows and holds nothing, because it cannot hold anything
#             without waiting on itself.
#
# An observation written as a precondition would hold the very session it asks about.
# A gate must decide, and "before" requires a `holds` that NAMES the held sessions.
# Legacy rows were normalized once in the database, so the current model has no
# historical dialect to interpret.
GATE_SCHEMA = Schema("dated gate", [
    field("id", str, pattern=r"[a-z0-9][a-z0-9-]+"),
    field("due", str, pattern=DATE_RE, note="YYYY-MM-DD"),
    field("when", str, choices=GATE_WHEN,
          note="before = precondition, holds the sessions of `holds`; "
               "after = observation, holds nothing"),
    field("holds", list, required=False, item=field("session", str),
          note="the ids of the sessions this gate holds -- "
               "required when when=before, forbidden when when=after"),
    field("question", str, minimum=10, note="what to ask the athlete that day"),
    field("on_fail", str, minimum=3, note="what becomes of the plan if the answer is bad"),
])

CHANGE_FIELDS = (
    "added", "removed", "title", "date", "start_time", "workout", "status",
    "distance_target_km", "distance_cap_km", "duration_cap_min", "rpe_min",
    "rpe_max", "hr_zone", "cadence_target", "demanding", "transition",
    # `calibration` is no longer written; the name stays valid in the `changes` of
    # existing revisions, which are re-read and validated in full.
    "calibration",
    "carbs_target_g_h", "location_label", "strength", "note",
)

#: A restriction names its premise as a type. A restriction without a premise is the most
#: frequent prescription error, and free-text reminders did not prevent it. A free-text
#: `rule` cannot be verified; a rulebook identifier and an `evidence` fact can. `request`
#: covers what the athlete asked for directly.
REQUEST_RULE = "request"


def restriction_rules() -> tuple[str, ...]:
    """The premises of a restriction: the instance rulebook's rules, or a request."""
    return (*(rule.id for rule in rulebook.current().rules), REQUEST_RULE)


#: Prefixes of a FACT: what the ledger, Strava, an opinion or a gate said.
#: A rulebook rule applies only to a fact; a request comes from a message.
_FACT_TRIGGER = re.compile(r"^(?:observation|exposure|medical|gate|incident|strength|strava):")
_REQUEST_TRIGGER = re.compile(r"^(?:messenger|request|availability):")

def _known_rule(restriction: dict) -> list[str]:
    """The named rule exists in the instance rulebook, read at validation time."""
    if restriction["rule"] in restriction_rules():
        return []
    return [f"field `rule`: {restriction['rule']!r} unknown "
            f"(expected: {', '.join(restriction_rules())})"]


RESTRICTION_SCHEMA = Schema("restriction premise", [
    field("rule", str, minimum=1,
          note="the id of the rulebook rule that imposes the restriction, or request"),
    field("fact", str, minimum=3,
          note="an exact entry of evidence: the fact that triggers the rule"),
], check=_known_rule)

CHANGE_SCHEMA = Schema("session change evidence", [
    field("session_id", str, pattern=r"[a-z0-9][a-z0-9-]+"),
    field("fields", list, minimum=1,
          item=field("field", str, choices=CHANGE_FIELDS)),
    field("trigger", str, minimum=3,
          note="an exact entry of evidence that triggers this change"),
    field("context", str, minimum=3,
          note="the context or confirmed intent that gives the evidence its meaning"),
    field("rule", str, minimum=3,
          note="the coaching rule applied to this evidence"),
    # Optional on read: older revisions do not carry it.
    # Required on write for any restriction (`change_evidence_errors`).
    field("restriction", dict, required=False, schema=RESTRICTION_SCHEMA,
          note="required when the session is removed, cancelled or reduced"),
])

ALTERNATIVE_SCHEMA = Schema("rejected option", [
    field("option", str, minimum=3, note="the option considered and not chosen"),
    field("rejected_because", str, minimum=3, note="why it was set aside"),
])


def _decision_coherent(decision: dict) -> list[str]:
    by, rule = decision["by"], decision.get("rule")
    if by == Decider.RULE and not rule:
        return ["field `rule`: required when `by` is rule -- the rulebook id that decided"]
    if by != Decider.RULE and rule:
        return [f"field `rule`: only for `by` rule, not {by}"]
    if by == Decider.JUDGMENT and not decision["alternatives"]:
        return ["field `alternatives`: a judgment names at least one option it set aside"]
    return []


DECISION_SCHEMA = Schema("decision", [
    field("by", str, choices=tuple(Decider),
          note="rule (a rulebook rule left no choice), judgment, or request"),
    field("rule", str, required=False, minimum=1),
    field("alternatives", list, item=field("alternative", dict, schema=ALTERNATIVE_SCHEMA),
          note="the options considered and set aside, with the reason"),
], check=_decision_coherent)

#: Computed by the engine when the revision is written (`analysis.decision_state`).
STATE_SNAPSHOT_SCHEMA = Schema("decision state", [
    field("as_of", str, pattern=r"\d{4}-\d{2}-\d{2}"),
    field("week_km_done", float, minimum=0),
    field("pain_max_7d", dict),
    field("daily", dict, required=False),
])

REVISION_SCHEMA = Schema("weekly revision", [
    field("revision", int, minimum=1),
    field("computed_at", str, pattern=STAMP_RE, note="ISO 8601 date-time"),
    field("reason", str, minimum=3),
    field("evidence", list, minimum=1, item=field("evidence", str)),
    field("constraints", list, item=field("constraint", str)),
    # Optional so history can be read; required at the `revise` boundary as soon as a
    # future prescription changes (see `change_evidence_errors`).
    field("changes", list, required=False,
          item=field("change", dict, schema=CHANGE_SCHEMA)),
    # Optional: older revisions carry none, and a week that conditions nothing on a date
    # is the normal case.
    field("gates", list, required=False,
          item=field("gate", dict, schema=GATE_SCHEMA)),
    # Optional on read: older revisions do not carry it.
    # Required on write once the week is running (`typed_target_errors`).
    field("volume_target_km", float, required=False, minimum=1, maximum=200,
          note="running volume targeted over the whole week, in km"),
    field("sessions", list, minimum=1,
          item=field("session", dict, schema=SESSION_SCHEMA)),
    # Optional on read: older revisions carry neither. Required on write
    # (`decision_errors`); the snapshot is the engine's, never the model's.
    field("decision", dict, required=False, schema=DECISION_SCHEMA),
    field("state_snapshot", dict, required=False, schema=STATE_SNAPSHOT_SCHEMA),
])

WEEKLY_PLAN_SCHEMA = Schema("weekly plan", [
    field("week", str, pattern=WEEK_RE, note="YYYY-WNN"),
    field("revisions", list, minimum=1,
          item=field("revision", dict, schema=REVISION_SCHEMA)),
])


def now_stamp() -> str:
    """The real instant of the write, in local time with its offset.

    A single function, patchable in tests and called from one place only:
    `redline week-plan revise`. The `computed_at` field used to be filled by the model, which
    has no clock -- see the comment in `revise`.
    """
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def current_week(today: dt.date | None = None) -> str:
    iso = (today or dt.date.today()).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def next_week(today: dt.date | None = None) -> str:
    day = (today or dt.date.today()) + dt.timedelta(days=7)
    return current_week(day)


def _week_dates(week: str) -> set[str]:
    year, number = week.split("-W")
    monday = dt.date.fromisocalendar(int(year), int(number), 1)
    return {(monday + dt.timedelta(days=i)).isoformat() for i in range(7)}


# `title` and `note` are the ONLY two revision fields sent verbatim to the athlete --
# morning text message and program card. `reason`, `evidence` and `constraints` are the
# archive and are never sent. A schema that accepts any string in a field it PUBLISHES
# protects nothing.
#
# Append-only: already-written revisions cannot be repaired. The rule therefore judges
# only what is written from now on, otherwise an old revision would become invalid and
# block all prescription. ISO string comparison, like the rest of the module
# (`gate["due"] < written_on`): all `computed_at` values carry the same local offset.
def _text_errors(text: str, where: str, label: str, gate_ids: list[str]) -> list[str]:
    """`title`/`note`: what the athlete reads, judged as such."""
    problems = []
    for gate_id in gate_ids:
        if gate_id in text:
            problems.append(
                f"{where}: {label} cites the dated gate `{gate_id}` -- this text goes to "
                "the athlete's phone and to the card; the internal machinery stays in "
                "reason/evidence")
    return problems


def written_text_errors(revision: dict, where: str = "revision") -> list[str]:
    """The forbidden marks in the text of ONE revision about to be written.

    Called by `redline week-plan revise` on the NEW revision, never by `validate()`. History is
    append-only and `revise` validates the whole plan: the same rule in `semantic_errors`
    would reject today's revision because of an older text, blocking the coach on an
    uncorrectable fault. Here only what comes in is judged, and the program cleans up as
    it is revised.

    Every prose field is checked, not only `title`/`note`: `as_json()` renders the ENTIRE
    ACTIVE revision, so `reason`, `evidence`, `constraints` and the gate text are fed back
    to the coach on every prescriptive turn just like them.
    """
    problems = []
    for label in ("reason",):
        if revision.get(label):
            problems += typography.errors(revision[label], where, label)
    for label in ("evidence", "constraints"):
        for index, item in enumerate(revision.get(label) or []):
            problems += typography.errors(item, where, f"{label}[{index}]")
    for gate in revision.get("gates") or []:
        for label in ("question", "on_fail"):
            if gate.get(label):
                problems += typography.errors(
                    gate[label], f"{where}.gate[{gate.get('id', '?')}]", label)
        # Walking is never asked about: the athlete reports any discomfort unprompted.
        # A precondition no longer holds any session: blocking gates never failed on their
        # criterion, and nearly all session changes came from an athlete message. The
        # condition is written in the session itself, and the logbook's "clear" alert
        # triggers it.
        if gate.get("when") == "before":
            problems.append(
                f"{where}.gate[{gate.get('id', '?')}]: a precondition no longer holds a "
                "session. Write the condition in the session's note (if the trace is clear "
                "this morning: rest); the notebook alerts on a clear trace. A gate is only "
                "for an observation (when=after).")
    for session in revision.get("sessions") or []:
        for label in ("title", "note"):
            if session.get(label):
                problems += typography.errors(
                    session[label], f"{where}.{session.get('id', '?')}", label)
    return problems


def intention_errors(revision: dict, where: str = "revision") -> list[str]:
    """Refuse to copy execution into a new revision.

    The read schema keeps ``done`` for historical plans and for views enriched from the
    cycles. The write boundary is deliberately narrower: a revision says what is planned
    or cancelled, never what happened.
    """
    return [
        f"{where}.{session.get('id', '?')}: status=done belongs to the session "
        "cycle. Keep status=planned; the rendering adds 'done' from PostgreSQL."
        for session in revision.get("sessions") or []
        if session.get("status") not in INTENT_STATUSES
    ]


def typed_target_errors(revision: dict, where: str = "revision") -> list[str]:
    """The target volume and the HR cap are typed, like a future date.

    "About 41 km over five outings" lived in `constraints`, so nowhere the code could see
    it: the logbook could not show "14.1 / ~41 km" without reading prose. A new revision
    that prescribes running gives it as a number; the ledger remains the only source of
    what was run.
    """
    runs = [session for session in revision.get("sessions") or []
            if session.get("kind") == "run" and session.get("status") != "cancelled"]
    problems = []
    if runs and revision.get("volume_target_km") is None:
        problems.append(f"{where}.volume_target_km: missing -- the running volume targeted "
                        "over the whole week, in km (sessions already done included), the "
                        "one the constraints state in prose")
    # Same for HR: "Z1-Z2, 130-157 bpm" is readable but not comparable. The logbook sets
    # the fact (Strava average HR) against this cap. It stays out of CHANGE_FIELDS: it
    # duplicates `hr_zone`, whose every change already requires evidence, and the first
    # addition on an existing session is not a new prescription.
    problems.extend(
        f"{where}.{session.get('id', '?')}.hr_cap_bpm: missing -- the AVERAGE heart "
        f"rate cap in bpm that hr_zone `{session['hr_zone']}` states in words"
        for session in revision.get("sessions") or []
        if session.get("status") != "cancelled" and session.get("hr_zone")
        and session.get("hr_cap_bpm") is None)
    return problems


def _session_errors(session: dict, where: str) -> list[str]:
    problems = []
    target, cap = session.get("distance_target_km"), session.get("distance_cap_km")
    if target is not None and cap is not None and target > cap:
        problems.append(f"{where}: target {target:g} km above the cap {cap:g} km")
    lo, hi = session.get("rpe_min"), session.get("rpe_max")
    if lo is not None and hi is not None and lo > hi:
        problems.append(f"{where}: minimum RPE {lo} above the maximum {hi}")
    workout = session.get("workout")
    duration = session.get("duration_cap_min", 0)
    if workout in session_kinds.BY_SLUG and session.get("kind") != session_kinds.family(workout):
        problems.append(f"{where}: kind must be {session_kinds.family(workout)} "
                        f"for {workout}")
    expected_hard = (workout in HARD_WORKOUTS
                     or (workout == session_kinds.EASY and duration > 75))
    if session.get("demanding") != expected_hard:
        problems.append(
            f"{where}: demanding must be {str(expected_hard).lower()} for {workout}")
    if session.get("kind") == "run" and target is None and cap is None and duration == 0:
        problems.append(f"{where}: a run requires a target, a cap or a duration")
    if session.get("transition") and session.get("kind") == "rest":
        problems.append(f"{where}: a rest day is not a transition")
    if session.get("carbs_target_g_h") is not None and workout not in session_kinds.FUELLED:
        problems.append(f"{where}: a fuelling target is only tested on "
                        + " or ".join(sorted(session_kinds.FUELLED)))
    is_strength = workout == session_kinds.STRENGTH
    for name in ("strength", "routine"):
        if session.get(name) is not None and not is_strength:
            problems.append(f"{where}: {name} only applies to a strength session")
    if is_strength and session.get("routine") is None:
        problems.append(f"{where}: routine missing -- the strength routine its note declares")
    if is_strength and session.get("strength") is None:
        problems.append(
            f"{where}: strength missing. Even without a modulation, write strength={{}} "
            "to freeze the card's prescription")
    return problems


def _strength_program_errors(session: dict, where: str) -> list[str]:
    """Resolve a strength prescription against its real program.

    The schema proves the shape of ``strength``; only ``Program.build`` can prove that
    its variant and exercises exist. Only the active revision is validated: append-only
    history may hold an old error that can no longer be fixed, but a new revision must
    never activate it.
    """
    if session.get("workout") != session_kinds.STRENGTH or not session.get("routine"):
        return []
    selection = {
        "session": session["routine"],
        "date": session["date"],
        "sid": session["id"],
        **(session.get("strength") or {}),
    }
    try:
        strength_program.load(paths.vault_root()).build(selection)
    except Exception as exc:
        return [f"{where}.strength: cannot be prescribed -- {exc}"]
    return []


def _gate_typing_errors(gate: dict, where: str, sessions: list[dict]) -> list[str]:
    """`when` and `holds`: what a dated gate may hold.

    `when` is required. "Before" requires a `holds` naming the held sessions -- writing
    "I hold session A" next to a question asking how session A went is a visible
    contradiction.
    """
    problems = []
    when = gate["when"]
    holds = gate.get("holds")

    if when == "after" and holds:
        problems.append(
            f"{where}: when=after with `holds` -- an observation holds nothing. Its "
            "answer comes AFTER the sessions; it cannot be their condition.")
    if when == "before" and not holds:
        problems.append(
            f"{where}: when=before without `holds` -- name the sessions this gate "
            "holds. A gate that holds everything also holds the one whose answer it "
            "waits for.")

    known = {session["id"]: session["date"] for session in sessions}
    for session_id in holds or []:
        if session_id not in known:
            problems.append(
                f"{where}: `holds` cites `{session_id}`, which is not a session of "
                "this revision")
            continue
        # Holding a session already past when the question is asked has no effect: it
        # happened. That signals an observation was intended.
        if known[session_id] < gate["due"]:
            problems.append(
                f"{where}: holds `{session_id}` of {known[session_id]}, before the due "
                f"date {gate['due']} -- that session will already have happened. A "
                "condition that comes after its session is an observation (when=after).")
    return problems


def semantic_errors(plan: dict, where: str = "weekly_plan",
                    today: dt.date | None = None) -> list[str]:
    """Cross-revision and coaching invariants not expressible field by field."""
    problems = WEEKLY_PLAN_SCHEMA.errors(plan, where)
    if problems:
        return problems
    week = plan["week"]
    allowed = _week_dates(week)
    # Strength resolves against the current program: this is a prescription guard.
    # A closed week has nothing left to prescribe, and the program may have changed since
    # -- judging it that way would make its archive unreadable.
    prescribable = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 7) \
        >= (today or dt.date.today())
    revisions = plan["revisions"]
    numbers = [revision["revision"] for revision in revisions]
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"{where}: revisions not contiguous ({numbers})")
    for revision in revisions:
        prefix = f"{where}.revision[{revision['revision']}]"
        gates = revision.get("gates") or []
        gate_ids = [gate["id"] for gate in gates]
        written_on = revision["computed_at"][:10]
        sessions = revision["sessions"]
        ids = [session["id"] for session in sessions]
        if len(ids) != len(set(ids)):
            problems.append(f"{prefix}: duplicate session ids")
        problems.extend(transitions.per_week_errors(sessions, prefix))
        for session in sessions:
            if session["date"] not in allowed:
                problems.append(
                    f"{prefix}.{session['id']}: date {session['date']} outside {week}")
            problems.extend(_session_errors(session, f"{prefix}.{session['id']}"))
            if revision is revisions[-1] and prescribable:
                problems.extend(_strength_program_errors(
                    session, f"{prefix}.{session['id']}"))
            for label in ("title", "note"):
                if session.get(label):
                    problems.extend(_text_errors(
                        session[label], f"{prefix}.{session['id']}", label, gate_ids))
        if revision is revisions[-1] and prescribable:
            problems.extend(_spacing_errors(sessions, prefix))

        if len(gate_ids) != len(set(gate_ids)):
            problems.append(f"{prefix}: duplicate dated gate ids")
        for gate in gates:
            where_gate = f"{prefix}.gate[{gate['id']}]"
            # A gate belongs to the week that carries it. A condition landing past Sunday
            # has a different owner -- `redline.deadlines`, whose overdue check already
            # exists -- otherwise it would sit in a plan nothing looks at once the week is
            # over.
            if gate["due"] not in allowed:
                problems.append(
                    f"{where_gate}: due date {gate['due']} outside {week} -- "
                    "a condition beyond Sunday goes to PostgreSQL through "
                    "redline fact-write deadline")
            if gate["due"] < written_on:
                problems.append(
                    f"{where_gate}: due date {gate['due']} before the revision "
                    f"was computed ({written_on})")
            problems.extend(_gate_typing_errors(gate, where_gate, sessions))

        hard_days = sorted({dt.date.fromisoformat(s["date"]) for s in sessions
                            if s["status"] != "cancelled" and s["demanding"]})
        for first, second in pairwise(hard_days):
            if (second - first).days == 1:
                problems.append(
                    f"{prefix}: consecutive demanding days {first} and {second}")
    return problems


def _spacing_errors(sessions: list[dict], prefix: str) -> list[str]:
    """The gaps each strength routine declares (`min_gap_h`), checked day by day."""
    try:
        routines = strength_program.load(paths.vault_root()).sessions
    except NoteError:
        return []   # the program error itself is reported by _strength_program_errors
    active = [s for s in sessions if s["status"] != "cancelled"]
    problems, seen = [], set()
    for session in active:
        gaps = (routines.get(session.get("routine") or "") or {}).get("min_gap_h") or {}
        day = dt.date.fromisoformat(session["date"])
        for other in active:
            hours = gaps.get(other["workout"])
            pair = frozenset((session["id"], other["id"]))
            if other is session or hours is None or pair in seen:
                continue
            if abs((dt.date.fromisoformat(other["date"]) - day).days) * 24 < hours:
                seen.add(pair)
                problems.append(f"{prefix}: routine {session['routine']} less than "
                                f"{hours} h from a {other['workout']} session ({other['date']})")
    return problems


def validate(plan: dict, where: str = "weekly_plan",
             today: dt.date | None = None) -> dict:
    problems = semantic_errors(plan, where, today)
    if problems:
        raise SchemaError(problems)
    return plan


def active(plan: dict) -> dict:
    validate(plan)
    return plan["revisions"][-1]


def scheduled(sessions: list[dict]) -> list[dict]:
    """What is on the program: everything except what was cancelled.

    DESCRIPTIVE view -- it tells the week's story, completed sessions included. It is the
    one used by the journal, the card and the hot-cache.
    """
    return [session for session in sessions if session["status"] != "cancelled"]


def remaining(sessions: list[dict], *, since: str = "") -> list[dict]:
    """What REMAINS to do from `since`: the PRESCRIPTIVE view.

    A completed or cancelled session commits to nothing, and that rule must live in one
    place only. Each rendering of the same object used to filter on its own, and the most
    assertive one ("the rest of the week") filtered on DATE alone, handing back a session
    already done. A rendering never redefines what a session to do is; it asks here.

    `since` is an INCLUSIVE bound: the current day stays prescribable until its session
    is done.
    """
    return [session for session in sessions
            if session["status"] not in SETTLED_STATUSES
            and session.get("date", "") >= since]


def due_gates(plan: dict, today: dt.date | None = None) -> list[dict]:
    """The dated gates whose day has come, of BOTH kinds.

    These are the questions to ask today and nothing else: `blocking_gates` says which
    ones hold a prescription. Confusing the two closes a door every time a question is
    asked.

    An answer is only obtained by ASKING the question: a due gate is the normal state of
    the morning it falls on, never a failure. Treating it as a broken program makes
    persistence reject the turn and drop the very message that asks the question.
    """
    return due_gates_of(active(plan), today)


def due_gates_of(revision: dict, today: dt.date | None = None) -> list[dict]:
    """The due gates of ONE revision, when it is already at hand.

    The service holds the active revision as JSON and has no plan to give `due_gates`.
    Keeping its own date comparison and its own partition by `when` would duplicate what
    this module declares, on exactly the boundary where a disagreement would show.
    """
    as_of = today or dt.date.today()
    return [gate for gate in (revision.get("gates") or [])
            if isinstance(gate, dict)
            and dt.date.fromisoformat(gate["due"]) <= as_of]


def split_by_when(gates: list[dict]) -> tuple[list[dict], list[dict]]:
    """(preconditions, observations) -- two instructions, never one bundle.

    Merging them lets an observation carry a precondition's ban on prescribing: the coach
    would ask the question AND hold the card of the session it is asked about.
    """
    return ([gate for gate in gates if gate["when"] == "before"],
            [gate for gate in gates if gate["when"] == "after"])


def blocking_gates(plan: dict, today: dt.date | None = None) -> list[dict]:
    """The due gates that really HOLD a prescription.

    `due_gates` returns both kinds, and treating the whole result as a block freezes the
    week on an observation whose answer requires doing the frozen session. Validation and
    persistence consult this function, not `due_gates`: a question asked is not a door
    closed.
    """
    return split_by_when(due_gates(plan, today))[0]


def gate_reasons(plan: dict, today: dt.date | None = None) -> list[str]:
    """The text of the due gates, as served to the coach."""
    return [text.GATE_REASON.format(due=gate["due"], question=gate["question"],
                                    on_fail=gate["on_fail"])
            for gate in blocking_gates(plan, today)]


def as_json(plan: dict) -> str:
    return json.dumps(active(plan), ensure_ascii=False, indent=2)




def distance_of(session: dict) -> str:
    """What COMMITS in distance or duration, in reading order.

    Single source. The text message, the journal and hot-cache, and the PNG card each used
    to carry their own version; a field added to `SESSION_SCHEMA` now needs one edit here
    and every rendering receives it.
    """
    target, cap = session.get("distance_target_km"), session.get("distance_cap_km")
    if target is not None and cap is not None:
        return text.DISTANCE_TARGET_AND_CAP.format(target=f"{target:g}", cap=f"{cap:g}")
    if cap is not None:
        return text.DISTANCE_CAP.format(cap=f"{cap:g}")
    if target is not None:
        return text.DISTANCE_TARGET.format(target=f"{target:g}")
    if session.get("duration_cap_min"):
        return text.DURATION_CAP.format(minutes=session["duration_cap_min"])
    return ""


def details_of(session: dict, *, cadence_unit: str = "") -> list[str]:
    """Everything a session commits to, in order, without its title or status.

    The order is part of the contract: distance, RPE, HR zone, cadence, note. Each
    rendering only decides its separator and markup -- and `cadence_unit` is the one real
    difference, the journal naming the unit that the text message and the card leave
    implicit.
    """
    details = [distance_of(session)]
    if session.get("rpe_min") is not None:
        high = session.get("rpe_max", session["rpe_min"])
        details.append(text.RPE.format(low=session["rpe_min"], high=high))
    if session.get("hr_zone"):
        details.append(session["hr_zone"])
    if session.get("cadence_target"):
        details.append(text.CADENCE.format(cadence=session["cadence_target"], unit=cadence_unit))
    if session.get("carbs_target_g_h") is not None:
        details.append(text.CARBS.format(grams=session["carbs_target_g_h"]))
    if session.get("note"):
        details.append(session["note"])
    return [part for part in details if part]


def _session_line(session: dict) -> str:
    day = dt.date.fromisoformat(session["date"])
    label = dates.weekday(day).capitalize()
    suffix = " · ".join(details_of(session, cadence_unit=text.CADENCE_UNIT))
    status = text.DONE if session["status"] == "done" else session["status"]
    if session.get("transition"):
        status += text.WITH_TRANSITION.format(label=transitions.label(session["transition"]))
    line = text.SESSION_LINE.format(day=label, date=f"{day:%d/%m}", title=session["title"],
                                    status=status)
    return line + (f" · {suffix}" if suffix else "")


def render_journal(plan: dict) -> str:
    revision = active(plan)
    lines = [text.JOURNAL_TITLE.format(revision=revision["revision"]), "",
             text.JOURNAL_COMPUTED.format(computed_at=revision["computed_at"],
                                          reason=revision["reason"]), ""]
    if revision.get("volume_target_km") is not None:
        lines += [text.VOLUME_TARGET.format(km=f"{revision['volume_target_km']:g}"), ""]
    lines.extend(_session_line(session) for session in revision["sessions"])
    gates = revision.get("gates") or []
    for kind in GATE_WHEN:
        due = [gate for gate in gates if gate["when"] == kind]
        if not due:
            continue
        lines += ["", text.GATE_SECTIONS[kind], ""]
        lines.extend(text.GATE_LINE.format(day=gate["due"][8:], month=gate["due"][5:7],
                                           question=gate["question"], on_fail=gate["on_fail"])
                     for gate in due)
    attention = revision.get("constraints") or []
    if attention:
        lines += ["", text.ATTENTION + " · ".join(attention)]
    lines += ["", text.GENERATED]
    return "\n".join(lines)


def journal_skeleton(week: str, today: dt.date) -> str:
    """The journal of a week that has a program but no file yet.

    Creating it cannot be left to the model: the projector only writes into an existing
    file, so a program without a journal is canonical in the database yet invisible in
    Obsidian. The structure follows the existing journals; the program block is filled
    right after by the projection.
    """
    year, number = week.split("-W")
    monday = dt.date.fromisocalendar(int(year), int(number), 1)
    sunday = monday + dt.timedelta(days=6)
    span = (f"{monday.day}-{dates.day_month(sunday)}" if monday.month == sunday.month
            else f"{dates.day_month(monday)} - {dates.day_month(sunday)}")
    frontmatter = f"---\ntype: weekly-review\nweek: {week}\nupdated: {today.isoformat()}\n---\n\n"
    return frontmatter + text.JOURNAL_SKELETON.format(
        week=week, span=span, block=f"<!-- {BLOCK}_START -->\n<!-- {BLOCK}_END -->")


def render_hot_cache(plan: dict) -> str:
    revision = active(plan)
    sessions = scheduled(revision["sessions"])
    compact = "; ".join(
        f"{dates.weekday(dt.date.fromisoformat(s['date']), 'abbreviated')} {s['title']}"
        + (text.HOT_CACHE_CAP.format(cap=f"{s['distance_cap_km']:g}")
           if s.get("distance_cap_km") is not None else "")
        for s in sessions)
    return text.HOT_CACHE.format(week=plan["week"], revision=revision["revision"],
                                 sessions=compact, file=plan["week"].lower())


#: A revision is read back before it is narrated.
#:
#: The coach composes the session table from memory and sends it on stdin; the CLI renders
#: the week as it IS, day by day, empty days and cancelled sessions included -- the only
#: rendering that shows them to the coach before it speaks. The diff says what moved, the
#: week says what results, and neither can be deduced from the other: an omission is only
#: visible in the second.
def _revision_index(revision: dict | None) -> dict[str, dict]:
    return {s["id"]: s for s in (revision or {}).get("sessions", [])}


def _day_label(stamp: str) -> str:
    day = dt.date.fromisoformat(stamp)
    return f"{dates.weekday(day, 'abbreviated')} {day:%d/%m}"


def _comparable(session: dict) -> tuple:
    """What deserves a change flag: what COMMITS, not prose."""
    return (session.get("title"), session.get("workout"), session.get("status"),
            session.get("distance_target_km"), session.get("distance_cap_km"),
            session.get("rpe_min"), session.get("rpe_max"), session.get("demanding"),
            session.get("transition"))


def _changed_session_fields(previous: dict | None, current: dict,
                            as_of: str) -> dict[str, set[str]]:
    before, after = _revision_index(previous), _revision_index(current)
    changed: dict[str, set[str]] = {}
    for session_id in before.keys() | after.keys():
        old, new = before.get(session_id), after.get(session_id)
        relevant_dates = [session["date"] for session in (old, new) if session]
        if not relevant_dates or max(relevant_dates) < as_of:
            continue
        if old is None:
            changed[session_id] = {"added"}
            continue
        if new is None:
            changed[session_id] = {"removed"}
            continue
        # `done` is not an intent: `load_plan` sets it from the cycle, and
        # `intention_errors` requires `planned` on write. A session done earlier today
        # must therefore not count as "changed", or it would demand an impossible
        # justification and block every revision of the day.
        if old.get("status") == "done" and new.get("status") == "planned":
            old = {**old, "status": "planned"}
        fields = {name for name in CHANGE_FIELDS if name not in {"added", "removed"}
                  and old.get(name) != new.get(name)}
        if fields:
            changed[session_id] = fields
    return changed


def restrictions(old: dict, new: dict | None) -> list[str]:
    """What, from one revision to the next, REMOVES from a future session.

    Adding, moving or lengthening is not one: only restriction needs a typed premise.
    An already cancelled session is no longer restricted.
    """
    if old.get("status") == "cancelled":
        return []
    if new is None:
        return ["removed"]
    if new.get("status") == "cancelled":
        return ["cancelled"]
    found = []
    old_cap, new_cap = old.get("distance_cap_km"), new.get("distance_cap_km")
    if new_cap is not None and (
            (old_cap is not None and new_cap < old_cap)
            or (old_cap is None and old.get("distance_target_km") is not None
                and new_cap < old["distance_target_km"])):
        found.append("distance cap lowered")
    old_target, new_target = old.get("distance_target_km"), new.get("distance_target_km")
    if old_target is not None and new_target is not None and new_target < old_target:
        found.append("distance target lowered")
    old_minutes, new_minutes = old.get("duration_cap_min"), new.get("duration_cap_min")
    if old_minutes is not None and new_minutes is not None and new_minutes < old_minutes:
        found.append("duration cap lowered")
    if old.get("workout") in HARD_WORKOUTS and new.get("workout") not in HARD_WORKOUTS:
        found.append(f"{old['workout']} replaced by {new['workout']}")
    old_strength, new_strength = old.get("strength") or {}, new.get("strength") or {}
    if set(new_strength.get("drop") or []) - set(old_strength.get("drop") or []):
        found.append("exercise dropped from strength")
    if new_strength.get("half_dose") and not old_strength.get("half_dose"):
        found.append("strength at half dose")
    return found


def _restriction_errors(session_id: str, entry: dict, cuts: list[str],
                        evidence: list[str]) -> list[str]:
    restriction = entry.get("restriction")
    if not restriction:
        return [
            f"changes.{session_id}.restriction: restriction ({', '.join(cuts)}) without a "
            "typed premise. Name the rulebook rule that imposes it (`rule`: "
            f"{', '.join(restriction_rules())}) and the fact that triggers it (`fact`: an "
            "exact entry of evidence). Without a premise, the session stays."]
    fact = restriction.get("fact") or ""
    if fact not in evidence:
        return [f"changes.{session_id}.restriction.fact must copy an entry of evidence "
                "exactly."]
    if restriction.get("rule") == REQUEST_RULE:
        if not _REQUEST_TRIGGER.match(fact):
            return [f"changes.{session_id}.restriction: rule=request requires a message from "
                    "the athlete (messenger:, request:, availability:)."]
    elif not _FACT_TRIGGER.match(fact):
        return [f"changes.{session_id}.restriction: rule={restriction.get('rule')} applies to "
                "an observed fact (observation:, strava:, medical:, gate:, exposure:, "
                "incident:, strength:), not to an intent."]
    return []


_AUTHORITATIVE_TRIGGER = re.compile(
    r"^(?:observation|exposure|messenger|medical|availability|gate|request|incident|strength|strava):"
)


def decision_errors(revision: dict, where: str = "revision") -> list[str]:
    """A new revision says what settled it and, for a judgment, what it set aside.

    Afterwards nobody can rebuild the options that were dropped, so the register is
    written at the moment of deciding or not at all.
    """
    decision = revision.get("decision")
    if not isinstance(decision, dict):
        return [f"{where}.decision: missing -- `by` (rule, judgment or request), `rule` "
                "when by is rule, and `alternatives`: the options set aside, each with "
                "`option` and `rejected_because`"]
    rule = decision.get("rule")
    known = [entry for entry in restriction_rules() if entry != REQUEST_RULE]
    if decision.get("by") == Decider.RULE and rule and rule not in known:
        return [f"{where}.decision.rule: {rule!r} unknown (expected: {', '.join(known)})"]
    return []


def change_evidence_errors(previous: dict | None, current: dict) -> list[str]:
    """Each future change names its evidence and the rule that turns it into a decision;
    a restriction adds its typed `restriction`."""
    if previous is None:
        return []
    as_of = str(current.get("computed_at") or now_stamp())[:10]
    expected = _changed_session_fields(previous, current, as_of)
    entries = current.get("changes")
    if not expected:
        return ["changes: no future prescription changes; remove these justifications."] \
            if entries else []
    if not isinstance(entries, list) or not entries:
        return [
            "changes required: every modified future session names the fields it touches, "
            "an exact entry of evidence (`trigger`) and the rule applied."]
    problems: list[str] = []
    before, after = _revision_index(previous), _revision_index(current)
    by_session: dict[str, dict] = {}
    for entry in entries:
        session_id = entry.get("session_id") if isinstance(entry, dict) else None
        if not session_id or session_id in by_session:
            problems.append(f"changes: session_id missing or duplicated ({session_id!r}).")
            continue
        by_session[session_id] = entry
    for session_id, fields in expected.items():
        entry = by_session.get(session_id)
        if entry is None:
            problems.append(f"changes: no justification for {session_id}.")
            continue
        declared = set(entry.get("fields") or [])
        if declared != fields:
            problems.append(
                f"changes.{session_id}.fields: expected {sorted(fields)}, got {sorted(declared)}.")
        trigger = entry.get("trigger") or ""
        if trigger not in (current.get("evidence") or []):
            problems.append(
                f"changes.{session_id}.trigger must copy an entry of evidence exactly.")
        elif not _AUTHORITATIVE_TRIGGER.match(trigger):
            problems.append(
                f"changes.{session_id}.trigger is not a typed authoritative piece of evidence.")
        if not str(entry.get("rule") or "").strip():
            problems.append(f"changes.{session_id}.rule is empty.")
        if not str(entry.get("context") or "").strip():
            problems.append(f"changes.{session_id}.context is empty.")
        cuts = restrictions(before[session_id], after.get(session_id)) \
            if session_id in before else []
        if cuts:
            problems += _restriction_errors(session_id, entry, cuts,
                                      current.get("evidence") or [])
    extras = sorted(set(by_session) - set(expected))
    if extras:
        problems.append("changes: no future change for " + ", ".join(extras) + ".")
    return problems


def revision_diff(previous: dict | None, current: dict) -> list[str]:
    """What moved between two revisions, one line per touched session."""
    before, after = _revision_index(previous), _revision_index(current)
    lines = []
    for session_id, session in after.items():
        old = before.get(session_id)
        values = {"session": session_id, "day": _day_label(session["date"]),
                  "title": session["title"]}
        if old is None:
            lines.append(text.DIFF["added"].format(**values))
        elif old["date"] != session["date"]:
            lines.append(text.DIFF["moved"].format(**values, old_day=_day_label(old["date"])))
        elif session["status"] == "cancelled" and old["status"] != "cancelled":
            lines.append(text.DIFF["cancelled"].format(**values))
        elif _comparable(old) != _comparable(session):
            lines.append(text.DIFF["modified"].format(**values))
    for session_id, session in before.items():
        if session_id not in after:
            lines.append(text.DIFF["removed"].format(session=session_id,
                                                     day=_day_label(session["date"])))
    return lines


def revision_week_lines(week: str, revision: dict) -> list[str]:
    """The resulting week, one day per line, empty and cancelled days included."""
    monday = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 1)
    by_day: dict[str, list[dict]] = {}
    for session in revision.get("sessions", []):
        by_day.setdefault(session["date"], []).append(session)
    lines = []
    for offset in range(7):
        day = monday + dt.timedelta(days=offset)
        same = by_day.get(day.isoformat(), [])
        sessions = " + ".join(
            s["title"] + ("" if s["status"] == "planned"
                          else text.SESSION_STATUS.format(status=s["status"]))
            for s in same) or text.NO_SESSION
        lines.append(text.WEEK_DAY.format(day=_day_label(day.isoformat()), sessions=sessions))
    return lines


def revision_readback(week: str, previous: dict | None, current: dict) -> str:
    """What the CLI returns to the coach: the diff, then the week as it is."""
    since = f"r{previous['revision']}" if previous else text.FIRST_REVISION
    diff = revision_diff(previous, current) or [text.NOTHING_CHANGED]
    block = [
        text.READBACK_SAVED.format(week=week, revision=current["revision"]),
        "",
        text.READBACK_CHANGES.format(since=since),
        *(f"- {line}" for line in diff),
        "",
        text.READBACK_WEEK,
        *(f"- {line}" for line in revision_week_lines(week, current)),
    ]
    if current.get("volume_target_km") is not None:
        block.append(text.READBACK_VOLUME.format(km=f"{current['volume_target_km']:g}"))
    orphans = orphan_sessions(previous, current)
    if orphans:
        block += ["", text.READBACK_ATTENTION, *(f"- {line}" for line in orphans)]
    block += ["", text.READBACK_CHECK]
    return "\n".join(block)


def orphan_sessions(previous: dict | None, current: dict) -> list[str]:
    """The days THIS revision leaves with nothing to do.

    Not a fault: giving up a session is an ordinary coaching decision. But it is easy to
    miss in every rendering -- a cancelled session leaves `scheduled()`, hence the text
    message, the card and the hot-cache. The coach therefore receives it named, with the
    accompanying question.
    """
    before = _revision_index(previous)
    by_day: dict[str, list[dict]] = {}
    for session in current.get("sessions", []):
        by_day.setdefault(session["date"], []).append(session)
    orphans = []
    for stamp, same in sorted(by_day.items()):
        if any(s["status"] != "cancelled" for s in same):
            continue
        fresh = [s for s in same
                  if before.get(s["id"], {}).get("status") != "cancelled"]
        if fresh:
            orphans.append(text.ORPHAN_DAY.format(
                day=_day_label(stamp), titles=", ".join(s["title"] for s in fresh)))
    return orphans
