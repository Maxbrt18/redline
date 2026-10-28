"""Guarantees exercised by a real PostgreSQL, not by a SQL mock."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa
from conftest import ENGINE_ROOT, VAULT_ROOT, cli_command

from redline.model import predictions as prediction_model
from redline.model.activity import Activity
from redline.service import strength
from redline.service.attachments import encode_payload
from redline.service.postgres_state import PostgresStateStore
from redline.storage import Database, OperationalStore, StorageError
from redline.storage.repository import ATHLETE
from redline.storage.schema import (
    TRIGGERS,
    audit_events,
    coach_predictions,
    conversation_messages,
    conversation_questions,
    correction_deliveries,
    metadata,
    observation_metrics,
    observations,
    proactive_jobs,
    projection_states,
    session_cycles,
    strava_activity_intakes,
    strength_session_links,
    turn_commands,
)

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL absent")


def _add_strength_session(store, payload: dict) -> bool:
    """The core of /strength-report, without its quota: False if the key is already taken."""
    with store.database.transaction() as connection:
        return store._add_strength_session_in(connection, payload, actor="test")


@pytest.fixture(autouse=True)
def clean_database():
    database = Database(URL, application_name="pytest")
    with database.engine.begin() as connection:
        names = ", ".join(f"redline.{table.name}" for table in metadata.sorted_tables)
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
        connection.execute(sa.text("INSERT INTO redline.athletes(id) VALUES ('sam')"))
        # The monitored zone of the fictional instance, as the service syncs it.
        connection.execute(sa.text(
            "INSERT INTO redline.pain_zones(athlete_id, slug, label) VALUES ('sam', 'genou', 'Genou')"))
    yield
    database.dispose()


@pytest.fixture
def store():
    return OperationalStore(Database(URL, application_name="pytest"))


@pytest.fixture(scope="module")
def service_role_store():
    """A database migrated after the real roles exist, then used as redline_app.

    The ordinary pytest database is migrated before the suite starts and its tests run as
    `postgres`. This isolated database replays the production sequence: roles present,
    migration that grants the rights, operation under the application role.
    """
    base_url = sa.engine.make_url(URL)
    maintenance_url = base_url.set(database="postgres")
    admin = sa.create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    database_name = f"redline_acl_{uuid.uuid4().hex[:12]}"
    created_roles: list[str] = []
    with admin.connect() as connection:
        for role in ("redline_owner", "redline_app"):
            exists = connection.execute(sa.text(
                "SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}).scalar()
            if not exists:
                connection.exec_driver_sql(f'CREATE ROLE "{role}"')
                created_roles.append(role)
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

    database = None
    try:
        test_url = base_url.set(database=database_name)
        env = {**os.environ, "DATABASE_URL": test_url.render_as_string(hide_password=False)}
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ENGINE_ROOT, env=env, text=True, capture_output=True, check=False)
        assert migrated.returncode == 0, migrated.stderr or migrated.stdout

        database = Database(test_url.render_as_string(hide_password=False),
                            application_name="pytest-redline-app")

        @sa.event.listens_for(database.engine, "connect")
        def use_service_role(dbapi_connection, _):
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET ROLE redline_app")
            # SET ROLE is transactional: rolling back the first read must not leave
            # later connections running as superuser.
            dbapi_connection.commit()

        yield OperationalStore(database)
    finally:
        if database is not None:
            database.dispose()
        with admin.connect() as connection:
            connection.execute(sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :database AND pid <> pg_backend_pid()"),
                {"database": database_name})
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
            for role in reversed(created_roles):
                connection.exec_driver_sql(f'DROP ROLE "{role}"')
        admin.dispose()


def test_sql_names_follow_one_plural_snake_case_convention():
    assert all(table.name.endswith("s") for table in metadata.sorted_tables)
    for table in metadata.sorted_tables:
        for constraint in table.constraints:
            assert constraint.name is not None
            assert any(constraint.name.startswith(f"{prefix}_{table.name}")
                       for prefix in ("pk", "fk", "uq", "ck"))
        for index in table.indexes:
            assert index.name is not None
            assert index.name.startswith(f"ix_{table.name}")


def observation(strava=90000000008):
    return {
        "occurred_on": dt.date(2026, 8, 12), "kind": "easy",
        "idempotency_key": f"test:{strava or 'manual'}",
        "strava_activity_id": strava, "note": "projection | libre",
        "measurements": {
            "distance_km": {"state": "known", "value": 7.01},
            "rpe": {"state": "pending"},
        },
    }


def test_strava_is_idempotent_in_the_database(store):
    first = store.add_observation(observation())
    second = store.add_observation(observation())
    assert first == second
    assert store.counts()["observations"] == 1


def test_same_idempotency_key_with_different_content_is_refused(store):
    store.add_observation(observation())
    changed = observation()
    changed["note"] = "contenu contradictoire"
    with pytest.raises(StorageError, match="idempotence"):
        store.add_observation(changed)


def test_a_messenger_observation_can_be_linked_to_strava_once(store):
    payload = {**observation(strava=None), "idempotency_key": "messenger:m1:ef"}
    oid = store.add_observation(payload)

    assert store.activity_recorded(90000000001) is False
    assert store.link_activity("messenger:m1:ef", 90000000001) == oid
    assert store.link_activity("messenger:m1:ef", 90000000001) == oid
    assert store.activity_recorded(90000000001) is True
    with pytest.raises(StorageError, match="déjà rattachée"):
        store.link_activity("messenger:m1:ef", 90000000009)


def test_activity_link_obeys_the_transactional_turn_boundary(store):
    store.add_observation({
        **observation(strava=None), "idempotency_key": "messenger:m2:ef"})
    turn_id = "6d1a8d15-00fd-4681-8e35-f7aac9adfd8a"
    store.stage_command(turn_id, "activity-link:messenger:m2:ef:90000000010",
                        "activity.link", {
                            "idempotency_key": "messenger:m2:ef",
                            "strava_activity_id": 90000000010,
                        })

    assert store.activity_recorded(90000000010) is False
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.activity_recorded(90000000010) is True


def test_same_command_key_with_different_payload_is_refused(store):
    turn_id = "f3483ea2-49ac-4c70-bc6a-0b667af5ce4f"
    payload = {**observation(strava=None), "idempotency_key": "commande-stable"}
    store.stage_command(turn_id, "observation:stable", "observation.add", payload)
    with pytest.raises(StorageError, match="commande"):
        store.stage_command(turn_id, "observation:stable", "observation.add",
                            {**payload, "note": "autre contenu"})


def test_a_known_measurement_cannot_be_rewritten(store):
    oid = store.add_observation(observation())
    with pytest.raises(StorageError):
        store.complete_measurement(oid, "distance_km", 8.0)
    store.complete_measurement(oid, "rpe", 3)
    with pytest.raises(StorageError):
        store.complete_measurement(oid, "rpe", 4)


def test_a_known_heart_rate_is_doubted_once_with_its_cause(store):
    """The measurement stays immutable; only the annotation says what it is worth."""
    payload = observation()
    payload["measurements"]["hr_avg_bpm"] = {"state": "known", "value": 157}
    oid = store.add_observation(payload)
    with pytest.raises(StorageError):
        store.doubt_hr(oid, "no_sensor", "  ")
    with pytest.raises(StorageError):
        store.doubt_hr(oid, "poignet", "pas de brassard")
    store.doubt_hr(oid, "no_sensor", "pas de capteur FC, 95 % des points ratés")
    row = next(r for r in store.observation_rows() if r["id"] == oid)
    assert row["hr_doubt"] == "no_sensor"
    assert row["measurements"]["hr_avg_bpm"]["value"] == 157
    with pytest.raises(StorageError):
        store.doubt_hr(oid, "context", "repas")


def test_an_unknown_heart_rate_cannot_be_doubted(store):
    oid = store.add_observation(observation())
    with pytest.raises(StorageError):
        store.doubt_hr(oid, "context", "repas une heure avant")


def test_a_metric_never_recorded_can_still_be_filled_later(store):
    """Only `known` is immutable. `not_applicable` is the DEFAULT of any column absent at
    creation, not data: blocking it would make a session recorded without its numbers
    permanently incompletable (and weigh ZERO in the weekly gauge).
    """
    oid = store.add_observation(observation())
    store.complete_measurement(oid, "duration_s", 6177)
    rows = {row["idempotency_key"]: row for row in store.observation_rows()}
    measured = rows[observation()["idempotency_key"]]["measurements"]["duration_s"]
    assert measured["state"] == "known" and float(measured["value"]) == 6177
    # Once filled, it becomes immutable like the others.
    with pytest.raises(StorageError):
        store.complete_measurement(oid, "duration_s", 7000)


def test_the_purge_only_targets_tables_the_service_may_delete_from(service_role_store):
    """The purge only targets traces that are explicitly expirable."""
    with service_role_store.database.engine.connect() as connection:
        assert connection.execute(sa.text("SELECT current_user")).scalar_one() == "redline_app"
        for table in OperationalStore.EXPIRY_DAYS:
            assert connection.execute(sa.text(
                "SELECT has_table_privilege(current_user, :table, 'DELETE')"),
                {"table": f"redline.{table}"}).scalar_one(), (
                    f"redline_app ne peut pas purger {table}")
    assert service_role_store.purge_expired() == {}


def test_the_purge_removes_markers_but_keeps_command_idempotence(store):
    """An applied command stays the barrier against any late replay.

    Deleting it after 30 days would not necessarily repeat the business effect: the plan
    has its own contiguity and the observation its own identity. But the old command could
    come back as pending and break the new turn. The key must therefore never disappear,
    applied or not.
    """
    turn_id = "7f1c25a0-6f4d-4a51-9b6a-1f2c3d4e5f60"
    store.stage_command(turn_id, "observation:vieille", "observation.add",
                        {**observation(strava=None), "idempotency_key": "vieille"})
    store.apply_turn_with_receipts(turn_id)[0]
    en_attente = "9a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9"
    store.stage_command(en_attente, "observation:jamais", "observation.add",
                        {**observation(strava=None), "idempotency_key": "jamais"})
    with store.database.transaction() as connection:
        connection.execute(sa.text(
            "INSERT INTO redline.proactive_markers(marker_key, created_at) "
            "VALUES ('tech:vieux:2026-01-01', now() - interval '200 days')"))
        connection.execute(sa.text(
            "INSERT INTO redline.proactive_markers(marker_key) VALUES ('tech:frais')"))
        # Age the traces already written by the commands above all at once.
        connection.execute(sa.text(
            "UPDATE redline.turn_commands SET applied_at = now() - interval '90 days' "
            "WHERE applied_at IS NOT NULL"))

    removed = store.purge_expired()
    assert removed["proactive_markers"] == 1
    assert "turn_commands" not in removed
    with store.database.engine.connect() as connection:
        restant = dict(connection.execute(sa.text(
            "SELECT 'markers', count(*) FROM redline.proactive_markers "
            "UNION ALL SELECT 'commandes', count(*) FROM redline.turn_commands "
            "UNION ALL SELECT 'observations', count(*) FROM redline.observations")).all())
    assert restant["markers"] == 1        # the fresh one stays
    assert restant["commandes"] == 2      # applied or pending: both keys stay
    assert restant["observations"] == 1   # no FACT is touched
    with pytest.raises(StorageError, match="collision d'idempotence"):
        store.stage_command(
            "8f0f98df-a68c-44bd-9dc3-80e240775fa8", "observation:vieille",
            "observation.add",
            {**observation(strava=None), "idempotency_key": "vieille"})


def test_the_declared_triggers_are_exactly_those_the_database_carries(store):
    """Guarantees that look at the OLD row would otherwise live only in migrations.

    `alembic check` compares metadata: it sees no trigger, so the loss of one -- a manual
    `DROP`, a partial restore -- would never be reported.

    Both directions matter. A declared but absent trigger is an immutability one believes
    one has. A present but undeclared trigger is a rule nobody can read in `schema.py`.
    """
    with store.database.engine.connect() as connection:
        present = dict(connection.execute(sa.text(
            "SELECT t.tgname, c.relname FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'redline' AND NOT t.tgisinternal")).all())
    assert present == TRIGGERS


def test_strength_capability_stores_only_its_digest_and_an_exact_snapshot(store):
    state = PostgresStateStore(store.database)
    original = {
        "sid": "strength-2026-08-22-a", "session": "a", "title": "Renfo A",
        "exercises": [{"slug": "step-up", "name": "Step-up", "dose": {"slots": []}}],
    }
    token = state.issue_strength_link("4242", original)
    original["title"] = "réécrit après émission"

    with store.database.engine.connect() as connection:
        row = connection.execute(sa.select(strength_session_links)).mappings().one()
    assert row["token_hash"] == hashlib.sha256(token.encode("ascii")).hexdigest()
    assert token not in str(dict(row))
    assert row["snapshot"]["title"] == "Renfo A"
    assert row["expires_at"] > row["created_at"]
    resolved = state.resolve_strength_link("4242", token)
    assert resolved["status"] == "active"
    assert resolved["session"] == row["snapshot"]
    assert state.resolve_strength_link("un-autre-athlète", token)["status"] == "missing"


def test_strength_snapshot_is_database_immutable_and_revocation_irreversible(store):
    state = PostgresStateStore(store.database)
    token = state.issue_strength_link("4242", {
        "sid": "strength-immutable", "session": "b", "title": "Renfo B",
        "exercises": [{"slug": "mollet", "name": "Mollet"}],
    })
    digest = strength.token_digest(token)
    with pytest.raises(StorageError), store.database.transaction() as connection:
        connection.execute(sa.text(
            "UPDATE redline.strength_session_links "
            "SET snapshot = snapshot || '{\"title\":\"faux\"}'::jsonb "
            "WHERE token_hash = :digest"), {"digest": digest})

    assert state.revoke_strength_link("4242", token)
    assert not state.revoke_strength_link("4242", token)
    assert state.resolve_strength_link("4242", token)["status"] == "revoked"
    with pytest.raises(StorageError), store.database.transaction() as connection:
        connection.execute(sa.text(
            "UPDATE redline.strength_session_links SET revoked_at = NULL "
            "WHERE token_hash = :digest"), {"digest": digest})


def test_audit_log_is_append_only_in_postgresql(store):
    store.add_observation(observation())
    with pytest.raises(StorageError), store.database.transaction() as connection:
        connection.execute(sa.text("UPDATE redline.audit_events SET action = 'réécrit'"))


def test_strength_series_are_rows_and_session_key_is_atomic(store):
    payload = {
        "session_key": "strength-2026-08-11", "day": dt.date(2026, 8, 11),
        "workout": "a", "exercises": [{
            "exercise_slug": "calf-raise-straight", "difficulty": 10,
            "sets": [{"side": "right", "set_index": i + 1, "repetitions": value}
                     for i, value in enumerate((23, 22, 20))],
        }],
    }
    assert _add_strength_session(store, payload) is True
    assert _add_strength_session(store, payload) is False
    assert [row["repetitions"] for row in store.strength_history()[0]["exercises"][0]["sets"]] \
        == [23, 22, 20]
    assert store.strength_history()[0]["exercises"][0]["position"] == 1


def test_partial_strength_report_preserves_unknown_counts_notes_and_exact_prescription(store):
    from redline.model.dose import Dose
    from redline.service.web import _strength_payload
    session = {"sid": "partial", "session": "a", "exercises": [{
        "slug": "calf-raise-bent", "name": "Mollet", "dose": Dose.read({
            "measure": "amrap", "sets": 3, "side": "each"}).payload()}]}
    report = strength.parse_report({"performed_on": "2026-10-02", "exercises": [{
        "i": 0, "status": "partial", "reps": [None, 17, 12], "score": 6,
        "note": "Arrêt après trois côtés."}]}, session["exercises"])
    rows = strength.log_entries(session, report, "2026-10-02")
    payload = _strength_payload(session, report, rows, psid="4242", message="Renfo partiel")
    assert store.accept_strength_report(payload, 8) == "accepted"
    assert store.accept_strength_report(payload, 8) == "duplicate"
    result = store.strength_history()[0]["exercises"][0]
    assert result["status"] == "partial" and result["completed_slots"] == 3
    assert result["note"] == "Arrêt après trois côtés." and result["skipped"] is False
    assert result["sets"] == [{"side": "left", "set_index": 1, "repetitions": 17},
                              {"side": "right", "set_index": 2, "repetitions": 12}]
    assert store.previous_strength_prescription({"sid": "next", "session": "a"}, dt.date(2026, 10, 2)) == session
    assert store.previous_strength_prescription(session, dt.date(2026, 10, 2)) is None
    assert store.previous_strength_prescription({"sid": "next", "session": "b"}, dt.date(2026, 10, 2)) is None


def _strength_plan(store, *, two_sessions=False):
    from test_weekly_plan import revision, session
    sessions = [session("w33-mar-a", date="2026-08-11", workout="strength", routine="a")]
    if two_sessions:
        sessions.append(session("w33-mar-a-bis", date="2026-08-11", workout="strength", routine="a"))
    store.append_plan_revision("2026-W33", revision(
        sessions=sessions, computed_at="2026-08-09T19:10:00+02:00"))


@pytest.mark.parametrize("late", [False, True])
def test_strength_report_completes_cycle_and_cancels_false_missed_job(store, late):
    _strength_plan(store)
    state = PostgresStateStore(store.database)
    now = dt.datetime(2026, 8, 12, 8, tzinfo=dt.UTC)
    cycle_id = store.session_cycle_status()[0]["id"]
    job_id = f"missed-session:{cycle_id}"
    if late:
        state.materialize_intelligence(now)
        assert state.missed_session_is_current(job_id)
    assert store.accept_strength_report({
        "session_key": "strength-a", "performed_on": dt.date(2026, 8, 11),
        "workout": "a", "duration_minutes": 61, "pains": {"genou": {"after": 1}},
        "psid": "4242", "message_id": "strength:strength-a", "message": "Renfo A fait",
    }, 8) == "accepted"
    assert store.session_cycle_status()[0]["state"] == "completed"
    state.materialize_intelligence(now)
    state.materialize_intelligence(now)
    assert not state.missed_session_is_current(job_id)
    assert not any(job["kind"] == "missed_session"
                   for job in state.proactive.jobs(active_only=True))
    assert len(store.strength_history()) == 1
    with store.database.engine.connect() as connection:
        assert connection.execute(sa.select(sa.func.count()).select_from(observations)).scalar() == 0
        assert connection.execute(sa.text(
            "SELECT count(*) FROM redline.audit_events "
            "WHERE action = 'session.completed.strength'"
        )).scalar_one() == 1


def test_historical_strength_report_is_reconciled_before_missing_detection(store):
    _add_strength_session(store, {
        "session_key": "historical-a", "day": dt.date(2026, 8, 11), "workout": "a"})
    _strength_plan(store)
    state = PostgresStateStore(store.database)
    state.materialize_intelligence(dt.datetime(2026, 8, 12, 8, tzinfo=dt.UTC))
    assert store.session_cycle_status()[0]["state"] == "completed"
    assert not any(job["kind"] == "missed_session" for job in state.proactive.jobs())


@pytest.mark.parametrize("day,workout", [(dt.date(2026, 8, 10), "a"),
                                        (dt.date(2026, 8, 11), "b")])
def test_other_strength_date_or_workout_does_not_complete_the_plan(store, day, workout):
    _strength_plan(store)
    _add_strength_session(store, {"session_key": "other", "day": day, "workout": workout})
    state = PostgresStateStore(store.database)
    state.materialize_intelligence(dt.datetime(2026, 8, 12, 8, tzinfo=dt.UTC))
    cycle = store.session_cycle_status()[0]
    assert cycle["state"] == "missed"
    assert state.missed_session_is_current(f"missed-session:{cycle['id']}")


@pytest.mark.parametrize("two_sessions", [False, True])
def test_ambiguous_strength_match_neither_assigns_nor_claims_absence(store, two_sessions):
    # Reports that predate the plan: one report for two sessions, or the reverse.
    for index in range(1 if two_sessions else 2):
        _add_strength_session(store, {
            "session_key": f"report-{index}", "day": dt.date(2026, 8, 11), "workout": "a"})
    _strength_plan(store, two_sessions=two_sessions)
    state = PostgresStateStore(store.database)
    state.materialize_intelligence(dt.datetime(2026, 8, 12, 8, tzinfo=dt.UTC))
    assert all(row["state"] == "planned" for row in store.session_cycle_status())
    assert not any(job["kind"] == "missed_session" for job in state.proactive.jobs())


def test_cancelled_strength_is_not_reopened_and_unknown_missed_job_is_rejected(store):
    _strength_plan(store)
    with store.database.transaction() as connection:
        connection.execute(session_cycles.update().values(state="cancelled"))
    _add_strength_session(store, {
        "session_key": "cancelled-a", "day": dt.date(2026, 8, 11), "workout": "a"})
    state = PostgresStateStore(store.database)
    assert store.session_cycle_status()[0]["state"] == "cancelled"
    for key in ("", "missed-session:invalid", f"missed-session:{uuid.uuid4()}"):
        assert not state.missed_session_is_current(key)


def test_ambiguous_historical_misses_do_not_create_an_adherence_signal(store):
    _add_strength_session(store, {
        "session_key": "historical", "day": dt.date(2026, 8, 11), "workout": "a"})
    _strength_plan(store, two_sessions=True)
    now = dt.datetime(2026, 8, 12, 8, tzinfo=dt.UTC)
    with store.database.transaction() as connection:
        connection.execute(session_cycles.update().values(state="missed", updated_at=now))
    state = PostgresStateStore(store.database)
    state.materialize_intelligence(now)
    for cycle in store.session_cycle_status():
        assert not state.missed_session_is_current(f"missed-session:{cycle['id']}")
    assert not any(job["kind"] == "daily_signal" for job in state.proactive.jobs())


def test_plan_revisions_are_contiguous_and_immutable(store):
    base = {
        "revision": 1, "computed_at": "2026-08-09T19:10:00+02:00", "reason": "test",
        "sessions": [], "gates": [],
    }
    store.append_plan_revision("2026-W33", base)
    with pytest.raises(StorageError):
        store.append_plan_revision("2026-W33", {**base, "revision": 3})
    plan = store.load_plan("2026-W33")
    assert plan and [row["revision"] for row in plan["revisions"]] == [1]
    with store.database.engine.connect() as connection:
        revision_id = connection.execute(sa.text(
            "SELECT id FROM redline.plan_revisions")).scalar_one()
    with pytest.raises(StorageError), store.database.transaction() as connection:
        connection.execute(sa.text(
            "UPDATE redline.plan_revisions SET reason = 'réécrit' WHERE id = :id"),
            {"id": revision_id})
    with pytest.raises(StorageError), store.database.transaction() as connection:
        connection.execute(sa.text(
            "DELETE FROM redline.plan_revisions WHERE id = :id"), {"id": revision_id})
    assert store.load_plan("2026-W33") is not None


def test_exposure_is_confirmed_then_can_be_completed_or_corrected_without_duplication(store):
    store.add_observation({
        "occurred_on": dt.date(2026, 9, 2), "kind": "easy",
        "idempotency_key": "messenger:2026-09-02:ef",
        "note": "Cadence travaillée volontairement.", "measurements": {},
    })
    candidate = {
        "exposure_key": "cadence:2026-09-02:ef",
        "observation_key": "messenger:2026-09-02:ef",
        "exposure_type": "cadence", "origin": "voluntary", "status": "candidate",
        "duration_min": 44, "target_value": 172, "target_unit": "spm",
        "tolerance": "easy", "note": "à confirmer",
    }
    store.upsert_exposure(candidate)
    assert store.exposure_rows(confirmed_only=True) == []

    confirmed = {**candidate, "status": "confirmed", "note": "confirmé par Sam"}
    store.upsert_exposure(confirmed)
    assert store.exposure_rows(confirmed_only=True)[0]["origin"] == "voluntary"

    store.upsert_exposure({**confirmed, "tolerance": "easy"})
    assert store.exposure_rows(confirmed_only=True)[0]["tolerance"] == "easy"
    assert len(store.exposure_rows()) == 1
    store.upsert_exposure({**confirmed, "status": "dismissed"})
    assert store.exposure_rows(confirmed_only=True) == []


def test_intelligence_ignores_a_confirmed_intentional_cadence_jump(store):
    for day, cadence, key in (
            (dt.date(2026, 8, 24), 160, "cadence:old"),
            (dt.date(2026, 8, 31), 170, "cadence:worked")):
        store.add_observation({
            "occurred_on": day, "kind": "easy", "idempotency_key": key,
            "note": "EF comparable", "measurements": {
                "distance_km": {"state": "known", "value": 7},
                "cadence_spm": {"state": "known", "value": cadence},
            },
        })
    store.upsert_exposure({
        "exposure_key": "cadence:worked", "observation_key": "cadence:worked",
        "exposure_type": "cadence", "origin": "voluntary", "status": "confirmed",
        "duration_min": 40, "target_value": 170, "target_unit": "spm",
        "tolerance": "easy", "note": "travail volontaire",
    })

    state = PostgresStateStore(store.database)
    state.materialize_intelligence(dt.datetime(2026, 9, 4, 8, tzinfo=dt.UTC))

    assert not any(row["id"].startswith("daily-signal:")
                   for row in state.proactive.jobs())


def test_a_removed_gate_requires_an_existing_canonical_observation(store):
    from test_weekly_plan import revision as build_revision

    first = build_revision(
        1, sessions=[], computed_at="2026-08-09T19:10:00+02:00",
        gates=[{"id": "w33-genou", "due": "2026-08-14", "when": "apres",
                "question": "Comment est la blessure au réveil ce matin ?",
                "on_fail": "appliquer le repli prévu"}])
    store.append_plan_revision("2026-W33", first)

    second = build_revision(
        2, sessions=[], computed_at="2026-08-14T09:10:00+02:00", gates=[])
    with pytest.raises(StorageError, match="retiré sans résolution typée"):
        store.append_plan_revision("2026-W33", second)

    second["evidence"] = [
        "gate:w33-genou=observation:messenger:2026-08-14:reveil"]
    with pytest.raises(StorageError, match="observation canonique inconnue"):
        store.append_plan_revision("2026-W33", second)

    store.add_observation({
        "occurred_on": dt.date(2026, 8, 14), "kind": "wake-trace",
        "idempotency_key": "messenger:2026-08-14:reveil",
        "note": "Aucune gêne dans les dix premiers pas.",
        "pains": {"genou": {"state": "unavailable"}},
    })
    store.append_plan_revision("2026-W33", second)
    assert store.load_plan("2026-W33")["revisions"][-1]["revision"] == 2


def test_a_due_gate_can_close_on_its_precommitted_fallback(store):
    from test_weekly_plan import revision as build_revision

    first = build_revision(
        1, sessions=[], computed_at="2026-08-09T19:10:00+02:00",
        gates=[{"id": "w33-genou", "due": "2026-08-14", "when": "apres",
                "question": "Comment est la blessure au réveil ce matin ?",
                "on_fail": "appliquer le repli prévu"}])
    store.append_plan_revision("2026-W33", first)
    second = build_revision(
        2, sessions=[], computed_at="2026-08-14T09:10:00+02:00", gates=[],
        evidence=["gate:w33-genou=fallback:repli-applique"])
    store.append_plan_revision("2026-W33", second)


def test_a_timed_plan_session_survives_storage_and_creates_one_stable_cycle(store):
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    first = build_revision(1, sessions=[session(
        "w33-ven-ef", date="2026-08-14", start_time="18:30")],
        computed_at="2026-08-09T19:10:00+02:00")
    store.append_plan_revision("2026-W33", first)
    loaded = store.load_plan("2026-W33")
    assert loaded["revisions"][0]["sessions"][0]["start_time"] == "18:30"

    second_session = session("w33-ven-ef", date="2026-08-14", start_time="19:00")
    second = build_revision(2, sessions=[second_session],
                            computed_at="2026-08-10T19:10:00+02:00")
    store.append_plan_revision("2026-W33", second)
    with store.database.engine.connect() as connection:
        rows = connection.execute(sa.select(session_cycles)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["state"] == "planned"
    assert rows[0]["planned_for"].astimezone(dt.timezone(dt.timedelta(hours=2))).strftime(
        "%Y-%m-%d %H:%M") == "2026-08-14 19:00"


def test_a_load_increase_waits_for_and_consumes_one_challenge(store):
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    first = build_revision(1, sessions=[session(distance_target_km=7, distance_cap_km=9)],
                           computed_at="2026-08-09T19:10:00+02:00")
    store.append_plan_revision("2026-W33", first)
    increased = build_revision(2, sessions=[session(distance_target_km=8, distance_cap_km=9)],
                               computed_at="2026-08-10T19:10:00+02:00")
    with pytest.raises(StorageError, match="hausse de charge bloquée"):
        store.append_plan_revision("2026-W33", increased)

    store.upsert_prediction({
        "prediction_key": "w33-charge", "domain": "charge",
        "statement": "8 km restent assimilables sans douleur le lendemain",
        "probability": .7, "due_on": dt.date(2026, 8, 12), "direction": "increase",
    })
    store.upsert_prediction({"prediction_key": "w33-charge", "operation": "challenge",
                             "challenge": "La hausse suit une semaine incomplète."})
    store.append_plan_revision("2026-W33", increased)
    with store.database.engine.connect() as connection:
        prediction = connection.execute(sa.select(coach_predictions)).mappings().one()
    assert prediction["used_at"] is not None


def test_a_prediction_resolved_without_its_observation_is_rectified_to_void(store):
    # A prediction settled false while its condition was not met and its wake-up question
    # was still open must be rectifiable to void.
    store.upsert_prediction({
        "prediction_key": "w40-dim", "domain": "charge", "statement": "trace à la base",
        "probability": .65, "due_on": dt.date(2026, 10, 5)})
    with pytest.raises(StorageError, match="seule une prédiction soldée"):
        store.upsert_prediction({"prediction_key": "w40-dim", "operation": "rectify",
                                 "reason": "condition non tenue"})
    store.upsert_prediction({"prediction_key": "w40-dim", "operation": "resolve",
                             "outcome": False})
    with pytest.raises(StorageError, match="exige son motif"):
        store.upsert_prediction({"prediction_key": "w40-dim", "operation": "rectify",
                                 "reason": " "})
    store.upsert_prediction({"prediction_key": "w40-dim", "operation": "rectify",
                             "reason": "8,81 km pour un plafond de 8,5 km"})
    store.upsert_prediction({"prediction_key": "w40-dim", "operation": "rectify",
                             "reason": "rejouée"})
    (row,) = store.prediction_rows()
    assert (row["status"], row["outcome"], row["brier_score"]) == ("void", None, None)
    assert prediction_model.calibration(store.prediction_rows(), dt.date(2026, 10, 9))[0][
        "resolved"] == 0


def test_a_settled_prediction_keeps_why_it_failed_and_the_calibration_counts_it(store):
    for key in ("refuted", "untested"):
        store.upsert_prediction({
            "prediction_key": key, "domain": "corps", "statement": "trace légère",
            "probability": .7, "due_on": dt.date(2026, 10, 5)})
    store.upsert_prediction({"prediction_key": "refuted", "operation": "resolve",
                             "outcome": False, "reason": "optimistic"})
    store.upsert_prediction({"prediction_key": "untested", "operation": "resolve",
                             "outcome": None, "void": True, "reason": "exposure"})
    reasons = {row["prediction_key"]: row["outcome_reason"] for row in store.prediction_rows()}
    assert reasons == {"refuted": "optimistic", "untested": "exposure"}
    (line,) = prediction_model.calibration_lines(store.prediction_rows(), dt.date(2026, 10, 9))
    assert "réfutée optimiste 1" in line and "void exposition 1" in line


def test_a_reason_that_does_not_fit_the_outcome_is_refused_by_the_database(store):
    store.upsert_prediction({
        "prediction_key": "held", "domain": "corps", "statement": "trace légère",
        "probability": .7, "due_on": dt.date(2026, 10, 5)})
    with pytest.raises(StorageError):
        store.upsert_prediction({"prediction_key": "held", "operation": "resolve",
                                 "outcome": True, "reason": "optimistic"})


def test_a_sealed_permit_choice_creates_its_prediction_and_cannot_be_rewritten(store):
    choice = {"iso_week": "2026-W40", "session_key": "w40-mar-ef",
              "scheduled_on": dt.date(2026, 9, 29), "coach_minutes": 40,
              "prediction_key": "easy-permit-w40-mar-ef",
              "statement": "Choix scellé : 40 min.", "probability": .6,
              "due_on": dt.date(2026, 10, 4)}
    store.seal_permit_choice(choice)
    store.seal_permit_choice(choice)  # replayed identically: idempotent
    assert [row["coach_minutes"] for row in store.permit_choice_rows()] == [40]
    prediction = next(row for row in store.prediction_rows()
                      if row["prediction_key"] == "easy-permit-w40-mar-ef")
    assert prediction["domain"] == "easy-permit" and prediction["status"] == "open"
    with pytest.raises(StorageError, match="ne se corrige pas"):
        store.seal_permit_choice({**choice, "coach_minutes": 50})


def test_a_typed_unavailability_blocks_a_demanding_session(store):
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    store.upsert_deadline({
        "deadline_key": "examens", "due_on": dt.date(2026, 8, 5),
        "starts_on": dt.date(2026, 8, 4), "ends_on": dt.date(2026, 8, 7),
        "kind": "availability", "item": "examens", "fact_owner": "athlete/athlete.md",
        "blocks": "séances exigeantes", "status": "scheduled", "context": {},
    })
    candidate = build_revision(1, sessions=[session(
        "w32-mer-sl", "2026-08-05", "long")])
    with pytest.raises(StorageError, match="indisponibilité typée examens"):
        store.append_plan_revision("2026-W32", candidate)


def test_a_revision_after_the_run_keeps_its_pending_gut_review(store):
    """A revision must not cancel the pending gut review of a session already run."""
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    long_run = session("w33-dim-sl", "2026-08-16", "long", carbs_target_g_h=60)
    store.append_plan_revision("2026-W33", build_revision(
        1, sessions=[long_run], computed_at="2026-08-09T19:10:00+02:00"))
    store.add_observation({
        "occurred_on": dt.date(2026, 8, 16), "kind": "long",
        "started_at": dt.datetime(2026, 8, 16, 8, 0, tzinfo=dt.UTC),
        "idempotency_key": "strava:4242", "strava_activity_id": 4242,
        "note": "", "measurements": {},
        "session_key": "2026-W33:w33-dim-sl"})
    state = PostgresStateStore(store.database)
    state.materialize_intelligence(dt.datetime(2026, 8, 16, 12, tzinfo=dt.UTC))

    def gut_review():
        return [job["status"] for job in state.proactive.jobs()
                if job["kind"] == "gut_review"]

    assert gut_review() == ["pending"]
    store.append_plan_revision("2026-W33", build_revision(
        2, sessions=[long_run], computed_at="2026-08-16T19:10:00+02:00"))
    assert gut_review() == ["pending"]


def test_deterministic_strava_capture_matches_a_unique_planned_session(store):
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    store.append_plan_revision("2026-W33", build_revision(
        1, sessions=[session("w33-ven-ef", date="2026-08-14")],
        computed_at="2026-08-09T19:10:00+02:00"))
    result = store.ingest_strava_activity(Activity(
        id=90000000011, sport="Run", name="EF du vendredi",
        start_local="2026-08-14T08:03:00", distance_m=7123.0, moving_s=2530,
        hr_avg_bpm=151.2, hr_max_bpm=168, cadence_spm=169.4, elevation_gain_m=62.5))
    assert result["status"] == "matched"
    assert result["candidate_session_keys"] == ["2026-W33:w33-ven-ef"]
    with store.database.engine.connect() as connection:
        intake = connection.execute(sa.select(strava_activity_intakes)).mappings().one()
        cycle = connection.execute(sa.select(session_cycles)).mappings().one()
    assert intake["observation_id"] == cycle["observation_id"]
    assert store.activity_recorded(90000000011)


def test_historical_capture_links_an_existing_observation_to_its_cycle(store):
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    paris = dt.timezone(dt.timedelta(hours=2))
    store.append_plan_revision("2026-W33", build_revision(
        1, sessions=[session("w33-ven-ef", date="2026-08-14")],
        computed_at="2026-08-09T19:10:00+02:00"))
    observation_id = store.add_observation({
        "occurred_on": dt.date(2026, 8, 14), "kind": "easy",
        "idempotency_key": "strava:90000000012", "strava_activity_id": 90000000012,
        "measurements": {},
    })
    # The observation predates the started_at column: the distillate must complete it.
    result = store.ingest_strava_activity(Activity(
        id=90000000012, sport="Run", name="EF historique",
        start_local="2026-08-14T08:03:00", distance_m=7000.0, moving_s=2520))
    assert result["candidate_session_keys"] == ["2026-W33:w33-ven-ef"]
    with store.database.engine.connect() as connection:
        cycle = connection.execute(sa.select(session_cycles)).mappings().one()
        stored = connection.execute(sa.select(observations).where(
            observations.c.id == observation_id)).mappings().one()
    assert cycle["observation_id"] == observation_id
    assert cycle["state"] == "completed"
    assert stored["started_at"].astimezone(paris).strftime("%H:%M") == "08:03"


def test_a_wbgt_already_logged_by_the_coach_does_not_sink_the_capture(store):
    """The coach logged the WBGT before the webhook: that value stays and the
    pipeline's differing one becomes a trace, instead of failing the capture."""
    observation_id = store.add_observation({
        "occurred_on": dt.date(2026, 9, 26), "kind": "easy",
        "idempotency_key": "strava:90000000013", "strava_activity_id": 90000000013,
        "measurements": {"wbgt_c": {"state": "known", "value": 11.0}},
    })
    store.ingest_strava_activity(Activity(
        id=90000000013, sport="Run", name="Morning Run",
        start_local="2026-09-26T07:45:00", distance_m=8000.0, moving_s=2880,
        route_polyline="x"))
    assert store.finish_strava_enrichment(90000000013, "wbgt", value=14.2) == "known"
    with store.database.engine.connect() as connection:
        kept = connection.execute(sa.select(observation_metrics.c.wbgt_c).where(
            observation_metrics.c.observation_id == observation_id)).scalar_one()
        trace = connection.execute(sa.select(audit_events.c.payload).where(
            audit_events.c.action == "strava.enriched")).scalar_one()
    assert float(kept) == 11.0
    assert trace["observation_kept"] == 11.0 and trace["value"] == 14.2
    assert store.strava_intake(90000000013)["status"] != "failed"


