"""The cycle versions in PostgreSQL: one in force, written whole, changed only by its state;
the planner's pass keeps one in force and announces each transition but the takeover."""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid

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


def _goal(store, on=dt.date(2030, 6, 30)):
    write(store, commands.DEADLINE_UPSERT, {
        "deadline_key": "half", "due_on": on, "item": "Semi", "fact_owner": "test",
        "kind": "race", "priority": "a", "race_state": "active", "athlete_label": "Semi",
        "context": {"distance_km": 21.1, "terrain": "road"}})


def _proposal_trame(starts="2030-W12"):
    from conftest import VAULT_ROOT
    template = cycle_templates.load(VAULT_ROOT).preparation_for(21.1, "road")
    goal = generator.Goal(dt.date(2030, 6, 30), 21.1)
    return generator.candidates(template, goal, _inputs(starts))[0]


def test_a_proposal_waits_for_the_athlete_and_only_the_open_one_is_taken(store):
    _goal(store)
    general = _apply(store, _general("2030-W10"), CycleCause.MIGRATION,
                     level=GeneralLevel.MAINTAIN)
    now = dt.datetime(2030, 3, 13, 12, tzinfo=dt.UTC)
    later = now + dt.timedelta(days=7)
    with store.database.transaction() as connection:
        first = store.propose_cycle_in(connection, _proposal_trame(), {"by": "judgment"},
                                       "half", later)
        second = store.propose_cycle_in(connection, _proposal_trame(), {"by": "judgment"},
                                        "half", later)
    assert store.active_cycle().id == general.id, "a proposal changes nothing in force"
    assert store.open_cycle_proposal(now).id == second.id
    with store.database.transaction() as connection:
        assert store.take_cycle_in(connection, first.id, now) is None, "replaced"
        taken = store.take_cycle_in(connection, second.id, now)
    assert taken is not None and store.active_cycle().id == second.id
    assert [state for _, state in _states(store)] == ["closed", "superseded", "active"]
    assert store.cycle_version(general.id).state == "closed"
    assert store.cycle_version(first.id).state == "superseded"


def test_a_proposal_left_untaken_expires_and_nothing_changes(store):
    _goal(store)
    _apply(store, _general("2030-W10"), CycleCause.MIGRATION, level=GeneralLevel.MAINTAIN)
    now = dt.datetime(2030, 3, 13, 12, tzinfo=dt.UTC)
    with store.database.transaction() as connection:
        found = store.propose_cycle_in(connection, _proposal_trame(), {"by": "judgment"},
                                       "half", now + dt.timedelta(days=7))
    late = now + dt.timedelta(days=8)
    with store.database.transaction() as connection:
        assert store.expire_cycle_proposals_in(connection, late) == 1
        assert store.take_cycle_in(connection, found.id, late) is None
    assert store.cycle_version(found.id).state == "expired"
    assert store.active_cycle().kind == CycleKind.GENERAL


def test_the_coach_proposes_and_the_athlete_is_sent_the_summary(store, monkeypatch, capsys):
    from conftest import VAULT_ROOT

    from redline.cli import cycle
    template = cycle_templates.load(VAULT_ROOT).preparation_for(21.1, "road")
    race_on = dt.date(2030, 6, 30)
    starts = generator.candidate_starts(template, race_on)
    week = min(starts.values())
    candidate = next(slug for slug, start in starts.items() if start == week)
    sunday = (generator.iso_week.monday(week) - dt.timedelta(days=1)).isoformat()
    _goal(store, race_on)
    _apply(store, _general(generator.iso_week.shift(week, -4)), CycleCause.MIGRATION,
           level=GeneralLevel.MAINTAIN)
    monkeypatch.setattr("redline.storage.open_store", lambda actor: store)
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)
    decision = json.dumps({"by": "judgment", "alternatives": [
        {"option": "attendre", "rejected_because": "la fenêtre est ouverte"}]})
    monkeypatch.setattr("sys.argv", ["cycle", "propose", "--candidate", candidate,
                                     "--decision", decision, "--date", sunday])
    assert cycle.main() == 0
    assert f"Candidat « {candidate} » proposé pour « Semi »" in capsys.readouterr().out
    state = PostgresStateStore(store.database)
    [job] = [job for job in state.proactive.jobs(active_only=True)
             if job["kind"] == "cycle_proposal"]
    rendered = compose_closed_proactive("cycle_proposal", job["detail"])
    assert rendered is not None and rendered.replies[0][0] == "Je la prends"
    version = uuid.UUID(json.loads(job["detail"])["version_id"])
    monday, written = state.take_cycle(version)
    assert monday == generator.iso_week.monday(week) and written == ()
    assert store.active_cycle().kind == CycleKind.PREPARATION


