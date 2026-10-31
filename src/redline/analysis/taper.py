"""The taper, measured: how much the weeks before a race really drop.

`taper_weeks` places the taper; nothing checked that it happened. The reference is the
mean running volume of the REFERENCE_WEEKS weeks before it, assimilation weeks skipped:
an assimilation week already drops, and counting it would hide part of the cut. The
race week counts the days before the race only, and never the race itself.

The length and the band are the race's, the rulebook's when it leaves them unsaid
(`model/race.py`): a drop of 41 to 60 % of the volume is where meta-analyses find the
benefit (Bosquet et al. 2007, Wang et al. 2023). It bounds the taper as a whole, never a single week: a progressive taper
drops little at first and a lot at the end. So a cut beyond the ceiling is judged as soon
as it shows, and a loose taper only once the race week is written or over.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from redline.coach.fr import ledger as text
from redline.model import race as race_model
from redline.model import rulebook, session_kinds, transitions
from redline.model.vocabulary import Decider

from . import ledger

REFERENCE_WEEKS = 2
#: Weeks searched back for the reference before giving up: a long block of assimilation
#: weeks means no comparable load.
REFERENCE_SEARCH_WEEKS = 6
#: Runs the taper may drop below the reference frequency, race week aside: the taper
#: keeps its frequency and cuts each session.
FREQUENCY_SLACK = 1


@dataclass(frozen=True)
class Window:
    race: race_model.Race
    weeks: tuple[str, ...]          # the taper, race week last
    reference: tuple[str, ...]      # the weeks it is compared with, oldest first

    @property
    def race_on(self) -> dt.date:
        return self.race.on

    @property
    def race_week(self) -> str:
        return self.weeks[-1]


def window(race: race_model.Race) -> Window:
    race_week = transitions.week_of(race.on)
    weeks = tuple(transitions.shift(race_week, -offset)
                  for offset in reversed(range(race.taper_weeks)))
    skipped = set(rulebook.current().parameters.assimilation_weeks)
    reference: list[str] = []
    for back in range(1, REFERENCE_SEARCH_WEEKS + 1):
        week = transitions.shift(weeks[0], -back)
        if week not in skipped:
            reference.insert(0, week)
        if len(reference) == REFERENCE_WEEKS:
            break
    return Window(race=race, weeks=weeks, reference=tuple(reference))


def race_window(store: ledger.LedgerStore, week: str) -> Window | None:
    """The typed race whose taper holds `week`, run or still ahead."""
    for race in race_model.typed(store):
        found = window(race)
        if week in found.weeks:
            return found
    return None


def _before_race(rows: list[ledger.Row], week: str, race_on: dt.date) -> list[ledger.Row]:
    return [row for row in rows if row.week == week and row.date < race_on]


@dataclass(frozen=True)
class Cut:
    reference_km: float
    reference_runs: float
    weeks: tuple[tuple[str, float], ...]    # taper weeks counted so far, with their km

    @property
    def percent(self) -> float:
        """The drop of the taper's mean weekly volume so far; negative when it rose."""
        mean = sum(km for _, km in self.weeks) / len(self.weeks)
        return (1 - mean / self.reference_km) * 100


def _reference(rows: list[ledger.Row], frame: Window) -> tuple[float, float] | None:
    volumes = [ledger.week_volume([row for row in rows if row.week == week])
               for week in frame.reference]
    km = sum(volume.km_run for volume in volumes) / len(volumes) if volumes else 0
    if not km:
        return None
    return km, sum(volume.runs for volume in volumes) / len(volumes)


def measured(rows: list[ledger.Row], frame: Window, through: str) -> Cut | None:
    """The drop the ledger shows, taper weeks up to `through` included."""
    reference = _reference(rows, frame)
    if reference is None or through not in frame.weeks:
        return None
    counted = frame.weeks[:frame.weeks.index(through) + 1]
    weeks = tuple((week, ledger.week_volume(_before_race(rows, week, frame.race_on)).km_run)
                  for week in counted)
    return Cut(reference_km=reference[0], reference_runs=reference[1], weeks=weeks)


def _planned_runs(sessions: list[dict], race_on: dt.date) -> list[dict]:
    return [session for session in sessions
            if session.get("kind") == "run" and session.get("status") != "cancelled"
            and session.get("workout") != session_kinds.RACE
            and transitions.day_of(session["date"]) < race_on]


