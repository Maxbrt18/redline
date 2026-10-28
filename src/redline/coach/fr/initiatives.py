"""The initiatives written without the model: a typed fact turned into a fixed question."""

from __future__ import annotations

import datetime as dt

from .calendar import listing

UNTITLED_SESSION = "prévue"
UNNAMED_GEAR = "Ta paire"
LAST_SESSION = "Comment tu as vécu ta dernière séance ?"

#: How a missing ledger cell is named, asked in Messenger or in the notebook.
CELL_LABELS = {"Dlr": "ton pic de douleur", "RPE": "ton RPE global", "Plaisir": "ton plaisir"}
NOTEBOOK_CELL_LABELS = {**CELL_LABELS, "RPE": "ton RPE"}

#: The exact question, a substring of the text: tracked as a question asked, it gives
#: its meaning to a "légère" typed in the chat.
WAKE_QUESTION = ("quelle trace de douleur au réveil : aucune, très légère, "
                 "légère ou nette ?")


def silence(days: int) -> str:
    return f"Ça fait {days} jours qu'on ne s'est pas parlé. Comment tu vas en ce moment ?"


def cell_label(labels: dict[str, str], base: str, zone: str) -> str:
    """The pain of a zone names its zone."""
    return labels[base] + (f" ({zone})" if zone else "")


def missing_cell(kind: str, day: str, requested: str) -> str:
    return f"Pour ta {kind or 'séance'} du {day or 'récente'}, tu me donnes {requested} sur 10 ?"


def missing_cells_in_notebook(label: str, day: dt.date | None, requested: list[str]) -> str:
    session = " ".join(("Ta séance", label)).strip()
    session += day.strftime(" du %d/%m") if day else " récente"
    return f"{session} attend encore {listing(requested)} : un tap dans le carnet."


def wake_trace() -> str:
    return f"Ce matin, {WAKE_QUESTION} Un tap dans le carnet, ou réponds-moi ici."


def missed_session(title: str) -> str:
    return f"Je n’ai pas de retour sur {title}. Tu l’as faite, reportée ou laissée de côté ?"


def gut_review(title: str) -> str:
    return (f"Sur ta séance {title}, tu as pris combien de glucides par heure, et tes "
            "symptômes digestifs étaient à combien sur 10 ?")


def gear_alert(name: str, km: float | None, limit: float | None) -> str:
    counter = (f" arrive à {km:g} km pour un seuil d'inspection à {limit:g} km"
               if km is not None and limit is not None
               else " approche de son seuil d'inspection")
    return (f"{name}{counter}. Fais un contrôle visuel et de sensation : "
            "qu'est-ce que tu observes ?")
