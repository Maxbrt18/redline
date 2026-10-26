"""Garanties exécutées par un vrai PostgreSQL, pas par un mock SQL."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa
from conftest import VAULT_ROOT

from redline import strength
from redline.attachments import encode_payload
from redline.postgres_state import PostgresStateStore
from scripts.model import predictions as prediction_model
from scripts.storage import Database, OperationalStore, StorageError
from scripts.storage.schema import (
    TRIGGERS,
    audit_events,
    coach_predictions,
    conversation_messages,
    conversation_questions,
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
    """Le cœur de /strength-report, sans son quota : False si la clé est déjà prise."""
    with store.database.transaction() as connection:
        return store._add_strength_session_in(connection, payload, actor="test")


@pytest.fixture(autouse=True)
def clean_database():
    database = Database(URL, application_name="pytest")
    with database.engine.begin() as connection:
        names = ", ".join(f"redline.{table.name}" for table in metadata.sorted_tables)
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
        connection.execute(sa.text("INSERT INTO redline.athletes(id) VALUES ('sam')"))
    yield
    database.dispose()


@pytest.fixture
def store():
    return OperationalStore(Database(URL, application_name="pytest"))


@pytest.fixture(scope="module")
def service_role_store():
    """Une base migrée après création des vrais rôles, puis utilisée en redline_app.

    La base ordinaire de pytest est migrée avant le lancement de la suite et ses tests
    tournent sous `postgres`. Cette base isolée rejoue donc la séquence de production :
    rôles présents, migration qui accorde les droits, opération sous le rôle applicatif.
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
            cwd=VAULT_ROOT, env=env, text=True, capture_output=True, check=False)
        assert migrated.returncode == 0, migrated.stderr or migrated.stdout

        database = Database(test_url.render_as_string(hide_password=False),
                            application_name="pytest-redline-app")

        @sa.event.listens_for(database.engine, "connect")
        def use_service_role(dbapi_connection, _):
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET ROLE redline_app")
            # SET ROLE est transactionnel : le rollback de la première lecture
            # ne doit pas rendre les connexions suivantes superutilisatrices.
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
        "occurred_on": dt.date(2026, 8, 12), "kind": "EF",
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
    """La mesure reste immuable ; seule l'annotation dit ce qu'elle vaut (2026-10-20)."""
    payload = observation()
    payload["measurements"]["hr_avg_bpm"] = {"state": "known", "value": 157}
    oid = store.add_observation(payload)
    with pytest.raises(StorageError):
        store.doubt_hr(oid, "sans_brassard", "  ")
    with pytest.raises(StorageError):
        store.doubt_hr(oid, "poignet", "pas de brassard")
    store.doubt_hr(oid, "sans_brassard", "pas de capteur FC, 95 % des points ratés")
    row = next(r for r in store.observation_rows() if r["id"] == oid)
    assert row["hr_doubt"] == "sans_brassard"
    assert row["measurements"]["hr_avg_bpm"]["value"] == 157
    with pytest.raises(StorageError):
        store.doubt_hr(oid, "contexte", "repas")


def test_an_unknown_heart_rate_cannot_be_doubted(store):
    oid = store.add_observation(observation())
    with pytest.raises(StorageError):
        store.doubt_hr(oid, "contexte", "repas une heure avant")


def test_a_metric_never_recorded_can_still_be_filled_later(store):
    """⭐ Seul `known` est immuable. `not_applicable` est le DÉFAUT de toute colonne
    absente à la création, pas une donnée — le bloquer rendait définitivement
    incomplétable une séance consignée sans ses chiffres.

    Le vélo du 2026-08-13 : consigné à la main après un débrief qui n'avait pas pu lire
    Strava, donc sans durée ni D+, donc pesant ZÉRO dans la jauge de la semaine. Trois
    jours plus tard, ses chiffres étaient toujours chez Strava et toujours irrattrapables.
    """
    oid = store.add_observation(observation())
    store.complete_measurement(oid, "duration_s", 6177)
    rows = {row["idempotency_key"]: row for row in store.observation_rows()}
    measured = rows[observation()["idempotency_key"]]["measurements"]["duration_s"]
    assert measured["state"] == "known" and float(measured["value"]) == 6177
    # Et une fois remplie, elle redevient immuable comme les autres.
    with pytest.raises(StorageError):
        store.complete_measurement(oid, "duration_s", 7000)


def test_the_purge_only_targets_tables_the_service_may_delete_from(service_role_store):
    """La purge ne vise que des traces explicitement expirables."""
    with service_role_store.database.engine.connect() as connection:
        assert connection.execute(sa.text("SELECT current_user")).scalar_one() == "redline_app"
        for table in OperationalStore.EXPIRY_DAYS:
            assert connection.execute(sa.text(
                "SELECT has_table_privilege(current_user, :table, 'DELETE')"),
                {"table": f"redline.{table}"}).scalar_one(), (
                    f"redline_app ne peut pas purger {table}")
    assert service_role_store.purge_expired() == {}


