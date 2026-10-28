"""The validator: every rule, and the exact wording of what it refuses.

The messages are asserted, not just the failure. They are read by Sam in
`vault-check` output and by Claude when a script refuses its input: a message
that does not name the field and the value sends the reader back to the source,
which is how a validator gets abandoned.
"""

import pytest

from redline.model.schema import Schema, SchemaError, field

PERSON = Schema("personne", [
    field("name", str),
    field("age", int, required=False, minimum=0, maximum=130),
    field("rank", str, required=False, choices=("a", "b")),
])


def only(payload, schema=PERSON):
    """The single problem raised, or a failure naming what came instead."""
    problems = schema.errors(payload)
    assert len(problems) == 1, f"attendu 1 problème, reçu {problems}"
    return problems[0]


# ------------------------------------------------------------------ the basics

def test_a_valid_payload_has_no_problem():
    assert PERSON.errors({"name": "Sam", "age": 33, "rank": "a"}) == []
    assert PERSON.errors({"name": "Sam"}) == []


def test_a_missing_required_field_names_itself():
    assert only({}) == "champ « name » manquant"


def test_an_optional_field_may_be_absent_or_null():
    assert PERSON.errors({"name": "Sam", "age": None}) == []


def test_an_unknown_field_is_an_error_not_a_shrug():
    """A typo silently swallowed is the silent failure this package deletes."""
    assert only({"name": "Sam", "aeg": 33}) == \
        "champ « aeg » inconnu (attendus : age, name, rank)"


def test_extra_true_tolerates_the_unknown():
    lenient = Schema("x", [field("name", str)], extra=True)
    assert lenient.errors({"name": "Sam", "whatever": 1}) == []


def test_the_wrong_type_shows_what_was_received():
    assert only({"name": 42}) == "champ « name » : texte attendu, entier reçu (42)"


def test_a_boolean_never_passes_for_an_integer():
    """In Python True IS an int; letting it through would type-check a nonsense."""
    assert only({"name": "Sam", "age": True}) == "champ « age » : booléen reçu, entier attendu"


def test_an_integer_is_accepted_where_a_float_is_expected():
    schema = Schema("x", [field("ratio", float)])
    assert schema.errors({"ratio": 2}) == []


# --------------------------------------------------------------- the modifiers

def test_choices_list_what_was_expected():
    assert only({"name": "Sam", "rank": "z"}) == \
        "champ « rank » : 'z' inconnu (attendus : a, b)"


def test_bounds_say_value_for_a_number():
    assert only({"name": "Sam", "age": 200}) == "champ « age » : valeur 200, maximum 130"
    assert only({"name": "Sam", "age": -1}) == "champ « age » : valeur -1, minimum 0"


def test_bounds_say_length_for_a_list():
    """« champ « x » : 0, minimum 1 » would read as a value."""
    schema = Schema("x", [field("items", list, minimum=1)])
    assert schema.errors({"items": []}) == ["champ « items » : longueur 0, minimum 1"]


def test_a_pattern_falls_back_on_its_note():
    dated = Schema("x", [field("day", str, pattern=r"\d{4}-\d{2}-\d{2}", note="AAAA-MM-JJ")])
    assert dated.errors({"day": "25/07"}) == \
        ["champ « day » : '25/07' ne suit pas le format attendu — AAAA-MM-JJ"]
    assert dated.errors({"day": "2026-07-25"}) == []


def test_a_default_fills_in_on_validate_but_not_on_errors():
    schema = Schema("x", [field("side", str, required=False, default="both")])
    assert schema.validate({})["side"] == "both"
    assert schema.errors({}) == []


# ----------------------------------------------------------------- composition

def test_list_items_are_checked_one_by_one_and_located():
    schema = Schema("x", [field("scores", list, item=field("s", int, maximum=5))])
    assert schema.errors({"scores": [3, 9, 12]}) == [
        "champ « scores[1] » : valeur 9, maximum 5",
        "champ « scores[2] » : valeur 12, maximum 5",
    ]


def test_a_nested_object_is_validated_by_its_own_schema():
    inner = Schema("dose", [field("measure", str, choices=("reps",))])
    outer = Schema("exercice", [field("dose", dict, schema=inner)])
    assert outer.errors({"dose": {"measure": "nope"}}, where="mollet.md") == [
        "mollet.md : dose : champ « measure » : 'nope' inconnu (attendus : reps)"]


def test_every_problem_is_reported_not_the_first():
    """Fixing a note blind over six round trips is how a validator dies."""
    assert len(PERSON.errors({"age": 999, "rank": "z", "oops": 1})) == 4


# -------------------------------------------------------------- cross-field

def test_the_cross_field_check_runs_only_once_the_fields_are_valid():
    """Otherwise it piles errors derived from an error."""
    schema = Schema(
        "x", [field("a", int), field("b", int, required=False)],
        check=lambda data: ["a et b s'excluent"] if data.get("b") else [])
    assert schema.errors({"a": "pas un entier", "b": 1}) == \
        ["champ « a » : entier attendu, texte reçu ('pas un entier')"]
    assert schema.errors({"a": 1, "b": 1}) == ["a et b s'excluent"]


def test_where_prefixes_every_message():
    assert PERSON.errors({}, where="athlete/x.md") == ["athlete/x.md : champ « name » manquant"]


# ------------------------------------------------------------------- validate

def test_validate_raises_with_every_problem_attached():
    with pytest.raises(SchemaError) as caught:
        PERSON.validate({"age": 999})
    assert len(caught.value.problems) == 2
    assert "donnée invalide" in str(caught.value)


def test_a_payload_that_is_not_an_object_is_refused_readably():
    assert PERSON.errors(["Sam"]) == ["personne : objet attendu, liste reçue"]
    assert PERSON.errors(None) == ["personne : objet attendu, NoneType reçu"]
