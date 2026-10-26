"""Ce que le modèle déclare, la table doit savoir le ranger.

⭐ 2026-08-18, et la panne la plus chère de la journée. Le schéma applicatif a gagné
`gates[].when` et `gates[].holds` le matin même ; `redline.plan_gates`, non. Un store
relationnel qui n'a pas de colonne ne se plaint pas : il insère les champs qu'il
connaît et jette le reste sans un mot. La révision W34 r4 est partie valide et typée,
et elle est revenue de PostgreSQL sans son typage, donc invalide au contrat 2 -- qui
exige précisément `when`. Le programme de la semaine est devenu inprescriptible en une
écriture, et l'erreur affichée décrivait la RELECTURE, pas l'écriture : elle a fait
croire à un refus alors que la ligne était déjà committée.

Les tests du modèle étaient verts, ceux du store aussi : aucun ne regardait la
frontière entre les deux. Celui-ci ne tient aucune liste et n'a besoin d'aucune base --
il compare deux déclarations, donc il tombe le jour où l'une prend de l'avance.
"""

from scripts.model.weekly_plan import GATE_SCHEMA, REVISION_SCHEMA, SESSION_SCHEMA
from scripts.storage.schema import plan_gates, plan_revisions, planned_sessions

# Les renommages assumés, et leur raison. Tout le reste doit correspondre au nom près.
GATE_COLUMNS = {
    "id": "logical_key",        # `id` est la clé technique de la ligne, pas la nôtre
    "due": "due_on",
    "when": "applies_when",     # `when` est un mot réservé SQL
}
SESSION_COLUMNS = {
    "id": "logical_key",
    "date": "scheduled_on",
    "strength": "strength_spec",
    "weather": "weather_policy",
}
REVISION_COLUMNS = {
    "revision": "revision",     # le NUMÉRO ; `id` reste la clé technique
}
# `sessions` et `gates` vivent dans leurs propres tables.
REVISION_ELSEWHERE = {"sessions", "gates"}


def _missing(schema, table, renames):
    columns = set(table.c.keys())
    return sorted(field.name for field in schema.fields
                  if renames.get(field.name, field.name) not in columns)


def test_every_gate_field_has_a_column():
    missing = _missing(GATE_SCHEMA, plan_gates, GATE_COLUMNS)
    assert not missing, (
        f"{missing} : déclaré sur le contrôle daté, rangé nulle part dans "
        "redline.plan_gates. Une révision partirait valide et reviendrait mutilée. "
        "Ajoute la colonne par une migration, sa lecture et son écriture dans "
        "scripts/storage/repository.py, et son renommage éventuel ci-dessus.")


def test_every_session_field_has_a_column():
    """La même frontière, sur l'objet d'à côté : `sources` vit dans sa propre table."""
    missing = set(_missing(SESSION_SCHEMA, planned_sessions, SESSION_COLUMNS))
    assert missing <= {"sources"}, sorted(missing)


def test_every_revision_field_has_a_column():
    """L'ÉTAGE AU-DESSUS, et il était resté ouvert.

    `append_plan_revision_in` énumère les champs de révision à la main, exactement comme
    il énumérait ceux du contrôle daté le matin du 2026-08-18. Un champ ajouté à
    `REVISION_SCHEMA` repartirait donc de PostgreSQL sans lui, et la révision serait
    invalide au contrat qu'elle déclare -- la semaine inprescriptible en une écriture.
    Le correctif du 18/08 fermait la porte du bas ; celle-ci était restée entrouverte.
    """
    missing = set(_missing(REVISION_SCHEMA, plan_revisions, REVISION_COLUMNS))
    assert missing <= REVISION_ELSEWHERE, (
        f"{sorted(missing - REVISION_ELSEWHERE)} : déclaré sur la révision, rangé nulle "
        "part dans redline.plan_revisions. Ajoute la colonne par une migration, sa "
        "lecture et son écriture dans scripts/storage/repository.py, et son renommage "
        "éventuel ci-dessus -- ou nomme-le dans REVISION_ELSEWHERE en disant où il vit.")


def test_the_comparison_is_not_vacuous():
    """Une table renommée rendrait les tests précédents verts sans rien vérifier."""
    assert "applies_when" in plan_gates.c
    assert "holds" in plan_gates.c
    assert {field.name for field in GATE_SCHEMA.fields} >= {"when", "holds", "due"}
    assert {"persisted_at", "calibration_target"} <= set(
        plan_revisions.c.keys())
    assert "text_contract" not in plan_revisions.c
    assert "calibration_target" not in REVISION_ELSEWHERE