def test_deterministic_strava_capture_preserves_ambiguity_without_inventing_a_type(store):
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    store.append_plan_revision("2026-W33", build_revision(
        1, sessions=[session("w33-ef-a", date="2026-08-14"),
                     session("w33-ef-b", date="2026-08-14")],
        computed_at="2026-08-09T19:10:00+02:00"))
    result = store.ingest_strava_activity(Activity(
        id=90000000014, sport="Run", name="Sortie",
        start_local="2026-08-14T08:03:00", distance_m=7000.0, moving_s=2520))
    assert result["status"] == "ambiguous"
    assert len(result["candidate_session_keys"]) == 2
    assert store.activity_recorded(90000000014) is False
    assert store.strava_intakes(unresolved_only=True)[0]["activity_id"] == 90000000014
    store.match_session("2026-W33:w33-ef-b", 90000000014)
    assert store.activity_recorded(90000000014) is True
    assert store.strava_intakes(unresolved_only=True) == []


def test_observed_time_wins_but_keeps_the_optional_declared_clock(store):
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    paris = dt.timezone(dt.timedelta(hours=2))
    store.append_plan_revision("2026-W33", build_revision(
        1, sessions=[session("w33-ven-ef", date="2026-08-14")],
        computed_at="2026-08-09T19:10:00+02:00"))
    store.declare_session_start({
        "session_key": "2026-W33:w33-ven-ef",
        "declared_at": dt.datetime(2026, 8, 14, 7, 55, tzinfo=paris),
    })
    store.add_observation({
        "occurred_on": dt.date(2026, 8, 14), "kind": "easy",
        "started_at": dt.datetime(2026, 8, 14, 8, 2, tzinfo=paris),
        "idempotency_key": "strava:90000000015", "strava_activity_id": 90000000015,
        "measurements": {},
    })
    with store.database.engine.connect() as connection:
        cycle = connection.execute(sa.select(session_cycles)).mappings().one()
    assert cycle["declared_started_at"].astimezone(paris).strftime("%H:%M") == "07:55"
    assert cycle["started_at"].astimezone(paris).strftime("%H:%M") == "08:02"
    assert cycle["observed_started_at"] == cycle["started_at"]


