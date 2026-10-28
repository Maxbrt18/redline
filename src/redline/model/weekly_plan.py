"""Validation and rendering of the canonical weekly plans stored in PostgreSQL.

The Obsidian journals are read-only projections of them.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from itertools import pairwise

from redline.coach.fr import session_labels
from redline.coach.fr.calendar import DAYS as WEEKDAYS

from . import paths, rulebook, session_kinds, transitions, typography
from . import programme as strength_programme
from .notes import NoteError
from .programme import GATE_SCHEMA as STRENGTH_GATE_SCHEMA
from .schema import Schema, SchemaError, field

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


STRENGTH_SPEC_SCHEMA = Schema("modulation de renforcement", [
    field("loads", list, required=False, maximum=20,
          item=field("charge", dict, schema=strength_programme.PRESCRIBED_LOAD_SCHEMA)),
    field("variant", str, required=False,
          note="une variante déclarée par la fiche de séance"),
    field("half_dose", bool, required=False),
    field("drop", list, required=False, item=field("slug", str)),
    field("note", str, required=False, maximum=300),
    field("duration_min", int, required=False, minimum=5, maximum=120),
    field("gate", dict, required=False, schema=STRENGTH_GATE_SCHEMA),
])

SESSION_SCHEMA = Schema("séance planifiée", [
    field("id", str, pattern=r"[a-z0-9][a-z0-9-]+"),
    # Human-facing text is part of the persisted transaction. Renderers must
    # never maintain a second, incomplete taxonomy of workout labels.
    field("title", str, minimum=1),
    field("date", str, pattern=DATE_RE, note="AAAA-MM-JJ"),
    field("start_time", str, required=False, pattern=TIME_RE,
          note="HH:MM, heure locale de l'athlète ; absent si le créneau n'est pas fixé"),
    field("kind", str, choices=KINDS),
    field("workout", str, choices=WORKOUTS),
    field("routine", str, required=False, pattern=r"[a-z0-9][a-z0-9-]{0,39}",
          note="la routine de renfo, telle que sa fiche la déclare ; seulement pour strength"),
    field("status", str, choices=STATUSES),
    field("distance_target_km", float, required=False, minimum=0),
    field("distance_cap_km", float, required=False, minimum=0),
    field("duration_cap_min", int, required=False, minimum=1),
    field("rpe_min", int, required=False, minimum=0, maximum=10),
    field("rpe_max", int, required=False, minimum=0, maximum=10),
    field("hr_zone", str, required=False),
    field("hr_cap_bpm", int, required=False, minimum=80, maximum=220,
          note="plafond de FC MOYENNE de la séance, en bpm : le chiffre que hr_zone "
               "annonce en texte ; obligatoire à l'écriture avec hr_zone"),
    field("cadence_target", int, required=False, minimum=120, maximum=220),
    field("demanding", bool),
    field("transition", str, required=False, pattern=rulebook.SLUG,
          note="nature qui sort des 30 derniers jours (garde-fou n°8), l'une de celles "
               "du règlement ; absent pour une séance déjà connue"),
    field("location_label", str, required=False),
    field("carbs_target_g_h", int, required=False, minimum=0, maximum=150,
          note="cible digestive expérimentale en grammes de glucides par heure"),
    field("strength", dict, required=False, schema=STRENGTH_SPEC_SCHEMA,
          note="modulations typées ; la séance et les exercices viennent du workout"),
    field("note", str, required=False),
    field("sources", list, minimum=1, item=field("source", str)),
])

GATE_WHEN = ("avant", "apres")


# A dated condition is typed so it cannot be prescribed around once due.
#
# `when` is the central question and is asked at write time. A dated gate is one of two
# very different things:
#
#   avant  -- a PRECONDITION. Its answer exists before the sessions it holds, and while
#             it is missing those sessions are not prescribed.
#   apres  -- an OBSERVATION. Its answer exists only once the session is done. It
#             informs what follows and holds nothing, because it cannot hold anything
#             without waiting on itself.
#
# An observation written as a precondition would hold the very session it asks about.
# A gate must decide, and "avant" requires a `holds` that NAMES the held sessions.
# Legacy rows were normalized once in the database, so the current model has no
# historical dialect to interpret.
GATE_SCHEMA = Schema("contrôle daté", [
    field("id", str, pattern=r"[a-z0-9][a-z0-9-]+"),
    field("due", str, pattern=DATE_RE, note="AAAA-MM-JJ"),
    field("when", str, choices=GATE_WHEN,
          note="avant = pré-condition, retient les séances de `holds` ; "
               "apres = observation, ne retient rien"),
    field("holds", list, required=False, item=field("séance", str),
          note="les identifiants de séance que ce contrôle retient — "
               "obligatoire si when=avant, interdit si when=apres"),
    field("question", str, minimum=10, note="ce qu'il faut demander à l'athlète ce jour-là"),
    field("on_fail", str, minimum=3, note="ce que devient le programme si la réponse est mauvaise"),
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
#: `rule` cannot be verified; a rulebook identifier and an `evidence` fact can. `demande`
#: covers what the athlete asked for directly.
REQUEST_MOTIF = "demande"


def motif_rules() -> tuple[str, ...]:
    """The motives of a restriction: the instance rulebook's rules, or a request."""
    return (*(rule.id for rule in rulebook.current().rules), REQUEST_MOTIF)


