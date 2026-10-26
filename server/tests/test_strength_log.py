"""La lecture de `athlete/strength-log.csv`, projection de PostgreSQL.

Ce qui est testé ici, c'est ce que la page et la vue du journal lisent : la dernière
séance par exercice, les séries, et la ligne affichée sous l'exercice en cours.
"""

import pytest

from scripts.model.strength_log import COLUMNS, human, last_by_slug, series


@pytest.fixture
def log(tmp_path):
    return tmp_path / "strength-log.csv"


def write(path, *entries) -> None:
    lines = [",".join(COLUMNS)]
    for entry in entries:
        data = dict({"date": "2026-08-01", "sid": "s1", "seance": "a",
                     "slug": "calf-raise-bent"}, **entry)
        lines.append(",".join(str(data.get(column, "")) for column in COLUMNS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_last_by_slug_keeps_the_most_recent_session(log):
    write(log, {"droite": "14|12", "score": 6},
          {"sid": "s2", "date": "2026-08-08", "droite": "15|13", "score": 5},
          {"sid": "s3", "date": "2026-08-04", "slug": "step-up", "valeurs": "8"})
    latest = last_by_slug(log)
    assert latest["calf-raise-bent"]["sid"] == "s2"
    assert latest["step-up"]["sid"] == "s3"


def test_series_reads_back_what_was_written():
    assert series("14|12|11") == [14, 12, 11]
    assert series("") == [] and series(None) == []


# --------------------------------------------------------- ce que la page lit

@pytest.mark.parametrize("data,line", [
    ({"droite": "14|12|11", "gauche": "13|11|10", "score": 6},
     "D 14·12·11 · G 13·11·10 — 6/10"),
    ({"valeurs": "10|10|10|10"}, "10·10·10·10"),
    ({"score": 4}, "4/10"),
    ({"passe": "oui"}, "pas fait"),
    ({}, ""),
])
def test_the_line_the_page_shows_is_rendered_here(data, line):
    """Rendu dans le vault, comme `Dose.human()` : une seule fonction du dépôt
    fabrique une ligne lisible, et la page ne formate rien."""
    assert human(dict(data)) == line


def test_the_line_is_capped():
    long = "|".join(["999"] * 12)
    assert len(human({"droite": long, "gauche": long, "score": 6})) <= 64
