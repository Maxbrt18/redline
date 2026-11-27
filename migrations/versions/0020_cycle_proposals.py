"""Lets the planner ask for a plan review and send a proposal the athlete takes.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

KINDS_BEFORE = ("strava_debrief", "strava_debrief_late", "silence", "ledger_incomplete",
                "morning", "weekly", "weekly_repair", "monthly", "session_followup",
                "missed_session", "daily_signal", "premortem", "correction",
                "decision_challenge", "gut_review", "gear_alert", "race_countdown",
                "strength_card", "calibration_eve", "operator_message", "wake_trace",
                "cycle_transition")
KINDS_AFTER = (*KINDS_BEFORE, "plan_review", "cycle_proposal")


def _kinds(kinds: tuple[str, ...]) -> None:
    listed = ",".join(f"'{kind}'" for kind in kinds)
    op.drop_constraint("ck_proactive_jobs_kind", "proactive_jobs", schema="redline")
    op.create_check_constraint("ck_proactive_jobs_kind", "proactive_jobs",
                               f"kind IN ({listed})", schema="redline")


def upgrade() -> None:
    _kinds(KINDS_AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM redline.proactive_jobs "
               "WHERE kind IN ('plan_review', 'cycle_proposal')")
    _kinds(KINDS_BEFORE)
