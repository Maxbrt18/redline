"""The typed prescription of a run: relief, climb and descent intents, structure."""

import pytest
from test_weekly_plan import plan, revision, session

from redline.model import prescription, weekly_plan
from redline.model.schema import SchemaError
from redline.model.vocabulary import ClimbIntent, SessionStructure


def test_absent_fields_take_their_defaults():
    easy, hills = session(), session("w32-mer-cotes", workout="hills")
    assert prescription.climb_intent(easy) == ClimbIntent.EASY
    assert prescription.climb_intent(hills) == ClimbIntent.HARD
    assert prescription.structure(easy) == SessionStructure.STEADY
    assert not prescription.has_efforts(easy) and prescription.has_efforts(hills)
    assert prescription.has_efforts(session(structure="efforts"))


def test_the_plan_schema_reads_the_typed_fields():
    weekly_plan.validate(plan([session(dplus_cap_m=250, dminus_cap_m=250, dplus_target_m=200,
                                       climb_intent="run_slow", descent_intent="held")]))
    with pytest.raises(SchemaError, match="climb_intent"):
        weekly_plan.validate(plan([session(climb_intent="walk")]))


@pytest.mark.parametrize("overrides, message", [
    ({"dplus_cap_m": 200, "dplus_target_m": 300}, "D+ target 300 m above the cap 200 m"),
    ({"climb_intent": "hard"}, "hard climbs belong to a hill session"),
    ({"climb_intent": "power_hike"}, "power_hike_from_pct"),
    ({"power_hike_from_pct": 10}, "power_hike_from_pct"),
    ({"structure": "efforts", "hr_zone": "Z2", "hr_cap_bpm": 157},
     "no cap on the session's mean HR over prescribed efforts"),
])
def test_a_contradictory_prescription_is_refused(overrides, message):
    problems = prescription.errors(None, revision(sessions=[session(**overrides)]))
    assert any(message in problem for problem in problems), problems


def test_coherent_prescriptions_pass():
    sessions = [
        session(climb_intent="power_hike", power_hike_from_pct=10, dplus_cap_m=400),
        session("w32-mer-cotes", workout="hills", climb_intent="hard"),
        session("w32-jeu-ef", structure="efforts"),
    ]
    assert prescription.errors(None, revision(sessions=sessions)) == []


def test_a_hill_session_declares_no_other_climb_intent_and_others_no_structure():
    hills = session("w32-mer-cotes", workout="hills", climb_intent="run_slow")
    threshold = session("w32-mer-seuil", workout="threshold", structure="efforts")
    problems = prescription.errors(None, revision(sessions=[hills, threshold]))
    assert any("a hill session's climbs are hard" in problem for problem in problems)
    assert any("only an easy or long run declares a structure" in problem
               for problem in problems)


def test_only_runs_carry_intents_and_only_feet_carry_relief():
    bike = session("w32-sam-velo", workout="bike", dplus_cap_m=300, climb_intent="easy")
    hike = session("w32-dim-rando", workout="hike", dplus_cap_m=600)
    problems = prescription.errors(None, revision(sessions=[bike, hike]))
    assert any("w32-sam-velo.dplus_cap_m: only a run or a hike" in p for p in problems)
    assert any("w32-sam-velo.climb_intent: only a run" in p for p in problems)
    assert not any("w32-dim-rando" in p for p in problems)


def test_a_mean_hr_cap_the_previous_revision_held_over_efforts_is_carried():
    """A week planned before the rule stays revisable; a new cap is still refused."""
    held = session(structure="efforts", hr_zone="Z2", hr_cap_bpm=157)
    previous = revision(sessions=[held])
    assert prescription.errors(previous, revision(2, sessions=[dict(held)])) == []
    lowered = dict(held, hr_cap_bpm=150)
    assert prescription.errors(previous, revision(2, sessions=[lowered]))


def test_the_readout_shows_only_what_the_coach_declared():
    assert prescription.readout(session()) == []
    assert prescription.readout(session(dplus_cap_m=250, dminus_cap_m=250)) == [
        "D+ et D- 250 m au plus"]
    hilly = session(dplus_target_m=350, dplus_cap_m=400, dminus_cap_m=300,
                    climb_intent="power_hike", power_hike_from_pct=10, descent_intent="held")
    assert prescription.readout(hilly) == [
        "D+ ~350 m, 400 m au plus, D- 300 m au plus",
        "marche vite dès 10 %, cours facile en dessous", "descentes retenues ou marchées"]


def test_every_rendering_carries_the_typed_prescription():
    """The text message, journal and card read `details_of`; the notebook its own parts."""
    from redline.service import notebook_board

    pyramid = session(structure="efforts", climb_intent="run_slow", dplus_cap_m=150)
    details = weekly_plan.details_of(pyramid)
    assert "D+ 150 m au plus" in details
    assert any(part.startswith("montées toutes en courant") for part in details)
    assert any(part.startswith("efforts prescrits") for part in details)
    assert "D+ 150 m au plus" in notebook_board._planned(pyramid)


# ------------------------------------------------------------------ the dose of repetitions


def test_a_session_of_repetitions_types_its_dose_and_reads_it():
    threshold = session("w32-jeu-seuil", workout="threshold", reps=5, rep_seconds=420,
                        recovery_seconds=120, rep_pace_s=275)
    weekly_plan.validate(plan([threshold]))
    assert prescription.errors(None, revision(sessions=[threshold])) == []
    assert prescription.dose_of(threshold) == "5 × 7 min, récup 2 min, à 4:35/km"
    assert prescription.readout(threshold)[0] == "5 × 7 min, récup 2 min, à 4:35/km"
    short = session("w32-jeu-seuil", workout="threshold", reps=8, rep_seconds=90)
    assert prescription.dose_of(short) == "8 × 1 min 30"
    assert prescription.dose_of(session(workout="strides", reps=6, rep_seconds=20)) == "6 × 20 s"


@pytest.mark.parametrize("workout, overrides, message", [
    ("easy", {"reps": 5, "rep_seconds": 60}, "only a session of repetitions types its dose"),
    ("threshold", {"reps": 5}, "reps and rep_seconds go together"),
    ("threshold", {"recovery_seconds": 60}, "needs reps and rep_seconds"),
    ("hills", {"reps": 6, "rep_seconds": 60, "rep_pace_s": 300}, "its pace is the grade's"),
    ("threshold", {"reps": 5, "rep_seconds": 60, "override_reason": "tolerated_more"},
     "override_reason and override_prediction go together"),
    ("threshold", {"override_reason": "race_demand", "override_prediction": "w11-seuil"},
     "needs reps and rep_seconds"),
])
def test_a_contradictory_dose_is_refused(workout, overrides, message):
    found = prescription.errors(None, revision(sessions=[session(workout=workout, **overrides)]))
    assert any(message in problem for problem in found), found
