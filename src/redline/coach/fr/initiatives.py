"""The initiatives written without the model: a typed fact turned into a fixed question."""

from __future__ import annotations

import datetime as dt

from redline.coach.dates import alternatives, listing

UNTITLED_SESSION = "prévue"
UNNAMED_GEAR = "Ta paire"
LAST_SESSION = "Comment tu as vécu ta dernière séance ?"

#: How a missing ledger cell is named, asked in Messenger or in the notebook.
CELL_LABELS = {"pain": "ton pic de douleur", "rpe": "ton RPE global", "enjoyment": "ton plaisir"}
NOTEBOOK_CELL_LABELS = {**CELL_LABELS, "rpe": "ton RPE"}

def wake_question(levels: list[str]) -> str:
    """The exact question, a substring of the text: tracked as a question asked, it gives
    its meaning to a level named in the chat. `levels`: the labels of the instance's
    morning-trace scale."""
    return f"quelle trace de douleur au réveil : {alternatives(levels)} ?"


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


def wake_trace(question: str) -> str:
    return f"Ce matin, {question} Un tap dans le carnet, ou réponds-moi ici."


def missed_session(title: str) -> str:
    return f"Je n’ai pas de retour sur {title}. Tu l’as faite, reportée ou laissée de côté ?"


def gut_review(title: str) -> str:
    return (f"Sur ta séance {title}, tu as pris combien de glucides par heure, sous quelle "
            "forme (boisson, gel, pâte, solide) et avec quels sucres ? Et tes troubles "
            "digestifs, à combien sur 10 ? S'il y en a eu : lesquels, et vers quelle minute "
            "ils sont apparus ?")


def gut_review_in_notebook(title: str) -> str:
    return (f"Ton retour carburant sur {title} t’attend dans le carnet : glucides, forme, "
            "sucres et troubles digestifs, un tap chacun.")


def gear_alert(name: str, km: float | None, limit: float | None) -> str:
    counter = (f" arrive à {km:g} km pour un seuil d'inspection à {limit:g} km"
               if km is not None and limit is not None
               else " approche de son seuil d'inspection")
    return (f"{name}{counter}. Fais un contrôle visuel et de sensation : "
            "qu'est-ce que tu observes ?")


def cycle_transition(kind: str, level: str, monday: dt.date, weeks: int,
                     volume: str) -> str:
    """A new cycle in force: `kind` and `level` are already French labels."""
    named = f"{kind} « {level} »" if level else kind
    span = "1 semaine" if weeks == 1 else f"{weeks} semaines"
    return (f"Nouveau cycle à partir du lundi {monday:%d/%m} : {named}, {span}, jusqu’à "
            f"{volume} km par semaine. Je prépare chaque semaine dans ce cadre.")


#: The two answers to a proposal, as the athlete taps them.
TAKE = "Je la prends"
LATER = "Pas maintenant"


def cycle_proposal(race: str, monday: dt.date, weeks: int, peak: str, long_run: str,
                   flagships: int, risks: int, until: dt.date) -> str:
    """A preparation proposed: its frame in a few figures, and until when it waits."""
    target = f"pour « {race} »" if race else "pour l'objectif"
    lines = [f"Je te propose une préparation {target} : {weeks} semaines à partir du lundi "
             f"{monday:%d/%m}, jusqu’à {peak} km par semaine et une sortie longue de "
             f"{long_run} km au plus."]
    if flagships:
        lines.append(f"{flagships} séance{'s' if flagships > 1 else ''} phare"
                     f"{'s' if flagships > 1 else ''} pour finir le bloc spécifique.")
    if risks:
        lines.append(f"{risks} point{'s' if risks > 1 else ''} à surveiller : demande-moi "
                     "le détail.")
    lines.append(f"Elle t’attend jusqu’au {until:%d/%m} ; sans réponse, rien ne change.")
    return " ".join(lines)


def cycle_taken(monday: dt.date, written: list[str]) -> str:
    """The proposal taken; the weeks already written are read again against it."""
    text = f"C’est noté : la préparation commence le lundi {monday:%d/%m}."
    if written:
        text += (f" La semaine {', '.join(written)} déjà écrite sera relue dans ce cadre au "
                 "prochain bilan.")
    return text


CYCLE_LEFT = "D’accord, rien ne change : la proposition reste ouverte jusqu’à sa date."
CYCLE_GONE = "Cette proposition n’est plus ouverte : rien ne change."
