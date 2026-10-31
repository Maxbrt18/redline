"""The taper measured against the weeks before it, and its band on write."""

import datetime as dt
from dataclasses import replace

from redline.analysis import taper
from redline.analysis.ledger import Row
from redline.model import race as race_model
from redline.model import rulebook, transitions

RACE_ON = dt.date(2030, 11, 30)      # Saturday of 2030-W48: taper W46-W48
RACE_ROW = {"kind": "race", "status": "scheduled", "due_on": RACE_ON.isoformat()}
RACE = race_model.from_row(RACE_ROW)


def _week(week, km_each, runs=5):
    monday = transitions.monday_of(week)
    return [Row(monday + dt.timedelta(days=day), "easy", km=km_each) for day in range(runs)]


def _history(*taper_weeks):
    rows = _week("2030-W44", 10.0) + _week("2030-W45", 10.0)
    for week, km_each, runs in taper_weeks:
        rows += _week(week, km_each, runs)
    return rows


class Store:
    def __init__(self, *rows):
        self.rows = list(rows) or [RACE_ROW]

    def deadline_rows(self):
        return self.rows


def _runs(week, km_each, runs, workout="easy"):
    monday = transitions.monday_of(week)
    return [{"id": f"run-{day}", "kind": "run", "workout": workout, "status": "planned",
             "date": (monday + dt.timedelta(days=day)).isoformat(),
             "distance_target_km": km_each} for day in range(runs)]


def _revision(sessions, by="judgment"):
    return {"revision": 1, "sessions": sessions, "decision": {"by": by, "alternatives": []}}


def test_the_reference_is_the_two_weeks_before_the_taper_without_assimilation(monkeypatch):
    frame = taper.window(RACE)
    assert frame.weeks == ("2030-W46", "2030-W47", "2030-W48")
    assert frame.reference == ("2030-W44", "2030-W45")
    book = rulebook.current()
    resting = replace(book, parameters=replace(book.parameters,
                                               assimilation_weeks=("2030-W45",)))
    monkeypatch.setattr(taper.rulebook, "current", lambda: resting)
    assert taper.window(RACE).reference == ("2030-W43", "2030-W44")


def test_the_drop_is_the_taper_mean_so_far_against_the_reference():
    rows = _history(("2030-W46", 8.0, 5), ("2030-W47", 4.0, 5))
    frame = taper.window(RACE)
    assert round(taper.measured(rows, frame, "2030-W46").percent) == 20
    assert round(taper.measured(rows, frame, "2030-W47").percent) == 40


def test_the_race_week_counts_the_days_before_the_race_only():
    rows = _history(("2030-W46", 8.0, 5), ("2030-W47", 4.0, 5))
    rows += [Row(dt.date(2030, 11, 26), "easy", km=6.0), Row(RACE_ON, "race", km=44.0)]
    cut = taper.measured(rows, taper.window(RACE), "2030-W48")
    assert cut.weeks[-1] == ("2030-W48", 6.0)


def test_a_judgment_cutting_beyond_the_ceiling_is_refused():
    frame = taper.window(RACE)
    deep = _revision(_runs("2030-W46", 3.0, 5))
    problems = taper.plan_errors(_history(), frame, "2030-W46", deep)
    assert len(problems) == 1 and "beyond the 60 % ceiling" in problems[0]
    assert taper.plan_errors(_history(), frame, "2030-W46",
                             _revision(_runs("2030-W46", 8.0, 5))) == []


def test_a_rule_or_a_request_may_leave_the_band():
    frame = taper.window(RACE)
    for by in ("rule", "request"):
        assert taper.plan_errors(_history(), frame, "2030-W46",
                                 _revision(_runs("2030-W46", 1.0, 2), by=by)) == []


def test_a_loose_taper_is_refused_on_the_race_week_only():
    rows = _history(("2030-W46", 9.0, 5), ("2030-W47", 8.0, 5))
    frame = taper.window(RACE)
    # W47 alone is not judged loose: the race week can still cut.
    assert taper.plan_errors(_history(("2030-W46", 9.0, 5)), frame, "2030-W47",
                             _revision(_runs("2030-W47", 8.0, 5))) == []
    loose = _revision(_runs("2030-W48", 9.0, 4))
    problems = taper.plan_errors(rows, frame, "2030-W48", loose)
    assert len(problems) == 1 and "under the 41 % floor" in problems[0]