def test_the_purge_removes_markers_but_keeps_command_idempotence(store):
    """⭐ Une commande appliquée reste la barrière contre tout replay tardif.

    La supprimer après 30 jours ne répétait pas forcément l'effet métier : le plan a
    aussi sa contiguïté et l'observation sa propre identité. Mais la vieille commande
    pouvait revenir pending et faire tomber le nouveau tour. La clé ne doit donc jamais
    disparaître, appliquée ou non.
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
        # Les traces déjà écrites par les commandes ci-dessus sont vieillies d'un coup.
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
    assert restant["markers"] == 1        # le frais reste
    assert restant["commandes"] == 2      # appliquée ou pending : les deux clés restent
    assert restant["observations"] == 1   # aucun FAIT n'est touché
    with pytest.raises(StorageError, match="collision d'idempotence"):
        store.stage_command(
            "8f0f98df-a68c-44bd-9dc3-80e240775fa8", "observation:vieille",
            "observation.add",
            {**observation(strava=None), "idempotency_key": "vieille"})


def test_the_declared_triggers_are_exactly_those_the_database_carries(store):
    """⭐ Les garanties qui regardent l'ANCIENNE ligne ne vivaient que dans leurs
    migrations. `alembic check` compare des métadonnées : il ne voit aucun trigger, donc
    la disparition de l'un d'eux -- un `DROP` à la main, une restauration partielle --
    n'aurait été signalée par rien, jamais.

    Les deux sens comptent. Un trigger déclaré et absent, c'est une immuabilité qu'on
    croit avoir. Un trigger présent et non déclaré, c'est une règle que personne ne
    peut lire dans `schema.py`.
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
    from redline.web import _strength_payload
    from scripts.model.dose import Dose
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
    sessions = [session("w33-mar-a", date="2026-08-11", workout="renfo-a")]
    if two_sessions:
        sessions.append(session("w33-mar-a-bis", date="2026-08-11", workout="renfo-a"))
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
        "workout": "a", "duration_minutes": 61, "shin_after": 1,
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
    # Rapports antérieurs au plan : un seul rapport pour deux séances, ou l'inverse.
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
        "occurred_on": dt.date(2026, 9, 2), "kind": "EF",
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
            "occurred_on": day, "kind": "EF", "idempotency_key": key,
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
        gates=[{"id": "w33-injury", "due": "2026-08-14", "when": "apres",
                "question": "Comment est le injury au réveil ce matin ?",
                "on_fail": "appliquer le repli prévu"}])
    store.append_plan_revision("2026-W33", first)

    second = build_revision(
        2, sessions=[], computed_at="2026-08-14T09:10:00+02:00", gates=[])
    with pytest.raises(StorageError, match="retiré sans résolution typée"):
        store.append_plan_revision("2026-W33", second)

    second["evidence"] = [
        "gate:w33-injury=observation:messenger:2026-08-14:reveil"]
    with pytest.raises(StorageError, match="observation canonique inconnue"):
        store.append_plan_revision("2026-W33", second)

    store.add_observation({
        "occurred_on": dt.date(2026, 8, 14), "kind": "reveil",
        "idempotency_key": "messenger:2026-08-14:reveil",
        "note": "Aucune gêne dans les dix premiers pas.",
        "measurements": {"pain": {"state": "unavailable"}},
    })
    store.append_plan_revision("2026-W33", second)
    assert store.load_plan("2026-W33")["revisions"][-1]["revision"] == 2


def test_a_due_gate_can_close_on_its_precommitted_fallback(store):
    from test_weekly_plan import revision as build_revision

    first = build_revision(
        1, sessions=[], computed_at="2026-08-09T19:10:00+02:00",
        gates=[{"id": "w33-injury", "due": "2026-08-14", "when": "apres",
                "question": "Comment est le injury au réveil ce matin ?",
                "on_fail": "appliquer le repli prévu"}])
    store.append_plan_revision("2026-W33", first)
    second = build_revision(
        2, sessions=[], computed_at="2026-08-14T09:10:00+02:00", gates=[],
        evidence=["gate:w33-injury=fallback:repli-applique"])
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
    # Audit du 2026-10-09 : la seule prédiction « charge » avait été soldée fausse alors
    # que sa condition n'avait pas été tenue et que la question du réveil restait ouverte.
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


def test_a_sealed_permit_choice_creates_its_prediction_and_cannot_be_rewritten(store):
    choice = {"iso_week": "2026-W40", "session_key": "w40-mar-ef",
              "scheduled_on": dt.date(2026, 9, 29), "coach_minutes": 40,
              "prediction_key": "permis-ef-w40-mar-ef",
              "statement": "Choix scellé : 40 min.", "probability": .6,
              "due_on": dt.date(2026, 10, 4)}
    store.seal_permit_choice(choice)
    store.seal_permit_choice(choice)  # rejouée à l'identique : idempotente
    assert [row["coach_minutes"] for row in store.permit_choice_rows()] == [40]
    prediction = next(row for row in store.prediction_rows()
                      if row["prediction_key"] == "permis-ef-w40-mar-ef")
    assert prediction["domain"] == "permis-ef" and prediction["status"] == "open"
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
        "w32-mer-sl", "2026-08-05", "SL")])
    with pytest.raises(StorageError, match="indisponibilité typée examens"):
        store.append_plan_revision("2026-W32", candidate)


def test_a_revision_after_the_run_keeps_its_pending_gut_review(store):
    """Toute révision annulait le retour digestif en attente d'une séance courue."""
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    long_run = session("w33-dim-sl", "2026-08-16", "SL", carbs_target_g_h=60)
    store.append_plan_revision("2026-W33", build_revision(
        1, sessions=[long_run], computed_at="2026-08-09T19:10:00+02:00"))
    store.add_observation({
        "occurred_on": dt.date(2026, 8, 16), "kind": "SL",
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
    result = store.ingest_strava_activity({
        "id": 90000000011, "sport": "Run", "nom": "EF du vendredi",
        "depart_local": "2026-08-14T08:03:00", "distance_km": 7.123,
        "temps_mouvement": "42:10", "allure_min_km": "5:55",
        "fc_moy": 151.2, "fc_max": 168, "cadence_pas_min": 169.4,
        "dplus_m": 62.5,
    })
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
        "occurred_on": dt.date(2026, 8, 14), "kind": "EF",
        "idempotency_key": "strava:90000000012", "strava_activity_id": 90000000012,
        "measurements": {},
    })
    # L'observation précède la colonne started_at : le distillat doit la compléter.
    result = store.ingest_strava_activity({
        "id": 90000000012, "sport": "Run", "nom": "EF historique",
        "depart_local": "2026-08-14T08:03:00", "distance_km": 7.0,
        "temps_mouvement": "42:00",
    })
    assert result["candidate_session_keys"] == ["2026-W33:w33-ven-ef"]
    with store.database.engine.connect() as connection:
        cycle = connection.execute(sa.select(session_cycles)).mappings().one()
        stored = connection.execute(sa.select(observations).where(
            observations.c.id == observation_id)).mappings().one()
    assert cycle["observation_id"] == observation_id
    assert cycle["state"] == "completed"
    assert stored["started_at"].astimezone(paris).strftime("%H:%M") == "08:03"


