"""The taper, measured: how much the weeks before a race really drop.

The taper belongs to the preparation that holds the race: the weeks its trame places in
the taper phase, the band of the template it was generated from. A race no preparation
holds has no taper planned, so nothing to measure. The reference is the
mean running volume of the REFERENCE_WEEKS weeks before it, the weeks a cycle planned low
skipped: a deload already drops, and counting it would hide part of the cut. The
race week counts the days before the race only, and never the race itself.

The band bounds the taper as a whole, never a single week: a progressive taper drops
little at first and a lot at the end. So a cut beyond the ceiling is judged as soon
as it shows, and a loose taper only once the race week is written or over.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from redline.coach.fr import ledger as text
from redline.model import cycle_templates, iso_week, paths, session_kinds, transitions
from redline.model import race as race_model
from redline.model.cycle_templates import Range
from redline.model.plan_generator import Trame
from redline.model.vocabulary import CycleKind, Decider, PlanPhase, RaceState, SessionStatus

from . import ledger

#: The states of a goal whose taper is read: ahead, or run.
TAPERED = frozenset({RaceState.ACTIVE, RaceState.RUN})
REFERENCE_WEEKS = 2
#: Weeks searched back for the reference before giving up: a long block of weeks planned
#: low means no comparable load.
REFERENCE_SEARCH_WEEKS = 6
#: Runs the taper may drop below the reference frequency, race week aside: the taper
#: keeps its frequency and cuts each session.
FREQUENCY_SLACK = 1


@dataclass(frozen=True)
class Window:
    race: race_model.Race
    weeks: tuple[str, ...]          # the taper, race week last
    reference: tuple[str, ...]      # the weeks it is compared with, oldest first
    #: The drop of the taper's mean weekly volume its template allows, in %.
    band: Range | None = None

    @property
    def race_on(self) -> dt.date:
        return self.race.on

    @property
    def race_week(self) -> str:
        return self.weeks[-1]


def window(race: race_model.Race, trame: Trame, band: Range | None,
           planned_low: Collection[str] = ()) -> Window:
    """The taper of the race's trame and the weeks before it that it is compared with."""
    weeks = tuple(week.label for week in trame.weeks if week.phase == PlanPhase.TAPER)
    skipped = set(planned_low) - set(weeks)
    reference: list[str] = []
    for back in range(1, REFERENCE_SEARCH_WEEKS + 1):
        week = iso_week.shift(weeks[0], -back)
        if week not in skipped:
            reference.insert(0, week)
        if len(reference) == REFERENCE_WEEKS:
            break
    return Window(race=race, weeks=weeks, reference=tuple(reference), band=band)


def race_window(store: Any, week: str) -> Window | None:
    """The taper that holds `week`: the taper phase of the preparation that holds the
    week, its race run or still ahead. A race cancelled or abandoned binds no taper band."""
    version = store.cycle_for_week(week)
    held = version.week(week) if version is not None else None
    if (version is None or held is None or version.kind != CycleKind.PREPARATION
            or held.phase != PlanPhase.TAPER):
        return None
    race = next((race for race in race_model.typed(store)
                 if race.key == version.race_key and race.state in TAPERED), None)
    if race is None:
        return None
    template = cycle_templates.load(paths.vault_root()).preparation_named(version.trame.template)
    return window(race, version.trame, template.taper_cut_pct if template else None,
                  store.planned_low_weeks())


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
            if session.get("kind") == "run" and session.get("status") != SessionStatus.CANCELLED
            and session.get("workout") != session_kinds.RACE
            and transitions.day_of(session["date"]) < race_on]


def planned(rows: list[ledger.Row], frame: Window, week: str,
            sessions: list[dict]) -> Cut | None:
    """The drop a revision of `week` plans: the weeks before it as run, then its own
    sessions as planned."""
    before = measured(rows, frame, iso_week.shift(week, -1)) \
        if week != frame.weeks[0] else None
    reference = _reference(rows, frame)
    if reference is None or week not in frame.weeks:
        return None
    runs = _planned_runs(sessions, frame.race_on)
    km = sum(float(session.get("distance_target_km") or session.get("distance_cap_km") or 0)
             for session in runs)
    return Cut(reference_km=reference[0], reference_runs=reference[1],
               weeks=(*(before.weeks if before else ()), (week, km)))


def _bounds(frame: Window) -> tuple[int | None, int | None]:
    if frame.band is None:
        return None, None
    return round(frame.band.low), round(frame.band.high)


def plan_errors(rows: list[ledger.Row], frame: Window, week: str,
                revision: dict, where: str = "revision") -> list[str]:
    """The band binds the coach's judgment. A rule (a pain cap) or the athlete's
    request may cut deeper or keep more; the readback still shows the drop."""
    low, high = _bounds(frame)
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
    return week == closing or iso_week.monday(week) + dt.timedelta(days=7) <= today


def report_lines(store: Any, rows: list[ledger.Row], label: str,
                 today: dt.date, closing: bool = False) -> list[str]:
    """The drop so far, for a week of the taper. Only closed weeks are judged: a week
    in progress is shown, never compared, since its first days would read as a deep
    cut. A loose taper is only said once the race is run: before, the weeks left can
    still cut."""
    frame = race_window(store, label)
    if frame is None:
        return []
    span = f"{iso_week.short(frame.reference[0])}-{iso_week.short(frame.reference[-1])}"
    cut = measured(rows, frame, label)
    if cut is None:
        return [text.TAPER_NO_REFERENCE.format(span=span)]
    done = tuple((week, km) for week, km in cut.weeks
                 if _closed(frame, week, today, label if closing else None))
    weeks = " · ".join(
        (text.TAPER_WEEK if (week, km) in done else text.TAPER_WEEK_OPEN).format(
            week=iso_week.short(week), km=km)
        for week, km in cut.weeks)
    if not done:
        return [text.TAPER_OPENING.format(reference_km=cut.reference_km, span=span,
                                          weeks=weeks)]
    closed = Cut(reference_km=cut.reference_km, reference_runs=cut.reference_runs,
                 weeks=done)
    low, high = _bounds(frame)
    band = text.TAPER_BAND.format(low=low, high=high) if low is not None and high is not None else ""
    lines = [text.TAPER.format(reference_km=cut.reference_km, span=span, weeks=weeks,
                               percent=closed.percent, band=band)]
    if high is not None and closed.percent > high:
        lines.append(text.TAPER_TOO_CUT.format(high=high))
    if low is not None and label == frame.race_week and today >= frame.race_on \
            and closed.percent < low:
        lines.append(text.TAPER_LOOSE.format(low=low))
    return lines