def test_the_race_itself_is_not_taper_volume():
    rows = _history(("2030-W46", 7.0, 5), ("2030-W47", 5.0, 5))
    race = {"id": "race", "kind": "run", "workout": "race", "status": "planned",
            "date": RACE_ON.isoformat(), "distance_target_km": 44.0}
    cut = taper.planned(rows, taper.window(RACE), "2030-W48",
                        [*_runs("2030-W48", 3.0, 3), race])
    assert cut.weeks[-1] == ("2030-W48", 9.0)


def test_the_taper_keeps_its_frequency_before_the_race_week():
    frame = taper.window(RACE)
    sparse = _revision(_runs("2030-W46", 14.0, 3))
    problems = taper.plan_errors(_history(), frame, "2030-W46", sparse)
    assert len(problems) == 1 and "keeps its frequency (at least 4)" in problems[0]
    assert taper.plan_errors(_history(("2030-W46", 8.0, 5), ("2030-W47", 4.0, 5)), frame,
                             "2030-W48", _revision(_runs("2030-W48", 4.0, 2))) == []


def test_no_reference_run_means_no_check():
    frame = taper.window(RACE)
    assert taper.plan_errors([], frame, "2030-W46", _revision(_runs("2030-W46", 1.0, 1))) == []


def test_a_band_the_rulebook_does_not_declare_does_not_apply(monkeypatch):
    book = rulebook.current()
    bare = replace(book, parameters=replace(book.parameters, taper_cut_min_pct=None,
                                            taper_cut_max_pct=None))
    monkeypatch.setattr(taper.rulebook, "current", lambda: bare)
    unbanded = taper.window(race_model.from_row(RACE_ROW))
    assert taper.plan_errors(_history(), unbanded, "2030-W46",
                             _revision(_runs("2030-W46", 3.0, 5))) == []


def test_the_report_says_a_loose_taper_only_once_the_race_is_run():
    rows = _history(("2030-W46", 9.0, 5), ("2030-W47", 8.0, 5),
                    ("2030-W48", 9.0, 4))
    before = taper.report_lines(Store(), rows, "2030-W48", dt.date(2030, 11, 29))
    assert before[0].startswith("Affûtage : référence 50.0 km/sem (W44-W45)")
    assert len(before) == 1
    after = taper.report_lines(Store(), rows, "2030-W48", RACE_ON)
    assert after[-1].startswith("🔴 Affûtage relâché")


def test_the_report_flags_a_cut_beyond_the_ceiling_at_once():
    rows = _history(("2030-W46", 3.0, 5))
    lines = taper.report_lines(Store(), rows, "2030-W46", dt.date(2030, 11, 17))
    assert lines[-1].startswith("🔴 Affûtage coupé au-delà de 60 %")
    assert taper.report_lines(Store(), rows, "2030-W40", dt.date(2030, 10, 6)) == []
    assert taper.report_lines(Store(), [], "2030-W46", dt.date(2030, 11, 17)) == [
        "Affûtage : aucune course à pied en W44-W45, baisse non mesurable."]


def test_a_race_sets_its_own_taper_and_band():
    short = {**RACE_ROW, "context": {"taper_weeks": 1, "taper_cut_min_pct": 20,
                                     "taper_cut_max_pct": 40}}
    frame = taper.window(race_model.from_row(short))
    assert frame.weeks == ("2030-W48",) and frame.reference == ("2030-W46", "2030-W47")
    rows = _week("2030-W46", 10.0) + _week("2030-W47", 10.0)
    problems = taper.plan_errors(rows, frame, "2030-W48", _revision(_runs("2030-W48", 11.0, 4)))
    assert len(problems) == 1 and "under the 20 % floor" in problems[0]
    assert taper.plan_errors(rows, frame, "2030-W48", _revision(_runs("2030-W48", 8.0, 4))) == []
    assert taper.race_window(Store(short), "2030-W47") is None


def test_each_race_of_the_season_keeps_its_own_taper():
    spring = {"kind": "race", "status": "done", "due_on": "2030-04-13",
              "context": {"taper_weeks": 1}}
    store = Store(spring, RACE_ROW)
    assert taper.race_window(store, "2030-W15").weeks == ("2030-W15",)
    assert taper.race_window(store, "2030-W14") is None
    assert taper.race_window(store, "2030-W46").weeks[-1] == "2030-W48"
