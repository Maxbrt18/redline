"""model/hot_cache.py: the hot-cache Form block, generated from the ledger.

What these tests protect: the hot-cache can no longer carry a WRONG figure silently.
Values typed by hand go stale as soon as a ledger line is added, until someone reruns
the script AND remembers to re-edit the cache.

What these tests deliberately do NOT protect: that the VERSIONED block is up to date.
That is vault content, not code, and it is checked by `vault-check`
(`check_generated_views`), as stated in the header of `code-checks.yml`. A gate that
cries wolf on a note that moves ends up ignored. Drift is prevented upstream:
`redline/vault.py` regenerates before committing, `.githooks/pre-commit` covers
human clones.
"""

import datetime as dt

from conftest import RPE_TARGETS

from redline.analysis import ledger
from redline.analysis.ledger import Instruments, Row
from redline.model import hot_cache


def test_the_block_carries_the_gauge_of_the_last_week():
    """Synthetic figures, not the real ledger's: otherwise this test breaks every week
        that passes (W30 becomes W31...), for a reason unrelated to a bug in `render()`."""
    rows = [Row(dt.date(2026, 3, 2), "easy", km=10, hours=5.0),
            Row(dt.date(2026, 3, 4), "easy", km=10, hours=5.0)]
    block = hot_cache.render(ledger, rows, {}, RPE_TARGETS)
    assert "W10" in block
    assert "10 h-éq · 140/100" in block   # h_eq = 5+5 ; level = round(10 * 14)
    assert "au rouge" in block           # 140 > LEVEL_CAP (85)


def test_a_red_made_of_walking_says_so():
    """A red gauge made of hiking does not read like a red gauge made of running.
        The block must carry the walking share, otherwise the reading is redone by
        hand every week -- and lost."""
    rows = [Row(dt.date(2026, 3, 2), "easy", km=10, hours=2.0),
            Row(dt.date(2026, 3, 4), "hike", km=15, hours=6.0, dplus=300)]
    assert "% de marche" in hot_cache.render(ledger, rows, {}, RPE_TARGETS)


def test_the_walk_share_uses_the_gauge_formula_not_a_copy_of_it():
    """REGRESSION: `hot_cache` carried its own copy of the weighting and that copy had
        lost the +0.5 of the MISSED quality session. Same block line, two arithmetics: the
        eq-hours on one side, the walking share on the other -- which came out inflated and
        could announce an injury red that the gauge denies.

        Here: threshold 1 h at RPE 8 (target 6.5) = 2.0 eq-h; hike 2 h = 1.0 eq-h, so 33 %
        walking. With the faulty copy the threshold weighed 1.5 and walking showed 40 %.

    """
    rows = [Row(dt.date(2026, 3, 2), "threshold", km=10, hours=1.0, rpe=8.0),
            Row(dt.date(2026, 3, 4), "hike", km=8, hours=2.0)]
    assert ledger.row_weight(rows[0], RPE_TARGETS) == (2.0, True)
    assert "33 % de marche" in hot_cache.render(ledger, rows, {}, RPE_TARGETS)


def test_a_quiet_week_carries_no_red_verdict():
    rows = [Row(dt.date(2026, 3, 2), "easy", km=8.0, hours=0.8, pains={"knee": 0.0}),
            Row(dt.date(2026, 3, 4), "easy", km=8.0, hours=0.8, pains={"knee": 0.0})]
    block = hot_cache.render(ledger, rows, {"2026-W10": Instruments()}, RPE_TARGETS)
    assert "au rouge" not in block
    assert "% de marche" not in block   # zero hiking: the mention has no reason to appear
    assert "2 sorties" in block


def test_an_empty_ledger_does_not_crash():
    assert "vide" in hot_cache.render(ledger, [], {}, RPE_TARGETS)


# ------------------------------------------- open week and State block

def test_the_week_in_progress_is_never_announced_as_closed():
    """On a Saturday, the block announced «W39 closed -- 13 km, ratio 0.3»."""
    rows = [Row(dt.date(2026, 9, 22), "easy", km=7.0, hours=0.75),
            Row(dt.date(2026, 9, 24), "easy", km=6.0, hours=0.65)]
    saturday = hot_cache.render(ledger, rows, {}, RPE_TARGETS, today=dt.date(2026, 9, 26))
    assert "W39 en cours" in saturday and "bouclée" not in saturday
    monday = hot_cache.render(ledger, rows, {}, RPE_TARGETS, today=dt.date(2026, 9, 28))
    assert "W39 bouclée" in monday


