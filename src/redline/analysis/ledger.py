#!/usr/bin/env python3
"""The ledger, computed: the load gauge that coaching/protocols.md defines.

The gauge is presented as an ARBITER ("the debate becomes a computation, not a
negotiation"), so it PRINTS ITS INPUTS. A number nobody can reconstruct is a number
that drifts.

Usage:
    redline ledger                       # the last week
    redline ledger --week 2030-W30    # the load line for a weekly review
    redline ledger --pain-trend 3  # last 3 weeks
    redline ledger --csv                 # both tables, normalized, on stdout

Reads observations, metrics, daily declarations and instruments directly from
PostgreSQL. No Markdown ledger is kept in parallel.

The French CLI flags and the CSV column names are a contract with the athlete and with
whatever reads the export: they stay French while the code around them is English.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import os
import re
import sys
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Protocol

from redline.model import (
    daily,
    paths,
    programme,
    rulebook,
    session_kinds,
)
from redline.model import ledger as table
from redline.model.errors import InputError
from redline.model.vocabulary import HrDoubt, WeekLifeLoad, WeekSleep

POINTS_PER_EQ_HOUR = 14.0          # calibrated, see coaching/protocols.md
LEVEL_CAP, RATIO_CAP = 85, 1.3

QUALITY_TYPES = session_kinds.QUALITY
HALF_TYPES = session_kinds.HALF_LOAD

# The gauge counts the descent, not the climb: the climb is already paid in time.
# Each kind's share of it is `session_kinds.descent_weight`.


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
        year, week, _ = self.date.isocalendar()
        return f"{year}-W{week:02d}"

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


# --------------------------------------------------------------------------- parsing

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


def open_store() -> LedgerStore:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL obligatoire : aucun registre de secours n'existe")
    from redline.storage import Database, OperationalStore
    return OperationalStore(Database(url, application_name="ledger-gauge"))


def _known(observation: dict, metric: str) -> float | None:
    """The value of a measurement, or None if it is not KNOWN.

    The four states reduce to two for a computation: `known` carries a number, everything
    else is absent. `unavailable` (asked for, then lost) counts as absent, not as zero.
    """
    measurement = observation["measurements"].get(metric)
    if not measurement or measurement["state"] != "known":
        return None
    return float(measurement["value"])


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
                   if reading.get("state") == "known" and reading.get("value") is not None},
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
                    if _week_label(row["occurred_on"]) == label]
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
        label = row["checked_on"].strftime("%G-W%V")
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


# ------------------------------------------------------------------------- the gauge

def row_weight(row: Row, targets: dict) -> tuple[float, bool]:
    """One row's raw contribution to the gauge, before the week multipliers, and
    whether it was a MISSED quality session (RPE above target, which costs +0.5).

    Exposed rather than inlined because `model/hot_cache.py` needs the same number to
    work out the walking share; a second copy of the formula would drift (a walk share
    from different arithmetic over-states walking and can raise a "red made of hiking"
    the gauge denies).

    `targets` is REQUIRED, with no default: an empty default would silently yield a gauge
    stripped of its penalty. The computation states what it compares against.
    """
    if row.type in table.REST_TYPES or not row.hours:
        return 0.0, False
    weighted = row.hours * (0.5 if row.type in HALF_TYPES else 1.0)
    weighted += (row.dminus / 600) * session_kinds.descent_weight(row.type)
    missed = False
    if row.type in QUALITY_TYPES:
        weighted += 0.5
        target = programme.target_for(targets, row.type, row.routine)
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
        return (f"{self.date} {self.kind} — RPE {self.rpe:g} déclaré "
                f"pour une cible de {self.target:g}")


def over_target(rows: list[Row], targets: dict) -> list[TargetFlag]:
    """All sessions above their target, not only the quality ones.

    The gauge penalty stays reserved to QUALITY_TYPES: it was calibrated that way and
    widening it would rescale an instrument. This is a SIGNAL, not a term of the formula.
    """
    flags = []
    for row in sorted(rows, key=lambda r: r.date):
        target = programme.target_for(targets, row.type, row.routine)
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
            trace.append(f"{row.date} {row.type} séance RATÉE (+0.5)")
        base += weighted
        trace.append(f"{row.date} {row.type} → {weighted:.2f} h-éq")

    multiplier = 1.0
    if _two_hard_days_in_a_row(rows):
        multiplier *= 1.15
        trace.append("×1.15 deux jours exigeants consécutifs")
    if instr.sleep == WeekSleep.BAD_WEEK:
        multiplier *= 1.20
        trace.append("×1.20 sommeil dégradé toute la semaine")
    elif instr.sleep == WeekSleep.BAD_NIGHTS:
        multiplier *= 1.10
        trace.append("×1.10 2-3 mauvaises nuits")
    if instr.life_load == WeekLifeLoad.HIGH:
        multiplier *= 1.10
        trace.append("×1.10 grosse charge de vie")

    h_eq = base * multiplier
    return h_eq, round(h_eq * POINTS_PER_EQ_HOUR), trace


def _two_hard_days_in_a_row(rows: list[Row]) -> bool:
    hard_days = sorted({row.date for row in rows if row.hard})
    return any((b - a).days == 1 for a, b in pairwise(hard_days))


def _week_start(label: str) -> dt.date:
    year, week = label.split("-W")
    return dt.date.fromisocalendar(int(year), int(week), 1)


def _week_label(day: dt.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def normalize_week(text: str, known: list[str]) -> str:
    """« 31 », « W31 », « 2030-w31 » -> « 2030-W31 », rejecting unknown weeks.

    Without this, `--week 31` would raise nothing: the label would match no row, the
    week would come out empty and the gauge would print « 0.00 h-éq (0/100) », a perfectly
    credible zero. A reader that meets non-conforming data REFUSES, it does not interpret.
    """
    raw = text.strip().upper().replace(" ", "")
    match = re.fullmatch(r"(?:(\d{4})-?)?W?(\d{1,2})", raw)
    if not match:
        raise ValueError(f"semaine illisible : « {text} » (attendu 2030-W30, W30 ou 30)")
    year, week = match.group(1), int(match.group(2))
    if year:
        label = f"{year}-W{week:02d}"
    else:
        # No year: take the ledger weeks that carry this number. Normally only one; if
        # several, the most recent, and the label printed at the head of the output says
        # which one was kept.
        candidates = [lab for lab in known if lab.endswith(f"W{week:02d}")]
        if not candidates:
            raise ValueError(f"semaine W{week:02d} absente du registre")
        label = max(candidates)
    if label not in known:
        span = f"{min(known)} → {max(known)}" if known else "registre vide"
        raise ValueError(f"{label} n'a aucune ligne au registre ({span})")
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
    first = _week_start(min(per_week))
    start = _week_start(label)
    previous = []
    for offset in range(1, 5):
        day = start - dt.timedelta(weeks=offset)
        if day >= first:
            previous.append(per_week.get(_week_label(day), 0.0))
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


# ------------------------------------------------------------------------- the trends

ESCULIER_CAP = 2.0        # coaching/protocols.md § Comment lire la douleur


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
        return f"pic {self.peak} · médiane {self.median} (n={self.n})"


@dataclass
class PainWeek:
    week: str
    effort: PainSeries      # the peak felt DURING the session
    rest: PainSeries        # resting readings (types `lecture` and `reveil`)
    wake: PainSeries        # sub-series of morning readings -- the Esculier judge

    def __str__(self) -> str:
        return (f"{self.week} : effort {self.effort} · repos {self.rest}"
                + (f" · réveil {self.wake}" if self.wake.n else ""))


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
        return "aucun historique de repos sur 8 sem"
    series = _series(window)
    return f"repos 8 sem : {series}"


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
        if pain > ESCULIER_CAP:
            flags.append(EsculierFlag(row.date, "AU-DESSUS DU PLAFOND",
                                      f"{row.type} à {pain}/10 > {ESCULIER_CAP} "
                                      f"→ réduction immédiate"))
        after = wake.get(row.date + dt.timedelta(days=1))
        if after is None:
            if pain > 0 and row.date >= table.completeness_from():
                flags.append(EsculierFlag(row.date, "non vérifié",
                                          f"{row.type} à {pain}/10, aucune lecture "
                                          f"« reveil » le lendemain"))
        else:
            morning = row.date + dt.timedelta(days=1)
            # The reference is its own rest series, never the previous day's effort peak.
            # Without history, a non-zero wake reading is still flagged once:
            # `rest_baseline` itself says it comes out without a reference.
            reference = rest_peak(past, morning, zone)
            if after > (reference if reference is not None else 0.0):
                flags.append(EsculierFlag(row.date, "TRACE AU RÉVEIL",
                                          f"{after}/10 au réveil après {row.type} "
                                          f"à {pain}/10 — "
                                          f"{rest_baseline(past, morning, zone)}"))
    return flags


def _pain_gate_cases(rows: list[Row], label: str, previous: str,
                     zone: str) -> list[tuple[str, str]]:
    def peak(week: str) -> float | None:
        values = [value for row in rows if row.week == week and row.type in RUN_TYPES
                  and (value := row.pain_at(zone)) is not None]
        return max(values) if values else None

    now, before = peak(label), peak(previous)
    cases = []
    if now is None:
        cases.append(("?", "pic d'effort : aucune course chiffrée"))
    else:
        ceiling = rulebook.current().parameters.gate_effort_peak
        cases.append(("✓" if now <= ceiling else "✗",
                      f"pic d'effort {now:g}/10 (au plus {ceiling:g})"))
    if now is None or before is None:
        cases.append(("?", f"tendance : {previous} {before if before is not None else '—'} "
                           f"→ {label} {now if now is not None else '—'}"))
    else:
        cases.append(("✓" if now <= before else "✗",
                      f"tendance : {before:g} → {now:g}"))
    wakes = [row for row in rows if row.week == label and row.type == session_kinds.WAKE_TRACE]
    measured = [(row.date, value) for row in wakes
                if (value := row.pain_at(zone)) is not None]
    base = rest_peak(rows, _week_start(label), zone)
    above = [(day, value) for day, value in measured
             if value > (base if base is not None else 0.0)]
    detail = (f"réveils : {len(measured)} chiffré(s) sur {len(wakes)}"
              + (f", référence 8 sem {base:g}" if base is not None else ""))
    if above:
        cases.append(("✗", detail + " · au-dessus : "
                      + ", ".join(f"{day:%d/%m} {value:g}" for day, value in above)))
    elif not measured or len(measured) < len(wakes):
        cases.append(("?", detail))
    else:
        cases.append(("✓", detail))
    return cases


def gate(rows: list[Row], label: str, instr: Instruments,
         zones: list[PainZone]) -> list[str]:
    """The gate (rule `po` of the rulebook), computed box by box from the ledger.

    Judging it by eye in the review lets prose claim readings the numeric series does not
    hold. The computation does not replace the behavioural reading the rule requires: it
    states what the ledger establishes, and names what it cannot establish (« ? »). The
    pain boxes are judged zone by zone: the gate opens only if each one passes. Without a
    monitored zone, they do not exist.
    """
    from redline.analysis import signals  # signals imports this module: deferred import

    previous = _week_label(_week_start(label) - dt.timedelta(days=7))
    cases: list[tuple[str, str]] = []
    for zone in zones:
        cases.extend((mark, zone_prefix(zone, zones) + text)
                     for mark, text in _pain_gate_cases(rows, label, previous, zone.slug))
    drifts = [drift for drift in signals.hr_at_pace(rows)
              if _week_label(drift.date) in (previous, label)
              and drift.delta >= signals.HR_DRIFT_BPM]
    if drifts:
        cases.append(("✗", "FC des footings haute : " + ", ".join(
            f"{drift.date:%d/%m} {drift.delta:+.0f}" for drift in drifts)))
    else:
        cases.append(("✓", "FC des footings normale"))
    if instr.sleep is None:
        cases.append(("?", "sommeil non déclaré"))
    else:
        cases.append(("✗" if instr.sleep != WeekSleep.OK else "✓", f"sommeil {instr.sleep}"))
    marks = {mark for mark, _ in cases}
    verdict = "rouge" if "✗" in marks else "incomplète" if "?" in marks else "verte"
    return [f"Porte (règle po, calculée) : {verdict}"] + [f"  {mark} {text}"
                                                           for mark, text in cases]


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
    produce a weekly volume: `--week` prints it and `model/hot_cache.py` renders it.
    """

    km_run: float
    runs: int
    km_walk: float
    walks: int
    dplus: int
    dminus: int

    def __str__(self) -> str:
        out = f"{self.km_run:.2f} km courus ({self.runs} sortie{'s' if self.runs > 1 else ''})"
        if self.km_walk:
            out += f" + {self.km_walk:.2f} km marchés ({self.walks})"
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