def test_a_wbgt_already_logged_by_the_coach_does_not_sink_the_capture(store):
    """2026-09-26 : le coach consigne la sortie (WBGT 11) avant le webhook ; le
    pipeline calcule une autre valeur, levait, et le débrief expirait après deux
    heures de reprises. La mesure du coach reste, l'écart devient une trace."""
    observation_id = store.add_observation({
        "occurred_on": dt.date(2026, 9, 26), "kind": "EF",
        "idempotency_key": "strava:90000000013", "strava_activity_id": 90000000013,
        "measurements": {"wbgt_c": {"state": "known", "value": 11.0}},
    })
    store.ingest_strava_activity({
        "id": 90000000013, "sport": "Run", "nom": "Morning Run",
        "depart_local": "2026-09-26T07:45:00", "distance_km": 8.0,
        "temps_mouvement": "48:00", "meteo": {"polyline": "x", "time": "t"},
    })
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
    result = store.ingest_strava_activity({
        "id": 90000000014, "sport": "Run", "nom": "Sortie",
        "depart_local": "2026-08-14T08:03:00", "distance_km": 7.0,
        "temps_mouvement": "42:00",
    })
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
        "occurred_on": dt.date(2026, 8, 14), "kind": "EF",
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
        "occurred_on": dt.date(2026, 8, 14), "kind": "EF",
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
    """⭐ 2026-08-18. `plan_gates` n'avait pas de colonne pour `when` ni `holds` : la
    révision partait typée et revenait nue, donc invalide au contrat 2 qui exige
    `when`. Un store relationnel qui ne connaît pas un champ ne proteste pas, il
    l'oublie -- et l'erreur remontée décrivait la relecture, pas l'écriture.

    L'aller-retour est le SEUL endroit où ça se voit : le modèle validait, le store
    écrivait, et personne ne relisait ce qui était ressorti."""
    store.append_plan_revision("2026-W33", {
        "revision": 1, "computed_at": "2026-08-09T19:10:00+02:00", "reason": "test",
        "sessions": [],
        "gates": [
            {"id": "g-avant", "due": "2026-08-14", "when": "avant",
             "holds": ["w33-jeu-b", "w33-ven-ef"],
             "question": "le injury au réveil ?", "on_fail": "repos"},
            {"id": "g-apres", "due": "2026-08-15", "when": "apres",
             "question": "la cheville a parlé pendant la pliométrie ?",
             "on_fail": "la pliométrie sort de la séance A"},
        ],
    })
    gates = {gate["id"]: gate for gate in store.load_plan("2026-W33")["revisions"][0]["gates"]}
    assert gates["g-avant"]["when"] == "avant"
    assert gates["g-avant"]["holds"] == ["w33-jeu-b", "w33-ven-ef"]
    assert gates["g-apres"]["when"] == "apres"
    # Absent, et non None : le schéma lit « when manquant » comme « écrit avant le
    # typage », ce qu'un None explicite ne dirait pas.
    assert "holds" not in gates["g-apres"]


def test_a_revision_round_trip_has_no_schema_version(store):
    store.append_plan_revision("2026-W33", {
        "revision": 1, "computed_at": "2026-08-09T19:10:00+02:00", "reason": "test",
        "sessions": [], "gates": [],
    })
    revision = store.load_plan("2026-W33")["revisions"][0]
    assert "text_contract" not in revision


def test_a_stale_plan_projection_is_detected_without_being_repaired(store, tmp_path):
    """⭐ Le contrôle `vues générées à jour` ANNONÇAIT couvrir le plan hebdo sans jamais
    l'ouvrir. Le 2026-08-16, dix projections attendaient dans l'outbox après des
    écritures faites en CLI hors tour : le journal de W33 affichait une révision périmée
    de deux crans et le contrôle affichait vert. Il compare désormais vraiment — et,
    comme les autres, il ne répare pas : un contrôle qui répare en silence n'en est pas un.
    """
    from scripts.storage.projector import plan_drift, plan_view_count, project_plans

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
            "workout": "EF", "status": "planned", "demanding": False,
            "distance_cap_km": 9.0, "sources": ["[[current-plan]]"]}]})

    assert plan_drift(store, tmp_path) == ["athlete/journal/2026-w33.md"]
    assert "périmé" in page.read_text(encoding="utf-8"), "le contrôle n'écrit pas"

    project_plans(store, tmp_path)
    assert plan_drift(store, tmp_path) == []

    # Le hot-cache n'existe pas dans ce vault : le contrôle le saute au lieu de
    # planter dessus. Il a planté en CI le 2026-08-16, et un contrôle qui plante ne
    # dit rien sur la dérive qu'il était censé voir.
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
    """Un signal de Sam avance le débrief sans jamais le reculer ; un tour
    réactif le réclame avant son échéance, une seule fois."""
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

    assert "message antérieur de Sam" in context
    assert "Mon ancien constat." in context


