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
