"""The closed vocabularies stored in PostgreSQL become English slugs.

Daily and weekly sleep and life load, desire, the reason an average heart rate is
doubtful, exposure types, the predicted morning trace, capability lights, conversation
threads and the easy-run permit domain. Each column keeps its CHECK constraint, under the
same name, rewritten for the new values; existing rows are translated in place.

Revision ID: 20261029_0007
Revises: 20261028_0006
"""

from __future__ import annotations

from alembic import op

revision = "20261029_0007"
down_revision = "20261028_0006"
branch_labels = None
depends_on = None

# (table, column, constraint, {old: new}, rule with `{values}`)
COLUMNS = (
    ("daily_checkins", "sleep", "ck_daily_checkins_sleep",
     {"bonne": "good", "moyenne": "fair", "mauvaise": "poor"},
     "sleep IS NULL OR sleep IN ({values})"),
    ("daily_checkins", "life_load", "ck_daily_checkins_life_load",
     {"legere": "light", "normale": "normal", "elevee": "high"},
     "life_load IS NULL OR life_load IN ({values})"),
    ("daily_checkins", "desire", "ck_daily_checkins_desire",
     {"forte": "high", "normale": "normal", "basse": "low"},
     "desire IS NULL OR desire IN ({values})"),
    ("weekly_instruments", "sleep", "ck_weekly_instruments_sleep",
     {"ok": "ok", "2_3_mauvaises_nuits": "bad_nights", "degrade_semaine": "bad_week"},
     "sleep IS NULL OR sleep IN ({values})"),
    ("weekly_instruments", "life_load", "ck_weekly_instruments_life_load",
     {"normale": "normal", "elevee": "high"},
     "life_load IS NULL OR life_load IN ({values})"),
    ("observation_metrics", "hr_doubt", "ck_observation_metrics_hr_doubt",
     {"sans_brassard": "no_sensor", "contexte": "context"},
     "hr_doubt IS NULL OR (hr_doubt IN ({values}) AND hr_avg_bpm_state = 'known')"),
    ("session_exposures", "exposure_type", "ck_session_exposures_type",
     {"cadence": "cadence", "descente": "descent", "lignes": "strides",
      "nutrition": "nutrition", "equipement": "gear", "autre": "other"},
     "exposure_type IN ({values})"),
    ("session_predictions", "predicted_morning_trace", "ck_session_predictions_morning_trace",
     {"aucune": "none", "legere": "light", "nette": "clear"},
     "predicted_morning_trace IN ({values})"),
    ("capability_evaluations", "status", "ck_capability_evaluations_status",
     {"rouge": "red", "orange": "amber", "vert": "green"},
     "status IN ({values})"),
    ("conversation_threads", "kind", "ck_conversation_threads_kind",
     {"promesse": "promise", "ressenti": "feeling", "suspens": "open_loop"},
     "kind IN ({values})"),
    ("conversation_threads", "status", "ck_conversation_threads_status",
     {"ouvert": "open", "clos": "closed"},
     "status IN ({values})"),
    ("permit_choices", "domain", "ck_permit_choices_domain",
     {"ef": "easy"},
     "domain IN ({values})"),
)
CLOSED_AT = "ck_conversation_threads_closed_at"
CLOSED_RULE = "(status = '{closed}') = (closed_at IS NOT NULL)"


def _quoted(values) -> str:
    return ",".join(f"'{value}'" for value in values)


def _translate(table: str, column: str, mapping: dict[str, str]) -> None:
    cases = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in mapping.items() if old != new)
    if cases:
        op.execute(f"UPDATE redline.{table} SET {column} = CASE {column} {cases} "
                   f"ELSE {column} END WHERE {column} IN ({_quoted(mapping)})")


def _move(forward: bool) -> None:
    op.drop_constraint(CLOSED_AT, "conversation_threads", type_="check", schema="redline")
    for table, column, name, mapping, rule in COLUMNS:
        if not forward:
            mapping = {new: old for old, new in mapping.items()}
        op.drop_constraint(name, table, type_="check", schema="redline")
        _translate(table, column, mapping)
        op.create_check_constraint(name, table, rule.format(values=_quoted(mapping.values())),
                                   schema="redline")
    op.alter_column("permit_choices", "domain", server_default="easy" if forward else "ef",
                    schema="redline")
    op.create_check_constraint(
        CLOSED_AT, "conversation_threads",
        CLOSED_RULE.format(closed="closed" if forward else "clos"), schema="redline")


def upgrade() -> None:
    _move(forward=True)


def downgrade() -> None:
    _move(forward=False)
