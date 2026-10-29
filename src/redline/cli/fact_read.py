#!/usr/bin/env python3
"""Reads facts from PostgreSQL without creating any secondary storage file."""



from __future__ import annotations

import argparse
import datetime as dt
import os
from typing import Any

from redline.coach.fr import fact_read as wording
from redline.coach.fr import session_labels
from redline.model import entrypoint, voice
from redline.model import ledger as table
from redline.model import predictions as prediction_model
from redline.model.errors import ConfigError, InputError
from redline.model.vocabulary import ThreadStatus
from redline.service.race_countdown import compile_deadline


def _known(row: dict, metric: str, suffix: str = "") -> str | None:
    measurement = row["measurements"].get(metric)
    if not measurement or measurement["state"] != "known":
        return None
    return f"{measurement['value']}{suffix}"


def observations(store: Any, limit: int) -> list[str]:
    """Recent observations, WITH their key and what is left to complete.

    The key is shown because `redline ledger-complete` requires it: an identifier that
    nothing published could not be supplied. A row recorded without a Strava id lives
    under a `messenger:<date>:<slug>` key, and the output must reveal it.
    """
    rows = store.observation_rows()[:limit]
    lines = [wording.OBSERVATIONS.format(rows=len(rows))]
    for row in rows:
        facts = [row["occurred_on"].isoformat(), row["kind"]]
        for metric, suffix in (("distance_km", wording.KM), ("hr_avg_bpm", wording.BPM),
                               ("rpe", wording.RPE), ("enjoyment", wording.ENJOYMENT)):
            value = _known(row, metric, suffix)
            if value:
                facts.append(value)
        facts.extend(table.pain_facts(row.get("pains") or {}))
        pending = sorted(metric for metric, measure in row["measurements"].items()
                         if measure["state"] == "pending")
        pending += table.pending_pains(row.get("pains") or {})
        if pending:
            facts.append(wording.TO_COMPLETE + ", ".join(pending))
        if row["note"]:
            facts.append(row["note"])
        facts.append(wording.KEY.format(idempotency_key=row["idempotency_key"]))
        lines.append(" · ".join(facts))
    return lines


def exposures(store: Any, limit: int, include_candidates: bool) -> list[str]:
    rows = store.exposure_rows(confirmed_only=not include_candidates)[:limit]
    scope = wording.ALL_FEMININE if include_candidates else wording.CONFIRMED
    lines = [wording.EXPOSURES.format(scope=scope, rows=len(rows))]
    for row in rows:
        dose = ""
        if row["duration_min"] is not None:
            dose += wording.DOSE_MINUTES.format(duration_min=row["duration_min"])
        if row["target_value"] is not None:
            dose += wording.DOSE_TARGET.format(target_value=row["target_value"],
                                               target_unit=row["target_unit"])
        lines.append(
            wording.EXPOSURE.format(occurred_on=row["occurred_on"],
                                    exposure_type=row["exposure_type"], origin=row["origin"],
                                    status=row["status"], tolerance=row["tolerance"], dose=dose,
                                    observation_key=row["observation_key"],
                                    exposure_key=row["exposure_key"])
            + (wording.NOTE.format(note=row["note"]) if row["note"] else ""))
    return lines


def strength(store: Any, limit: int) -> list[str]:
    """Canonical strength reports, most recent first.

    The page writes this structure before notifying the coach. Publishing it here
    keeps the coach from rebuilding a session from the CSV projection or re-entering
    it as a general observation.
    """
    rows = list(reversed(store.strength_history()))[:limit]
    lines = [wording.STRENGTH_SESSIONS.format(rows=len(rows))]
    for row in rows:
        facts = [row["performed_on"].isoformat(), wording.STRENGTH.format(workout=row["workout"])]
        if row.get("duration_minutes") is not None:
            facts.append(wording.MINUTES.format(duration_minutes=row["duration_minutes"]))
        facts.extend(wording.PAIN + fact
                     for fact in table.strength_pain_facts(row.get("pains") or {}))
        facts.append(wording.STRENGTH_KEY.format(session_key=row["session_key"]))
        lines.append(" · ".join(str(value) for value in facts))
        for exercise in row.get("exercises", []):
            detail = [exercise["exercise_slug"]]
            if exercise.get("difficulty") is not None:
                detail.append(wording.DIFFICULTY.format(difficulty=exercise["difficulty"]))
            if exercise.get("skipped"):
                detail.append(wording.SKIPPED)
            grouped: dict[str, list[str]] = {}
            for series in exercise.get("sets", []):
                grouped.setdefault(series["side"], []).append(str(series["repetitions"]))
            if grouped:
                detail.append(wording.SETS + " ; ".join(
                    wording.SIDE.format(side=side) + "|".join(values)
                    for side, values in grouped.items()))
            lines.append("  " + " · ".join(detail))
        if row.get("note"):
            lines.append(wording.STRENGTH_NOTE + row["note"])
    return lines