#: Prefixes of a FACT: what the ledger, Strava, an opinion or a gate said.
#: A rulebook rule applies only to a fact; a request comes from a message.
_FACT_TRIGGER = re.compile(r"^(?:observation|exposure|medical|gate|incident|strength|strava):")
_REQUEST_TRIGGER = re.compile(r"^(?:messenger|request|availability):")

def _known_rule(motif: dict) -> list[str]:
    """The named rule exists in the instance rulebook, read at validation time."""
    if motif["regle"] in motif_rules():
        return []
    return [f"champ « regle » : {motif['regle']!r} inconnu "
            f"(attendus : {', '.join(motif_rules())})"]


MOTIF_SCHEMA = Schema("motif d'une restriction", [
    field("regle", str, minimum=1,
          note="l'identifiant du règlement qui impose la restriction, ou demande"),
    field("fait", str, minimum=3,
          note="une entrée exacte de evidence : le fait qui déclenche la règle"),
], check=_known_rule)

CHANGE_SCHEMA = Schema("preuve d'un changement de séance", [
    field("session_id", str, pattern=r"[a-z0-9][a-z0-9-]+"),
    field("fields", list, minimum=1,
          item=field("champ", str, choices=CHANGE_FIELDS)),
    field("trigger", str, minimum=3,
          note="une entrée exacte de evidence qui déclenche ce changement"),
    field("context", str, minimum=3,
          note="le contexte ou l'intention confirmée qui donne son sens à la preuve"),
    field("rule", str, minimum=3,
          note="la règle de coaching appliquée à cette preuve"),
    # Optional on read: older revisions do not carry it.
    # Required on write for any restriction (`change_evidence_errors`).
    field("motif", dict, required=False, schema=MOTIF_SCHEMA,
          note="obligatoire si la séance est retirée, annulée ou réduite"),
])

REVISION_SCHEMA = Schema("révision hebdomadaire", [
    field("revision", int, minimum=1),
    field("computed_at", str, pattern=STAMP_RE, note="date-heure ISO 8601"),
    field("reason", str, minimum=3),
    field("evidence", list, minimum=1, item=field("preuve", str)),
    field("constraints", list, item=field("contrainte", str)),
    # Optional so history can be read; required at the `revise` boundary as soon as a
    # future prescription changes (see `change_evidence_errors`).
    field("changes", list, required=False,
          item=field("changement", dict, schema=CHANGE_SCHEMA)),
    # Optional: older revisions carry none, and a week that conditions nothing on a date
    # is the normal case.
    field("gates", list, required=False,
          item=field("contrôle", dict, schema=GATE_SCHEMA)),
    # Optional on read: older revisions do not carry it.
    # Required on write once the week is running (`typed_target_errors`).
    field("volume_target_km", float, required=False, minimum=1, maximum=200,
          note="volume couru visé sur la semaine entière, en km"),
    field("sessions", list, minimum=1,
          item=field("session", dict, schema=SESSION_SCHEMA)),
])

WEEKLY_PLAN_SCHEMA = Schema("plan hebdomadaire", [
    field("week", str, pattern=WEEK_RE, note="AAAA-WNN"),
    field("revisions", list, minimum=1,
          item=field("revision", dict, schema=REVISION_SCHEMA)),
])