def _ask(store, state, questions, source_key, *, body=None, platform_messages=()):
    """Un tour qui DÉCLARE ses questions (`fact-write.py ask`), puis la livraison."""
    from scripts.model import commands
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
    first, second = "Quel était ton RPE ?", "Et le injury au réveil ?"
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
    _ask(store, state, "Le injury au réveil et à la marche ?", "q")
    for i, text in enumerate(("172 pas/min", "oui", "le injury, je ne sais pas", "merci")):
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
    from scripts.model import commands
    state = PostgresStateStore(store.database)
    question = "Le injury au réveil ?"
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
    from scripts.model import commands
    state = PostgresStateStore(store.database)
    turn_id = str(uuid.uuid4())
    store.stage_command(turn_id, "ask:rpe", commands.QUESTION_ASK,
                        {"text": "Quel était ton RPE ?", "cells": ["2026-09-05:EF:RPE"]})
    store.apply_turn_with_receipts(turn_id)[0]
    state.record_outbound("4242", delivered, source_key="out", turn_id=turn_id)
    assert state.proactive.marked("asked:2026-09-05:EF:RPE") == ("RPE" in delivered)
    assert not state.proactive.marked("asked:2026-09-05:EF:Dlr")
    assert not state.proactive.marked("asked:2026-09-05:EF:Plaisir")
    state.record_outbound("4242", delivered, source_key="out", turn_id=turn_id)
    assert len(store.question_rows()) == (1 if "RPE" in delivered else 0)


def test_a_question_is_tracked_only_from_a_declaration_found_in_the_delivered_text(store):
    """Aucun « ? » de prose ne devient un agenda ; une déclaration absente du texte livré
    non plus : ni question ouverte, ni cellule marquée comme demandée."""
    from scripts.model import commands
    state = PostgresStateStore(store.database)
    state.record_outbound("4242", "Belle sortie, non ? Tu vois où je veux en venir ?",
                          source_key="out:rhetoric")
    turn_id = str(uuid.uuid4())
    store.stage_command(turn_id, "ask:rpe", commands.QUESTION_ASK,
                        {"text": "Quel était ton RPE ?", "cells": ["2026-09-05:EF:RPE"]})
    store.apply_turn_with_receipts(turn_id)[0]
    state.record_outbound("4242", "Tu l'as ressenti à combien sur dix ?",
                          source_key="out:rephrase", turn_id=turn_id)
    assert store.question_rows() == []
    assert not state.proactive.marked("asked:2026-09-05:EF:RPE")


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
    """« Comment tu te sens ce matin ? » se repose chaque jour : une réponse d'hier ne
    rend pas la question d'aujourd'hui « déjà traitée »."""
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
    state.record_outbound("4242", "Et le injury après ?", source_key="out:1")
    assert state.claim_inbound("m2", "4242", "text", "Injury calme au réveil")
    assert state.claim_inbound("foreign", "9999", "text", "Ma descente était difficile")
    results = store.conversation_search("4242", "descente", limit=4)
    assert len(results) == 1
    assert results[0]["hit"]["role"] == "athlete"
    assert results[0]["hit"]["body"] == "La descente hier était facile"
    assert results[0]["after"]["body"] == "Et le injury après ?"


def test_conversation_search_uses_registered_turn_scope(store):
    state = PostgresStateStore(store.database)
    assert state.claim_inbound("mine", "4242", "text", "Mon injury va mieux")
    assert state.claim_inbound("other", "9999", "text", "Mon injury reste sensible")
    turn_id = str(uuid.uuid4())
    state.register_turn(turn_id, "4242")

    results = store.conversation_search_for_turn(turn_id, "injury")
    assert [row["hit"]["body"] for row in results] == ["Mon injury va mieux"]
    with pytest.raises(ValueError, match="absent ou expiré"):
        store.conversation_search_for_turn(str(uuid.uuid4()), "injury")


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
            "score": 6, "scale": 5, "status": "vert",
            "evaluated_on": dt.date(2026, 8, 13)})
    assert store.latest_capabilities() == []


def _capability_cli(*extra: str, turn_id: str = "") -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": URL}
    env.pop("REDLINE_TURN_ID", None)
    if turn_id:
        env["REDLINE_TURN_ID"] = turn_id
    return subprocess.run([
        sys.executable, "scripts/fact-write.py", "capability", "--capability-number", "5",
        "--slug", "cap5-injury", "--need", "Injury intacts", "--score", "3", "--scale", "5",
        "--status", "orange", "--evaluated-on", "2026-10-11", *extra,
    ], cwd=VAULT_ROOT, env=env, capture_output=True, text=True, check=False)


