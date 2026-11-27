"""The plan skeleton of the active goal, read from PostgreSQL: candidates and the chosen one.

The computation lives in `redline.model.plan_skeleton`; this module gathers its inputs:
the race and its goal, the template that serves it, the rulebook's thresholds, and where
the athlete stands (the mean weekly running volume of the closed weeks before, the
longest run of the last 30 days), and what the athlete held (`held_of`), the
counter-reading's reference. The skeleton starts with the current week, so a week
off is read at the next computation: what is left compresses or lengthens, never made up.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from typing import Any

from redline.coach.fr import plan_skeleton as text
from redline.coach.fr import session_labels
from redline.model import efforts as effort_model
from redline.model import iso_week, plan_skeleton, plan_templates, rulebook, session_kinds
from redline.model import race as race_model
from redline.model.errors import ConfigError, InputError
from redline.model.plan_skeleton import NOTHING_HELD, Held, Levers, Limits, Skeleton, Start
from redline.model.vocabulary import (
    DeadlineKind,
    RacePriority,
    RaceState,
    RaceTerrain,
    SessionStatus,
)

from . import critical_speed, expected_rpe, ledger, race_estimate, signals

#: The closed weeks the starting volume is the mean of.
START_WEEKS = 3
#: A race run, or started, this close before the skeleton starts calls for a recovery first.
RECOVERY_DAYS = 21
#: The skeleton's figures are rounded to 0.1 km: a bound is met within this.
EPSILON_KM = 0.05
#: A week's long-run share is read from this many runs: with fewer, one run is the week.
SHARE_MIN_RUNS = 3


@dataclass(frozen=True)
class GoalReading:
    """A road goal's estimated time, the bound the recent sessions prove and the easy
    runs' efficiency since the last effort."""

    config: race_estimate.Settings
    estimate: race_estimate.Estimate | None
    bound: race_estimate.Bound | None
    efficiency: signals.Efficiency | None


def goal_reading(store: Any, rows: list[ledger.Row], goal: race_model.Goal,
                 today: dt.date) -> GoalReading | None:
    config = race_estimate.settings()
    if goal.terrain != RaceTerrain.ROAD or config is None:
        return None
    found = race_estimate.estimate(effort_model.efforts(store.effort_rows()),
                                   effort_model.breaks(store.break_rows()), today,
                                   goal.distance_km, config)
    since = (found.sources[-1].effort.on if found is not None
             else today - dt.timedelta(weeks=config.window_weeks))
    return GoalReading(config, found,
                       race_estimate.bound(race_estimate.pieces(store, rows, today, config)),
                       signals.efficiency(rows, since, today))


@dataclass(frozen=True)
class CriticalReading:
    """The critical-speed fit of the recent efforts, None without one, and its settings."""

    config: critical_speed.Settings
    fit: critical_speed.Fit | None


def critical_reading(store: Any, today: dt.date) -> CriticalReading | None:
    config = critical_speed.settings()
    if config is None:
        return None
    return CriticalReading(config, critical_speed.fit(
        effort_model.efforts(store.effort_rows()), today, config))


@dataclass(frozen=True)
class Frame:
    """Everything a skeleton is computed from, but the levers."""

    race: race_model.Race
    goal: race_model.Goal
    template: plan_templates.Template
    start: Start
    limits: Limits
    first: dt.date
    recovering: bool
    held: Held = NOTHING_HELD
    #: What the declared all-out efforts say of a road goal's time; None for a trail goal
    #: or when the rulebook declares no estimate.
    reading: GoalReading | None = None
    #: The critical speed of the recent all-out efforts; None when the rulebook declares
    #: none.
    critical: CriticalReading | None = None


def start_of(rows: list[ledger.Row], today: dt.date) -> Start:
    """The athlete's weekly running volume over the closed weeks, and the longest run."""
    current = iso_week.of(today)
    weeks = [iso_week.shift(current, -offset) for offset in range(1, START_WEEKS + 1)]
    volumes = [ledger.week_volume([row for row in rows if row.week == week]).km_run
               for week in weeks]
    return Start(round(statistics.fmean(volumes), 1),
                 ledger.longest_recent(rows, today) or 0.0)


