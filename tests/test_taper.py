"""The taper of the preparation that holds the race, measured against the weeks before it,
and its band on write."""

import datetime as dt
import uuid
from dataclasses import replace

from conftest import TEST_VAULT

from redline.analysis import taper
from redline.analysis.ledger import Row
from redline.model import cycle_templates, iso_week
from redline.model import race as race_model
from redline.model.cycles import CycleVersion
from redline.model.plan_generator import Inputs, Trame, Week
from redline.model.vocabulary import CycleCause, CycleKind, CycleVersionState, PlanPhase

RACE_ON = dt.date(2030, 11, 30)      # Saturday of 2030-W48: taper W46-W48
RACE_ROW = {"kind": "race", "status": "scheduled", "priority": "a", "race_state": "active",
            "due_on": RACE_ON.isoformat(), "deadline_key": "race"}
RACE = race_model.from_row(RACE_ROW)
#: The test vault's half on the road: its taper's band.
TEMPLATE = cycle_templates.load(TEST_VAULT).preparation_for(21.1, "road")
BAND = TEMPLATE.taper_cut_pct


def _trame(slug=TEMPLATE.slug):
    labels = [iso_week.shift("2030-W43", offset) for offset in range(6)]
    weeks = tuple(Week(label, PlanPhase.SPECIFIC if label < "2030-W46" else PlanPhase.TAPER,
                       False, 50.0, 15.0, 1) for label in labels)
    return Trame(CycleKind.PREPARATION, slug, "", weeks, (), (),
                 Inputs("2030-W43", 50, 15, 50, 10, 10))


TRAME = _trame()
FRAME = taper.window(RACE, TRAME, BAND)


class Store:
    """The races, and the preparation that holds the first one."""

    def __init__(self, *rows, trame=TRAME, kind=CycleKind.PREPARATION):
        self.rows = list(rows) or [RACE_ROW]
        self.version = CycleVersion(uuid.uuid4(), uuid.uuid4(), 1, CycleVersionState.ACTIVE,
                                    replace(trame, kind=kind), CycleCause.CREATE,
                                    race_key=self.rows[0]["deadline_key"])

    def deadline_rows(self):
        return self.rows

    def planned_low_weeks(self):
        return set()

    def cycle_for_week(self, week):
        return self.version if self.version.week(week) is not None else None


def _week(week, km_each, runs=5):
    monday = iso_week.monday(week)
    return [Row(monday + dt.timedelta(days=day), "easy", km=km_each) for day in range(runs)]


def _history(*taper_weeks):
    rows = _week("2030-W44", 10.0) + _week("2030-W45", 10.0)
    for week, km_each, runs in taper_weeks:
        rows += _week(week, km_each, runs)
    return rows


def _runs(week, km_each, runs, workout="easy"):
    monday = iso_week.monday(week)
    return [{"id": f"run-{day}", "kind": "run", "workout": workout, "status": "planned",
             "date": (monday + dt.timedelta(days=day)).isoformat(),
             "distance_target_km": km_each} for day in range(runs)]


def _revision(sessions, by="judgment"):
    return {"revision": 1, "sessions": sessions, "decision": {"by": by, "alternatives": []}}


def test_the_reference_is_the_two_weeks_before_the_taper_without_a_deload():
    assert FRAME.weeks == ("2030-W46", "2030-W47", "2030-W48")
    assert FRAME.reference == ("2030-W44", "2030-W45")
    assert taper.window(RACE, TRAME, BAND, {"2030-W45"}).reference == ("2030-W43", "2030-W44")


def test_the_drop_is_the_taper_mean_so_far_against_the_reference():
    rows = _history(("2030-W46", 8.0, 5), ("2030-W47", 4.0, 5))
    frame = FRAME
    assert round(taper.measured(rows, frame, "2030-W46").percent) == 20
    assert round(taper.measured(rows, frame, "2030-W47").percent) == 40


def test_the_race_week_counts_the_days_before_the_race_only():
    rows = _history(("2030-W46", 8.0, 5), ("2030-W47", 4.0, 5))
    rows += [Row(dt.date(2030, 11, 26), "easy", km=6.0), Row(RACE_ON, "race", km=44.0)]
    cut = taper.measured(rows, FRAME, "2030-W48")
    assert cut.weeks[-1] == ("2030-W48", 6.0)


def test_a_judgment_cutting_beyond_the_ceiling_is_refused():
    frame = FRAME
    deep = _revision(_runs("2030-W46", 3.0, 5))
    problems = taper.plan_errors(_history(), frame, "2030-W46", deep)
    assert len(problems) == 1 and "beyond the 60 % ceiling" in problems[0]
    assert taper.plan_errors(_history(), frame, "2030-W46",
                             _revision(_runs("2030-W46", 8.0, 5))) == []


