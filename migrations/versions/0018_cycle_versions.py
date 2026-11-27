"""Keeps the versions of the training cycles, their trames and the cycle each revision
was written in; retires the skeleton's keys from the races' contexts.

The tables start empty: the planner writes the first version, the instance's takeover, on
its next pass. A version changes only by its state, along the moves a trigger allows; its
weeks and key sessions never change.

The peaks, the levers, the quality sessions a week and the families of a race's context
belonged to the plan skeleton, which the cycles replace: they are removed from the context,
and a downgrade does not bring them back.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

KINDS_BEFORE = ("strava_debrief", "strava_debrief_late", "silence", "ledger_incomplete",
                "morning", "weekly", "weekly_repair", "monthly", "session_followup",
                "missed_session", "daily_signal", "premortem", "correction",
                "decision_challenge", "gut_review", "gear_alert", "race_countdown",
                "strength_card", "calibration_eve", "operator_message", "wake_trace")
KINDS_AFTER = (*KINDS_BEFORE, "cycle_transition")
SKELETON_KEYS = ("skeleton", "peak_volume_km", "peak_long_run_km", "quality_per_week",
                 "families")
WEEK = "iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'"
REFS = "'first_threshold','critical_speed','goal_pace','rpe','hr_zone'"


def _listed(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _proactive_kinds(kinds: tuple[str, ...]) -> None:
    op.drop_constraint("ck_proactive_jobs_kind", "proactive_jobs", schema="redline")
    op.create_check_constraint("ck_proactive_jobs_kind", "proactive_jobs",
                               f"kind IN ({_listed(kinds)})", schema="redline")


def upgrade() -> None:
    op.create_table(
        "cycle_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
        sa.Column("cycle_key", UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.SmallInteger, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("race_id", UUID(as_uuid=True), sa.ForeignKey("redline.deadlines.id")),
        sa.Column("level", sa.Text),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("starts_on", sa.Date, nullable=False),
        sa.Column("ends_on", sa.Date, nullable=False),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("candidate", sa.Text, nullable=False, server_default=""),
        sa.Column("levers", JSONB),
        sa.Column("goal", JSONB),
        sa.Column("hypotheses", JSONB, nullable=False),
        sa.Column("risks", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cause", JSONB, nullable=False),
        sa.Column("decision", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.Column("taken_at", sa.DateTime(timezone=True)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("version >= 1", name="ck_cycle_versions_number"),
        sa.CheckConstraint("kind IN ('preparation','general','recovery','rebuild')",
                           name="ck_cycle_versions_kind"),
        sa.CheckConstraint("state IN ('proposed','active','superseded','expired','closed')",
                           name="ck_cycle_versions_state"),
        sa.CheckConstraint("level IS NULL OR level IN ('progress','maintain','light')",
                           name="ck_cycle_versions_level"),
        sa.CheckConstraint("(kind = 'general') = (level IS NOT NULL)",
                           name="ck_cycle_versions_general_level"),
        sa.CheckConstraint("kind <> 'preparation' OR race_id IS NOT NULL",
                           name="ck_cycle_versions_preparation_race"),
        sa.CheckConstraint("starts_on <= ends_on", name="ck_cycle_versions_range"),
        sa.CheckConstraint(
            "extract(isodow FROM starts_on) = 1 AND extract(isodow FROM ends_on) = 7",
            name="ck_cycle_versions_weeks"),
        sa.CheckConstraint(
            "cause->>'kind' IN ('create','replan','transition','level_change','block_end',"
            "'migration')", name="ck_cycle_versions_cause"),
        sa.CheckConstraint("state <> 'proposed' OR expires_at IS NOT NULL",
                           name="ck_cycle_versions_expiry"),
        sa.UniqueConstraint("cycle_key", "version", name="uq_cycle_versions_number"),
        schema="redline")
    op.create_index("ix_cycle_versions_active", "cycle_versions", ["athlete_id"], unique=True,
                    postgresql_where=sa.text("state = 'active'"), schema="redline")
    op.create_table(
        "cycle_weeks",
        sa.Column("cycle_version_id", UUID(as_uuid=True),
                  sa.ForeignKey("redline.cycle_versions.id"), primary_key=True),
        sa.Column("iso_week", sa.Text, primary_key=True),
        sa.Column("phase", sa.Text, nullable=False),
        sa.Column("deload", sa.Boolean, nullable=False),
        sa.Column("volume_km", sa.Numeric(5, 1), nullable=False),
        sa.Column("long_run_km", sa.Numeric(5, 1), nullable=False),
        sa.Column("qualities", sa.SmallInteger, nullable=False),
        sa.Column("segment_km", sa.Numeric(5, 1)),
        sa.Column("segment_ref", sa.Text),
        sa.Column("segment_low", sa.Numeric(5, 1)),
        sa.Column("segment_high", sa.Numeric(5, 1)),
        sa.CheckConstraint(WEEK, name="ck_cycle_weeks_iso_week"),
        sa.CheckConstraint(
            "phase IN ('recovery','base','development','specific','taper','general',"
            "'rebuild')", name="ck_cycle_weeks_phase"),
        sa.CheckConstraint("volume_km >= 0 AND long_run_km >= 0 AND qualities >= 0",
                           name="ck_cycle_weeks_figures"),
        sa.CheckConstraint(
            "(segment_km IS NULL) = (segment_ref IS NULL) AND (segment_ref IS NULL) = "
            "(segment_low IS NULL) AND (segment_low IS NULL) = (segment_high IS NULL)",
            name="ck_cycle_weeks_segment"),
        sa.CheckConstraint(f"segment_ref IS NULL OR segment_ref IN ({REFS})",
                           name="ck_cycle_weeks_segment_ref"),
        schema="redline")
    op.create_table(
        "cycle_key_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cycle_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("iso_week", sa.Text, nullable=False),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("workout", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("dose", JSONB),
        sa.Column("intensity_ref", sa.Text),
        sa.Column("intensity_low", sa.Numeric(5, 1)),
        sa.Column("intensity_high", sa.Numeric(5, 1)),
        sa.ForeignKeyConstraint(["cycle_version_id", "iso_week"],
                                ["redline.cycle_weeks.cycle_version_id",
                                 "redline.cycle_weeks.iso_week"],
                                name="fk_cycle_key_sessions_week"),
        sa.CheckConstraint("role IN ('key','flagship','test')",
                           name="ck_cycle_key_sessions_role"),
        sa.CheckConstraint(
            "(intensity_ref IS NULL) = (intensity_low IS NULL) AND "
            "(intensity_low IS NULL) = (intensity_high IS NULL)",
            name="ck_cycle_key_sessions_intensity"),
        sa.CheckConstraint(f"intensity_ref IS NULL OR intensity_ref IN ({REFS})",
                           name="ck_cycle_key_sessions_intensity_ref"),
        sa.UniqueConstraint("cycle_version_id", "iso_week", "position",
                            name="uq_cycle_key_sessions_position"),
        schema="redline")
    op.add_column("plan_revisions",
                  sa.Column("cycle_version_id", UUID(as_uuid=True),
                            sa.ForeignKey("redline.cycle_versions.id")),
                  schema="redline")
    op.execute("""
        CREATE FUNCTION redline.protect_cycle_version_number() RETURNS trigger
            LANGUAGE plpgsql
            AS $$ DECLARE expected integer;
                  BEGIN
                    SELECT coalesce(max(version), 0) + 1 INTO expected
                      FROM redline.cycle_versions WHERE cycle_key = NEW.cycle_key;
                    IF NEW.version <> expected THEN
                      RAISE EXCEPTION 'cycle % version %: the next one is %',
                        NEW.cycle_key, NEW.version, expected;
                    END IF;
                    RETURN NEW;
                  END; $$""")
    op.execute("""
        CREATE TRIGGER trg_cycle_version_contiguous
            BEFORE INSERT ON redline.cycle_versions
            FOR EACH ROW EXECUTE FUNCTION redline.protect_cycle_version_number()""")
    op.execute("""
        CREATE FUNCTION redline.protect_cycle_version() RETURNS trigger
            LANGUAGE plpgsql
            AS $$ BEGIN
                    IF TG_OP = 'DELETE' THEN
                      RAISE EXCEPTION 'a cycle version is never deleted';
                    END IF;
                    IF (to_jsonb(NEW) - 'state' - 'taken_at' - 'settled_at')
                       <> (to_jsonb(OLD) - 'state' - 'taken_at' - 'settled_at') THEN
                      RAISE EXCEPTION 'a cycle version changes only by its state';
                    END IF;
                    IF NEW.state <> OLD.state AND NOT (
                         (OLD.state = 'proposed'
                          AND NEW.state IN ('active', 'expired', 'superseded'))
                         OR (OLD.state = 'active' AND NEW.state IN ('superseded', 'closed')))
                    THEN
                      RAISE EXCEPTION 'a cycle version does not move from % to %',
                        OLD.state, NEW.state;
                    END IF;
                    RETURN NEW;
                  END; $$""")
    op.execute("""
        CREATE TRIGGER trg_cycle_version_immutable
            BEFORE UPDATE OR DELETE ON redline.cycle_versions
            FOR EACH ROW EXECUTE FUNCTION redline.protect_cycle_version()""")
    op.execute("""
        CREATE FUNCTION redline.forbid_trame_mutation() RETURNS trigger
            LANGUAGE plpgsql
            AS $$ BEGIN
                    RAISE EXCEPTION 'a trame is written once';
                  END; $$""")
    for trigger, table in (("trg_cycle_week_immutable", "cycle_weeks"),
                           ("trg_cycle_key_session_immutable", "cycle_key_sessions")):
        op.execute(f"""
            CREATE TRIGGER {trigger}
                BEFORE UPDATE OR DELETE ON redline.{table}
                FOR EACH ROW EXECUTE FUNCTION redline.forbid_trame_mutation()""")
    _proactive_kinds(KINDS_AFTER)
    removed = " - ".join(f"'{key}'" for key in SKELETON_KEYS)
    op.execute(f"""
        UPDATE redline.deadlines SET context = context - {removed}
         WHERE kind = 'race' AND context ?| array[{_listed(SKELETON_KEYS)}]""")


def downgrade() -> None:
    op.execute("DELETE FROM redline.proactive_jobs WHERE kind = 'cycle_transition'")
    _proactive_kinds(KINDS_BEFORE)
    op.drop_column("plan_revisions", "cycle_version_id", schema="redline")
    op.drop_table("cycle_key_sessions", schema="redline")
    op.drop_table("cycle_weeks", schema="redline")
    op.drop_table("cycle_versions", schema="redline")
    op.execute("DROP FUNCTION redline.forbid_trame_mutation()")
    op.execute("DROP FUNCTION redline.protect_cycle_version()")
    op.execute("DROP FUNCTION redline.protect_cycle_version_number()")
