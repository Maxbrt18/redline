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
        connection.execute(sa.text("INSERT INTO redline.athletes(id) VALUES ('sam')"))
        # The monitored zone of the fictitious instance, as the service syncs it.
        connection.execute(sa.text(
            "INSERT INTO redline.pain_zones(athlete_id, slug, label) VALUES ('sam', 'genou', 'Genou')"))
    yield
    database.dispose()


@pytest.fixture
def store():
    return OperationalStore(Database(URL, application_name="pytest-repetition"))


def coach(command: str, *arguments: str, turn: str = "") -> subprocess.CompletedProcess:
    """A coach command (`scripts/week-plan.py`, as the instructions spell it),
    launched exactly the way the service launches it."""
    environment = {**os.environ, "DATABASE_URL": URL}
    if turn:
        environment["REDLINE_TURN_ID"] = turn
    name = command.removeprefix("scripts/").removesuffix(".py")
    return subprocess.run([*cli_command(name), *arguments], cwd=ENGINE_ROOT, env=environment,
                          capture_output=True, text=True, check=False)


def reussit(process: subprocess.CompletedProcess, geste: str) -> subprocess.CompletedProcess:
    assert process.returncode == 0, f"{geste} a échoué : {process.stderr or process.stdout}"
    return process


def revision_json(numero: int) -> str:
    """A valid revision of a build week, as the coach would write it."""
    import json
    return json.dumps({
        "revision": numero,
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


def poser_le_plan(store, numero: int = 1) -> None:
    tour = str(uuid.uuid4())
    process = subprocess.run(
        [*cli_command("week-plan"), "revise", "--week", "2026-W33"],
        cwd=ENGINE_ROOT, env={**os.environ, "DATABASE_URL": URL, "REDLINE_TURN_ID": tour},
        input=revision_json(numero), capture_output=True, text=True, check=False)
    reussit(process, "revise")
    store.apply_turn_with_receipts(tour)[0]


# --------------------------------------------------------------------- sequences

def test_un_debrief_complet_passe_la_frontiere_du_tour(store):
    """A full morning debrief. The coach reads the output and records it; reconciliation
    moves the cycle to ``completed`` without copying the execution into the plan."""
    poser_le_plan(store)
    tour = str(uuid.uuid4())

    reussit(coach("scripts/ledger-add.py", "--date", "2026-08-14", "--type", "easy",
                  "--km", "6.969", "--temps", "43:05", "--allure", "6:11",
                  "--fcmoy", "151.3", "--cad", "165", "--dplus", "64.6",
                  "--strava", "90000000002", "--start-local", "2026-08-14T07:15:00",
                  "--session-key", "2026-W33:w33-ven-ef", turn=tour), "ledger-add")

    assert store.apply_turn_with_receipts(tour)[0] == 1
    assert store.load_plan("2026-W33")["revisions"][-1]["sessions"][0]["status"] == "done"
    assert reussit(coach("scripts/week-plan.py", "check", "--week", "2026-W33"),
                   "check").returncode == 0


def test_donner_un_rpe_ne_casse_jamais_rien(store):
    """Giving a perceived-effort figure and a pain peak is the most ordinary coaching
    gesture: it must go through and leave the program prescribable."""
    # The session predates the plan: its persistence watermark has therefore already seen it.
    # This is the starting point of the gesture under test.
    tour_seance = str(uuid.uuid4())
    reussit(coach("scripts/ledger-add.py", "--date", "2026-08-14", "--type", "bike",
                  "--km", "36.9", "--temps", "1:42:57",
                  "--idempotency-key", "messenger:2026-08-14:velo", turn=tour_seance),
            "ledger-add")
    store.apply_turn_with_receipts(tour_seance)[0]
    poser_le_plan(store)
    assert coach("scripts/week-plan.py", "check", "--week", "2026-W33").returncode == 0

    # Hours later, the athlete gives their perceived effort. NOTHING must move on the plan.
    tour = str(uuid.uuid4())
    reussit(coach("scripts/ledger-complete.py", "--date", "2026-08-14", "--type", "bike",
                  "--metric", "rpe", "--value", "7.5", turn=tour), "ledger-complete")
    store.apply_turn_with_receipts(tour)[0]

    plan = coach("scripts/week-plan.py", "check", "--week", "2026-W33")
    assert plan.returncode == 0, (
        "compléter une mesure ne doit pas périmer le programme : "
        + (plan.stderr or plan.stdout))


def test_completer_sans_connaitre_la_cle_inventee_hier(store):
    """The coach does not remember the key of a row written in a dead session.
    It has the date; the date must be enough. Otherwise it guesses, and guesses wrong."""
    poser_le_plan(store)
    tour_seance = str(uuid.uuid4())
    reussit(coach("scripts/ledger-add.py", "--date", "2026-08-14", "--type", "bike",
                  "--km", "36.9", "--temps", "1:42:57",
                  "--idempotency-key", "messenger:2026-08-14:velo-du-padre",
                  turn=tour_seance), "ledger-add sans id Strava")
    store.apply_turn_with_receipts(tour_seance)[0]

    lecture = reussit(coach("scripts/fact-read.py", "observations", "--limit", "5"),
                      "fact-read")
    assert "clé=messenger:2026-08-14:velo-du-padre" in lecture.stdout, (
        "la clé exigée par ledger-complete doit être publiée par le lecteur")

    tour_lien = str(uuid.uuid4())
    reussit(coach(
        "scripts/ledger-link.py", "--key", "messenger:2026-08-14:velo-du-padre",
        "--strava", "90000000001", turn=tour_lien), "rattachement à Strava")
    assert store.activity_recorded(90000000001) is False
    store.apply_turn_with_receipts(tour_lien)[0]
    assert store.activity_recorded(90000000001) is True

    tour = str(uuid.uuid4())
    reussit(coach("scripts/ledger-complete.py", "--date", "2026-08-14", "--type", "bike",
                  "--metric", "pain", "--value", "0", turn=tour), "complétion par date")
    store.apply_turn_with_receipts(tour)[0]


def test_une_cle_fausse_echoue_tout_de_suite_pas_a_la_frontiere(store):
    """A faulty command fails WHILE the coach can still correct itself. At the turn
    boundary it has already written its reply and everything is lost."""
    poser_le_plan(store)
    process = coach("scripts/ledger-complete.py", "--key", "strava:90000000001",
                    "--metric", "rpe", "--value", "7", turn=str(uuid.uuid4()))
    assert process.returncode != 0, "une clé inexistante doit échouer immédiatement"
    assert "observation inconnue" in (process.stderr + process.stdout)


def test_l_etat_du_jour_ne_perime_pas_le_programme(store):
    """Saying one slept badly feeds the gauge; it does not make the program wrong."""
    poser_le_plan(store)
    tour = str(uuid.uuid4())
    reussit(coach("scripts/fact-write.py", "daily", "--date", "2026-08-14",
                  "--sleep", "mauvaise", "--life-load", "elevee", "--desire", "normale",
                  turn=tour), "fact-write daily")
    store.apply_turn_with_receipts(tour)[0]

    assert reussit(coach("scripts/week-plan.py", "check", "--week", "2026-W33"),
                   "check").returncode == 0


def test_un_programme_perime_reste_lisible_et_le_dit(store):
    """The CLI and the service must agree: stale is not invalid. The program
    prints, and the exit code names the remedy."""
    poser_le_plan(store)
    tour = str(uuid.uuid4())
    reussit(coach("scripts/ledger-add.py", "--date", "2026-08-14", "--type", "easy",
                  "--km", "7", "--temps", "43:00",
                  "--idempotency-key", "messenger:2026-08-14:ef", turn=tour),
            "ledger-add sans révision")
    store.apply_turn_with_receipts(tour)[0]

    process = coach("scripts/week-plan.py", "show", "--week", "2026-W33")
    assert process.returncode == 3, "périmé = 3, jamais 1"
    assert "w33-ven-ef" in process.stdout, "le programme doit rester lisible"
    assert "révision" in process.stderr


# ------------------------------------------------- proactive triggers

def sequence(*gestes: tuple[str, list[str]]) -> None:
    """Play a series of commands and name the one that fails.

    A proactive trigger is not one command but a CHAIN: the morning message reads the plan,
    the threads, the deadlines, the cockpit, then writes its trace. Testing them one by one
    misses what breaks between two.
    """
    for nom, arguments in gestes:
        reussit(coach(*arguments), nom)


def test_le_mot_du_matin_joue_toute_sa_sequence(store):
    """What the `morning` trigger orders, in the order it orders it."""
    poser_le_plan(store)
    sequence(
        ("plan du jour", ["scripts/week-plan.py", "show", "--week", "2026-W33"]),
        ("validation", ["scripts/week-plan.py", "check", "--week", "2026-W33"]),
        ("registre", ["scripts/fact-read.py", "observations", "--limit", "10"]),
        ("fils ouverts", ["scripts/fact-read.py", "threads"]),
        ("échéances", ["scripts/fact-read.py", "deadlines"]),
        ("cockpit", ["scripts/fact-read.py", "capabilities"]),
        ("journal proactif", ["scripts/fact-read.py", "proactive"]),
    )


def test_le_bilan_du_dimanche_joue_toute_sa_sequence(store):
    """What the `weekly` trigger orders. It touches the most scripts, so it is the one
    that breaks most quietly -- once a week, on Sunday."""
    # A Sunday without any session does not exist: `signals.py` refuses an empty ledger, and
    # the rehearsal must play a lived week, not a theoretical one.
    tour_seance = str(uuid.uuid4())
    reussit(coach("scripts/ledger-add.py", "--date", "2026-08-14", "--type", "easy",
                  "--km", "7", "--temps", "43:00", "--allure", "6:08",
                  "--rpe", "3", "--dlr", "1", "--plaisir", "7",
                  "--idempotency-key", "messenger:2026-08-14:ef-bilan",
                  turn=tour_seance), "séance de la semaine")
    store.apply_turn_with_receipts(tour_seance)[0]
    poser_le_plan(store)
    sequence(
        ("agrégats", ["scripts/ledger.py", "--semaine", "2026-W33"]),
        ("tendance douleur", ["scripts/ledger.py", "--tendance-douleur", "3"]),
        ("signaux faibles", ["scripts/signals.py"]),
        ("adhérence du plan", ["scripts/week-plan.py", "check", "--week", "2026-W33"]),
        ("cockpit", ["scripts/fact-read.py", "capabilities"]),
    )
    tour = str(uuid.uuid4())
    reussit(coach("scripts/fact-write.py", "deadline", "--key", "repetition-hebdo",
                  "--due-on", "2026-08-20", "--item", "échéance de la répétition",
                  "--fact-owner", "coaching/decisions.md", "--status", "todo",
                  turn=tour), "échéance")
    store.apply_turn_with_receipts(tour)[0]
    assert "échéance de la répétition" in reussit(
        coach("scripts/fact-read.py", "deadlines"), "relecture").stdout


def test_le_debrief_strava_joue_toute_sa_sequence(store):
    """Record and reconcile with the cycle -- the full chain of a debrief."""
    poser_le_plan(store)
    tour = str(uuid.uuid4())
    reussit(coach("scripts/ledger-add.py", "--date", "2026-08-14", "--type", "easy",
                  "--km", "6.969", "--temps", "43:05", "--allure", "6:11",
                  "--fcmoy", "151.3", "--cad", "165", "--dplus", "64.6",
                  "--dmoins", "65", "--wbgt", "23", "--strava", "90000000002",
                  "--start-local", "2026-08-14T07:15:00",
                  "--session-key", "2026-W33:w33-ven-ef",
                  turn=tour), "ledger-add")
    assert store.apply_turn_with_receipts(tour)[0] == 1
    assert store.load_plan("2026-W33")["revisions"][-1]["sessions"][0]["status"] == "done"
    assert coach("scripts/week-plan.py", "check", "--week", "2026-W33").returncode == 0
