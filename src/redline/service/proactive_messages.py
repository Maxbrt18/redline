"""Closed renderings of initiatives that need no coach judgement.

Already-typed data does not need a model to become a stable question. This module
stays deliberately small: a trigger that must arbitrate, explain or revise a
prescription stays on the agent path.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from redline.coach.fr import initiatives, session_labels
from redline.model import session_kinds


@dataclass(frozen=True)
class ClosedProactive:
    """`question`: the information actually expected, an exact substring of `text`.

    It becomes a tracked question after confirmed delivery, like one from an agent
    turn, and forbids asking the same one again. Empty for a send that expects nothing
    (operator message) and for a social check-in.
    """
    text: str
    markers: tuple[str, ...] = ()
    question: str = ""
    #: The text goes out with a button to the notebook: the answer is given there in
    #: one tap, with no conversation turn, so no reply is awaited here.
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
    """`Dlr:zone` -> `Dlr` (same rule as `ledger.column_base`)."""
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
    """A text written outside the service (e.g. a review made from a laptop), delivered as is."""
    text = str(_object(detail).get("text") or "").strip()
    return ClosedProactive(text) if text else None


_COMPOSERS = {
    "silence": _silence,
    "ledger_incomplete": _missing_cells,
    "missed_session": _missed_session,
    "gut_review": _gut_review,
    "gear_alert": _gear_alert,
    "operator_message": _operator_message,
    "wake_trace": _wake_trace,
}

CLOSED_KINDS = frozenset(_COMPOSERS)


def compose_closed_proactive(kind: str, detail: str) -> ClosedProactive | None:
    composer = _COMPOSERS.get(kind)
    return composer(detail) if composer else None