def test_ambiguous_strava_match_is_left_for_alex_to_decide(store):
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    paris = dt.timezone(dt.timedelta(hours=2))
    store.append_plan_revision("2026-W33", build_revision(
        1, sessions=[
            session("w33-ven-ef-a", date="2026-08-14"),
            session("w33-ven-ef-b", date="2026-08-14"),
        ], computed_at="2026-08-09T19:10:00+02:00"))
    store.add_observation({
        "occurred_on": dt.date(2026, 8, 14), "kind": "easy",
        "started_at": dt.datetime(2026, 8, 14, 8, 2, tzinfo=paris),
        "idempotency_key": "strava:90000000016", "strava_activity_id": 90000000016,
        "measurements": {},
    })
    with store.database.engine.connect() as connection:
        assert connection.execute(sa.select(sa.func.count()).select_from(
            session_cycles).where(session_cycles.c.observation_id.is_not(None))).scalar_one() == 0
    unmatched = store.unmatched_session_activities()
    assert len(unmatched) == 1
    assert unmatched[0]["strava_activity_id"] == 90000000016
    assert unmatched[0]["candidates"] == [
        "2026-W33:w33-ven-ef-a", "2026-W33:w33-ven-ef-b"]
    store.match_session("2026-W33:w33-ven-ef-b", 90000000016)
    with store.database.engine.connect() as connection:
        linked = connection.execute(sa.select(session_cycles.c.logical_key).where(
            session_cycles.c.observation_id.is_not(None))).scalar_one()
    assert linked == "w33-ven-ef-b"


def test_a_typed_gate_survives_the_round_trip_through_postgresql(store):
    """`plan_gates` must have columns for `when` and `holds`: otherwise the revision is
    written typed and comes back bare, hence invalid under the contract that requires
    `when`. A relational store that does not know a field does not complain, it forgets
    it.

    The round trip is the ONLY place where this shows: the model validates, the store
    writes, and nobody reads back what came out."""
    store.append_plan_revision("2026-W33", {
        "revision": 1, "computed_at": "2026-08-09T19:10:00+02:00", "reason": "test",
        "sessions": [],
        "gates": [
            {"id": "g-avant", "due": "2026-08-14", "when": "avant",
             "holds": ["w33-jeu-b", "w33-ven-ef"],
             "question": "la blessure au réveil ?", "on_fail": "rest"},
            {"id": "g-apres", "due": "2026-08-15", "when": "apres",
             "question": "la cheville a parlé pendant la pliométrie ?",
             "on_fail": "la pliométrie sort de la séance A"},
        ],
    })
    gates = {gate["id"]: gate for gate in store.load_plan("2026-W33")["revisions"][0]["gates"]}
    assert gates["g-avant"]["when"] == "avant"
    assert gates["g-avant"]["holds"] == ["w33-jeu-b", "w33-ven-ef"]
    assert gates["g-apres"]["when"] == "apres"
    # Absent, not None: the schema reads a missing `when` as "written before typing",
    # which an explicit None would not say.
    assert "holds" not in gates["g-apres"]


def test_a_revision_round_trip_has_no_schema_version(store):
    store.append_plan_revision("2026-W33", {
        "revision": 1, "computed_at": "2026-08-09T19:10:00+02:00", "reason": "test",
        "sessions": [], "gates": [],
    })
    revision = store.load_plan("2026-W33")["revisions"][0]
    assert "text_contract" not in revision


def test_a_stale_plan_projection_is_detected_without_being_repaired(store, tmp_path):
    """The `generated views up to date` check must really open the weekly plan view and
    compare it, even when projections are still waiting in the outbox after CLI writes
    made outside a turn. Like the other checks, it does not repair: a check that
    repairs silently is not a check.
    """
    from redline.storage.projector import plan_drift, plan_view_count, project_plans

    journal = tmp_path / "athlete" / "journal"
    journal.mkdir(parents=True)
    page = journal / "2026-w33.md"
    page.write_text("---\ntype: journal\n---\n\n<!-- WEEK_PLAN_START -->\n"
                    "périmé\n<!-- WEEK_PLAN_END -->\n", encoding="utf-8")
    store.append_plan_revision("2026-W33", {
        "revision": 1, "computed_at": "2026-08-09T19:10:00+02:00",
        "reason": "programme initial", "gates": [], "constraints": [],
        "evidence": ["registre W32"],
        "sessions": [{
            "id": "w33-lun-ef", "title": "EF", "date": "2026-08-10", "kind": "run",
            "workout": "easy", "status": "planned", "demanding": False,
            "distance_cap_km": 9.0, "sources": ["[[current-plan]]"]}]})

    assert plan_drift(store, tmp_path) == ["athlete/journal/2026-w33.md"]
    assert "périmé" in page.read_text(encoding="utf-8"), "le contrôle n'écrit pas"

    project_plans(store, tmp_path)
    assert plan_drift(store, tmp_path) == []

    # The hot-cache does not exist in this vault: the check skips it instead of crashing
    # on it, since a crashing check says nothing about the drift it was meant to see.
    assert not (tmp_path / "ai" / "hot-cache.md").exists()
    assert plan_view_count(store, tmp_path) == 1


