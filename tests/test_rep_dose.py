"""A quality run's WER load: each repetition's share of its limit time, the recoveries
through ln(1 + work / recovery), and the bench that scores it against the session dose."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from redline.analysis import critical_speed, expected_rpe, ledger, race_estimate, rep_dose
from redline.evaluation import quality_dose

T_PACE_MPS = 1000 / 255  # Daniels' threshold pace at VDOT 50: 4:15/km


def reps(count: int, seconds: float, recovery_s: float, speed: float = T_PACE_MPS) -> list[dict]:
    out = []
    for number in range(count):
        rep = {"time_s": seconds, "dist_m": speed * seconds}
        if number + 1 < count:
            rep["recovery"] = {"time_s": recovery_s, "dist_m": 2.5 * recovery_s}
        out.append(rep)
    return out


def test_the_limit_reads_daniels_curve_then_the_critical_speed():
    assert rep_dose.limit_s(T_PACE_MPS, 50) / 60 == pytest.approx(67, abs=2)
    assert rep_dose.limit_s(2.6, 50) is None
    fit = critical_speed.Fit(4.2, 180.0, ())
    assert rep_dose.limit_s(4.5, 50, fit) == pytest.approx(180 / 0.3)
    assert rep_dose.limit_s(T_PACE_MPS, 50, fit) == rep_dose.limit_s(T_PACE_MPS, 50)


def test_five_by_seven_costs_more_than_seven_by_five_at_the_same_pace():
    long_reps = rep_dose.wer(reps(5, 420, 90), 0.0, 5 * 420 + 4 * 90, 50)
    short_reps = rep_dose.wer(reps(7, 300, 90), 0.0, 7 * 300 + 6 * 90, 50)
    assert long_reps is not None and short_reps is not None
    assert long_reps > short_reps
    shared = 35 * 60 / rep_dose.limit_s(T_PACE_MPS, 50)
    assert long_reps == pytest.approx(shared + math.log(1 + 2100 / 360), rel=1e-3)


def test_without_a_recovery_there_is_no_wer_load():
    assert rep_dose.wer([{"time_s": 1200, "dist_m": 4700}], 4.7, 1200, 50) is None


def test_an_easy_warm_up_adds_nothing_and_a_brisk_one_its_share():
    session = 5 * 420 + 4 * 90
    work_km = (5 * 420 * T_PACE_MPS + 4 * 90 * 2.5) / 1000
    bare = rep_dose.wer(reps(5, 420, 90), work_km, session, 50)
    easy = rep_dose.wer(reps(5, 420, 90), work_km + 1800 * 1.6 / 1000, session + 1800, 50)
    brisk = rep_dose.wer(reps(5, 420, 90), work_km + 1800 * 3.7 / 1000, session + 1800, 50)
    assert bare is not None and easy is not None and brisk is not None
    assert easy == pytest.approx(bare) and brisk > bare


class Store:
    def effort_rows(self):
        return [{"effort_key": "ten", "done_on": dt.date(2030, 9, 1), "distance_km": 10,
                 "duration_s": 41 * 60 + 21, "terrain": "flat", "kind": "race", "note": ""}]

    def strava_intake(self, activity_id):
        laps = [{"distance_m": 2000, "moving_s": 720}]
        for _ in range(4):
            laps += [{"distance_m": 1647, "moving_s": 420}, {"distance_m": 225, "moving_s": 90}]
        laps += [{"distance_m": 1647, "moving_s": 420}, {"distance_m": 1500, "moving_s": 540}]
        return {"distilled": {"id": activity_id, "sport": "Run", "laps": laps}}


def test_a_quality_run_reads_its_vdot_from_the_efforts_before_it():
    dose = rep_dose.quality_doses(Store())
    run = ledger.Row(dt.date(2030, 10, 1), "threshold", "strava:9", km=12.6, hours=1.0)
    found = dose(run)
    assert found is not None and found > 0
    assert dose(ledger.Row(dt.date(2030, 8, 1), "threshold", "strava:9", km=12.6,
                           hours=1.0)) is None
    assert dose(ledger.Row(dt.date(2030, 10, 2), "easy", "strava:9", km=10, hours=1)) is None


def test_the_rpe_reads_the_quality_dose_and_the_load_keeps_the_session_dose():
    run = ledger.Row(dt.date(2030, 10, 1), "threshold", km=12, hours=1.0, rpe=8)
    config = expected_rpe.Settings(weeks=26, min_sessions=3, settled_sessions=5, width=1.0,
                                   width_unsettled=1.5, exponent=2.0)
    history = expected_rpe.History([run], lambda row: None, lambda day: 3.0, config,
                                   quality_of=lambda row: 1.7)
    assert history.rpe_dose(run) == expected_rpe.Dose(1.7)
    assert history.dose(run) is not None and history.dose(run).value != 1.7
    plain = expected_rpe.History([run], lambda row: None, lambda day: 3.0, config)
    assert plain.rpe_dose(run) == plain.dose(run)


def test_the_bench_scores_both_doses_on_the_same_runs():
    config = expected_rpe.Settings(weeks=26, min_sessions=3, settled_sessions=5, width=1.0,
                                   width_unsettled=1.5, exponent=2.0)
    rows, whole, wer_load = [], {}, {}
    for day in range(8):
        row = ledger.Row(dt.date(2030, 10, 1) + dt.timedelta(days=7 * day), "threshold",
                         f"k{day}", km=12, hours=1.0, rpe=5 + 0.5 * day)
        rows.append(row)
        wer_load[row.idempotency_key] = math.exp((row.rpe - 4) / 2)
        whole[row.idempotency_key] = 30.0 + (7 - day) % 3
    rows.append(ledger.Row(dt.date(2031, 1, 1), "threshold", "missing", km=12, hours=1.0, rpe=7))
    found = quality_dose.score(rows, lambda row: whole.get(row.idempotency_key),
                               lambda row: wer_load.get(row.idempotency_key), config)
    assert [(item.kind, item.runs, item.paired) for item in found] == [("threshold", 9, 8)]
    assert found[0].reps_mae is not None and found[0].session_mae is not None
    assert found[0].reps_mae < found[0].session_mae


def test_the_vdot_of_the_estimate_is_the_one_the_dose_reads():
    efforts = Store().effort_rows()
    assert race_estimate.vdot(efforts[0]["distance_km"], efforts[0]["duration_s"]) == \
        pytest.approx(50, abs=0.1)


def test_the_command_prints_each_quality_kind(monkeypatch, capsys):
    import redline.storage
    from redline.cli import evaluate_quality_dose

    rows = [ledger.Row(dt.date(2030, 10, 1) + dt.timedelta(days=7 * day), "threshold",
                       "strava:9", km=12.6, hours=1.0, rpe=7) for day in range(4)]
    monkeypatch.setattr(redline.storage, "open_store", lambda name: Store())
    monkeypatch.setattr(ledger, "parse_ledger", lambda store: rows)
    monkeypatch.setattr("sys.argv", ["evaluate-quality-dose"])
    assert evaluate_quality_dose.main() == 0
    assert capsys.readouterr().out.startswith("threshold    runs   4  paired")
