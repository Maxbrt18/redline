"""The iOS app reads the notebook views as Python declares them.

tests/ios_contract.py writes the Swift models, the payload fixtures and the round-trip tests
from `redline.model.notebook_views`; a view changed without rewriting them fails here.
"""

import ios_contract
import ios_palette
import pytest


def test_the_swift_side_of_the_contract_is_current():
    assert ios_contract.stale_or_stray() == [], "run: python tests/ios_contract.py"


@pytest.mark.parametrize(("key", "name"), [
    ("km_done", "kmDone"), ("z1", "z1"), ("default", "`default`"), ("held_back", "heldBack"),
])
def test_a_json_key_becomes_a_swift_property(key, name):
    assert ios_contract.property_name(key) == name


@pytest.mark.parametrize(("value", "name"), [
    ("very-light", "veryLight"), ("as_planned", "asPlanned"), ("none", "noneValue"), ("ready", "ready"),
])
def test_a_literal_becomes_a_swift_case(value, name):
    assert ios_contract.case_name(value) == name


def test_a_tagged_union_names_each_member_by_its_tag():
    tagged = [ios_contract.discriminator(members) for members in ios_contract.unions()]
    assert ("type", {"WakeAsk", "CellsAsk", "DailyAsk", "HrAsk"}) in [
        (key, {member.__name__ for member in values}) for key, values in filter(None, tagged)]


def test_a_union_member_is_named_without_what_all_members_share():
    from redline.model import notebook_views as views
    named = {ios_contract.member_case(member, members, union)
             for members, union in ios_contract.unions().items() for member in members}
    assert {"fact", "strengthFact", "ready", "closed", "wake", "hr"} <= named
    assert all(name == name.strip("`") and name[:1].islower() for name in named), named
    assert views.SessionFact


def _luminance(colour):
    def linear(channel):
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(int(colour[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(first, second):
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_accent_is_readable_on_the_paper_in_both_modes():
    colours = ios_palette.resolved()
    light_accent, dark_accent = colours[ios_palette.ACCENT]
    light_bg, dark_bg = colours["bg"]
    assert _contrast(light_accent, light_bg) >= 4.5
    assert _contrast(dark_accent, dark_bg) >= 4.5


def test_the_track_is_the_same_colour_in_both_modes():
    light, dark = ios_palette.resolved()["track"]
    assert light == dark


def test_a_retired_token_says_what_replaces_it():
    swift = ios_palette.palette_swift()
    retired = [token for token in ios_palette.TOKENS if token.deprecated]
    assert retired
    for token in retired:
        declaration = f"static let {ios_palette.camel(token.name)} "
        assert f'message: "Use {token.deprecated}")\n    {declaration}' in swift
    assert swift.count("@available(*, deprecated") == len(retired)
