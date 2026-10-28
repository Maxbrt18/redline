"""The dose algebra: what it renders, what it refuses, what it hands the page.

Every dose that appears in `coaching/strength.md` is covered below. That list is
the real contract: if the programme gains a shape the algebra cannot express,
this file fails before the Saturday morning session does.
"""

import pytest

from redline.model.dose import DOSE_SCHEMA, Dose, Span
from redline.model.schema import SchemaError


def dose(**data) -> Dose:
    return Dose.read(data)


# ------------------------------------------------------------------- rendering

# Left: the frontmatter. Right: what Sam reads on the card and on the page.
#
# Every entry was transcribed FROM coaching/strength.md, not from memory. That
# distinction is not pedantic: the first version of this list was written from
# memory, every case passed, and it hid the fact that the programme prescribes
# RANGES OF SETS ("2-3× jusqu'à échec technique", exercice 3) which the algebra
# could not express at all. A contract test written from memory tests the memory.
@pytest.mark.parametrize("data,text", [
    # séance A
    ({"measure": "time", "seconds": 180}, "3 min"),
    ({"measure": "reps", "sets": 3, "reps": 8, "side": "each"}, "3 × 8 / jambe"),
    ({"measure": "amrap", "sets": 3, "side": "each"},
     "3 × jusqu'à échec technique / jambe"),
    ({"measure": "amrap", "sets": [2, 3], "side": "each"},
     "2-3 × jusqu'à échec technique / jambe"),
    ({"measure": "reps", "sets": 3, "reps": [8, 10], "side": "each"}, "3 × 8-10 / jambe"),
    ({"measure": "amrap", "sets": 2, "side": "each"},
     "2 × jusqu'à échec technique / jambe"),
    ({"measure": "contacts", "contacts": 40, "block": 10}, "40 contacts, en blocs de 10"),
    ({"measure": "reps", "sets": 2, "reps": 15}, "2 × 15"),
    ({"measure": "time", "seconds": [120, 180]}, "2-3 min"),
    # séance B
    ({"measure": "time", "seconds": 240}, "4 min"),
    ({"measure": "reps", "sets": 3, "reps": 6, "side": "each"}, "3 × 6 / jambe"),
    ({"measure": "reps", "sets": 3, "reps": 5}, "3 × 5"),
    ({"measure": "reps", "sets": 2, "reps": 4, "side": "each"}, "2 × 4 / jambe"),
    # version dégradée et assimilation
    ({"measure": "time", "sets": 3, "seconds": [30, 45], "side": "each"},
     "3 × 30-45 s / jambe"),
    ({"measure": "time", "sets": 3, "seconds": 30}, "3 × 30 s"),
    ({"measure": "time", "sets": 3, "seconds": 45, "side": "each"}, "3 × 45 s / jambe"),
    ({"measure": "time", "sets": 2, "seconds": 30}, "2 × 30 s"),
])
def test_every_dose_of_the_programme_renders(data, text):
    assert dose(**data).human() == text


def test_a_range_of_sets_offers_the_top_of_the_range():
    """Same rule as a countdown: proposing the minimum would cap the athlete at
    the least he was asked for, and skipping the last set is one tap away."""
    prescribed = dose(measure="amrap", sets=[2, 3], side="each")
    assert len(prescribed.slots) == 6
    assert prescribed.slots[-1]["of"] == 3


def test_a_duration_over_a_minute_reads_in_minutes_and_seconds():
    assert dose(measure="time", seconds=90).human() == "1 min 30"


def test_a_range_reads_in_one_unit():
    """"1 min 30-2 min" would be unreadable at arm's length."""
    assert dose(measure="time", seconds=[45, 90]).human() == "45-90 s"
    assert dose(measure="time", seconds=[60, 120]).human() == "1-2 min"


def test_contacts_without_a_block_stay_plain():
    assert dose(measure="contacts", contacts=40).human() == "40 contacts"


# ----------------------------------------------------------------------- spans

def test_a_span_reads_a_scalar_or_a_pair_never_a_string():
    assert Span.read(8) == Span(8)
    assert Span.read([8, 10]) == Span(8, 10)
    assert Span.read([8]) == Span(8)


def test_a_span_aims_at_the_top_of_its_range():
    """A clock stopping at the minimum would cap the athlete at the least he was
    asked for; validating early is one tap away."""
    assert Span(30, 45).target == 45
    assert Span(45).target == 45


# ------------------------------------------------------------------- what the
# page is handed

def test_the_payload_carries_the_walked_out_slots_and_the_rendered_label():
    """`sets`, `side` and `seconds` are deliberately absent: they existed only so
    the page could rebuild the slot list, which it is now handed (2026-07-31)."""
    payload = dose(measure="time", sets=3, seconds=[30, 45], side="each",
                   rest_s=45).payload()
    assert payload == {
        "measure": "time", "text": "3 × 30-45 s / jambe", "rest_s": 45,
        "current_text": "30-45 s",
        "slots": [{"n": 1, "of": 3, "s": "d", "t": 45}, {"n": 1, "of": 3, "s": "g", "t": 45},
                  {"n": 2, "of": 3, "s": "d", "t": 45}, {"n": 2, "of": 3, "s": "g", "t": 45},
                  {"n": 3, "of": 3, "s": "d", "t": 45}, {"n": 3, "of": 3, "s": "g", "t": 45}],
    }


