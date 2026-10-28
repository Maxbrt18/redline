"""Rendus fermés des initiatives qui ne demandent aucun jugement de coach.

Une donnée déjà typée n'a pas besoin d'un modèle pour devenir une question stable.
Ce module reste volontairement petit : si un déclencheur doit arbitrer, expliquer ou
réviser une prescription, il demeure dans le chemin agent.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from scripts.model import session_kinds, session_labels


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
    return str(detail.get("title") or detail.get("workout") or "prévue").strip()


def _silence(detail: str) -> ClosedProactive:
    try:
        days = max(1, int(detail))
    except (TypeError, ValueError):
        days = 3
    return ClosedProactive(
        f"Ça fait {days} jours qu'on ne s'est pas parlé. Comment tu vas en ce moment ?")


def _missing_cells(detail: str) -> ClosedProactive | None:
    data = _object(detail)
    if not data:
        return ClosedProactive(
            "Comment tu as vécu ta dernière séance ?")
    # Le réveil est une trace qualitative recueillie le matin même. Il ne doit
    # jamais retomber dans le filet du soir, qui ne sait formuler que des cellules
    # numériques d'effort (2026-09-15).
    if data.get("type") == session_kinds.WAKE_TRACE:
        return None
    labels = {
        "RPE": "ton RPE global",
        "Dlr": "ton pic de douleur",
        "Plaisir": "ton plaisir",
    }
    names = list(data.get("missing", ()))
    selected = next((name for wanted in ("Dlr", "RPE", "Plaisir") for name in names
                     if _base(name) == wanted), None)
    if selected is None:
        return ClosedProactive("Comment tu as vécu ta dernière séance ?")
    requested = _requested(labels, selected)
    markers = list(data.get("markers", ()))
    index = names.index(selected)
    kind = str(data.get("type") or "séance")
    day = str(data.get("date") or "récente")
    text = f"Pour ta {kind} du {day}, tu me donnes {requested} sur 10 ?"
    return ClosedProactive(
        text, (str(markers[index]),) if index < len(markers) and markers[index] else (),
        question=text)


def _base(column: str) -> str:
    """« Dlr:genou » → « Dlr » (même règle que `ledger.column_base`)."""
    return column.split(":", 1)[0]


def _requested(labels: dict[str, str], column: str) -> str:
    """Le libellé demandé ; la douleur d'une zone nomme sa zone."""
    _, _, zone = column.partition(":")
    return labels[_base(column)] + (f" ({zone})" if zone else "")


def missing_cells_in_notebook(detail: str) -> ClosedProactive | None:
    """Le repli du soir quand le carnet est connecté (2026-10-09).

    Une réponse dans Messenger coûte un tour de coach complet pour écrire un chiffre ;
    la même réponse dans le carnet ne coûte rien. Le rappel nomme donc tout ce qui
    manque et renvoie au carnet, sans poser de question.
    """
    data = _object(detail)
    if not data or data.get("type") == session_kinds.WAKE_TRACE:
        return None
    labels = {"Dlr": "ton pic de douleur", "RPE": "ton RPE", "Plaisir": "ton plaisir"}
    names = [name for wanted in ("Dlr", "RPE", "Plaisir")
             for name in data.get("missing", ()) if _base(name) == wanted]
    if not names:
        return None
    wanted = [_requested(labels, name) for name in names]
    listed = wanted[0] if len(wanted) == 1 else ", ".join(wanted[:-1]) + " et " + wanted[-1]
    kind = str(data.get("type") or "").strip()
    label = session_labels.short(kind) if kind in session_labels.LABELS else kind
    session = " ".join(("Ta séance", label)).strip()
    try:
        session += dt.date.fromisoformat(str(data.get("date"))).strftime(" du %d/%m")
    except ValueError:
        session += " récente"
    markers = tuple(str(marker) for marker in data.get("markers", ()) if marker)
    return ClosedProactive(
        f"{session} attend encore {listed} : un tap dans le carnet.",
        markers, notebook=True)


#: La question exacte, sous-chaîne du texte : suivie comme une question posée, elle
#: donne son sens à un « légère » tapé dans le chat.
WAKE_QUESTION = ("quelle trace de douleur au réveil : aucune, très légère, "
                 "légère ou nette ?")


def _wake_trace(detail: str) -> ClosedProactive:
    """Le rappel de 10 h 30 : le carnet prend la réponse d'un tap jusqu'à 14 h."""
    text = f"Ce matin, {WAKE_QUESTION} Un tap dans le carnet, ou réponds-moi ici."
    return ClosedProactive(text, question=WAKE_QUESTION, notebook=True)


def _missed_session(detail: str) -> ClosedProactive:
    title = _session_title(_object(detail))
    text = f"Je n’ai pas de retour sur {title}. Tu l’as faite, reportée ou laissée de côté ?"
    return ClosedProactive(text, question=text)


def _gut_review(detail: str) -> ClosedProactive:
    data = _object(detail)
    title = _session_title(data)
    text = (f"Sur ta séance {title}, tu as pris combien de glucides par heure, et tes "
            "symptômes digestifs étaient à combien sur 10 ?")
    return ClosedProactive(text, question=text)


def _gear_alert(detail: str) -> ClosedProactive:
    data = _object(detail)
    name = str(data.get("name") or "Ta paire").strip()
    km = data.get("km")
    limit = data.get("limit")
    counter = (f" arrive à {km:g} km pour un seuil d'inspection à {limit:g} km"
               if isinstance(km, (int, float)) and isinstance(limit, (int, float))
               else " approche de son seuil d'inspection")
    text = (f"{name}{counter}. Fais un contrôle visuel et de sensation : "
            "qu'est-ce que tu observes ?")
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
