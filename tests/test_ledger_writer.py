"""The typed ledger writer.

A source returns `average_heartrate: 156.61`, the `FCmoy` column wants `\\d{2,3}`, and rounding
used to be left to the model's judgement on every write. One malformed cell made the WHOLE
ledger unreadable, and everything downstream with it.

The Markdown ledger no longer exists: PostgreSQL constrains each measure per column, and an
out-of-type value is refused by the database, not by a re-read. What remains, and what these
tests freeze, is the INPUT BOUNDARY: `ledger-add` rounds what the source returns as a
float, refuses by naming the column what no rounding can fix, and never touches a cell that
is already conforming.
"""

import sys

import pytest

from redline.model import ledger as table

# --------------------------------------------------------------- rounding

@pytest.mark.parametrize("column, source, written", [
    ("hr_avg", "156.61", "157"),      # average_heartrate
    ("cadence", "168.884", "169"),       # average_cadence x2
    ("dplus", "64.7", "65"),            # elevation_gain
    ("wbgt", "-3.4", "-3"),          # a winter night, and the sign matters
    ("km", "6.8396", "6.84"),
    ("hr_avg", "156,61", "157"),      # a decimal comma comes from the same prose
])
def test_strava_returns_floats_and_the_column_wants_integers(column, source, written):
    """The source lies about the type, and the rounding lives here."""
    assert table.canonical_value(column, source) == written


def test_a_zone_pain_is_rounded_to_the_tenth():
    assert table.pain_reading("1.55") == {"state": "known", "value": 1.6}
    assert table.pain_reading("?") == {"state": "unavailable"}
    with pytest.raises(table.LedgerError):
        table.pain_reading("11")


def test_a_cell_already_valid_is_never_rewritten():
    """The invariant that allows running this function over the whole row.

    "A known measure is immutable" (a ledger rule, now enforced by a PostgreSQL trigger)
    starts here: what gets corrected was never data, it was a refusal to read.
    """
    for item in table.INPUTS:
        for value in ("", table.UNKNOWN):
            assert table.canonical_value(item.name, value) == value
    conforming = {
        "date": "2026-08-10", "type": "easy", "km": "6.84", "time": "0:41:56",
        "pace": "6:08", "hr_avg": "157", "hr_max": "168", "cadence": "169",
        "dplus": "65", "dminus": "64", "wbgt": "20", "rpe": "2",
        "enjoyment": "6", "strava": "90000000003", "note": "EF du jour",
    }
    for name, value in conforming.items():
        assert table.canonical_value(name, value) == value


def test_what_is_not_a_number_is_not_invented():
    """A range ("1-2") has no rounding: data is not fabricated."""
    assert table.canonical_value("rpe", "1-2") == "1-2"
    assert table.canonical_value("type", "footing") == "footing"


# --------------------------------------------------------------- the typed writer

def test_the_typed_writer_rounds_what_strava_returns(monkeypatch, capsys):
    """The clean path: RAW values go in, a conforming row comes out."""
    add = _load_writer()
    monkeypatch.setattr(sys, "argv", [
        "ledger-add", "--dry-run",
        "--date", "2026-08-10", "--type", "easy",
        "--km", "6.8396", "--time", "0:41:56", "--pace", "6:08", "--hr-avg", "156.61",
        "--hr-max", "168", "--cadence", "168.884", "--dplus", "64.7", "--dminus", "64",
        "--wbgt", "20", "--strava", "90000000003", "--note", "EF | du jour"])
    assert add.main() == 0
    captured = capsys.readouterr()
    assert "'hr_avg': '157'" in captured.out and "'km': '6.84'" in captured.out
    assert "EF | du jour" in captured.out  # PostgreSQL keeps the text, pipe included.


def test_the_typed_writer_refuses_and_names_the_column(monkeypatch, capsys):
    """What no rounding can fix is refused BEFORE any write."""
    add = _load_writer()
    monkeypatch.setattr(sys, "argv", [
        "ledger-add", "--date", "2026-08-10", "--type", "footing", "--km", "6.84"])
    assert add.main() == 1
    assert "type" in capsys.readouterr().err


