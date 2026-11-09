"""The ledger, computed: the load gauge that coaching/protocols.md defines.

The gauge is presented as an ARBITER ("the debate becomes a computation, not a
negotiation"), so it PRINTS ITS INPUTS. A number nobody can reconstruct is a number
that drifts.

Reads observations, metrics, daily declarations and instruments directly from
PostgreSQL. No Markdown ledger is kept in parallel.

Usage:
    redline ledger                    # the last week
    redline ledger --week 2030-W30    # the load line for a weekly review
    redline ledger --pain-trend 3     # last 3 weeks
    redline ledger --csv              # both tables, normalized, on stdout
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Protocol

from redline.coach.fr import ledger as text
from redline.model import (
    daily,
    iso_week,
    program,
    rulebook,
    session_kinds,
)
from redline.model import ledger as table
from redline.model import race as race_model
from redline.model.vocabulary import HrDoubt, SessionStatus, WeekLifeLoad, WeekSleep

POINTS_PER_EQ_HOUR = 14.0          # calibrated, see coaching/protocols.md
LEVEL_CAP, RATIO_CAP = 85, 1.3

QUALITY_TYPES = session_kinds.QUALITY
HALF_TYPES = session_kinds.HALF_LOAD


@dataclass
class Row:
    date: dt.date
    type: str
    idempotency_key: str = ""
    #: The strength routine, when the session is a `strength` one and it is known.
    routine: str | None = None
    km: float | None = None
    hours: float | None = None
    hr_avg: int | None = None
    cadence: int | None = None
    dplus: int = 0
    dminus: int = 0
    rpe: float | None = None
    #: The known pain of each monitored zone: {zone: /10}. Never merged: a series
    #: is read zone by zone (`pain_at`).
    pains: dict[str, float] = field(default_factory=dict)
    enjoyment: int | None = None
    note: str = ""
    wbgt: float | None = None
    #: Average HR declared doubtful (`vocabulary.HrDoubt`). None = valid HR.
    hr_doubt: str | None = None

    def pain_at(self, zone: str) -> float | None:
        return self.pains.get(zone)

    @property
    def hr_valid(self) -> int | None:
        """The average HR usable to judge a cap: None without the armband."""
        return None if self.hr_doubt == HrDoubt.NO_SENSOR else self.hr_avg

    @property
    def week(self) -> str:
        return iso_week.of(self.date)

    @property
    def hard(self) -> bool:
        return (self.type in QUALITY_TYPES
                or (self.hours or 0) > rulebook.current().parameters.hard_min_hours)


@dataclass
class Instruments:
    """The two gauge inputs the ledger cannot know, declared day by day.

    None means NOT DECLARED, never "ok": a silent week must not print « sommeil ok, charge
    normale ». An undeclared week adds no multiplier, and says so.
    """

    sleep: str | None = None      # vocabulary.WeekSleep
    life_load: str | None = None  # vocabulary.WeekLifeLoad


class LedgerStore(Protocol):
    """Minimal interface of the computation, provided by PostgreSQL."""

    def observation_rows(self) -> list[dict]: ...

    def daily_rows(self) -> list[dict]: ...

    def weekly_instrument_rows(self) -> list[dict]: ...

    def strength_history(self) -> list[dict]: ...

    def exposure_rows(self, *, confirmed_only: bool = False) -> list[dict]: ...

    def deadline_rows(self) -> list[dict]: ...

    def pain_zones(self) -> list[dict]: ...


@dataclass(frozen=True)
class PainZone:
    slug: str
    label: str


def pain_zones(store: LedgerStore) -> list[PainZone]:
    """The active monitored zones, in declaration order."""
    return [PainZone(zone["slug"], zone["label"]) for zone in store.pain_zones()
            if zone["active"]]


def zone_prefix(zone: PainZone, zones: list[PainZone]) -> str:
    """A zone label (« Genou · ») before a pain line, only when there are several zones."""
    return f"{zone.label} · " if len(zones) > 1 else ""


def _known(observation: dict, metric: str) -> float | None:
    """The value of a measurement, or None if it is not KNOWN.

    The four states reduce to two for a computation: `known` carries a number, everything
    else is absent. `unavailable` (asked for, then lost) counts as absent, not as zero.
    """
    value = table.known(observation["measurements"].get(metric))
    return None if value is None else float(value)


def _known_int(observation: dict, metric: str) -> int | None:
    value = _known(observation, metric)
    return int(value) if value is not None else None


def parse_ledger(store: LedgerStore) -> list[Row]:
    """PostgreSQL observations to computation objects, most recent first.

    Read directly from the typed measurements. The store is provided by the caller: a
    process opens one connection, not one per read function.
    """
    rows: list[Row] = []
    observations = store.observation_rows()
    for observation in observations:
        duration_s = _known(observation, "duration_s")
        pace_s = _known(observation, "pace_s_per_km")
        km = _known(observation, "distance_km")
        if duration_s is not None:
            hours: float | None = duration_s / 3600
        elif pace_s is not None and km is not None:
            # Some migrated rows carry a pace without a duration. Dropping them would
            # empty the chronic load, hence DEFLATE the acute:chronic ratio and hide the
            # alarm it exists to raise.
            hours = pace_s / 3600 * km
        else:
            hours = None
        rows.append(Row(
            date=observation["occurred_on"], type=observation["kind"],
            idempotency_key=observation.get("idempotency_key", ""),
            routine=observation.get("routine"),
            km=km,
            hours=hours,
            hr_avg=_known_int(observation, "hr_avg_bpm"),
            cadence=_known_int(observation, "cadence_spm"),
            dplus=_known_int(observation, "elevation_gain_m") or 0,
            dminus=_known_int(observation, "elevation_loss_m") or 0,
            rpe=_known(observation, "rpe"),
            pains={zone: float(reading["value"])
                   for zone, reading in (observation.get("pains") or {}).items()
                   if table.known(reading) is not None},
            enjoyment=_known_int(observation, "enjoyment"),
            note=observation["note"] or "",
            wbgt=_known(observation, "wbgt_c"),
            hr_doubt=observation.get("hr_doubt"),
        ))
    # Strength-page reports have their own relational owner. Copying them into
    # observations would violate uniqueness, but ignoring them drops their duration and
    # pain from the gauge. So they are merged at COMPUTATION time only.
    # Older sessions sometimes have both forms: the general observation wins on the
    # same date/type, notably because it may carry a global RPE.
    strength = session_kinds.STRENGTH
    occupied = {(row.date, row.routine) for row in rows if row.type == strength}
    for session in store.strength_history():
        routine = session.get("workout")
        if not routine:
            continue
        key = (session["performed_on"], routine)
        if key in occupied or (session["performed_on"], None) in occupied:
            continue
        duration = session.get("duration_minutes")
        rows.append(Row(
            date=session["performed_on"],
            type=strength,
            routine=routine,
            hours=duration / 60 if duration is not None else None,
            pains={zone: float(moments["after"])
                   for zone, moments in (session.get("pains") or {}).items()
                   if moments.get("after") is not None},
            note=session.get("note") or "",
        ))
        occupied.add(key)
    return sorted(rows, key=lambda row: row.date, reverse=True)


def week_completeness(store: LedgerStore, label: str,
                      today: dt.date | None = None) -> list[str]:
    """The required and missing cells, for the displayed week only.

    Read directly from the typed states: "not measured", "lost" and zero are distinct
    without a textual sentinel.
    """
    observations = [row for row in store.observation_rows()
                    if iso_week.of(row["occurred_on"]) == label]
    return table.observation_completeness_errors(
        observations, today or dt.date.today())


def load_instruments(store: LedgerStore) -> dict[str, Instruments]:
    """The two gauge inputs the ledger cannot know.

    Two tables and a deliberate precedence: `daily_checkins` first, `weekly_instruments`
    as fallback. The daily wins because it is declared the same day; the weekly one is
    filled in on Sunday, from memory, over seven nights.

    Historical weeks without daily data keep their weekly value; recomputing them would
    fabricate data.
    """
    out = {row["iso_week"]: Instruments(
        sleep=row["sleep"] or None, life_load=row["life_load"] or None)
           for row in store.weekly_instrument_rows()}
    grouped: dict[str, list[dict]] = {}
    for row in store.daily_rows():
        label = iso_week.of(row["checked_on"])
        grouped.setdefault(label, []).append({
            "date": row["checked_on"].isoformat(), "sleep": row["sleep"],
            "life_load": row["life_load"], "desire": row["desire"]})
    for label, days in grouped.items():
        days = [{key: value for key, value in row.items() if value} for row in days]
        current = out.get(label, Instruments())
        out[label] = Instruments(
            sleep=daily.sleep_of(days) or current.sleep,
            life_load=daily.life_load_of(days) or current.life_load)
    return out


def row_weight(row: Row, targets: dict) -> tuple[float, bool]:
    """One row's raw contribution to the gauge, before the week multipliers, and
    whether it was a MISSED quality session (RPE above target, which costs +0.5).

    Exposed rather than inlined because `analysis/hot_cache.py` needs the same number to
    work out the walking share; a second copy of the formula would drift (a walk share
    from different arithmetic over-states walking and can raise a "red made of hiking"
    the gauge denies).

    `targets` is REQUIRED, with no default: an empty default would silently yield a gauge
    stripped of its penalty. The computation states what it compares against.
    """
    if row.type in table.REST_TYPES or not row.hours:
        return 0.0, False
    weighted = row.hours * (0.5 if row.type in HALF_TYPES else 1.0)
    # The gauge counts the descent, not the climb: the climb is already paid in time.
    weighted += (row.dminus / 600) * session_kinds.descent_weight(row.type)
    missed = False
    if row.type in QUALITY_TYPES:
        weighted += 0.5
        target = program.target_for(targets, row.type, row.routine)
        missed = bool(row.rpe and target and row.rpe > target)
        if missed:
            weighted += 0.5
    return weighted, missed


@dataclass
class TargetFlag:
    """A session run above its RPE target, whatever its type."""

    date: dt.date
    kind: str
    rpe: float
    target: float

    def __str__(self) -> str:
        return (text.OVER_TARGET.format(date=self.date, kind=self.kind, rpe=self.rpe, target=self.target))


def over_target(rows: list[Row], targets: dict) -> list[TargetFlag]:
    """All sessions above their target, not only the quality ones.

    The gauge penalty stays reserved to QUALITY_TYPES: it was calibrated that way and
    widening it would rescale an instrument. This is a SIGNAL, not a term of the formula.
    """
    flags = []
    for row in sorted(rows, key=lambda r: r.date):
        target = program.target_for(targets, row.type, row.routine)
        if row.rpe is not None and target is not None and row.rpe > target:
            flags.append(TargetFlag(row.date, row.type, row.rpe, target))
    return flags


def equivalent_hours(rows: list[Row], instr: Instruments,
                     targets: dict) -> tuple[float, int, list[str]]:
    """coaching/protocols.md § Formule v1 -> (h_eq, level out of 100, trace).

    The trace is not a debug aid: it is what makes the gauge an arbiter rather than an
    opinion, so every term that moved the number names itself.
    """
    base, trace = 0.0, []
    for row in sorted(rows, key=lambda r: r.date):
        weighted, missed = row_weight(row, targets)
        if not weighted:
            continue
        if missed:
            trace.append(text.TRACE_MISSED.format(date=row.date, type=row.type))
        base += weighted
        trace.append(text.TRACE_SESSION.format(date=row.date, type=row.type, weighted=weighted))

    multiplier = 1.0
    if _two_hard_days_in_a_row(rows):
        multiplier *= 1.15
        trace.append(text.TRACE_TWO_HARD_DAYS)
    if instr.sleep == WeekSleep.BAD_WEEK:
        multiplier *= 1.20
        trace.append(text.TRACE_BAD_SLEEP_WEEK)
    elif instr.sleep == WeekSleep.BAD_NIGHTS:
        multiplier *= 1.10
        trace.append(text.TRACE_BAD_NIGHTS)
    if instr.life_load == WeekLifeLoad.HIGH:
        multiplier *= 1.10
        trace.append(text.TRACE_HIGH_LIFE_LOAD)

    h_eq = base * multiplier
    return h_eq, round(h_eq * POINTS_PER_EQ_HOUR), trace


def _two_hard_days_in_a_row(rows: list[Row]) -> bool:
    hard_days = sorted({row.date for row in rows if row.hard})
    return any((b - a).days == 1 for a, b in pairwise(hard_days))


def normalize_week(text: str, known: list[str]) -> str:
    """« 31 », « W31 », « 2030-w31 » -> « 2030-W31 », rejecting unknown weeks.

    Without this, `--week 31` would raise nothing: the label would match no row, the
    week would come out empty and the gauge would print « 0.00 h-éq (0/100) », a perfectly
    credible zero. A reader that meets non-conforming data REFUSES, it does not interpret.
    """
    raw = text.strip().upper().replace(" ", "")
    match = re.fullmatch(r"(?:(\d{4})-?)?W?(\d{1,2})", raw)
    if not match:
        raise ValueError(f"unreadable week: {text!r} (expected 2030-W30, W30 or 30)")
    year, week = match.group(1), int(match.group(2))
    if year:
        label = f"{year}-W{week:02d}"
    else:
        # No year: take the ledger weeks that carry this number. Normally only one; if
        # several, the most recent, and the label printed at the head of the output says
        # which one was kept.
        candidates = [lab for lab in known if lab.endswith(f"W{week:02d}")]
        if not candidates:
            raise ValueError(f"week W{week:02d} is not in the ledger")
        label = max(candidates)
    if label not in known:
        span = f"{min(known)} → {max(known)}" if known else "empty ledger"
        raise ValueError(f"{label} has no row in the ledger ({span})")
    return label


def acute_chronic_ratio(per_week: dict[str, float], label: str) -> float | None:
    """This week's h_eq over the mean of the four previous CALENDAR weeks.

    Not the four weeks that happen to have rows. A week with no session at all is a
    real 0: skipping it inflates the chronic load and deflates the ratio, which hides
    the alarm. Weeks before the ledger starts are not counted, because no data is not
    a zero. (This ratio is not a relapse detector: see coaching/protocols.md
    § Calibration.)
    """
    if label not in per_week:
        return None
    first = iso_week.monday(min(per_week))
    start = iso_week.monday(label)
    previous = []
    for offset in range(1, 5):
        day = start - dt.timedelta(weeks=offset)
        if day >= first:
            previous.append(per_week.get(iso_week.of(day), 0.0))
    if not previous or sum(previous) == 0:
        return None
    return round(per_week[label] / (sum(previous) / len(previous)), 2)


def eq_hours_per_week(rows: list[Row], instruments: dict[str, Instruments],
                      targets: dict) -> dict[str, float]:
    weeks: dict[str, list[Row]] = {}
    for row in rows:
        weeks.setdefault(row.week, []).append(row)
    return {label: round(equivalent_hours(rs, instruments.get(label, Instruments()),
                                         targets)[0], 2)
            for label, rs in weeks.items()}


@dataclass
class PainSeries:
    """One week of readings of the SAME nature. `n` is printed with the peak because
    a maximum over three readings and a maximum over seven are not comparable: read
    side by side without their counts, a diligent week looks like a worse week."""

    peak: float | None
    median: float | None
    n: int

    def __str__(self) -> str:
        if not self.n:
            return "—"
        return text.PAIN_SERIES.format(peak=self.peak, median=self.median, n=self.n)


@dataclass
class PainWeek:
    week: str
    effort: PainSeries      # the peak felt DURING the session
    rest: PainSeries        # resting readings (types `lecture` and `reveil`)
    wake: PainSeries        # sub-series of morning readings -- the Esculier judge

    def __str__(self) -> str:
        return (text.PAIN_WEEK.format(week=self.week, effort=self.effort, rest=self.rest)
                + (text.PAIN_WEEK_WAKE.format(wake=self.wake) if self.wake.n else ""))


def _series(values: list[float]) -> PainSeries:
    """The peak leads, and that is not a detail: a median over a week of mostly
    zero-pain hikes is 0 by construction and hides the most recent readings."""
    if not values:
        return PainSeries(peak=None, median=None, n=0)
    return PainSeries(peak=max(values), median=round(_median(values), 2), n=len(values))


def pain_trend(rows: list[Row], weeks_back: int, zone: str) -> list[PainWeek]:
    """Weekly pain of ONE zone over the last `weeks_back` weeks, in TWO series.

    Pain during effort and pain at rest answer different questions, and the rule that
    arbitrates -- Esculier, `coaching/protocols.md` -- needs both: at most 2-3/10,
    settling within an hour AND no worse the next day. Pooled into one column and reduced to
    a weekly maximum, the second half of that rule vanishes: a 1.5 felt in the climbs and
    a 0.5 read cold that evening would come out as a single « pic 1.5 ».
    """
    per_week: dict[str, list[Row]] = {}
    for row in rows:
        if row.pain_at(zone) is not None:
            per_week.setdefault(row.week, []).append(row)
    out = []
    for label in sorted(per_week)[-weeks_back:]:
        week = per_week[label]
        out.append(PainWeek(
            week=label,
            # The effort series is defined POSITIVELY. Written as "not in REST_TYPES", it
            # would silently absorb any new type: a `palpation` (pain provoked by
            # pressure) would count as an effort peak, i.e. in the series that judges
            # the injury.
            effort=_series([value for r in week if (value := r.pain_at(zone)) is not None
                            and r.type in table.EFFORT_TYPES]),
            rest=_series([value for r in week if (value := r.pain_at(zone)) is not None
                          and r.type in table.REST_TYPES]),
            wake=_series([value for r in week if (value := r.pain_at(zone)) is not None
                          and r.type == session_kinds.WAKE_TRACE])))
    return out


@dataclass
class EsculierFlag:
    date: dt.date
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.date} {self.kind} — {self.detail}"


REST_BASELINE_DAYS = 56  # eight weeks: enough to cover a full load cycle


def rest_peak(rows: list[Row], before: dt.date, zone: str) -> float | None:
    """The peak of the rest series preceding `before`, or None without history.

    This is the reference the morning reading is judged against. It is NOT the peak of
    the previous day's session: the athlete's /10 scale is written for effort
    (`coaching/protocols.md`), and the morning reading is a different thing, hard to
    compare to it. Subtracting the two would put two currencies on one ruler.
    """
    window = _rest_window(rows, before, zone)
    return max(window) if window else None


def _rest_window(rows: list[Row], before: dt.date, zone: str) -> list[float]:
    return [value for row in rows
            if row.type in table.REST_TYPES and (value := row.pain_at(zone)) is not None
            and before - dt.timedelta(days=REST_BASELINE_DAYS) <= row.date < before]


def rest_baseline(rows: list[Row], before: dt.date, zone: str) -> str:
    """How a rest reading compares to the ones that came before it.

    Exists because a rest reading taken as news (a change of category) can in fact equal
    earlier readings of the same series: a flag arriving without its own history is
    misread.

    So « pire le lendemain » never goes out alone again: it goes out with the median and
    the peak of the rest series that precede it. A number inside its own range cannot be
    reported as news.
    """
    window = _rest_window(rows, before, zone)
    if not window:
        return text.NO_REST_HISTORY
    series = _series(window)
    return text.REST_BASELINE.format(series=series)


def esculier_check(rows: list[Row], zone: str,
                   history: list[Row] | None = None) -> list[EsculierFlag]:
    """Where the Esculier rule is broken, or cannot be applied for want of a reading.

    The "no worse the next day" clause looks at what a session leaves the NEXT MORNING at
    rest, which is why `reveil` is a type of its own. A session followed by no morning reading
    is reported too: an untested rule is not a passed rule, and the injury's failure mode
    here is silence, not noise.

    What « worse » means is a deliberate departure from the published rule. The morning
    is compared to the peak of ITS OWN rest series (`rest_peak`), never to the previous
    day's effort peak: the literal subtraction assumes a single ruler, and the athlete has
    one scale, written for effort. The qualification therefore lives in the TRIGGER, not
    the MESSAGE: a reading inside its own range does not fire at all. Hence the flag is
    named after the morning trace, not after "worse".

    That missing-reading flag only looks at rows written since `reveil` exists as a type.
    The two others read a number that was always there, so they apply to the whole ledger.

    `history` is the full ledger when `rows` is a single week: without it the morning has
    no series to be judged against. Defaults to `rows`. The rule reads ONE zone: a knee
    session is never judged by an ankle morning.
    """
    past = history if history is not None else rows
    effort = rulebook.current().scales.effort_pain
    wake = {row.date: value for row in rows
            if row.type == session_kinds.WAKE_TRACE and (value := row.pain_at(zone)) is not None}
    flags = []
    for row in sorted(rows, key=lambda r: r.date):
        # Positive test, not "not in REST_TYPES": the Esculier rule is about what a SESSION
        # leaves the next day. A measurement or a palpation is not a session, and negation
        # would let them in as soon as a new type appears.
        pain = row.pain_at(zone)
        if row.type not in table.EFFORT_TYPES or pain is None:
            continue
        if effort.above_cap(pain):
            flags.append(EsculierFlag(row.date, text.ESCULIER_OVER_CAP,
                                      text.ESCULIER_OVER_CAP_DETAIL.format(type=row.type, pain=pain, esculier_cap=effort.cap)))
        after = wake.get(row.date + dt.timedelta(days=1))
        if after is None:
            if pain > 0 and row.date >= table.completeness_from():
                flags.append(EsculierFlag(row.date, text.ESCULIER_UNCHECKED,
                                          text.ESCULIER_UNCHECKED_DETAIL.format(type=row.type, pain=pain)))
        else:
            morning = row.date + dt.timedelta(days=1)
            # The reference is its own rest series, never the previous day's effort peak.
            # Without history, a non-zero wake reading is still flagged once:
            # `rest_baseline` itself says it comes out without a reference.
            reference = rest_peak(past, morning, zone)
            if after > (reference if reference is not None else 0.0):
                flags.append(EsculierFlag(row.date, text.ESCULIER_WAKE_TRACE,
                                          text.ESCULIER_WAKE_TRACE_DETAIL.format(after=after, type=row.type, pain=pain, rest_baseline=rest_baseline(past, morning, zone))))
    return flags


# The ledger types that count as "run", as opposed to walking and strength. The
# distinction matters for the reading: a red gauge made of hiking does not mean the same
# as a red gauge made of running.
RUN_TYPES = session_kinds.RUN
WALK_TYPES = session_kinds.WALK


@dataclass
class Volume:
    """A week's mileage, summed from the ledger and from it alone.

    The plan carries the INTENTION, the ledger carries the FACT, and nothing tells them
    apart once a figure leaves its table. This computation is the only one allowed to
    produce a weekly volume: `--week` prints it and `analysis/hot_cache.py` renders it.
    """

    km_run: float
    runs: int
    km_walk: float
    walks: int
    dplus: int
    dminus: int

    def __str__(self) -> str:
        out = text.VOLUME_RUN.format(km_run=self.km_run, outings=text.outings(self.runs))
        if self.km_walk:
            out += text.VOLUME_WALK.format(km_walk=self.km_walk, walks=self.walks)
        # D- is not repeated here: it has its own line just below, which states that it
        # does not count in the gauge. One fact, one place.
        return out + f" · D+ {self.dplus} m"


def week_volume(rows: list[Row]) -> Volume:
    """Sum of the week's distances, never rounded before the sum.

    Ledger cells already carry Strava's metres to the hundredth of a km. Rounding does
    not accumulate, it applies once, at display time: hence the full sum here and the
    formatting at the very end.
    """
    runs = [row for row in rows if row.type in RUN_TYPES and row.km]
    walks = [row for row in rows if row.type in WALK_TYPES and row.km]
    return Volume(km_run=sum(row.km or 0 for row in runs), runs=len(runs),
                  km_walk=sum(row.km or 0 for row in walks), walks=len(walks),
                  dplus=sum(row.dplus for row in rows),
                  dminus=week_descent(rows))


def week_descent(rows: list[Row]) -> int:
    """Total D- of the week, reported next to the gauge: descent is a load of its own
    (impact, braking) and an early canary for the injury, so it is surfaced even when
    the gauge does not weigh it."""
    return sum(row.dminus for row in rows)


TRAJECTORY_WEEKS = 6      # enough to hold an assimilation week without reading it as a dip
TRAJECTORY_HALF = 3       # compare 3 closed weeks to the 3 before


@dataclass
class WeekTrace:
    """One week of the trajectory. `elapsed` is set only while the week is running."""

    label: str
    km: float
    longest: float
    elapsed: int | None = None

    @property
    def open(self) -> bool:
        return self.elapsed is not None

    def __str__(self) -> str:
        line = text.TRAJECTORY_WEEK.format(label=self.label, km=self.km, longest=self.longest)
        if self.open:
            line += text.TRAJECTORY_OPEN_WEEK.format(elapsed=self.elapsed)
        return line


def _runs(rows: list[Row]) -> list[Row]:
    return [row for row in rows if row.type in RUN_TYPES and row.km]


def longest_recent(rows: list[Row], before: dt.date) -> float | None:
    """The longest RUN of the 30 days before `before`.

    A reference, not a cap: no fixed percentage bounds the next run (see `rulebook.py`).
    It tells by how much a session departs from the past month, so the gap is judged
    rather than discovered.
    """
    days = rulebook.current().parameters.transition_lookback_days
    window = [row for row in _runs(rows) if 0 <= (before - row.date).days < days]
    if not window:
        return None
    return max(row.km or 0 for row in window)


def trajectory(rows: list[Row], today: dt.date,
               weeks: int = TRAJECTORY_WEEKS) -> list[WeekTrace]:
    """The last N weeks, run volume and longest run.

    The gauge reads ONE week, the canary ONE session, the guardrail ONE gap to the last
    30 days. Nobody read the slope; this does.

    The current week is INCOMPLETE by construction and is read as such: on a Thursday it
    holds four days, not seven, and comparing it to closed weeks fabricates a collapse.
    """
    current = iso_week.of(today)
    # N CLOSED weeks, plus the current week if it exists. Counting the open week among
    # the N would leave N-1 closed ones, so a verdict could never be computed.
    seen = sorted({row.week for row in rows})
    labels = [label for label in seen if label != current][-weeks:]
    if seen and seen[-1] == current:
        labels.append(current)
    traces = []
    for label in labels:
        week_rows = [row for row in rows if row.week == label]
        traces.append(WeekTrace(
            label=label,
            km=sum(row.km or 0 for row in _runs(week_rows)),
            longest=max((row.km or 0 for row in _runs(week_rows)), default=0.0),
            elapsed=(today - iso_week.monday(label)).days + 1 if label == current else None))
    return traces


def trajectory_verdict(traces: list[WeekTrace]) -> list[str]:
    """Two comparisons, on CLOSED weeks only.

    Volume is compared as a MEAN (an assimilation week inside the window must not pass
    for a drop); the longest run is compared as a MAXIMUM, because that is what it is:
    a record is not averaged.
    """
    closed = [trace for trace in traces if not trace.open]
    if len(closed) < 2 * TRAJECTORY_HALF:
        return [text.SLOPE_UNAVAILABLE.format(closed=len(closed), needed=2 * TRAJECTORY_HALF)]
    recent, before = closed[-TRAJECTORY_HALF:], closed[-2 * TRAJECTORY_HALF:-TRAJECTORY_HALF]
    lines = []
    for name, now, then in (
            (text.SLOPE_VOLUME, sum(t.km for t in recent) / TRAJECTORY_HALF,
             sum(t.km for t in before) / TRAJECTORY_HALF),
            (text.SLOPE_LONGEST, max(t.longest for t in recent),
             max(t.longest for t in before))):
        change = f"{(now / then - 1) * 100:+.0f} %" if then else "—"
        lines.append(text.SLOPE_LINE.format(name=name, now=now, then=then, trajectory_half=TRAJECTORY_HALF, change=change))
    return lines


@dataclass
class Stall:
    """For how many CLOSED weeks the longest run has not beaten its record."""

    weeks: int
    record_km: float
    record_week: str

    @property
    def triggered(self) -> bool:
        return self.weeks >= rulebook.current().parameters.stall_weeks

    def __str__(self) -> str:
        if not self.weeks:
            return (text.STALL_RECORD_BEATEN.format(record_km=self.record_km, record_week=self.record_week))
        head = "🔴 " if self.triggered else ""
        line = (text.STALL.format(head=head, weeks=self.weeks, record_km=self.record_km, record_week=self.record_week))
        if self.triggered:
            line += (text.STALL_RULE_S9)
        return line


def long_run_stall(rows: list[Row], today: dt.date) -> Stall | None:
    """The tripwire of pre-mortem scenario 9, COMPUTED: a stalled longest run.

    `trajectory_verdict` does not measure it: it compares the maximum of three weeks to
    the maximum of the previous three, so a longest run flat for three weeks after a
    record reads there as a rise. Here, a record is the maximum of ALL earlier closed
    weeks; we count the latest closed weeks that do not beat it (within the stall
    tolerance: 11.53 against 11.5 is not progress). The current week is never judged.
    """
    current = iso_week.of(today)
    closed = sorted({row.week for row in _runs(rows)} - {current})
    if not closed:
        return None
    # Weeks without a run count too: not having run is not progressing.
    labels = []
    week = iso_week.monday(closed[0])
    last_closed = iso_week.monday(current) - dt.timedelta(weeks=1)
    while week <= last_closed:
        labels.append(iso_week.of(week))
        week += dt.timedelta(weeks=1)
    longest = {label: max((row.km or 0 for row in _runs(rows) if row.week == label),
                          default=0.0) for label in labels}
    tolerance = rulebook.current().parameters.stall_tolerance_km
    record, record_week, stalled = 0.0, labels[0], 0
    for label in labels:
        if longest[label] > record + tolerance:
            record, record_week, stalled = longest[label], label, 0
        else:
            stalled += 1
    return Stall(weeks=stalled, record_km=record, record_week=record_week)


@dataclass
class Projection:
    """What the long run needs, from now to the taper, for a target."""

    start_km: float
    weeks: list[str]               # the weeks that can still carry an increase
    race_on: dt.date
    taper_from: str

    def required_growth(self, target_km: float, lost: int = 0) -> float | None:
        """The mean weekly growth needed, if `lost` weeks carry none.

        None when no useful week remains; 1.0 when the target is already reached.
        """
        usable = len(self.weeks) - lost
        if target_km <= self.start_km:
            return 1.0
        if usable <= 0:
            return None
        return (target_km / self.start_km) ** (1 / usable)


def race_projection(rows: list[Row], today: dt.date, race_on: dt.date,
                    taper_weeks: int | None = None) -> Projection | None:
    """Backward planner: the pace a long-run target demands.

    Starting point: the longest run of the last 30 days. Useful weeks run from the next
    week up to the taper (which starts `taper_weeks` weeks before the race week,
    inclusive). No rule is applied: the instrument does not say what is allowed, it says
    what WOULD BE NEEDED, assimilation weeks and novelties included, so the gap to the
    target is visible before the milestone. The decision belongs to the athlete and coach.
    """
    longest = longest_recent(rows, today)
    if longest is None or race_on <= today:
        return None
    race_week = iso_week.monday(iso_week.of(race_on))
    if taper_weeks is None:
        taper_weeks = rulebook.current().parameters.taper_weeks
    taper_start = race_week - dt.timedelta(weeks=taper_weeks - 1)
    # The current week may already have had its long run: start from the next one, a
    # cautious reading on a review day.
    week = iso_week.monday(iso_week.of(today)) + dt.timedelta(weeks=1)
    weeks = []
    while week < taper_start:
        weeks.append(iso_week.of(week))
        week += dt.timedelta(weeks=1)
    return Projection(start_km=longest, weeks=weeks, race_on=race_on,
                      taper_from=iso_week.of(taper_start))


def next_race_date(store: LedgerStore, today: dt.date) -> dt.date | None:
    """The next TYPED race (`redline fact-write deadline --kind race`), never prose."""
    race = race_model.upcoming(store, today)
    return race.on if race else None


def transition_notes(rows: list[Row], sessions: list[dict], today: dt.date) -> list[str]:
    """The gap of each planned run to the longest of the previous 30 days.

    Informational, never blocking, and without a threshold: no fixed percentage judges
    progression. The gap is shown so it can be decided.
    """
    notes = []
    for session in sessions:
        if session.get("kind") != "run" or session.get("status") == SessionStatus.CANCELLED:
            continue
        day = dt.date.fromisoformat(session["date"])
        if day < today:
            continue
        planned = session.get("distance_cap_km") or session.get("distance_target_km")
        if not planned:
            continue
        longest = longest_recent(rows, day)
        if longest is None:
            notes.append(text.PLANNED_NO_RECENT_RUN.format(session=session["id"], planned=float(planned)))
            continue
        notes.append(text.PLANNED_GAP.format(session=session["id"], planned=float(planned), gap=(float(planned) / longest - 1) * 100, longest=longest))
    return notes


def projection_lines(projection: Projection, target_km: float | None = None) -> list[str]:
    lines = [text.PROJECTION.format(race_on=projection.race_on, weeks=len(projection.weeks), taper_from=projection.taper_from, start_km=projection.start_km)]
    if not target_km:
        lines.append(text.PROJECTION_NO_TARGET)
        return lines
    parts = []
    for lost in (0, 1, 2):
        growth = projection.required_growth(target_km, lost)
        label = (text.PROJECTION_EVERY_WEEK if not lost
                 else text.PROJECTION_WEEKS_LOST.format(lost=lost))
        parts.append(f"{label} : " + (text.PROJECTION_IMPOSSIBLE if growth is None
                                      else text.PROJECTION_GROWTH.format(percent=(growth - 1) * 100)))
    lines.append(text.PROJECTION_REQUIRED.format(target_km=target_km)
                 + " ; ".join(parts) + text.PROJECTION_REQUIRED_NOTE)
    return lines


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
