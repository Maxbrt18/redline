"""The strength-session link: one immutable snapshot, a card and a page to follow.

Links carry only an opaque capability (`/s#t=<token>`). The exact prescription lives
in PostgreSQL and the raw token never does.

Leaf module: standard library only, imports nothing from the package. The card script
uses it to write a snapshot sidecar, while the service alone mints capabilities.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import date
from pathlib import Path
from typing import Any

PAGE_PATH = "/s"
SIDECAR_SUFFIX = ".link"
BUTTON_TITLE = "Ouvrir la séance"   # Messenger caps a button title at 20 characters

NOTE_MAX = 300          # a free-text note the coach will read, not an essay
REPS_MAX_SETS = 24
LINK_TTL_SECONDS = 7 * 24 * 60 * 60
TOKEN_BYTES = 24             # 192 bits; the URL itself is the capability
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class ReportError(ValueError):
    """Le compte-rendu ne respecte pas le contrat de la page."""


class ExpiredLink(ValueError):
    """La capacité a dépassé sa durée d'utilisation."""


# ------------------------------------------------------------------------ URL

def new_token() -> str:
    """Capability put in the fragment; only its SHA-256 digest is persisted."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    if not TOKEN_RE.fullmatch(token):  # guards a future stdlib behaviour change
        raise RuntimeError("jeton de séance produit dans un format inattendu")
    return token


def token_digest(token: str) -> str:
    """Stable lookup key, or an empty string for a malformed capability."""
    if not TOKEN_RE.fullmatch(token or ""):
        return ""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def token_url(base_url: str, token: str) -> str:
    if not base_url or not token_digest(token):
        raise ValueError("URL de séance ou jeton invalide")
    return f"{base_url.rstrip('/')}{PAGE_PATH}#t={token}"


def expiring_snapshot(session: dict, *, now: int | None = None) -> dict:
    """Freeze a JSON-safe prescription and add the server-owned expiration."""
    try:
        snapshot = json.loads(json.dumps(session, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ReportError(f"session non sérialisable : {exc}") from exc
    snapshot["expires_at"] = (int(time.time()) if now is None else now) + LINK_TTL_SECONDS
    validate_session(snapshot, now=now)
    return snapshot


def sidecar_enabled() -> bool:
    """The card script only needs to know whether the service has a public base URL.

    The service, not the sandboxed agent, owns token generation and persistence.
    """
    return bool((os.environ.get("COACH_BASE_URL") or "").strip())


# -------------------------------------------------------------------- sidecar

def sidecar_for(image: Path) -> Path:
    """The link file riding alongside a card in the outbox."""
    return image.with_name(image.name + SIDECAR_SUFFIX)


def write_sidecar(image: Path, text: str, session: dict) -> Path:
    """The bubble text and exact prescription awaiting server-side token issuance.

    The bot turns it into a button only after the image has been delivered, so Claude
    never receives the capability and cannot create a valid link by itself.
    """
    path = sidecar_for(image)
    path.write_text(json.dumps({"text": text, "session": session}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def read_sidecar(image: Path) -> tuple | None:
    """(text, session snapshot), or None if there is no usable sidecar."""
    try:
        data = json.loads(sidecar_for(image).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    session = data.get("session")
    if not isinstance(session, dict) or not session.get("sid"):
        return None
    if not isinstance(session.get("exercises"), list) or not session["exercises"]:
        return None
    return str(data.get("text") or "Ta séance de renfo"), session


# --------------------------------------------------------------------- report

def validate_session(session: dict, *, now: int | None = None) -> None:
    """Refuse un snapshot expiré ou étranger au contrat du générateur."""
    sid = session.get("sid")
    workout = session.get("session")
    exercises = session.get("exercises")
    expires_at = session.get("expires_at")
    if not isinstance(sid, str) or not sid.strip() or len(sid) > 64:
        raise ReportError("session.sid invalide")
    if workout not in {"a", "b", "assimilation"}:
        raise ReportError("session.session invalide")
    if not isinstance(exercises, list) or not exercises or len(exercises) > 20:
        raise ReportError("session.exercises invalide")
    if type(expires_at) is not int:
        raise ReportError("session.expires_at invalide")
    if expires_at < (int(time.time()) if now is None else now):
        raise ExpiredLink("ce lien de séance a expiré")
    for index, exercise in enumerate(exercises):
        if not isinstance(exercise, dict):
            raise ReportError(f"session.exercises[{index}] invalide")
        slug = exercise.get("slug")
        if slug is not None and (not isinstance(slug, str) or len(slug) > 60):
            raise ReportError(f"session.exercises[{index}].slug invalide")


def _optional_int(value, low: int, high: int, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not low <= value <= high:
        raise ReportError(f"{field} : entier attendu entre {low} et {high}")
    return value


def _clean_text(value) -> str:
    text = _CONTROL.sub(" ", str(value or ""))
    return " ".join(text.split())[:NOTE_MAX]


def _sides_of(exercise) -> list:
    """The side of each slot, in the order the page walks them: ["d", "g", "d", ...].

    Read off the session -- which the route already holds -- rather than trusted
    from the report. The page posts its counts POSITIONALLY, blanks included, and
    that position is the only thing that says which leg a number belongs to.
    """
    dose = exercise.get("dose") if isinstance(exercise, dict) else None
    slots = dose.get("slots") if isinstance(dose, dict) else None
    if not isinstance(slots, list):
        return []      # no slots, no sides: every count lands in the sideless bucket
    return [str(slot.get("s") or "") for slot in slots if isinstance(slot, dict)]


def parse_report(report: dict, exercises: list, zones: dict[str, str] | None = None) -> dict:
    """Valide puis normalise le contrat posté par la page.

    Une valeur hors bornes est une requête invalide, pas une valeur à corriger en
    silence : transformer 99 en douleur 10 ou ``"false"`` en vrai fabriquerait un
    fait médical que l'athlète n'a pas saisi. La douleur arrive par zone surveillée
    (`zones` : {slug: libellé}) ; une zone que l'instance ne surveille pas est refusée.
    """
    exercise_count = len(exercises)
    if not isinstance(report, dict):
        raise ReportError("report : objet JSON attendu")
    allowed = {"performed_on", "pains", "minutes", "note", "exercises"}
    unknown = sorted(set(report) - allowed)
    if unknown:
        raise ReportError("champs inconnus : " + ", ".join(unknown))
    performed_on = report.get("performed_on")
    if performed_on is not None:
        if not isinstance(performed_on, str):
            raise ReportError("performed_on : date ISO attendue")
        try:
            date.fromisoformat(performed_on)
        except ValueError as exc:
            raise ReportError("performed_on : date ISO attendue") from exc
    note = report.get("note", "")
    if not isinstance(note, str):
        raise ReportError("note : texte attendu")
    raw_exercises = report.get("exercises", [])
    if not isinstance(raw_exercises, list) or len(raw_exercises) > exercise_count:
        raise ReportError("exercises : liste trop longue ou invalide")
    items = {}
    for entry_position, entry in enumerate(raw_exercises):
        if not isinstance(entry, dict):
            raise ReportError(f"exercises[{entry_position}] : objet attendu")
        entry_unknown = sorted(set(entry) - {"i", "score", "skipped", "gated", "reps", "status", "note"})
        if entry_unknown:
            raise ReportError(f"exercises[{entry_position}] : champs inconnus : "
                              + ", ".join(entry_unknown))
        # Un index hors séance est refusé, jamais ramené dans les bornes : le
        # corriger classerait silencieusement « exercice 99 » sous un vrai exercice.
        raw_index = entry.get("i")
        if type(raw_index) is not int:
            raise ReportError(f"exercises[{entry_position}].i : entier attendu")
        index = raw_index
        if not 0 <= index < exercise_count or index in items:
            raise ReportError(f"exercises[{entry_position}].i : index invalide ou dupliqué")
        raw_reps = entry.get("reps", [])
        if not isinstance(raw_reps, list) or len(raw_reps) > REPS_MAX_SETS:
            raise ReportError(f"exercises[{entry_position}].reps : liste invalide")
        reps = [_optional_int(rep, 0, 999, f"exercises[{entry_position}].reps")
                for rep in raw_reps]
        slots = (exercises[index].get("dose") or {}).get("slots") or []
        if slots and len(reps) > len(slots):
            raise ReportError(f"exercises[{entry_position}].reps : trop de séries")
        for flag in ("skipped", "gated"):
            if flag in entry and type(entry[flag]) is not bool:
                raise ReportError(f"exercises[{entry_position}].{flag} : booléen attendu")
        status = entry.get("status")
        if status is None:
            status = ("gated" if entry.get("gated") else
                      "partial" if entry.get("skipped") and reps else
                      "skipped" if entry.get("skipped") else
                      "completed" if slots and len(reps) == len(slots) else
                      "partial" if reps else "unknown")
        if status not in {"completed", "partial", "skipped", "gated", "unknown"}:
            raise ReportError(f"exercises[{entry_position}].status : état invalide")
        if ((status in {"skipped", "gated"} and reps)
                or (status == "partial" and not reps)
                or (status == "completed" and slots and len(reps) != len(slots))):
            raise ReportError(f"exercises[{entry_position}].status : séries incompatibles")
        exercise_note = entry.get("note", "")
        if not isinstance(exercise_note, str) or len(exercise_note) > NOTE_MAX:
            raise ReportError(f"exercises[{entry_position}].note : texte de 300 caractères maximum")
        # An unspoken left/right gap is a flag of the calf capacity test (>10 %,
        # coaching/strength.md), and it disappears the moment the two sides are
        # poured into one list. Hence the split, done here where the doses are.
        sides = _sides_of(exercises[index])
        banked: dict = {"": [], "d": [], "g": []}
        for slot_position, value in enumerate(reps):
            if value is None:
                continue
            side = sides[slot_position] if slot_position < len(sides) else ""
            banked.setdefault(side, []).append(value)
        items[index] = {
            "score": _optional_int(entry.get("score"), 0, 10,
                                   f"exercises[{entry_position}].score"),
            "reps": [rep for rep in reps if rep is not None],
            "slot_values": reps,
            "sides": banked,
            "skipped": status in {"skipped", "gated"},
            "gated": status == "gated",
            "status": status,
            "completed_slots": len(reps),
            "note": _clean_text(exercise_note),
        }
    return {
        "items": items,
        # Deux lectures qui ne se mélangent pas, comme au registre depuis le
        # 2026-07-29 : celle d'avant est au repos, celle d'après porte l'effort.
        "performed_on": performed_on,
        "pains": _parse_pains(report.get("pains"), zones or {}),
        "minutes": _optional_int(report.get("minutes"), 0, 300, "minutes"),
        "note": _clean_text(note),
    }


def log_entries(session: dict, clean: dict, day: str) -> list:
    """Les faits d'exercice normalisés que le repository PostgreSQL reçoit.

    Built from the SAME parsed report the message is built from, so the typed
    log and the sentence the coach reads can never disagree. Written by the route
    rather than retyped by Claude out of French prose: producer and consumer are
    code in the same commit, which is exactly the case the vault's rule (« aucun
    code ne lit de la prose pour en tirer un fait ») exists to forbid.
    """
    exercises = session.get("exercises") or []
    sid = str(session.get("sid") or "")[:64]
    if not sid:
        return []

    entries = []
    for index, exercise in enumerate(exercises):
        item = clean["items"].get(index)
        slug = _clean_text(exercise.get("slug"))[:60] if isinstance(exercise, dict) else ""
        # No slug, no identity: a one-off derogation (`add` in the morning
        # selection) is not a programme exercise, and there is nothing next week
        # will compare it against. The name is prose and moves; it is not a key.
        if not item or not slug:
            continue
        row = {"date": day, "sid": sid,
               "seance": _clean_text(session.get("session"))[:20], "slug": slug}
        row.update(status=item["status"], completed_slots=item["completed_slots"],
                   note=item["note"])
        if item["skipped"]:
            row["passe"] = "oui"
        else:
            sides = item["sides"]
            if sides.get("d") or sides.get("g"):
                for column, key in (("droite", "d"), ("gauche", "g")):
                    if sides.get(key):
                        row[column] = "|".join(str(value) for value in sides[key])
            elif sides.get(""):
                row["valeurs"] = "|".join(str(value) for value in sides[""])
            if item["score"] is not None:
                row["score"] = item["score"]
        entries.append(row)
    return entries


def _parse_pains(raw: Any, zones: dict[str, str]) -> dict[str, dict]:
    """{zone: {before, after}} : avant au repos, après l'effort, jamais mélangés."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ReportError("pains : objet {zone: {before, after}} attendu")
    unknown = sorted(set(raw) - set(zones))
    if unknown:
        raise ReportError("pains : zone non surveillée " + ", ".join(unknown))
    pains = {}
    for zone, label in zones.items():
        moments = raw.get(zone)
        if moments is None:
            continue
        if not isinstance(moments, dict) or set(moments) - {"before", "after"}:
            raise ReportError(f"pains.{zone} : objet {{before, after}} attendu")
        before = _optional_int(moments.get("before"), 0, 10, f"pains.{zone}.before")
        after = _optional_int(moments.get("after"), 0, 10, f"pains.{zone}.after")
        if before is not None or after is not None:
            pains[zone] = {"label": label, "before": before, "after": after}
    return pains