def _zones(monkeypatch, add, zones=("knee",)):
    """The monitored zones live in the database: the CLI reads them, the test fixes them."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(add, "_active_zones", lambda url: list(zones))


@pytest.mark.parametrize("trace", ["", "?"])
def test_a_wake_reading_without_a_trace_is_not_written(monkeypatch, capsys, trace):
    """Without `--pain`, the measure used to be born `pending` silently and the athlete's
    answer lived only in the note. The gate never read it."""
    add = _load_writer()
    _zones(monkeypatch, add)
    argv = ["ledger-add", "--dry-run", "--date", "2026-10-11", "--type", "wake-trace",
            "--note", "Trace légère déclarée par Sam"]
    monkeypatch.setattr(sys, "argv", argv + (["--pain", trace] if trace else []))
    assert add.main() == 1
    assert "réveil sans trace" in capsys.readouterr().err


@pytest.mark.parametrize("word, value", [("none", "0"), ("Light", "1"), ("light", "1"),
                                         ("very light", "0.5"), ("clear", "2")])
def test_the_athletes_word_becomes_the_value(monkeypatch, capsys, word, value):
    add = _load_writer()
    _zones(monkeypatch, add)
    monkeypatch.setattr(sys, "argv", [
        "ledger-add", "--dry-run", "--date", "2026-10-11", "--type", "wake-trace",
        "--pain", word])
    assert add.main() == 0
    assert f"'pain': {{'knee': '{value}'}}" in capsys.readouterr().out


def test_with_several_zones_each_pain_names_its_own(monkeypatch, capsys):
    add = _load_writer()
    _zones(monkeypatch, add, ("talon", "cheville"))
    monkeypatch.setattr(sys, "argv", [
        "ledger-add", "--dry-run", "--date", "2026-10-11", "--type", "easy",
        "--pain", "talon=2", "--pain", "cheville=?"])
    assert add.main() == 0
    assert "'pain': {'talon': '2', 'cheville': '?'}" in capsys.readouterr().out


@pytest.mark.parametrize("given, refused", [
    (["2"], "name the zone"), (["hanche=1"], "unknown zone"),
    (["talon=1", "talon=2"], "given twice")])
def test_a_pain_without_a_clear_zone_is_refused(monkeypatch, capsys, given, refused):
    add = _load_writer()
    _zones(monkeypatch, add, ("talon", "cheville"))
    argv = ["ledger-add", "--dry-run", "--date", "2026-10-11", "--type", "easy"]
    for value in given:
        argv += ["--pain", value]
    monkeypatch.setattr(sys, "argv", argv)
    assert add.main() == 1
    assert refused in capsys.readouterr().err


def test_a_wake_reading_requires_a_trace_per_zone(monkeypatch, capsys):
    add = _load_writer()
    _zones(monkeypatch, add, ("talon", "cheville"))
    monkeypatch.setattr(sys, "argv", [
        "ledger-add", "--dry-run", "--date", "2026-10-11", "--type", "wake-trace",
        "--pain", "talon=aucune"])
    assert add.main() == 1
    assert "réveil sans trace pour cheville" in capsys.readouterr().err


def _load_writer():
    from redline.cli import ledger_add
    return ledger_add


def test_existing_observation_is_rejected_before_staging(monkeypatch):
    from unittest.mock import MagicMock

    import redline.storage

    writer = _load_writer()
    store = MagicMock()
    store.observation_id.return_value = "existing-id"
    monkeypatch.setenv("REDLINE_TURN_ID", "active-turn")
    monkeypatch.setattr(redline.storage, "Database", MagicMock())
    monkeypatch.setattr(redline.storage, "OperationalStore", lambda *args: store)
    with pytest.raises(writer.table.LedgerError, match=r"ledger-complete"):
        writer._write_database("test", {"date": "2026-09-11", "type": "easy"}, {},
                               "strava:90000000004")
    store.stage_command.assert_not_called()
    store.add_observation.assert_not_called()
