"""Easy-run permit: the permit state is COMPUTED.

Nothing is stored except the coach's sealed choice (`permit_choices`): what the athlete
ran comes from the ledger, so does the next-day trace; life-load declarations come from
the daily state, the group flag from the Strava capture. The status (supervised, permit,
withdrawal, restriction) derives from them, like any state derived from the vault.

What is NOT detected here, and the report says so: a new route (it would need track
comparison), and a big form signal while its threshold is unset.
Method: `coaching/protocols.md`, permit section. Thresholds: the `permit_*` parameters of
the rulebook; an instance that declares none judges no permit.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from redline.coach import dates
from redline.coach.fr import permit as text
from redline.coach.fr.debrief import number as said_number
from redline.coach.fr.debrief import pace as said_pace
from redline.model.vocabulary import LifeLoad, Sleep

from . import iso_week, rulebook, session_kinds
from .errors import ConfigError

#: Big form: pace progression at equal heart rate. Without history there is no honest
#: threshold; it is set when the permit is judged. Until then the signal is displayed
#: and triggers nothing.
BIG_FORM_THRESHOLD: float | None = None


@dataclass(frozen=True)
class Thresholds:
    """The permit's thresholds, as the rulebook in force declares them."""

    #: The supervised period lasts this many ISO weeks, the week of the first sealed
    #: choice included; the permit is judged at the end of the last one.
    weeks: int
    #: Easy runs run in the period to obtain it.
    min_runs: int
    #: A run within this many minutes of the coach's sealed choice agrees with it.
    agreement_min: int
    #: Supervision after a morning trace above the base, the day after a run.
    withdrawal_days: int
    #: Graded verdicts (`permit_tolerance_*`, `permit_minor_*`). The defaults grade
    #: nothing: any run past its envelope or over the HR cap is a failure.
    tolerance_min: int = 0
    tolerance_pct: float = 0
    minor_pct: float = 0
    minor_hr_bpm: int = 0
    #: Supervision after a failure; without the graded thresholds, `withdrawal_days`.
    failure_withdrawal_days: int | None = None
    #: Declared (`permit_raw_guard_pct`), the envelope counts in load, equivalent minutes
    #: at the reference easy pace, and a raw duration more than this share (%) above the
    #: envelope still fails. Absent, the envelope counts raw minutes.
    raw_guard_pct: float | None = None

    @property
    def after_failure(self) -> int:
        return self.withdrawal_days if self.failure_withdrawal_days is None \
            else self.failure_withdrawal_days


def declared() -> Thresholds | None:
    """The thresholds of the rulebook in force, or None when it declares no permit."""
    book = rulebook.current().parameters
    # The rulebook accepts the four together or none: one absent means all are.
    if (book.permit_weeks is None or book.permit_min_runs is None
            or book.permit_agreement_min is None or book.permit_withdrawal_days is None):
        return None
    base = Thresholds(weeks=book.permit_weeks, min_runs=book.permit_min_runs,
                      agreement_min=book.permit_agreement_min,
                      withdrawal_days=book.permit_withdrawal_days)
    # The graded five also go together: one absent means none is declared.
    if (book.permit_tolerance_min is None or book.permit_tolerance_pct is None
            or book.permit_minor_pct is None or book.permit_minor_hr_bpm is None
            or book.permit_failure_withdrawal_days is None):
        return base
    return replace(base, tolerance_min=book.permit_tolerance_min,
                   tolerance_pct=book.permit_tolerance_pct, minor_pct=book.permit_minor_pct,
                   minor_hr_bpm=book.permit_minor_hr_bpm,
                   failure_withdrawal_days=book.permit_failure_withdrawal_days,
                   raw_guard_pct=book.permit_raw_guard_pct)


def thresholds() -> Thresholds:
    """The thresholds in force; a permit read under a rulebook without them is a fault."""
    found = declared()
    if found is None:
        raise ConfigError("the rulebook declares no permit thresholds (permit_* parameters)")
    return found


@dataclass(frozen=True)
class Load:
    """A run counted in equivalent minutes at the reference easy pace of its day."""

    minutes: float
    reference_pace_s: int


@dataclass(frozen=True)
class Run:
    """A ledger run, reduced to what the permit reads. `load` is None when its streams or
    the reference pace of its day are missing."""

    date: dt.date
    type: str
    minutes: float | None
    hr_avg: int | None
    strava_id: int | None = None
    load: Load | None = None


@dataclass(frozen=True)
class Envelope:
    """A planned easy run with its duration cap: judged even without a sealed choice."""

    session_key: str
    date: dt.date
    cap_minutes: int


