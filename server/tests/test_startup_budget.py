"""model/startup_budget.py : le coût de lecture du coach, plafonné par fichier et par étage.

Ce que ces tests protègent : l'alerte doit NOMMER le fichier fautif. La version à
cagnotte unique disait « 8042 > 8000 » et rien d'autre, ce qui obligeait à rouvrir
les trois fichiers — et faisait systématiquement porter le chapeau au hot-cache,
seul fichier réécrit à chaque session, même quand la masse était ailleurs.

Depuis le 2026-08-06 elle doit aussi nommer l'ÉTAGE : `athlete.md` est lu en N2 et
non plus à chaque message, donc ses mots et ceux de `CLAUDE.md` ne se paient pas à la
même fréquence et ne doivent jamais se compenser dans un total commun.

Le dépassement RÉEL du vault, lui, n'est pas testé ici : c'est du contenu, et le
hot-cache que le bot réécrit plusieurs fois par jour vit à ~90 mots de son plafond.
Un mot de trop écrit par le bot n'est pas un bug de code et n'a pas à rougir le gate
code — `vault-check.py` (`check_startup_budget`) le dit à la revue, avec en plus le
palier « marge < 5 % » qui prévient AVANT la panne, ce qu'un assert ne fait pas.
"""

import pytest

from scripts.model import startup_budget as budget


def test_every_file_belongs_to_exactly_one_tier():
    """Un fichier sans étage serait compté nulle part : dépassement invisible."""
    assert set(budget.TIER_OF) == set(budget.CEILINGS)
    assert set(budget.TIER_OF.values()) <= set(budget.TOTALS)


def test_ceilings_never_exceed_their_tier_total():
    """Le total d'un étage est sa vraie contrainte : relever un plafond doit obliger
    à rouvrir ce total, pas permettre de le contourner en douce."""
    for tier, total in budget.TOTALS.items():
        ceilings = sum(c for name, c in budget.CEILINGS.items()
                       if budget.TIER_OF[name] == tier)
        assert ceilings <= total, tier


def test_an_overflow_names_its_file_and_its_tier():
    result = budget.audit({"CLAUDE.md": "mot " * 4000,
                           "athlete/athlete.md": "mot " * 10,
                           "ai/hot-cache.md": "mot " * 10})
    assert [name for name, _, _ in result.over] == ["CLAUDE.md"]
    assert "CLAUDE.md" in result.problems()[0]
    assert "4000" in result.problems()[0]
    assert budget.PER_TURN in result.problems()[0]


def test_one_file_never_pays_for_another():
    """LE cas qui a motivé la refonte de 07/27 : CLAUDE.md au plafond ne doit pas
    faire échouer une mise à jour du hot-cache qui, elle, tient dans le sien."""
    result = budget.audit({"CLAUDE.md": "mot " * budget.CEILINGS["CLAUDE.md"],
                           "athlete/athlete.md": "mot " * 10,
                           "ai/hot-cache.md": "mot " * budget.CEILINGS["ai/hot-cache.md"]})
    assert not result.over and not result.over_total


def test_the_two_tiers_never_compensate():
    """Vider athlete.md ne rend pas un mot au coût par tour, et l'inverse non plus.
    C'est toute la raison d'être de la séparation du 2026-08-06 : un budget unique
    laissait croire qu'un dégraissage N2 soulageait le message médian."""
    slack_in_n2 = budget.audit({"CLAUDE.md": "mot " * (budget.CEILINGS["CLAUDE.md"] + 1),
                                "athlete/athlete.md": "",
                                "ai/hot-cache.md": "mot " * 10})
    assert [name for name, _, _ in slack_in_n2.over] == ["CLAUDE.md"]

    slack_per_turn = budget.audit({"CLAUDE.md": "",
                                   "ai/hot-cache.md": "",
                                   "athlete/athlete.md":
                                       "mot " * (budget.CEILINGS["athlete/athlete.md"] + 1)})
    assert [name for name, _, _ in slack_per_turn.over] == ["athlete/athlete.md"]
    assert [tier for tier, _, _ in slack_per_turn.over_total] == [budget.N2]


def test_a_missing_file_counts_as_zero():
    result = budget.audit({})
    assert result.totals == dict.fromkeys(budget.TOTALS, 0)
    assert not result.over and not result.over_total


def test_the_configuration_refuses_to_lie():
    """Une somme de plafonds au-dessus du total de son étage est une configuration
    qui ment : elle laisserait passer un étage au-dessus du budget, tous fichiers verts."""
    original = dict(budget.CEILINGS)
    budget.CEILINGS["CLAUDE.md"] = budget.TOTALS[budget.PER_TURN]
    try:
        with pytest.raises(budget.BudgetError):
            budget.audit({})
    finally:
        budget.CEILINGS.clear()
        budget.CEILINGS.update(original)
