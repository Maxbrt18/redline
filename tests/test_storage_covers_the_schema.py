"""Every field the model declares must have a column to be stored in.

A relational store with no column for a field does not complain: it inserts the fields
it knows and silently drops the rest. A revision written valid and typed would then come
back from PostgreSQL without its typing, hence invalid under the contract that requires
it, and the week's program would become unprescribable in a single write. The error
would describe the read, not the write, although the row was already committed.

Model tests and store tests can both pass while nothing checks the boundary between
them. This one keeps no list and needs no database: it compares two declarations, so it
fails as soon as one gets ahead of the other.
"""

from redline.model.weekly_plan import GATE_SCHEMA, REVISION_SCHEMA, SESSION_SCHEMA
from redline.storage.schema import plan_gates, plan_revisions, planned_sessions

# Deliberate renames, with their reason. Everything else must match by name.
GATE_COLUMNS = {
    "id": "logical_key",        # `id` is the row's technical key, not the schema's
    "due": "due_on",
    "when": "applies_when",     # `when` is a reserved SQL word
}
SESSION_COLUMNS = {
    "id": "logical_key",
    "date": "scheduled_on",
    "strength": "strength_spec",
    "weather": "weather_policy",
}
REVISION_COLUMNS = {
    "revision": "revision",     # the NUMBER; `id` stays the technical key
}
# `sessions` and `gates` live in their own tables.
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
        "src/redline/storage/repository/plans.py, et son renommage éventuel ci-dessus.")


def test_every_session_field_has_a_column():
    """Same boundary on the neighbouring object: `sources` lives in its own table."""
    missing = set(_missing(SESSION_SCHEMA, planned_sessions, SESSION_COLUMNS))
    assert missing <= {"sources"}, sorted(missing)


def test_every_revision_field_has_a_column():
    """The revision level, one floor above the gates and sessions.

    `append_plan_revision_in` lists the revision fields by hand, exactly as it lists the
    dated-check fields. A field added to `REVISION_SCHEMA` would come back from
    PostgreSQL without it, and the revision would be invalid under the contract it
    declares -- the week unprescribable in a single write.
    """
    missing = set(_missing(REVISION_SCHEMA, plan_revisions, REVISION_COLUMNS))
    assert missing <= REVISION_ELSEWHERE, (
        f"{sorted(missing - REVISION_ELSEWHERE)} : déclaré sur la révision, rangé nulle "
        "part dans redline.plan_revisions. Ajoute la colonne par une migration, sa "
        "lecture et son écriture dans src/redline/storage/repository/plans.py, et son renommage "
        "éventuel ci-dessus -- ou nomme-le dans REVISION_ELSEWHERE en disant où il vit.")


def test_the_comparison_is_not_vacuous():
    """A renamed table would turn the previous tests green without checking anything."""
    assert "applies_when" in plan_gates.c
    assert "holds" in plan_gates.c
    assert {field.name for field in GATE_SCHEMA.fields} >= {"when", "holds", "due"}
    assert {"persisted_at", "calibration_target"} <= set(
        plan_revisions.c.keys())
    assert "text_contract" not in plan_revisions.c
    assert "calibration_target" not in REVISION_ELSEWHERE