def test_the_coach_changes_the_level_and_the_athlete_is_told(store, monkeypatch):
    from redline.cli import cycle
    first = _apply(store, _general("2030-W10"), CycleCause.MIGRATION,
                   level=GeneralLevel.MAINTAIN)
    monkeypatch.setattr("redline.storage.open_store", lambda actor: store)
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)
    decision = json.dumps({"by": "request", "alternatives": []})
    monkeypatch.setattr("sys.argv", ["cycle", "level", "--set", "progress", "--decision",
                                     decision, "--date", "2030-03-10"])
    assert cycle.main() == 0
    found = store.active_cycle()
    assert (found.level, found.cause, found.version) == (GeneralLevel.PROGRESS,
                                                         CycleCause.LEVEL_CHANGE, 2)
    assert found.cycle_key == first.cycle_key and found.trame.weeks[0].label == "2030-W11"
    state = PostgresStateStore(store.database)
    assert [job["kind"] for job in state.proactive.jobs(active_only=True)] == [
        "cycle_transition"]


def test_the_planner_asks_for_a_plan_review_once_a_candidate_can_start(store):
    from conftest import VAULT_ROOT
    template = cycle_templates.load(VAULT_ROOT).preparation_for(21.1, "road")
    race_on = dt.date(2030, 6, 30)
    week = generator.window_start(template, race_on)
    _goal(store, race_on)
    state = PostgresStateStore(store.database)
    eve = dt.datetime.combine(generator.iso_week.monday(week) - dt.timedelta(days=1),
                              dt.time(12), tzinfo=dt.UTC)
    state.materialize_intelligence(eve - dt.timedelta(days=7))
    assert not [job for job in state.proactive.jobs(active_only=True)
                if job["kind"] == "plan_review"]
    state.materialize_intelligence(eve)
    state.materialize_intelligence(eve)
    [job] = [job for job in state.proactive.jobs(active_only=True)
             if job["kind"] == "plan_review"]
    assert job["detail"] == "Semi"


def test_a_first_revision_keeps_its_draft_and_the_vault_projects_the_version(store, tmp_path,
                                                                              monkeypatch,
                                                                              capsys):
    from test_weekly_plan import revision, session

    from redline.cli import week_plan
    from redline.storage import projector
    _apply(store, _general(), CycleCause.MIGRATION, level=GeneralLevel.MAINTAIN)
    kept = {"revision": 1, "sessions": []}
    store.append_plan_revision("2030-W12", revision(
        computed_at="2030-03-15T19:10:00+01:00", draft=kept,
        sessions=[session("w12-mar-ef", date="2030-03-19")]))
    assert store.load_plan("2030-W12")["revisions"][-1]["draft"] == kept
    projector.project_cycle(store, tmp_path)
    note = (tmp_path / "coaching" / "current-plan.md").read_text()
    assert note.startswith("# Plan en cours") and "| 2030-W12 | générale |" in note
    monkeypatch.setattr("redline.storage.open_store", lambda actor: store)
    monkeypatch.setattr("sys.argv", ["week-plan", "draft", "--week", "2030-W13"])
    assert week_plan.main() == 0
    drafted = json.loads(capsys.readouterr().out)
    assert drafted["revision"] == 1 and drafted["volume_target_km"] > 0
    monkeypatch.setattr("sys.argv", ["week-plan", "draft", "--week", "2030-W40"])
    from redline.model.errors import InputError
    with pytest.raises(InputError, match="no cycle version holds"):
        week_plan.main()