def now_stamp() -> str:
    """The real instant of the write, in local time with its offset.

    A single function, patchable in tests and called from one place only:
    `week-plan.py revise`. The `computed_at` field used to be filled by the model, which
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
_INTERNAL_RE = re.compile(r"\b(?:rev|r[ée]vision)\.?\s*\d", re.IGNORECASE)


def _text_errors(text: str, where: str, label: str, gate_ids: list[str]) -> list[str]:
    """`title`/`note`: what the athlete reads, judged as such."""
    problems = []
    for gate_id in gate_ids:
        if gate_id in text:
            problems.append(
                f"{where} : {label} cite le contrôle daté « {gate_id} » — ce texte part "
                "sur le téléphone de l'athlète et sur la carte, la machinerie interne "
                "reste dans reason/evidence")
    match = _INTERNAL_RE.search(text)
    if match:
        problems.append(
            f"{where} : {label} cite une révision (« {match.group()} ») — dis ce que la "
            "séance devient, pas d'où la consigne vient")
    return problems


def written_text_errors(revision: dict, where: str = "revision") -> list[str]:
    """The forbidden marks in the text of ONE revision about to be written.

    Called by `week-plan.py revise` on the NEW revision, never by `validate()`. History is
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
        if gate.get("when") == "avant":
            problems.append(
                f"{where}.gate[{gate.get('id', '?')}] : une pré-condition ne retient plus de "
                "séance. Écris la condition dans la note de la séance (si la trace est nette "
                "ce matin : repos) ; le carnet alerte sur une trace nette. Un contrôle sert "
                "seulement à une observation (when=apres).")
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
        f"{where}.{session.get('id', '?')} : status=done appartient au cycle de "
        "séance. Garde status=planned ; le rendu ajoute 'fait' depuis PostgreSQL."
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
        problems.append(f"{where}.volume_target_km : manquant — le volume couru visé de "
                        "la semaine entière, en km (séances déjà faites comprises), celui "
                        "que les contraintes annoncent en prose")
    # Same for HR: "Z1-Z2, 130-157 bpm" is readable but not comparable. The logbook sets
    # the fact (Strava average HR) against this cap. It stays out of CHANGE_FIELDS: it
    # duplicates `hr_zone`, whose every change already requires evidence, and the first
    # addition on an existing session is not a new prescription.
    problems.extend(
        f"{where}.{session.get('id', '?')}.hr_cap_bpm : manquant — le plafond de FC "
        f"MOYENNE en bpm que hr_zone « {session['hr_zone']} » annonce en texte"
        for session in revision.get("sessions") or []
        if session.get("status") != "cancelled" and session.get("hr_zone")
        and session.get("hr_cap_bpm") is None)
    return problems


def _session_errors(session: dict, where: str) -> list[str]:
    problems = []
    target, cap = session.get("distance_target_km"), session.get("distance_cap_km")
    if target is not None and cap is not None and target > cap:
        problems.append(f"{where} : cible {target:g} km au-dessus du plafond {cap:g} km")
    lo, hi = session.get("rpe_min"), session.get("rpe_max")
    if lo is not None and hi is not None and lo > hi:
        problems.append(f"{where} : RPE minimum {lo} au-dessus du maximum {hi}")
    workout = session.get("workout")
    duration = session.get("duration_cap_min", 0)
    if workout in session_kinds.BY_SLUG and session.get("kind") != session_kinds.family(workout):
        problems.append(f"{where} : kind doit valoir {session_kinds.family(workout)} "
                        f"pour {workout}")
    expected_hard = (workout in HARD_WORKOUTS
                     or (workout == session_kinds.EASY and duration > 75))
    if session.get("demanding") != expected_hard:
        problems.append(
            f"{where} : demanding doit valoir {str(expected_hard).lower()} pour {workout}")
    if session.get("kind") == "run" and target is None and cap is None and duration == 0:
        problems.append(f"{where} : une course exige une cible, un plafond ou une durée")
    if session.get("transition") and session.get("kind") == "rest":
        problems.append(f"{where} : un repos n'est pas une transition")
    if session.get("carbs_target_g_h") is not None and workout not in session_kinds.FUELLED:
        problems.append(f"{where} : une cible digestive ne se teste que sur "
                        + " ou ".join(sorted(session_kinds.FUELLED)))
    is_strength = workout == session_kinds.STRENGTH
    for name in ("strength", "routine"):
        if session.get(name) is not None and not is_strength:
            problems.append(f"{where} : {name} ne vaut que pour une séance strength")
    if is_strength and session.get("routine") is None:
        problems.append(f"{where} : routine manque — la routine de renfo que sa fiche déclare")
    if is_strength and session.get("strength") is None:
        problems.append(
            f"{where} : strength manque. Même sans modulation, écris strength={{}} "
            "pour figer la prescription de carte")
    return problems