def test_same_capability_same_day_is_refused_not_announced(store):
    """Le 2026-10-13, un `--next-test` corrigé pour le 2026-10-11 a été ignoré par la
    contrainte journalière pendant que la CLI répondait « enregistré »."""
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
    # `sys.executable`, jamais `"python3"` : sur le runner CI les deux désignent le même
    # binaire, sur le Mac `python3` est celui du système -- sans SQLAlchemy. Ce test
    # échouait donc en local depuis toujours, ce qui rendait toute vérification locale
    # de la couche PostgreSQL impossible à lire (2026-08-18).
    process = subprocess.run([
        sys.executable, "scripts/fact-write.py", "thread", "--key", "cli-commun",
        "--opened-on", "2026-08-13", "--kind", "suspens", "--status", "ouvert",
        "--body", "même commande pour Claude et Codex",
    ], cwd=VAULT_ROOT, env={**os.environ, "DATABASE_URL": URL,
                            "REDLINE_TURN_ID": turn_id},
       capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stderr
    assert store.thread_rows() == []
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.thread_rows()[0]["body"] == "même commande pour Claude et Codex"


def test_the_cli_patch_keeps_the_long_text_it_was_not_given(store):
    """La garantie qui compte : un `--patch` ne peut pas tronquer ce qu'il ignore.

    `sys.executable` et pas `python3` : l'interpréteur nu vaut 3.9 sur le Mac de
    Sam, où les scripts demandent 3.10+ (`docs/vault-maintenance.md`).
    """
    long_item = "texte de 1400 caractères " * 56
    common = [sys.executable, "scripts/fact-write.py", "deadline", "--key", "cli-patch"]
    environment = {**os.environ, "DATABASE_URL": URL}
    created = subprocess.run([
        *common, "--due-on", "2026-08-11", "--item", long_item,
        "--fact-owner", "`coaching/strength.md`", "--blocks", "dose W34",
        "--status", "todo",
    ], cwd=VAULT_ROOT, env=environment, capture_output=True, text=True, check=False)
    assert created.returncode == 0, created.stderr

    patched = subprocess.run([*common, "--patch", "--due-on", "2026-08-23"],
                             cwd=VAULT_ROOT, env=environment,
                             capture_output=True, text=True, check=False)
    assert patched.returncode == 0, patched.stderr

    rows = [row for row in store.deadline_rows() if row["deadline_key"] == "cli-patch"]
    assert len(rows) == 1, "un patch ne doit jamais dédoubler la ligne"
    assert rows[0]["due_on"] == dt.date(2026, 8, 23)
    assert rows[0]["item"] == long_item
    assert rows[0]["blocks"] == "dose W34"


def test_the_cli_refuses_to_patch_a_key_that_does_not_exist(store):
    refused = subprocess.run([
        sys.executable, "scripts/fact-write.py", "deadline", "--key", "jamais-vue",
        "--patch", "--status", "done",
    ], cwd=VAULT_ROOT, env={**os.environ, "DATABASE_URL": URL},
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
    # La commande fautive n'entre pas dans le tour ; le fait valide qui la
    # précédait reste applicable au lieu d'être détruit avec elle après la réponse.
    assert store.counts()["turn_commands"] == 1
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.counts()["observations"] == 1


def test_a_bad_gate_resolution_is_refused_before_it_can_cancel_same_turn_facts(store):
    """Reproduction du 31/08 : fait correct, puis retrait de gate sans preuve."""
    from test_weekly_plan import revision as build_revision

    first = build_revision(
        1, sessions=[], computed_at="2026-08-09T19:10:00+02:00",
        gates=[{"id": "w33-injury", "due": "2026-08-14", "when": "avant",
                "question": "Comment est le injury au réveil ce matin ?",
                "on_fail": "appliquer le repli prévu"}])
    store.append_plan_revision("2026-W33", first)
    turn_id = "d740e54c-1933-4cbc-ae7f-982a908626db"
    store.stage_command(turn_id, "observation:reveil", "observation.add", {
        "occurred_on": "2026-08-14", "kind": "reveil",
        "idempotency_key": "messenger:reveil", "note": "injury plus calme",
        "measurements": {"pain": {"state": "unavailable"}},
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
    """CONTRAT INVERSÉ LE 2026-08-14, volontairement.

    Ce test exigeait l'inverse : sans programme pour la semaine, les faits du tour
    étaient annulés. C'est ce qui faisait perdre une séance parce qu'un artefact
    dérivé manquait — et le jour où le coach a enfin pu lire Strava, deux débriefs
    complets sont partis à la poubelle pour cette raison.

    Une séance reste vraie même sans programme. L'absence de programme est un défaut
    de PRESCRIPTIBILITÉ, rendu par `VaultRepo.validate_weekly_plan` : elle retient la
    prescription, et c'est le seul cas qui fasse encore taire le coach."""
    turn_id = "69e645ed-bde6-403a-900f-020362da6fc8"
    store.stage_command(turn_id, "observation:without-plan", "observation.add", {
        **observation(strava=None), "idempotency_key": "without-plan"})
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.counts()["observations"] == 1
    assert store.load_plan("2026-W33") is None


def test_strength_quota_refuses_without_consuming_an_extra_claim(store):
    payload = {"session_key": "sid-1", "performed_on": dt.date(2026, 8, 20),
               "workout": "a", "shin_before": 1, "shin_after": 2,
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
            "select shin_before, shin_after, duration_minutes, note "
            "from redline.strength_sessions where session_key = 'sid-1'"
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
                                        "sleep": "bonne", "note": "ok"}),
        ("thread:test", "thread.upsert", {
            "thread_key": "injury-jeudi", "opened_on": dt.date(2026, 8, 13),
            "kind": "ressenti", "body": "à revoir", "status": "ouvert"}),
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
        store.ingest_strava_activity({
            "id": 9900 + index, "sport": "Run",
            "depart_local": f"2026-08-{day:02d}T08:00:00",
            "nom": "EF", "distance_km": 7, "materiel_id": "g-mach",
        })
    state = PostgresStateStore(store.database)
    now = dt.datetime(2026, 8, 23, 12, tzinfo=dt.UTC)
    state.materialize_intelligence(now)
    state.materialize_intelligence(now)
    jobs = [row for row in state.proactive.jobs()
            if row["id"].startswith("gear-rotation:g-mach:2026-W34")]
    assert jobs == []


def test_race_deadline_materializes_the_complete_surviving_roadmap(store):
    store.upsert_deadline({
        "deadline_key": "race-test", "due_on": dt.date(2026, 11, 28),
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
        "deadline_key": "race-moved", "due_on": dt.date(2026, 11, 28),
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
    old = [row for row in jobs if ":2026-11-28:" in row["id"]]
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
            "UPDATE redline.proactive_messages SET summary = 'réécrit'"))
    with pytest.raises(StorageError):
        store.add_proactive_message({**row, "summary": "contenu contradictoire"})


def test_a_false_correction_is_revised_under_the_same_key_and_reannounced(store):
    """Une correction n'est pas un fait immuable : le 28/08, une correction erronée
    déjà livrée a survécu au correctif du prompt et a fait réinterdire l'EF. La même
    occurrence doit avoir une mémoire unique, révisable et de nouveau annonçable.
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
    # Audit du 2026-10-09 : sept excuses livrées 23 à 26 jours après l'erreur.
    store.register_correction({"correction_key": "ancienne", "summary": "Erreur ancienne."})
    store.register_correction({"correction_key": "recente", "summary": "Erreur récente."})
    with store.database.transaction() as connection:
        connection.execute(sa.text(
            "UPDATE redline.correction_deliveries SET registered_at = now() - interval '10 days'"
            " WHERE correction_key = 'ancienne'"))
        # Une annonce déjà en file avant la règle, sans échéance.
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
    # Une récidive remplace le résumé : sans ce compte, la ligne ne disait plus que
    # l'erreur était revenue (audit du 2026-10-09).
    store.register_correction({"correction_key": "reveil", "summary": "06/09 : oui franc exigé."})
    store.register_correction({"correction_key": "reveil", "summary": "Récidive le 01/10."})
    store.register_correction({"correction_key": "reveil", "summary": "Récidive le 08/10."})
    store.register_correction({"correction_key": "autre", "summary": "Une seule fois."})
    rows = {row["correction_key"]: row for row in store.correction_rows()}
    assert rows["reveil"]["revisions"] == 2
    assert rows["reveil"]["summary"] == "Récidive le 08/10."
    assert rows["autre"]["revisions"] == 0
    assert rows["reveil"]["first_registered_at"] is not None


def test_plan_watermark_includes_facts_staged_earlier_in_the_same_turn(store):
    # La révision doit être VALIDE : depuis le 2026-08-14, `apply_turn_with_receipts` relit ce qu'il
    # vient d'écrire, et un programme invalide est refusé à l'écriture. Ce payload
    # passait avant parce que la validation ne se déclenchait qu'avec `required_weeks`.
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
    """Une révision VALIDE de 2026-W33.

    On réutilise les fabriques de `test_weekly_plan` plutôt que d en réinventer une
    approximative : le schéma exige au moins une séance et un `evidence`, et refuse
    une séance datée hors de sa semaine. Une révision invalide échouerait sur la
    validation AVANT d atteindre le contrôle de péremption, et ces tests passeraient
    au vert en ne vérifiant rien de ce qu ils prétendent vérifier.
    """
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session
    base = build_revision(
        number,
        sessions=[session("w33-ven-ef", date="2026-08-14", workout="EF")],
        computed_at="2026-08-14T09:00:00+02:00",
    )
    return {**base, **overrides}


def test_a_fact_is_never_rolled_back_because_a_derived_plan_went_stale(store):
    """LE test de l'inversion réparée le 2026-08-14.

    Une séance est de la vérité terrain ; le programme hebdomadaire en est un calcul.
    Le dépôt annulait le fait quand le calcul devenait périmé — il jetait la séance
    pour protéger ce qui s'en déduit. Ce jour-là, un débrief complet et correct a été
    détruit deux fois, et la sortie de Sam est restée non consignée.

    Désormais le fait entre, et la péremption devient un verdict de prescriptibilité
    que `VaultRepo.validate_weekly_plan` rend séparément."""
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")

    turn_id = "3f0d9d3a-6a0f-4a2f-9b0f-6a1f2c3d4e5f"
    store.stage_command(turn_id, "observation:sortie", "observation.add", {
        **observation(strava=None), "idempotency_key": "sortie-du-matin"})

    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.counts()["observations"] == 1
    # Le plan est bien périmé — mais c'est une lecture, pas une erreur d'écriture.
    assert store.plan_needs_arbitration("2026-W33", 1) is True


def test_completing_a_declarative_cell_never_makes_the_plan_stale(store):
    """« Effort ressenti 7-8/10 et pic injury 0/10 » a détruit un tour le 2026-08-14 :
    l'empreinte hachait la VALEUR des mesures, donc déclarer un RPE périmait le plan
    au même titre qu'une séance surgie de nulle part. Un chiffre déclaré appelle un
    jugement de coach, pas une révision de programme."""
    oid = store.add_observation({**observation(strava=None),
                                 "idempotency_key": "sortie-a-completer"})
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")
    store.complete_measurement(oid, "rpe", 7)
    assert store.plan_needs_arbitration("2026-W33", 1) is False


def test_the_daily_state_never_makes_the_plan_stale(store):
    """Même raison : dire qu'on a mal dormi nourrit la jauge, ça ne rend pas le
    programme faux. C'était la troisième des quatre portes qui cassaient un tour."""
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")
    store.put_daily({"day": dt.date(2026, 8, 14), "sleep": "mauvaise",
                     "life_load": "elevee", "desire": "normale"})
    assert store.plan_needs_arbitration("2026-W33", 1) is False


def test_a_new_session_still_makes_the_plan_stale(store):
    """Le resserrement ne désarme pas le contrôle : ce qu'il protège vraiment, c'est
    « le plan connaît-il tout ce qui a eu lieu ? », et une séance de plus le périme."""
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")
    store.add_observation({**observation(strava=None), "idempotency_key": "nouvelle"})
    assert store.plan_needs_arbitration("2026-W33", 1) is True


def test_arbitration_uses_the_persistence_watermark(store):
    """L'observation présente avant la révision a déjà été vue par l'arbitrage. Seule
    une observation d'effort entrée ensuite et toujours sans cycle doit fermer la
    prescription.
    """
    store.add_observation({**observation(strava=None),
                           "idempotency_key": "connue-avant-revision"})
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")

    assert store.plan_needs_arbitration("2026-W33", 1) is False

    store.add_observation({**observation(strava=None),
                           "idempotency_key": "surprise-apres-revision"})

    assert store.plan_needs_arbitration("2026-W33", 1) is True


def test_a_planned_activity_linked_after_the_revision_needs_no_arbitration(store):
    """L'exécution normale fait avancer le cycle, pas l'intention du plan."""
    store.append_plan_revision("2026-W33", _plan_revision(1), actor="test")
    store.add_observation({
        **observation(strava=90000000017),
        "occurred_on": dt.date(2026, 8, 14),
        "started_at": dt.datetime(2026, 8, 14, 8, 3, tzinfo=dt.UTC),
        "idempotency_key": "strava:90000000017",
    })

    assert store.plan_needs_arbitration("2026-W33", 1) is False


def test_an_invalid_revision_is_still_refused_at_write_time(store):
    """Les faits sont inconditionnels, PAS les plans : écrire un programme invalide
    reste une faute d'écriture, désormais rendue avant la réponse du coach."""
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
    """Le chemin correct, celui que le prompt doit faire prendre au coach : la
    révision pose son watermark APRÈS que la séance a été appliquée, donc le tour
    passe. Un garde-fou qui n'ouvre pas de chemin correct en fabrique un faux."""
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
    """L'angle a un ENDROIT, sinon il finit dans le message (2026-08-21).

    La trace d'un proactif s'écrit APRÈS la livraison Messenger, donc bien après
    `apply_turn_with_receipts` : la commande de tour sert de dépôt et `turn_angle` la relit.
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


def test_a_revision_that_cancels_a_session_without_a_typed_motif_is_refused(
        store, monkeypatch, capsys):
    """Audit du 2026-10-09 : une restriction sans prémisse ne s'écrit plus."""
    import io
    import json

    from conftest import load_script
    from test_weekly_plan import frozen_clock, session, written_today

    from scripts.model.schema import SchemaError

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
        with pytest.raises(SchemaError, match="sans motif typé"):
            cli._database_command(store, command, "2026-W32", False)
    assert len(store.load_plan("2026-W32")["revisions"]) == 1

    change["motif"] = {"regle": "n3", "fait": "observation:2026-08-04-reveil"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(cut)))
    assert cli._database_command(store, "revise", "2026-W32", False) == 0
    stored = store.load_plan("2026-W32")["revisions"][1]["changes"][0]
    assert stored["motif"] == {"regle": "n3", "fait": "observation:2026-08-04-reveil"}


def test_a_plan_readback_shows_each_run_gap_to_the_month_without_blocking(
        store, monkeypatch, capsys):
    """Aucun pourcentage fixe (2026-09-27) : la relecture montre l'écart de chaque
    course à la plus longue des 30 jours, et la révision passe quel que soit l'écart."""
    import io
    import json

    from conftest import load_script
    from test_weekly_plan import frozen_clock, session, written_today

    store.add_observation({
        "occurred_on": dt.date(2026, 7, 26), "kind": "SL", "idempotency_key": "sl-juillet",
        "note": "", "measurements": {"distance_km": {"state": "known", "value": 9.0}}})
    cli = load_script("week-plan")
    frozen_clock(monkeypatch, cli)
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)
    long_run = session("w32-dim-sl", date="2026-08-09", workout="SL",
                       distance_target_km=12.0, distance_cap_km=13.5)
    fresh = written_today(number=1, sessions=[long_run])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(fresh)))
    assert cli._database_command(store, "revise", "2026-W32", False) == 0
    out = capsys.readouterr().out
    assert "w32-dim-sl 13.5 km = +50 % de 9.0 km (plus longue des 30 j)" in out
    assert "jamais bloquant" in out
    assert store.load_plan("2026-W32")["revisions"][0]["revision"] == 1


