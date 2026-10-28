"""Rendus fermés des initiatives qui ne demandent aucun jugement de coach.

Une donnée déjà typée n'a pas besoin d'un modèle pour devenir une question stable.
Ce module reste volontairement petit : si un déclencheur doit arbitrer, expliquer ou
réviser une prescription, il demeure dans le chemin agent.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from redline.coach.fr import initiatives, session_labels
from redline.model import session_kinds


@dataclass(frozen=True)
class ClosedProactive:
    """`question` : l'information réellement attendue, sous-chaîne exacte de `text`.

    Elle devient une question suivie après livraison confirmée, comme celle d'un tour
    d'agent, et interdit de reposer la même. Vide pour un envoi qui n'attend rien
    (message opérateur) et pour une prise de nouvelles sociale.
    """
    text: str
    markers: tuple[str, ...] = ()
    question: str = ""
    #: Le texte part avec un bouton vers le carnet : la réponse s'y donne d'un tap,
    #: sans tour de conversation, donc il n'attend aucune réponse ici.
    notebook: bool = False


def _object(detail: str) -> dict:
    try:
        value = json.loads(detail)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _session_title(detail: dict) -> str:
    return str(detail.get("title") or detail.get("workout")
               or initiatives.UNTITLED_SESSION).strip()


def _silence(detail: str) -> ClosedProactive:
    try:
        days = max(1, int(detail))
    except (TypeError, ValueError):
        days = 3
    return ClosedProactive(initiatives.silence(days))


def _missing_cells(detail: str) -> ClosedProactive | None:
    data = _object(detail)
    if not data:
        return ClosedProactive(initiatives.LAST_SESSION)
    # The wake trace is collected the same morning; the evening net only asks for
    # effort figures.
    if data.get("type") == session_kinds.WAKE_TRACE:
        return None
    names = list(data.get("missing", ()))
    selected = next((name for wanted in ("Dlr", "RPE", "Plaisir") for name in names
                     if _base(name) == wanted), None)
    if selected is None:
        return ClosedProactive(initiatives.LAST_SESSION)
    markers = list(data.get("markers", ()))
    index = names.index(selected)
    text = initiatives.missing_cell(str(data.get("type") or ""), str(data.get("date") or ""),
                                    _requested(initiatives.CELL_LABELS, selected))
    return ClosedProactive(
        text, (str(markers[index]),) if index < len(markers) and markers[index] else (),
        question=text)


def _base(column: str) -> str:
    """« Dlr:genou » → « Dlr » (même règle que `ledger.column_base`)."""
    return column.split(":", 1)[0]


def _requested(labels: dict[str, str], column: str) -> str:
    _, _, zone = column.partition(":")
    return initiatives.cell_label(labels, _base(column), zone)


def missing_cells_in_notebook(detail: str) -> ClosedProactive | None:
    """The evening net when the notebook is connected: it names everything missing and
    points to the notebook, where an answer costs no coach turn, without asking."""
    data = _object(detail)
    if not data or data.get("type") == session_kinds.WAKE_TRACE:
        return None
    names = [name for wanted in ("Dlr", "RPE", "Plaisir")
             for name in data.get("missing", ()) if _base(name) == wanted]
    if not names:
        return None
    kind = str(data.get("type") or "").strip()
    label = session_labels.short(kind) if kind in session_labels.LABELS else kind
    try:
        day: dt.date | None = dt.date.fromisoformat(str(data.get("date")))
    except ValueError:
        day = None
    markers = tuple(str(marker) for marker in data.get("markers", ()) if marker)
    return ClosedProactive(
        initiatives.missing_cells_in_notebook(
            label, day, [_requested(initiatives.NOTEBOOK_CELL_LABELS, name) for name in names]),
        markers, notebook=True)


#: Re-exported: the question tracked after the wake-trace reminder.
WAKE_QUESTION = initiatives.WAKE_QUESTION


def _wake_trace(detail: str) -> ClosedProactive:
    """The mid-morning reminder; the notebook takes the answer in one tap."""
    return ClosedProactive(initiatives.wake_trace(), question=WAKE_QUESTION, notebook=True)


def _missed_session(detail: str) -> ClosedProactive:
    text = initiatives.missed_session(_session_title(_object(detail)))
    return ClosedProactive(text, question=text)


def _gut_review(detail: str) -> ClosedProactive:
    text = initiatives.gut_review(_session_title(_object(detail)))
    return ClosedProactive(text, question=text)


def _number(value) -> float | None:
    return value if isinstance(value, (int, float)) else None


def _gear_alert(detail: str) -> ClosedProactive:
    data = _object(detail)
    km, limit = _number(data.get("km")), _number(data.get("limit"))
    text = initiatives.gear_alert(
        str(data.get("name") or initiatives.UNNAMED_GEAR).strip(),
        km if limit is not None else None, limit if km is not None else None)
    return ClosedProactive(text, question=text)


def _operator_message(detail: str) -> ClosedProactive | None:
    """Un texte écrit hors du service (bilan fait depuis le Mac), livré tel quel."""
    text = str(_object(detail).get("text") or "").strip()
    return ClosedProactive(text) if text else None


_COMPOSERS = {
    "silence": _silence,
    "registre_incomplet": _missing_cells,
    "missed_session": _missed_session,
    "gut_review": _gut_review,
    "gear_alert": _gear_alert,
    "operator_message": _operator_message,
    "trace_reveil": _wake_trace,
}

CLOSED_KINDS = frozenset(_COMPOSERS)


def compose_closed_proactive(kind: str, detail: str) -> ClosedProactive | None:
    composer = _COMPOSERS.get(kind)
    return composer(detail) if composer else None