def _strength_programme_errors(session: dict, where: str) -> list[str]:
    """Resolve a strength prescription against its real programme.

    The schema proves the shape of ``strength``; only ``Programme.build`` can prove that
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
        strength_programme.load(paths.vault_root()).build(selection)
    except Exception as exc:
        return [f"{where}.strength : prescription impossible — {exc}"]
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

    if when == "apres" and holds:
        problems.append(
            f"{where} : when=apres avec `holds` — une observation ne retient rien. Sa "
            "réponse arrive APRÈS les séances, elle ne peut pas être leur condition.")
    if when == "avant" and not holds:
        problems.append(
            f"{where} : when=avant sans `holds` — nomme les séances que ce contrôle "
            "retient. Un contrôle qui retient « tout » retient aussi celle dont il "
            "attend la réponse.")

    known = {session["id"]: session["date"] for session in sessions}
    for session_id in holds or []:
        if session_id not in known:
            problems.append(
                f"{where} : `holds` cite « {session_id} », qui n'est pas une séance de "
                "cette révision")
            continue
        # Holding a session already past when the question is asked has no effect: it
        # happened. That signals an observation was intended.
        if known[session_id] < gate["due"]:
            problems.append(
                f"{where} : retient « {session_id} » du {known[session_id]}, antérieure "
                f"à l'échéance {gate['due']} — cette séance aura déjà eu lieu. Une "
                "condition qui arrive après sa séance est une observation (when=apres).")
    return problems


def semantic_errors(plan: dict, where: str = "weekly_plan",
                    today: dt.date | None = None) -> list[str]:
    """Cross-revision and coaching invariants not expressible field by field."""
    problems = WEEKLY_PLAN_SCHEMA.errors(plan, where)
    if problems:
        return problems
    week = plan["week"]
    allowed = _week_dates(week)
    # Strength resolves against the current programme: this is a prescription guard.
    # A closed week has nothing left to prescribe, and the programme may have changed since
    # -- judging it that way would make its archive unreadable.
    prescribable = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 7) \
        >= (today or dt.date.today())
    revisions = plan["revisions"]
    numbers = [revision["revision"] for revision in revisions]
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"{where} : révisions non contiguës ({numbers})")
    for revision in revisions:
        prefix = f"{where}.revision[{revision['revision']}]"
        gates = revision.get("gates") or []
        gate_ids = [gate["id"] for gate in gates]
        written_on = revision["computed_at"][:10]
        sessions = revision["sessions"]
        ids = [session["id"] for session in sessions]
        if len(ids) != len(set(ids)):
            problems.append(f"{prefix} : identifiants de séance dupliqués")
        problems.extend(transitions.per_week_errors(sessions, prefix))
        for session in sessions:
            if session["date"] not in allowed:
                problems.append(
                    f"{prefix}.{session['id']} : date {session['date']} hors de {week}")
            problems.extend(_session_errors(session, f"{prefix}.{session['id']}"))
            if revision is revisions[-1] and prescribable:
                problems.extend(_strength_programme_errors(
                    session, f"{prefix}.{session['id']}"))
            for label in ("title", "note"):
                if session.get(label):
                    problems.extend(_text_errors(
                        session[label], f"{prefix}.{session['id']}", label, gate_ids))
        if revision is revisions[-1] and prescribable:
            problems.extend(_spacing_errors(sessions, prefix))

        if len(gate_ids) != len(set(gate_ids)):
            problems.append(f"{prefix} : identifiants de contrôle daté dupliqués")
        for gate in gates:
            where_gate = f"{prefix}.gate[{gate['id']}]"
            # A gate belongs to the week that carries it. A condition landing past Sunday
            # has a different owner -- `redline.deadlines`, whose overdue check already
            # exists -- otherwise it would sit in a plan nothing looks at once the week is
            # over.
            if gate["due"] not in allowed:
                problems.append(
                    f"{where_gate} : échéance {gate['due']} hors de {week} — "
                    "une condition au-delà du dimanche va dans PostgreSQL via "
                    "scripts/fact-write.py deadline")
            if gate["due"] < written_on:
                problems.append(
                    f"{where_gate} : échéance {gate['due']} antérieure au calcul "
                    f"de la révision ({written_on})")
            problems.extend(_gate_typing_errors(gate, where_gate, sessions))

        hard_days = sorted({dt.date.fromisoformat(s["date"]) for s in sessions
                            if s["status"] != "cancelled" and s["demanding"]})
        for first, second in pairwise(hard_days):
            if (second - first).days == 1:
                problems.append(
                    f"{prefix} : jours exigeants consécutifs {first} et {second}")
    return problems


def _spacing_errors(sessions: list[dict], prefix: str) -> list[str]:
    """The gaps each strength routine declares (`min_gap_h`), checked day by day."""
    try:
        routines = strength_programme.load(paths.vault_root()).sessions
    except NoteError:
        return []   # the programme error itself is reported by _strength_programme_errors
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
                problems.append(f"{prefix} : routine {session['routine']} à moins de "
                                f"{hours} h d'une séance {other['workout']} ({other['date']})")
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
    """What is on the programme: everything except what was cancelled.

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
    return ([gate for gate in gates if gate["when"] == "avant"],
            [gate for gate in gates if gate["when"] == "apres"])


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
    return [f"contrôle daté du {gate['due']} non levé — DEMANDE À L'ATHLÈTE AVANT "
            f"TOUTE PRESCRIPTION : {gate['question']} ; à défaut : {gate['on_fail']}. "
            "Le contrôle ne se lève qu'en enregistrant une révision dont le motif "
            "porte SA réponse, jamais en le retirant du programme."
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
        return f"cible {target:g} km, plafond {cap:g} km"
    if cap is not None:
        return f"plafond {cap:g} km"
    if target is not None:
        return f"cible {target:g} km"
    if session.get("duration_cap_min"):
        return f"plafond {session['duration_cap_min']} min"
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
        details.append(f"RPE {session['rpe_min']}-{high}")
    if session.get("hr_zone"):
        details.append(session["hr_zone"])
    if session.get("cadence_target"):
        details.append(f"cadence ~{session['cadence_target']}{cadence_unit}")
    if session.get("carbs_target_g_h") is not None:
        details.append(f"glucides {session['carbs_target_g_h']} g/h")
    if session.get("note"):
        details.append(session["note"])
    return [part for part in details if part]


def _session_line(session: dict) -> str:
    day = dt.date.fromisoformat(session["date"])
    label = WEEKDAYS[day.weekday()].capitalize()
    suffix = " · ".join(details_of(session, cadence_unit=" pas/min"))
    status = "fait" if session["status"] == "done" else session["status"]
    if session.get("transition"):
        status += ", transition " + transitions.label(session["transition"])
    return f"- **{label} {day:%d/%m}** : {session['title']} ({status})" + (f" · {suffix}" if suffix else "")


def render_journal(plan: dict) -> str:
    revision = active(plan)
    lines = [f"## Programme actif — révision {revision['revision']}", "",
             f"Calculé le `{revision['computed_at']}` · Motif : {revision['reason']}", ""]
    if revision.get("volume_target_km") is not None:
        lines += [f"Volume couru visé : ~{revision['volume_target_km']:g} km", ""]
    lines.extend(_session_line(session) for session in revision["sessions"])
    gates = revision.get("gates") or []
    # The journal must not claim that ALL gates refuse the program: an observation
    # (`apres`) is asked and holds nothing. A view that misstates the rule is worse than
    # no view.
    for kind, titre in (
        ("avant", "**Pré-conditions** (aucune séance retenue ne se prescrit tant "
                  "qu'une révision ne porte pas la réponse) :"),
        ("apres", "**Observations** (à poser le jour dit ; elles ne retiennent "
                  "aucune séance) :"),
    ):
        due = [gate for gate in gates if gate["when"] == kind]
        if not due:
            continue
        lines += ["", titre, ""]
        lines.extend(f"- **{gate['due'][8:]}/{gate['due'][5:7]}** : {gate['question']} "
                     f"→ à défaut, {gate['on_fail']}" for gate in due)
    attention = revision.get("constraints") or []
    if attention:
        lines += ["", "**Points d'attention** : " + " · ".join(attention)]
    lines += ["", "Ce bloc est généré depuis le programme typé canonique. Ne pas l'éditer à la main."]
    return "\n".join(lines)


MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre")


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
    span = (f"{monday.day}-{sunday.day} {MONTHS[sunday.month - 1]}"
            if monday.month == sunday.month else
            f"{monday.day} {MONTHS[monday.month - 1]} - {sunday.day} {MONTHS[sunday.month - 1]}")
    return "\n".join([
        "---", "type: bilan", f"semaine: {week}", f"maj: {today.isoformat()}", "---", "",
        f"# Bilan {week} ({span})", "",
        "> Les séances restent dans le registre. Le programme opérationnel est la révision "
        "PostgreSQL",
        f"> `{week}` ; ce journal recevra la lecture de fin de semaine.", "",
        f"<!-- {BLOCK}_START -->", f"<!-- {BLOCK}_END -->", "",
        "## Intention d'ouverture", "",
        "## Ressenti de l'athlète", "",
        "## Bilan de clôture", "",
        "## Lecture du coach", ""])


def render_hot_cache(plan: dict) -> str:
    revision = active(plan)
    sessions = scheduled(revision["sessions"])
    compact = "; ".join(
        f"{WEEKDAYS[dt.date.fromisoformat(s['date']).weekday()][:3]} {s['title']}"
        + (f" ≤{s['distance_cap_km']:g} km" if s.get("distance_cap_km") is not None else "")
        for s in sessions)
    return (f"- **Semaine en cours — {plan['week']} · plan typé r{revision['revision']}** : "
            f"{compact}. PostgreSQL → `athlete/journal/{plan['week'].lower()}.md`. "
            "Macro-plan jamais prescriptible.")


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
    return f"{WEEKDAYS[day.weekday()][:3]} {day:%d/%m}"


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
        return ["retirée"]
    if new.get("status") == "cancelled":
        return ["annulée"]
    found = []
    old_cap, new_cap = old.get("distance_cap_km"), new.get("distance_cap_km")
    if new_cap is not None and (
            (old_cap is not None and new_cap < old_cap)
            or (old_cap is None and old.get("distance_target_km") is not None
                and new_cap < old["distance_target_km"])):
        found.append("plafond de distance abaissé")
    old_target, new_target = old.get("distance_target_km"), new.get("distance_target_km")
    if old_target is not None and new_target is not None and new_target < old_target:
        found.append("cible de distance abaissée")
    old_minutes, new_minutes = old.get("duration_cap_min"), new.get("duration_cap_min")
    if old_minutes is not None and new_minutes is not None and new_minutes < old_minutes:
        found.append("plafond de durée abaissé")
    if old.get("workout") in HARD_WORKOUTS and new.get("workout") not in HARD_WORKOUTS:
        found.append(f"{session_labels.short(old['workout'])} remplacée par "
                     f"{session_labels.short(new['workout'])}")
    old_strength, new_strength = old.get("strength") or {}, new.get("strength") or {}
    if set(new_strength.get("drop") or []) - set(old_strength.get("drop") or []):
        found.append("exercice retiré du renfo")
    if new_strength.get("half_dose") and not old_strength.get("half_dose"):
        found.append("renfo en demi-dose")
    return found


def _motif_errors(session_id: str, entry: dict, cuts: list[str],
                  evidence: list[str]) -> list[str]:
    motif = entry.get("motif")
    if not motif:
        return [
            f"changes.{session_id}.motif : restriction ({', '.join(cuts)}) sans motif "
            "typé. Nomme la règle du règlement qui l'impose (`regle` : "
            f"{', '.join(motif_rules())}) et le fait qui la déclenche (`fait` : une entrée "
            "exacte de evidence). Sans prémisse, la séance reste."]
    fait = motif.get("fait") or ""
    if fait not in evidence:
        return [f"changes.{session_id}.motif.fait doit recopier exactement une entrée "
                "de evidence."]
    if motif.get("regle") == REQUEST_MOTIF:
        if not _REQUEST_TRIGGER.match(fait):
            return [f"changes.{session_id}.motif : regle=demande exige un message de "
                    "l'athlète (messenger:, request:, availability:)."]
    elif not _FACT_TRIGGER.match(fait):
        return [f"changes.{session_id}.motif : regle={motif.get('regle')} s'applique à "
                "un fait observé (observation:, strava:, medical:, gate:, exposure:, "
                "incident:, strength:), pas à une intention."]
    return []


_AUTHORITATIVE_TRIGGER = re.compile(
    r"^(?:observation|exposure|messenger|medical|availability|gate|request|incident|strength|strava):"
)


def change_evidence_errors(previous: dict | None, current: dict) -> list[str]:
    """Each future change names its evidence and the rule that turns it into a decision;
    a restriction adds its typed `motif`."""
    if previous is None:
        return []
    as_of = str(current.get("computed_at") or now_stamp())[:10]
    expected = _changed_session_fields(previous, current, as_of)
    entries = current.get("changes")
    if not expected:
        return ["changes : aucune prescription future ne change ; retire ces justifications."] \
            if entries else []
    if not isinstance(entries, list) or not entries:
        return [
            "changes obligatoire : chaque séance future modifiée doit nommer les champs "
            "touchés, une preuve exacte de evidence (`trigger`) et la règle appliquée."]
    problems: list[str] = []
    before, after = _revision_index(previous), _revision_index(current)
    by_session: dict[str, dict] = {}
    for entry in entries:
        session_id = entry.get("session_id") if isinstance(entry, dict) else None
        if not session_id or session_id in by_session:
            problems.append(f"changes : session_id absent ou dupliqué ({session_id!r}).")
            continue
        by_session[session_id] = entry
    for session_id, fields in expected.items():
        entry = by_session.get(session_id)
        if entry is None:
            problems.append(f"changes : justification absente pour {session_id}.")
            continue
        declared = set(entry.get("fields") or [])
        if declared != fields:
            problems.append(
                f"changes.{session_id}.fields : attendu {sorted(fields)}, reçu {sorted(declared)}.")
        trigger = entry.get("trigger") or ""
        if trigger not in (current.get("evidence") or []):
            problems.append(
                f"changes.{session_id}.trigger doit recopier exactement une entrée de evidence.")
        elif not _AUTHORITATIVE_TRIGGER.match(trigger):
            problems.append(
                f"changes.{session_id}.trigger n'est pas une preuve autoritative typée.")
        if not str(entry.get("rule") or "").strip():
            problems.append(f"changes.{session_id}.rule est vide.")
        if not str(entry.get("context") or "").strip():
            problems.append(f"changes.{session_id}.context est vide.")
        cuts = restrictions(before[session_id], after.get(session_id)) \
            if session_id in before else []
        if cuts:
            problems += _motif_errors(session_id, entry, cuts,
                                      current.get("evidence") or [])
    extras = sorted(set(by_session) - set(expected))
    if extras:
        problems.append("changes : aucune modification future pour " + ", ".join(extras) + ".")
    return problems


def revision_diff(previous: dict | None, current: dict) -> list[str]:
    """What moved between two revisions, one line per touched session."""
    avant, apres = _revision_index(previous), _revision_index(current)
    lignes = []
    for session_id, session in apres.items():
        old = avant.get(session_id)
        if old is None:
            lignes.append(f"{session_id} : AJOUTÉE le {_day_label(session['date'])} "
                          f"({session['title']})")
        elif old["date"] != session["date"]:
            lignes.append(f"{session_id} : DÉPLACÉE du {_day_label(old['date'])} "
                          f"au {_day_label(session['date'])}")
        elif session["status"] == "cancelled" and old["status"] != "cancelled":
            lignes.append(f"{session_id} : ANNULÉE le {_day_label(session['date'])} "
                          f"({session['title']})")
        elif _comparable(old) != _comparable(session):
            lignes.append(f"{session_id} : MODIFIÉE le {_day_label(session['date'])} "
                          f"({session['title']})")
    for session_id, session in avant.items():
        if session_id not in apres:
            lignes.append(f"{session_id} : RETIRÉE de la révision "
                          f"(elle était le {_day_label(session['date'])})")
    return lignes


def revision_week_lines(week: str, revision: dict) -> list[str]:
    """The resulting week, one day per line, empty and cancelled days included."""
    monday = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 1)
    par_jour: dict[str, list[dict]] = {}
    for session in revision.get("sessions", []):
        par_jour.setdefault(session["date"], []).append(session)
    lignes = []
    for offset in range(7):
        day = monday + dt.timedelta(days=offset)
        same = par_jour.get(day.isoformat(), [])
        if not same:
            lignes.append(f"{_day_label(day.isoformat())} : AUCUNE SÉANCE")
            continue
        lignes.append(f"{_day_label(day.isoformat())} : " + " + ".join(
            s["title"] + ("" if s["status"] == "planned" else f" ({s['status']})")
            for s in same))
    return lignes


