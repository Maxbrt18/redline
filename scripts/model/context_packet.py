"""Paquet borné de faits PostgreSQL injecté avant un tour de coaching."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from scripts.model import rulebook
from scripts.storage import StorageError

if TYPE_CHECKING:
    from scripts.storage import OperationalStore

log = logging.getLogger("redline")

METRICS = (
    ("distance_km", "km"), ("duration_s", "s"), ("hr_avg_bpm", "FC"),
    ("rpe", "RPE"), ("pain", "douleur"), ("enjoyment", "plaisir"),
    ("wbgt_c", "WBGT"),
)

def _day(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _as_date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])


def _observation(row: dict) -> str:
    parts = [_day(row["occurred_on"]), str(row["kind"])]
    for key, label in METRICS:
        measure = (row.get("measurements") or {}).get(key) or {}
        if measure.get("state") == "known":
            parts.append(f"{label}={measure.get('value')}")
    pending = [key for key, measure in (row.get("measurements") or {}).items()
               if measure.get("state") == "pending"]
    if pending:
        parts.append("attend=" + ",".join(sorted(pending)))
    if row.get("note"):
        parts.append("note=" + str(row["note"])[:180])
    parts.append("clé=" + str(row["idempotency_key"]))
    return " | ".join(parts)


def render_context_packet(store: OperationalStore, depth: str, today: dt.date) -> str:
    """Rend seulement les faits utiles au niveau ; N0 ne paie aucun paquet."""
    if depth == "N0":
        return ""
    lines = ["[CONTEXTE TYPÉ FOURNI PAR LE SERVICE, invisible, ne pas réciter",
             f"Date de lecture : {today.isoformat()}. Les dates des faits restent leur date réelle."]

    def read(reader: Callable[..., Iterable[dict]], label: str, **kwargs: Any) -> list:
        try:
            rows = list(reader(**kwargs))
        except StorageError:
            log.warning("Paquet de contexte : lecture %s indisponible", label)
            lines.append(f"{label} : lecture échouée, état inconnu ; ce n'est pas une absence de faits.")
            return []
        if not rows:
            lines.append(f"{label} : aucune donnée enregistrée dans cette lecture.")
        return rows

    all_observations = read(store.observation_rows, "Observations")
    observations = all_observations[:10]
    lines.append("Observations récentes :")
    lines.extend("- " + _observation(row) for row in observations)
    # Les activités peuvent occuper les dix places : sélectionner le réveil AVANT
    # cette coupe évite de demander à l'utilisateur de reconstruire son historique.
    morning = sorted((row for row in all_observations
                      if row["kind"] == "reveil"
                      and (today - dt.timedelta(days=56)).isoformat()
                      <= _day(row["occurred_on"]) < today.isoformat()),
                     key=lambda row: _day(row["occurred_on"]), reverse=True)[:8]
    if morning:
        lines.append("Réveils antérieurs : 8 dernières lectures au maximum sur 56 jours ; "
                     "repères historiques, jamais preuve de l'état actuel. "
                     "Comparer les mesures comparables, sans exiger de Sam une certitude "
                     "sur sa base ni convertir une note qualitative en chiffre.")
        lines.extend("- " + _observation(row) for row in morning)
    exposures = read(store.exposure_rows, "Expositions", confirmed_only=True)[:8]
    if exposures:
        lines.append("Expositions confirmées (intention déclarée, jamais déduite d'une mesure) :")
        for row in exposures:
            dose = []
            if row.get("duration_min") is not None:
                dose.append(f"{row['duration_min']} min")
            if row.get("target_value") is not None:
                dose.append(f"cible {row['target_value']} {row['target_unit']}")
            lines.append(
                f"- {_day(row['occurred_on'])} | {row['exposure_type']} | "
                f"origine={row['origin']} | tolérance={row['tolerance']} | "
                f"dose={', '.join(dose) if dose else 'non précisée'} | "
                f"observation={row['observation_key']}")
    recent_strength = list(reversed(read(store.strength_history, "Renfo")))[:2]
    if recent_strength:
        lines.append("Renfo récent, déjà enregistré par la page :")
        for session in recent_strength:
            detail = [
                str(session["performed_on"]),
                f"renfo {session.get('workout') or '?'}",
            ]
            if session.get("duration_minutes") is not None:
                detail.append(f"{session['duration_minutes']} min")
            if (session.get("shin_before") is not None
                    or session.get("shin_after") is not None):
                before = ("?" if session.get("shin_before") is None
                          else session["shin_before"])
                after = ("?" if session.get("shin_after") is None
                         else session["shin_after"])
                detail.append(f"injury {before}->{after}/10")
            difficult = [
                f"{exercise['exercise_slug']}={exercise['difficulty']}/10"
                for exercise in session.get("exercises", [])
                if exercise.get("difficulty") is not None
                and exercise["difficulty"] >= 8
            ]
            if difficult:
                detail.append("difficultés hautes " + ", ".join(difficult[:5]))
            if session.get("note"):
                detail.append("note=" + session["note"][:220])
            lines.append("- " + " | ".join(detail))
    daily = sorted(
        (row for row in read(store.daily_rows, "État quotidien") if row["checked_on"] <= today),
        key=lambda row: row["checked_on"])[-3:]
    if daily:
        lines.append("État quotidien récent :")
        if daily[-1]["checked_on"] != today:
            lines.append("Aucun état déclaré pour aujourd'hui : les lignes suivantes sont historiques.")
        lines.extend(
            f"- {_day(row['checked_on'])} | sommeil={row['sleep']} | "
            f"charge={row['life_load']} | envie={row['desire']}"
            + (f" | note={row['note'][:120]}" if row.get("note") else "")
            for row in daily)
    if depth in {"N2", "N3"}:
        threads = [row for row in read(store.thread_rows, "Fils") if row["status"] == "ouvert"][:8]
        if threads:
            lines.append("Fils ouverts :")
            lines.extend(
                f"- {row['thread_key']} | {row['body'][:180]} | "
                f"relance={_day(row['follow_up_on']) if row['follow_up_on'] else 'aucune'}"
                for row in threads)
        deadlines = [row for row in read(store.deadline_rows, "Échéances") if row["status"] != "done"][:8]
        if deadlines:
            lines.append("Échéances actives :")
            lines.extend(
                f"- {row['deadline_key']} | {_day(row['due_on'])}"
                + (" ÉCHUE" if _as_date(row["due_on"]) < today else "")
                + f" | {row['item']} | "
                f"propriétaire={row['fact_owner']} | "
                f"bloque={row.get('blocks') or 'rien dans le programme courant'}"
                for row in deadlines)
        capabilities = read(store.latest_capabilities, "Capacités")
        if capabilities:
            lines.append("Capacités :")
            lines.extend(
                f"- {row['capability_number']} {row['slug']} | "
                f"{row['score']}/{row['scale']} {row['status']}"
                for row in capabilities)
    lines.append("Les commandes de lecture ne servent qu'à élargir ou vérifier ce paquet. "
                 "Une lecture échouée ne confirme rien : utilise les sections disponibles, "
                 "vérifie seulement le fait nécessaire et explique la limite s'il reste inaccessible. "
                 "Un ressenti actuel ne s'annule pas face à une mesure objective ou une ancienne "
                 "déclaration. En cas de contradiction, nomme les deux dates et sources ; "
                 "ne fusionne pas les valeurs et ne modifie pas le plan sur cette seule base.")
    # Le règlement en vigueur voyage avec les faits : c'est lui qui départage une
    # formulation ancienne relue dans un fichier ou dans la conversation (audit du
    # 2026-09-27, `scripts/model/rulebook.py`).
    return "\n".join(lines) + "]\n" + rulebook.render()