def held_of(rows: list[ledger.Row], history: expected_rpe.History, today: dt.date) -> Held:
    """What the athlete held over the closed weeks of the RPE model's window: the median
    load a quality run adds beyond its effort-kilometres, the highest weekly run load
    (weeks whose every run has a dose), the largest share of a week's kilometres its
    longest run took."""
    current = iso_week.of(today)
    weeks = {iso_week.shift(current, -offset) for offset in range(1, history.config.weeks + 1)}
    runs = [row for row in rows if row.week in weeks and row.type in session_kinds.RUN
            and row.km]
    premiums = [dose.value - dose.effort_km for row in runs if row.type in session_kinds.QUALITY
                for dose in [history.dose(row)] if dose is not None and dose.effort_km]
    loads, shares = [], []
    for week in weeks:
        own = [row for row in runs if row.week == week]
        doses = [history.dose(row) for row in own]
        if own and all(dose is not None for dose in doses):
            loads.append(sum(dose.value for dose in doses if dose is not None))
        if len(own) >= SHARE_MIN_RUNS:
            shares.append(max(row.km or 0.0 for row in own) / sum(row.km or 0.0 for row in own))
    return Held(statistics.median(premiums) if len(premiums) >= history.config.min_sessions
                else None, max(loads) if loads else None, max(shares) if shares else None)


def frame(store: Any, today: dt.date) -> Frame:
    """The inputs of the goal's skeleton; InputError or ConfigError naming what is
    missing."""
    race = race_model.active_goal(store)
    if race is None:
        raise InputError("no active goal: `redline fact-write deadline --kind race "
                         "--priority a`")
    if race.on < today:
        raise InputError(f"the goal of {race.on.isoformat()} is past: write its outcome with "
                         "`redline fact-write deadline --race-state` (run, abandoned, "
                         "cancelled)")
    goal = race.goal
    if goal is None or goal.peak_volume_km is None or goal.peak_long_run_km is None:
        raise InputError("the race's context lacks its goal: distance_km, terrain, "
                         "peak_volume_km and peak_long_run_km")
    template = race_model.template_of(goal)
    if template is None:
        raise ConfigError(f"no plan template serves {goal.distance_km:g} km on "
                          f"{goal.terrain}")
    book = rulebook.current().parameters
    if book.skeleton_ramp_max_pct is None or book.skeleton_long_step_max_pct is None:
        raise ConfigError("rulebook.toml declares no skeleton thresholds "
                          "(skeleton_ramp_max_pct, skeleton_long_step_max_pct)")
    low, high = race.taper_cut_min_pct, race.taper_cut_max_pct
    taper_cut = (low + high) / 2 if low is not None and high is not None else 0.0
    first = iso_week.monday(iso_week.of(today))
    rows = ledger.parse_ledger(store)
    recovering = any(other.on < race.on and other.state in race_model.RECOVERED_FROM
                     and 0 <= (first - other.on).days <= RECOVERY_DAYS
                     for other in race_model.typed(store))
    quality = goal.quality_per_week
    if quality is None:
        quality = book.quality_sessions_per_week
    elif book.quality_sessions_per_week is not None:
        quality = min(quality, book.quality_sessions_per_week)
    limits = Limits(book.skeleton_ramp_max_pct, book.skeleton_long_step_max_pct, taper_cut,
                    quality, frozenset(book.assimilation_weeks))
    config = expected_rpe.settings()
    held = (held_of(rows, expected_rpe.history(store, rows, config), today)
            if config is not None else NOTHING_HELD)
    return Frame(race, goal, template, start_of(rows, today), limits, first, recovering, held,
                 goal_reading(store, rows, goal, today), critical_reading(store, today))


