"""La carte des répétitions, construite par le service depuis les tours Strava.

15/10 : le premier fractionné avec Redline est parti sans carte, laissée au choix d'un
débrief à effort bas. Elle ne dépend plus du modèle.
"""

from scripts.model import debrief

SEUIL = {"workout": "seuil", "title": "Seuil doux en côte, calibration"}


def _laps(*rows):
    return [{"temps": t, "distance_km": d, "fc_moy": h} for t, d, h in rows]


# Les tours réels de l'activité 90000000005 (15/10) : échauffement, 4 x 3 min en
# côte, récups en trottinant, retour au calme plus rapide que les récups.
SEUIL_DU_15 = _laps(
    ("17:42", 2.453, 155.7), ("3:04", 0.654, 178.6), ("3:26", 0.498, 159.6),
    ("3:02", 0.647, 178.0), ("4:43", 0.683, 158.7), ("3:01", 0.652, 178.7),
    ("4:49", 0.675, 154.2), ("3:06", 0.667, 177.4), ("4:24", 0.664, 154.1),
    ("9:57", 1.621, 154.5))


def test_the_threshold_of_the_15th_gives_four_reps_and_three_recoveries():
    card = debrief.quality_card(SEUIL_DU_15, SEUIL, "15/10")
    assert card["title"] == "Seuil doux en côte, calibration"
    assert card["date"] == "15/10"
    assert [(rep["label"], rep["time_s"], rep["dist_m"]) for rep in card["reps"]] == [
        ("R1", 184.0, 654.0), ("R2", 182.0, 647.0), ("R3", 181.0, 652.0),
        ("R4", 186.0, 667.0)]
    assert [rep.get("recovery") for rep in card["reps"]] == [
        {"time_s": 206.0, "dist_m": 498.0}, {"time_s": 283.0, "dist_m": 683.0},
        {"time_s": 289.0, "dist_m": 675.0}, None]
    assert card["reps"][0]["hr"] == 179.0


def test_auto_laps_of_a_continuous_run_are_not_reps():
    ef = _laps(*[("6:05", 1.0, 148)] * 6, ("6:20", 1.0, 150))
    assert debrief.workout_reps(ef) is None


def test_only_a_quality_session_gets_a_card():
    assert debrief.quality_card(SEUIL_DU_15, {"workout": "EF"}) is None
    assert debrief.quality_card(SEUIL_DU_15, None) is None
    assert debrief.quality_card(SEUIL_DU_15, {"workout": "SL", "transition": "qualite"})


def test_contiguous_fast_laps_are_one_rep_and_a_standing_recovery_is_slow():
    laps = _laps(("10:00", 1.6, 140), ("4:00", 1.0, 170), ("4:00", 1.0, 172),
                 ("2:00", 0.0, 130), ("4:05", 1.0, 173), ("8:00", 1.3, 140))
    reps = debrief.workout_reps(laps)
    assert [(rep["time_s"], rep["dist_m"]) for rep in reps] == [(480.0, 2000.0),
                                                                  (245.0, 1000.0)]
    assert reps[0]["recovery"] == {"time_s": 120.0, "dist_m": 0.0}


def test_a_single_effort_or_no_laps_gives_no_card():
    assert debrief.workout_reps([]) is None
    assert debrief.workout_reps(_laps(("10:00", 1.6, 140), ("4:00", 1.0, 170),
                                      ("8:00", 1.3, 140))) is None


# ------------------------------------------------------------------ le bot

import time  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from conftest import FakeAgent  # noqa: E402

from redline.agent import RunResult  # noqa: E402
from redline.outbox import Outbox  # noqa: E402
from redline.proactive import ProactiveJob, ProactiveScheduler  # noqa: E402
from redline.vault import PersistenceResult  # noqa: E402

CARD = debrief.quality_card(SEUIL_DU_15, SEUIL, "15/10")


def _bot(make_bot, tmp_path):
    outbox = Outbox(tmp_path / "outbox")
    repo = MagicMock()
    repo.sync.return_value = False
    repo.is_clean.return_value = True
    repo.ensure_clean.return_value = []
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.ledger_cells.return_value = ([], [])
    repo.drain_alerts.return_value = []
    repo.head.return_value = "0123456789abcdef"
    repo.activity_recorded.return_value = True

    def render(card, activity_id):
        outbox.target(f"workout-{activity_id}.png").write_bytes(b"\x89PNG")
        return True

    repo.render_workout_card.side_effect = render
    agent = FakeAgent(result=RunResult(full_text="Quatre montées régulières, bravo."))
    return make_bot(agent=agent, repo=repo, outbox=outbox), agent, repo


def test_the_job_carries_the_card_and_the_quality_flag_from_the_snapshot():
    job = ProactiveScheduler._job("4242", {
        "id": "strava:1", "kind": "strava_debrief", "detail": "1",
        "snapshot": {"activity": {"id": 1}, "qualite": True, "repetitions": CARD}})
    assert job.quality and job.workout_card == CARD


def test_a_quality_debrief_sends_its_card_without_the_model(make_bot, store, tmp_path):
    bot, agent, repo = _bot(make_bot, tmp_path)
    store.proactive.enqueue_job("strava:1", "strava_debrief", "90000000005",
                                time.time() - 1)
    record = store.proactive.claim_due_job(time.time())

    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="strava_debrief", prompt="débriefe", job_id=record["id"],
        detail=record["detail"], workout_card=CARD, quality=True))

    repo.render_workout_card.assert_called_once_with(CARD, "90000000005")
    assert bot._client.send_image.called
    assert "chart-workout" not in agent.calls[0]["message"]


def test_a_quality_debrief_runs_on_the_judgement_route(make_bot, tmp_path):
    bot, agent, _ = _bot(make_bot, tmp_path)
    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="p", quality=True))
    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief", prompt="p"))
    # Codex dans les tests : N2 = Sol, N1 = Luna.
    assert [call["model"] for call in agent.calls] == ["gpt-6-sol", "gpt-6-luna"]


def test_an_easy_run_debrief_renders_no_card(make_bot, tmp_path):
    bot, _, repo = _bot(make_bot, tmp_path)
    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief", prompt="p",
                                        detail="1"))
    repo.render_workout_card.assert_not_called()