# ------------------------------------------------------------------ the trajectory

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
        line = f"{self.label}  {self.km:6.1f} km   plus longue {self.longest:5.1f}"
        if self.open:
            line += f"   ⧗ en cours, {self.elapsed} j sur 7 — hors verdict"
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
    current = _week_label(today)
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
            elapsed=(today - _week_start(label)).days + 1 if label == current else None))
    return traces


def trajectory_verdict(traces: list[WeekTrace]) -> list[str]:
    """Two comparisons, on CLOSED weeks only.

    Volume is compared as a MEAN (an assimilation week inside the window must not pass
    for a drop); the longest run is compared as a MAXIMUM, because that is what it is:
    a record is not averaged.
    """
    closed = [trace for trace in traces if not trace.open]
    if len(closed) < 2 * TRAJECTORY_HALF:
        return [f"pente non calculable — {len(closed)} semaine(s) close(s) sur "
                f"les {2 * TRAJECTORY_HALF} qu'il faut"]
    recent, before = closed[-TRAJECTORY_HALF:], closed[-2 * TRAJECTORY_HALF:-TRAJECTORY_HALF]
    lines = []
    for name, now, then in (
            ("Volume", sum(t.km for t in recent) / TRAJECTORY_HALF,
             sum(t.km for t in before) / TRAJECTORY_HALF),
            ("Plus longue", max(t.longest for t in recent),
             max(t.longest for t in before))):
        change = f"{(now / then - 1) * 100:+.0f} %" if then else "—"
        lines.append(f"{name} : {now:.1f} contre {then:.1f} sur les "
                     f"{TRAJECTORY_HALF} d'avant — {change}")
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
            return (f"Plus longue : record de {self.record_km:.1f} km battu en "
                    f"{self.record_week}, la dernière semaine close")
        head = "🔴 " if self.triggered else ""
        line = (f"{head}Plus longue sans nouveau record depuis {self.weeks} semaine(s) "
                f"close(s) — record {self.record_km:.1f} km en {self.record_week}")
        if self.triggered:
            line += (" · règle s9 : le protocole d'instruction des baisses s'ouvre "
                     "maintenant, sans attendre son jalon daté")
        return line


