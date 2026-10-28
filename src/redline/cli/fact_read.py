#!/usr/bin/env python3
"""Lit les faits PostgreSQL sans fabriquer de fichier de stockage secondaire."""



from __future__ import annotations

import argparse
import datetime as dt
import os
from typing import Any

from redline.coach.fr import session_labels
from redline.model import entrypoint, voice
from redline.model import ledger as table
from redline.model import predictions as prediction_model
from redline.model.errors import ConfigError, InputError
from redline.service.race_countdown import compile_deadline


def _known(row: dict, metric: str, suffix: str = "") -> str | None:
    measurement = row["measurements"].get(metric)
    if not measurement or measurement["state"] != "known":
        return None
    return f"{measurement['value']}{suffix}"


def observations(store: Any, limit: int) -> list[str]:
    """Les observations récentes, AVEC leur clé et ce qui reste à compléter.

    La clé est affichée parce que `ledger-complete.py` l'exige : ne pas la montrer
    revenait à demander un identifiant que rien ne publiait. Le 2026-08-14, l'athlète
    donne le RPE de son vélo de la veille ; le coach connaît son id Strava et tente
    donc `strava:90000000001`, alors que la ligne avait été consignée sans id, sous
    `messenger:2026-08-13:velo-remplace-renfob`. La mesure était impossible à écrire,
    et rien dans la sortie ne permettait de s'en apercevoir.
    """
    rows = store.observation_rows()[:limit]
    lines = [f"Observations récentes ({len(rows)})"]
    for row in rows:
        facts = [row["occurred_on"].isoformat(), row["kind"]]
        for metric, suffix in (("distance_km", " km"), ("hr_avg_bpm", " bpm"),
                               ("rpe", "/10 RPE"), ("enjoyment", "/10 plaisir")):
            value = _known(row, metric, suffix)
            if value:
                facts.append(value)
        facts.extend(table.pain_facts(row.get("pains") or {}))
        pending = sorted(metric for metric, measure in row["measurements"].items()
                         if measure["state"] == "pending")
        pending += table.pending_pains(row.get("pains") or {})
        if pending:
            facts.append("à compléter : " + ", ".join(pending))
        if row["note"]:
            facts.append(row["note"])
        facts.append(f"clé={row['idempotency_key']}")
        lines.append(" · ".join(facts))
    return lines


def exposures(store: Any, limit: int, include_candidates: bool) -> list[str]:
    rows = store.exposure_rows(confirmed_only=not include_candidates)[:limit]
    lines = [f"Expositions {'toutes' if include_candidates else 'confirmées'} ({len(rows)})"]
    for row in rows:
        dose = ""
        if row["duration_min"] is not None:
            dose += f" · {row['duration_min']} min"
        if row["target_value"] is not None:
            dose += f" · cible {row['target_value']} {row['target_unit']}"
        lines.append(
            f"{row['occurred_on']} · {row['exposure_type']} · {row['origin']} · "
            f"{row['status']} · tolérance {row['tolerance']}{dose} · "
            f"observation={row['observation_key']} · clé={row['exposure_key']}"
            + (f" · {row['note']}" if row["note"] else ""))
    return lines


def strength(store: Any, limit: int) -> list[str]:
    """Les comptes rendus de renfo canoniques, récents d'abord.

    La page écrit cette structure avant de prévenir le coach. La publier ici évite
    qu'il tente de reconstruire une séance depuis la projection CSV ou, pire, de la
    ressaisir comme une observation générale.
    """
    rows = list(reversed(store.strength_history()))[:limit]
    lines = [f"Séances de renfo récentes ({len(rows)})"]
    for row in rows:
        facts = [row["performed_on"].isoformat(), f"renfo {row['workout']}"]
        if row.get("duration_minutes") is not None:
            facts.append(f"{row['duration_minutes']} min")
        facts.extend("douleur " + fact
                     for fact in table.strength_pain_facts(row.get("pains") or {}))
        facts.append(f"clé={row['session_key']}")
        lines.append(" · ".join(str(value) for value in facts))
        for exercise in row.get("exercises", []):
            detail = [exercise["exercise_slug"]]
            if exercise.get("difficulty") is not None:
                detail.append(f"difficulté {exercise['difficulty']}/10")
            if exercise.get("skipped"):
                detail.append("passé")
            grouped: dict[str, list[str]] = {}
            for series in exercise.get("sets", []):
                grouped.setdefault(series["side"], []).append(str(series["repetitions"]))
            if grouped:
                detail.append("séries " + " ; ".join(
                    f"{side}=" + "|".join(values) for side, values in grouped.items()))
            lines.append("  " + " · ".join(detail))
        if row.get("note"):
            lines.append("  note : " + row["note"])
    return lines


