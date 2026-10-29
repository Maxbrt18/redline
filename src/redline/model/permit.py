"""Easy-run permit: the permit state is COMPUTED.

Nothing is stored except the coach's sealed choice (`permit_choices`): what the athlete
ran comes from the ledger, so does the next-day trace; life-load declarations come from
the daily state, the group flag from the Strava capture. The status (supervised, permit,
withdrawal, restriction) derives from them, like any state derived from the vault.

What is NOT detected here, and the report says so: a new route (it would need track
comparison), and a big form signal while its threshold is unset.
Method: `coaching/protocols.md`, permit section.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

from redline.coach import dates
from redline.coach.fr import permit as text
from redline.model.vocabulary import LifeLoad, Sleep

from . import session_kinds

#: The supervised period lasts this many ISO weeks, including the week of the first
#: sealed choice; the permit is judged at the end of the last one.
PERMIT_WEEKS = 3
#: Criteria for obtaining the permit.
MIN_EF = 5
TOLERANCE_MIN = 10
#: Length of the supervised period after a withdrawal trigger.
WITHDRAWAL_DAYS = 14
#: Big form: pace progression at equal heart rate. Without history there is no honest
#: threshold; it is set when the permit is judged. Until then the signal is displayed
#: and triggers nothing.
BIG_FORM_THRESHOLD: float | None = None
#: Waking trace: 2 = clear (none 0, very light 0.5, light 1, clear 2).
NET_TRACE = 2


@dataclass(frozen=True)
class Run:
    """A ledger run, reduced to what the permit reads."""

    date: dt.date
    type: str
    minutes: float | None
    hr_avg: int | None
    strava_id: int | None = None


@dataclass
class EfLine:
    session_key: str
    date: dt.date
    coach_minutes: int
    cap_minutes: int | None
    actual_minutes: float | None = None
    hr_avg: int | None = None
    next_trace: float | None = None
    group: bool | None = None
    status: str = "upcoming"  # upcoming · run · skipped
    triggers: list[str] = field(default_factory=list)

    @property
    def gap(self) -> float | None:
        if self.actual_minutes is None:
            return None
        return self.actual_minutes - self.coach_minutes

    @property
    def agrees(self) -> bool | None:
        gap = self.gap
        return None if gap is None else abs(gap) <= TOLERANCE_MIN


def iso_week(day: dt.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_end(week: str) -> dt.date:
    year, number = int(week[:4]), int(week[-2:])
    return dt.date.fromisocalendar(year, number, 7)


def _day(value: dt.date | str) -> dt.date:
    return dt.date.fromisoformat(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class Window:
    """The supervised period: from the Monday of the first sealed choice to the judgment."""

    start: dt.date
    granted_week: str


def window(choices: Iterable[dict]) -> Window | None:
    """The permit starts with the first choice the coach seals: no instance date in the
    code, and no window while nothing is sealed."""
    days = [_day(choice["scheduled_on"]) for choice in choices]
    if not days:
        return None
    first = min(days)
    start = first - dt.timedelta(days=first.weekday())
    return Window(start, iso_week(start + dt.timedelta(weeks=PERMIT_WEEKS - 1)))


def evaluate(choices: Iterable[dict], runs: Iterable[Run], traces: dict[dt.date, float],
             caps: dict[str, int | None], groups: dict[int, bool],
             today: dt.date, *, hr_cap: int) -> list[EfLine]:
    """One line per sealed easy run: the coach's choice against what the athlete ran.

    `hr_cap`: the top of the athlete's Z2 (`Instance.hr_bounds[1]`). A run whose
    average heart rate exceeds it is "too fast" (`trop rapide`).
    """
    by_day: dict[dt.date, list[Run]] = {}
    for run in runs:
        if run.type == session_kinds.EASY:
            by_day.setdefault(run.date, []).append(run)
    lines = []
    for choice in sorted(choices, key=lambda row: row["scheduled_on"]):
        day = _day(choice["scheduled_on"])
        line = EfLine(choice["session_key"], day, int(choice["coach_minutes"]),
                      caps.get(choice["session_key"]))
        done = by_day.get(day, [])
        if done:
            run = done[0]
            line.status = "run"
            line.actual_minutes = run.minutes
            line.hr_avg = run.hr_avg
            line.next_trace = traces.get(day + dt.timedelta(days=1))
            if run.strava_id is not None:
                line.group = groups.get(run.strava_id)
            if line.next_trace is not None and line.next_trace >= NET_TRACE:
                line.triggers.append(text.TRACE_NEXT_DAY)
            if (line.cap_minutes is not None and line.actual_minutes is not None
                    and line.actual_minutes > line.cap_minutes):
                line.triggers.append(text.OVER_ENVELOPE.format(cap=line.cap_minutes))
            if line.hr_avg is not None and line.hr_avg > hr_cap:
                line.triggers.append(text.TOO_FAST.format(hr_avg=line.hr_avg, hr_cap=hr_cap))
        elif day < today:
            line.status = "skipped"
        lines.append(line)
    return lines


@dataclass
class State:
    phase: str
    withdrawal_until: dt.date | None
    restricted_weeks: dict[str, list[str]]
    grant: dict | None
    window: Window


def state(lines: list[EfLine], daily: Iterable[dict], today: dt.date,
          period: Window) -> State:
    """Supervised, permit or withdrawal, derived from the lines and the declarations."""
    withdrawal_until = None
    for line in lines:
        if line.triggers:
            until = line.date + dt.timedelta(days=WITHDRAWAL_DAYS)
            if withdrawal_until is None or until > withdrawal_until:
                withdrawal_until = until

    restricted: dict[str, list[str]] = {}
    for row in daily:
        day = row.get("checked_on") or row.get("date")
        if isinstance(day, str):
            day = dt.date.fromisoformat(day)
        if day is None or day < period.start:
            continue
        reasons = []
        if row.get("life_load") == LifeLoad.HIGH:
            reasons.append(text.HIGH_LIFE_LOAD.format(day=day))
        if row.get("sleep") == Sleep.POOR:
            reasons.append(text.POOR_NIGHT.format(day=day))
        if reasons:
            restricted.setdefault(iso_week(day), []).extend(reasons)
    for line in lines:
        if line.group:
            restricted.setdefault(iso_week(line.date), []).append(
                text.GROUP_RUN.format(day=line.date))

    grant = None
    judged_on = week_end(period.granted_week)
    if today > judged_on:
        judged = [line for line in lines if line.date <= judged_on]
        run = [line for line in judged if line.status == "run"]
        agreements = sum(1 for line in run if line.agrees)
        failures = [text.FAILURE.format(session=line.session_key, triggers=", ".join(line.triggers))
                    for line in judged if line.triggers]
        grant = {"run": len(run), "agreements": agreements, "failures": failures,
                 "granted": len(run) >= MIN_EF and not failures}

    if withdrawal_until is not None and today <= withdrawal_until:
        phase = "withdrawn"
    elif grant is None:
        phase = "supervised"
    elif grant["granted"]:
        phase = "granted"
    else:
        phase = "refused"
    return State(phase, withdrawal_until, restricted, grant, period)


def _fmt_minutes(value: float | None) -> str:
    return "—" if value is None else text.MINUTES.format(minutes=round(value))


def render(lines: list[EfLine], current: State, week: str | None = None) -> list[str]:
    """The block the coach reads, at the review or on request."""
    shown = [line for line in lines if week is None or iso_week(line.date) == week]
    out = [text.TITLE.format(phase=text.PHASES[current.phase])]
    if current.withdrawal_until is not None:
        out.append(text.WITHDRAWAL.format(until=current.withdrawal_until))
    for line in shown:
        gap = line.gap
        agreement = "" if gap is None else text.GAP.format(
            gap=gap, agreement=text.AGREEMENT if line.agrees else text.DISAGREEMENT)
        trace = (text.TRACE_UNKNOWN if line.next_trace is None
                 else text.TRACE.format(trace=text.TRACES.get(line.next_trace, line.next_trace)))
        group = text.IN_GROUP if line.group else ""
        detail = (text.DETAIL_RUN.format(coach=line.coach_minutes,
                                         actual=_fmt_minutes(line.actual_minutes),
                                         agreement=agreement, trace=trace, group=group)
                  if line.status == "run" else text.DETAIL.format(coach=line.coach_minutes))
        flags = f" ⇒ {'; '.join(line.triggers)}" if line.triggers else ""
        out.append(text.LINE.format(weekday=dates.weekday(line.date, "abbreviated"),
                                    date=line.date, session=line.session_key,
                                    status=text.STATUSES[line.status], detail=detail,
                                    flags=flags))
    for iso, reasons in sorted(current.restricted_weeks.items()):
        if week is None or iso == week:
            out.append(text.RESTRICTION.format(week=iso, reasons="; ".join(reasons)))
    if current.grant is not None:
        verdict = current.grant
        out.append(text.GRANT.format(
            week=current.window.granted_week, run=verdict["run"], needed=MIN_EF,
            agreements=verdict["agreements"], tolerance=TOLERANCE_MIN,
            failures=len(verdict["failures"]),
            verdict=text.GRANTED if verdict["granted"] else text.NOT_GRANTED))
    else:
        run = sum(1 for line in lines if line.status == "run")
        out.append(text.COUNTER.format(run=run, needed=MIN_EF,
                                       week=current.window.granted_week))
    out.append(text.NOT_DETECTED + (text.BIG_FORM_UNSET if BIG_FORM_THRESHOLD is None
                                    else text.BIG_FORM.format(threshold=BIG_FORM_THRESHOLD)))
    return out