def test_a_rule_or_a_request_may_leave_the_band():
    frame = FRAME
    for by in ("rule", "request"):
        assert taper.plan_errors(_history(), frame, "2030-W46",
                                 _revision(_runs("2030-W46", 1.0, 2), by=by)) == []


def test_a_loose_taper_is_refused_on_the_race_week_only():
    rows = _history(("2030-W46", 9.0, 5), ("2030-W47", 8.0, 5))
    frame = FRAME
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
    cut = taper.planned(rows, FRAME, "2030-W48",
                        [*_runs("2030-W48", 3.0, 3), race])
    assert cut.weeks[-1] == ("2030-W48", 9.0)


def test_the_taper_keeps_its_frequency_before_the_race_week():
    frame = FRAME
    sparse = _revision(_runs("2030-W46", 14.0, 3))
    problems = taper.plan_errors(_history(), frame, "2030-W46", sparse)
    assert len(problems) == 1 and "keeps its frequency (at least 4)" in problems[0]
    assert taper.plan_errors(_history(("2030-W46", 8.0, 5), ("2030-W47", 4.0, 5)), frame,
                             "2030-W48", _revision(_runs("2030-W48", 4.0, 2))) == []


def test_no_reference_run_means_no_check():
    frame = FRAME
    assert taper.plan_errors([], frame, "2030-W46", _revision(_runs("2030-W46", 1.0, 1))) == []


def test_a_template_without_a_band_binds_none():
    unbanded = replace(FRAME, band=None)
    assert taper.plan_errors(_history(), unbanded, "2030-W46",
                             _revision(_runs("2030-W46", 3.0, 5))) == []


def test_the_report_says_a_loose_taper_only_once_the_race_is_run():
    rows = _history(("2030-W46", 9.0, 5), ("2030-W47", 8.0, 5),
                    ("2030-W48", 9.0, 4))
    before = taper.report_lines(Store(), rows, "2030-W48", dt.date(2030, 11, 29))
    assert before[0].startswith("Affûtage : référence 50.0 km/sem (W44-W45)")
    assert len(before) == 1
    after = taper.report_lines(Store(), rows, "2030-W48", RACE_ON)
    assert after[-1].startswith("⚠ Affûtage relâché")


def test_the_report_flags_a_cut_beyond_the_ceiling_once_the_week_closes():
    rows = _history(("2030-W46", 3.0, 5))
    sunday = dt.date(2030, 11, 17)
    assert taper.report_lines(Store(), rows, "2030-W46", dt.date(2030, 11, 18))[-1] \
        .startswith("⚠ Affûtage coupé au-delà de 60 %")
    assert taper.report_lines(Store(), rows, "2030-W46", sunday, closing=True)[-1] \
        .startswith("⚠ Affûtage coupé au-delà de 60 %")
    assert taper.report_lines(Store(), rows, "2030-W46", sunday) == [
        "Affûtage : référence 50.0 km/sem (W44-W45) · W46 15.0 km (en cours) · la baisse "
        "se mesure à la clôture de la semaine"]


def test_a_week_in_progress_is_shown_but_never_judged():
    rows = _history(("2030-W46", 3.0, 5), ("2030-W47", 2.0, 2))
    lines = taper.report_lines(Store(), rows, "2030-W47", dt.date(2030, 11, 20))
    assert "W46 15.0 km · W47 4.0 km (en cours)" in lines[0]
    assert "baisse moyenne des semaines closes 70 %" in lines[0]
    assert taper.report_lines(Store(), rows, "2030-W40", dt.date(2030, 10, 6)) == []
    assert taper.report_lines(Store(), [], "2030-W46", dt.date(2030, 11, 17)) == [
        "Affûtage : aucune course à pied en W44-W45, baisse non mesurable."]


def test_the_taper_is_the_one_of_the_preparation_that_holds_the_race():
    found = taper.race_window(Store(), "2030-W46")
    assert found is not None and found.weeks == ("2030-W46", "2030-W47", "2030-W48")
    assert found.band == BAND, "the band of the template the trame was generated from"
    assert taper.race_window(Store(), "2030-W45") is None, "a specific week is no taper"
    assert taper.race_window(Store(kind=CycleKind.GENERAL), "2030-W46") is None
    assert taper.race_window(Store(trame=_trame("gone")), "2030-W46").band is None


def test_a_race_cancelled_binds_no_taper():
    cancelled = {**RACE_ROW, "race_state": "cancelled", "status": "done"}
    assert taper.race_window(Store(cancelled), "2030-W46") is None
