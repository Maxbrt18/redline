"""Les signaux faibles : ce que le script sort, et ce qu'il refuse de sortir.

`protocols.md` § D lists five checks "that cannot lie". None of them was ever
computed: they relied on the coach thinking of it, on a Sunday. This is the machinery
meant to catch the injury before the confession -- and the 09/07 confession came two
months late.
"""

import pytest

from scripts import signals
from scripts.ledger import Row


def _row(week_day: str, **kwargs):
    import datetime as dt
    return Row(date=dt.date.fromisoformat(week_day), type=kwargs.pop("type", "EF"), **kwargs)


def _labels(rows):
    return sorted({row.week for row in rows})


# ------------------------------------------------------------------ cadence

def test_a_cadence_jump_at_easy_pace_is_flagged():
    """Une hausse non expliquée ouvre une vérification, sans porter sa cause."""
    rows = [_row("2026-08-03", cadence=160), _row("2026-08-10", cadence=170)]
    found = signals.signals(rows, _labels(rows), frozenset())
    assert any("cadence" in signal.label for signal in found)


def test_a_confirmed_voluntary_cadence_exposure_is_not_a_spontaneous_signal():
    rows = [_row("2026-08-03", cadence=160, idempotency_key="old"),
            _row("2026-08-10", cadence=170, idempotency_key="worked")]

    found = signals.signals(rows, _labels(rows), frozenset({"worked"}))

    assert not any("cadence" in signal.label for signal in found)


def test_a_small_wobble_is_not():
    rows = [_row("2026-08-03", cadence=160), _row("2026-08-10", cadence=162)]
    assert signals.signals(rows, _labels(rows), frozenset()) == []


def test_hilly_and_quality_sessions_do_not_pollute_the_cadence():
    """In trail, on hills and in quality work the cadence lives its own life: measuring
    it there reads the terrain, not the injury."""
    rows = [_row("2026-08-03", cadence=160),
            _row("2026-08-10", cadence=190, type="côtes"),
            _row("2026-08-11", cadence=161)]
    assert signals.signals(rows, _labels(rows), frozenset()) == []


# ------------------------------------------------------------------ plaisir

def test_falling_enjoyment_is_no_longer_a_signal():
    # Retiré le 2026-10-09 : l'alarme n'avait ni source ni confirmation dans le registre.
    rows = [_row("2026-07-27", enjoyment=8), _row("2026-08-03", enjoyment=7),
            _row("2026-08-10", enjoyment=5)]
    found = signals.signals(rows, _labels(rows), frozenset())
    assert not any("plaisir" in signal.label for signal in found)
    assert "Plaisir" not in signals.render(rows, _labels(rows), frozenset())


# ------------------------------------------------------------- plus longue

def test_a_longer_long_run_is_no_longer_a_signal():
    """Aucun pourcentage fixe ne juge la progression de la sortie longue depuis la
    décision de Sam du 2026-09-27 : un allongement se décide, il ne sonne pas."""
    rows = [_row("2026-08-03", km=10.0), _row("2026-08-10", km=14.0)]
    assert not any("plus longue" in s.label for s in signals.signals(rows, _labels(rows), frozenset()))


def test_a_group_run_counts_as_a_long_run():
    """Capping it to EF/SL would make the 29/07 group session -- the one that blew the
    week's load open -- read as a quiet week."""
    rows = [_row("2026-08-03", km=8.0), _row("2026-08-10", km=12.0, type="course")]
    assert signals.longest_run(rows, _labels(rows))[-1][1] == 12.0


def test_walking_and_cycling_never_count_as_a_long_run():
    rows = [_row("2026-08-03", km=8.0), _row("2026-08-10", km=25.0, type="rando")]
    assert signals.longest_run(rows, _labels(rows))[-1][1] is None


# --------------------------------------------------------------- rendering

def test_a_quiet_week_says_so_rather_than_printing_nothing():
    """A check that disappears when it has nothing to say reads exactly like a check
    that passed -- and this one is printed into the weekly review."""
    rows = [_row("2026-08-03", cadence=160), _row("2026-08-10", cadence=160)]
    assert "Rien à signaler" in signals.render(rows, _labels(rows), frozenset())


def test_the_strava_half_is_named_in_the_output():
    """Four of the § D checks live at Strava and stay Claude's job. Printing the table
    without saying so would let it read as the whole of § D."""
    rows = [_row("2026-08-03", cadence=160)]
    assert "restent à lire chez Strava" in signals.render(rows, _labels(rows), frozenset())