def long_run_stall(rows: list[Row], today: dt.date) -> Stall | None:
    """The tripwire of pre-mortem scenario 9, COMPUTED: a stalled longest run.

    `trajectory_verdict` does not measure it: it compares the maximum of three weeks to
    the maximum of the previous three, so a longest run flat for three weeks after a
    record reads there as a rise. Here, a record is the maximum of ALL earlier closed
    weeks; we count the latest closed weeks that do not beat it (within the stall
    tolerance: 11.53 against 11.5 is not progress). The current week is never judged.
    """
    current = _week_label(today)
    closed = sorted({row.week for row in _runs(rows)} - {current})
    if not closed:
        return None
    # Weeks without a run count too: not having run is not progressing.
    labels = []
    week = _week_start(closed[0])
    last_closed = _week_start(current) - dt.timedelta(weeks=1)
    while week <= last_closed:
        labels.append(_week_label(week))
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
    race_week = _week_start(_week_label(race_on))
    if taper_weeks is None:
        taper_weeks = rulebook.current().parameters.taper_weeks
    taper_start = race_week - dt.timedelta(weeks=taper_weeks - 1)
    # The current week may already have had its long run: start from the next one, a
    # cautious reading on a review day.
    week = _week_start(_week_label(today)) + dt.timedelta(weeks=1)
    weeks = []
    while week < taper_start:
        weeks.append(_week_label(week))
        week += dt.timedelta(weeks=1)
    return Projection(start_km=longest, weeks=weeks, race_on=race_on,
                      taper_from=_week_label(taper_start))