def threads(store: Any, include_closed: bool) -> list[str]:
    rows = [row for row in store.thread_rows() if include_closed or row["status"] == ThreadStatus.OPEN]
    lines = [wording.THREADS.format(scope=wording.ALL if include_closed else wording.OPEN,
                                    rows=len(rows))]
    for row in rows:
        follow_up = row["follow_up_on"].isoformat() if row["follow_up_on"] else wording.NO_FOLLOW_UP
        lines.append(wording.THREAD.format(thread_key=row["thread_key"], opened_on=row["opened_on"],
                                           kind=row["kind"], follow_up=follow_up,
                                           status=row["status"], body=row["body"]))
    return lines


def deadlines(store: Any, include_done: bool) -> list[str]:
    rows = [row for row in store.deadline_rows() if include_done or row["status"] != "done"]
    scope = wording.ALL_FEMININE if include_done else wording.ACTIVE
    lines = [wording.DEADLINES.format(scope=scope, rows=len(rows))]
    for row in rows:
        blocked = wording.BLOCKS.format(blocks=row["blocks"]) if row["blocks"] else ""
        period = ""
        if row.get("starts_on") or row.get("ends_on"):
            period = wording.PERIOD.format(start=row.get("starts_on") or row["due_on"],
                                           end=row.get("ends_on") or row["due_on"])
        lines.append(wording.DEADLINE.format(deadline_key=row["deadline_key"], due_on=row["due_on"],
                                             kind=row.get("kind", "general"), status=row["status"],
                                             item=row["item"], fact_owner=row["fact_owner"],
                                             blocked=blocked))
        if period:
            lines[-1] += period
        if row.get("athlete_label"):
            lines[-1] += wording.NOTEBOOK_LABEL.format(athlete_label=row["athlete_label"])
    return lines


def capabilities(store: Any) -> list[str]:
    rows = store.latest_capabilities()
    lines = [wording.CAPABILITIES.format(rows=len(rows))]
    for row in rows:
        lock = wording.LOCK.format(lock_reason=row["lock_reason"]) if row["lock_reason"] else ""
        test = wording.NEXT_TEST.format(next_test=row["next_test"]) if row["next_test"] else ""
        lines.append(wording.CAPABILITY.format(capability_number=row["capability_number"],
                                               slug=row["slug"], score=row["score"],
                                               scale=row["scale"], status=row["status"],
                                               evaluated_on=row["evaluated_on"], test=test,
                                               lock=lock))
    return lines


def _as_read(row: dict) -> str:
    """A conversation message as the coach rereads it (`src/redline/model/voice.py`)."""
    return row["body"] if row["role"] == "athlete" else voice.muted(row["body"])


def proactive(store: Any, limit: int) -> list[str]:
    rows = store.proactive_message_rows(limit=limit)
    lines = [wording.PROACTIVE.format(rows=len(rows))]
    for row in rows:
        angle = wording.ANGLE.format(angle=row["angle"]) if row["angle"] else ""
        lines.append(f"{row['sent_at'].isoformat()} · {row['kind']}{angle} · "
                     + voice.muted(row["summary"]))
    return lines


def corrections(store: Any, limit: int, now: dt.datetime | None = None) -> list[str]:
    from redline.storage.repository import CORRECTION_ANNOUNCE_WINDOW

    now = now or dt.datetime.now(dt.UTC)
    rows = store.correction_rows()[:limit]
    lines = [wording.CORRECTIONS.format(rows=len(rows))]
    for row in rows:
        if row["delivered_at"]:
            delivery = wording.DELIVERED.format(delivered_at=row["delivered_at"])
        elif (row.get("registered_at") and dt.datetime.fromisoformat(str(row["registered_at"]))
              + CORRECTION_ANNOUNCE_WINDOW <= now):
            delivery = wording.NOT_ANNOUNCED
        else:
            delivery = wording.TO_ANNOUNCE
        recurrence = (wording.RECURRENCES.format(revisions=row["revisions"],
                                                 since=str(row["first_registered_at"])[:10])
                      if row.get("revisions") else "")
        lines.append(wording.CORRECTION.format(correction_key=row["correction_key"],
                                               delivery=delivery, recurrence=recurrence,
                                               summary=row["summary"]))
    return lines


def gear(store: Any) -> list[str]:
    rows = store.gear_rows()
    lines = [wording.GEAR.format(rows=len(rows))]
    for row in rows:
        lines.append(wording.GEAR_LINE.format(gear_id=row["gear_id"], name=row["name"],
                                              role=row["role"],
                                              distance_km=float(row["distance_km"]),
                                              retire_at_km=float(row["retire_at_km"]),
                                              status=row["status"])
                     + (wording.PRIMARY if row.get("primary") else ""))
    return lines


