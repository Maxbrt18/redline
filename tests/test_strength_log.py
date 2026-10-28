"""Reading `athlete/strength-log.csv`, a projection of PostgreSQL.

What is tested here is what the page and the journal view read: the latest session per
exercise, the sets, and the line displayed under the current exercise.
"""

import pytest

from redline.model.strength_log import COLUMNS, human, last_by_slug, series


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


# --------------------------------------------------------- what the page reads

@pytest.mark.parametrize("data,line", [
    ({"droite": "14|12|11", "gauche": "13|11|10", "score": 6},
     "D 14·12·11 · G 13·11·10 — 6/10"),
    ({"valeurs": "10|10|10|10"}, "10·10·10·10"),
    ({"score": 4}, "4/10"),
    ({"passe": "oui"}, "pas fait"),
    ({}, ""),
])
def test_the_line_the_page_shows_is_rendered_here(data, line):
    """Rendered in the vault, like `Dose.human()`: a single function in the repository
    builds a readable line, and the page formats nothing."""
    assert human(dict(data)) == line


def test_the_line_is_capped():
    long = "|".join(["999"] * 12)
    assert len(human({"droite": long, "gauche": long, "score": 6})) <= 64