@dataclass
class EfLine:
    """One easy run of the permit. `triggers` are its failures, `minors` its minor gaps;
    `safety` marks a failure read on the body (the next morning's trace), not on a choice.
    `coach_minutes` is None for an enveloped run the coach sealed no choice for. `load` is
    set when the rulebook counts the envelope in load and the run's load is known.
    `efforts`: the plan prescribed efforts (`prescription.has_efforts`), whose pace and HR
    are the prescription's: the run is judged on its raw duration, its mean HR is not."""

    session_key: str
    date: dt.date
    coach_minutes: int | None
    cap_minutes: int | None
    actual_minutes: float | None = None
    hr_avg: int | None = None
    next_trace: float | None = None
    group: bool | None = None
    status: str = "upcoming"
    triggers: list[str] = field(default_factory=list)
    minors: list[str] = field(default_factory=list)
    safety: bool = False
    load: Load | None = None
    efforts: bool = False

    @property
    def measured_minutes(self) -> float | None:
        """The minutes held against the envelope and the coach's choice."""
        return self.actual_minutes if self.load is None else self.load.minutes

    @property
    def gap(self) -> float | None:
        measured = self.measured_minutes
        if measured is None or self.coach_minutes is None:
            return None
        return measured - self.coach_minutes

    @property
    def agrees(self) -> bool | None:
        gap = self.gap
        return None if gap is None else abs(gap) <= thresholds().agreement_min


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
    return Window(start, iso_week.of(start + dt.timedelta(weeks=thresholds().weeks - 1)))


def evaluate(choices: Iterable[dict], runs: Iterable[Run], traces: dict[dt.date, float],
             caps: dict[str, int | None], groups: dict[int, bool],
             today: dt.date, *, hr_cap: int, envelopes: Iterable[Envelope] = (),
             efforts: Iterable[str] = ()) -> list[EfLine]:
    """One line per sealed easy run, and per enveloped one the coach sealed no choice for:
    whatever the phase, a run with an envelope is judged against it.

    `hr_cap`: the top of the athlete's Z2 (`Instance.hr_bounds[1]`). Past the tolerance,
    a run is a minor gap up to `minor_pct` above its envelope and a failure beyond; a mean
    HR is a minor gap up to `minor_hr_bpm` above the cap. A second minor gap within the
    length of the supervised period is a failure. `efforts`: the keys of the sessions the
    plan prescribed efforts on.
    """
    limits = thresholds()
    prescribed = set(efforts)
    by_day: dict[dt.date, list[Run]] = {}
    for run in runs:
        if run.type == session_kinds.EASY:
            by_day.setdefault(run.date, []).append(run)
    trace = rulebook.current().scales.morning_trace
    sealed = {choice["session_key"]: choice for choice in choices}
    lines = [EfLine(key, _day(choice["scheduled_on"]), int(choice["coach_minutes"]),
                    caps.get(key)) for key, choice in sealed.items()]
    lines += [EfLine(envelope.session_key, envelope.date, None, envelope.cap_minutes)
              for envelope in envelopes if envelope.session_key not in sealed]
    lines.sort(key=lambda line: (line.date, line.session_key))
    for line in lines:
        line.efforts = line.session_key in prescribed
        done = by_day.get(line.date, [])
        if done:
            run = done[0]
            line.status = "run"
            line.actual_minutes = run.minutes
            line.hr_avg = run.hr_avg
            if limits.raw_guard_pct is not None and not line.efforts:
                line.load = run.load
            line.next_trace = traces.get(line.date + dt.timedelta(days=1))
            if run.strava_id is not None:
                line.group = groups.get(run.strava_id)
            if line.next_trace is not None and trace.above_base(line.next_trace):
                line.triggers.append(text.TRACE_NEXT_DAY)
                line.safety = True
            _judge(line, limits, hr_cap)
        elif line.date < today:
            line.status = "skipped"
    _second_minors(lines, limits)
    return lines


def _judge(line: EfLine, limits: Thresholds, hr_cap: int) -> None:
    """The envelope and the HR cap, each a minor gap or a failure past its tolerance;
    past the raw-duration guard, a run fails whatever its load."""
    cap, measured = line.cap_minutes, line.measured_minutes
    if cap is not None and measured is not None:
        tolerance = max(limits.tolerance_min, cap * limits.tolerance_pct / 100)
        guard = limits.raw_guard_pct
        actual = line.actual_minutes
        if guard is not None and actual is not None and actual > cap * (1 + guard / 100):
            line.triggers.append(text.OVER_RAW_GUARD.format(cap=cap, guard=said_number(guard)))
        elif measured > cap + tolerance:
            if measured <= cap * (1 + limits.minor_pct / 100):
                line.minors.append(text.MINOR_ENVELOPE.format(cap=cap))
            else:
                line.triggers.append(text.OVER_ENVELOPE.format(cap=cap))
    if line.hr_avg is not None and line.hr_avg > hr_cap and not line.efforts:
        if line.hr_avg <= hr_cap + limits.minor_hr_bpm:
            line.minors.append(text.MINOR_HR.format(hr_avg=line.hr_avg, hr_cap=hr_cap))
        else:
            line.triggers.append(text.HR_ABOVE_CAP.format(hr_avg=line.hr_avg, hr_cap=hr_cap))


