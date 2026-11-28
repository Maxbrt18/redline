"""What the coach reads of the cycles: the version in force, the week the Sunday review
prepares within it, and the goal's window while another cycle is in force."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import trajectory

from redline.analysis import cycle_report, cycle_review, weekly_dossier
from redline.coach.fr import cycle as text
from redline.model import iso_week
from redline.model import plan_generator as generator
from redline.model.cycles import CycleVersion
from redline.model.vocabulary import CycleCause, CycleVersionState, GeneralLevel

TEMPLATES = trajectory.TEMPLATES


def _store(cycle: CycleVersion | None = None) -> trajectory.Store:
    store = trajectory.Store(trajectory.HALF, trajectory.RACE_ON)
    for back in range(1, trajectory.HISTORY_WEEKS + 1):
        trajectory.run_week(store, iso_week.shift(trajectory.FIRST_WEEK, -back), 30, 14)
    store.cycle = cycle
    return store


def _general(origin: str = trajectory.FIRST_WEEK) -> CycleVersion:
    trame = generator.general(TEMPLATES.general_at(GeneralLevel.MAINTAIN),
                              generator.Inputs(origin, 30, 14, 34, 10, 10))
    return CycleVersion(uuid.uuid4(), uuid.uuid4(), 2, CycleVersionState.ACTIVE, trame,
                        CycleCause.BLOCK_END, level=GeneralLevel.MAINTAIN)


def test_the_review_reads_the_week_it_prepares_within_the_version_in_force():
    store = _store(_general())
    lines = cycle_report.review_lines(store, iso_week.shift(trajectory.FIRST_WEEK, -1),
                                      trajectory.review_day(trajectory.FIRST_WEEK))
    assert lines[0].startswith("Cycle en cours : Préparation générale « maintenir », "
                               "version 2 (bloc suivant)")
    assert lines[1].startswith(f"  {trajectory.FIRST_WEEK} générale : ")
    beyond = cycle_report.review_lines(store, store.cycle.last_week, dt.date(2031, 1, 5))
    assert beyond[1] == f"  Aucune semaine {iso_week.shift(store.cycle.last_week, 1)} dans ce cycle."


def test_without_a_version_the_review_says_the_planner_writes_one():
    lines = cycle_report.review_lines(_store(), trajectory.FIRST_WEEK, dt.date(2030, 12, 1))
    assert lines[0].startswith("Aucun cycle en cours")


def test_the_goal_s_window_is_told_while_another_cycle_is_in_force():
    opens = trajectory.window_start()
    template = TEMPLATES.preparation_for(21.1, "road")
    assert opens >= generator.window_start(template, trajectory.RACE_ON)
    early = trajectory.review_day(generator.window_start(template, trajectory.RACE_ON))
    general = _general()
    assert cycle_report.window_line(_store(general), TEMPLATES, general,
                                    early - dt.timedelta(weeks=2)) is None
    line = cycle_report.window_line(_store(general), TEMPLATES, general, early)
    assert line is not None and "`redline cycle candidates`" in line
    store = trajectory.simulate(through=opens).store
    assert cycle_report.window_line(store, TEMPLATES, store.cycle, early) is None, \
        "a preparation in force is the window taken"


def test_the_dossier_carries_the_cycle_and_reports_a_refusal(monkeypatch):
    store = _store(_general())
    week = iso_week.shift(trajectory.FIRST_WEEK, -1)
    today = trajectory.review_day(trajectory.FIRST_WEEK)
    found = weekly_dossier._cycle(store, week, today)
    assert found[1].startswith("Cycle en cours")

    def unreadable(*args):
        raise cycle_report.ConfigError("no general template")
    monkeypatch.setattr(cycle_report, "review_lines", unreadable)
    assert weekly_dossier._cycle(store, week, today)[1].startswith("Cycle en cours illisible")


def test_the_version_in_force_is_shown_whole():
    store = trajectory.simulate(through=trajectory.window_start()).store
    lines = cycle_report.shown(store, store.cycle, trajectory.review_day(trajectory.FIRST_WEEK))
    assert lines[0].startswith("Cycle en cours : Préparation, version 1 (créée pour l'objectif)")
    assert any(line.startswith("Préparation « half-road »") for line in lines)
    assert sum(1 for line in lines if line.startswith("  20")) == len(store.cycle.trame.weeks)


@pytest.mark.parametrize("cause", list(CycleCause))
def test_every_cause_has_its_words(cause):
    assert cycle_report.text.CAUSES[cause]


def test_the_dossier_carries_the_engine_s_draft_of_the_week_it_prepares():
    start = trajectory.window_start()
    store = trajectory.simulate(through=start).store
    week = iso_week.shift(start, 0)
    lines = weekly_dossier._cycle(store, week, trajectory.review_day(iso_week.shift(week, 1)))
    head = next(line for line in lines if line.startswith("BROUILLON DU MOTEUR"))
    assert iso_week.shift(week, 1) in head
    assert any("[1]" in line for line in lines), "the key session carries its number"


def test_the_review_reads_whether_the_preparation_s_hypotheses_hold():
    start = trajectory.window_start()
    through, day = iso_week.shift(start, 4), trajectory.review_day(iso_week.shift(start, 5))
    held = trajectory.simulate(through=through).store
    assert cycle_review.lines(held, day) == [text.HYPOTHESES_HOLD]
    short = trajectory.simulate(short={2: 0.6, 3: 0.6}, through=through).store
    [line] = cycle_review.lines(short, day)
    assert line.startswith("  Hypothèses tombées (replanification : ")
    assert "volume : depuis le départ actuel, la rampe n'atteint que" in line
    assert cycle_review.lines(_store(_general()), day) == [], "only a preparation is checked"
