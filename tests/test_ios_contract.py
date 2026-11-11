"""The iOS app reads the notebook views as Python declares them.

tests/ios_contract.py writes the Swift models, the payload fixtures and the round-trip tests
from `redline.model.notebook_views`; a view changed without rewriting them fails here.
"""

import ios_contract
import pytest


def test_the_swift_side_of_the_contract_is_current():
    assert ios_contract.stale_or_stray() == [], "run: python tests/ios_contract.py"


@pytest.mark.parametrize(("key", "name"), [
    ("km_done", "kmDone"), ("z1", "z1"), ("default", "`default`"), ("held_back", "heldBack"),
])
def test_a_json_key_becomes_a_swift_property(key, name):
    assert ios_contract.property_name(key) == name


@pytest.mark.parametrize(("value", "name"), [
    ("very-light", "veryLight"), ("as_planned", "asPlanned"), ("none", "noneValue"),
])
def test_a_literal_becomes_a_swift_case(value, name):
    assert ios_contract.case_name(value) == name


def test_a_tagged_union_names_each_member_by_its_tag():
    tagged = [ios_contract.discriminator(members) for members in ios_contract.unions()]
    assert ("type", {"WakeAsk", "CellsAsk", "DailyAsk", "HrAsk"}) in [
        (key, {member.__name__ for member in values}) for key, values in filter(None, tagged)]
