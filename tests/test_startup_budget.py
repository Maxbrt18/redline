"""The agent's reading cost: a ceiling per file, inside a tier the instance declares.

An overflow names its file and its tier, and two tiers never compensate: the words read
on every message and those read only for a judgement are not paid as often.
"""

import pytest
from conftest import TEST_VAULT

from redline.model import instance
from redline.model import startup_budget as budget

PER_TURN = budget.Tier("per-turn", 3600, (("CLAUDE.md", 2200), ("ai/hot-cache.md", 1400)))
N2 = budget.Tier("N2", 2800, (("athlete/athlete.md", 2800),))
TIERS = (PER_TURN, N2)


def _words(count: int) -> str:
    return "mot " * count


def test_an_overflow_names_its_file_and_its_tier():
    result = budget.audit(TIERS, {"CLAUDE.md": _words(4000)})
    assert [name for name, _, _ in result.over] == ["CLAUDE.md"]
    assert "4000" in result.problems()[0]
    assert "per-turn" in result.problems()[0]


def test_one_file_never_pays_for_another():
    result = budget.audit(TIERS, {"CLAUDE.md": _words(2200), "ai/hot-cache.md": _words(1400)})
    assert not result.over and not result.over_total


def test_the_two_tiers_never_compensate():
    result = budget.audit(TIERS, {"athlete/athlete.md": _words(2801)})
    assert [name for name, _, _ in result.over] == ["athlete/athlete.md"]
    assert [tier for tier, _, _ in result.over_total] == ["N2"]


def test_a_missing_file_counts_as_zero():
    result = budget.audit(TIERS, {})
    assert result.totals == {"per-turn": 0, "N2": 0}
    assert not result.over and not result.over_total


def test_ceilings_never_exceed_their_tier_total():
    assert budget.ceiling_errors((budget.Tier("t", 10, (("a.md", 8), ("b.md", 8))),)) == [
        "étage « t » : somme des plafonds 16 > total 10"]
    assert budget.ceiling_errors((budget.Tier("t", 10, (("a.md", 5),)),
                                  budget.Tier("u", 10, (("a.md", 5),)))) == [
        "a.md est budgété dans deux étages"]


def test_the_instance_declares_its_tiers():
    assert instance.load(TEST_VAULT).read_budget == TIERS


def test_an_incoherent_budget_is_refused():
    text = (TEST_VAULT / "redline.toml").read_text(encoding="utf-8").replace(
        "total = 2800", "total = 100")
    with pytest.raises(instance.InstanceError, match="somme des plafonds"):
        instance.parse(text)