def predictions(store: Any, *, due: bool = False, today: dt.date | None = None) -> list[str]:
    every = store.prediction_rows()
    today = today or dt.date.today()
    rows = every
    if due:
        rows = [row for row in rows if row["status"] == "open" and row["due_on"] <= today]
    label = wording.PREDICTIONS_DUE if due else wording.PREDICTIONS
    lines = [f"{label} ({len(rows)})"]
    for row in rows:
        challenge = wording.CONTRADICTED if row["challenge"] else ""
        used = wording.CONSUMED if row.get("used_at") else ""
        score = (wording.BRIER.format(brier_score=float(row["brier_score"]))
                 if row["brier_score"] is not None else "")
        zone = (wording.ZONE.format(resolves_zone=row["resolves_zone"])
                if row.get("resolves_zone") else "")
        bound = (wording.BOUND.format(kind=session_labels.short(row["resolves_kind"]), zone=zone,
                                      resolves_on=row["resolves_on"], success_op=row["success_op"],
                                      success_value=float(row["success_value"]))
                 if row.get("resolves_kind") else "")
        lines.append(wording.PREDICTION.format(prediction_key=row["prediction_key"],
                                               domain=row["domain"],
                                               probability=float(row["probability"]),
                                               due_on=row["due_on"], status=row["status"],
                                               score=score, bound=bound, challenge=challenge,
                                               used=used, statement=row["statement"]))
    lines.extend(prediction_model.calibration_lines(every, today))
    return lines


def roadmap(store: Any) -> list[str]:
    races = [row for row in store.deadline_rows()
             if row.get("kind") == "race" and row["status"] != "done"]
    lines = [wording.RACE_PLAN.format(races=len(races))]
    for race in races:
        lines.append(f"{race['item']} · {race['due_on']}")
        for checkpoint in compile_deadline(race):
            detail = checkpoint.detail
            lines.append(
                wording.MILESTONE.format(date=checkpoint.due_at.date(), title=detail["title"],
                                         action=detail["action"], evidence=detail["evidence"]))
    return lines


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    commands = root.add_subparsers(dest="command", required=True)
    question_parser = commands.add_parser("questions")
    question_parser.add_argument("--limit", type=int, default=20)
    conversation_parser = commands.add_parser(
        "conversation", help="search past Messenger exchanges")
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
        help="only open predictions that reached their due date")
    commands.add_parser("roadmap")
    messages = commands.add_parser("proactive")
    messages.add_argument("--limit", type=int, default=10)
    correction_parser = commands.add_parser("corrections")
    correction_parser.add_argument("--limit", type=int, default=20)
    permit_parser = commands.add_parser(
        "permit", help="easy-run permit: sealed choices against what was run, grant, withdrawal")
    permit_parser.add_argument("--week", help="YYYY-WNN; every week otherwise")
    return root


def main() -> int:
    args = parser().parse_args()
    if not args.database_url:
        raise ConfigError("DATABASE_URL is required")
    from redline.storage import Database, OperationalStore

    database = Database(args.database_url, application_name="fact-read")
    store = OperationalStore(database)
    limit = getattr(args, "limit", 1)
    if limit < 1 or limit > 1000:
        raise InputError("limit expected between 1 and 1000")
    if args.command == "questions":
        lines = [wording.QUESTION.format(id=row["id"], status=row["status"],
                                         asked_at=row["asked_at"],
                                         follow_up=row["follow_up_on"] or wording.NOT_SET,
                                         question=row["question"], review_note=row["review_note"])
                 for row in store.question_rows(args.limit)]
    elif args.command == "conversation":
        turn_id = os.environ.get("REDLINE_TURN_ID", "").strip()
        if not turn_id:
            raise InputError("REDLINE_TURN_ID is required for the conversation")
        lines = []
        for group in store.conversation_search_for_turn(turn_id, args.query, min(limit, 12)):
            hit = group["hit"]
            lines.append(wording.MESSAGE.format(id=hit["id"], created_at=hit["created_at"],
                                                role=hit["role"])
                         + _as_read(hit)[:1600].replace("\n", " "))
            for label in ("before", "after"):
                row = group[label]
                if row:
                    lines.append(wording.MESSAGE_CONTEXT.format(label=label,
                                                                created_at=row["created_at"],
                                                                role=row["role"])
                                 + _as_read(row)[:600].replace("\n", " "))
        if not lines:
            lines = [wording.NO_EXCHANGE]
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
            wording.NO_PERMIT]
    else:
        lines = proactive(store, args.limit)
    database.dispose()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