def test_state_claims_are_persisted_and_jobs_use_the_same_database():
    database = Database(URL, application_name="pytest-state")
    state = PostgresStateStore(database)
    assert state.claim_inbound("message-1", "4242", "attachment", "")
    assert not state.claim_inbound("message-1", "4242", "text", "doublon")
    assert state.pending_inbound() == [{
        "message_id": "message-1", "psid": "4242", "kind": "attachment", "body": ""}]
    state.complete_inbound(("message-1",))
    assert state.pending_inbound() == []
    assert state.proactive.enqueue_job("job-1", "morning", "", 1.0)
    job = state.proactive.claim_due_job(dt.datetime.now(dt.UTC).timestamp())
    assert job and job["id"] == "job-1" and job["status"] == "running"
    assert state.proactive.claim_due_job(dt.datetime.now(dt.UTC).timestamp()) is None
    state.proactive.finish_delivery("job-1", dt.datetime.now(dt.UTC))
    assert state.proactive.jobs()[0]["status"] == "done"
    now = dt.datetime.now(dt.UTC).timestamp()
    state.proactive.reopen_job("job-1", now, now + 3600)
    with database.engine.connect() as connection:
        reopened = connection.execute(sa.select(
            proactive_jobs.c.status, proactive_jobs.c.completed_at).where(
                proactive_jobs.c.job_key == "job-1")).one()
    assert reopened.status == "pending"
    assert reopened.completed_at is None


def test_a_waiting_debrief_is_brought_forward_or_taken_by_a_reply():
    """A signal from the athlete brings the debrief forward and never pushes it back; a
    reactive turn claims it before its due time, only once."""
    database = Database(URL, application_name="pytest-hasten")
    state = PostgresStateStore(database)
    now = dt.datetime.now(dt.UTC).timestamp()
    state.proactive.record_activity("999", now + 300)
    assert not state.proactive.hasten_job("strava:999", now + 600)
    assert state.proactive.hasten_job("strava:999", now)
    assert state.proactive.jobs()[0]["due"] == pytest.approx(now, abs=0.01)
    assert not state.proactive.hasten_job("strava:inconnu", now)

    state.proactive.record_activity("1000", now + 300)
    claimed = state.proactive.claim_job("strava:1000")
    assert claimed and claimed["status"] == "running" and claimed["attempts"] == 1
    assert state.proactive.claim_job("strava:1000") is None
    assert not state.proactive.hasten_job("strava:1000", now)
    assert state.proactive.release_job("strava:1000", delay=0)
    assert state.proactive.claim_job("strava:1000")["status"] == "running"


def test_postgres_delivery_count_never_becomes_a_daily_cap(make_config):
    database = Database(URL, application_name="pytest-proactive-no-cap")
    state = PostgresStateStore(database)
    now = dt.datetime(2026, 8, 27, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    config = make_config(proactive_min_gap_hours=0)

    for _ in range(100):
        state.proactive.finish_delivery("", now)

    assert state.proactive.would_pass(now, config)


def test_messenger_reply_resolves_the_exact_outbound_chunk():
    database = Database(URL, application_name="pytest-replies")
    state = PostgresStateStore(database)
    state.record_outbound(
        "4242", "Premier morceau.\n---\nDeuxième morceau.",
        source_key="test:reply-target",
        platform_messages=(("mid-1", "Premier morceau."),
                           ("mid-2", "Deuxième morceau.")))
    assert state.claim_inbound(
        "mid-reply", "4242", "text", "C'est ce deuxième point.",
        reply_to_message_id="mid-2")

    context = state.reply_context(("mid-reply",))

    assert "Deuxième morceau." in context
    assert "Premier morceau." not in context
    assert "message antérieur du coach" in context


def test_messenger_reply_to_an_existing_inbound_is_backed_by_the_same_relation():
    database = Database(URL, application_name="pytest-replies-inbound")
    state = PostgresStateStore(database)
    assert state.claim_inbound("mid-old", "4242", "text", "Mon ancien constat.")
    assert state.claim_inbound(
        "mid-new", "4242", "text", "Je précise.", reply_to_message_id="mid-old")

    context = state.reply_context(("mid-new",))

    assert "message antérieur de ⟨athlète⟩" in context
    assert "Mon ancien constat." in context


def _ask(store, state, questions, source_key, *, body=None, platform_messages=()):
    """A turn that DECLARES its questions (`fact-write ask`), then the delivery."""
    from redline.model import commands
    questions = (questions,) if isinstance(questions, str) else tuple(questions)
    turn_id = str(uuid.uuid4())
    for index, question in enumerate(questions):
        store.stage_command(turn_id, f"{source_key}:ask:{index}", commands.QUESTION_ASK,
                            {"text": question, "cells": []})
    store.apply_turn_with_receipts(turn_id)[0]
    state.record_outbound("4242", body or " ".join(questions), source_key=source_key,
                          platform_messages=platform_messages, turn_id=turn_id)
    return turn_id


def _review_question(store, question, message_key, status="answered", **extra):
    with store.database.engine.connect() as connection:
        question_id = connection.execute(sa.select(conversation_questions.c.id).where(
            conversation_questions.c.question == question)).scalar_one()
        answer_id = connection.execute(sa.select(conversation_messages.c.id).where(
            conversation_messages.c.source_key == "inbound:" + message_key)).scalar_one()
    payload = {"question_id": str(question_id), "answer_id": str(answer_id),
               "status": status, "note": "Réponse examinée.", **extra}
    store.review_question(payload)
    return payload


def test_an_open_question_requires_an_explicit_review_of_the_answer(store):
    state = PostgresStateStore(store.database)
    question = "Ces petits pas étaient-ils spontanés ou volontaires ?"
    _ask(store, state, question, "coach:q1")
    for key, message in (("metrics", "RPE 3, douleur 1, plaisir 6"),
                         ("answer", "C'était volontaire")):
        assert state.claim_inbound(key, "4242", "text", message)
        state.record_outbound("4242", "Compris.", source_key="coach:" + key)
        assert "Questions restées ouvertes" in state.relationship_context("4242")
    _review_question(store, question, "answer")
    assert "Questions restées ouvertes" not in state.relationship_context("4242")


def test_two_questions_in_one_swipe_target_survive_a_partial_answer(store):
    state = PostgresStateStore(store.database)
    first, second = "Quel était ton RPE ?", "Et la blessure au réveil ?"
    _ask(store, state, (first, second), "q",
         platform_messages=(("mid-q", first + " " + second),))
    assert state.claim_inbound("partial", "4242", "text", "RPE 3", reply_to_message_id="mid-q")
    state.record_outbound("4242", "Compris pour le RPE.", source_key="r")
    assert len(store.question_rows()) == 2
    _review_question(store, first, "partial")
    remaining = store.question_rows()
    assert len(remaining) == 1 and remaining[0]["question"] == second


def test_a_number_or_a_one_word_answer_never_closes_a_medical_question(store):
    state = PostgresStateStore(store.database)
    _ask(store, state, "La blessure au réveil et à la marche ?", "q")
    for i, text in enumerate(("172 pas/min", "oui", "la blessure, je ne sais pas", "merci")):
        state.claim_inbound(str(i), "4242", "text", text)
        state.record_outbound("4242", "Je te lis.", source_key=f"r:{i}")
        assert store.question_rows()[0]["status"] == "open"


def test_partial_review_survives_a_new_session_and_a_topic_change(store):
    state = PostgresStateStore(store.database)
    question = "Quel était ton RPE et ton pic de douleur ?"
    _ask(store, state, question, "q")
    state.claim_inbound("partial", "4242", "text", "RPE 3")
    _review_question(store, question, "partial", "open", note="RPE reçu ; douleur inconnue.")
    state.clear_session("4242")
    state.claim_inbound("topic", "4242", "text", "Sinon, quelle chaussure demain ?")
    state.record_outbound("4242", "Je regarde la chaussure.", source_key="r")
    memory = PostgresStateStore(store.database).relationship_context("4242")
    assert "RPE reçu ; douleur inconnue." in memory
    assert "message_id=" in memory and "Questions restées ouvertes" in memory


def test_defer_resume_and_refuse_are_distinct_from_answering(store):
    state = PostgresStateStore(store.database)
    question = "Comment as-tu vécu cette sortie ?"
    _ask(store, state, question, "q")
    state.claim_inbound("later", "4242", "text", "On en reparle lundi")
    _review_question(store, question, "later", "deferred",
                     follow_up_on=dt.date(2026, 9, 7), note="Reprise convenue lundi.")
    assert store.question_rows()[0]["status"] == "deferred"
    assert "2026-09-07" in state.relationship_context("4242")
    state.claim_inbound("resume", "4242", "text", "Pour revenir à ma sortie, j'étais déçu")
    _review_question(store, question, "resume", "open", note="Déception exprimée, à écouter.")
    assert store.question_rows()[0]["follow_up_on"] is None
    state.claim_inbound("stop", "4242", "text", "Je préfère arrêter ce sujet")
    _review_question(store, question, "stop", "expired", note="Ne plus relancer ce sujet.")
    assert store.question_rows() == []


def test_review_rejects_another_persons_answer_and_rolls_back_a_failed_turn(store):
    from redline.model import commands
    state = PostgresStateStore(store.database)
    question = "La blessure au réveil ?"
    _ask(store, state, question, "q")
    state.claim_inbound("foreign", "9999", "text", "aucune gêne")
    with pytest.raises(ValueError, match="appartenir"):
        _review_question(store, question, "foreign")
    state.claim_inbound("own", "4242", "text", "aucune gêne")
    payload = _review_question(store, question, "own", "open")
    turn_id = str(uuid.uuid4())
    store.stage_command(turn_id, "review", commands.QUESTION_REVIEW,
                        {**payload, "status": "answered"})
    assert store.question_rows()[0]["status"] == "open"
    store.discard_turn(turn_id)
    assert store.question_rows()[0]["status"] == "open"


@pytest.mark.parametrize("delivered", ["Merci pour ton retour.", "Quel était ton RPE ?"])
def test_only_the_exact_delivered_question_marks_its_declared_cell(store, delivered):
    from redline.model import commands
    state = PostgresStateStore(store.database)
    turn_id = str(uuid.uuid4())
    store.stage_command(turn_id, "ask:rpe", commands.QUESTION_ASK,
                        {"text": "Quel était ton RPE ?", "cells": ["2026-09-05:easy:RPE"]})
    store.apply_turn_with_receipts(turn_id)[0]
    state.record_outbound("4242", delivered, source_key="out", turn_id=turn_id)
    assert state.proactive.marked("asked:2026-09-05:easy:RPE") == ("RPE" in delivered)
    assert not state.proactive.marked("asked:2026-09-05:easy:Dlr")
    assert not state.proactive.marked("asked:2026-09-05:easy:Plaisir")
    state.record_outbound("4242", delivered, source_key="out", turn_id=turn_id)
    assert len(store.question_rows()) == (1 if "RPE" in delivered else 0)


def test_a_question_is_tracked_only_from_a_declaration_found_in_the_delivered_text(store):
    """No prose "?" becomes an agenda; neither does a declaration missing from the
    delivered text: no open question, no cell marked as asked."""
    from redline.model import commands
    state = PostgresStateStore(store.database)
    state.record_outbound("4242", "Belle sortie, non ? Tu vois où je veux en venir ?",
                          source_key="out:rhetoric")
    turn_id = str(uuid.uuid4())
    store.stage_command(turn_id, "ask:rpe", commands.QUESTION_ASK,
                        {"text": "Quel était ton RPE ?", "cells": ["2026-09-05:easy:RPE"]})
    store.apply_turn_with_receipts(turn_id)[0]
    state.record_outbound("4242", "Tu l'as ressenti à combien sur dix ?",
                          source_key="out:rephrase", turn_id=turn_id)
    assert store.question_rows() == []
    assert not state.proactive.marked("asked:2026-09-05:easy:RPE")


def test_reasking_an_open_question_does_not_erase_a_partial_answer_or_deferral(store):
    state = PostgresStateStore(store.database)
    question = "Quel était ton RPE et ton pic de douleur ?"
    _ask(store, state, question, "ask:first")
    state.claim_inbound("partial", "4242", "text", "RPE 3")
    _review_question(store, question, "partial", "deferred",
                     note="RPE 3 connu ; douleur inconnue.",
                     follow_up_on=dt.date(2026, 9, 26))
    _ask(store, state, question, "ask:repeated")
    rows = store.question_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "deferred"
    assert rows[0]["review_note"] == "RPE 3 connu ; douleur inconnue."
    assert rows[0]["follow_up_on"] == dt.date(2026, 9, 26)


def test_a_question_asked_again_after_its_answer_is_a_new_open_question(store):
    """A daily question ("How do you feel this morning?") is asked again every day:
    yesterday's answer does not make today's question "already handled"."""
    state = PostgresStateStore(store.database)
    question = "Comment tu te sens ce matin ?"
    _ask(store, state, question, "ask:first")
    state.claim_inbound("answer", "4242", "text", "Bien reposé")
    _review_question(store, question, "answer", note="Bien reposé, le 24.")

    _ask(store, state, question, "ask:next-day")

    rows = store.question_rows()
    assert [(row["question"], row["status"]) for row in rows] == [(question, "open")]
    memory = state.relationship_context("4242")
    assert "Questions déjà traitées" in memory and "Bien reposé, le 24." in memory
    assert "Questions restées ouvertes" in memory


def test_open_questions_left_unexamined_expire_after_two_weeks(store):
    state = PostgresStateStore(store.database)
    _ask(store, state, "Tu as pu tester la nouvelle paire ?", "ask:old")
    with store.database.transaction() as connection:
        connection.execute(conversation_questions.update().values(
            asked_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=15)))
    _ask(store, state, "Et le sommeil cette semaine ?", "ask:recent")

    assert state.expire_stale_questions("4242") == 1

    assert [row["question"] for row in store.question_rows()] == [
        "Et le sommeil cette semaine ?"]


def test_conversation_search_returns_dated_message_and_neighbours_for_same_psid(store):
    state = PostgresStateStore(store.database)
    assert state.claim_inbound("m1", "4242", "text", "La descente hier était facile")
    state.record_outbound("4242", "Et la blessure après ?", source_key="out:1")
    assert state.claim_inbound("m2", "4242", "text", "Talon calme au réveil")
    assert state.claim_inbound("foreign", "9999", "text", "Ma descente était difficile")
    results = store.conversation_search("4242", "descente", limit=4)
    assert len(results) == 1
    assert results[0]["hit"]["role"] == "athlete"
    assert results[0]["hit"]["body"] == "La descente hier était facile"
    assert results[0]["after"]["body"] == "Et la blessure après ?"


def test_conversation_search_uses_registered_turn_scope(store):
    state = PostgresStateStore(store.database)
    assert state.claim_inbound("mine", "4242", "text", "Mon talon va mieux")
    assert state.claim_inbound("other", "9999", "text", "Mon talon reste sensible")
    turn_id = str(uuid.uuid4())
    state.register_turn(turn_id, "4242")

    results = store.conversation_search_for_turn(turn_id, "talon")
    assert [row["hit"]["body"] for row in results] == ["Mon talon va mieux"]
    with pytest.raises(ValueError, match="absent ou expiré"):
        store.conversation_search_for_turn(str(uuid.uuid4()), "genou")