def planned(rows: list[ledger.Row], frame: Window, week: str,
            sessions: list[dict]) -> Cut | None:
    """The drop a revision of `week` plans: the weeks before it as run, then its own
    sessions as planned."""
    before = measured(rows, frame, transitions.shift(week, -1)) \
        if week != frame.weeks[0] else None
    reference = _reference(rows, frame)
    if reference is None or week not in frame.weeks:
        return None
    runs = _planned_runs(sessions, frame.race_on)
    km = sum(float(session.get("distance_target_km") or session.get("distance_cap_km") or 0)
             for session in runs)
    return Cut(reference_km=reference[0], reference_runs=reference[1],
               weeks=(*(before.weeks if before else ()), (week, km)))


def plan_errors(rows: list[ledger.Row], frame: Window, week: str,
                revision: dict, where: str = "revision") -> list[str]:
    """The band binds the coach's judgment. A rule (a pain cap) or the athlete's
    request may cut deeper or keep more; the readback still shows the drop."""
    low, high = frame.race.taper_cut_min_pct, frame.race.taper_cut_max_pct
    if (revision.get("decision") or {}).get("by") != Decider.JUDGMENT:
        return []
    cut = planned(rows, frame, week, revision.get("sessions") or [])
    if cut is None:
        return []
    problems = []
    span = f"{frame.reference[0]}..{frame.reference[-1]}"
    if high is not None and cut.percent > high:
        problems.append(
            f"{where}: the taper's mean volume drops {cut.percent:.0f} % from "
            f"{cut.reference_km:.1f} km ({span}), beyond the {high} % ceiling -- keep "
            "more, or decide by rule or request")
    if low is not None and week == frame.race_week and cut.percent < low:
        problems.append(
            f"{where}: the taper's mean volume drops only {cut.percent:.0f} % from "
            f"{cut.reference_km:.1f} km ({span}), under the {low} % floor -- cut more, "
            "or decide by rule or request")
    runs = len(_planned_runs(revision.get("sessions") or [], frame.race_on))
    floor = int(cut.reference_runs) - FREQUENCY_SLACK
    if week != frame.race_week and runs < floor:
        problems.append(
            f"{where}: {runs} planned run(s) against {cut.reference_runs:g} a week in "
            f"{span} -- the taper keeps its frequency (at least {floor}) and shortens "
            "each run")
    return problems


def _closed(frame: Window, week: str, today: dt.date, closing: str | None) -> bool:
    if week == frame.race_week:
        return today >= frame.race_on
    return week == closing or transitions.monday_of(week) + dt.timedelta(days=7) <= today


def report_lines(store: ledger.LedgerStore, rows: list[ledger.Row], label: str,
                 today: dt.date, closing: bool = False) -> list[str]:
    """The drop so far, for a week of the taper. Only closed weeks are judged: a week
    in progress is shown, never compared, since its first days would read as a deep
    cut. A loose taper is only said once the race is run: before, the weeks left can
    still cut."""
    frame = race_window(store, label)
    if frame is None:
        return []
    span = f"{transitions.short(frame.reference[0])}-{transitions.short(frame.reference[-1])}"
    cut = measured(rows, frame, label)
    if cut is None:
        return [text.TAPER_NO_REFERENCE.format(span=span)]
    done = tuple((week, km) for week, km in cut.weeks
                 if _closed(frame, week, today, label if closing else None))
    weeks = " · ".join(
        (text.TAPER_WEEK if (week, km) in done else text.TAPER_WEEK_OPEN).format(
            week=transitions.short(week), km=km)
        for week, km in cut.weeks)
    if not done:
        return [text.TAPER_OPENING.format(reference_km=cut.reference_km, span=span,
                                          weeks=weeks)]
    closed = Cut(reference_km=cut.reference_km, reference_runs=cut.reference_runs,
                 weeks=done)
    low, high = frame.race.taper_cut_min_pct, frame.race.taper_cut_max_pct
    band = text.TAPER_BAND.format(low=low, high=high) if low is not None and high is not None else ""
    lines = [text.TAPER.format(reference_km=cut.reference_km, span=span, weeks=weeks,
                               percent=closed.percent, band=band)]
    if high is not None and closed.percent > high:
        lines.append(text.TAPER_TOO_CUT.format(high=high))
    if low is not None and label == frame.race_week and today >= frame.race_on \
            and closed.percent < low:
        lines.append(text.TAPER_LOOSE.format(low=low))
    return lines
