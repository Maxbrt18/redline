"""The cycle versions in PostgreSQL: one in force, written whole, changed only by its state;
the planner's pass keeps one in force and announces each transition but the takeover."""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest
import sqlalchemy as sa
from conftest import reset_database
from writes import write

from redline.model import commands, cycle_templates
from redline.model import plan_generator as generator
from redline.model.vocabulary import CycleCause, CycleKind, GeneralLevel
from redline.service.postgres_state import PostgresStateStore
from redline.service.proactive_messages import compose_closed_proactive
from redline.storage import Database, OperationalStore, StorageError
from redline.storage.schema import cycle_versions, plan_revisions

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL absent")
TEMPLATES = cycle_templates.parse(cycle_templates.DEFAULT.read_text(encoding="utf-8"),
                                  cycle_templates.DEFAULT)
#: A Wednesday.
NOW = dt.datetime(2030, 3, 13, 12, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def clean_database():
    reset_database()


@pytest.fixture
def store():
    return OperationalStore(Database(URL, application_name="pytest"))


def _inputs(origin: str) -> generator.Inputs:
    return generator.Inputs(origin, 40, 14, 50, 10, 10)


def _general(origin: str = "2030-W12") -> generator.Trame:
    return generator.general(TEMPLATES.general_at(GeneralLevel.MAINTAIN), _inputs(origin))


def _apply(store, trame, cause, **kwargs):
    with store.database.transaction() as connection:
        return store.apply_transition_in(connection, trame, cause, actor="test", **kwargs)


def _states(store) -> list[tuple[int, str]]:
    with store.database.engine.connect() as connection:
        return [(row.version, row.state) for row in connection.execute(sa.select(
            cycle_versions.c.version, cycle_versions.c.state).order_by(
            cycle_versions.c.created_at))]


def test_a_transition_puts_its_version_in_force_and_settles_the_one_before(store):
    first = _apply(store, _general(), CycleCause.MIGRATION, level=GeneralLevel.MAINTAIN)
    assert store.active_cycle() == first and first.trame == _general()
    block = _apply(store, _general("2030-W16"), CycleCause.BLOCK_END,
                   level=GeneralLevel.MAINTAIN)
    assert (block.cycle_key, block.version) == (first.cycle_key, 2), "the same cycle"
    recovery = generator.recovery(TEMPLATES.recovery_after(10), _inputs("2030-W20"))
    after = _apply(store, recovery, CycleCause.TRANSITION)
    assert after.cycle_key != first.cycle_key and after.version == 1
    assert _states(store) == [(1, "superseded"), (2, "closed"), (1, "active")]
    assert store.active_cycle() == after and store.cycle_version(first.id).trame == _general()


def test_a_version_changes_only_by_its_state_along_its_moves(store):
    found = _apply(store, _general(), CycleCause.MIGRATION, level=GeneralLevel.MAINTAIN)
    refusals = {
        "UPDATE redline.cycle_versions SET template = 'progress'": "changes only by its state",
        "DELETE FROM redline.cycle_versions": "never deleted",
        "UPDATE redline.cycle_weeks SET volume_km = 99": "written once",
        "DELETE FROM redline.cycle_key_sessions": "written once",
    }
    for statement, message in refusals.items():
        with store.database.engine.begin() as connection, pytest.raises(
                sa.exc.DBAPIError, match=message):
            connection.execute(sa.text(statement))
    with store.database.engine.begin() as connection:
        connection.execute(sa.text("UPDATE redline.cycle_versions SET state = 'closed'"))
    with store.database.engine.begin() as connection, pytest.raises(
            sa.exc.DBAPIError, match="does not move from closed to active"):
        connection.execute(sa.text("UPDATE redline.cycle_versions SET state = 'active'"))
    with store.database.engine.begin() as connection, pytest.raises(
            sa.exc.DBAPIError, match="the next one is 2"):
        connection.execute(sa.text(
            "INSERT INTO redline.cycle_versions (id, athlete_id, cycle_key, version, kind, "
            "level, state, starts_on, ends_on, template, hypotheses, cause) SELECT "
            "gen_random_uuid(), athlete_id, cycle_key, 3, kind, level, 'closed', starts_on, "
            "ends_on, template, hypotheses, cause FROM redline.cycle_versions"))
    assert store.active_cycle() is None and store.cycle_version(found.id) is not None


def test_two_cycles_are_never_in_force_together(store):
    _apply(store, _general(), CycleCause.MIGRATION, level=GeneralLevel.MAINTAIN)
    with store.database.engine.begin() as connection, pytest.raises(
            sa.exc.IntegrityError, match="ix_cycle_versions_active"):
        connection.execute(sa.text(
            "INSERT INTO redline.cycle_versions (id, athlete_id, cycle_key, version, kind, "
            "level, state, starts_on, ends_on, template, hypotheses, cause) SELECT "
            "gen_random_uuid(), athlete_id, gen_random_uuid(), 1, kind, level, 'active', "
            "starts_on, ends_on, template, hypotheses, cause FROM redline.cycle_versions"))


def test_a_revision_records_the_version_in_force(store):
    from test_weekly_plan import revision
    store.append_plan_revision("2030-W10", revision(computed_at="2030-03-02T19:10:00+01:00"))
    found = _apply(store, _general(), CycleCause.MIGRATION, level=GeneralLevel.MAINTAIN)
    store.append_plan_revision("2030-W11", revision(computed_at="2030-03-09T19:10:00+01:00"))
    with store.database.engine.connect() as connection:
        linked = connection.execute(sa.select(plan_revisions.c.cycle_version_id).order_by(
            plan_revisions.c.computed_at)).scalars().all()
    assert linked == [None, found.id]


def test_the_planner_takes_over_silently_then_announces_each_transition(store):
    state = PostgresStateStore(store.database)
    state.materialize_intelligence(NOW)
    state.materialize_intelligence(NOW)
    found = store.active_cycle()
    assert found is not None and found.kind == CycleKind.GENERAL
    assert (found.cause, found.level) == (CycleCause.MIGRATION, GeneralLevel.MAINTAIN)
    assert len(_states(store)) == 1
    assert not [job for job in state.proactive.jobs(active_only=True)
                if job["kind"] == "cycle_transition"]
    race = {"deadline_key": "ten", "due_on": NOW.date(), "item": "Dix", "fact_owner": "test",
            "kind": "race", "priority": "b", "race_state": "active",
            "context": {"distance_km": 10}}
    write(store, commands.DEADLINE_UPSERT, race)
    state.materialize_intelligence(NOW)
    assert len(_states(store)) == 1, "a race ahead changes nothing"
    write(store, commands.DEADLINE_UPSERT, {**race, "race_state": "run"})
    state.materialize_intelligence(NOW)
    recovery = store.active_cycle()
    assert recovery.kind == CycleKind.RECOVERY and recovery.race_key == "ten"
    [job] = [job for job in state.proactive.jobs(active_only=True)
             if job["kind"] == "cycle_transition"]
    assert json.loads(job["detail"])["kind"] == "recovery"
    rendered = compose_closed_proactive("cycle_transition", job["detail"])
    assert rendered is not None and "Récupération, 1 semaine" in rendered.text


def test_the_coach_reads_the_version_in_force(store, monkeypatch, capsys):
    from redline.cli import cycle
    from redline.model.errors import InputError
    monkeypatch.setattr("redline.storage.open_store", lambda actor: store)
    monkeypatch.setattr("sys.argv", ["cycle", "show", "--date", "2030-03-13"])
    with pytest.raises(InputError, match="no cycle version"):
        cycle.main()
    _apply(store, _general(), CycleCause.MIGRATION, level=GeneralLevel.MAINTAIN)
    assert cycle.main() == 0
    shown = capsys.readouterr().out.splitlines()
    assert shown[0].startswith("Cycle en cours : Préparation générale « maintenir », version 1 "
                               "(reprise de l'existant), de 2030-W12 à 2030-W15")


def test_the_weeks_a_taken_version_planned_low_are_not_a_start(store):
    template = TEMPLATES.preparation_for(21.1, "road")
    trame = generator.candidates(template, generator.Goal(dt.date(2030, 6, 1), 21.1),
                                 _inputs("2030-W12"))[1]
    write(store, commands.DEADLINE_UPSERT, {
        "deadline_key": "half", "due_on": dt.date(2030, 6, 1), "item": "Semi",
        "fact_owner": "test", "kind": "race", "priority": "a", "race_state": "active",
        "context": {"distance_km": 21.1, "terrain": "road"}})
    found = _apply(store, trame, CycleCause.CREATE, race_key="half")
    assert found.race_key == "half"
    low = {week.label for week in trame.weeks if week.deload or week.phase == "taper"}
    with store.database.engine.connect() as connection:
        assert store.planned_low_weeks_in(connection) == low and low
    recovery = generator.recovery(TEMPLATES.recovery_after(21.1), _inputs("2030-W23"))
    _apply(store, recovery, CycleCause.TRANSITION)
    with store.database.engine.connect() as connection:
        assert store.planned_low_weeks_in(connection) == low | {"2030-W23"}
    assert found.kind == CycleKind.PREPARATION


def test_a_week_is_held_by_the_version_whose_trame_has_it(store):
    assert store.first_cycle_week() is None and store.cycle_for_week("2030-W12") is None
    first = _apply(store, _general(), CycleCause.MIGRATION, level=GeneralLevel.MAINTAIN)
    nxt = _apply(store, _general("2030-W16"), CycleCause.BLOCK_END, level=GeneralLevel.MAINTAIN)
    assert store.first_cycle_week() == "2030-W12"
    assert store.cycle_for_week("2030-W15").id == first.id, "a superseded version keeps its weeks"
    assert store.cycle_for_week("2030-W16").id == nxt.id
    assert store.cycle_for_week("2030-W20") is None


def test_a_session_keeps_the_key_session_it_runs_and_a_revision_its_overrides(store):
    from test_weekly_plan import revision, session
    found = _apply(store, _general(), CycleCause.MIGRATION, level=GeneralLevel.MAINTAIN)
    [key] = [key for key in found.trame.keys if key.week == "2030-W12"]
    overrides = [{"target": "key_dose", "session": "w12-q", "reason": "athlete_choice",
                  "prediction": "p-step"}]
    written = store.append_plan_revision("2030-W12", revision(
        computed_at="2030-03-15T19:10:00+01:00", cycle_overrides=overrides,
        sessions=[session("w12-q", date="2030-03-19", workout="threshold",
                          key_position=key.position)]))
    [read] = written["revisions"][-1]["sessions"]
    assert read["key_position"] == key.position
    assert written["revisions"][-1]["cycle_overrides"] == overrides
    with pytest.raises(StorageError, match="no key session at position 5"):
        store.append_plan_revision("2030-W12", revision(
            2, computed_at="2030-03-16T19:10:00+01:00",
            sessions=[session("w12-q", date="2030-03-19", workout="threshold",
                              key_position=5)]))
