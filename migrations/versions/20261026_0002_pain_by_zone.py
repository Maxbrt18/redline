"""Pain is stored per zone: one row per monitored zone, never a column.

`observation_metrics.pain` held a single series: two zones would have poured their
scores into the same column. The pain of an observation now lives in `pain_readings`
(one row per zone, same states and same immutability as the other measurements), that
of a strength session in `strength_pain_readings` (before / after), and a prediction
settled by the wake-up check names the zone it expects. The zones themselves
(`pain_zones`) are the reference fed by `redline.toml`.

A database that already holds pain attaches it to ONE zone, which the operator names:

    alembic -x pain_zone=genou -x pain_zone_label=Genou upgrade head

Without these arguments the migration refuses rather than inventing a zone. A new
database does not need them.

Revision ID: 20261026_0002
Revises: 20261026_0001
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import context, op

revision = "20261026_0002"
down_revision = "20261026_0001"
branch_labels = None
depends_on = None

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

# Les mesures de `observation_metrics` avant et après cette révision.
MEASUREMENTS_BEFORE = (
    "distance_km", "duration_s", "pace_s_per_km", "hr_avg_bpm", "hr_max_bpm",
    "cadence_spm", "elevation_gain_m", "elevation_loss_m", "wbgt_c", "rpe", "pain",
    "enjoyment", "carbs_g_h", "gut_symptoms", "reps_in_reserve",
)
MEASUREMENTS_AFTER = tuple(metric for metric in MEASUREMENTS_BEFORE if metric != "pain")

RESOLVES_BEFORE = (
    "(resolves_kind IS NULL AND resolves_on IS NULL AND success_op IS NULL "
    "AND success_value IS NULL) OR (resolves_kind IN ('reveil') "
    "AND resolves_on IS NOT NULL AND success_op IN ('<=','<','>=','>','=') "
    "AND success_value IS NOT NULL AND resolves_on <= due_on)")
RESOLVES_AFTER = (
    "(resolves_kind IS NULL AND resolves_on IS NULL AND resolves_zone IS NULL "
    "AND success_op IS NULL AND success_value IS NULL) OR (resolves_kind IN ('reveil') "
    "AND resolves_on IS NOT NULL AND resolves_zone IS NOT NULL "
    "AND success_op IN ('<=','<','>=','>','=') "
    "AND success_value IS NOT NULL AND resolves_on <= due_on)")


def _protect_measurements(metrics: tuple[str, ...]) -> None:
    known_changes = " OR ".join(
        f"(OLD.{metric}_state = 'known' AND "
        f"(NEW.{metric}_state, NEW.{metric}) IS DISTINCT FROM "
        f"(OLD.{metric}_state, OLD.{metric}))"
        for metric in metrics
    )
    op.execute(sa.text(f"""
        CREATE OR REPLACE FUNCTION redline.protect_measurement_history() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF {known_changes} THEN
            RAISE EXCEPTION 'une mesure connue est immuable; créer une observation correctrice';
          END IF;
          RETURN NEW;
        END;
        $$
    """))


def _existing_pain(connection: sa.Connection) -> int:
    return connection.execute(sa.text("""
        SELECT (SELECT count(*) FROM redline.observation_metrics
                WHERE pain_state <> 'not_applicable')
             + (SELECT count(*) FROM redline.strength_sessions
                WHERE shin_before IS NOT NULL OR shin_after IS NOT NULL)
             + (SELECT count(*) FROM redline.coach_predictions
                WHERE resolves_kind IS NOT NULL)
    """)).scalar_one()


def _operator_zone() -> tuple[str, str]:
    arguments = context.get_x_argument(as_dictionary=True)
    slug = (arguments.get("pain_zone") or "").strip()
    label = (arguments.get("pain_zone_label") or "").strip()
    if not SLUG.match(slug) or not label or len(label) > 40:
        raise RuntimeError(
            "cette base porte déjà de la douleur : nomme la zone qui la reçoit, ex. "
            "alembic -x pain_zone=genou -x pain_zone_label=Genou upgrade head")
    return slug, label


def upgrade() -> None:
    op.create_table(
        "pain_zones",
        sa.Column("athlete_id", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("timezone('utc', now())"), nullable=False),
        sa.CheckConstraint("label <> '' AND char_length(label) <= 40",
                           name="ck_pain_zones_label"),
        sa.CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{0,39}$'", name="ck_pain_zones_slug"),
        sa.ForeignKeyConstraint(["athlete_id"], ["redline.athletes.id"],
                                name=op.f("fk_pain_zones_athlete_id_athletes")),
        sa.PrimaryKeyConstraint("athlete_id", "slug", name=op.f("pk_pain_zones")),
        schema="redline",
    )
    op.create_table(
        "pain_readings",
        sa.Column("observation_id", sa.UUID(), nullable=False),
        sa.Column("zone", sa.Text(), nullable=False),
        sa.Column("athlete_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(precision=3, scale=1), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True),
                  server_default=sa.text("timezone('utc', now())"), nullable=False),
        sa.CheckConstraint(
            "(state = 'known' AND value IS NOT NULL AND value BETWEEN 0 AND 10) OR "
            "(state <> 'known' AND value IS NULL)", name="ck_pain_readings_value"),
        sa.CheckConstraint("state IN ('pending','known','unavailable','not_applicable')",
                           name="ck_pain_readings_state"),
        sa.ForeignKeyConstraint(["athlete_id", "zone"],
                                ["redline.pain_zones.athlete_id", "redline.pain_zones.slug"],
                                name="fk_pain_readings_zone"),
        sa.ForeignKeyConstraint(["observation_id"], ["redline.observations.id"],
                                name=op.f("fk_pain_readings_observation_id_observations"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("observation_id", "zone", name=op.f("pk_pain_readings")),
        schema="redline",
    )
    op.create_table(
        "strength_pain_readings",
        sa.Column("strength_session_id", sa.UUID(), nullable=False),
        sa.Column("zone", sa.Text(), nullable=False),
        sa.Column("moment", sa.Text(), nullable=False),
        sa.Column("athlete_id", sa.Text(), nullable=False),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("moment IN ('before','after')",
                           name="ck_strength_pain_readings_moment"),
        sa.CheckConstraint("value BETWEEN 0 AND 10", name="ck_strength_pain_readings_value"),
        sa.ForeignKeyConstraint(["athlete_id", "zone"],
                                ["redline.pain_zones.athlete_id", "redline.pain_zones.slug"],
                                name="fk_strength_pain_readings_zone"),
        sa.ForeignKeyConstraint(
            ["strength_session_id"], ["redline.strength_sessions.id"],
            name=op.f("fk_strength_pain_readings_strength_session_id_strength_sessions"),
            ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("strength_session_id", "zone", "moment",
                                name=op.f("pk_strength_pain_readings")),
        schema="redline",
    )
    op.add_column("coach_predictions", sa.Column("resolves_zone", sa.Text(), nullable=True),
                  schema="redline")

    connection = op.get_bind()
    if _existing_pain(connection):
        slug, label = _operator_zone()
        parameters = {"zone": slug, "label": label}
        connection.execute(sa.text("""
            INSERT INTO redline.pain_zones (athlete_id, slug, label)
            SELECT id, :zone, :label FROM redline.athletes
        """), parameters)
        connection.execute(sa.text("""
            INSERT INTO redline.pain_readings
              (observation_id, zone, athlete_id, state, value, recorded_at)
            SELECT m.observation_id, :zone, o.athlete_id, m.pain_state, m.pain, m.recorded_at
            FROM redline.observation_metrics m
            JOIN redline.observations o ON o.id = m.observation_id
            WHERE m.pain_state <> 'not_applicable'
        """), parameters)
        for moment, column in (("before", "shin_before"), ("after", "shin_after")):
            connection.execute(sa.text(f"""
                INSERT INTO redline.strength_pain_readings
                  (strength_session_id, zone, moment, athlete_id, value)
                SELECT id, :zone, '{moment}', athlete_id, {column}
                FROM redline.strength_sessions WHERE {column} IS NOT NULL
            """), parameters)
        connection.execute(sa.text("""
            UPDATE redline.coach_predictions SET resolves_zone = :zone
            WHERE resolves_kind IS NOT NULL
        """), parameters)

    op.drop_constraint("ck_coach_predictions_resolves", "coach_predictions",
                       schema="redline", type_="check")
    op.create_check_constraint("ck_coach_predictions_resolves", "coach_predictions",
                               RESOLVES_AFTER, schema="redline")
    op.create_foreign_key("fk_coach_predictions_zone", "coach_predictions", "pain_zones",
                          ["athlete_id", "resolves_zone"], ["athlete_id", "slug"],
                          source_schema="redline", referent_schema="redline")

    # The function must stop naming the column before it disappears.
    _protect_measurements(MEASUREMENTS_AFTER)
    op.drop_column("observation_metrics", "pain", schema="redline")
    op.drop_column("observation_metrics", "pain_state", schema="redline")
    op.drop_column("strength_sessions", "shin_after", schema="redline")
    op.drop_column("strength_sessions", "shin_before", schema="redline")

    # A known pain reading is immutable, like any known measurement.
    op.execute(sa.text("""
        CREATE FUNCTION redline.protect_pain_reading() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.state = 'known' AND (NEW.state, NEW.value) IS DISTINCT FROM
             (OLD.state, OLD.value) THEN
            RAISE EXCEPTION 'une mesure connue est immuable; créer une observation correctrice';
          END IF;
          RETURN NEW;
        END;
        $$
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_pain_reading_history
        BEFORE UPDATE ON redline.pain_readings
        FOR EACH ROW EXECUTE FUNCTION redline.protect_pain_reading()
    """))


def downgrade() -> None:
    connection = op.get_bind()
    zones = connection.execute(sa.text(
        "SELECT count(DISTINCT zone) FROM redline.pain_readings")).scalar_one()
    if zones > 1:
        raise RuntimeError("plusieurs zones portent de la douleur : une seule colonne ne "
                           "peut pas les recevoir sans les confondre")
    op.execute("DROP TRIGGER trg_pain_reading_history ON redline.pain_readings")
    op.execute("DROP FUNCTION redline.protect_pain_reading()")
    op.add_column("strength_sessions", sa.Column("shin_before", sa.SmallInteger()),
                  schema="redline")
    op.add_column("strength_sessions", sa.Column("shin_after", sa.SmallInteger()),
                  schema="redline")
    op.add_column("observation_metrics", sa.Column("pain", sa.Numeric(3, 1)), schema="redline")
    op.add_column("observation_metrics",
                  sa.Column("pain_state", sa.Text(), nullable=False,
                            server_default="not_applicable"), schema="redline")
    op.alter_column("observation_metrics", "pain_state", server_default=None,
                    schema="redline")
    connection.execute(sa.text("""
        UPDATE redline.observation_metrics m SET pain = r.value, pain_state = r.state
        FROM redline.pain_readings r WHERE r.observation_id = m.observation_id
    """))
    for moment, column in (("before", "shin_before"), ("after", "shin_after")):
        connection.execute(sa.text(f"""
            UPDATE redline.strength_sessions s SET {column} = r.value
            FROM redline.strength_pain_readings r
            WHERE r.strength_session_id = s.id AND r.moment = '{moment}'
        """))
    op.create_check_constraint(
        "ck_observation_metrics_pain_state", "observation_metrics",
        "pain_state IN ('pending','known','unavailable','not_applicable')", schema="redline")
    op.create_check_constraint(
        "ck_observation_metrics_pain_value", "observation_metrics",
        "(pain_state = 'known' AND pain IS NOT NULL AND (pain BETWEEN 0 AND 10)) OR "
        "(pain_state <> 'known' AND pain IS NULL)", schema="redline")
    op.create_check_constraint("ck_strength_sessions_shin_before", "strength_sessions",
                               "shin_before IS NULL OR shin_before BETWEEN 0 AND 10",
                               schema="redline")
    op.create_check_constraint("ck_strength_sessions_shin_after", "strength_sessions",
                               "shin_after IS NULL OR shin_after BETWEEN 0 AND 10",
                               schema="redline")
    _protect_measurements(MEASUREMENTS_BEFORE)
    op.drop_constraint("fk_coach_predictions_zone", "coach_predictions",
                       schema="redline", type_="foreignkey")
    op.drop_constraint("ck_coach_predictions_resolves", "coach_predictions",
                       schema="redline", type_="check")
    op.create_check_constraint("ck_coach_predictions_resolves", "coach_predictions",
                               RESOLVES_BEFORE, schema="redline")
    op.drop_column("coach_predictions", "resolves_zone", schema="redline")
    op.drop_table("strength_pain_readings", schema="redline")
    op.drop_table("pain_readings", schema="redline")
    op.drop_table("pain_zones", schema="redline")