def _state(**overrides):
    rows = []
    for day, km in (("2026-09-06", 10.4), ("2026-09-13", 11.53),
                    ("2026-09-20", 10.8), ("2026-09-27", 11.0)):
        rows.append(Row(dt.date.fromisoformat(day), "long", km=km, hours=km / 8))
    inputs = {
        "deadlines": [
            {"deadline_key": "verdict-cortex", "due_on": dt.date(2026, 9, 6),
             "status": "todo", "blocks": "descente courue"},
            {"deadline_key": "targetrace", "due_on": dt.date(2026, 11, 26),
             "status": "scheduled", "kind": "race"},
        ],
        "capabilities": [{"slug": "durabilite", "score": 2, "scale": 5},
                         {"slug": "descente", "score": 1, "scale": 5}],
        "typed_due": 0,
        "race_on": dt.date(2026, 11, 26), "today": dt.date(2026, 10, 5),
    }
    inputs.update(overrides)
    return hot_cache.render_state(ledger, rows, **inputs)


def test_the_state_block_carries_what_decides_the_next_weeks():
    block = _state()
    assert "sans nouveau record depuis 3 semaine(s) close(s)" in block
    assert "protocole des baisses ouvert" in block
    assert "course le 26/11, dans 7 semaine(s)" in block
    assert "verdict-cortex (06/09), bloque descente courue" in block
    assert "targetrace" not in block.split("Échéances échues")[1].split("\n")[0]
    assert "durabilite 2/5 · descente 1/5" in block


def test_the_state_block_does_not_change_from_one_day_to_the_next():
    """Checked against its regeneration: a date of the day would make it drift every
        morning. Tuesday and Wednesday of the same week, no new fact, same rendering."""
    assert _state(today=dt.date(2026, 10, 6)) == _state(today=dt.date(2026, 10, 7))


def test_refresh_fills_the_state_block_only_where_it_is_declared(tmp_path):
    (tmp_path / "ai").mkdir()
    cache = tmp_path / "ai" / "hot-cache.md"
    cache.write_text("<!-- FORM_START -->\n<!-- FORM_END -->\n"
                     "<!-- STATE_START -->\n<!-- STATE_END -->\n", encoding="utf-8")
    (tmp_path / "coaching" / "workouts").mkdir(parents=True)

    class Store:
        def deadline_rows(self):
            return []

        def latest_capabilities(self):
            return [{"slug": "knee", "score": 3, "scale": 5}]

        def prediction_rows(self):
            return [{"status": "open", "due_on": dt.date(2026, 9, 1)}]

        def pain_zones(self):
            return [{"slug": "knee", "label": "Genou", "active": True}]

    rows = [Row(dt.date(2026, 9, 20), "long", km=10.8, hours=1.3)]
    assert hot_cache.refresh(tmp_path, ledger, rows, {}, store=Store(),
                             today=dt.date(2026, 9, 27))
    text = cache.read_text(encoding="utf-8")
    assert "knee 3/5" in text and "Prédictions à solder** : 1" in text
    assert not hot_cache.refresh(tmp_path, ledger, rows, {}, store=Store(),
                                 today=dt.date(2026, 9, 27), check=True)


def test_each_watched_zone_has_its_own_trend_in_the_form_block():
    rows = [Row(dt.date(2026, 3, 2), "easy", km=8.0, hours=0.8,
                pains={"talon": 2.0, "cheville": 0.5})]
    zones = [ledger.PainZone("talon", "Talon"), ledger.PainZone("cheville", "Cheville")]
    block = hot_cache.render(ledger, rows, {}, RPE_TARGETS, zones=zones)
    assert "Talon W10 2/—" in block and "Cheville W10 0,5/—" in block


def test_without_a_watched_zone_the_form_block_says_so():
    rows = [Row(dt.date(2026, 3, 2), "easy", km=8.0, hours=0.8)]
    assert "aucune zone surveillée" in hot_cache.render(ledger, rows, {}, RPE_TARGETS)