def test_the_weekly_dossier_is_computed_from_the_same_functions_as_the_clis(store):
    from scripts import weekly_dossier

    for day, km in ((dt.date(2026, 9, 13), 11.53), (dt.date(2026, 9, 20), 10.8),
                    (dt.date(2026, 9, 27), 13.6)):
        store.add_observation({
            "occurred_on": day, "kind": "SL", "idempotency_key": f"sl-{day}",
            "note": "", "measurements": {
                "distance_km": {"state": "known", "value": km},
                "duration_s": {"state": "known", "value": int(km * 400)}}})
    store.upsert_deadline({"deadline_key": "verdict-cortex", "due_on": dt.date(2026, 9, 6),
                           "item": "confirmer le verdict oral", "fact_owner": "athlete.md",
                           "status": "todo", "blocks": "descente courue"})
    text = weekly_dossier.render(store, VAULT_ROOT, "2026-W39", dt.date(2026, 9, 27))
    assert text.startswith("[DOSSIER DU BILAN") and text.endswith("[/DOSSIER]")
    assert "Semaine 2026-W39" in text and "13.60 km courus (1 sortie)" in text
    # Le bilan du dimanche clôt la semaine : son record compte déjà.
    assert "record de 13.6 km battu en 2026-W39" in text
    assert "en cours" not in text.split("Trajectoire")[1].split("Plus longue des")[0]
    assert "Plus longue des 30 j : 13.6 km" in text
    assert "verdict-cortex (due le 06/09)" in text and "bloque descente courue" in text
    assert "Prédictions narratives échues dans decisions.md" in text
    assert "**Verdict** (2026-09-27)" in text
    assert "Faisabilité à rebours : aucune course typée" in text
    store.upsert_deadline({"deadline_key": "targetrace", "due_on": dt.date(2026, 11, 28),
                           "kind": "race", "item": "TargetRace",
                           "fact_owner": "athlete/goals.md", "status": "scheduled"})
    text = weekly_dossier.render(store, VAULT_ROOT, "2026-W39", dt.date(2026, 9, 27))
    assert "Faisabilité à rebours (garde-fou n°8, calculée) : course le 28/11" in text


