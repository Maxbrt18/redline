"""Session kinds get one English slug, and strength sessions name their routine.

The plan and the ledger spelled the same kinds differently (`cotes` and `côtes`,
`velo` and `vélo`, `renfo-a` and `renfoA`). Both now use the slugs of
`scripts/model/session_kinds.py`. The two hard-coded strength kinds become one,
`strength`, with the routine in its own column: routines are declared by the
instance's workout notes, so the engine no longer names them.

`session_cycles.plan_digest` covers the workout, so every digest is recomputed. A
digest is replaced only when the old one is reproduced from the stored session first;
one that is not stays as it is and the cycle reads as revised at the next sync, which
is what happened before for any change of the plan.

Run with the service stopped: a turn staged before the upgrade would carry old slugs.
The downgrade refuses a strength session whose routine is neither `a` nor `b`.

Revision ID: 20261028_0004
Revises: 20261027_0003
Create Date: 2026-10-28
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "20261028_0004"
down_revision = "20261027_0003"
branch_labels = None
depends_on = None

SCHEMA = "redline"
SLUG = "~ '^[a-z0-9][a-z0-9-]{0,39}$'"

#: Old ledger kind -> (new kind, routine).
OBSERVATION_KINDS = {
    "EF": ("easy", None), "SL": ("long", None), "seuil": ("threshold", None),
    "côtes": ("hills", None), "lignes": ("strides", None), "cadence": ("cadence", None),
    "course": ("race", None), "rando": ("hike", None), "vélo": ("bike", None),
    "renfoA": ("strength", "a"), "renfoB": ("strength", "b"),
    "instruments": ("instruments", None), "lecture": ("rest-reading", None),
    "reveil": ("wake-trace", None), "palpation": ("palpation", None),
}
#: Old plan workout -> (new workout, routine).
PLANNED_WORKOUTS = {
    "EF": ("easy", None), "SL": ("long", None), "seuil": ("threshold", None),
    "cotes": ("hills", None), "lignes": ("strides", None), "cadence": ("cadence", None),
    "course": ("race", None), "renfo-a": ("strength", "a"), "renfo-b": ("strength", "b"),
    "test-mollet": ("test", None), "repos": ("rest", None), "velo": ("bike", None),
    "rando": ("hike", None),
}


def _listed(values) -> str:
    return ",".join(f"'{value}'" for value in values)


OLD = {
    "ck_observations_kind": ("observations", f"kind IN ({_listed(OBSERVATION_KINDS)})"),
    "ck_planned_sessions_kind": (
        "planned_sessions", "kind IN ('run','strength','test','rest','bike','hike')"),
    "ck_planned_sessions_workout": (
        "planned_sessions", f"workout IN ({_listed(PLANNED_WORKOUTS)})"),
    "ck_planned_sessions_strength_owner": (
        "planned_sessions", "(workout IN ('renfo-a','renfo-b')) = (strength_spec IS NOT NULL)"),
    "ck_strength_sessions_workout": (
        "strength_sessions", "workout IS NULL OR workout IN ('a','b','assimilation')"),
}
NEW_OBSERVATION_KINDS = ("easy", "long", "threshold", "hills", "strides", "cadence", "race",
                         "hike", "bike", "strength", "wake-trace", "rest-reading",
                         "instruments", "palpation")
NEW_WORKOUTS = ("easy", "long", "threshold", "hills", "strides", "cadence", "race", "hike",
                "bike", "strength", "test", "rest")
NEW = {
    "ck_observations_kind": ("observations", f"kind IN ({_listed(NEW_OBSERVATION_KINDS)})"),
    "ck_observations_routine": (
        "observations", f"routine IS NULL OR (kind = 'strength' AND routine {SLUG})"),
    "ck_planned_sessions_kind": (
        "planned_sessions", "kind IN ('run','hike','bike','strength','test','rest')"),
    "ck_planned_sessions_workout": ("planned_sessions", f"workout IN ({_listed(NEW_WORKOUTS)})"),
    "ck_planned_sessions_strength_owner": (
        "planned_sessions", "(workout = 'strength') = (strength_spec IS NOT NULL)"),
    "ck_planned_sessions_routine": (
        "planned_sessions", f"(workout = 'strength') = (routine IS NOT NULL AND routine {SLUG})"),
    "ck_strength_sessions_workout": ("strength_sessions", f"workout IS NULL OR workout {SLUG}"),
}
RESOLVES = "ck_coach_predictions_resolves"
RESOLVES_RULE = (
    "(resolves_kind IS NULL AND resolves_on IS NULL AND resolves_zone IS NULL "
    "AND success_op IS NULL AND success_value IS NULL) OR (resolves_kind IN ('{kind}') "
    "AND resolves_on IS NOT NULL AND resolves_zone IS NOT NULL "
    "AND success_op IN ('<=','<','>=','>','=') "
    "AND success_value IS NOT NULL AND resolves_on <= due_on)")

#: The fields of `session_cycle.plan_digest`, frozen here as they were and as they are.
DIGEST_BEFORE = ("title", "date", "start_time", "kind", "workout", "status", "calibration",
                 "location_label", "carbs_target_g_h", "distance_target_km",
                 "distance_cap_km", "duration_cap_min", "rpe_min", "rpe_max", "hr_zone",
                 "cadence_target", "note")
DIGEST_AFTER = ("title", "date", "start_time", "kind", "workout", "routine", "status",
                "calibration", "location_label", "carbs_target_g_h", "distance_target_km",
                "distance_cap_km", "duration_cap_min", "rpe_min", "rpe_max", "hr_zone",
                "cadence_target", "note")


def _digest(session: dict, keys: tuple[str, ...]) -> str:
    visible = {key: session.get(key) for key in keys}
    canonical = json.dumps(visible, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _readings(row) -> list[dict]:
    """The stored session as the plan payload may have spelled it: numbers come back
    from NUMERIC as decimals, a whole number may have been written either way, and
    plans written before `calibration` left the payload still carried it."""
    base = {key: row[key] for key in (
        "title", "kind", "workout", "status", "location_label", "carbs_target_g_h",
        "duration_cap_min", "rpe_min", "rpe_max", "hr_zone", "cadence_target", "note")}
    base["date"] = row["scheduled_on"].isoformat()
    base["start_time"] = row["start_time"].strftime("%H:%M") if row["start_time"] else None
    variants = []
    for whole, calibration in ((False, None), (True, None),
                               (False, row["calibration"]), (True, row["calibration"])):
        session = dict(base, calibration=calibration)
        for key in ("distance_target_km", "distance_cap_km"):
            value = row[key]
            if value is not None:
                value = float(value)
                if whole and value.is_integer():
                    value = int(value)
            session[key] = value
        variants.append(session)
    return variants


def _old_workout(row) -> str:
    return next(old for old, new in PLANNED_WORKOUTS.items()
                if new == (row["workout"], row["routine"]))


def _new_spelling(row) -> dict:
    return {"workout": row["workout"], "routine": row["routine"]}


def _old_spelling(row) -> dict:
    return {"workout": _old_workout(row)}


def _redigest(bind, stored, stored_keys: tuple, target, target_keys: tuple) -> None:
    """Replace each digest that the stored session reproduces under `stored_keys`.

    `stored` and `target` give the workout spelling of each side for a row read with
    the new columns.
    """
    rows = bind.execute(sa.text(
        "SELECT c.id AS cycle_id, c.plan_digest, s.* FROM redline.session_cycles c "
        "JOIN redline.planned_sessions s ON s.id = c.source_session_id")).mappings().all()
    for row in rows:
        for session in _readings(row):
            if _digest({**session, **stored(row)}, stored_keys) != row["plan_digest"]:
                continue
            bind.execute(sa.text(
                "UPDATE redline.session_cycles SET plan_digest = :digest WHERE id = :id"),
                {"digest": _digest({**session, **target(row)}, target_keys),
                 "id": row["cycle_id"]})
            break


def _rename_markers(bind, pairs) -> None:
    """`asked:<date>:<kind>:<column>` markers follow their kind; two old kinds that
    become one keep a single marker."""
    for old, new in pairs:
        bind.execute(sa.text(
            "INSERT INTO redline.proactive_markers (marker_key, created_at) "
            "SELECT regexp_replace(marker_key, '^(asked:[0-9-]+:)[^:]+(:)', "
            "'\\1' || :new || '\\2'), created_at FROM redline.proactive_markers "
            "WHERE marker_key LIKE 'asked:%:' || :old || ':%' ON CONFLICT DO NOTHING"),
            {"old": old, "new": new})
        bind.execute(sa.text(
            "DELETE FROM redline.proactive_markers "
            "WHERE marker_key LIKE 'asked:%:' || :old || ':%'"), {"old": old})


def _drop(names) -> None:
    tables = {**OLD, **NEW}
    for name in names:
        op.drop_constraint(name, tables[name][0], type_="check", schema=SCHEMA)


def upgrade() -> None:
    bind = op.get_bind()
    _drop(OLD)
    op.drop_constraint(RESOLVES, "coach_predictions", type_="check", schema=SCHEMA)
    op.add_column("observations", sa.Column("routine", sa.Text()), schema=SCHEMA)
    op.add_column("planned_sessions", sa.Column("routine", sa.Text()), schema=SCHEMA)
    for old, (new, routine) in OBSERVATION_KINDS.items():
        bind.execute(sa.text(
            "UPDATE redline.observations SET kind = :new, routine = :routine "
            "WHERE kind = :old"), {"old": old, "new": new, "routine": routine})
    for old, (new, routine) in PLANNED_WORKOUTS.items():
        bind.execute(sa.text(
            "UPDATE redline.planned_sessions SET workout = :new, routine = :routine "
            "WHERE workout = :old"), {"old": old, "new": new, "routine": routine})
    bind.execute(sa.text(
        "UPDATE redline.coach_predictions SET resolves_kind = 'wake-trace' "
        "WHERE resolves_kind = 'reveil'"))
    _redigest(bind, _old_spelling, DIGEST_BEFORE, _new_spelling, DIGEST_AFTER)
    _rename_markers(bind, [(old, new) for old, (new, _) in OBSERVATION_KINDS.items()
                           if old != new])
    for name, (table, rule) in NEW.items():
        op.create_check_constraint(name, table, rule, schema=SCHEMA)
    op.create_check_constraint(RESOLVES, "coach_predictions",
                               RESOLVES_RULE.format(kind="wake-trace"), schema=SCHEMA)


def downgrade() -> None:
    bind = op.get_bind()
    unknown = bind.execute(sa.text(
        "SELECT count(*) FROM redline.planned_sessions WHERE workout = 'strength' "
        "AND routine NOT IN ('a','b') UNION ALL SELECT count(*) FROM redline.observations "
        "WHERE kind = 'strength' AND coalesce(routine, '') NOT IN ('a','b') UNION ALL "
        "SELECT count(*) FROM redline.strength_sessions "
        "WHERE workout NOT IN ('a','b','assimilation')")).scalars().all()
    if any(unknown):
        raise RuntimeError("strength routines other than a and b have no old spelling")
    _drop(NEW)
    op.drop_constraint(RESOLVES, "coach_predictions", type_="check", schema=SCHEMA)
    _redigest(bind, _new_spelling, DIGEST_AFTER, _old_spelling, DIGEST_BEFORE)
    for old, (new, routine) in OBSERVATION_KINDS.items():
        condition = "routine = :routine" if routine else "routine IS NULL"
        bind.execute(sa.text(
            f"UPDATE redline.observations SET kind = :old WHERE kind = :new AND {condition}"),
            {"old": old, "new": new, "routine": routine})
    for old, (new, routine) in PLANNED_WORKOUTS.items():
        condition = "routine = :routine" if routine else "routine IS NULL"
        bind.execute(sa.text(
            f"UPDATE redline.planned_sessions SET workout = :old "
            f"WHERE workout = :new AND {condition}"),
            {"old": old, "new": new, "routine": routine})
    bind.execute(sa.text(
        "UPDATE redline.coach_predictions SET resolves_kind = 'reveil' "
        "WHERE resolves_kind = 'wake-trace'"))
    _rename_markers(bind, [(new, old) for old, (new, routine) in OBSERVATION_KINDS.items()
                           if old != new and routine != "b"])
    op.drop_column("planned_sessions", "routine", schema=SCHEMA)
    op.drop_column("observations", "routine", schema=SCHEMA)
    for name, (table, rule) in OLD.items():
        op.create_check_constraint(name, table, rule, schema=SCHEMA)
    op.create_check_constraint(RESOLVES, "coach_predictions",
                               RESOLVES_RULE.format(kind="reveil"), schema=SCHEMA)