def revision_readback(week: str, previous: dict | None, current: dict) -> str:
    """What the CLI returns to the coach: the diff, then the week as it is."""
    depuis = f"r{previous['revision']}" if previous else "rien (première révision)"
    diff = revision_diff(previous, current) or [
        "AUCUNE séance ajoutée, annulée, déplacée ni modifiée. Si le message de l'athlète "
        "demandait un changement, cette révision ne le porte pas."]
    bloc = [
        f"{week} révision {current['revision']} enregistrée et validée.",
        "",
        f"Ce qui change depuis {depuis} :",
        *(f"- {ligne}" for ligne in diff),
        "",
        "RELIS LA SEMAINE AVANT DE RÉPONDRE. Elle est maintenant :",
        *(f"- {ligne}" for ligne in revision_week_lines(week, current)),
    ]
    if current.get("volume_target_km") is not None:
        bloc.append(f"- Volume couru visé : ~{current['volume_target_km']:g} km")
    orphelins = sessions_orphelines(previous, current)
    if orphelins:
        bloc += ["", "ATTENTION :", *(f"- {ligne}" for ligne in orphelins)]
    bloc += ["", "Chaque fait du message de l'athlète doit se retrouver ci-dessus. Un jour "
                 "marqué AUCUNE SÉANCE est un oubli tant que tu n'as pas vérifié qu'il "
                 "est voulu."]
    return "\n".join(bloc)