def test_one_transaction_rolls_back_fact_audit_and_projection_request(store):
    broken = observation(strava=None)
    broken["measurements"]["rpe"] = {"state": "known", "value": -1}
    with pytest.raises(StorageError):
        store.add_observation(broken)
    assert store.counts()["observations"] == 0
    with store.database.engine.connect() as connection:
        assert connection.execute(sa.text("select count(*) from redline.audit_events")).scalar_one() == 0
        assert connection.execute(sa.text(
            "select count(*) from redline.projection_states")).scalar_one() == 0


def test_projection_cannot_acknowledge_a_concurrent_write(store):
    store.add_observation(observation(strava=None))
    first_token = store.projection_token()
    assert first_token is not None

    store.put_daily({"day": dt.date(2026, 8, 12), "note": "écriture concurrente"})
    second_token = store.projection_token()
    assert second_token is not None and second_token != first_token

    assert store.mark_projected(first_token) is False
    assert store.projection_pending()
    assert store.mark_projected(second_token) is True
    assert not store.projection_pending()


def test_database_constraints_reject_invalid_typed_domains(store):
    with pytest.raises(StorageError):
        store.evaluate_capability({
            "capability_number": 1, "slug": "test", "need": "test",
            "score": 6, "scale": 5, "status": "green",
            "evaluated_on": dt.date(2026, 8, 13)})
    assert store.latest_capabilities() == []


def _capability_cli(*extra: str, turn_id: str = "") -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": URL}
    env.pop("REDLINE_TURN_ID", None)
    if turn_id:
        env["REDLINE_TURN_ID"] = turn_id
    return subprocess.run([
        *cli_command("fact-write"), "capability", "--capability-number", "5",
        "--slug", "cap5-genou", "--need", "Genou intact", "--score", "3", "--scale", "5",
        "--status", "amber", "--evaluated-on", "2026-10-11", *extra,
    ], cwd=ENGINE_ROOT, env=env, capture_output=True, text=True, check=False)


def test_same_capability_same_day_is_refused_not_announced(store):
    """A corrected `--next-test` for the same day must not be silently ignored by the daily
    constraint while the CLI answers "enregistré"."""
    first = _capability_cli("--next-test", "W42")
    assert first.returncode == 0, first.stderr
    assert "capability:5:2026-10-11 enregistré." in first.stdout

    replay = _capability_cli("--next-test", "W42")
    assert replay.returncode == 0, replay.stderr
    assert "déjà enregistré, rien d'écrit" in replay.stdout

    changed = _capability_cli("--next-test", "W44")
    assert changed.returncode != 0
    assert "une évaluation existe déjà pour la capacité 5 au 2026-10-11" in changed.stderr
    assert "next_test" in changed.stderr and "enregistré." not in changed.stdout

    staged = _capability_cli("--next-test", "W44",
                             turn_id="0b7f2d4e-9a51-4c3e-8f6d-2e1a7c9b5d30")
    assert staged.returncode != 0
    assert "préparé pour le tour" not in staged.stdout
    assert "une évaluation existe déjà" in staged.stderr

    [latest] = store.latest_capabilities()
    assert latest["next_test"] == "W42"
    with store.database.engine.connect() as connection:
        assert connection.execute(sa.text(
            "select count(*) from redline.turn_commands")).scalar_one() == 0


def test_turn_commands_are_invisible_until_applied_and_idempotent(store):
    turn_id = "4d476cd1-cb8b-46c0-b9ee-e0a9b721a387"
    store.stage_command(turn_id, "observation:turn-one", "observation.add",
                        {**observation(strava=None), "idempotency_key": "turn-one"})
    assert store.counts()["observations"] == 0
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.counts()["observations"] == 1
    assert store.apply_turn_with_receipts(turn_id)[0] == 0