def next_race_date(store: LedgerStore, today: dt.date) -> dt.date | None:
    """The next TYPED race (`redline fact-write deadline --kind race`), never prose."""
    dates = []
    for row in store.deadline_rows():
        if row.get("kind") != "race" or row.get("status") == "done":
            continue
        due = row.get("due_on")
        if isinstance(due, str):
            due = dt.date.fromisoformat(due)
        if isinstance(due, dt.date) and due > today:
            dates.append(due)
    return min(dates) if dates else None


def transition_notes(rows: list[Row], sessions: list[dict], today: dt.date) -> list[str]:
    """The gap of each planned run to the longest of the previous 30 days.

    Informational, never blocking, and without a threshold: no fixed percentage judges
    progression. The gap is shown so it can be decided.
    """
    notes = []
    for session in sessions:
        if session.get("kind") != "run" or session.get("status") == "cancelled":
            continue
        day = dt.date.fromisoformat(session["date"])
        if day < today:
            continue
        planned = session.get("distance_cap_km") or session.get("distance_target_km")
        if not planned:
            continue
        longest = longest_recent(rows, day)
        if longest is None:
            notes.append(f"{session['id']} {float(planned):.1f} km : aucune course dans "
                         "les 30 j, écart non calculable")
            continue
        notes.append(f"{session['id']} {float(planned):.1f} km = "
                     f"{(float(planned) / longest - 1) * 100:+.0f} % de {longest:.1f} km "
                     "(plus longue des 30 j)")
    return notes