#: THE MOTIVE AND THE OBJECT ARE JUDGED TOGETHER. `semantic_errors` checks physiological
#: invariants -- spacing between strength sessions, consecutive demanding days -- but not
#: whether the revision DOES what it SAYS.
#:
#: The check is on days because that is the only claim of a motive that a program can
#: mechanically contradict. It leaves the two correct paths open: either the revision
#: forgets that day, or the motive names it without changing it -- and a circumstance
#: that changes nothing lives in `constraints`, not in the motive.
_JOURS_NOMMES = {nom: index for index, nom in enumerate(WEEKDAYS)}
_DATE_COURTE = re.compile(r"\b(\d{2})/(\d{2})\b")
_DATE_ISO = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _dates_nommees(text: str, week: str) -> set[str]:
    monday = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 1)
    semaine = {(monday + dt.timedelta(days=index)).isoformat() for index in range(7)}
    trouvees = set()
    lowered = text.lower()
    for nom, index in _JOURS_NOMMES.items():
        if re.search(rf"\b{nom}\b", lowered):
            trouvees.add((monday + dt.timedelta(days=index)).isoformat())
    for jour, mois in _DATE_COURTE.findall(text):
        candidate = f"{monday.year}-{mois}-{jour}"
        if candidate in semaine:
            trouvees.add(candidate)
    trouvees.update(stamp for stamp in _DATE_ISO.findall(text) if stamp in semaine)
    return trouvees & semaine