def candidates(store: Any, today: dt.date) -> tuple[Frame, list[Skeleton]]:
    found = frame(store, today)
    return found, plan_skeleton.candidates(
        found.template, found.goal, found.start, found.limits, found.first,
        found.race.on, recovering=found.recovering, held=found.held)


def chosen(store: Any, today: dt.date) -> Skeleton | None:
    """The skeleton the goal's context chose, computed again from today; None without an
    active goal or when it chose none. A chosen skeleton whose frame cannot be read, a past
    goal among them, is a ConfigError or an InputError naming what is missing, never no
    skeleton."""
    race = race_model.active_goal(store)
    if race is None or not race.declares_skeleton:
        return None
    if race.unread:
        raise ConfigError(f"the context of the race of {race.on.isoformat()} has keys that "
                          f"cannot be read ({', '.join(race.unread)}): write them again with "
                          "`redline fact-write deadline`")
    found = frame(store, today)
    goal = found.goal
    # Declared and read: a readable `skeleton` key is the goal's chosen skeleton.
    assert goal.skeleton is not None
    levers = Levers(goal.skeleton.specific_weeks, goal.skeleton.deload_every,
                    goal.skeleton.second_quality)
    return plan_skeleton.generate(found.template, goal, levers, found.start, found.limits,
                                  found.first, found.race.on, recovering=found.recovering,
                                  candidate=goal.skeleton.candidate, held=found.held)


def race_key(store: Any, race: race_model.Race) -> str:
    """The deadline key of the active goal, to patch its context."""
    for row in store.deadline_rows():
        if (row.get("kind") == DeadlineKind.RACE and row.get("priority") == RacePriority.A
                and row.get("race_state") == RaceState.ACTIVE):
            return str(row["deadline_key"])
    raise InputError(f"no deadline row for the goal of {race.on.isoformat()}")


def _long_run(week: plan_skeleton.Week) -> str:
    return (text.RACE_WEEK if week.long_run_km == 0
            else text.LONG_RUN.format(km=week.long_run_km))


def _qualities(week: plan_skeleton.Week) -> str:
    if not week.qualities:
        return text.NO_QUALITY
    return text.QUALITIES.format(
        count=week.qualities, plural="s" if week.qualities > 1 else "",
        families=", ".join(session_labels.name(family).lower() for family in week.families))


def _week_line(week: plan_skeleton.Week) -> str:
    return text.WEEK.format(
        week=week.label, phase=text.PHASES[week.phase], deload=text.DELOAD if week.deload else "",
        volume=week.volume_km, long_run=_long_run(week), qualities=_qualities(week),
        intent=text.INTENTS[week.climb_intent].format(grade=week.power_hike_from_pct),
        load=text.LOAD.format(load=week.load) if week.load is not None else text.NO_LOAD)


