"""`decisions-index --find`: a decision is found by its words, read whole, alone."""

from conftest import load_script

decisions_index = load_script("decisions-index")

JOURNAL = """# Décisions

Préambule sans décision.

## 2026-10-04 — La porte se calcule

Raison : la douleur ne dit pas tout.

## 2026-09-07 — Le seuil reste

Rien sur la douleur ici ? Si : douleur.
"""


def test_a_decision_is_its_section_heading_included():
    assert decisions_index.sections(JOURNAL)[0] == (
        "## 2026-10-04 — La porte se calcule\n\nRaison : la douleur ne dit pas tout.")


def test_the_preamble_is_no_decision():
    assert len(decisions_index.sections(JOURNAL)) == 2


def test_find_matches_heading_and_body_ignoring_case():
    assert len(decisions_index.find(JOURNAL, "DOULEUR")) == 2
    assert decisions_index.find(JOURNAL, "porte") == [decisions_index.sections(JOURNAL)[0]]
    assert decisions_index.find(JOURNAL, "  ") == []
