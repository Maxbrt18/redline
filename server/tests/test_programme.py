"""The real programme, loaded from the real vault.

Not a fixture: these tests read `coaching/exercises/` and `coaching/workouts/`
as they stand. A note broken by hand fails the suite instead of failing on
Saturday morning -- which is the entire point of having a schema.
"""

import pytest

from redline.config import SERVER_DIR
from scripts.model.dose import Dose
from scripts.model.notes import NoteError
from scripts.model.programme import EXERCISE_SCHEMA, SESSION_SCHEMA, Programme, load
from scripts.model.schema import SchemaError

VAULT = SERVER_DIR.parent


@pytest.fixture(scope="module")
def programme():
    return load(VAULT)


def test_the_whole_programme_loads_and_validates(programme):
    assert len(programme.exercises) >= 20
    assert sorted(programme.sessions) == ["a", "assimilation", "b"]


def test_page_setup_is_frozen_and_load_override_is_local_to_selection(programme):
    from scripts.cards.strength_card import page_session
    original = programme.build({"session": "a"})
    changed = programme.build({"session": "a", "loads": [{"exercise": "rdl-single-leg", "kg": 6, "count": 2}]})
    def rdl(built):
        return next(exercise for exercise in page_session(built, {})["exercises"]
                    if exercise["slug"] == "rdl-single-leg")
    assert rdl(original)["load"] == {"kg": 10, "count": 1}
    assert rdl(changed)["load"] == {"kg": 6, "count": 2}
    assert rdl(original)["cue"] and rdl(original)["equipment"] == ["Haltère"]
    assert rdl(programme.build({"session": "a"}))["load"] == rdl(original)["load"]
    assert all(item.cue for key in ("a", "b", "assimilation")
               for item in programme.prescriptions(key))


def test_load_override_refuses_absent_or_duplicate_exercise(programme):
    entry = {"exercise": "rdl-single-leg", "kg": 6, "count": 2}
    with pytest.raises(NoteError, match="absent ou dupliqué"):
        programme.build({"session": "a", "loads": [entry, entry]})
    with pytest.raises(NoteError, match="absent ou dupliqué"):
        programme.build({"session": "b", "loads": [entry]})


def test_every_exercise_note_renders_its_dose(programme):
    """If the programme ever gains a shape the algebra cannot say, it fails
    here -- not in front of Sam."""
    for slug, note in programme.exercises.items():
        assert Dose.read(note["dose"], slug).human()


@pytest.mark.parametrize("key,first,last,count", [
    # 13 depuis le 2026-08-21 : les 7 min de contrôle moteur entrent en séance A
    # (decisions.md) — pont fessier et tenue unipodale juste après l'échauffement,
    # injury postérieur et planche gros orteils dans le bloc accessoire. Les bornes
    # ne bougent pas : warmup-a ouvre toujours, short-foot ferme toujours.
    ("a", "warmup-a", "plyometrics", 9),
    # Box drops suspendus le 2026-08-06 (séance B = transition injury, fenêtre
    # W35-W36 — decisions.md) : la séance se termine sur calf-drop. Réouverture
    # arbitrée au bilan W33 ; ce jour-là cette ligne redevient box-drop-single/7.
    ("b", "warmup-b", "calf-drop", 5),
    ("assimilation", "calf-iso-seated-bent", "hip-abduction-light", 4),
])
def test_each_session_resolves_in_its_prescribed_order(programme, key, first, last, count):
    """The order is declared non-negotiable in strength.md: fessier first, before
    fatigue spoils posture; abduction last; plyometrics at the end when
    everything is warm."""
    slugs = [p.slug for p in programme.prescriptions(key)]
    assert len(slugs) == count
    assert slugs[0] == first and slugs[-1] == last


def test_the_single_session_never_goes_to_failure(programme):
    """Décision du 2026-10-04 : la traction du mollet est la première charge du injury,
    et l'échec ralentit la récupération ; les mollets s'arrêtent 2 répétitions avant."""
    assert [p.dose.measure for p in programme.prescriptions("a")].count("amrap") == 0
    for slug in ("calf-raise-straight", "calf-raise-bent"):
        assert programme.exercises[slug]["dose"]["measure"] == "reps"
        assert "AVANT l'échec" in programme.exercises[slug]["cue"] or \
            "avant l'échec" in programme.exercises[slug]["cue"]


def test_every_unilateral_dose_says_so(programme):
    """The 25/07 bug: "unipodal" in the name, nothing in the dose, and the page
    offered three sets instead of six -- losing the left/right gap that flags
    the calf test."""
    for slug, note in programme.exercises.items():
        name = note["name"].lower()
        if any(word in name for word in ("unipodal", "unijambiste", "bulgare")):
            assert Dose.read(note["dose"], slug).per_side, f"{slug} : côté manquant"


def test_a_session_may_override_a_dose_and_only_that_session_sees_it(programme):
    """Assimilation holds the mid-range calf 30 s with 30 s rest, where the
    exercise note itself asks 45 s of rest. (The soleus lunge that carried this
    test was replaced by the seated calf hold on 2026-09-25.)"""
    held = {p.slug: p.dose for p in programme.prescriptions("assimilation")}
    assert held["calf-iso"].human() == "3 × 30 s"
    assert held["calf-iso"].rest_s == 30
    canonical = Dose.read(programme.exercises["calf-iso"]["dose"])
    assert canonical.human() == "3 × 30 s" and canonical.rest_s == 45


def test_the_card_titles_and_rules_come_from_the_notes(programme):
    """They used to live in a TYPES dict in chart-strength.py -- where session B
    stayed "force & économie" for three days after being converted, and where
    session A's footer contradicted the dose printed above it."""
    assert programme.session("a")["title"] == "Renfo, séance unique"
    assert "descente & excentrique" in programme.session("b")["title"]
    assert "avant l'échec" in programme.session("a")["rule"]


# ---------------------------------------------------------------- refusals

def test_an_unknown_slug_names_the_ones_that_exist(programme):
    # A Programme of its own: the module fixture is shared, and a test that
    # mutates it would break whichever test happens to run next.
    broken = Programme(programme.exercises,
                       {"a": dict(programme.session("a"), exercises=["mollet-fléchi"])})
    with pytest.raises(NoteError, match="introuvable"):
        broken.prescriptions("a")


def test_an_unknown_session_names_the_ones_that_exist(programme):
    with pytest.raises(NoteError, match="connues : a, assimilation, b"):
        programme.prescriptions("c")


def test_an_exercise_note_missing_its_dose_is_refused():
    with pytest.raises(SchemaError, match="champ « dose » manquant"):
        EXERCISE_SCHEMA.validate({"type": "exercice", "name": "Mollet"}, "x.md")


def test_a_session_without_exercises_is_refused():
    problems = SESSION_SCHEMA.errors(
        {"type": "seance-type", "title": "T", "rule": "une règle assez longue",
         "exercises": []}, "renfo-x.md")
    assert problems == ["renfo-x.md : champ « exercises » : longueur 0, minimum 1"]