def test_contacts_are_walked_one_block_at_a_time():
    """The prescription is "40 contacts, en blocs de 10, 30 s entre blocs", and
    its whole mechanism is the INTERVAL: bone mechanosensitivity collapses after
    ~20 consecutive cycles (src-warden-bone-workload) and spacing the jumps
    raises the osseous gain at equal contact count (src-umemura-jump-interval).
    Walked as one flat run of 40 -- which is what the page did until 2026-07-31 --
    the exercise delivers none of that."""
    assert dose(measure="contacts", contacts=40, block=10, rest_s=30).payload() == {
        "measure": "contacts", "text": "40 contacts, en blocs de 10", "rest_s": 30,
        "current_text": "10 contacts",
        "slots": [{"n": 1, "of": 4, "t": 10}, {"n": 2, "of": 4, "t": 10},
                  {"n": 3, "of": 4, "t": 10}, {"n": 4, "of": 4, "t": 10}],
    }


def test_contacts_without_a_block_stay_one_slot():
    assert dose(measure="contacts", contacts=40).payload()["slots"] == [
        {"n": 1, "of": 1, "t": 40}]


@pytest.mark.parametrize("data,slots", [
    ({"measure": "reps", "sets": 3, "reps": 8, "side": "each"}, 6),
    ({"measure": "reps", "sets": 3, "reps": 8}, 3),
    ({"measure": "amrap", "sets": 2, "side": "each"}, 4),
    ({"measure": "time", "seconds": 180}, 1),
    ({"measure": "contacts", "contacts": 40, "block": 10}, 4),
    ({"measure": "contacts", "contacts": 40}, 1),
])
def test_a_slot_is_a_set_times_a_side__or_a_block(data, slots):
    assert len(dose(**data).slots) == slots


def test_an_amrap_slot_names_no_target():
    """"Jusqu'à échec technique" has no number to aim at, and inventing one would
    contradict the stopping criterion."""
    assert dose(measure="amrap", sets=2).slots == [{"n": 1, "of": 2}, {"n": 2, "of": 2}]


# ------------------------------------------------------------------ modulation

def test_a_modulation_yields_a_new_dose_and_leaves_the_programme_alone():
    full = dose(measure="reps", sets=3, reps=8, side="each")
    assert full.fewer_sets().human() == "2 × 8 / jambe"
    assert full.human() == "3 × 8 / jambe"


@pytest.mark.parametrize("sets,halved", [
    (3, "2 × 8 / jambe"),
    ([2, 3], "1-2 × 8 / jambe"),      # the whole range shifts down
    ([1, 2], "1 × 8 / jambe"),        # ...and collapses when it has to
    (1, "1 × 8 / jambe"),             # never below one set
])
def test_half_dose_shifts_the_range_and_floors_at_one(sets, halved):
    assert dose(measure="reps", sets=sets, reps=8, side="each").fewer_sets().human() == halved


def test_half_dose_leaves_contacts_alone():
    """Contacts are counted in blocks: there is no set to remove, and the
    session that needs less drops the exercise (strength.md, 25/07)."""
    plyo = dose(measure="contacts", contacts=40, block=10)
    assert plyo.fewer_sets() == plyo


# ------------------------------------------------------------------- refusals

def test_a_measure_demands_its_own_field():
    with pytest.raises(SchemaError) as caught:
        dose(measure="time", sets=3)
    assert caught.value.problems == [
        "mesure « time » : le champ « seconds » est obligatoire"]


def test_a_field_that_does_not_belong_to_the_measure_is_refused():
    assert DOSE_SCHEMA.errors({"measure": "amrap", "sets": 3, "reps": 8}) == [
        "mesure « amrap » : le champ « reps » n'a pas de sens ici"]


def test_an_unknown_measure_lists_the_known_ones():
    assert DOSE_SCHEMA.errors({"measure": "duree"}) == [
        "champ « measure » : 'duree' inconnu (attendus : amrap, contacts, reps, time)"]


@pytest.mark.parametrize("value,problem", [
    ("8-10", "champ « reps » : un entier ou une paire [bas, haut] attendu"),
    ([8, 10, 12], "champ « reps » : un entier ou une paire [bas, haut] attendu"),
    ([10, 8], "champ « reps » : fourchette 10-8 — le bas doit être inférieur au haut"),
    ([8, 8], "champ « reps » : fourchette 8-8 — le bas doit être inférieur au haut"),
    (0, "champ « reps » : hors bornes (1 à 200)"),
    (500, "champ « reps » : hors bornes (1 à 200)"),
])
def test_a_malformed_span_is_refused_with_its_reason(value, problem):
    """The string form is the one that must never come back: it is exactly the
    parser this whole refactor deleted."""
    assert DOSE_SCHEMA.errors({"measure": "reps", "reps": value}) == [problem]


def test_a_block_must_divide_the_contacts():
    assert DOSE_SCHEMA.errors({"measure": "contacts", "contacts": 40, "block": 12}) == [
        "« block » 12 ne divise pas 40 contacts"]


def test_a_block_only_means_something_for_contacts():
    assert DOSE_SCHEMA.errors({"measure": "reps", "reps": 8, "block": 10}) == [
        "« block » ne s'emploie qu'avec la mesure « contacts »"]


def test_contacts_are_not_counted_in_sets():
    assert DOSE_SCHEMA.errors({"measure": "contacts", "contacts": 40, "sets": 3}) == [
        "mesure « contacts » : les contacts se comptent en blocs, pas en séries"]


def test_an_unknown_field_is_refused_like_anywhere_else():
    assert DOSE_SCHEMA.errors({"measure": "reps", "reps": 8, "mode": "time"})[0] \
        .startswith("champ « mode » inconnu")


def test_a_rest_out_of_range_is_refused():
    assert DOSE_SCHEMA.errors({"measure": "reps", "reps": 8, "rest_s": 1}) == [
        "champ « rest_s » : valeur 1, minimum 5"]


def test_the_side_vocabulary_is_closed():
    assert DOSE_SCHEMA.errors({"measure": "reps", "reps": 8, "side": "gauche"}) == [
        "champ « side » : 'gauche' inconnu (attendus : both, each)"]
