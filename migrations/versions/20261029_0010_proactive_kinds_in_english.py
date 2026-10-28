"""Three proactive job kinds get English names.

`strava_debrief_differe` becomes `strava_debrief_late`, `registre_incomplet`
`ledger_incomplete` and `trace_reveil` `wake_trace`; the wake reminder's job key moves
from `trace-reveil:<day>` to `wake-trace:<day>`. `proactive_messages` keeps what it
recorded: it is append-only history.

Revision ID: 20261029_0010
Revises: 20261029_0009
"""

from __future__ import annotations

from alembic import op

revision = "20261029_0010"
down_revision = "20261029_0009"
branch_labels = None
depends_on = None

NAME = "ck_proactive_jobs_kind"
KINDS = ("strava_debrief", "silence", "morning", "weekly", "weekly_repair", "monthly",
         "session_followup", "missed_session", "daily_signal", "premortem", "correction",
         "decision_challenge", "gut_review", "gear_alert", "race_countdown", "strength_card",
         "calibration_eve", "operator_message")
RENAMED = {"strava_debrief_differe": "strava_debrief_late",
           "registre_incomplet": "ledger_incomplete", "trace_reveil": "wake_trace"}
KEY = ("trace-reveil:", "wake-trace:")


def _move(renamed: dict[str, str], key: tuple[str, str]) -> None:
    op.drop_constraint(NAME, "proactive_jobs", type_="check", schema="redline")
    for old, new in renamed.items():
        op.execute(f"UPDATE redline.proactive_jobs SET kind = '{new}' WHERE kind = '{old}'")
    op.execute(f"UPDATE redline.proactive_jobs "
               f"SET job_key = '{key[1]}' || substr(job_key, {len(key[0]) + 1}) "
               f"WHERE job_key LIKE '{key[0]}%'")
    kinds = ",".join(f"'{kind}'" for kind in (*KINDS, *renamed.values()))
    op.create_check_constraint(NAME, "proactive_jobs", f"kind IN ({kinds})", schema="redline")


def upgrade() -> None:
    _move(RENAMED, KEY)


def downgrade() -> None:
    _move({new: old for old, new in RENAMED.items()}, KEY[::-1])
