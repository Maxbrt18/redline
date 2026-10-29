"""A whole program, loaded from the fictional test vault.

These tests read `coaching/exercises/` and `coaching/workouts/` of the vault the
suite runs on, as the service would. A note broken by hand fails the suite instead
of failing on Saturday morning -- which is the entire point of having a schema.
"""

import pytest
from conftest import VAULT_ROOT

from redline.model.dose import Dose
from redline.model.notes import NoteError
from redline.model.program import EXERCISE_SCHEMA, SESSION_SCHEMA, Program, load
from redline.model.schema import SchemaError

VAULT = VAULT_ROOT


@pytest.fixture(scope="module")
def program():
    return load(VAULT)


def test_the_whole_program_loads_and_validates(program):
    assert len(program.exercises) >= 20
    assert sorted(program.sessions) == ["a", "assimilation", "b"]


def test_page_setup_is_frozen_and_load_override_is_local_to_selection(program):
    from redline.cards.strength_card import page_session
    original = program.build({"session": "a"})
    changed = program.build({"session": "a", "loads": [{"exercise": "rdl-single-leg", "kg": 6, "count": 2}]})
    def rdl(built):
        return next(exercise for exercise in page_session(built, {})["exercises"]
                    if exercise["slug"] == "rdl-single-leg")
    assert rdl(original)["load"] == {"kg": 10, "count": 1}
    assert rdl(changed)["load"] == {"kg": 6, "count": 2}
    assert rdl(original)["cue"] and rdl(original)["equipment"] == ["Haltère"]
    assert rdl(program.build({"session": "a"}))["load"] == rdl(original)["load"]
    assert all(item.cue for key in ("a", "b", "assimilation")
               for item in program.prescriptions(key))


def test_load_override_refuses_absent_or_duplicate_exercise(program):
    entry = {"exercise": "rdl-single-leg", "kg": 6, "count": 2}
    with pytest.raises(NoteError, match="missing or duplicated"):
        program.build({"session": "a", "loads": [entry, entry]})
    with pytest.raises(NoteError, match="missing or duplicated"):
        program.build({"session": "b", "loads": [entry]})


def test_every_exercise_note_renders_its_dose(program):
    """If the program ever gains a shape the algebra cannot say, it fails
    here -- not in front of the athlete."""
    for slug, note in program.exercises.items():
        assert Dose.read(note["dose"], slug).human()


@pytest.mark.parametrize("key,first,last,count", [
    # Motor-control work enters session A right after the warm-up and in the accessory
    # block. The bounds do not move: warmup-a always opens, short-foot always closes.
    ("a", "warmup-a", "plyometrics", 9),
    # Box drops are suspended: the session ends on calf-drop. When reinstated, this line
    # becomes box-drop-single/7.
    ("b", "warmup-b", "calf-drop", 5),
    ("assimilation", "calf-iso-seated-bent", "hip-abduction-light", 4),
])
def test_each_session_resolves_in_its_prescribed_order(program, key, first, last, count):
    """The order is declared non-negotiable in strength.md: glutes first, before
    fatigue spoils posture; abduction last; plyometrics at the end when
    everything is warm."""
    slugs = [p.slug for p in program.prescriptions(key)]
    assert len(slugs) == count
    assert slugs[0] == first and slugs[-1] == last


def test_the_single_session_never_goes_to_failure(program):
    """Calf loading is the first load on the injury, and going to failure slows
    recovery; calves stop 2 repetitions short."""
    assert [p.dose.measure for p in program.prescriptions("a")].count("amrap") == 0
    for slug in ("calf-raise-straight", "calf-raise-bent"):
        assert program.exercises[slug]["dose"]["measure"] == "reps"
        assert "AVANT l'échec" in program.exercises[slug]["cue"] or \
            "avant l'échec" in program.exercises[slug]["cue"]


def test_every_unilateral_dose_says_so(program):
    """"unipodal" in the name, nothing in the dose, and the page
    offered three sets instead of six -- losing the left/right gap that flags
    the calf test."""
    for slug, note in program.exercises.items():
        name = note["name"].lower()
        if any(word in name for word in ("unipodal", "unijambiste", "bulgare")):
            assert Dose.read(note["dose"], slug).per_side, f"{slug} : côté manquant"


def test_a_session_may_override_a_dose_and_only_that_session_sees_it(program):
    """Assimilation holds the mid-range calf 30 s with 30 s rest, where the
    exercise note itself asks 45 s of rest."""
    held = {p.slug: p.dose for p in program.prescriptions("assimilation")}
    assert held["calf-iso"].human() == "3 × 30 s"
    assert held["calf-iso"].rest_s == 30
    canonical = Dose.read(program.exercises["calf-iso"]["dose"])
    assert canonical.human() == "3 × 30 s" and canonical.rest_s == 45


def test_the_card_titles_and_rules_come_from_the_notes(program):
    """They used to live in a TYPES dict in chart-strength -- where a converted
    session kept a stale title, and where session A's footer contradicted the dose
    printed above it."""
    assert program.session("a")["title"] == "Renfo, séance unique"
    assert "descente & excentrique" in program.session("b")["title"]
    assert "avant l'échec" in program.session("a")["rule"]


# ---------------------------------------------------------------- refusals

def test_an_unknown_slug_names_the_ones_that_exist(program):
    # A Programme of its own: the module fixture is shared, and a test that
    # mutates it would break whichever test happens to run next.
    broken = Program(program.exercises,
                       {"a": dict(program.session("a"), exercises=["mollet-fléchi"])})
    with pytest.raises(NoteError, match="not found"):
        broken.prescriptions("a")


def test_an_unknown_session_names_the_ones_that_exist(program):
    with pytest.raises(NoteError, match="known: a, assimilation, b"):
        program.prescriptions("c")


def test_an_exercise_note_missing_its_dose_is_refused():
    with pytest.raises(SchemaError, match="field 'dose' missing"):
        EXERCISE_SCHEMA.validate({"type": "exercise", "name": "Mollet"}, "x.md")


def test_a_session_without_exercises_is_refused():
    problems = SESSION_SCHEMA.errors(
        {"type": "workout", "title": "T", "rule": "une règle assez longue",
         "exercises": [], "routine": "x"}, "renfo-x.md")
    assert problems == ["renfo-x.md: field 'exercises': length 0, minimum 1"]
