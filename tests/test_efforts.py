"""All-out efforts and training breaks: declared through `fact-write`, read as typed facts,
and the easy runs' efficiency that sits next to an estimate."""

from __future__ import annotations

import argparse
import datetime as dt

import pytest
from conftest import load_script

from redline.analysis import ledger, signals
from redline.cli import _args
from redline.model import efforts
from redline.model.errors import InputError
from redline.model.vocabulary import EffortKind, EffortTerrain

fact_write = load_script("fact-write")


def parse(*argv: str):
    return fact_write.parser().parse_args(argv)


@pytest.mark.parametrize(("text", "seconds"), [("39:24", 2364), ("1:27:12", 5232),
                                               ("12:30.5", 750.5)])
def test_a_clock_reads_in_seconds(text, seconds):
    assert _args.clock(text) == seconds


@pytest.mark.parametrize("text", ["39", "1:75:00", "aa:bb", "0:00", "-1:00"])
def test_a_wrong_clock_is_refused(text):
    with pytest.raises(argparse.ArgumentTypeError):
        _args.clock(text)


def test_an_effort_carries_its_typed_fields():
    args = parse("effort", "--key", "ten-2030", "--date", "2030-03-02", "--km", "10",
                 "--time", "40:00", "--terrain", "flat", "--kind", "race", "--note", " solo ")
    entity, kind, payload = fact_write.payload_of(args)
    assert (entity, kind) == ("effort:ten-2030", "effort.upsert")
    assert payload == {"effort_key": "ten-2030", "done_on": dt.date(2030, 3, 2),
                       "distance_km": 10.0, "duration_s": 2400.0, "terrain": "flat",
                       "kind": "race", "note": "solo"}


def test_a_break_ends_on_or_after_its_start():
    args = parse("break", "--key", "shin", "--starts-on", "2030-05-01", "--ends-on",
                 "2030-06-15")
    _, kind, payload = fact_write.payload_of(args)
    assert kind == "break.upsert" and payload["ends_on"] == dt.date(2030, 6, 15)
    with pytest.raises(InputError):
        fact_write.payload_of(parse("break", "--key", "shin", "--starts-on", "2030-05-01",
                                    "--ends-on", "2030-04-01"))


def test_stored_rows_read_as_typed_facts_oldest_first():
    found = efforts.efforts([
        {"effort_key": "b", "done_on": "2030-05-01", "distance_km": 3.6, "duration_s": 800,
         "terrain": "flat", "kind": "test", "note": None},
        {"effort_key": "a", "done_on": dt.date(2030, 3, 2), "distance_km": 10,
         "duration_s": 2400, "terrain": "hilly", "kind": "race", "note": "x"}])
    assert [effort.key for effort in found] == ["a", "b"]
    assert found[1].kind is EffortKind.TEST and found[1].flat and found[1].note == ""
    assert found[0].terrain is EffortTerrain.HILLY and not found[0].flat
    pause = efforts.breaks([{"break_key": "s", "starts_on": "2030-05-01",
                             "ends_on": "2030-05-21"}])[0]
    assert pause.days == 21


def _easy(day: dt.date, minutes_per_km: float, hr: int) -> ledger.Row:
    return ledger.Row(day, "easy", km=8, hours=8 * minutes_per_km / 60, hr_avg=hr)


def test_the_efficiency_compares_the_first_and_last_six_weeks():
    start = dt.date(2030, 6, 1)
    rows = [_easy(start + dt.timedelta(days=day), 6.0, 150) for day in range(0, 40, 10)]
    rows += [_easy(start + dt.timedelta(days=day), 5.7, 150) for day in range(120, 160, 10)]
    today = start + dt.timedelta(days=160)
    found = signals.efficiency(rows, dt.date(2030, 1, 1), today)
    assert found is not None and found.since == start
    assert found.change_pct == pytest.approx((6.0 / 5.7 - 1) * 100)
    assert (found.runs_before, found.runs_now) == (4, 4)


def test_no_efficiency_while_the_two_stretches_overlap():
    start = dt.date(2030, 6, 1)
    rows = [_easy(start + dt.timedelta(days=day), 6.0, 150) for day in range(0, 60, 5)]
    assert signals.efficiency(rows, start, start + dt.timedelta(days=60)) is None
