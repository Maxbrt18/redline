"""Le règlement compilé : court, sans histoire, rattaché à des propriétaires réels."""

import re

from conftest import VAULT_ROOT

from scripts.model import rulebook, typography


def test_the_rulebook_is_short_enough_to_travel_with_every_packet():
    # Il est injecté à chaque tour N1+ : un règlement qui grossit redevient un
    # palimpseste. Relever ce plafond exige de retirer une règle, pas d'en ajouter.
    assert len(rulebook.render().split()) <= 400


def test_the_rulebook_uses_only_phone_friendly_signs():
    assert [glyph for glyph in typography.BANNED_GLYPHS if glyph in rulebook.render()] == []


def test_every_rule_names_an_owner_that_exists():
    for rule in rulebook.RULES:
        for owner in rule.owner.split(" ; "):
            path = owner.split(" §")[0].split(" 20")[0].strip()
            assert (VAULT_ROOT / path).exists(), f"{rule.id} : propriétaire absent {path}"


def test_rule_ids_are_unique():
    ids = [rule.id for rule in rulebook.RULES]
    assert len(ids) == len(set(ids))


def test_no_fixed_percentage_bounds_the_long_run():
    """Décision de Sam du 2026-09-27 : la progression de la sortie longue se juge,
    aucun pourcentage fixe ne la borne. Les seuls pourcentages restants sont ceux de
    l'assimilation (n2) et de la réduction sur douleur (n3)."""
    text = rulebook.render()
    for rule in rulebook.RULES:
        if rule.id in {"n2", "n3"}:
            continue
        assert not re.search(r"\d+\s?%", rule.text), rule.id
    assert "sans pourcentage fixe" in text


def test_the_gate_replaces_calendar_windows():
    """Décision de Sam du 2026-10-04 : hausses faciles cumulables sous une porte
    observable ; plus de fenêtre +3/+4 ni de nouveauté unique."""
    rules = {rule.id: rule for rule in rulebook.RULES}
    assert "+3" not in rules["n8"].text and "nouveauté" not in rules["n1"].text
    assert "1,5/10" in rules["po"].text and "cumulent" in rules["po"].text


def test_descent_opens_on_an_observable_condition_not_a_medical_verdict():
    """Décision de Sam du 2026-09-27 : dossier médical clos sans réponse."""
    rule = next(rule for rule in rulebook.RULES if rule.id == "de")
    assert "drapeau rouge" in rule.text and "avis médical" in rule.text
    assert "J+1" in rule.text
    assert "verdict" not in rule.text


def test_volume_and_intensity_follow_the_signals():
    """Décision de Sam du 2026-10-11 : l'alternance stricte volume/intensité est
    remplacée par une règle conditionnelle aux signaux de la semaine précédente."""
    rule = next(rule for rule in rulebook.RULES if rule.id == "n1")
    assert "jamais volume ET intensité" not in rule.text
    assert "porte ouverte" in rule.text and "introduction" in rule.text
    assert "Une par semaine" in rule.text and "zéro VMA" in rule.text
