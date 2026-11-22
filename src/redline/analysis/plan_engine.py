"""The plan skeleton of the next typed race, read from PostgreSQL: candidates and the chosen one.

The computation lives in `redline.model.plan_skeleton`; this module gathers its inputs:
the race and its goal, the template that serves it, the rulebook's thresholds, and where
the athlete stands (the mean weekly running volume of the closed weeks before, the
longest run of the last 30 days). The skeleton starts with the current week, so a week
off is read at the next computation: what is left compresses or lengthens, never made up.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from typing import Any

from redline.coach.fr import plan_skeleton as text
from redline.coach.fr import session_labels
from redline.model import iso_week, plan_skeleton, plan_templates, rulebook, session_kinds
from redline.model import race as race_model
from redline.model.errors import ConfigError, InputError
from redline.model.plan_skeleton import Levers, Limits, Skeleton, Start
from redline.model.vocabulary import DeadlineStatus, SessionStatus

from . import ledger

#: The closed weeks the starting volume is the mean of.
START_WEEKS = 3
#: A race run this close before the skeleton starts calls for a recovery first.
RECOVERY_DAYS = 21
#: The skeleton's figures are rounded to 0.1 km: a bound is met within this.
EPSILON_KM = 0.05


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


def start_of(rows: list[ledger.Row], today: dt.date) -> Start:
    """The athlete's weekly running volume over the closed weeks, and the longest run."""
    current = iso_week.of(today)
    weeks = [iso_week.shift(current, -offset) for offset in range(1, START_WEEKS + 1)]
    volumes = [ledger.week_volume([row for row in rows if row.week == week]).km_run
               for week in weeks]
    return Start(round(statistics.fmean(volumes), 1),
                 ledger.longest_recent(rows, today) or 0.0)


def frame(store: Any, today: dt.date) -> Frame:
    """The inputs of the next race's skeleton; InputError or ConfigError naming what is
    missing."""
    race = race_model.upcoming(store, today)
    if race is None:
        raise InputError("no typed race ahead: `redline fact-write deadline --kind race`")
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
    recovering = any(other.on < race.on and other.status == DeadlineStatus.DONE
                     and 0 <= (first - other.on).days <= RECOVERY_DAYS
                     for other in race_model.typed(store))
    quality = goal.quality_per_week
    if quality is None:
        quality = book.quality_sessions_per_week
    elif book.quality_sessions_per_week is not None:
        quality = min(quality, book.quality_sessions_per_week)
    limits = Limits(book.skeleton_ramp_max_pct, book.skeleton_long_step_max_pct, taper_cut,
                    quality, frozenset(book.assimilation_weeks))
    return Frame(race, goal, template, start_of(rows, today), limits, first, recovering)


def candidates(store: Any, today: dt.date) -> tuple[Frame, list[Skeleton]]:
    found = frame(store, today)
    return found, plan_skeleton.candidates(
        found.template, found.goal, found.start, found.limits, found.first,
        found.race.on, recovering=found.recovering)


def chosen(store: Any, today: dt.date) -> Skeleton | None:
    """The skeleton the race's context chose, computed again from today; None when the
    race chose none or cannot be framed."""
    try:
        found = frame(store, today)
    except (InputError, ConfigError):
        return None
    goal = found.goal
    if goal.skeleton is None:
        return None
    levers = Levers(goal.skeleton.specific_weeks, goal.skeleton.deload_every,
                    goal.skeleton.second_quality)
    return plan_skeleton.generate(found.template, goal, levers, found.start, found.limits,
                                  found.first, found.race.on, recovering=found.recovering,
                                  candidate=goal.skeleton.candidate)


def race_key(store: Any, race: race_model.Race) -> str:
    """The deadline key of a typed race, to patch its context."""
    for row in store.deadline_rows():
        due = row.get("due_on")
        day = due if isinstance(due, dt.date) else dt.date.fromisoformat(str(due)) if due else None
        if (day == race.on and row.get("kind") == "race"
                and row.get("status") != DeadlineStatus.DONE):
            return str(row["deadline_key"])
    raise InputError(f"no deadline row for the race of {race.on.isoformat()}")


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
        intent=text.INTENTS[week.climb_intent].format(grade=week.power_hike_from_pct))


def _goal_line(goal: race_model.Goal) -> str:
    if goal.goal_time_s is None or goal.goal_source is None:
        return ""
    hours, rest = divmod(goal.goal_time_s, 3600)
    return text.GOAL.format(time=f"{hours}:{rest // 60:02d}:{rest % 60:02d}",
                            source=text.GOAL_SOURCES[goal.goal_source])


def _risks(skeleton: Skeleton) -> str:
    if not skeleton.risks:
        return text.NO_RISK
    return text.RISKS.format(risks="; ".join(
        text.RISK_TEXT[risk.kind].format(value=risk.value, reference=risk.reference)
        for risk in skeleton.risks))


def render(found: Frame, skeletons: list[Skeleton], *, chosen: bool = False) -> list[str]:
    """The candidates, or the chosen skeleton, week by week, as the coach reads them."""
    lines = [text.HEADER.format(race=found.race.name or "course", race_on=found.race.on,
                                distance=f"{found.goal.distance_km:g}",
                                terrain=found.goal.terrain, goal=_goal_line(found.goal),
                                template=found.template.slug),
             text.START.format(volume=f"{found.start.volume_km:g}",
                               long_run=f"{found.start.long_run_km:g}",
                               recovery=text.RECOVERY if found.recovering else "")]
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