def test_the_common_cli_stages_directly_in_postgres_then_applies_atomically(store):
    turn_id = "14c37ce1-172d-4b98-b658-f58918efb47b"
    # `sys.executable`, never `"python3"`: on a developer machine the bare `python3` may be
    # the system one, without SQLAlchemy.
    process = subprocess.run([
        *cli_command("fact-write"), "thread", "--key", "cli-commun",
        "--opened-on", "2026-08-13", "--kind", "open_loop", "--status", "open",
        "--body", "même commande pour Claude et Codex",
    ], cwd=ENGINE_ROOT, env={**os.environ, "DATABASE_URL": URL,
                            "REDLINE_TURN_ID": turn_id},
       capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stderr
    assert store.thread_rows() == []
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.thread_rows()[0]["body"] == "même commande pour Claude et Codex"


def test_the_cli_patch_keeps_the_long_text_it_was_not_given(store):
    """The guarantee that matters: a `--patch` cannot truncate what it ignores.

    `sys.executable` and not `python3`: the bare interpreter may be older than the
    version the scripts require.
    """
    long_item = "texte de 1400 caractères " * 56
    common = [*cli_command("fact-write"), "deadline", "--key", "cli-patch"]
    environment = {**os.environ, "DATABASE_URL": URL}
    created = subprocess.run([
        *common, "--due-on", "2026-08-11", "--item", long_item,
        "--fact-owner", "`coaching/strength.md`", "--blocks", "dose W34",
        "--status", "todo",
    ], cwd=ENGINE_ROOT, env=environment, capture_output=True, text=True, check=False)
    assert created.returncode == 0, created.stderr

    patched = subprocess.run([*common, "--patch", "--due-on", "2026-08-23"],
                             cwd=ENGINE_ROOT, env=environment,
                             capture_output=True, text=True, check=False)
    assert patched.returncode == 0, patched.stderr

    rows = [row for row in store.deadline_rows() if row["deadline_key"] == "cli-patch"]
    assert len(rows) == 1, "un patch ne doit jamais dédoubler la ligne"
    assert rows[0]["due_on"] == dt.date(2026, 8, 23)
    assert rows[0]["item"] == long_item
    assert rows[0]["blocks"] == "dose W34"


def test_the_cli_refuses_to_patch_a_key_that_does_not_exist(store):
    refused = subprocess.run([
        *cli_command("fact-write"), "deadline", "--key", "jamais-vue",
        "--patch", "--status", "done",
    ], cwd=ENGINE_ROOT, env={**os.environ, "DATABASE_URL": URL},
       capture_output=True, text=True, check=False)
    assert refused.returncode != 0
    assert "clé inexistante" in refused.stderr
    assert store.deadline_rows() == []


def test_an_unknown_turn_command_is_refused(store):
    turn_id = "01d5be59-401b-4268-a418-e24d56029fbe"
    with pytest.raises(StorageError, match="PostgreSQL refusée"):
        store.stage_command(turn_id, "unknown", "unknown.command", {})


def test_retired_turn_kind_is_historical_only(store):
    with pytest.raises(StorageError, match="PostgreSQL refusée"):
        store.stage_command(
            "01d5be59-401b-4268-a418-e24d56029fbe", "retired",
            "proactive_message.add", {})

    with store.database.transaction() as connection:
        connection.execute(turn_commands.insert().values(
            id=uuid.uuid4(), turn_id=uuid.uuid4(), command_key="historical:retired",
            kind="proactive_message.add", payload={}, applied_at=dt.datetime.now(dt.UTC),
        ))


def test_a_broken_staged_command_rolls_back_the_whole_turn(store):
    turn_id = "7ed12929-362f-46bc-998d-3725390b2230"
    payload = {**observation(strava=None), "idempotency_key": "turn-broken"}
    store.stage_command(turn_id, "observation:turn-broken", "observation.add", payload)
    with pytest.raises(StorageError):
        store.stage_command(
            turn_id, "measurement:turn-broken:rpe", "measurement.complete", {
                "idempotency_key": "turn-broken", "metric": "rpe", "value": -1,
                "unavailable": False,
            })
    # The faulty command does not enter the turn; the valid fact before it stays
    # applicable instead of being destroyed with it after the reply.
    assert store.counts()["turn_commands"] == 1
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.counts()["observations"] == 1


def test_a_bad_gate_resolution_is_refused_before_it_can_cancel_same_turn_facts(store):
    """Reproduction: a correct fact, then a gate removal without proof."""
    from test_weekly_plan import revision as build_revision

    first = build_revision(
        1, sessions=[], computed_at="2026-08-09T19:10:00+02:00",
        gates=[{"id": "w33-genou", "due": "2026-08-14", "when": "avant",
                "question": "Comment est la blessure au réveil ce matin ?",
                "on_fail": "appliquer le repli prévu"}])
    store.append_plan_revision("2026-W33", first)
    turn_id = "d740e54c-1933-4cbc-ae7f-982a908626db"
    store.stage_command(turn_id, "observation:reveil", "observation.add", {
        "occurred_on": "2026-08-14", "kind": "wake-trace",
        "idempotency_key": "messenger:reveil", "note": "talon plus calme",
        "pains": {"genou": {"state": "unavailable"}},
    })
    second = build_revision(
        2, sessions=[], computed_at="2026-08-14T09:10:00+02:00", gates=[])
    with pytest.raises(StorageError, match="retiré sans résolution typée"):
        store.stage_command(turn_id, "plan:2026-W33:r2", "plan.revise", {
            "week": "2026-W33", "revision": second})

    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.observation_id("messenger:reveil") is not None
    assert store.load_plan("2026-W33")["revisions"][-1]["revision"] == 1


def test_a_missing_plan_no_longer_swallows_the_facts_of_the_turn(store):
    """Facts of a turn are kept even when the week has no program.

    A session stays true without a program. A missing program is a PRESCRIBABILITY
    defect, reported by `VaultRepo.validate_weekly_plan`: it withholds the prescription,
    and it is the only case that still silences the coach."""
    turn_id = "69e645ed-bde6-403a-900f-020362da6fc8"
    store.stage_command(turn_id, "observation:without-plan", "observation.add", {
        **observation(strava=None), "idempotency_key": "without-plan"})
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.counts()["observations"] == 1
    assert store.load_plan("2026-W33") is None


def test_strength_quota_refuses_without_consuming_an_extra_claim(store):
    payload = {"session_key": "sid-1", "performed_on": dt.date(2026, 8, 20),
               "workout": "a", "pains": {"genou": {"before": 1, "after": 2}},
               "duration_minutes": 31, "note": "soléaire chargé", "exercises": [],
               "message_id": "strength:sid-1", "psid": "4242", "message": "terminée"}
    assert store.accept_strength_report(payload, 1) == "accepted"
    assert store.accept_strength_report(payload, 1) == "duplicate"
    assert store.accept_strength_report({
        **payload, "session_key": "sid-2", "message_id": "strength:sid-2"}, 1) == "quota"
    with store.database.engine.connect() as connection:
        claims = connection.execute(sa.text(
            "select session_key from redline.strength_report_claims order by session_key"
        )).scalars().all()
        session = connection.execute(sa.text(
            "select (select value from redline.strength_pain_readings p "
            "        where p.strength_session_id = s.id and p.moment = 'before'), "
            "       (select value from redline.strength_pain_readings p "
            "        where p.strength_session_id = s.id and p.moment = 'after'), "
            "       duration_minutes, note "
            "from redline.strength_sessions s where session_key = 'sid-1'"
        )).one()
        inbound = connection.execute(sa.text(
            "select message_id, status, body from redline.inbound_messages "
            "where message_id = 'strength:sid-1'"
        )).one()
        activity = connection.execute(sa.text(
            "select last_inbound_at from redline.inbound_activity_states where psid = '4242'"
        )).scalar_one()
    assert claims == ["sid-1"]
    assert session == (1, 2, 31, "soléaire chargé")
    assert inbound == ("strength:sid-1", "pending", "terminée")
    assert activity is not None


def test_less_frequent_operational_facts_share_the_staged_boundary(store):
    turn_id = "63101651-859c-4295-a86b-17f19812c747"
    commands = [
        ("daily:test", "daily.upsert", {"day": dt.date(2026, 8, 13),
                                        "sleep": "good", "note": "ok"}),
        ("thread:test", "thread.upsert", {
            "thread_key": "injury-jeudi", "opened_on": dt.date(2026, 8, 13),
            "kind": "feeling", "body": "à revoir", "status": "open"}),
        ("deadline:test", "deadline.upsert", {
            "deadline_key": "controle-w34", "due_on": dt.date(2026, 8, 17),
            "item": "contrôle", "fact_owner": "protocols", "status": "todo"}),
    ]
    for key, kind, payload in commands:
        store.stage_command(turn_id, key, kind, payload)
    assert store.counts()["daily_checkins"] == store.counts()["conversation_threads"] == 0
    assert store.apply_turn_with_receipts(turn_id)[0] == 3
    counts = store.counts()
    assert counts["daily_checkins"] == counts["conversation_threads"] == counts["deadlines"] == 1


def test_deadlines_with_the_same_date_have_a_deterministic_projection_order(store):
    for key in ("z-last", "a-first"):
        store.upsert_deadline({
            "deadline_key": key, "due_on": dt.date(2026, 8, 17),
            "item": key, "fact_owner": "test", "status": "todo",
        })
    assert [row["deadline_key"] for row in store.deadline_rows()] == ["a-first", "z-last"]


def test_strava_gear_sync_owns_distance_but_preserves_the_curated_role(store):
    store.sync_strava_gear({
        "gear_id": "g-cat", "name": "Trail Shoe 3", "distance_km": 98.2,
        "primary": False, "retired": False,
    })
    first = store.gear_rows()[0]
    assert float(first["distance_km"]) == 98.2
    assert first["role"] == "à qualifier"

    store.upsert_gear({
        "gear_id": "g-cat", "name": "Trail Shoe 3",
        "role": "trail et course", "retire_at_km": 650, "status": "active",
    })
    store.sync_strava_gear({
        "gear_id": "g-cat", "name": "Trail Shoe 3", "distance_km": 121.7,
        "primary": True, "retired": False,
    })
    refreshed = store.gear_rows()[0]
    assert float(refreshed["distance_km"]) == 121.7
    assert refreshed["role"] == "trail et course"
    assert float(refreshed["retire_at_km"]) == 650
    assert refreshed["primary"] is True


def test_repeated_daily_shoes_do_not_open_rotation_check(store):
    for gear_id, name in (("g-mach", "Mach 7"), ("g-evo", "EVO SL")):
        store.sync_strava_gear({
            "gear_id": gear_id, "name": name, "distance_km": 100,
            "primary": gear_id == "g-mach", "retired": False,
        })
    for index, day in enumerate((20, 21, 22), start=1):
        store.ingest_strava_activity(Activity(
            id=9900 + index, sport="Run", start_local=f"2026-08-{day:02d}T08:00:00",
            name="easy", distance_m=7000.0, gear_id="g-mach"))
    state = PostgresStateStore(store.database)
    now = dt.datetime(2026, 8, 23, 12, tzinfo=dt.UTC)
    state.materialize_intelligence(now)
    state.materialize_intelligence(now)
    jobs = [row for row in state.proactive.jobs()
            if row["id"].startswith("gear-rotation:g-mach:2026-W34")]
    assert jobs == []


def test_race_deadline_materializes_the_complete_surviving_roadmap(store):
    store.upsert_deadline({
        "deadline_key": "race-test", "due_on": dt.date(2026, 11, 26),
        "item": "Course test", "fact_owner": "coach", "status": "scheduled",
        "kind": "race", "context": {"distance_km": 45},
    })
    state = PostgresStateStore(store.database)
    now = dt.datetime(2026, 8, 23, 12, tzinfo=dt.UTC)
    assert state.materialize_intelligence(now) >= 1
    jobs = [row for row in state.proactive.jobs(active_only=True)
            if row["id"].startswith("race:")]
    assert len(jobs) == 11
    assert {row["kind"] for row in jobs} == {"race_countdown", "premortem"}
    assert any(row["due"] <= now.timestamp() < row["expire_at"] for row in jobs)


def test_moving_a_race_date_cancels_the_old_roadmap(store):
    base = {
        "deadline_key": "race-moved", "due_on": dt.date(2026, 11, 26),
        "item": "Course déplacée", "fact_owner": "coach", "status": "scheduled",
        "kind": "race", "context": {},
    }
    store.upsert_deadline(base)
    state = PostgresStateStore(store.database)
    now = dt.datetime(2026, 8, 23, 12, tzinfo=dt.UTC)
    state.materialize_intelligence(now)
    store.upsert_deadline({**base, "due_on": dt.date(2026, 12, 5)})
    state.materialize_intelligence(now)
    jobs = [row for row in state.proactive.jobs()
            if row["id"].startswith("race:")]
    old = [row for row in jobs if ":2026-11-26:" in row["id"]]
    new = [row for row in jobs if ":2026-12-05:" in row["id"]]
    assert old and all(row["status"] == "done" for row in old)
    assert new and any(row["status"] == "pending" for row in new)


def test_attachment_transport_payload_is_wiped_but_relation_label_remains(store):
    state = PostgresStateStore(store.database)
    raw = encode_payload([{"type": "file", "payload": {
        "url": "https://lookaside.fbsbx.com/private.pdf",
        "name": "bilan.pdf"}}])
    assert state.claim_inbound("mid-file", "4242", "attachment", raw)
    state.complete_inbound(("mid-file",))
    with store.database.engine.connect() as connection:
        body = connection.execute(sa.text(
            "SELECT body FROM redline.inbound_messages WHERE message_id='mid-file'"
        )).scalar_one()
        relation = connection.execute(sa.text(
            "SELECT body FROM redline.conversation_messages "
            "WHERE source_key='inbound:mid-file'"
        )).scalar_one()
    assert body == ""
    assert relation == "[Pièce jointe : document]"
    assert "private" not in relation and "bilan.pdf" not in relation


def test_proactive_messages_are_typed_append_only_and_idempotent(store):
    row = {
        "source_key": "test:morning:2026-08-13", "sent_at": dt.datetime(
            2026, 8, 13, 8, 0, tzinfo=dt.UTC),
        "kind": "morning", "angle": None, "summary": "Question sommeil posée.",
    }
    first = store.add_proactive_message(row)
    assert store.add_proactive_message(row) == first
    assert store.proactive_message_rows()[0]["summary"] == "Question sommeil posée."
    with pytest.raises(StorageError), store.database.transaction() as connection:
        connection.execute(sa.text(
            "UPDATE redline.service.proactive_messages SET summary = 'réécrit'"))
    with pytest.raises(StorageError):
        store.add_proactive_message({**row, "summary": "contenu contradictoire"})


def test_a_false_correction_is_revised_under_the_same_key_and_reannounced(store):
    """A correction is not an immutable fact: an erroneous correction already delivered
    can be wrong again. The same occurrence must have a single memory, revisable and
    announceable again.
    """
    key = "2026-08-27-ef-intermittente"
    store.register_correction({"correction_key": key,
                               "summary": "Le repos devait être maintenu."})
    state = PostgresStateStore(store.database)
    first_due = dt.datetime.now(dt.UTC)
    state.materialize_intelligence(first_due)
    first = state.proactive.claim_due_job(first_due.timestamp() + 1)
    assert first and first["id"] == f"correction:{key}"
    state.proactive.finish_delivery(first["id"], first_due + dt.timedelta(seconds=2))

    store.register_correction({
        "correction_key": key,
        "summary": "Le pic intermittent ne suffisait pas à retirer l'EF conditionnelle.",
    })

    rows = store.correction_rows()
    assert len(rows) == 1
    assert rows[0]["summary"].startswith("Le pic intermittent")
    assert rows[0]["delivered_at"] is None
    second = state.proactive.claim_due_job(dt.datetime.now(dt.UTC).timestamp() + 1)
    assert second and second["id"].startswith("correction-id:")
    assert second["id"] != first["id"]
    assert second["detail"].startswith("Le pic intermittent")
    state.proactive.finish_delivery(second["id"], dt.datetime.now(dt.UTC))
    assert store.correction_rows()[0]["delivered_at"] is not None


def test_a_correction_not_announced_within_its_week_expires(store):
    # A stale correction must not be delivered long after the error.
    store.register_correction({"correction_key": "ancienne", "summary": "Erreur ancienne."})
    store.register_correction({"correction_key": "recente", "summary": "Erreur récente."})
    with store.database.transaction() as connection:
        connection.execute(sa.text(
            "UPDATE redline.correction_deliveries SET registered_at = now() - interval '10 days'"
            " WHERE correction_key = 'ancienne'"))
        # An announcement already queued before the rule, with no expiry.
        connection.execute(sa.text(
            "INSERT INTO redline.proactive_jobs (job_key, kind, detail, due_at, priority) "
            "VALUES ('correction:ancienne', 'correction', 'Erreur ancienne.', now(), true)"))
    state = PostgresStateStore(store.database)
    now = dt.datetime.now(dt.UTC)
    state.materialize_intelligence(now)
    claimed = []
    while job := state.proactive.claim_due_job(now.timestamp() + 1):
        claimed.append(job["id"])
        state.proactive.finish_delivery(job["id"], now)
    assert "correction:recente" in claimed
    assert "correction:ancienne" not in claimed


def test_rewriting_the_same_correction_verbatim_does_not_reannounce_it(store):
    row = {"correction_key": "stable", "summary": "Résumé juste."}
    store.register_correction(row)
    with store.database.transaction() as connection:
        connection.execute(sa.text(
            "UPDATE redline.correction_deliveries SET delivered_at = now() "
            "WHERE correction_key = 'stable'"))

    store.register_correction(row)

    assert store.correction_rows()[0]["delivered_at"] is not None


def test_a_recurring_correction_keeps_its_count_of_recurrences(store):
    # A recurrence replaces the summary: without this count, the row would no longer say
    # that the error came back.
    store.register_correction({"correction_key": "wake-trace", "summary": "06/09 : oui franc exigé."})
    store.register_correction({"correction_key": "wake-trace", "summary": "Récidive le 01/10."})
    store.register_correction({"correction_key": "wake-trace", "summary": "Récidive le 08/10."})
    store.register_correction({"correction_key": "autre", "summary": "Une seule fois."})
    rows = {row["correction_key"]: row for row in store.correction_rows()}
    assert rows["wake-trace"]["revisions"] == 2
    assert rows["wake-trace"]["summary"] == "Récidive le 08/10."
    assert rows["autre"]["revisions"] == 0
    assert rows["wake-trace"]["first_registered_at"] is not None


def test_plan_watermark_includes_facts_staged_earlier_in_the_same_turn(store):
    # The revision must be VALID: `apply_turn_with_receipts` reads back what it just wrote,
    # and an invalid program is refused at write time.
    turn_id = "f65cf6e3-797e-4af4-a999-bff20dad09cb"
    store.stage_command(turn_id, "observation:same-turn", "observation.add", {
        **observation(strava=None), "idempotency_key": "same-turn"})
    store.stage_command(turn_id, "plan:2026-W33:r1", "plan.revise", {
        "week": "2026-W33",
        "revision": _plan_revision(1, reason="faits du même tour"),
    })
    assert store.apply_turn_with_receipts(turn_id)[0] == 2
    assert store.plan_needs_arbitration("2026-W33", 1) is False


def _plan_revision(number: int, **overrides) -> dict:
    """A VALID revision of 2026-W33.

    Reuses the factories of `test_weekly_plan` rather than an approximate one: the schema
    requires at least one session and an `evidence`, and refuses a session dated outside
    its week. An invalid revision would fail validation BEFORE reaching the staleness
    check, and the tests would pass while verifying nothing of what they claim.
    """
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session
    base = build_revision(
        number,
        sessions=[session("w33-ven-ef", date="2026-08-14", workout="easy")],
        computed_at="2026-08-14T09:00:00+02:00",
    )
    return {**base, **overrides}


def test_a_fact_is_never_rolled_back_because_a_derived_plan_went_stale(store):
    """A session is ground truth; the weekly program is a computation derived from it.

    The store must not cancel the fact when the computation becomes stale. The fact is
    written, and staleness becomes a prescribability verdict that
    `VaultRepo.validate_weekly_plan` renders separately."""
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")

    turn_id = "3f0d9d3a-6a0f-4a2f-9b0f-6a1f2c3d4e5f"
    store.stage_command(turn_id, "observation:sortie", "observation.add", {
        **observation(strava=None), "idempotency_key": "sortie-du-matin"})

    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.counts()["observations"] == 1
    # The plan is indeed stale -- but that is a read, not a write error.
    assert store.plan_needs_arbitration("2026-W33", 1) is True


def test_completing_a_declarative_cell_never_makes_the_plan_stale(store):
    """The fingerprint must not hash the VALUE of measurements: declaring an RPE would
    otherwise stale the plan like a session coming from nowhere. A declared number calls
    for a coach judgment, not a program revision."""
    oid = store.add_observation({**observation(strava=None),
                                 "idempotency_key": "sortie-a-completer"})
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")
    store.complete_measurement(oid, "rpe", 7)
    assert store.plan_needs_arbitration("2026-W33", 1) is False


def test_the_daily_state_never_makes_the_plan_stale(store):
    """Same reason: saying one slept badly feeds the gauge, it does not make the program
    wrong."""
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")
    store.put_daily({"day": dt.date(2026, 8, 14), "sleep": "poor",
                     "life_load": "high", "desire": "normal"})
    assert store.plan_needs_arbitration("2026-W33", 1) is False


def test_a_new_session_still_makes_the_plan_stale(store):
    """The check still protects what matters: "does the plan know everything that took
    place?" -- one more session makes it stale."""
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")
    store.add_observation({**observation(strava=None), "idempotency_key": "nouvelle"})
    assert store.plan_needs_arbitration("2026-W33", 1) is True


def test_arbitration_uses_the_persistence_watermark(store):
    """An observation present before the revision was already seen by the arbitration.
    Only an effort observation entered afterwards and still without a cycle must close
    the prescription.
    """
    store.add_observation({**observation(strava=None),
                           "idempotency_key": "connue-avant-revision"})
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")

    assert store.plan_needs_arbitration("2026-W33", 1) is False

    store.add_observation({**observation(strava=None),
                           "idempotency_key": "surprise-apres-revision"})

    assert store.plan_needs_arbitration("2026-W33", 1) is True


def test_a_planned_activity_linked_after_the_revision_needs_no_arbitration(store):
    """Normal execution advances the cycle, not the plan's intent."""
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")
    store.add_observation({
        **observation(strava=90000000017),
        "occurred_on": dt.date(2026, 8, 14),
        "started_at": dt.datetime(2026, 8, 14, 8, 3, tzinfo=dt.UTC),
        "idempotency_key": "strava:90000000017",
    })

    assert store.plan_needs_arbitration("2026-W33", 1) is False


def test_an_invalid_revision_is_still_refused_at_write_time(store):
    """Facts are unconditional, plans are NOT: writing an invalid program remains a write
    fault, reported before the coach's reply."""
    turn_id = "5b2f9d4c-8c2e-4d3b-9e3f-8c3f4d5e6f71"
    with pytest.raises(StorageError, match="invalide"):
        store.stage_command(turn_id, "plan:2026-W33:r1", "plan.revise", {
            "week": "2026-W33",
            "revision": {
                "revision": 1, "computed_at": "2026-08-14T09:00:00+02:00",
                "reason": "révision vide", "sessions": [], "gates": [],
            },
        })
    assert store.counts()["turn_commands"] == 0


def test_logging_a_session_and_revising_in_the_same_turn_is_accepted(store):
    """The correct path: the revision sets its watermark AFTER the session was applied, so
    the turn passes. A guard that opens no correct path fabricates a false one."""
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")

    turn_id = "4a1e8c2b-7b1f-4c3a-8d2e-7b2f3c4d5e60"
    store.stage_command(turn_id, "observation:sortie", "observation.add", {
        **observation(strava=None), "idempotency_key": "sortie-du-matin"})
    store.stage_command(turn_id, "plan:2026-W33:r2", "plan.revise", {
        "week": "2026-W33",
        "revision": _plan_revision(2, reason="EF du jour consignée"),
    })

    assert store.apply_turn_with_receipts(turn_id)[0] == 2
    assert store.plan_needs_arbitration("2026-W33", 2) is False


def test_the_angle_a_turn_deposits_is_read_back_at_delivery(store):
    """The angle needs a PLACE to live, otherwise it ends up in the message.

    A proactive message's trace is written AFTER the Messenger delivery, hence well after
    `apply_turn_with_receipts`: the turn command serves as the deposit and `turn_angle`
    reads it back.
    """
    turn_id = str(uuid.uuid4())
    store.stage_command(turn_id, "angle:titre Strava, récit libre", "proactive.angle",
                        {"angle": "titre Strava, récit libre"})

    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.turn_angle(turn_id) == "titre Strava, récit libre"


def test_a_turn_without_an_angle_has_none(store):
    assert store.turn_angle(str(uuid.uuid4())) == ""
    assert store.turn_angle("") == ""


def test_an_empty_angle_is_refused_rather_than_stored(store):
    turn_id = str(uuid.uuid4())
    with pytest.raises(StorageError):
        store.stage_command(turn_id, "angle:vide", "proactive.angle", {"angle": "   "})
    assert store.counts()["turn_commands"] == 0


def test_preview_rolls_back_facts_audit_and_command_but_keeps_pending_work(store):
    turn_id = str(uuid.uuid4())
    payload = {**observation(strava=None), "idempotency_key": "preview-pending"}
    store.stage_command(turn_id, "observation:preview-pending", "observation.add", payload)
    before = store.counts()
    with store.database.engine.connect() as connection:
        audit_before = list(connection.execute(sa.select(audit_events)))
        projection_before = list(connection.execute(sa.select(projection_states)))
    simulated = {**payload, "idempotency_key": "preview-only"}

    assert store.preview_command("observation.add", simulated, turn_id=turn_id) == 2

    assert store.counts() == before
    with store.database.engine.connect() as connection:
        assert list(connection.execute(sa.select(audit_events))) == audit_before
        assert list(connection.execute(sa.select(projection_states))) == projection_before
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.counts()["observations"] == 1


def test_failed_preview_does_not_poison_pending_work(store):
    turn_id = str(uuid.uuid4())
    store.stage_command(turn_id, "observation:preview-kept", "observation.add",
                        {**observation(strava=None), "idempotency_key": "preview-kept"})
    before = store.counts()
    with pytest.raises(StorageError):
        store.preview_command("unknown.command", {}, turn_id=turn_id)
    assert store.counts() == before
    assert store.apply_turn_with_receipts(turn_id)[0] == 1


def test_standalone_preview_cannot_be_applied_later(store):
    before = store.counts()
    assert store.preview_command("observation.add", {
        **observation(strava=None), "idempotency_key": "preview-standalone",
    }) == 1
    assert store.counts() == before


def test_applied_receipts_survive_an_idempotent_replay(store):
    turn_id = str(uuid.uuid4())
    payload = {**observation(strava=None), "idempotency_key": "receipt-confirmed"}
    store.stage_command(turn_id, "observation:receipt-confirmed", "observation.add", payload)
    count, receipts = store.apply_turn_with_receipts(turn_id)
    assert count == 1
    assert len(receipts) == 1
    assert receipts[0]["kind"] == "observation.add"
    assert receipts[0]["payload"]["idempotency_key"] == "receipt-confirmed"
    assert dt.datetime.fromisoformat(receipts[0]["applied_at"]).tzinfo is not None
    assert store.apply_turn_with_receipts(turn_id) == (0, receipts)
    assert store.counts()["observations"] == 1


def test_real_plan_preview_leaves_no_revision_and_revise_still_applies(store, monkeypatch, capsys):
    import io
    import json

    from conftest import load_script
    from test_weekly_plan import frozen_clock, gate, session, written_today

    cli = load_script("week-plan")
    frozen_clock(monkeypatch, cli)
    turn_id = str(uuid.uuid4())
    monkeypatch.setenv("REDLINE_TURN_ID", turn_id)
    fresh = written_today(number=1, sessions=[session()])
    fresh["gates"] = [gate(when="apres")]
    before = store.counts()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(fresh)))
    assert cli._database_command(store, "preview", "2026-W32", False) == 0
    assert store.counts() == before
    assert store.load_plan("2026-W32") is None
    assert store.apply_turn_with_receipts(turn_id)[0] == 0

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(fresh)))
    assert cli._database_command(store, "revise", "2026-W32", False) == 0
    assert store.load_plan("2026-W32") is None
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.load_plan("2026-W32")["revisions"][0]["revision"] == 1