def _dates_touchees(previous: dict | None, current: dict) -> set[str]:
    avant, apres = _revision_index(previous), _revision_index(current)
    touchees = set()
    for session_id, session in apres.items():
        old = avant.get(session_id)
        if old is None or _comparable(old) != _comparable(session):
            touchees.add(session["date"])
        if old is not None and old["date"] != session["date"]:
            touchees.update({old["date"], session["date"]})
    touchees.update(session["date"] for session_id, session in avant.items()
                    if session_id not in apres)
    return touchees


def coherence_errors(week: str, previous: dict | None, current: dict) -> list[str]:
    """The motive names a day the object does not touch: one of the two is wrong."""
    if previous is None:      # a first revision touches the whole week
        return []
    nommees = _dates_nommees(current.get("reason") or "", week)
    manquantes = sorted(nommees - _dates_touchees(previous, current))
    return [
        f"revision {current['revision']} : le motif nomme le {_day_label(stamp)} mais "
        "aucune séance de ce jour n'est ajoutée, annulée, déplacée ni modifiée. Soit la "
        "révision oublie ce jour et il faut l'écrire, soit le motif le nomme sans le "
        "changer : retire-le du motif, ou porte la circonstance dans `constraints`."
        for stamp in manquantes]


def sessions_orphelines(previous: dict | None, current: dict) -> list[str]:
    """The days THIS revision leaves with nothing to do.

    Not a fault: giving up a session is an ordinary coaching decision. But it is easy to
    miss in every rendering -- a cancelled session leaves `scheduled()`, hence the text
    message, the card and the hot-cache. The coach therefore receives it named, with the
    accompanying question.
    """
    avant = _revision_index(previous)
    par_jour: dict[str, list[dict]] = {}
    for session in current.get("sessions", []):
        par_jour.setdefault(session["date"], []).append(session)
    orphelins = []
    for stamp, same in sorted(par_jour.items()):
        if any(s["status"] != "cancelled" for s in same):
            continue
        neuves = [s for s in same
                  if avant.get(s["id"], {}).get("status") != "cancelled"]
        if neuves:
            orphelins.append(
                f"{_day_label(stamp)} ne porte plus que des annulations "
                f"({', '.join(s['title'] for s in neuves)}) et aucune séance à la "
                "place. Voulu, ou remplaçante oubliée ?")
    return orphelins