def format_message(session: dict, clean: dict) -> str:
    """The parsed report, written out as the message the athlete would have typed.

    It travels as a durable postback so the coach can react to the debrief. The
    structured facts are already stored in the same transaction; the model never
    has to recover them from this prose.
    """
    exercises = session.get("exercises") or []

    head = session.get("title") or f"Renfo {session.get('type', '')}".strip()
    when = session.get("date") or ""
    duration = f", {clean['minutes']} min" if clean["minutes"] is not None else ""
    lines = [f"Séance de renfo terminée ({', '.join(x for x in [head, when] if x)}){duration}."]

    detail = []
    for index, exercise in enumerate(exercises):
        item = clean["items"].get(index)
        if not item:
            continue
        name = _clean_text(exercise.get("name"))[:80]
        if item["gated"]:
            # « Retiré par le garde-fou » n'est pas « pas fait » : le coach lirait
            # une inobservance là où sa propre règle a joué.
            remark = f" — remarque : « {item['note']} »" if item["note"] else ""
            detail.append(f"- {name} : retiré par le garde-fou de douleur{remark}")
            continue
        if item["skipped"]:
            remark = f" — remarque : « {item['note']} »" if item["note"] else ""
            detail.append(f"- {name} : pas fait{remark}")
            continue
        bits = []
        if item["status"] == "partial":
            bits.append(f"partiel, {item['completed_slots']} série(s)/côté(s) validé(s)")
        if item["score"] is not None:
            bits.append(f"{item['score']}/10")
        sides = item["sides"]
        if sides.get("d") or sides.get("g"):
            # Les deux côtés se lisent séparément ou pas du tout : leur écart est
            # un drapeau (>10 %, coaching/strength.md), et il disparaît dès qu'on
            # les mélange.
            told = [f"{label} {', '.join(str(rep) for rep in sides[key])}"
                    for label, key in (("droite", "d"), ("gauche", "g")) if sides.get(key)]
            bits.append(" · ".join(told))
        elif item["reps"]:
            bits.append("séries " + ", ".join(str(rep) for rep in item["reps"]))
        if item["note"]:
            bits.append(f"remarque : « {item['note']} »")
        detail.append(f"- {name} : {' — '.join(bits) if bits else 'fait'}")
    if detail:
        lines.append("")
        # Naming what the number IS matters as much here as on the page: read out
        # of context, "7/10" on an exercise reads like a performance mark.
        lines.append("Difficulté ressentie par exercice, 0 = facile, 10 = trop dur "
                     "(0-5 on progresse, 6-7 on tient, 8-10 on régresse) :")
        lines.extend(detail)

    tail = []
    # Les deux lectures se disent SÉPARÉMENT et en nommant laquelle est laquelle :
    # le registre en fait deux séries qui ne se mélangent pas (2026-07-29), la
    # première au repos, la seconde après l'effort. Un seul chiffre ne répondait
    # ni à l'une ni à l'autre des deux questions.
    for reading in clean["pains"].values():
        before, after, label = reading["before"], reading["after"], reading["label"]
        if before is not None and after is not None:
            move = "stable" if before == after else ("monté" if after > before else "descendu")
            tail.append(f"{label} : {before}/10 avant, {after}/10 après ({move}).")
        elif after is not None:
            tail.append(f"{label} : {after}/10 après la séance (rien avant).")
        elif before is not None:
            tail.append(f"{label} : {before}/10 avant de commencer.")
    if clean["note"]:
        # Quoted, and named as such: the free-text field is the only thing here
        # that is not a bounded number.
        tail.append(f"Ce que ⟨athlète⟩ ajoute : « {clean['note']} »")
    if tail:
        lines.append("")
        lines.extend(tail)
    return "\n".join(lines)