def threads(store: Any, include_closed: bool) -> list[str]:
    rows = [row for row in store.thread_rows() if include_closed or row["status"] == "ouvert"]
    lines = [f"Fils {'tous' if include_closed else 'ouverts'} ({len(rows)})"]
    for row in rows:
        follow_up = row["follow_up_on"].isoformat() if row["follow_up_on"] else "sans relance"
        lines.append(f"{row['thread_key']} · {row['opened_on']} · {row['kind']} · "
                     f"{follow_up} · {row['status']} · {row['body']}")
    return lines


def deadlines(store: Any, include_done: bool) -> list[str]:
    rows = [row for row in store.deadline_rows() if include_done or row["status"] != "done"]
    lines = [f"Échéances {'toutes' if include_done else 'actives'} ({len(rows)})"]
    for row in rows:
        blocked = f" · bloque {row['blocks']}" if row["blocks"] else ""
        period = ""
        if row.get("starts_on") or row.get("ends_on"):
            period = f" · période {row.get('starts_on') or row['due_on']}→{row.get('ends_on') or row['due_on']}"
        lines.append(f"{row['deadline_key']} · {row['due_on']} · {row.get('kind', 'general')} · {row['status']} · "
                     f"{row['item']} · propriétaire {row['fact_owner']}{blocked}")
        if period:
            lines[-1] += period
        if row.get("athlete_label"):
            lines[-1] += f" · carnet de l'athlète : « {row['athlete_label']} »"
    return lines


def capabilities(store: Any) -> list[str]:
    rows = store.latest_capabilities()
    lines = [f"Capacités ({len(rows)})"]
    for row in rows:
        lock = f" · verrou {row['lock_reason']}" if row["lock_reason"] else ""
        test = f" · prochain test {row['next_test']}" if row["next_test"] else ""
        lines.append(f"{row['capability_number']} · {row['slug']} · "
                     f"{row['score']}/{row['scale']} · {row['status']} · "
                     f"évaluée le {row['evaluated_on']}{test}{lock}")
    return lines


def _as_read(row: dict) -> str:
    """Un message de la conversation tel que le coach le relit (`src/redline/model/voice.py`)."""
    return row["body"] if row["role"] == "athlete" else voice.muted(row["body"])


def proactive(store: Any, limit: int) -> list[str]:
    rows = store.proactive_message_rows(limit=limit)
    lines = [f"Messages proactifs récents ({len(rows)})"]
    for row in rows:
        angle = f" · angle {row['angle']}" if row["angle"] else ""
        lines.append(f"{row['sent_at'].isoformat()} · {row['kind']}{angle} · "
                     + voice.muted(row["summary"]))
    return lines


def corrections(store: Any, limit: int, now: dt.datetime | None = None) -> list[str]:
    from redline.storage.repository import CORRECTION_ANNOUNCE_WINDOW

    now = now or dt.datetime.now(dt.UTC)
    rows = store.correction_rows()[:limit]
    lines = [f"Corrections enregistrées ({len(rows)})"]
    for row in rows:
        if row["delivered_at"]:
            delivery = f"livrée le {row['delivered_at']}"
        elif (row.get("registered_at") and dt.datetime.fromisoformat(str(row["registered_at"]))
              + CORRECTION_ANNOUNCE_WINDOW <= now):
            delivery = "non annoncée, délai dépassé"
        else:
            delivery = "à annoncer"
        recurrence = (f" · {row['revisions']} récidive(s) depuis le "
                      f"{str(row['first_registered_at'])[:10]}" if row.get("revisions") else "")
        lines.append(f"{row['correction_key']} · {delivery}{recurrence} · {row['summary']}")
    return lines


def gear(store: Any) -> list[str]:
    rows = store.gear_rows()
    lines = [f"Matériel ({len(rows)})"]
    for row in rows:
        lines.append(f"{row['gear_id']} · {row['name']} · {row['role']} · "
                     f"{float(row['distance_km']):.1f} km · seuil contrôle "
                     f"{float(row['retire_at_km']):.1f} km · {row['status']}"
                     + (" · principale" if row.get("primary") else ""))
    return lines


def predictions(store: Any, *, due: bool = False, today: dt.date | None = None) -> list[str]:
    every = store.prediction_rows()
    today = today or dt.date.today()
    rows = every
    if due:
        rows = [row for row in rows if row["status"] == "open" and row["due_on"] <= today]
    label = "Prédictions échues" if due else "Prédictions"
    lines = [f"{label} ({len(rows)})"]
    for row in rows:
        challenge = " · contredite" if row["challenge"] else ""
        used = " · consommée" if row.get("used_at") else ""
        score = f" · Brier {float(row['brier_score']):.3f}" if row["brier_score"] is not None else ""
        zone = f" ({row['resolves_zone']})" if row.get("resolves_zone") else ""
        bound = (f" · se solde au {session_labels.short(row['resolves_kind'])}{zone} "
                 f"du {row['resolves_on']} "
                 f"si {row['success_op']}{float(row['success_value']):g}"
                 if row.get("resolves_kind") else "")
        lines.append(f"{row['prediction_key']} · {row['domain']} · p={float(row['probability']):.0%} "
                     f"· échéance {row['due_on']} · {row['status']}{score}{bound}{challenge}"
                     f"{used} · {row['statement']}")
    lines.extend(prediction_model.calibration_lines(every, today))
    return lines