def test_the_weekly_dossier_does_not_flag_cadence_work_that_was_asked_for(store):
    """Le dossier du bilan lisait les signaux sans les cadences travaillées exprès."""
    from scripts import weekly_dossier

    for day, cadence, key in ((dt.date(2026, 9, 14), 160, "cadence:old"),
                              (dt.date(2026, 9, 21), 170, "cadence:worked")):
        store.add_observation({
            "occurred_on": day, "kind": "EF", "idempotency_key": key,
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
    """Un rendu fermé n'a pas de tour : le service nomme la question qu'il attend, et
    elle n'existe que si son texte exact est parti (audit du 2026-09-30, constat 4)."""
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

    # Une reprise d'envoi rejoue la même clé : ni second message, ni seconde question.
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

    # Le même gabarit une semaine plus tard désigne une autre séance.
    with store.database.transaction() as connection:
        connection.execute(conversation_questions.update().values(
            resolved_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=7)))
    assert not state.question_known("4242", question)


def test_service_lines_do_not_push_the_real_exchange_out_of_memory(store):
    """Constat 5 : les phrases d'attente occupaient les huit places du souvenir."""
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
    store.upsert_thread({"thread_key": "chaussures", "status": "ouvert",
                         "body": "Essai de la nouvelle paire à reprendre.",
                         "opened_on": dt.date(2026, 9, 20), "kind": "suspens",
                         "follow_up_on": None})
    assert "chaussures" in state.relationship_context("4242")
    without = state.relationship_context("4242", threads=False)
    assert "chaussures" not in without and "Salut" in without


def test_a_typed_transition_round_trips_and_opens_no_window(store):
    """Garde-fou n°8 porté par la base ; sans fenêtre +3/+4 depuis le 2026-10-04."""
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    night = session("w41-jeu-nuit", "2026-10-08", transition="nocturne")
    store.append_plan_revision("2026-W41", build_revision(
        1, sessions=[night], computed_at="2026-10-04T19:00:00+02:00"))
    loaded = store.load_plan("2026-W41")["revisions"][-1]["sessions"][0]
    assert loaded["transition"] == "nocturne"
    assert store.active_plans()["2026-W41"]["sessions"][0]["id"] == "w41-jeu-nuit"
    store.add_observation({
        "occurred_on": dt.date(2026, 10, 8), "kind": "EF",
        "started_at": dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.UTC),
        "idempotency_key": "strava:4141", "strava_activity_id": 4141,
        "note": "", "measurements": {}, "session_key": "2026-W41:w41-jeu-nuit"})
    assert store.active_plans()["2026-W41"]["sessions"][0]["status"] == "done"

    # Trois semaines plus tard, une nouvelle transition s'écrit : la porte juge, pas le calendrier.
    descent = session("w44-mar-desc", "2026-10-27", transition="descente")
    store.append_plan_revision("2026-W44", build_revision(
        1, sessions=[descent], computed_at="2026-10-25T19:00:00+02:00"))
    assert store.active_plans()["2026-W44"]["sessions"][0]["transition"] == "descente"