def projection_lines(projection: Projection, target_km: float | None = None) -> list[str]:
    lines = [f"Course du {projection.race_on:%d/%m} : {len(projection.weeks)} semaine(s) "
             f"utile(s) avant l'affûtage ({projection.taper_from}), depuis "
             f"{projection.start_km:.1f} km (plus longue des 30 j)."]
    if not target_km:
        lines.append("Cible de sortie longue non fournie (--long-run-target KM) : rythme "
                     "nécessaire non calculé.")
        return lines
    parts = []
    for lost in (0, 1, 2):
        growth = projection.required_growth(target_km, lost)
        label = ("toutes les semaines" if not lost
                 else f"{lost} semaine(s) sans hausse")
        parts.append(f"{label} : " + ("impossible" if growth is None
                                      else f"+{(growth - 1) * 100:.0f} %/sem"))
    lines.append(f"Pour {target_km:.1f} km avant l'affûtage, il faut en moyenne "
                 + " ; ".join(parts) + ". Un rythme, pas une règle : il se juge sur la "
                 "tolérance observée et l'historique.")
    return lines


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def week_report(store: LedgerStore, rows: list[Row], instruments: dict, targets: dict,
                label: str, today: dt.date, *, target_km: float | None = None,
                closing: bool = False) -> list[str]:
    """A week's report: volume, gauge, pain, then the slope.

    ONE function for the CLI (`redline ledger --week`) and for the dossier the service
    computes before the Sunday review (`weekly_dossier.py`): two renderings of the same
    computation would diverge.
    """
    per_week = eq_hours_per_week(rows, instruments, targets)
    week_rows = [row for row in rows if row.week == label]
    instr = instruments.get(label, Instruments())
    h_eq, level, trace = equivalent_hours(week_rows, instr, targets)
    ratio = acute_chronic_ratio(per_week, label)
    lines = [f"Semaine {label}", ""]
    # Volume before the gauge: it is the figure the athlete hears most often, and the only
    # one they can recount themselves. It comes from here or not at all.
    lines.append(f"Volume : {week_volume(week_rows)}")
    lines.append("(le registre, pas le plan — `Vol. cible` de current-plan.md est une "
                 "intention, pas un fait)")
    lines.append("")
    lines.extend(f"  {line}" for line in trace)
    over_level = " 🔴" if level > LEVEL_CAP else ""
    over_ratio = " 🔴" if ratio and ratio > RATIO_CAP else ""
    lines.append("")
    lines.append(f"Jauge : {h_eq:.2f} h-éq ({level}/100{over_level}) · "
                 f"ratio {ratio if ratio is not None else '—'}{over_ratio}")
    lines.append(f"(sommeil={instr.sleep or 'non déclaré'}, "
                 f"charge_vie={instr.life_load or 'non déclarée'})")
    dminus = week_descent(week_rows)
    if dminus:
        lines.append(f"D− de la semaine : {dminus} m — c'est LUI que compte la jauge")
    zones = pain_zones(store)
    for zone in zones:
        pain = pain_trend(week_rows, 1, zone.slug)
        if pain:
            lines.append(f"Douleur {zone.label} : {pain[0]}")
        lines.extend(f"⚠ Esculier {zone_prefix(zone, zones)}{flag}"
                     for flag in esculier_check(week_rows, zone.slug, history=rows))
    lines.extend(gate(rows, label, instr, zones))
    lines.extend(f"⚠ au-dessus de la cible — {overshoot}"
                 for overshoot in over_target(week_rows, targets))
    lines.extend(f"⚠ ligne incomplète — {problem}"
                 for problem in week_completeness(store, label, today))

    # The slope comes after the week: a preparation is not judged on seven days.
    reference = min(today, _week_start(label) + dt.timedelta(days=6))
    # `closing`: the Sunday-evening review CLOSES the week. Otherwise it would stay "in
    # progress" for the slope and its long run would only count for the record from
    # Monday, i.e. after the review that needs it.
    slope_day = (_week_start(label) + dt.timedelta(days=7)) if closing else today
    traces = trajectory(rows, slope_day)
    if traces:
        lines.append("")
        lines.append(f"Trajectoire — {len(traces)} dernières semaines")
        lines.extend(f"  {week_trace}" for week_trace in traces)
        lines.extend(trajectory_verdict(traces))
        stall = long_run_stall(rows, slope_day)
        if stall:
            lines.append(str(stall))
        longest = longest_recent(rows, reference)
        if longest is not None:
            lines.append(f"Plus longue des 30 j : {longest:.1f} km — repère, pas un plafond "
                         "(aucun pourcentage fixe)")
        race_on = next_race_date(store, today)
        projection = race_projection(rows, today, race_on) if race_on else None
        if projection:
            lines.extend(projection_lines(projection, target_km))
        elif race_on is None:
            lines.append("Projection course : aucune course typée (redline fact-write deadline "
                         "--kind race) — rien à projeter.")
    return lines


