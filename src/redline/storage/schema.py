"""Canonical relational schema.

CHECKs protect a row; UNIQUE/FK protect relationships. Rules that span several rows
stay as tested business transactions, in line with PostgreSQL guarantees.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from redline.model import commands, session_kinds
from redline.model import predictions as prediction_model
from redline.model import vocabulary as voc

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}
def _listed(values) -> str:
    return ",".join(f"'{value}'" for value in values)


def _slug(column: str) -> str:
    return f"{column} ~ '^[a-z0-9][a-z0-9-]{{0,39}}$'"


metadata = sa.MetaData(schema="redline", naming_convention=NAMING_CONVENTION)

UUID_PK = UUID(as_uuid=True)
UTC_NOW = sa.text("timezone('utc', now())")


def _id() -> sa.Column:
    return sa.Column("id", UUID_PK, primary_key=True)


athletes = sa.Table(
    "athletes", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint("id ~ '^[a-z][a-z0-9-]{1,63}$'", name="ck_athletes_id"),
)


observations = sa.Table(
    "observations", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("occurred_on", sa.Date, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("routine", sa.Text),
    sa.Column("idempotency_key", sa.Text, nullable=False),
    sa.Column("strava_activity_id", sa.BigInteger),
    sa.Column("gear_id", sa.Text),
    sa.Column("note", sa.Text, nullable=False, server_default=""),
    sa.Column("source", sa.Text, nullable=False, server_default="application"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.Column("supersedes_id", UUID_PK, sa.ForeignKey("redline.observations.id")),
    sa.CheckConstraint(f"kind IN ({_listed(session_kinds.RECORDED)})",
                       name="ck_observations_kind"),
    sa.CheckConstraint(
        f"routine IS NULL OR (kind = '{session_kinds.STRENGTH}' AND {_slug('routine')})",
        name="ck_observations_routine"),
    sa.CheckConstraint("idempotency_key <> ''", name="ck_observations_idempotency_key"),
    sa.CheckConstraint("strava_activity_id IS NULL OR strava_activity_id > 0",
                       name="ck_observations_strava_positive"),
    sa.CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id",
                       name="ck_observations_not_self_superseding"),
    sa.UniqueConstraint("athlete_id", "strava_activity_id", name="uq_observations_strava"),
    sa.UniqueConstraint("athlete_id", "idempotency_key", name="uq_observations_idempotency"),
)

strava_activity_intakes = sa.Table(
    "strava_activity_intakes", metadata,
    sa.Column("activity_id", sa.BigInteger, primary_key=True),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("sport", sa.Text, nullable=False),
    sa.Column("distilled", JSONB, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("candidate_session_keys", JSONB, nullable=False,
              server_default=sa.text("'[]'::jsonb")),
    sa.Column("observation_id", UUID_PK, sa.ForeignKey("redline.observations.id")),
    sa.Column("error", sa.Text, nullable=False, server_default=""),
    sa.Column("elevation_loss_state", sa.Text, nullable=False, server_default="pending"),
    sa.Column("elevation_loss_value", sa.Integer),
    sa.Column("elevation_loss_attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("elevation_loss_error", sa.Text, nullable=False, server_default=""),
    sa.Column("wbgt_state", sa.Text, nullable=False, server_default="pending"),
    sa.Column("wbgt_value", sa.Numeric(4, 1)),
    sa.Column("wbgt_attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("wbgt_error", sa.Text, nullable=False, server_default=""),
    # Session-detail data the logbook shows and the capture does not keep: the light
    # streams (time, HR, altitude, distance, speed, cadence) and the hourly weather at the
    # start. Provider data fetched once, on first display; never read by the coach.
    sa.Column("streams", JSONB),
    sa.Column("weather", JSONB),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False,
              server_default=UTC_NOW),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
              server_default=UTC_NOW),
    sa.CheckConstraint("activity_id > 0", name="ck_strava_activity_intakes_id"),
    sa.CheckConstraint(
        "status IN ('captured','matched','ambiguous','unmatched','failed')",
        name="ck_strava_activity_intakes_status"),
    sa.CheckConstraint("sport <> ''", name="ck_strava_activity_intakes_sport"),
    sa.CheckConstraint(
        "elevation_loss_state IN ('pending','known','unavailable','failed')",
        name="ck_strava_activity_intakes_elevation_loss_state"),
    sa.CheckConstraint(
        "wbgt_state IN ('pending','known','unavailable','failed')",
        name="ck_strava_activity_intakes_wbgt_state"),
    sa.CheckConstraint("elevation_loss_attempts >= 0",
                       name="ck_strava_activity_intakes_elevation_loss_attempts"),
    sa.CheckConstraint("wbgt_attempts >= 0",
                       name="ck_strava_activity_intakes_wbgt_attempts"),
    sa.CheckConstraint(
        "(elevation_loss_state = 'known' AND elevation_loss_value IS NOT NULL) OR "
        "(elevation_loss_state <> 'known' AND elevation_loss_value IS NULL)",
        name="ck_strava_activity_intakes_elevation_loss_value"),
    sa.CheckConstraint(
        "(wbgt_state = 'known' AND wbgt_value IS NOT NULL) OR "
        "(wbgt_state <> 'known' AND wbgt_value IS NULL)",
        name="ck_strava_activity_intakes_wbgt_value"),
    sa.UniqueConstraint("observation_id", name="uq_strava_activity_intakes_observation_id"),
)


MEASUREMENT_STATES = "('pending','known','unavailable','not_applicable')"
MEASUREMENT_DEFINITIONS: dict[str, tuple[Any, str]] = {
    "distance_km": (sa.Numeric(7, 3), "value >= 0 AND value <= 1000"),
    "duration_s": (sa.Integer, "value >= 0 AND value <= 604800"),
    "pace_s_per_km": (sa.Integer, "value BETWEEN 60 AND 3600"),
    "hr_avg_bpm": (sa.SmallInteger, "value BETWEEN 20 AND 250"),
    "hr_max_bpm": (sa.SmallInteger, "value BETWEEN 20 AND 250"),
    "cadence_spm": (sa.SmallInteger, "value BETWEEN 0 AND 300"),
    "elevation_gain_m": (sa.Integer, "value BETWEEN 0 AND 20000"),
    "elevation_loss_m": (sa.Integer, "value BETWEEN 0 AND 20000"),
    "wbgt_c": (sa.Numeric(4, 1), "value BETWEEN -50 AND 60"),
    "rpe": (sa.Numeric(3, 1), "value BETWEEN 0 AND 10"),
    "enjoyment": (sa.Numeric(3, 1), "value BETWEEN 0 AND 10"),
    "carbs_g_h": (sa.Numeric(5, 1), "value BETWEEN 0 AND 200"),
    "gut_symptoms": (sa.Numeric(3, 1), "value BETWEEN 0 AND 10"),
    # Reserve of a quality session: whole repetitions still possible at the same effort,
    # declared in the logbook.
    "reps_in_reserve": (sa.SmallInteger, "value BETWEEN 0 AND 5"),
}


def _measurement_columns() -> list[Any]:
    items: list[Any] = []
    for metric, (column_type, known_range) in MEASUREMENT_DEFINITIONS.items():
        state = f"{metric}_state"
        items.extend((
            sa.Column(metric, column_type),
            sa.Column(state, sa.Text, nullable=False),
            sa.CheckConstraint(f"{state} IN {MEASUREMENT_STATES}",
                               name=f"ck_observation_metrics_{metric}_state"),
            sa.CheckConstraint(
                f"({state} = 'known' AND {metric} IS NOT NULL AND "
                f"({known_range.replace('value', metric)})) OR "
                f"({state} <> 'known' AND {metric} IS NULL)",
                name=f"ck_observation_metrics_{metric}_value"),
        ))
    return items


observation_metrics = sa.Table(
    "observation_metrics", metadata,
    sa.Column("observation_id", UUID_PK,
              sa.ForeignKey("redline.observations.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    *_measurement_columns(),
    # Declared average HR flagged as doubtful: the measure stays immutable, the annotation
    # says what it is worth (`vocabulary.HrDoubt`).
    sa.Column("hr_doubt", sa.Text),
    sa.CheckConstraint(
        "hr_avg_bpm_state <> 'known' OR hr_max_bpm_state <> 'known' "
        "OR hr_avg_bpm <= hr_max_bpm", name="ck_observation_metrics_hr_order"),
    sa.CheckConstraint(
        f"hr_doubt IS NULL OR (hr_doubt IN ({voc.sql_values(voc.HrDoubt)}) "
        "AND hr_avg_bpm_state = 'known')", name="ck_observation_metrics_hr_doubt"),
)

# Zones the instance watches (`[[injuries]]` in redline.toml), synchronized at startup:
# the reference for pain readings. A zone removed from the config becomes inactive, never
# deleted -- its history stays readable and attached.
PAIN_ZONE_PATTERN = "^[a-z0-9][a-z0-9-]{0,39}$"
pain_zones = sa.Table(
    "pain_zones", metadata,
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), primary_key=True),
    sa.Column("slug", sa.Text, primary_key=True),
    sa.Column("label", sa.Text, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint(f"slug ~ '{PAIN_ZONE_PATTERN}'", name="ck_pain_zones_slug"),
    sa.CheckConstraint("label <> '' AND char_length(label) <= 40", name="ck_pain_zones_label"),
)

# Pain of an observation, ONE ROW PER ZONE: two zones never share a column, otherwise a
# rise in one zone would read as a rise in another. Same states and same immutability as
# the other measures (`observation_metrics`).
pain_readings = sa.Table(
    "pain_readings", metadata,
    sa.Column("observation_id", UUID_PK,
              sa.ForeignKey("redline.observations.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("zone", sa.Text, primary_key=True),
    sa.Column("athlete_id", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("value", sa.Numeric(3, 1)),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.ForeignKeyConstraint(["athlete_id", "zone"],
                            ["redline.pain_zones.athlete_id", "redline.pain_zones.slug"],
                            name="fk_pain_readings_zone"),
    sa.CheckConstraint(f"state IN {MEASUREMENT_STATES}", name="ck_pain_readings_state"),
    sa.CheckConstraint(
        "(state = 'known' AND value IS NOT NULL AND value BETWEEN 0 AND 10) OR "
        "(state <> 'known' AND value IS NULL)", name="ck_pain_readings_value"),
)

# An exposure is a declared intention/dose, not a cause reconstructed from an average.
# `candidate` keeps a hypothesis out of the calculations until confirmed.
session_exposures = sa.Table(
    "session_exposures", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("observation_id", UUID_PK,
              sa.ForeignKey("redline.observations.id", ondelete="CASCADE"), nullable=False),
    sa.Column("exposure_key", sa.Text, nullable=False),
    sa.Column("exposure_type", sa.Text, nullable=False),
    sa.Column("origin", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("duration_min", sa.SmallInteger),
    sa.Column("target_value", sa.Numeric(8, 2)),
    sa.Column("target_unit", sa.Text),
    sa.Column("tolerance", sa.Text, nullable=False, server_default="unknown"),
    sa.Column("note", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.UniqueConstraint("athlete_id", "exposure_key", name="uq_session_exposures_key"),
    sa.CheckConstraint(
        f"exposure_type IN ({voc.sql_values(voc.Exposure)})",
        name="ck_session_exposures_type"),
    sa.CheckConstraint("origin IN ('prescribed','voluntary','spontaneous')",
                       name="ck_session_exposures_origin"),
    sa.CheckConstraint("status IN ('candidate','confirmed','dismissed')",
                       name="ck_session_exposures_status"),
    sa.CheckConstraint("duration_min IS NULL OR duration_min BETWEEN 1 AND 1440",
                       name="ck_session_exposures_duration"),
    sa.CheckConstraint("target_value IS NULL OR target_value >= 0",
                       name="ck_session_exposures_target_value"),
    sa.CheckConstraint("(target_value IS NULL) = (target_unit IS NULL)",
                       name="ck_session_exposures_target_pair"),
    sa.CheckConstraint("tolerance IN ('unknown','easy','strained','protective')",
                       name="ck_session_exposures_tolerance"),
    sa.CheckConstraint("exposure_key <> ''", name="ck_session_exposures_key"),
)

daily_checkins = sa.Table(
    "daily_checkins", metadata,
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), primary_key=True),
    sa.Column("checked_on", sa.Date, primary_key=True),
    sa.Column("sleep", sa.Text),
    sa.Column("life_load", sa.Text),
    sa.Column("desire", sa.Text),
    sa.Column("note", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint(f"sleep IS NULL OR sleep IN ({voc.sql_values(voc.Sleep)})",
                       name="ck_daily_checkins_sleep"),
    sa.CheckConstraint(f"life_load IS NULL OR life_load IN ({voc.sql_values(voc.LifeLoad)})",
                       name="ck_daily_checkins_life_load"),
    sa.CheckConstraint(f"desire IS NULL OR desire IN ({voc.sql_values(voc.Desire)})",
                       name="ck_daily_checkins_desire"),
)

weekly_instruments = sa.Table(
    "weekly_instruments", metadata,
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), primary_key=True),
    sa.Column("iso_week", sa.Text, primary_key=True),
    sa.Column("reviewed_on", sa.Date, nullable=False),
    sa.Column("cmj_cm", sa.Numeric(7, 2)),
    sa.Column("calf_right_straight", sa.SmallInteger),
    sa.Column("calf_left_straight", sa.SmallInteger),
    sa.Column("calf_right_bent", sa.SmallInteger),
    sa.Column("calf_left_bent", sa.SmallInteger),
    sa.Column("enjoyment", sa.Numeric(4, 1)),
    sa.Column("canary_pain", sa.Numeric(4, 1)),
    sa.Column("pain_calibration_gap", sa.Numeric(5, 2)),
    sa.Column("hr_calibration_gap", sa.Numeric(7, 2)),
    sa.Column("sleep", sa.Text),
    sa.Column("life_load", sa.Text),
    sa.Column("note", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint("iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'",
                       name="ck_weekly_instruments_week"),
    sa.CheckConstraint(f"sleep IS NULL OR sleep IN ({voc.sql_values(voc.WeekSleep)})",
                       name="ck_weekly_instruments_sleep"),
    sa.CheckConstraint(f"life_load IS NULL OR life_load IN ({voc.sql_values(voc.WeekLifeLoad)})",
                       name="ck_weekly_instruments_life_load"),
    sa.CheckConstraint("cmj_cm IS NULL OR cmj_cm BETWEEN 0 AND 100",
                       name="ck_weekly_instruments_cmj"),
    sa.CheckConstraint("calf_right_straight IS NULL OR calf_right_straight BETWEEN 0 AND 999",
                       name="ck_weekly_instruments_calf_right_straight"),
    sa.CheckConstraint("calf_left_straight IS NULL OR calf_left_straight BETWEEN 0 AND 999",
                       name="ck_weekly_instruments_calf_left_straight"),
    sa.CheckConstraint("calf_right_bent IS NULL OR calf_right_bent BETWEEN 0 AND 999",
                       name="ck_weekly_instruments_calf_right_bent"),
    sa.CheckConstraint("calf_left_bent IS NULL OR calf_left_bent BETWEEN 0 AND 999",
                       name="ck_weekly_instruments_calf_left_bent"),
    sa.CheckConstraint("enjoyment IS NULL OR enjoyment BETWEEN 0 AND 10",
                       name="ck_weekly_instruments_enjoyment"),
    sa.CheckConstraint("canary_pain IS NULL OR canary_pain BETWEEN 0 AND 10",
                       name="ck_weekly_instruments_canary_pain"),
)

strength_sessions = sa.Table(
    "strength_sessions", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("session_key", sa.Text, nullable=False),
    sa.Column("performed_on", sa.Date, nullable=False),
    sa.Column("workout", sa.Text),
    sa.Column("duration_minutes", sa.SmallInteger),
    sa.Column("note", sa.Text, nullable=False, server_default=""),
    sa.Column("prescription", JSONB(none_as_null=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.UniqueConstraint("athlete_id", "session_key", name="uq_strength_sessions_key"),
    sa.CheckConstraint("session_key <> '' AND char_length(session_key) <= 64",
                       name="ck_strength_sessions_key"),
    sa.CheckConstraint(f"workout IS NULL OR {_slug('workout')}",
                       name="ck_strength_sessions_workout"),
    sa.CheckConstraint("duration_minutes IS NULL OR duration_minutes BETWEEN 0 AND 300",
                       name="ck_strength_sessions_duration"),
    sa.CheckConstraint("char_length(note) <= 300", name="ck_strength_sessions_note"),
)

# Pain of a strength session, before and after, per watched zone.
strength_pain_readings = sa.Table(
    "strength_pain_readings", metadata,
    sa.Column("strength_session_id", UUID_PK,
              sa.ForeignKey("redline.strength_sessions.id", ondelete="CASCADE"),
              primary_key=True),
    sa.Column("zone", sa.Text, primary_key=True),
    sa.Column("moment", sa.Text, primary_key=True),
    sa.Column("athlete_id", sa.Text, nullable=False),
    sa.Column("value", sa.SmallInteger, nullable=False),
    sa.ForeignKeyConstraint(["athlete_id", "zone"],
                            ["redline.pain_zones.athlete_id", "redline.pain_zones.slug"],
                            name="fk_strength_pain_readings_zone"),
    sa.CheckConstraint("moment IN ('before','after')", name="ck_strength_pain_readings_moment"),
    sa.CheckConstraint("value BETWEEN 0 AND 10", name="ck_strength_pain_readings_value"),
)

strength_results = sa.Table(
    "strength_results", metadata,
    _id(),
    sa.Column("session_id", UUID_PK,
              sa.ForeignKey("redline.strength_sessions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("position", sa.SmallInteger, nullable=False),
    sa.Column("exercise_slug", sa.Text, nullable=False),
    sa.Column("difficulty", sa.SmallInteger),
    sa.Column("skipped", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("status", sa.Text),
    sa.Column("completed_slots", sa.SmallInteger),
    sa.Column("note", sa.Text, nullable=False, server_default=""),
    sa.UniqueConstraint("session_id", "exercise_slug", name="uq_strength_results_exercise"),
    sa.UniqueConstraint("session_id", "position", name="uq_strength_results_position"),
    sa.CheckConstraint("position >= 1", name="ck_strength_results_position"),
    sa.CheckConstraint("difficulty IS NULL OR difficulty BETWEEN 0 AND 10",
                       name="ck_strength_results_difficulty"),
    sa.CheckConstraint("NOT skipped OR difficulty IS NULL", name="ck_strength_results_skipped"),
    sa.CheckConstraint("status IS NULL OR status IN ('completed','partial','skipped','gated','unknown')",
                       name="ck_strength_results_status"),
    sa.CheckConstraint("completed_slots IS NULL OR completed_slots BETWEEN 0 AND 24",
                       name="ck_strength_results_slots"),
    sa.CheckConstraint("char_length(note) <= 300", name="ck_strength_results_note"),
)

strength_sets = sa.Table(
    "strength_sets", metadata,
    sa.Column("result_id", UUID_PK,
              sa.ForeignKey("redline.strength_results.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("side", sa.Text, primary_key=True),
    sa.Column("set_index", sa.SmallInteger, primary_key=True),
    sa.Column("repetitions", sa.SmallInteger, nullable=False),
    sa.CheckConstraint("side IN ('both','right','left')", name="ck_strength_sets_side"),
    sa.CheckConstraint("set_index >= 1", name="ck_strength_sets_index"),
    sa.CheckConstraint("repetitions BETWEEN 0 AND 999", name="ck_strength_sets_repetitions"),
)

weekly_plans = sa.Table(
    "weekly_plans", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("iso_week", sa.Text, nullable=False),
    sa.UniqueConstraint("athlete_id", "iso_week", name="uq_weekly_plans_week"),
    sa.CheckConstraint("iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'",
                       name="ck_weekly_plans_week"),
)

plan_revisions = sa.Table(
    "plan_revisions", metadata,
    _id(),
    sa.Column("plan_id", UUID_PK,
              sa.ForeignKey("redline.weekly_plans.id", ondelete="CASCADE"), nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    # Database clock, distinct from the producer-supplied `computed_at` of the plan. It is
    # the canonical watermark for whether an activity arrived AFTER the arbitration; a
    # fingerprint cannot tell a technical contract change from a new fact.
    sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=False,
              server_default=UTC_NOW),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("constraints", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("changes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("calibration_target", sa.SmallInteger),
    # Targeted running volume of the week: the intention, never the fact (the ledger).
    sa.Column("volume_target_km", sa.Numeric(5, 1)),
    sa.UniqueConstraint("plan_id", "revision", name="uq_plan_revisions_number"),
    sa.CheckConstraint("revision >= 1", name="ck_plan_revisions_number"),
    sa.CheckConstraint("calibration_target IS NULL OR calibration_target IN (0, 2, 3)",
                       name="ck_plan_revisions_calibration_target"),
    sa.CheckConstraint("volume_target_km IS NULL OR volume_target_km BETWEEN 1 AND 200",
                       name="ck_plan_revisions_volume_target"),
)

planned_sessions = sa.Table(
    "planned_sessions", metadata,
    _id(),
    sa.Column("revision_id", UUID_PK,
              sa.ForeignKey("redline.plan_revisions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("logical_key", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("scheduled_on", sa.Date, nullable=False),
    sa.Column("start_time", sa.Time, nullable=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("workout", sa.Text, nullable=False),
    sa.Column("routine", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("distance_target_km", sa.Numeric(7, 2)),
    sa.Column("distance_cap_km", sa.Numeric(7, 2)),
    sa.Column("duration_cap_min", sa.Integer),
    sa.Column("rpe_min", sa.SmallInteger),
    sa.Column("rpe_max", sa.SmallInteger),
    sa.Column("hr_zone", sa.Text),
    sa.Column("hr_cap_bpm", sa.SmallInteger),
    sa.Column("cadence_target", sa.SmallInteger),
    sa.Column("demanding", sa.Boolean, nullable=False),
    # Typed guardrail: the kind that drops out of the last 30 days. NULL = known session.
    sa.Column("transition", sa.Text),
    sa.Column("calibration", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("location_label", sa.Text),
    sa.Column("carbs_target_g_h", sa.SmallInteger),
    # For optional JSONB, a business absence is an SQL NULL. The SQLAlchemy JSONB default
    # otherwise turns Python None into JSON `null`: `IS NULL` becomes false and conditional
    # constraints take an absence for a present object. The PostgreSQL CI covers this
    # representation.
    sa.Column("strength_spec", JSONB(none_as_null=True)),
    sa.Column("note", sa.Text),
    sa.UniqueConstraint("revision_id", "logical_key", name="uq_planned_sessions_key"),
    sa.CheckConstraint("logical_key ~ '^[a-z0-9][a-z0-9-]*$'",
                       name="ck_planned_sessions_logical_key"),
    sa.CheckConstraint(
        f"kind IN ({_listed(dict.fromkeys(map(session_kinds.family, session_kinds.PLANNED)))})",
        name="ck_planned_sessions_kind"),
    sa.CheckConstraint(f"workout IN ({_listed(session_kinds.PLANNED)})",
                       name="ck_planned_sessions_workout"),
    sa.CheckConstraint(
        f"(workout = '{session_kinds.STRENGTH}') = (routine IS NOT NULL AND {_slug('routine')})",
        name="ck_planned_sessions_routine"),
    # The plan carries the intention. ``done`` belongs exclusively to
    # ``session_cycles.state`` and is only added in the read views.
    sa.CheckConstraint("status IN ('planned','cancelled')",
                       name="ck_planned_sessions_status"),
    sa.CheckConstraint("rpe_min IS NULL OR rpe_min BETWEEN 0 AND 10",
                       name="ck_planned_sessions_rpe_min"),
    sa.CheckConstraint("rpe_max IS NULL OR rpe_max BETWEEN 0 AND 10",
                       name="ck_planned_sessions_rpe_max"),
    sa.CheckConstraint("rpe_min IS NULL OR rpe_max IS NULL OR rpe_min <= rpe_max",
                       name="ck_planned_sessions_rpe_order"),
    sa.CheckConstraint("distance_target_km IS NULL OR distance_cap_km IS NULL OR "
                       "distance_target_km <= distance_cap_km",
                       name="ck_planned_sessions_distance_order"),
    sa.CheckConstraint("distance_target_km IS NULL OR distance_target_km >= 0",
                       name="ck_planned_sessions_distance_target"),
    sa.CheckConstraint("distance_cap_km IS NULL OR distance_cap_km >= 0",
                       name="ck_planned_sessions_distance_cap"),
    sa.CheckConstraint("duration_cap_min IS NULL OR duration_cap_min >= 1",
                       name="ck_planned_sessions_duration_cap"),
    sa.CheckConstraint("cadence_target IS NULL OR cadence_target BETWEEN 120 AND 220",
                       name="ck_planned_sessions_cadence_target"),
    sa.CheckConstraint("hr_cap_bpm IS NULL OR hr_cap_bpm BETWEEN 80 AND 220",
                       name="ck_planned_sessions_hr_cap"),
    sa.CheckConstraint("carbs_target_g_h IS NULL OR carbs_target_g_h BETWEEN 0 AND 150",
                       name="ck_planned_sessions_carbs_target"),
    sa.CheckConstraint(
        "strength_spec IS NULL OR jsonb_typeof(strength_spec) = 'object'",
        name="ck_planned_sessions_strength_spec"),
    sa.CheckConstraint(
        f"(workout = '{session_kinds.STRENGTH}') = (strength_spec IS NOT NULL)",
        name="ck_planned_sessions_strength_owner"),
    # Possible kinds are those of the instance rulebook (`rulebook.toml`), checked when the
    # program is written: the database only keeps the shape.
    sa.CheckConstraint(
        "transition IS NULL OR transition ~ '^[a-z0-9][a-z0-9-]{0,39}$'",
        name="ck_planned_sessions_transition"),
)

session_cycles = sa.Table(
    "session_cycles", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("plan_id", UUID_PK,
              sa.ForeignKey("redline.weekly_plans.id", ondelete="CASCADE"), nullable=False),
    sa.Column("logical_key", sa.Text, nullable=False),
    sa.Column("source_session_id", UUID_PK,
              sa.ForeignKey("redline.planned_sessions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("observation_id", UUID_PK,
              sa.ForeignKey("redline.observations.id"), unique=True),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("planned_for", sa.DateTime(timezone=True)),
    sa.Column("plan_digest", sa.Text, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("declared_started_at", sa.DateTime(timezone=True)),
    sa.Column("observed_started_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.UniqueConstraint("plan_id", "logical_key", name="uq_session_cycles_plan_key"),
    sa.CheckConstraint("logical_key ~ '^[a-z0-9][a-z0-9-]*$'",
                       name="ck_session_cycles_logical_key"),
    sa.CheckConstraint(
        "state IN ('planned','started','completed','missed','cancelled',"
        "'followup_due','closed')", name="ck_session_cycles_state"),
    sa.CheckConstraint("plan_digest ~ '^[0-9a-f]{64}$'",
                       name="ck_session_cycles_plan_digest"),
)
sa.Index("ix_session_cycles_state_planned_for",
         session_cycles.c.state, session_cycles.c.planned_for)

session_predictions = sa.Table(
    "session_predictions", metadata,
    _id(),
    sa.Column("cycle_id", UUID_PK,
              sa.ForeignKey("redline.session_cycles.id", ondelete="CASCADE"),
              nullable=False, unique=True),
    sa.Column("predicted_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("predicted_pain", sa.Numeric(3, 1), nullable=False),
    sa.Column("predicted_morning_trace", sa.Text, nullable=False),
    sa.Column("predicted_hr", sa.SmallInteger, nullable=False),
    sa.Column("validity", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False, server_default="messenger"),
    sa.CheckConstraint("predicted_pain BETWEEN 0 AND 10",
                       name="ck_session_predictions_pain"),
    sa.CheckConstraint("predicted_hr BETWEEN 20 AND 250",
                       name="ck_session_predictions_hr"),
    sa.CheckConstraint("predicted_morning_trace IN ('none','light','clear')",
                       name="ck_session_predictions_morning_trace"),
    sa.CheckConstraint("validity IN ('pending','valid','late')",
                       name="ck_session_predictions_validity"),
)

session_followups = sa.Table(
    "session_followups", metadata,
    _id(),
    sa.Column("cycle_id", UUID_PK,
              sa.ForeignKey("redline.session_cycles.id", ondelete="CASCADE"),
              nullable=False, unique=True),
    sa.Column("observation_id", UUID_PK,
              sa.ForeignKey("redline.observations.id"), nullable=True, unique=True),
    sa.Column("outcome", sa.Text, nullable=False, server_default="pending"),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
              server_default=UTC_NOW),
    sa.Column("resolved_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("outcome IN ('pending','answered','unanswered')",
                       name="ck_session_followups_outcome"),
    sa.CheckConstraint(
        "(outcome = 'answered' AND observation_id IS NOT NULL AND resolved_at IS NOT NULL) OR "
        "(outcome = 'unanswered' AND observation_id IS NULL AND resolved_at IS NOT NULL) OR "
        "(outcome = 'pending' AND observation_id IS NULL AND resolved_at IS NULL)",
        name="ck_session_followups_resolution"),
)

planned_session_sources = sa.Table(
    "planned_session_sources", metadata,
    sa.Column("session_id", UUID_PK,
              sa.ForeignKey("redline.planned_sessions.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("position", sa.SmallInteger, primary_key=True),
    sa.Column("source", sa.Text, nullable=False),
    sa.CheckConstraint("position >= 1", name="ck_planned_session_sources_position"),
    sa.CheckConstraint("source <> ''", name="ck_planned_session_sources_source"),
)

# The two kinds of dated check. A PostgreSQL ENUM rather than free text: the value is
# closed in the model, so it must be closed here too.
gate_when = sa.Enum("avant", "apres", name="gate_when", schema="redline")

plan_gates = sa.Table(
    "plan_gates", metadata,
    _id(),
    sa.Column("revision_id", UUID_PK,
              sa.ForeignKey("redline.plan_revisions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("logical_key", sa.Text, nullable=False),
    sa.Column("due_on", sa.Date, nullable=False),
    sa.Column("question", sa.Text, nullable=False),
    sa.Column("on_fail", sa.Text, nullable=False),
    # `when` and `holds` type the check: a precondition that holds named sessions back, or an
    # observation that holds nothing back. A relational store without a column silently drops
    # fields it does not know, so a revision would go in valid and come back mutilated.
    # `redline.gate_when` rather than a CHECK: the model has two values, so does the database.
    sa.Column("applies_when", gate_when, nullable=False),
    sa.Column("holds", JSONB(none_as_null=True), nullable=True),
    sa.UniqueConstraint("revision_id", "logical_key", name="uq_plan_gates_key"),
    sa.CheckConstraint("logical_key ~ '^[a-z0-9][a-z0-9-]*$'",
                       name="ck_plan_gates_logical_key"),
    sa.CheckConstraint("length(question) >= 10", name="ck_plan_gates_question"),
    sa.CheckConstraint("length(on_fail) >= 3", name="ck_plan_gates_on_fail"),
    sa.CheckConstraint(
        "(applies_when = 'avant' AND jsonb_typeof(holds) = 'array' "
        "AND jsonb_array_length(holds) > 0) OR "
        "(applies_when = 'apres' AND holds IS NULL)",
        name="ck_plan_gates_holds"),
)

capability_evaluations = sa.Table(
    "capability_evaluations", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("capability_number", sa.SmallInteger, nullable=False),
    sa.Column("slug", sa.Text, nullable=False),
    sa.Column("need", sa.Text, nullable=False),
    sa.Column("score", sa.SmallInteger, nullable=False),
    sa.Column("scale", sa.SmallInteger, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("evaluated_on", sa.Date, nullable=False),
    sa.Column("next_test", sa.Text),
    sa.Column("lock_reason", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.UniqueConstraint("athlete_id", "capability_number", "evaluated_on",
                        name="uq_capability_evaluations_day"),
    sa.CheckConstraint("score BETWEEN 0 AND scale", name="ck_capability_evaluations_score"),
    sa.CheckConstraint("capability_number BETWEEN 1 AND 9",
                       name="ck_capability_evaluations_number"),
    sa.CheckConstraint("scale BETWEEN 1 AND 9", name="ck_capability_evaluations_scale"),
    sa.CheckConstraint(f"status IN ({voc.sql_values(voc.CapabilityStatus)})",
                       name="ck_capability_evaluations_status"),
)

conversation_threads = sa.Table(
    "conversation_threads", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("thread_key", sa.Text, nullable=False),
    sa.Column("opened_on", sa.Date, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("follow_up_on", sa.Date),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("closed_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint(f"kind IN ({voc.sql_values(voc.ThreadKind)})",
                       name="ck_conversation_threads_kind"),
    sa.CheckConstraint(f"status IN ({voc.sql_values(voc.ThreadStatus)})",
                       name="ck_conversation_threads_status"),
    sa.CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)",
                       name="ck_conversation_threads_closed_at"),
    sa.UniqueConstraint("athlete_id", "thread_key", name="uq_conversation_threads_key"),
)

conversation_messages = sa.Table(
    "conversation_messages", metadata,
    _id(),
    sa.Column("psid", sa.Text, nullable=False),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("source_key", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint("role IN ('athlete','coach','proactive')",
                       name="ck_conversation_messages_role"),
    sa.CheckConstraint("body <> ''", name="ck_conversation_messages_body"),
    sa.UniqueConstraint("psid", "source_key", name="uq_conversation_messages_source"),
)

messenger_message_refs = sa.Table(
    "messenger_message_refs", metadata,
    sa.Column("message_id", sa.Text, primary_key=True),
    sa.Column("conversation_message_id", UUID_PK,
              sa.ForeignKey("redline.conversation_messages.id", ondelete="CASCADE",
                            name="fk_messenger_message_refs_conversation_message"),
              nullable=False),
    # A coach reply can be split into several Messenger texts. The exact body of each chunk
    # is kept here: replying to the second must not quote the whole burst indiscriminately.
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
              server_default=UTC_NOW),
    sa.CheckConstraint("message_id <> '' AND body <> ''",
                       name="ck_messenger_message_refs_identity"),
)

conversation_questions = sa.Table(
    "conversation_questions", metadata,
    _id(),
    sa.Column("psid", sa.Text, nullable=False),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("question", sa.Text, nullable=False),
    sa.Column("source_message_id", UUID_PK,
              sa.ForeignKey("redline.conversation_messages.id", ondelete="CASCADE",
                            name="fk_conversation_questions_source_message"),
              nullable=False),
    sa.Column("source_platform_message_id", sa.Text,
              sa.ForeignKey("redline.messenger_message_refs.message_id",
                            ondelete="SET NULL",
                            name="fk_conversation_questions_source_platform")),
    sa.Column("status", sa.Text, nullable=False, server_default="open"),
    # Message explicitly examined by the coach; never a lexical match.
    sa.Column("candidate_answer_message_id", UUID_PK,
              sa.ForeignKey("redline.conversation_messages.id", ondelete="SET NULL",
                            name="fk_conversation_questions_candidate_answer")),
    sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.Column("resolved_at", sa.DateTime(timezone=True)),
    sa.Column("review_note", sa.Text, nullable=False, server_default=""),
    sa.Column("follow_up_on", sa.Date),
    sa.CheckConstraint("length(review_note) <= 500", name="ck_conversation_questions_note"),
    sa.CheckConstraint("status = 'deferred' OR follow_up_on IS NULL",
                       name="ck_conversation_questions_follow_up"),
    sa.CheckConstraint("status IN ('open','deferred','answered','expired')",
                       name="ck_conversation_questions_status"),
    sa.CheckConstraint("(status IN ('open','deferred')) = (resolved_at IS NULL)",
                       name="ck_conversation_questions_resolution"),
)

deadlines = sa.Table(
    "deadlines", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("deadline_key", sa.Text, nullable=False),
    sa.Column("due_on", sa.Date, nullable=False),
    sa.Column("item", sa.Text, nullable=False),
    sa.Column("fact_owner", sa.Text, nullable=False),
    sa.Column("blocks", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("completed_on", sa.Date),
    sa.Column("kind", sa.Text, nullable=False, server_default="general"),
    sa.Column("starts_on", sa.Date),
    sa.Column("ends_on", sa.Date),
    sa.Column("context", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    # Present = an athlete action, shown in the logbook under this short label.
    # Absent = a coach working deadline, which never leaves the vault.
    sa.Column("athlete_label", sa.Text),
    sa.CheckConstraint("status IN ('todo','scheduled','done')", name="ck_deadlines_status"),
    sa.CheckConstraint("athlete_label IS NULL OR char_length(athlete_label) BETWEEN 3 AND 80",
                       name="ck_deadlines_athlete_label"),
    sa.CheckConstraint("completed_on IS NULL OR status = 'done'",
                       name="ck_deadlines_completed_on"),
    sa.CheckConstraint("kind IN ('general','medical','availability','race')",
                       name="ck_deadlines_kind"),
    sa.CheckConstraint("starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on",
                       name="ck_deadlines_range"),
    sa.UniqueConstraint("athlete_id", "deadline_key", name="uq_deadlines_key"),
)

gear_items = sa.Table(
    "gear_items", metadata,
    sa.Column("gear_id", sa.Text, primary_key=True),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("retire_at_km", sa.Numeric(7, 1), nullable=False),
    sa.Column("strava_distance_km", sa.Numeric(9, 1)),
    sa.Column("distance_synced_at", sa.DateTime(timezone=True)),
    sa.Column("primary", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint("gear_id <> '' AND name <> ''", name="ck_gear_items_identity"),
    sa.CheckConstraint("retire_at_km > 0", name="ck_gear_items_retire"),
    sa.CheckConstraint("strava_distance_km IS NULL OR strava_distance_km >= 0",
                       name="ck_gear_items_strava_distance"),
    sa.CheckConstraint("status IN ('active','retired')", name="ck_gear_items_status"),
)

coach_predictions = sa.Table(
    "coach_predictions", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("prediction_key", sa.Text, nullable=False),
    sa.Column("domain", sa.Text, nullable=False),
    sa.Column("statement", sa.Text, nullable=False),
    sa.Column("probability", sa.Numeric(4, 3), nullable=False),
    sa.Column("due_on", sa.Date, nullable=False),
    sa.Column("direction", sa.Text, nullable=False, server_default="neutral"),
    sa.Column("status", sa.Text, nullable=False, server_default="open"),
    sa.Column("outcome", sa.Boolean),
    sa.Column("brier_score", sa.Numeric(7, 6)),
    sa.Column("challenge", sa.Text),
    sa.Column("challenged_at", sa.DateTime(timezone=True)),
    sa.Column("used_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.Column("resolved_at", sa.DateTime(timezone=True)),
    # The observation that settles the prediction: once written, the prediction settles by
    # itself; with the date passed without it, it becomes `void`, never false.
    sa.Column("resolves_kind", sa.Text),
    sa.Column("resolves_on", sa.Date),
    # The zone whose waking pain settles the prediction.
    sa.Column("resolves_zone", sa.Text),
    sa.Column("success_op", sa.Text),
    sa.Column("success_value", sa.Numeric(4, 1)),
    # Why a prediction failed or could not be tested; the calibration counts each reason.
    sa.Column("outcome_reason", sa.Text),
    sa.UniqueConstraint("athlete_id", "prediction_key", name="uq_coach_predictions_key"),
    sa.CheckConstraint("probability BETWEEN 0 AND 1", name="ck_coach_predictions_probability"),
    sa.CheckConstraint("direction IN ('increase','decrease','neutral')",
                       name="ck_coach_predictions_direction"),
    sa.CheckConstraint("status IN ('open','resolved','void')",
                       name="ck_coach_predictions_status"),
    sa.CheckConstraint(
        "(status = 'resolved' AND outcome IS NOT NULL AND brier_score IS NOT NULL "
        "AND resolved_at IS NOT NULL) OR "
        "(status = 'open' AND outcome IS NULL AND brier_score IS NULL AND resolved_at IS NULL) OR "
        "(status = 'void' AND outcome IS NULL AND brier_score IS NULL AND resolved_at IS NOT NULL)",
        name="ck_coach_predictions_resolution"),
    sa.CheckConstraint(
        "(resolves_kind IS NULL AND resolves_on IS NULL AND resolves_zone IS NULL "
        "AND success_op IS NULL AND success_value IS NULL) OR "
        f"(resolves_kind IN ('{session_kinds.WAKE_TRACE}') "
        "AND resolves_on IS NOT NULL AND resolves_zone IS NOT NULL "
        "AND success_op IN ('<=','<','>=','>','=') "
        "AND success_value IS NOT NULL AND resolves_on <= due_on)",
        name="ck_coach_predictions_resolves"),
    sa.CheckConstraint(prediction_model.OUTCOME_REASON_RULE,
                       name="ck_coach_predictions_outcome_reason"),
    sa.ForeignKeyConstraint(["athlete_id", "resolves_zone"],
                            ["redline.pain_zones.athlete_id", "redline.pain_zones.slug"],
                            name="fk_coach_predictions_zone"),
)

# Easy-run permit: the coach's choice for a delegated easy run, sealed BEFORE the athlete
# decides. Immutable: a second identical write is idempotent, a different write is
# refused -- a choice corrected after the fact proves nothing.
permit_choices = sa.Table(
    "permit_choices", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("domain", sa.Text, nullable=False, server_default="easy"),
    sa.Column("iso_week", sa.Text, nullable=False),
    sa.Column("session_key", sa.Text, nullable=False),
    sa.Column("scheduled_on", sa.Date, nullable=False),
    sa.Column("coach_minutes", sa.SmallInteger, nullable=False),
    sa.Column("prediction_key", sa.Text, nullable=False),
    sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.UniqueConstraint("athlete_id", "iso_week", "session_key", name="uq_permit_choices_session"),
    sa.CheckConstraint("domain IN ('easy')", name="ck_permit_choices_domain"),
    sa.CheckConstraint("iso_week ~ '^20[0-9]{2}-W[0-9]{2}$'", name="ck_permit_choices_week"),
    sa.CheckConstraint("session_key ~ '^[a-z0-9][a-z0-9-]*$'", name="ck_permit_choices_key"),
    sa.CheckConstraint("coach_minutes BETWEEN 10 AND 180", name="ck_permit_choices_minutes"),
    sa.CheckConstraint("prediction_key <> ''", name="ck_permit_choices_prediction"),
)

correction_deliveries = sa.Table(
    "correction_deliveries", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("correction_key", sa.Text, nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
    sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.Column("delivered_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("athlete_id", "correction_key", name="uq_correction_deliveries_key"),
    sa.CheckConstraint("correction_key <> '' AND summary <> ''",
                       name="ck_correction_deliveries_identity"),
)

agent_sessions = sa.Table(
    "agent_sessions", metadata,
    sa.Column("psid", sa.Text, primary_key=True),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("session_id", sa.Text, nullable=False),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    # Fingerprint of the instructions the session was opened under. A session is derived from
    # its prompt: when the prompt changes, the session is stale (see
    # `redline.service.prompts.STYLE_DIGEST`). Nullable: older sessions carry none, and an
    # absent fingerprint is treated as a stale one.
    sa.Column("prompt_digest", sa.Text),
    sa.CheckConstraint("provider IN ('codex','claude')", name="ck_agent_sessions_provider"),
    sa.CheckConstraint("psid <> '' AND session_id <> ''", name="ck_agent_sessions_identity"),
)

handoffs = sa.Table(
    "handoffs", metadata,
    sa.Column("psid", sa.Text, primary_key=True),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

inbound_activity_states = sa.Table(
    "inbound_activity_states", metadata,
    sa.Column("psid", sa.Text, primary_key=True),
    sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=False),
)

inbound_messages = sa.Table(
    "inbound_messages", metadata,
    sa.Column("message_id", sa.Text, primary_key=True),
    sa.Column("psid", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("reply_to_message_id", sa.Text),
    sa.Column("status", sa.Text, nullable=False, server_default="pending"),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("message_id <> '' AND psid <> ''", name="ck_inbound_messages_identity"),
    sa.CheckConstraint("reply_to_message_id IS NULL OR reply_to_message_id <> ''",
                       name="ck_inbound_messages_reply"),
    sa.CheckConstraint("status IN ('pending','done')", name="ck_inbound_messages_status"),
    sa.CheckConstraint("kind IN ('text','postback','sticker','attachment')",
                       name="ck_inbound_messages_kind"),
)

patience_notifications = sa.Table(
    "patience_notifications", metadata,
    sa.Column("singleton", sa.Boolean, primary_key=True, server_default=sa.true()),
    sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("singleton", name="ck_patience_notifications_singleton"),
)

athlete_activity_states = sa.Table(
    "athlete_activity_states", metadata,
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), primary_key=True),
    sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
)

strength_report_daily_quotas = sa.Table(
    "strength_report_daily_quotas", metadata,
    sa.Column("quota_on", sa.Date, primary_key=True),
    sa.Column("accepted_count", sa.Integer, nullable=False),
    sa.CheckConstraint("accepted_count >= 0", name="ck_strength_report_daily_quotas_count"),
)

proactive_delivery_budgets = sa.Table(
    "proactive_delivery_budgets", metadata,
    sa.Column("budget_on", sa.Date, primary_key=True),
    sa.Column("sent_count", sa.Integer, nullable=False),
    sa.Column("last_sent_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("sent_count >= 0", name="ck_proactive_delivery_budgets_count"),
)

proactive_jobs = sa.Table(
    "proactive_jobs", metadata,
    sa.Column("job_key", sa.Text, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("detail", sa.Text, nullable=False, server_default=""),
    sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True)),
    sa.Column("priority", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("status", sa.Text, nullable=False, server_default="pending"),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    # Why a job stopped without delivering. `done` means delivered or deliberately silent;
    # `failed` means lost, and the cause stays readable without a log.
    sa.Column("last_error", sa.Text, nullable=False, server_default=""),
    sa.CheckConstraint("status IN ('pending','running','done','failed')",
                       name="ck_proactive_jobs_status"),
    sa.CheckConstraint(
        "kind IN ('strava_debrief','strava_debrief_differe','silence',"
        "'registre_incomplet','morning','weekly','weekly_repair','monthly',"
        "'session_followup','missed_session','daily_signal','premortem','correction',"
        "'decision_challenge','gut_review','gear_alert','race_countdown','strength_card',"
        "'calibration_eve','operator_message','trace_reveil')",
        name="ck_proactive_jobs_kind"),
    sa.CheckConstraint("length(last_error) <= 500", name="ck_proactive_jobs_last_error"),
    sa.CheckConstraint("attempts >= 0", name="ck_proactive_jobs_attempts"),
    sa.CheckConstraint("expires_at IS NULL OR expires_at >= due_at",
                       name="ck_proactive_jobs_expiry"),
)

proactive_markers = sa.Table(
    "proactive_markers", metadata,
    sa.Column("marker_key", sa.Text, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint("marker_key <> ''", name="ck_proactive_markers_key"),
)

proactive_messages = sa.Table(
    "proactive_messages", metadata,
    _id(),
    sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
    sa.Column("source_key", sa.Text, nullable=False),
    sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("angle", sa.Text),
    sa.Column("summary", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.UniqueConstraint("athlete_id", "source_key", name="uq_proactive_messages_source_key"),
    sa.CheckConstraint("source_key <> ''", name="ck_proactive_messages_source_key"),
    sa.CheckConstraint("summary <> ''", name="ck_proactive_messages_summary"),
    sa.CheckConstraint(
        "kind IN ('decision','morning','debrief','weekly','monthly','correction','other')",
        name="ck_proactive_messages_kind"),
)

strength_report_claims = sa.Table(
    "strength_report_claims", metadata,
    sa.Column("session_key", sa.Text, primary_key=True),
    sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
)

strength_session_links = sa.Table(
    "strength_session_links", metadata,
    # Raw capability in the fragment and the browser only. A backup leak or an SQL read must
    # not be enough to open a session.
    sa.Column("token_hash", sa.Text, primary_key=True),
    sa.Column("psid", sa.Text, nullable=False),
    sa.Column("session_key", sa.Text, nullable=False),
    sa.Column("snapshot", JSONB, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
              server_default=UTC_NOW),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'",
                       name="ck_strength_session_links_token_hash"),
    sa.CheckConstraint("psid <> ''", name="ck_strength_session_links_psid"),
    sa.CheckConstraint("session_key <> '' AND char_length(session_key) <= 64",
                       name="ck_strength_session_links_session_key"),
    sa.CheckConstraint("jsonb_typeof(snapshot) = 'object'",
                       name="ck_strength_session_links_snapshot"),
    sa.CheckConstraint("expires_at > created_at",
                       name="ck_strength_session_links_expiry"),
    sa.CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at",
                       name="ck_strength_session_links_revocation"),
)

delivery_debts = sa.Table(
    "delivery_debts", metadata,
    sa.Column("psid", sa.Text, primary_key=True),
    sa.Column("iso_week", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint("iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'",
                       name="ck_delivery_debts_week"),
    sa.CheckConstraint("revision >= 1", name="ck_delivery_debts_revision"),
)

outbound_deliveries = sa.Table(
    "outbound_deliveries", metadata,
    sa.Column("source_key", sa.Text, primary_key=True),
    sa.Column("psid", sa.Text, nullable=False),
    sa.Column("payload_digest", sa.Text, nullable=False),
    # The computed envelope itself: a resume RETURNS it instead of redoing the model turn that
    # produced it. `prepared` = computed, not yet attempted (proactive held back because the
    # athlete is writing, for example).
    sa.Column("payload", JSONB),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
    sa.Column("last_error", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
              server_default=UTC_NOW),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
              server_default=UTC_NOW),
    sa.CheckConstraint("source_key <> '' AND psid <> ''",
                       name="ck_outbound_deliveries_identity"),
    sa.CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'",
                       name="ck_outbound_deliveries_digest"),
    sa.CheckConstraint("status IN ('prepared','sending','delivered','failed')",
                       name="ck_outbound_deliveries_status"),
    sa.CheckConstraint("payload IS NULL OR jsonb_typeof(payload) = 'object'",
                       name="ck_outbound_deliveries_payload"),
    sa.CheckConstraint("attempts >= 1", name="ck_outbound_deliveries_attempts"),
)

turn_scopes = sa.Table(
    "turn_scopes", metadata,
    sa.Column("turn_id", UUID_PK, primary_key=True),
    sa.Column("psid", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint("psid <> ''", name="ck_turn_scopes_psid"),
)

turn_commands = sa.Table(
    "turn_commands", metadata,
    _id(),
    sa.Column("turn_id", UUID_PK, nullable=False),
    sa.Column("command_key", sa.Text, nullable=False, unique=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.Column("applied_at", sa.DateTime(timezone=True)),
    # Already-applied `proactive_message.add` rows stay as the idempotence proof of past turns,
    # but no new intention of this type can be created: it has no handler anymore.
    sa.CheckConstraint(
        "kind IN (" + ",".join(f"'{kind}'" for kind in commands.ACTIVE) + ") OR "
        "(kind = 'proactive_message.add' AND applied_at IS NOT NULL)",
        name="ck_turn_commands_kind"),
    sa.CheckConstraint("command_key <> ''", name="ck_turn_commands_command_key"),
)

projection_states = sa.Table(
    "projection_states", metadata,
    sa.Column("singleton", sa.Boolean, primary_key=True, server_default=sa.true()),
    sa.Column("dirty_since", sa.DateTime(timezone=True)),
    sa.Column("last_projected_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("singleton", name="ck_projection_states_singleton"),
)

audit_events = sa.Table(
    "audit_events", metadata,
    _id(),
    sa.Column("actor", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("aggregate_type", sa.Text, nullable=False),
    sa.Column("aggregate_id", UUID_PK, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
)


# Invariants that look at the OLD row, which this file would otherwise not mention.
#
# A CHECK judges an isolated row. "A known measure is immutable", "revisions are
# contiguous", "the audit is append-only" compare the old value to the new one: they are
# triggers, and they lived ONLY in the migrations that created them. Consequences:
# `alembic check` compares metadata, so it cannot see them nor their disappearance; and
# anyone reading this file -- the canonical schema -- could not know they exist.
#
# What is declared here is their PRESENCE, not their body: duplicating the PL/pgSQL would
# make two sources of truth for one function. `test_postgres_storage.py` compares this
# table to `pg_trigger` both ways -- nothing missing, nothing unknown -- and the SQL
# UPDATE/DELETE tests prove their bodies actually bite.
TRIGGERS: dict[str, str] = {
    "trg_measurement_history": "observation_metrics",
    "trg_pain_reading_history": "pain_readings",
    "trg_plan_revision_contiguous": "plan_revisions",
    "trg_plan_revision_immutable": "plan_revisions",
    "trg_audit_event_immutable": "audit_events",
    "trg_proactive_message_immutable": "proactive_messages",
    "trg_strength_session_link_immutable": "strength_session_links",
}


# Indexes are part of the current model, so Alembic can detect their drift.
sa.Index("ix_observations_day", observations.c.athlete_id, observations.c.occurred_on.desc())
sa.Index("ix_observations_supersedes", observations.c.supersedes_id)
sa.Index("ix_turn_commands_pending", turn_commands.c.turn_id,
         postgresql_where=turn_commands.c.applied_at.is_(None))
sa.Index("ix_turn_scopes_created", turn_scopes.c.created_at)
# A question can only be open once; once resolved, the same question asked later is a new
# question (a daily "how do you feel this morning?" must not stay "already handled" for
# good).
sa.Index("ix_conversation_questions_open_fingerprint",
         conversation_questions.c.psid, conversation_questions.c.fingerprint, unique=True,
         postgresql_where=conversation_questions.c.status.in_(("open", "deferred")))
sa.Index("ix_proactive_jobs_claim", proactive_jobs.c.priority.desc(), proactive_jobs.c.due_at,
         postgresql_where=proactive_jobs.c.status == "pending")
sa.Index("ix_proactive_messages_sent_at", proactive_messages.c.athlete_id,
         proactive_messages.c.sent_at.desc())
sa.Index("ix_outbound_deliveries_updated", outbound_deliveries.c.updated_at)
sa.Index("ix_inbound_messages_received", inbound_messages.c.received_at)
sa.Index("ix_inbound_messages_pending", inbound_messages.c.received_at,
         postgresql_where=inbound_messages.c.status == "pending")
sa.Index("ix_conversation_messages_recent", conversation_messages.c.psid,
         conversation_messages.c.created_at.desc())
sa.Index("ix_conversation_messages_fts",
         sa.text("to_tsvector('french'::regconfig, body)"),
         postgresql_using="gin", _table=conversation_messages)
sa.Index("ix_messenger_message_refs_conversation", messenger_message_refs.c.conversation_message_id)
sa.Index("ix_strength_session_links_expiry", strength_session_links.c.expires_at)
sa.Index("ix_conversation_questions_open", conversation_questions.c.psid,
         postgresql_where=conversation_questions.c.status == "open")
sa.Index("ix_coach_predictions_due", coach_predictions.c.due_on,
         postgresql_where=coach_predictions.c.status == "open")
sa.Index("ix_coach_predictions_resolves", coach_predictions.c.resolves_kind,
         coach_predictions.c.resolves_on,
         postgresql_where=sa.text("status = 'open' AND resolves_kind IS NOT NULL"))
sa.Index("ix_audit_events_created", audit_events.c.created_at)

notebook_entries = sa.Table(
    "notebook_entries", metadata,
    _id(),
    sa.Column("psid", sa.Text, nullable=False),
    sa.Column("source_key", sa.Text, nullable=False),
    sa.Column("slot", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("period", sa.Text, nullable=False, server_default=""),
    sa.Column("occurred_on", sa.Date, nullable=False),
    sa.Column("body", sa.Text, nullable=False, server_default=""),
    sa.Column("image", sa.LargeBinary),
    sa.Column("image_type", sa.Text, nullable=False, server_default="image/png"),
    sa.Column("image_hash", sa.Text, nullable=False, server_default=""),
    sa.Column("snapshot", JSONB(none_as_null=True)),
    sa.Column("origin", sa.Text, nullable=False, server_default="delivery"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint("psid <> '' AND source_key <> '' AND slot <> ''", name="ck_notebook_entries_identity"),
    sa.CheckConstraint("kind IN ('weekly','monthly','strength','other')", name="ck_notebook_entries_kind"),
    sa.CheckConstraint("origin IN ('delivery','recovered')", name="ck_notebook_entries_origin"),
    sa.CheckConstraint("image IS NULL OR octet_length(image) <= 12582912", name="ck_notebook_entries_image"),
    sa.UniqueConstraint("psid", "source_key", "slot", name="uq_notebook_entries_source"),
)
sa.Index("ix_notebook_entries_calendar", notebook_entries.c.psid, notebook_entries.c.occurred_on)

notebook_credentials = sa.Table(
    "notebook_credentials", metadata,
    sa.Column("token_hash", sa.Text, primary_key=True),
    sa.Column("psid", sa.Text, nullable=False),
    sa.Column("purpose", sa.Text, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_notebook_credentials_hash"),
    sa.CheckConstraint("psid <> ''", name="ck_notebook_credentials_psid"),
    sa.CheckConstraint("purpose IN ('link','device')", name="ck_notebook_credentials_purpose"),
)
