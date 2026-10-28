"""The keys stored in JSON become English.

- `strava_activity_intakes.weather`: `heure_utc`, `ressenti`, `humidite`, `vent`,
  `pression`, `rayonnement`, `rayonnement_direct`, `bande`, `solaire_recale` and
  `fraction_directe` get English names, and the WBGT flag an English value.
- `plan_revisions.changes`: a restriction's premise, `motif: {regle, fait}`, becomes
  `restriction: {rule, fact}`, and the athlete's request is `request`, not `demande`.
  Revisions are append-only: their immutability trigger is lifted for this one rewrite.
- The easy-run permit's predictions: domain `easy-permit` and keys `easy-permit-*`,
  in `coach_predictions` and `permit_choices`.

Run with the service stopped: a turn staged before the upgrade would carry old keys.

Revision ID: 20261029_0008
Revises: 20261029_0007
"""

from __future__ import annotations

from alembic import op

revision = "20261029_0008"
down_revision = "20261029_0007"
branch_labels = None
depends_on = None

WEATHER_KEYS = {
    "heure_utc": "time_utc", "ressenti": "feels_like", "humidite": "humidity", "vent": "wind",
    "pression": "pressure", "rayonnement": "radiation", "rayonnement_direct": "direct_radiation",
    "bande": "band", "solaire_recale": "solar_adjusted", "fraction_directe": "direct_fraction",
}
FLAGS = {"blanc": "white", "vert": "green", "jaune": "yellow", "rouge": "red", "noir": "black"}
PREMISE = {"key": ("motif", "restriction"), "rule": ("regle", "rule"),
           "fact": ("fait", "fact"), "request": ("demande", "request")}
PERMIT = ("permis-ef", "easy-permit")
IMMUTABLE = "trg_plan_revision_immutable"


def _weather(keys: dict[str, str], flags: dict[str, str], band: str) -> None:
    for old, new in keys.items():
        op.execute(f"UPDATE redline.strava_activity_intakes "
                   f"SET weather = (weather - '{old}') || jsonb_build_object('{new}', weather->'{old}') "
                   f"WHERE weather ? '{old}'")
    cases = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in flags.items())
    op.execute(f"UPDATE redline.strava_activity_intakes "
               f"SET weather = jsonb_set(weather, '{{{band}}}', "
               f"to_jsonb(CASE weather->>'{band}' {cases} ELSE weather->>'{band}' END)) "
               f"WHERE weather ? '{band}'")


def _premises(forward: bool) -> None:
    pick = 1 if forward else 0
    old_key, new_key = PREMISE["key"][1 - pick], PREMISE["key"][pick]
    old_rule, new_rule = PREMISE["rule"][1 - pick], PREMISE["rule"][pick]
    old_fact, new_fact = PREMISE["fact"][1 - pick], PREMISE["fact"][pick]
    old_request, new_request = PREMISE["request"][1 - pick], PREMISE["request"][pick]
    op.execute(f"ALTER TABLE redline.plan_revisions DISABLE TRIGGER {IMMUTABLE}")
    op.execute(f"""
        UPDATE redline.plan_revisions SET changes = (
          SELECT jsonb_agg(CASE WHEN entry ? '{old_key}' THEN
                   (entry - '{old_key}') || jsonb_build_object('{new_key}', jsonb_build_object(
                     '{new_rule}', CASE entry->'{old_key}'->>'{old_rule}'
                                     WHEN '{old_request}' THEN '{new_request}'
                                     ELSE entry->'{old_key}'->>'{old_rule}' END,
                     '{new_fact}', entry->'{old_key}'->'{old_fact}'))
                 ELSE entry END ORDER BY position)
          FROM jsonb_array_elements(changes) WITH ORDINALITY AS item(entry, position))
        WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(changes) AS item(entry)
                      WHERE entry ? '{old_key}')
    """)
    op.execute(f"ALTER TABLE redline.plan_revisions ENABLE TRIGGER {IMMUTABLE}")


def _permit(old: str, new: str) -> None:
    op.execute(f"UPDATE redline.coach_predictions SET domain = '{new}' WHERE domain = '{old}'")
    for table in ("coach_predictions", "permit_choices"):
        op.execute(f"UPDATE redline.{table} "
                   f"SET prediction_key = '{new}-' || substr(prediction_key, {len(old) + 2}) "
                   f"WHERE prediction_key LIKE '{old}-%'")


def upgrade() -> None:
    _weather(WEATHER_KEYS, FLAGS, "band")
    _premises(forward=True)
    _permit(*PERMIT)


def downgrade() -> None:
    _weather({new: old for old, new in WEATHER_KEYS.items()},
             {new: old for old, new in FLAGS.items()}, "bande")
    _premises(forward=False)
    _permit(*reversed(PERMIT))