# -------------------------------------------------------------------------------- CLI

# The export's column names. Pain has one column per zone, `pain_<zone>`, between `rpe`
# and `enjoyment`: whatever reads this CSV finds every series under its own name.
CSV_HEADERS = ["date", "week", "type", "km", "duration_h", "hr_avg", "cadence",
               "dplus", "dminus", "rpe", "enjoyment", "note"]


def _export_csv(rows: list[Row], zones: list[PainZone]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    pain_headers = [f"pain_{zone.slug}" for zone in zones]
    writer.writerow([*CSV_HEADERS[:10], *pain_headers, *CSV_HEADERS[10:]])
    for row in rows:
        writer.writerow([row.date, row.week, row.type, row.km,
                         row.hours and round(row.hours, 3), row.hr_avg, row.cadence,
                         row.dplus, row.dminus, row.rpe,
                         *(row.pain_at(zone.slug) for zone in zones),
                         row.enjoyment, row.note])
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=paths.vault_root(), type=Path)
    parser.add_argument("--week", dest="week", help="ex. 2030-W30")
    parser.add_argument("--pain-trend", dest="pain_weeks", type=int, metavar="N")
    parser.add_argument("--csv", action="store_true", help="export normalisé sur stdout")
    parser.add_argument("--long-run-target", dest="target_km", type=float, metavar="KM",
                        help="distance de sortie longue à atteindre avant l'affûtage")
    args = parser.parse_args()

    try:
        store = open_store()
        rows = parse_ledger(store)
    except table.LedgerError as exc:
        raise InputError(f"Registre illisible — {exc}") from exc
    instruments = load_instruments(store)
    # Targets come from the session sheets, never from a hard-coded table.
    targets = programme.rpe_targets(args.root)

    if args.csv:
        sys.stdout.write(_export_csv(rows, pain_zones(store)))
        return 0

    if args.pain_weeks:
        print("Tendance douleur — le PIC fait foi (le déclaratif est biaisé bas).")
        print("Deux séries : douleur PENDANT l'effort, douleur AU REPOS. "
              "Le n compte : un max sur 7 lectures\nn'est pas comparable à un max sur 3.\n")
        zones = pain_zones(store)
        if not zones:
            print("Aucune zone surveillée ([[injuries]] de redline.toml) : rien à suivre.")
        for zone in zones:
            print(f"{zone.label}")
            weeks = pain_trend(rows, args.pain_weeks, zone.slug)
            for week in weeks:
                print(f"  {week}")
            recent = [row for row in rows if weeks and row.week >= weeks[0].week]
            flags = esculier_check(recent, zone.slug, history=rows)
            print("\n  Règle Esculier (≤2/10, pas pire le lendemain) :")
            for flag in flags:
                print(f"    {flag}")
            if not flags:
                print("    rien à signaler")
            print()
        return 0

    per_week = eq_hours_per_week(rows, instruments, targets)
    if not per_week:
        print("Registre vide.")
        return 0
    if args.week:
        try:
            label = normalize_week(args.week, sorted(per_week))
        except ValueError as exc:
            raise InputError(f"Semaine refusée — {exc}") from exc
    else:
        label = max(per_week)

    print()
    for line in week_report(store, rows, instruments, targets, label, dt.date.today(),
                            target_km=args.target_km):
        print(f"  {line}" if line else "")
    print()
    return 0