def test_a_revision_that_cancels_a_session_without_a_typed_premise_is_refused(
        store, monkeypatch, capsys):
    """A restriction without a premise can no longer be written."""
    import io
    import json

    from conftest import load_script
    from test_weekly_plan import frozen_clock, session, written_today

    from redline.model.schema import SchemaError

    cli = load_script("week-plan")
    frozen_clock(monkeypatch, cli)
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)
    thursday = session("w32-jeu-ef", date="2026-08-06")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        written_today(number=1, sessions=[thursday]))))
    assert cli._database_command(store, "revise", "2026-W32", False) == 0

    change = {"session_id": "w32-jeu-ef", "fields": ["status"],
              "trigger": "observation:2026-08-04-reveil", "context": "réveil à 3/10",
              "rule": "douleur persistante"}
    cut = written_today(number=2, sessions=[{**thursday, "status": "cancelled"}],
                        evidence=["observation:2026-08-04-reveil"], changes=[change])
    for command in ("preview", "revise"):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(cut)))
        with pytest.raises(SchemaError, match="sans prémisse typée"):
            cli._database_command(store, command, "2026-W32", False)
    assert len(store.load_plan("2026-W32")["revisions"]) == 1

    change["restriction"] = {"rule": "n3", "fact": "observation:2026-08-04-reveil"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(cut)))
    assert cli._database_command(store, "revise", "2026-W32", False) == 0
    stored = store.load_plan("2026-W32")["revisions"][1]["changes"][0]
    assert stored["restriction"] == {"rule": "n3", "fact": "observation:2026-08-04-reveil"}


def test_a_plan_readback_shows_each_run_gap_to_the_month_without_blocking(
        store, monkeypatch, capsys):
    """No fixed percentage: the readback shows each run's gap to the longest run of the
    last 30 days, and the revision passes whatever the gap."""
    import io
    import json

    from conftest import load_script
    from test_weekly_plan import frozen_clock, session, written_today

    store.add_observation({
        "occurred_on": dt.date(2026, 7, 26), "kind": "long", "idempotency_key": "sl-juillet",
        "note": "", "measurements": {"distance_km": {"state": "known", "value": 9.0}}})
    cli = load_script("week-plan")
    frozen_clock(monkeypatch, cli)
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)
    long_run = session("w32-dim-sl", date="2026-08-09", workout="long",
                       distance_target_km=12.0, distance_cap_km=13.5)
    fresh = written_today(number=1, sessions=[long_run])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(fresh)))
    assert cli._database_command(store, "revise", "2026-W32", False) == 0
    out = capsys.readouterr().out
    assert "w32-dim-sl 13.5 km = +50 % de 9.0 km (plus longue des 30 j)" in out
    assert "jamais bloquant" in out
    assert store.load_plan("2026-W32")["revisions"][0]["revision"] == 1


def test_the_weekly_dossier_is_computed_from_the_same_functions_as_the_clis(store):
    from redline.analysis import weekly_dossier

    for day, km in ((dt.date(2026, 9, 13), 11.53), (dt.date(2026, 9, 20), 10.8),
                    (dt.date(2026, 9, 27), 13.6)):
        store.add_observation({
            "occurred_on": day, "kind": "long", "idempotency_key": f"sl-{day}",
            "note": "", "measurements": {
                "distance_km": {"state": "known", "value": km},
                "duration_s": {"state": "known", "value": int(km * 400)}}})
    store.upsert_deadline({"deadline_key": "verdict-cortex", "due_on": dt.date(2026, 9, 6),
                           "item": "confirmer le verdict oral", "fact_owner": "athlete.md",
                           "status": "todo", "blocks": "descente courue"})
    text = weekly_dossier.render(store, VAULT_ROOT, "2026-W39", dt.date(2026, 9, 27))
    assert text.startswith("[DOSSIER DU BILAN") and text.endswith("[/DOSSIER]")
    assert "Semaine 2026-W39" in text and "13.60 km courus (1 sortie)" in text
    # The Sunday review closes the week: its record already counts.
    assert "record de 13.6 km battu en 2026-W39" in text
    assert "en cours" not in text.split("Trajectoire")[1].split("Plus longue des")[0]
    assert "Plus longue des 30 j : 13.6 km" in text
    assert "verdict-cortex (due le 06/09)" in text and "bloque descente courue" in text
    assert "--reason optimistic" in text and "narrative" not in text
    assert "Faisabilité à rebours : aucune course typée" in text
    store.upsert_deadline({"deadline_key": "targetrace", "due_on": dt.date(2026, 11, 26),
                           "kind": "race", "item": "TargetRace",
                           "fact_owner": "athlete/goals.md", "status": "scheduled"})
    text = weekly_dossier.render(store, VAULT_ROOT, "2026-W39", dt.date(2026, 9, 27))
    assert "Faisabilité à rebours (garde-fou n°8, calculée) : course le 26/11" in text


def test_the_weekly_dossier_does_not_flag_cadence_work_that_was_asked_for(store):
    """The review dossier must not flag cadences that were deliberately worked."""
    from redline.analysis import weekly_dossier

    for day, cadence, key in ((dt.date(2026, 9, 14), 160, "cadence:old"),
                              (dt.date(2026, 9, 21), 170, "cadence:worked")):
        store.add_observation({
            "occurred_on": day, "kind": "easy", "idempotency_key": key,
            "note": "EF comparable", "measurements": {
                "distance_km": {"state": "known", "value": 7},
                "cadence_spm": {"state": "known", "value": cadence}}})

    def dossier():
        return weekly_dossier.render(store, VAULT_ROOT, "2026-W39", dt.date(2026, 9, 27))

    assert "cadence en hausse" in dossier()
    store.upsert_exposure({
        "exposure_key": "cadence:worked", "observation_key": "cadence:worked",
        "exposure_type": "cadence", "origin": "prescribed", "status": "confirmed",
        "duration_min": 40, "target_value": 170, "target_unit": "spm",
        "tolerance": "easy", "note": "travail prescrit"})
    assert "cadence en hausse" not in dossier()


def test_a_service_declared_question_is_tracked_only_if_it_was_delivered(store):
    """A closed render has no turn: the service names the question it expects, and it
    exists only if its exact text was sent."""
    state = PostgresStateStore(store.database)
    question = "Tu l’as faite, reportée ou laissée de côté ?"
    state.record_outbound("4242", "Je n’ai pas de retour sur EF.", source_key="proactive:cut",
                          proactive=True, questions=(question,))
    assert store.question_rows() == []
    assert not state.question_known("4242", question)

    body = "Je n’ai pas de retour sur EF. " + question
    state.record_outbound("4242", body, source_key="proactive:full", proactive=True,
                          platform_messages=(("mid-full", body),), questions=(question,))
    rows = store.question_rows()
    assert [(row["question"], row["status"]) for row in rows] == [(question, "open")]
    assert rows[0]["source_platform_message_id"] == "mid-full"
    assert state.question_known("4242", question)
    assert not state.question_known("9999", question)

    # A resend replays the same key: no second message, no second question.
    state.record_outbound("4242", body, source_key="proactive:full", proactive=True,
                          questions=(question,))
    assert len(store.question_rows()) == 1


def test_a_settled_question_blocks_its_template_only_for_the_same_fact(store):
    state = PostgresStateStore(store.database)
    question = "Tu l’as faite, reportée ou laissée de côté ?"
    state.record_outbound("4242", question, source_key="proactive:w39", proactive=True,
                          questions=(question,))
    state.claim_inbound("refus", "4242", "text", "Laisse tomber ce sujet")
    _review_question(store, question, "refus", "expired", note="Ne plus relancer.")
    assert store.question_rows() == []
    assert state.question_known("4242", question)

    # The same template a week later designates another session.
    with store.database.transaction() as connection:
        connection.execute(conversation_questions.update().values(
            resolved_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=7)))
    assert not state.question_known("4242", question)


def test_service_lines_do_not_push_the_real_exchange_out_of_memory(store):
    """Holding sentences must not occupy the eight slots of the memory."""
    state = PostgresStateStore(store.database)
    state.claim_inbound("sujet", "4242", "text", "On reprend la séance de vendredi ?")
    for index in range(9):
        state.record_outbound("4242", f"Je suis encore sur ta réponse {index}.",
                              source_key=f"holding:{index}", resolve_questions=False)
    state.record_outbound("4242", "Un incident m'empêche de répondre.",
                          source_key="failure:m1", resolve_questions=False)
    memory = state.relationship_context("4242")
    assert "On reprend la séance de vendredi ?" in memory
    assert "Je suis encore sur ta réponse" not in memory
    assert "Un incident" not in memory


def test_memory_leaves_threads_to_the_packet_when_asked(store):
    state = PostgresStateStore(store.database)
    state.claim_inbound("m", "4242", "text", "Salut")
    store.upsert_thread({"thread_key": "chaussures", "status": "open",
                         "body": "Essai de la nouvelle paire à reprendre.",
                         "opened_on": dt.date(2026, 9, 20), "kind": "open_loop",
                         "follow_up_on": None})
    assert "chaussures" in state.relationship_context("4242")
    without = state.relationship_context("4242", threads=False)
    assert "chaussures" not in without and "Salut" in without


def test_a_typed_transition_round_trips_and_opens_no_window(store):
    """Guardrail 8 is carried by the database; a transition opens no +3/+4 window."""
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    night = session("w41-jeu-nuit", "2026-10-08", transition="nocturne")
    store.append_plan_revision("2026-W41", build_revision(
        1, sessions=[night], computed_at="2026-10-04T19:00:00+02:00"))
    loaded = store.load_plan("2026-W41")["revisions"][-1]["sessions"][0]
    assert loaded["transition"] == "nocturne"
    assert store.active_plans()["2026-W41"]["sessions"][0]["id"] == "w41-jeu-nuit"
    store.add_observation({
        "occurred_on": dt.date(2026, 10, 8), "kind": "easy",
        "started_at": dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.UTC),
        "idempotency_key": "strava:4141", "strava_activity_id": 4141,
        "note": "", "measurements": {}, "session_key": "2026-W41:w41-jeu-nuit"})
    assert store.active_plans()["2026-W41"]["sessions"][0]["status"] == "done"

    # Weeks later, a new transition can be written: the gate decides, not the calendar.
    descent = session("w44-mar-desc", "2026-10-27", transition="descente")
    store.append_plan_revision("2026-W44", build_revision(
        1, sessions=[descent], computed_at="2026-10-25T19:00:00+02:00"))
    assert store.active_plans()["2026-W44"]["sessions"][0]["transition"] == "descente"


def test_a_staged_revision_with_a_transition_is_applied(store):
    """The turn command carries `date` values: the first typed revision must not crash on them."""
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    turn_id = "0b7d8c1e-3a4f-4e44-9a1e-6f0c2d9b7a11"
    store.stage_command(turn_id, "plan:2026-W41:r1", "plan.revise", {
        "week": "2026-W41", "revision": build_revision(
            1, sessions=[session("w41-jeu-nuit", "2026-10-08", transition="nocturne")],
            computed_at="2026-10-04T19:00:00+02:00")})
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.load_plan("2026-W41")["revisions"][0]["sessions"][0]["transition"] == "nocturne"


# ---- Notebook: real SQL rights, real revocation, real HTTP boundaries.

def test_notebook_archives_survive_outbox_and_are_isolated_by_owner(store):
    from redline.storage.notebook import NotebookStore
    archive = NotebookStore(store.database)
    record = {'slot': 'week.png', 'kind': 'weekly', 'title': 'W39', 'period': '2026-W39',
              'occurred_on': dt.date(2026, 9, 27), 'body': 'Texte exact',
              'image': b'original', 'image_hash': 'original', 'origin': 'recovered'}
    identity = archive.save_many('owner', 'source', [record])[0]
    assert archive.save_many('owner', 'source', [record]) == [identity]
    assert archive.get('owner', identity)['body'] == 'Texte exact'
    assert archive.get('owner', identity, image=True)['image'] == b'original'
    assert archive.get('other', identity) is None
    assert archive.list_entries('other') == []
    hidden = archive.save_many('owner', 'not-sent', [{**record, 'origin':'delivery'}])[0]
    assert archive.get('owner', hidden) is None
    state = PostgresStateStore(store.database)
    state.begin_outbound_delivery('not-sent', 'owner', 'a' * 64, {})
    state.finish_outbound_delivery('not-sent', delivered=True)
    assert archive.get('owner', hidden) is not None


def test_notebook_link_is_one_time_and_device_access_is_revocable(store):
    from redline.storage.notebook import NotebookStore
    archive = NotebookStore(store.database)
    link = archive.issue('owner')
    assert not archive.authenticate('owner', link)
    assert archive.exchange('other', link) is None
    device = archive.exchange('owner', link)
    assert device and archive.authenticate('owner', device)
    assert archive.exchange('owner', link) is None
    assert not archive.authenticate('other', device)
    archive.revoke('owner')
    assert not archive.authenticate('owner', device)