def _second_minors(lines: list[EfLine], limits: Thresholds) -> None:
    """A minor gap within the supervised period's length of the previous one fails."""
    previous: dt.date | None = None
    for line in lines:
        if not line.minors or line.triggers:
            continue
        if previous is not None and (line.date - previous).days < limits.weeks * 7:
            line.triggers.append(text.SECOND_MINOR)
        previous = line.date


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
    limits = thresholds()
    withdrawal_until = None
    for line in lines:
        if line.triggers:
            days = limits.withdrawal_days if line.safety else limits.after_failure
            until = line.date + dt.timedelta(days=days)
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
            restricted.setdefault(iso_week.of(day), []).extend(reasons)
    for line in lines:
        if line.group:
            restricted.setdefault(iso_week.of(line.date), []).append(
                text.GROUP_RUN.format(day=line.date))

    grant = None
    judged_on = iso_week.sunday(period.granted_week)
    if today > judged_on:
        judged = [line for line in lines if line.date <= judged_on]
        run = [line for line in judged if line.status == "run"]
        agreements = sum(1 for line in run if line.agrees)
        failures = [text.FAILURE.format(session=line.session_key, triggers=", ".join(line.triggers))
                    for line in judged if line.triggers]
        grant = {"run": len(run), "agreements": agreements, "failures": failures,
                 "granted": len(run) >= limits.min_runs and not failures}

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


def _fmt_actual(line: EfLine, limits: Thresholds) -> str:
    """The raw minutes, with the load they count for when the envelope is counted in load."""
    actual = _fmt_minutes(line.actual_minutes)
    if line.efforts:
        return actual + text.EFFORTS
    if limits.raw_guard_pct is None:
        return actual
    if line.load is None:
        return actual + text.LOAD_UNKNOWN
    return actual + text.LOAD.format(minutes=round(line.load.minutes),
                                     pace=said_pace(line.load.reference_pace_s))


def render(lines: list[EfLine], current: State, week: str | None = None) -> list[str]:
    """The block the coach reads, at the review or on request."""
    shown = [line for line in lines if week is None or iso_week.of(line.date) == week]
    scale = rulebook.current().scales.morning_trace
    limits = thresholds()
    out = [text.TITLE.format(phase=text.PHASES[current.phase])]
    if current.withdrawal_until is not None:
        out.append(text.WITHDRAWAL.format(until=current.withdrawal_until))
    for line in shown:
        gap = line.gap
        agreement = "" if gap is None else text.GAP.format(
            gap=gap, agreement=text.AGREEMENT if line.agrees else text.DISAGREEMENT)
        trace = (text.TRACE_UNKNOWN if line.next_trace is None
                 else text.TRACE.format(trace=scale.said(line.next_trace)))
        group = text.IN_GROUP if line.group else ""
        chosen = (text.CHOSEN.format(coach=line.coach_minutes) if line.coach_minutes is not None
                  else text.ENVELOPE_ONLY.format(cap=_fmt_minutes(line.cap_minutes)))
        detail = (text.DETAIL_RUN.format(chosen=chosen,
                                         actual=_fmt_actual(line, limits),
                                         agreement=agreement, trace=trace, group=group)
                  if line.status == "run" else chosen)
        flags = text.MINORS.format(minors="; ".join(line.minors)) if line.minors else ""
        flags += f" ⇒ {'; '.join(line.triggers)}" if line.triggers else ""
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
            week=current.window.granted_week, run=verdict["run"], needed=limits.min_runs,
            agreements=verdict["agreements"], tolerance=limits.agreement_min,
            failures=len(verdict["failures"]),
            verdict=text.GRANTED if verdict["granted"] else text.NOT_GRANTED))
    else:
        run = sum(1 for line in lines if line.status == "run")
        out.append(text.COUNTER.format(run=run, needed=limits.min_runs,
                                       week=current.window.granted_week))
    out.append(text.NOT_DETECTED + (text.BIG_FORM_UNSET if BIG_FORM_THRESHOLD is None
                                    else text.BIG_FORM.format(threshold=BIG_FORM_THRESHOLD)))
    return out