def _clock(seconds: float) -> str:
    hours, rest = divmod(round(seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}"


def _goal_line(goal: race_model.Goal) -> str:
    if goal.goal_time_s is None or goal.goal_source is None:
        return ""
    return text.GOAL.format(time=_clock(goal.goal_time_s),
                            source=text.GOAL_SOURCES[goal.goal_source])


def _source_line(source: race_estimate.Source) -> str:
    effort = source.effort
    return text.ESTIMATE_SOURCE.format(
        kind=text.EFFORT_KINDS[effort.kind], km=text.number(effort.km),
        clock=_clock(effort.seconds), on=effort.on, months=text.number(source.months),
        vdot=text.number(source.vdot), equivalent=_clock(source.equivalent_s),
        share=f"{source.share * 100:.0f}")


def _pace(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _critical_lines(reading: CriticalReading | None) -> list[str]:
    """The critical speed and the domains around it, or the test that would give one."""
    if reading is None:
        return []
    found, config = reading.fit, reading.config
    if found is None:
        return [text.NO_CRITICAL_SPEED.format(weeks=config.weeks)]
    if found.speed_se is None:
        error = text.CRITICAL_SPEED_TWO
    else:
        slower = 1000 / (found.speed_mps - found.speed_se) - found.pace_s
        error = text.CRITICAL_SPEED_ERROR.format(error=f"{slower:.0f}")
    return [text.CRITICAL_SPEED.format(
                pace=_pace(found.pace_s), speed=text.number(found.speed_mps * 3.6),
                d_prime=f"{found.d_prime_m:.0f}", n=len(found.efforts),
                first=found.efforts[0].on, last=found.efforts[-1].on, error=error),
            text.CRITICAL_SPEED_DOMAINS.format(
                easy=_pace(found.pace_at(config.first_threshold_pct)),
                first=text.number(config.first_threshold_pct),
                low=_pace(found.pace_at(config.threshold_low_pct)),
                high=_pace(found.pace_at(config.threshold_high_pct)),
                low_pct=text.number(config.threshold_low_pct),
                high_pct=text.number(config.threshold_high_pct), pace=_pace(found.pace_s))]


def _estimate_lines(reading: GoalReading | None) -> list[str]:
    """What the declared efforts say of a road goal's time, with its controls."""
    if reading is None:
        return []
    found, config = reading.estimate, reading.config
    if found is None:
        lines = [text.NO_ESTIMATE.format(km=text.number(config.min_source_km),
                                         weeks=config.window_weeks)]
    else:
        lines = [text.ESTIMATE.format(time=_clock(found.time_s), low=_clock(found.low_s),
                                      high=_clock(found.high_s), band=found.band_pct,
                                      vdot=text.number(found.vdot),
                                      sd=text.number(found.vdot_sd))]
        if found.capped_by is not None:
            lines.append(text.ESTIMATE_CAPPED.format(start=found.capped_by.starts_on,
                                                     end=found.capped_by.ends_on,
                                                     time=_clock(found.time_s)))
        if len(found.sources) == 1:
            lines.append(text.ESTIMATE_SINGLE.format(months=text.number(found.sources[0].months)))
        lines += [_source_line(source) for source in found.sources]
    held = reading.bound
    if held is not None:
        above = found is not None and held.vdot > found.vdot
        lines.append(text.BOUND.format(weeks=config.bound_weeks, vdot=text.number(held.vdot),
                                       km=text.number(held.km), clock=_clock(held.seconds),
                                       on=held.on) + (text.BOUND_ABOVE if above else ""))
    trend = reading.efficiency
    if trend is not None:
        lines.append(text.EFFICIENCY.format(
            change=f"{trend.change_pct:+.1f}".replace(".", ","), since=trend.since,
            before=f"{trend.before:.2f}".replace(".", ","),
            now=f"{trend.now:.2f}".replace(".", ","), runs_before=trend.runs_before,
            runs_now=trend.runs_now))
    return lines


def _risks(skeleton: Skeleton) -> str:
    if not skeleton.risks:
        return text.NO_RISK
    return text.RISKS.format(risks="; ".join(
        text.RISK_TEXT[risk.kind].format(value=risk.value, reference=risk.reference,
                                         week=risk.week)
        for risk in skeleton.risks))


def _held_line(held: Held) -> str:
    return text.HELD.format(
        premium=text.number(held.quality_premium) if held.quality_premium is not None
        else text.UNKNOWN,
        load=text.number(held.week_load) if held.week_load is not None else text.UNKNOWN,
        share=f"{held.long_share * 100:.0f} %" if held.long_share is not None else text.UNKNOWN)


def render(found: Frame, skeletons: list[Skeleton], *, chosen: bool = False) -> list[str]:
    """The candidates, or the chosen skeleton, week by week, as the coach reads them."""
    lines = [text.HEADER.format(race=found.race.name or "course", race_on=found.race.on,
                                distance=f"{found.goal.distance_km:g}",
                                terrain=found.goal.terrain, goal=_goal_line(found.goal),
                                template=found.template.slug),
             text.START.format(volume=f"{found.start.volume_km:g}",
                               long_run=f"{found.start.long_run_km:g}",
                               recovery=text.RECOVERY if found.recovering else ""),
             *_estimate_lines(found.reading),
             *_critical_lines(found.critical),
             _held_line(found.held)]
    for skeleton in skeletons:
        levers = skeleton.levers
        line = text.CHOSEN if chosen else text.CANDIDATE
        lines += ["", line.format(candidate=skeleton.candidate, specific=levers.specific_weeks,
                                  deload=levers.deload_every,
                                  second=text.SECOND[levers.second_quality]),
                  _risks(skeleton), *(_week_line(week) for week in skeleton.weeks)]
    if not chosen and found.goal.skeleton is None:
        lines += ["", text.NONE_CHOSEN]
    return lines


def report(store: Any, today: dt.date) -> list[str]:
    """The chosen skeleton when the race has one, else the candidates."""
    found, skeletons = candidates(store, today)
    current = chosen(store, today)
    return render(found, [current] if current is not None else skeletons,
                  chosen=current is not None)


def _distance(session: dict) -> float | None:
    value = session.get("distance_cap_km") or session.get("distance_target_km")
    return None if value is None else float(value)


def revision_errors(skeleton: Skeleton, week: str, revision: dict,
                    previous: dict | None) -> list[str]:
    """A weekly revision against the chosen skeleton's week: its volume target, its long
    run, its quality sessions and their families. A plan may always ask less; what the
    previous revision already held is carried, not given again."""
    bound = skeleton.week(week)
    if bound is None:
        return []
    where = f"{week} revision {revision.get('revision')}"
    tail = (f" -- skeleton « {skeleton.candidate} » (`redline plan-skeleton`); change a "
            "lever and choose again rather than exceed a week")
    live = [session for session in revision.get("sessions") or []
            if session.get("status") != SessionStatus.CANCELLED]
    before = {session["id"]: session for session in (previous or {}).get("sessions") or []
              if session.get("status") != SessionStatus.CANCELLED}
    problems = []
    volume, held = revision.get("volume_target_km"), (previous or {}).get("volume_target_km")
    if (volume is not None and volume > bound.volume_km + EPSILON_KM
            and (held is None or volume > held)):
        problems.append(f"{where}.volume_target_km: {volume:g} km above the "
                        f"{bound.volume_km:g} km of the week" + tail)
    for session in live:
        if session.get("workout") != session_kinds.LONG:
            continue
        distance, old = _distance(session), before.get(session["id"])
        if (distance is not None and distance > bound.long_run_km + EPSILON_KM
                and (old is None or (_distance(old) or 0) < distance)):
            problems.append(f"{where}.{session['id']}: long run of {distance:g} km above the "
                            f"{bound.long_run_km:g} km of the week" + tail)
    quality = [session for session in live if session.get("workout") in session_kinds.QUALITY]
    held_quality = [session for session in before.values()
                    if session.get("workout") in session_kinds.QUALITY]
    if len(quality) > bound.qualities and len(quality) > len(held_quality):
        problems.append(f"{where}: {len(quality)} quality sessions for {bound.qualities} in "
                        "the week" + tail)
    for session in quality:
        old = before.get(session["id"])
        if session["workout"] not in bound.families and (
                old is None or old.get("workout") != session["workout"]):
            problems.append(f"{where}.{session['id']}: {session['workout']} is not a family "
                            f"of the week ({', '.join(bound.families) or 'none'})" + tail)
    return problems


def bounds_line(skeleton: Skeleton, week: plan_skeleton.Week) -> str:
    """One week's bounds in a line: what the weekly review prescribes within."""
    return text.BOUNDS.format(candidate=skeleton.candidate, week=week.label,
                              phase=text.PHASES[week.phase],
                              deload=text.DELOAD if week.deload else "",
                              volume=week.volume_km, long_run=_long_run(week),
                              qualities=_qualities(week))