def test_notebook_service_role_can_archive_but_cannot_rewrite(service_role_store):
    from redline.storage.notebook import NotebookStore
    from redline.storage.schema import notebook_entries
    archive = NotebookStore(service_role_store.database)
    identity = archive.save_many('owner', 'acl', [{'slot':'one', 'kind':'other',
        'title':'Archive', 'occurred_on':dt.date(2026,10,2), 'origin':'recovered'}])[0]
    assert archive.get('owner', identity)
    link = archive.issue('owner')
    device = archive.exchange('owner', link)
    assert archive.authenticate('owner', device)
    archive.revoke('owner')
    with pytest.raises(StorageError), service_role_store.database.transaction() as connection:
        connection.execute(notebook_entries.update().values(title='Réécrit'))


def test_notebook_routes_protect_both_metadata_and_images(make_config, store):
    from unittest.mock import MagicMock

    from conftest import RecordingBot

    from redline.service.notebook import NotebookService
    from redline.service.notebook_web import COOKIE
    from redline.service.web import create_app
    from redline.storage.notebook import NotebookStore
    archive = NotebookStore(store.database)
    identity = archive.save_many('4242', 'http', [{'slot':'one', 'kind':'other',
        'title':'Privé', 'body':'Jamais public', 'image': b'PNG', 'image_hash':'png',
        'occurred_on':dt.date(2026,10,2), 'origin':'recovered'}])[0]
    service = NotebookService(archive, MagicMock(), MagicMock(), VAULT_ROOT, 'https://coach.test')
    app = create_app(make_config(), RecordingBot(), MagicMock(), notebook=service)
    client = app.test_client()
    assert b'Jamais public' not in client.get('/carnet').data
    shell = client.get('/carnet', headers={'Accept-Encoding': 'gzip'})
    assert shell.headers['Content-Encoding'] == 'gzip' and shell.headers['Cache-Control'] == 'no-cache'
    assert client.get('/carnet', headers={'Accept-Encoding': 'gzip',
                                          'If-None-Match': shell.headers['ETag']}).status_code == 304
    for path in ('entries', f'entries/{identity}', f'entries/{identity}/image', 'current'):
        assert client.get('/notebook-api/' + path).status_code == 401
    link = archive.issue('4242')
    assert client.post('/notebook-api/connect', json={'token':link}, headers={'Origin':'https://evil.test'}).status_code == 403
    response = client.post('/notebook-api/connect', json={'token':link})
    assert response.status_code == 200
    cookie = response.headers['Set-Cookie']
    assert COOKIE in cookie and 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    assert client.get(f'/notebook-api/entries/{identity}').json['body'] == 'Jamais public'
    image = client.get(f'/notebook-api/entries/{identity}/image')
    assert image.data == b'PNG' and image.headers['Cache-Control'] == 'no-store'
    assert client.post('/notebook-api/logout', json={}).status_code == 200
    assert client.get('/notebook-api/entries').status_code == 401


def test_notebook_replay_preserves_the_card_after_a_text_failure(store, tmp_path):
    from unittest.mock import MagicMock

    from redline.cards import archive as descriptor
    from redline.service.delivery import DeliveryService, EnvelopeBuilder
    from redline.service.messenger import DeliveryResult
    from redline.service.notebook import NotebookService
    from redline.service.outbox import Outbox
    from redline.storage.notebook import NotebookStore
    archive = NotebookStore(store.database)
    state = PostgresStateStore(store.database)
    notebook = NotebookService(archive, MagicMock(), state, VAULT_ROOT, 'https://coach.test')
    outbox = Outbox(tmp_path)
    image = outbox.target('weekly.png')
    image.write_bytes(b'original-png')
    descriptor.write(image, kind='weekly', title='W39', period='2026-W39')
    client = MagicMock()
    client.send_burst.return_value = DeliveryResult.failure('transport indisponible')
    service = DeliveryService(client, outbox, state, 'https://coach.test', notebook)
    envelope = EnvelopeBuilder('4242', 'proactive:weekly:2026-W39').text('Texte exact').image(outbox, image).build()
    assert not service.deliver(envelope).text_ok
    assert archive.list_entries('4242') == []
    assert not image.exists()
    client.send_burst.return_value = DeliveryResult.success()
    client.send_button.return_value = DeliveryResult.success()
    assert service.redeliver(service.stored(envelope.source_key)).text_ok
    entries = archive.list_entries('4242')
    assert len(entries) == 1
    assert archive.get('4242', entries[0]['id'], image=True)['image'] == b'original-png'
    assert archive.get('4242', entries[0]['id'])['body'] == 'Texte exact'
    assert client.send_button.call_args.args[3] == 'Ouvrir mon carnet'


def test_notebook_current_resolves_the_same_typed_workout_and_rechecks_claims(store, monkeypatch):
    import json
    from unittest.mock import MagicMock

    from redline.service import clock
    from redline.service.notebook import NotebookService
    from redline.service.plans import PlanStatus
    from redline.storage.notebook import NotebookStore
    from redline.storage.schema import strength_report_claims
    monkeypatch.setattr(clock, 'today', lambda: dt.date(2026, 10, 2))
    repo = MagicMock()
    repo.validate_weekly_plan.return_value = PlanStatus.VALID
    repo.weekly_plan_context.return_value = json.dumps({'revision': 1, 'sessions': [{
        'id':'a', 'date':'2026-10-02', 'kind':'strength', 'workout':'strength', 'routine': 'a',
        'status':'planned', 'strength':{}}], 'gates':[]})
    state = PostgresStateStore(store.database)
    service = NotebookService(NotebookStore(store.database), repo, state, VAULT_ROOT, 'https://coach.test')
    current = service.current()
    assert current['status'] == 'ready'
    assert current['session']['exercises'][0]['dose']['text']
    url = service.launch('4242', current['signature'])
    assert state.resolve_strength_link('4242', url.split('#t=')[1])['status'] == 'active'
    with store.database.transaction() as connection:
        connection.execute(strength_report_claims.insert().values(session_key=current['session']['sid']))
    assert service.current()['status'] == 'done'
    assert service.launch('4242', current['signature']) is None


def test_notebook_expired_link_cannot_create_a_device(store):
    from redline.storage.notebook import NotebookStore, token_hash
    from redline.storage.schema import notebook_credentials
    archive = NotebookStore(store.database)
    link = archive.issue('owner')
    with store.database.transaction() as connection:
        connection.execute(notebook_credentials.update().where(
            notebook_credentials.c.token_hash == token_hash(link),
        ).values(expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)))
    assert archive.exchange('owner', link) is None


def _wake(store, day, value, *, state="known"):
    measurement = {"state": state, "value": value} if state == "known" else {"state": state}
    return store.add_observation({
        "occurred_on": day, "kind": "wake-trace", "source": "notebook",
        "idempotency_key": f"notebook:wake-trace:{day.isoformat()}",
        "pains": {"genou": measurement}})


def _bound(store, key, day, success_if="<=1", probability=.8):
    store.upsert_prediction({
        "prediction_key": key, "domain": "corps", "statement": "trace au plus légère",
        "probability": probability, "due_on": day + dt.timedelta(days=2),
        "resolves_on": {"kind": "wake-trace", "date": day.isoformat(), "success_if": success_if}})


def test_a_wake_prediction_settles_itself_when_the_notebook_writes_the_trace(store):
    # Bound to the wake-up trace, the prediction settles on entry, without a written verdict.
    day = dt.date(2026, 10, 12)
    _bound(store, "w42-sl-reveil", day)
    _bound(store, "w42-sl-net", day, success_if="=0", probability=.3)
    _bound(store, "w42-autre-jour", day + dt.timedelta(days=1))
    _wake(store, day, 1.0)
    rows = {row["prediction_key"]: row for row in store.prediction_rows()}
    assert (rows["w42-sl-reveil"]["status"], rows["w42-sl-reveil"]["outcome"]) == (
        "resolved", True)
    assert rows["w42-sl-reveil"]["brier_score"] == pytest.approx(.04)
    assert (rows["w42-sl-net"]["status"], rows["w42-sl-net"]["outcome"]) == ("resolved", False)
    assert rows["w42-autre-jour"]["status"] == "open"
    [corps] = prediction_model.calibration(store.prediction_rows(), day)
    assert (corps["resolved"], corps["observed"], corps["void"]) == (2, 2, 0)


def test_a_wake_prediction_is_written_before_its_outcome_and_reads_a_closed_rule(store):
    day = dt.date(2026, 10, 12)
    _wake(store, day, 0.0)
    with pytest.raises(StorageError, match="existe déjà"):
        _bound(store, "trop-tard", day)
    with pytest.raises(StorageError, match="critère de réussite illisible"):
        _bound(store, "illisible", day + dt.timedelta(days=1), success_if="peu")
    with pytest.raises(StorageError, match="observation de solde inconnue"):
        store.upsert_prediction({
            "prediction_key": "inconnue", "domain": "corps", "statement": "x",
            "probability": .5, "due_on": day,
            "resolves_on": {"kind": "seance", "date": day.isoformat(), "success_if": "<=1"}})
    with pytest.raises(StorageError, match="après l'échéance"):
        store.upsert_prediction({
            "prediction_key": "apres", "domain": "corps", "statement": "x",
            "probability": .5, "due_on": day,
            "resolves_on": {"kind": "wake-trace", "date": "2026-10-20", "success_if": "<=1"}})


def test_a_completed_wake_cell_settles_and_a_missing_wake_voids_never_falsifies(store):
    first, second, third = (dt.date(2026, 10, d) for d in (13, 14, 15))
    for key, day in (("cellule", first), ("absent", second), ("indisponible", third)):
        _bound(store, key, day, success_if="<1")
    observation_id = _wake(store, first, None, state="pending")
    assert {row["prediction_key"]: row["status"] for row in store.prediction_rows()}[
        "cellule"] == "open"
    store.complete_measurement(observation_id, "pain", 2.0)
    _wake(store, third, None, state="unavailable")
    with store.database.transaction() as connection:
        assert store.settle_unobserved_predictions_in(connection, second) == 0
        assert store.settle_unobserved_predictions_in(connection, third) == 1
    rows = {row["prediction_key"]: row for row in store.prediction_rows()}
    assert (rows["cellule"]["status"], rows["cellule"]["outcome"]) == ("resolved", False)
    assert (rows["absent"]["status"], rows["absent"]["outcome"]) == ("void", None)
    assert (rows["indisponible"]["status"], rows["indisponible"]["outcome"]) == ("void", None)


# ------------------------------------------------------------ pain per zone

def _second_zone(store):
    store.sync_pain_zones([("genou", "Genou"), ("talon", "Talon")])


def test_each_watched_zone_gets_its_own_reading(store):
    _second_zone(store)
    observation_id = store.add_observation({
        "occurred_on": dt.date(2026, 10, 12), "kind": "easy", "idempotency_key": "zones:ef",
        "measurements": {}, "pains": {"talon": {"state": "known", "value": 2.5}}})
    row = next(row for row in store.observation_rows() if row["id"] == observation_id)
    assert row["pains"] == {"talon": {"state": "known", "value": 2.5},
                            "genou": {"state": "pending", "value": None}}
    store.complete_measurement(observation_id, "pain", 1.0, zone="genou")
    with pytest.raises(StorageError, match="déjà connue"):
        store.complete_measurement(observation_id, "pain", 3.0, zone="talon")
    with pytest.raises(StorageError, match="nomme la zone"):
        store.complete_measurement(observation_id, "pain", 3.0)


def test_a_zone_the_instance_no_longer_watches_is_refused_but_its_history_stays(store):
    store.add_observation({
        "occurred_on": dt.date(2026, 10, 12), "kind": "easy", "idempotency_key": "zones:old",
        "measurements": {}, "pains": {"genou": {"state": "known", "value": 1.0}}})
    store.sync_pain_zones([("talon", "Talon")])
    assert [(zone["slug"], zone["active"]) for zone in store.pain_zones()] == [
        ("talon", True), ("genou", False)]
    with pytest.raises(StorageError, match="inconnue ou inactive"):
        store.add_observation({
            "occurred_on": dt.date(2026, 10, 13), "kind": "easy",
            "idempotency_key": "zones:new", "measurements": {},
            "pains": {"genou": {"state": "known", "value": 1.0}}})
    old = next(row for row in store.observation_rows()
               if row["idempotency_key"] == "zones:old")
    assert old["pains"] == {"genou": {"state": "known", "value": 1.0}}


def test_without_a_watched_zone_an_effort_claims_no_pain(store):
    store.sync_pain_zones([])
    observation_id = store.add_observation({
        "occurred_on": dt.date(2026, 10, 12), "kind": "easy", "idempotency_key": "zones:none",
        "measurements": {}})
    row = next(row for row in store.observation_rows() if row["id"] == observation_id)
    assert row["pains"] == {}


def test_a_wake_prediction_waits_for_the_zone_it_names(store):
    _second_zone(store)
    day = dt.date(2026, 10, 12)
    with pytest.raises(StorageError, match="nomme sa zone"):
        _bound(store, "sans-zone", day)
    store.upsert_prediction({
        "prediction_key": "talon-calme", "domain": "corps", "statement": "talon calme",
        "probability": .7, "due_on": day + dt.timedelta(days=2),
        "resolves_on": {"kind": "wake-trace", "date": day.isoformat(), "success_if": "<=1",
                        "zone": "talon"}})
    store.add_observation({
        "occurred_on": day, "kind": "wake-trace", "idempotency_key": "zones:reveil",
        "measurements": {}, "pains": {"genou": {"state": "known", "value": 2.0},
                                      "talon": {"state": "known", "value": 0.5}}})
    [row] = [row for row in store.prediction_rows() if row["prediction_key"] == "talon-calme"]
    assert (row["status"], row["outcome"]) == ("resolved", True)


def test_a_strength_report_keeps_each_zone_before_and_after(store):
    _second_zone(store)
    payload = {"session_key": "zones-renfo", "performed_on": dt.date(2026, 10, 12),
               "workout": "a", "duration_minutes": 30, "note": "", "exercises": [],
               "pains": {"talon": {"before": 1, "after": 3}, "genou": {"after": 0}},
               "message_id": "strength:zones-renfo", "psid": "4242", "message": "x"}
    assert store.accept_strength_report(payload, max_per_day=8) == "accepted"
    [session] = [row for row in store.strength_history() if row["session_key"] == "zones-renfo"]
    assert session["pains"] == {"talon": {"before": 1, "after": 3},
                                "genou": {"before": None, "after": 0}}


def test_corrections_are_matched_on_their_rare_words(store):
    """A word every correction uses matches nothing; a word few share does."""
    summaries = {
        "c1": "La descente du dimanche se court plus lentement pour les mollets",
        "c2": "La cadence se mesure sur les tours faciles pour la comparer",
        "c3": "La sortie longue garde son plafond pour la semaine suivante",
    }
    with store.database.engine.begin() as connection:
        connection.execute(sa.insert(correction_deliveries), [
            {"id": uuid.uuid4(), "athlete_id": ATHLETE, "correction_key": key,
             "summary": summary}
            for key, summary in summaries.items()])
    state = PostgresStateStore(store.database)
    assert state.correction_context("pour la suite") == ""
    context = state.correction_context("et la descente de demain ?")
    assert summaries["c1"] in context
    assert summaries["c2"] not in context and summaries["c3"] not in context
