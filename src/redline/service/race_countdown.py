"""Deterministic compilation of a race deadline into executable milestones."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from redline.model import local_time


@dataclass(frozen=True)
class Stage:
    key: str
    days_before: int
    title: str
    action: str
    evidence: str
    kind: str = "race_countdown"


@dataclass(frozen=True)
class Checkpoint:
    key: str
    kind: str
    due_at: dt.datetime
    expires_at: dt.datetime
    detail: dict[str, Any]


STAGES = (
    Stage("architecture", 120, "Architecture de préparation",
          "Auditer les limiteurs de la course et attribuer à chacun un prochain test daté.",
          "Chaque limiteur important possède un test, une date et un propriétaire."),
    Stage("terrain", 90, "Terrain et inconnues",
          "Fermer ou planifier les inconnues de parcours, règlement, reconnaissance et matériel.",
          "Les inconnues critiques sont soit résolues, soit portées par une échéance typée."),
    # These two milestones CHECK a date, they do not impose one: a generic calendar
    # would schedule the rehearsal at a fixed offset the plan may contradict. Two
    # owners of the race calendar means a milestone pushing a session the plan
    # forbids. The plan decides the date.
    Stage("systemes", 60, "Systèmes en conditions réelles",
          "Vérifier que durée, terrain, nuit, nutrition, digestion et chaussures ont chacun une "
          "répétition représentative datée dans le plan ou une échéance typée, ou déjà faite.",
          "Chaque système a une répétition datée ou une mesure déjà exploitable."),
    Stage("simulation", 45, "Simulation générale",
          "Vérifier que la répétition générale a une date ferme dans le plan ; si elle a déjà "
          "eu lieu, comparer le scénario prédit au scénario observé.",
          "La répétition est datée, ou ses écarts pacing, digestif, matériel et mental sont "
          "écrits avec leur correction."),
    Stage("gel-v1", 30, "Gel du plan de course v1",
          "Figer pacing, marche, nutrition, matériel et plan mental ; ne laisser ouvertes que les preuves manquantes.",
          "Une fiche v1 cohérente existe et chaque case encore ouverte nomme sa dernière preuve."),
    Stage("premortem", 21, "Pré-mortem contradictoire",
          "Jouer le scénario d'échec le plus plausible et décider à froid de la réponse.",
          "Le scénario, son signal précoce et la décision pré-engagée sont explicites.",
          kind="premortem"),
    Stage("taper", 14, "Entrée dans l'affûtage",
          "Protéger la fraîcheur : supprimer les nouveautés et vérifier que la dernière charge utile est derrière.",
          "Aucun stimulus nouveau n'est nécessaire pour se rassurer."),
    Stage("preuve", 7, "Semaine de preuve",
          "Vérifier sommeil, météo, transport, dossard, batteries, tenue et matériel obligatoire.",
          "Chaque élément logistique est physiquement présent ou attribué à un achat daté."),
    Stage("carburant", 3, "Carburant et horaires",
          "Exécuter seulement le protocole alimentaire et de sommeil déjà testé, avec les heures réelles du départ.",
          "Le déroulé de J-2 à l'arrivée tient sur une page sans décision nouvelle."),
    Stage("brief", 1, "Brief d'exécution",
          "Réduire toute la stratégie à cinq décisions observables pour le départ et les moments critiques.",
          "La carte finale nomme départ, marche, nutrition, incident digestif et drapeaux d'arrêt."),
    Stage("jour-j", 0, "Jour J",
          "Restituer le plan déjà décidé ; aucune optimisation, aucun test, aucune nouvelle consigne.",
          "⟨athlète⟩ sait quoi faire dans la première heure et au premier imprévu."),
)


def compile_deadline(deadline: dict) -> list[Checkpoint]:
    """Turn a race date into a stable, replayable calendar."""
    due_on = deadline["due_on"]
    if isinstance(due_on, str):
        due_on = dt.date.fromisoformat(due_on)
    race_key = str(deadline["deadline_key"])
    race_id = str(deadline.get("id") or race_key)
    timezone = local_time.zone()
    checkpoints = []
    for index, stage in enumerate(STAGES):
        day = due_on - dt.timedelta(days=stage.days_before)
        hour = 9 if stage.days_before == 0 else 18
        minute = 0 if stage.days_before == 0 else 30
        due_at = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=timezone)
        if index + 1 < len(STAGES):
            next_stage = STAGES[index + 1]
            next_day = due_on - dt.timedelta(days=next_stage.days_before)
            expires_at = dt.datetime.combine(
                next_day, dt.time(18, 29), tzinfo=timezone)
        else:
            expires_at = dt.datetime.combine(
                due_on, dt.time(23, 59), tzinfo=timezone)
        checkpoints.append(Checkpoint(
            key=f"race:{race_id}:{due_on.isoformat()}:{stage.key}",
            kind=stage.kind,
            due_at=due_at,
            expires_at=expires_at,
            detail={
                "deadline_key": race_key,
                "race": deadline.get("item") or race_key,
                "race_on": due_on.isoformat(),
                "stage": stage.key,
                "title": stage.title,
                "days_before": stage.days_before,
                "action": stage.action,
                "evidence": stage.evidence,
                "context": deadline.get("context") or {},
            },
        ))
    return checkpoints
