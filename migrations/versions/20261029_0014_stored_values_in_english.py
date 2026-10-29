"""The last French values the database stores become English.

- `redline.gate_when`: a dated check applies `before` or `after`, no longer `avant` or
  `apres`.
- `planned_sessions.transition`: the three default transitions with a French slug,
  `qualite`, `descente` and `nocturne`, become `quality`, `descent` and `night`.
- `strava_activity_intakes.weather`: the sky is stored as its WMO `weather_code`; the
  French `conditions` label is computed when the weather is shown.
- Race milestones: the keys of `systemes`, `gel-v1`, `preuve`, `carburant` and `jour-j`
  become `systems`, `freeze-v1`, `proof`, `fuel` and `race-day`, in every `job_key` and
  `source_key` and in the stage of a queued job, so that no milestone is sent twice.
- The trigger functions raise their errors in English.

Run with the service stopped, like every data migration.

Revision ID: 20261029_0014
Revises: 20261029_0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_0014"
down_revision = "20261029_0013"
branch_labels = None
depends_on = None

GATE_WHEN = {"avant": "before", "apres": "after"}
TRANSITIONS = {"qualite": "quality", "descente": "descent", "nocturne": "night"}
STAGES = {"systemes": "systems", "gel-v1": "freeze-v1", "preuve": "proof",
          "carburant": "fuel", "jour-j": "race-day"}
# The labels the engine stored, frozen here as they were written.
SKY = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine", 55: "bruine dense",
    56: "bruine verglaçante", 57: "bruine verglaçante dense",
    61: "pluie faible", 63: "pluie", 65: "pluie forte",
    66: "pluie verglaçante", 67: "pluie verglaçante forte",
    71: "neige faible", 73: "neige", 75: "neige forte", 77: "grésil",
    80: "averses faibles", 81: "averses", 82: "averses violentes",
    85: "averses de neige", 86: "averses de neige fortes",
    95: "orage", 96: "orage avec grêle", 99: "orage avec forte grêle",
}
UNKNOWN_SKY = "conditions inconnues"
MESSAGES = {
    "une mesure connue est immuable; créer une observation correctrice":
        "a known measurement is immutable; record a correcting observation",
    "révision attendue %, reçue %": "expected revision %, got %",
    "une révision de programme est append-only": "a plan revision is append-only",
    "le journal d audit est append-only": "the audit log is append-only",
    "la trace des messages proactifs est append-only":
        "the proactive message trace is append-only",
    "le snapshot d une séance livrée est immuable":
        "the snapshot of a delivered session is immutable",
    "la révocation d une séance est irréversible": "revoking a session cannot be undone",
}
FUNCTIONS = ("protect_measurement_history", "protect_pain_reading", "protect_plan_revision",
             "forbid_plan_revision_mutation", "forbid_audit_mutation",
             "forbid_proactive_message_mutation", "protect_strength_session_link")


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _gates(renamed: dict[str, str]) -> None:
    for old, new in renamed.items():
        op.execute(f"ALTER TYPE redline.gate_when RENAME VALUE '{old}' TO '{new}'")


def _transitions(renamed: dict[str, str]) -> None:
    for old, new in renamed.items():
        op.execute(f"UPDATE redline.planned_sessions SET transition = '{new}' "
                   f"WHERE transition = '{old}'")


def _sky_to_code() -> None:
    cases = " ".join(f"WHEN {_literal(label)} THEN {code}" for code, label in SKY.items())
    op.execute(f"""
        UPDATE redline.strava_activity_intakes
        SET weather = (weather - 'conditions') || jsonb_build_object(
            'weather_code', CASE weather->>'conditions' {cases} END)
        WHERE weather ? 'conditions'
    """)


def _code_to_sky() -> None:
    cases = " ".join(f"WHEN {code} THEN {_literal(label)}" for code, label in SKY.items())
    op.execute(f"""
        UPDATE redline.strava_activity_intakes
        SET weather = (weather - 'weather_code') || jsonb_build_object(
            'conditions', CASE (weather->>'weather_code')::integer {cases}
                          ELSE {_literal(UNKNOWN_SKY)} END)
        WHERE weather ? 'weather_code'
    """)


def _stages(renamed: dict[str, str]) -> None:
    bind = op.get_bind()
    columns = bind.execute(sa.text(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'redline' AND column_name IN ('job_key', 'source_key') "
        "ORDER BY 1, 2")).all()
    for table, column in columns:
        op.execute(f"ALTER TABLE redline.{table} DISABLE TRIGGER USER")
        for old, new in renamed.items():
            bind.execute(sa.text(
                f"UPDATE redline.{table} SET {column} = regexp_replace({column}, :old, :new) "
                f"WHERE {column} ~ :key"),
                {"old": f":{old}$", "new": f":{new}",
                 "key": f"^race:.+:[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}:{old}$"})
        op.execute(f"ALTER TABLE redline.{table} ENABLE TRIGGER USER")
    for old, new in renamed.items():
        bind.execute(sa.text(
            "UPDATE redline.proactive_jobs SET detail = replace(detail, :old, :new) "
            "WHERE job_key LIKE 'race:%'"),
            {"old": f'"stage":"{old}"', "new": f'"stage":"{new}"'})


def _messages(renamed: dict[str, str]) -> None:
    bind = op.get_bind()
    for name in FUNCTIONS:
        definition = bind.execute(sa.text(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'redline' AND p.proname = :name"), {"name": name}).scalar_one()
        for old, new in renamed.items():
            definition = definition.replace(f"'{old}'", f"'{new}'")
        op.execute(sa.text(definition.replace(":", r"\:")))


def upgrade() -> None:
    _gates(GATE_WHEN)
    _transitions(TRANSITIONS)
    _sky_to_code()
    _stages(STAGES)
    _messages(MESSAGES)


def downgrade() -> None:
    _messages({new: old for old, new in MESSAGES.items()})
    _stages({new: old for old, new in STAGES.items()})
    _code_to_sky()
    _transitions({new: old for old, new in TRANSITIONS.items()})
    _gates({new: old for old, new in GATE_WHEN.items()})