@pytest.mark.parametrize("count", [1, 3])
def test_it_never_crashes_on_a_short_history(count):
    rows = [_row(f"2026-08-{2 + week * 7:02d}", cadence=160) for week in range(count)]
    signals.render(rows, _labels(rows), frozenset())


# ------------------------------------------ FC à allure comparable (alarme n°4)

def _ef(day: str, pace_s: float, hr: int, km: float = 7.0, **kwargs):
    return _row(day, km=km, hours=pace_s * km / 3600, hr_avg=hr, **kwargs)


def _baseline():
    """Six EF plates de référence, à 6:15-6:25/km et ~145 bpm, sur août."""
    return [_ef("2026-08-10", 375, 144), _ef("2026-08-13", 380, 146),
            _ef("2026-08-17", 385, 145), _ef("2026-08-20", 378, 143),
            _ef("2026-08-24", 382, 147), _ef("2026-08-27", 376, 145)]


def test_two_easy_runs_with_a_high_heart_rate_at_the_same_pace_are_flagged():
    rows = [*_baseline(), _ef("2026-09-01", 380, 153), _ef("2026-09-03", 379, 154)]
    found = signals.signals(rows, _labels(rows), frozenset())
    alarm = [signal for signal in found if "FC haute" in signal.label]
    assert alarm and "Alarme n°4" in alarm[0].detail


def test_a_single_high_run_is_not_a_trend():
    rows = [*_baseline(), _ef("2026-09-01", 380, 153), _ef("2026-09-03", 379, 146)]
    assert not any("FC haute" in s.label for s in signals.signals(rows, _labels(rows), frozenset()))


@pytest.mark.parametrize("doubt", ["sans_brassard", "contexte"])
def test_a_doubtful_heart_rate_neither_drifts_nor_feeds_the_reference(doubt):
    """07/10 : couru sans brassard, déclaré le jour même, et pourtant compté par la porte
    deux semaines de suite (audit d'effet du 2026-10-19)."""
    rows = [*_baseline(), _ef("2026-09-01", 380, 153, hr_doubt=doubt),
            _ef("2026-09-03", 379, 154, hr_doubt=doubt)]
    assert [d.date.isoformat() for d in signals.hr_at_pace(rows)][-1] == "2026-08-27"
    assert not any("FC haute" in s.label for s in signals.signals(rows, _labels(rows), frozenset()))


def test_only_a_strapless_heart_rate_loses_its_cap_value():
    assert _ef("2026-09-01", 380, 162, hr_doubt="sans_brassard").hr_valid is None
    assert _ef("2026-09-01", 380, 162, hr_doubt="contexte").hr_valid == 162
    assert _ef("2026-09-01", 380, 162).hr_valid == 162


def test_a_faster_run_is_compared_to_runs_of_its_own_pace_only():
    """À 5:30/km la FC est plus haute par construction : sans voisines de même
    allure, il n'y a pas de référence, donc pas d'alarme."""
    rows = [*_baseline(), _ef("2026-09-01", 330, 160), _ef("2026-09-03", 331, 161)]
    assert signals.hr_at_pace(rows)[-1].date.isoformat() == "2026-08-27"
    assert not any("FC haute" in s.label for s in signals.signals(rows, _labels(rows), frozenset()))


def test_hot_or_hilly_runs_neither_trigger_nor_serve_as_reference():
    hot = [_ef("2026-09-01", 380, 158, wbgt=25.0), _ef("2026-09-03", 379, 159, wbgt=24.0)]
    hilly = [_ef("2026-09-05", 380, 158, dplus=200), _ef("2026-09-06", 379, 159, dplus=180)]
    rows = _baseline() + hot + hilly
    assert not any("FC haute" in s.label for s in signals.signals(rows, _labels(rows), frozenset()))


def test_the_render_names_a_long_run_stall():
    import datetime as dt
    rows = []
    for day, km in (("2026-09-06", 10.4), ("2026-09-13", 11.53),
                    ("2026-09-20", 10.8), ("2026-09-27", 10.5), ("2026-10-04", 11.0)):
        rows.append(_row(day, type="SL", km=km, hours=km / 8))
    text = signals.render(rows, _labels(rows), frozenset(), today=dt.date(2026, 10, 5))
    assert "sans nouveau record depuis 3 semaine(s)" in text