def test_a_staged_revision_with_a_transition_is_applied(store):
    """La commande de tour porte des `date` : la 1re révision typée a planté dessus."""
    from test_weekly_plan import revision as build_revision
    from test_weekly_plan import session

    turn_id = "0b7d8c1e-3a4f-4e44-9a1e-6f0c2d9b7a11"
    store.stage_command(turn_id, "plan:2026-W41:r1", "plan.revise", {
        "week": "2026-W41", "revision": build_revision(
            1, sessions=[session("w41-jeu-nuit", "2026-10-08", transition="nocturne")],
            computed_at="2026-10-04T19:00:00+02:00")})
    assert store.apply_turn_with_receipts(turn_id)[0] == 1
    assert store.load_plan("2026-W41")["revisions"][0]["sessions"][0]["transition"] == "nocturne"


# ---- Carnet : vrais droits SQL, vraie révocation, véritables frontières HTTP.

def test_notebook_archives_survive_outbox_and_are_isolated_by_owner(store):
    from scripts.storage.notebook import NotebookStore
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
    from scripts.storage.notebook import NotebookStore
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
    from scripts.storage.notebook import NotebookStore
    from scripts.storage.schema import notebook_entries
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

    from redline.notebook import NotebookService
    from redline.notebook_web import COOKIE
    from redline.web import create_app
    from scripts.storage.notebook import NotebookStore
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
        assert client.get('/carnet-api/' + path).status_code == 401
    link = archive.issue('4242')
    assert client.post('/carnet-api/connect', json={'token':link}, headers={'Origin':'https://evil.test'}).status_code == 403
    response = client.post('/carnet-api/connect', json={'token':link})
    assert response.status_code == 200
    cookie = response.headers['Set-Cookie']
    assert COOKIE in cookie and 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    assert client.get(f'/carnet-api/entries/{identity}').json['body'] == 'Jamais public'
    image = client.get(f'/carnet-api/entries/{identity}/image')
    assert image.data == b'PNG' and image.headers['Cache-Control'] == 'no-store'
    assert client.post('/carnet-api/logout', json={}).status_code == 200
    assert client.get('/carnet-api/entries').status_code == 401


def test_notebook_replay_preserves_the_card_after_a_text_failure(store, tmp_path):
    from unittest.mock import MagicMock

    from redline.delivery import DeliveryService, EnvelopeBuilder
    from redline.messenger import DeliveryResult
    from redline.notebook import NotebookService
    from redline.outbox import Outbox
    from scripts.cards import archive as descriptor
    from scripts.storage.notebook import NotebookStore
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

    from redline import clock
    from redline.notebook import NotebookService
    from redline.plans import PlanStatus
    from scripts.storage.notebook import NotebookStore
    from scripts.storage.schema import strength_report_claims
    monkeypatch.setattr(clock, 'today', lambda: dt.date(2026, 10, 2))
    repo = MagicMock()
    repo.validate_weekly_plan.return_value = PlanStatus.VALID
    repo.weekly_plan_context.return_value = json.dumps({'revision': 1, 'sessions': [{
        'id':'a', 'date':'2026-10-02', 'kind':'strength', 'workout':'renfo-a',
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
    from scripts.storage.notebook import NotebookStore, token_hash
    from scripts.storage.schema import notebook_credentials
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
        "occurred_on": day, "kind": "reveil", "source": "carnet",
        "idempotency_key": f"carnet:reveil:{day.isoformat()}",
        "measurements": {"pain": measurement}})


def _bound(store, key, day, success_if="<=1", probability=.8):
    store.upsert_prediction({
        "prediction_key": key, "domain": "corps", "statement": "trace au plus légère",
        "probability": probability, "due_on": day + dt.timedelta(days=2),
        "resolves_on": {"kind": "reveil", "date": day.isoformat(), "success_if": success_if}})


def test_a_wake_prediction_settles_itself_when_the_notebook_writes_the_trace(store):
    # Audit du 2026-10-09 : 7 prédictions « corps » confrontées sur 20. Rattachée au
    # réveil, la prédiction se solde à la saisie, sans verdict rédigé.
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
            "resolves_on": {"kind": "reveil", "date": "2026-10-20", "success_if": "<=1"}})


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