def roadmap(store: Any) -> list[str]:
    races = [row for row in store.deadline_rows()
             if row.get("kind") == "race" and row["status"] != "done"]
    lines = [f"Rétroplanning course ({len(races)} course(s))"]
    for race in races:
        lines.append(f"{race['item']} · {race['due_on']}")
        for checkpoint in compile_deadline(race):
            detail = checkpoint.detail
            lines.append(
                f"  {checkpoint.due_at.date()} · {detail['title']} · "
                f"{detail['action']} · preuve : {detail['evidence']}")
    return lines


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    commands = root.add_subparsers(dest="command", required=True)
    question_parser = commands.add_parser("questions")
    question_parser.add_argument("--limit", type=int, default=20)
    conversation_parser = commands.add_parser(
        "conversation", help="rechercher les échanges Messenger passés")
    conversation_parser.add_argument("--query", required=True)
    conversation_parser.add_argument("--limit", type=int, default=6)
    recent = commands.add_parser("observations")
    recent.add_argument("--limit", type=int, default=10)
    exposure_parser = commands.add_parser("exposures")
    exposure_parser.add_argument("--limit", type=int, default=20)
    exposure_parser.add_argument("--all", action="store_true")
    strength_parser = commands.add_parser("strength")
    strength_parser.add_argument("--limit", type=int, default=3)
    thread_parser = commands.add_parser("threads")
    thread_parser.add_argument("--all", action="store_true")
    deadline_parser = commands.add_parser("deadlines")
    deadline_parser.add_argument("--all", action="store_true")
    commands.add_parser("capabilities")
    commands.add_parser("gear")
    prediction_parser = commands.add_parser("predictions")
    prediction_parser.add_argument(
        "--due", action="store_true",
        help="seulement les prédictions ouvertes arrivées à échéance")
    commands.add_parser("roadmap")
    messages = commands.add_parser("proactive")
    messages.add_argument("--limit", type=int, default=10)
    correction_parser = commands.add_parser("corrections")
    correction_parser.add_argument("--limit", type=int, default=20)
    permit_parser = commands.add_parser(
        "permit", help="permis EF : choix scellés face au couru, obtention, retrait")
    permit_parser.add_argument("--week", help="AAAA-WNN ; toutes les semaines sinon")
    return root


def main() -> int:
    args = parser().parse_args()
    if not args.database_url:
        raise ConfigError("DATABASE_URL obligatoire")
    from redline.storage import Database, OperationalStore

    database = Database(args.database_url, application_name="fact-read")
    store = OperationalStore(database)
    limit = getattr(args, "limit", 1)
    if limit < 1 or limit > 1000:
        raise InputError("limit attendu entre 1 et 1000")
    if args.command == "questions":
        lines = [f"{row['id']} · {row['status']} · posée {row['asked_at']} · "
                 f"reprise {row['follow_up_on'] or 'non fixée'} · {row['question']} · "
                 f"{row['review_note']}" for row in store.question_rows(args.limit)]
    elif args.command == "conversation":
        turn_id = os.environ.get("REDLINE_TURN_ID", "").strip()
        if not turn_id:
            raise InputError("REDLINE_TURN_ID obligatoire pour la conversation")
        lines = []
        for group in store.conversation_search_for_turn(turn_id, args.query, min(limit, 12)):
            hit = group["hit"]
            lines.append(f"Message {hit['id']} · {hit['created_at']} · {hit['role']} : "
                         + _as_read(hit)[:1600].replace("\n", " "))
            for label in ("before", "after"):
                row = group[label]
                if row:
                    lines.append(f"  {label} · {row['created_at']} · {row['role']} : "
                                 + _as_read(row)[:600].replace("\n", " "))
        if not lines:
            lines = ["Aucun échange retrouvé. Une absence de résultat ne prouve pas "
                     "que le sujet n'a jamais été discuté."]
    elif args.command == "observations":
        lines = observations(store, args.limit)
    elif args.command == "exposures":
        lines = exposures(store, args.limit, args.all)
    elif args.command == "strength":
        lines = strength(store, args.limit)
    elif args.command == "threads":
        lines = threads(store, args.all)
    elif args.command == "deadlines":
        lines = deadlines(store, args.all)
    elif args.command == "capabilities":
        lines = capabilities(store)
    elif args.command == "gear":
        lines = gear(store)
    elif args.command == "predictions":
        lines = predictions(store, due=args.due)
    elif args.command == "roadmap":
        lines = roadmap(store)
    elif args.command == "corrections":
        lines = corrections(store, args.limit)
    elif args.command == "permit":
        from redline.analysis import permit_report

        lines = permit_report.report(store, dt.date.today(), args.week) or [
            "Permis EF : aucun choix scellé."]
    else:
        lines = proactive(store, args.limit)
    database.dispose()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
