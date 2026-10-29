"""Dress rehearsal: the REAL coach commands, in the REAL order.

Unit tests check each piece in isolation, and they all passed while the bot was unusable
because a path the coach could not take failed and destroyed its turn. This file does not test
one piece: it replays the SEQUENCES of a coaching day, with the scripts called the way the
coach calls them, across the same turn boundary.

Each test carries the lived scenario it replays.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest
import sqlalchemy as sa
from conftest import ENGINE_ROOT, cli_command

from redline.storage import Database, OperationalStore
from redline.storage.schema import metadata

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL absent")


@pytest.fixture(autouse=True)
def clean_database():
    database = Database(URL, application_name="pytest-repetition")
    with database.engine.begin() as connection:
        names = ", ".join(f"redline.{table.name}" for table in metadata.sorted_tables)
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
        connection.execute(sa.text("INSERT INTO redline.athletes(id) VALUES ('athlete')"))
        # The monitored zone of the fictitious instance, as the service syncs it.
        connection.execute(sa.text(
            "INSERT INTO redline.pain_zones(athlete_id, slug, label) VALUES ('athlete', 'knee', 'Genou')"))
    yield
    database.dispose()


@pytest.fixture
def store():
    return OperationalStore(Database(URL, application_name="pytest-repetition"))


def coach(command: str, *arguments: str, turn: str = "") -> subprocess.CompletedProcess:
    """A coach command (`redline week-plan`, as the instructions spell it),
    launched exactly the way the service launches it."""
    environment = {**os.environ, "DATABASE_URL": URL}
    if turn:
        environment["REDLINE_TURN_ID"] = turn
    name = command.removeprefix("scripts/").removesuffix(".py")
    return subprocess.run([*cli_command(name), *arguments], cwd=ENGINE_ROOT, env=environment,
                          capture_output=True, text=True, check=False)


def succeeds(process: subprocess.CompletedProcess, cue: str) -> subprocess.CompletedProcess:
    assert process.returncode == 0, f"{cue} a échoué : {process.stderr or process.stdout}"
    return process


def revision_json(number: int) -> str:
    """A valid revision of a build week, as the coach would write it."""
    import json
    return json.dumps({
        "revision": number,
        "computed_at": "2026-08-14T09:00:00+02:00",
        "reason": "répétition générale",
        "evidence": ["registre W33"],
        "constraints": [],
        "volume_target_km": 30.0,
        "sessions": [{
            "id": "w33-ven-ef", "title": "EF", "date": "2026-08-14", "kind": "run",
            "workout": "easy", "status": "planned", "distance_target_km": 7.0,
            "distance_cap_km": 9.0, "rpe_min": 2, "rpe_max": 3,
            "demanding": False, "sources": ["[[current-plan]]"],
        }],
        "gates": [],
    })


def poser_le_plan(store, number: int = 1) -> None:
    tour = str(uuid.uuid4())
    process = subprocess.run(
        [*cli_command("week-plan"), "revise", "--week", "2026-W33"],
        cwd=ENGINE_ROOT, env={**os.environ, "DATABASE_URL": URL, "REDLINE_TURN_ID": tour},
        input=revision_json(number), capture_output=True, text=True, check=False)
    succeeds(process, "revise")
    store.apply_turn_with_receipts(tour)[0]


# --------------------------------------------------------------------- sequences

def test_a_complete_debrief_crosses_the_turn_boundary(store):
    """A full morning debrief. The coach reads the output and records it; reconciliation
    moves the cycle to ``completed`` without copying the execution into the plan."""
    poser_le_plan(store)
    tour = str(uuid.uuid4())

    succeeds(coach("redline ledger-add", "--date", "2026-08-14", "--type", "easy",
                  "--km", "6.969", "--time", "43:05", "--pace", "6:11",
                  "--hr-avg", "151.3", "--cadence", "165", "--dplus", "64.6",
                  "--strava", "90000000002", "--start-local", "2026-08-14T07:15:00",
                  "--session-key", "2026-W33:w33-ven-ef", turn=tour), "ledger-add")

    assert store.apply_turn_with_receipts(tour)[0] == 1
    assert store.load_plan("2026-W33")["revisions"][-1]["sessions"][0]["status"] == "done"
    assert succeeds(coach("redline week-plan", "check", "--week", "2026-W33"),
                   "check").returncode == 0


def test_giving_an_rpe_never_breaks_anything(store):
    """Giving a perceived-effort figure and a pain peak is the most ordinary coaching
    gesture: it must go through and leave the program prescribable."""
    # The session predates the plan: its persistence watermark has therefore already seen it.
    # This is the starting point of the gesture under test.
    session_turn = str(uuid.uuid4())
    succeeds(coach("redline ledger-add", "--date", "2026-08-14", "--type", "bike",
                  "--km", "36.9", "--time", "1:42:57",
                  "--idempotency-key", "messenger:2026-08-14:velo", turn=session_turn),
            "ledger-add")
    store.apply_turn_with_receipts(session_turn)[0]
    poser_le_plan(store)
    assert coach("redline week-plan", "check", "--week", "2026-W33").returncode == 0

    # Hours later, the athlete gives their perceived effort. NOTHING must move on the plan.
    tour = str(uuid.uuid4())
    succeeds(coach("redline ledger-complete", "--date", "2026-08-14", "--type", "bike",
                  "--metric", "rpe", "--value", "7.5", turn=tour), "ledger-complete")
    store.apply_turn_with_receipts(tour)[0]

    plan = coach("redline week-plan", "check", "--week", "2026-W33")
    assert plan.returncode == 0, (
        "compléter une mesure ne doit pas périmer le programme : "
        + (plan.stderr or plan.stdout))


def test_completing_without_knowing_yesterdays_invented_key(store):
    """The coach does not remember the key of a row written in a dead session.
    It has the date; the date must be enough. Otherwise it guesses, and guesses wrong."""
    poser_le_plan(store)
    session_turn = str(uuid.uuid4())
    succeeds(coach("redline ledger-add", "--date", "2026-08-14", "--type", "bike",
                  "--km", "36.9", "--time", "1:42:57",
                  "--idempotency-key", "messenger:2026-08-14:velo-du-padre",
                  turn=session_turn), "ledger-add sans id Strava")
    store.apply_turn_with_receipts(session_turn)[0]

    lecture = succeeds(coach("redline fact-read", "observations", "--limit", "5"),
                      "fact-read")
    assert "clé=messenger:2026-08-14:velo-du-padre" in lecture.stdout, (
        "la clé exigée par ledger-complete doit être publiée par le lecteur")

    tour_lien = str(uuid.uuid4())
    succeeds(coach(
        "redline ledger-link", "--key", "messenger:2026-08-14:velo-du-padre",
        "--strava", "90000000001", turn=tour_lien), "rattachement à Strava")
    assert store.activity_recorded(90000000001) is False
    store.apply_turn_with_receipts(tour_lien)[0]
    assert store.activity_recorded(90000000001) is True

    tour = str(uuid.uuid4())
    succeeds(coach("redline ledger-complete", "--date", "2026-08-14", "--type", "bike",
                  "--metric", "pain", "--value", "0", turn=tour), "complétion par date")
    store.apply_turn_with_receipts(tour)[0]


def test_a_wrong_key_fails_at_once_not_at_the_boundary(store):
    """A faulty command fails WHILE the coach can still correct itself. At the turn
    boundary it has already written its reply and everything is lost."""
    poser_le_plan(store)
    process = coach("redline ledger-complete", "--key", "strava:90000000001",
                    "--metric", "rpe", "--value", "7", turn=str(uuid.uuid4()))
    assert process.returncode != 0, "une clé inexistante doit échouer immédiatement"
    assert "unknown observation" in (process.stderr + process.stdout)


def test_the_daily_state_does_not_make_the_plan_stale(store):
    """Saying one slept badly feeds the gauge; it does not make the program wrong."""
    poser_le_plan(store)
    tour = str(uuid.uuid4())
    succeeds(coach("redline fact-write", "daily", "--date", "2026-08-14",
                  "--sleep", "poor", "--life-load", "high", "--desire", "normal",
                  turn=tour), "fact-write daily")
    store.apply_turn_with_receipts(tour)[0]

    assert succeeds(coach("redline week-plan", "check", "--week", "2026-W33"),
                   "check").returncode == 0


def test_a_stale_plan_stays_readable_and_says_so(store):
    """The CLI and the service must agree: stale is not invalid. The program
    prints, and the exit code names the remedy."""
    poser_le_plan(store)
    tour = str(uuid.uuid4())
    succeeds(coach("redline ledger-add", "--date", "2026-08-14", "--type", "easy",
                  "--km", "7", "--time", "43:00",
                  "--idempotency-key", "messenger:2026-08-14:ef", turn=tour),
            "ledger-add sans révision")
    store.apply_turn_with_receipts(tour)[0]

    process = coach("redline week-plan", "show", "--week", "2026-W33")
    assert process.returncode == 3, "périmé = 3, jamais 1"
    assert "w33-ven-ef" in process.stdout, "le programme doit rester lisible"
    assert "révision" in process.stderr


# ------------------------------------------------- proactive triggers

def sequence(*cues: tuple[str, list[str]]) -> None:
    """Play a series of commands and name the one that fails.

    A proactive trigger is not one command but a CHAIN: the morning message reads the plan,
    the threads, the deadlines, the cockpit, then writes its trace. Testing them one by one
    misses what breaks between two.
    """
    for name_field, arguments in cues:
        succeeds(coach(*arguments), name_field)


def test_the_morning_note_plays_its_whole_sequence(store):
    """What the `morning` trigger orders, in the order it orders it."""
    poser_le_plan(store)
    sequence(
        ("plan du jour", ["redline week-plan", "show", "--week", "2026-W33"]),
        ("validation", ["redline week-plan", "check", "--week", "2026-W33"]),
        ("registre", ["redline fact-read", "observations", "--limit", "10"]),
        ("fils ouverts", ["redline fact-read", "threads"]),
        ("échéances", ["redline fact-read", "deadlines"]),
        ("cockpit", ["redline fact-read", "capabilities"]),
        ("journal proactif", ["redline fact-read", "proactive"]),
    )


def test_the_sunday_review_plays_its_whole_sequence(store):
    """What the `weekly` trigger orders. It touches the most scripts, so it is the one
    that breaks most quietly -- once a week, on Sunday."""
    # A Sunday without any session does not exist: `signals` refuses an empty ledger, and
    # the rehearsal must play a lived week, not a theoretical one.
    session_turn = str(uuid.uuid4())
    succeeds(coach("redline ledger-add", "--date", "2026-08-14", "--type", "easy",
                  "--km", "7", "--time", "43:00", "--pace", "6:08",
                  "--rpe", "3", "--pain", "1", "--enjoyment", "7",
                  "--idempotency-key", "messenger:2026-08-14:ef-bilan",
                  turn=session_turn), "séance de la semaine")
    store.apply_turn_with_receipts(session_turn)[0]
    poser_le_plan(store)
    sequence(
        ("agrégats", ["redline ledger", "--week", "2026-W33"]),
        ("tendance douleur", ["redline ledger", "--pain-trend", "3"]),
        ("signaux faibles", ["redline signals"]),
        ("adhérence du plan", ["redline week-plan", "check", "--week", "2026-W33"]),
        ("cockpit", ["redline fact-read", "capabilities"]),
    )
    tour = str(uuid.uuid4())
    succeeds(coach("redline fact-write", "deadline", "--key", "repetition-hebdo",
                  "--due-on", "2026-08-20", "--item", "échéance de la répétition",
                  "--fact-owner", "coaching/decisions.md", "--status", "todo",
                  turn=tour), "échéance")
    store.apply_turn_with_receipts(tour)[0]
    assert "échéance de la répétition" in succeeds(
        coach("redline fact-read", "deadlines"), "relecture").stdout


def test_the_strava_debrief_plays_its_whole_sequence(store):
    """Record and reconcile with the cycle -- the full chain of a debrief."""
    poser_le_plan(store)
    tour = str(uuid.uuid4())
    succeeds(coach("redline ledger-add", "--date", "2026-08-14", "--type", "easy",
                  "--km", "6.969", "--time", "43:05", "--pace", "6:11",
                  "--hr-avg", "151.3", "--cadence", "165", "--dplus", "64.6",
                  "--dminus", "65", "--wbgt", "23", "--strava", "90000000002",
                  "--start-local", "2026-08-14T07:15:00",
                  "--session-key", "2026-W33:w33-ven-ef",
                  turn=tour), "ledger-add")
    assert store.apply_turn_with_receipts(tour)[0] == 1
    assert store.load_plan("2026-W33")["revisions"][-1]["sessions"][0]["status"] == "done"
    assert coach("redline week-plan", "check", "--week", "2026-W33").returncode == 0
