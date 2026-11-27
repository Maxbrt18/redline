"""What the coach reads of the cycles: the candidates the engine generates, the version in
force, and the week of it the Sunday review prepares.

The candidates are generated from the athlete's real state, the store read once
(`redline.analysis.cycle_inputs`): for the active goal, the preparation's candidates;
without one, a block of the general preparation at the default level, or another kind or
level when asked. Each week shows its phase, deload, volume, long run and key sessions,
with today's paces rendered from the calibration in force; the fitness lines follow the
header (`redline.analysis.fitness_report`).

The review reads the active version's next week, and is told when the goal's preparation
window is open while another cycle is in force: entering a preparation is the athlete's.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from redline.coach.fr import cycle as text
from redline.model import calibration as calibration_model
from redline.model import cycle_templates, instance, intensity, iso_week, paths
from redline.model import plan_generator as generator
from redline.model import race as race_model
from redline.model.cycle_templates import CycleTemplates, Intensity
from redline.model.cycles import CycleVersion
from redline.model.errors import ConfigError, InputError
from redline.model.plan_generator import Key, Trame, Week
from redline.model.progression import Dose
from redline.model.vocabulary import CycleKind, GeneralLevel, KeyRole

from . import calibration, critical_speed, cycle_inputs, fitness_report


def _clock(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _dose(dose: Dose | None) -> str:
    if dose is None:
        return ""
    seconds = dose.get("rep_seconds")
    return text.DOSE.format(reps=f"{dose.get('reps') or 1:g}",
                            duration=_clock(seconds) if seconds else text.NO_DURATION,
                            recovery=_clock(dose.get("recovery_seconds") or 0))


def _intensity(found: Intensity | None, refs: intensity.References) -> str:
    if found is None:
        return ""
    rendered = intensity.render(found.ref, found.band, refs)
    if rendered.pace_s is not None:
        return text.PACE.format(fast=_clock(rendered.pace_s.high),
                                slow=_clock(rendered.pace_s.low))
    if rendered.rpe is not None:
        return text.RPE.format(low=text.number(rendered.rpe.low),
                               high=text.number(rendered.rpe.high))
    if rendered.hr_bpm is not None:
        return text.HEART_RATE.format(low=f"{rendered.hr_bpm.low:.0f}",
                                      high=f"{rendered.hr_bpm.high:.0f}")
    return text.UNCALIBRATED


def _key(key: Key, refs: intensity.References) -> str:
    if key.role == KeyRole.TEST:
        return text.TEST
    return text.KEY.format(position=key.position,
                           role=text.FLAGSHIP if key.role == KeyRole.FLAGSHIP else "",
                           workout=key.workout, dose=_dose(key.dose),
                           intensity=_intensity(key.intensity, refs)).strip()


def week_line(week: Week, keys: list[Key], refs: intensity.References) -> str:
    segment = (text.SEGMENT.format(km=week.segment.km,
                                   intensity=_intensity(week.segment.intensity, refs))
               if week.segment else "")
    listed = "; ".join(_key(key, refs) for key in keys)
    return text.WEEK.format(
        week=week.label, phase=text.PHASES[week.phase], deload=text.DELOAD if week.deload else "",
        volume=week.volume_km,
        long_run=text.RACE_WEEK if not week.long_run_km else
        text.LONG_RUN.format(km=week.long_run_km),
        segment=segment, keys=text.KEYS.format(keys=listed) if listed else "")


def _risks(trame: Trame) -> str:
    if not trame.risks:
        return text.NO_RISK
    return text.RISKS.format(risks="; ".join(
        text.RISK_TEXT[risk.kind].format(value=risk.value, reference=risk.reference,
                                         workout=risk.workout,
                                         phase=text.PHASES.get(risk.phase or "", ""))
        for risk in trame.risks))


def _weeks(trame: Trame, refs: intensity.References) -> list[str]:
    return [week_line(week, [key for key in trame.keys if key.week == week.label], refs)
            for week in trame.weeks]


def header(trame: Trame) -> list[str]:
    inputs = trame.hypotheses
    template = text.LEVELS.get(trame.template, trame.template)
    lines = [text.HEADER.format(kind=text.KINDS[trame.kind], template=template,
                                origin=trame.weeks[0].label if trame.weeks else inputs.origin,
                                start=text.number(inputs.start_km),
                                held=text.number(inputs.held_km))]
    if trame.goal is not None:
        lines.append(text.HEADER_GOAL.format(on=trame.goal.on,
                                             distance=text.number(trame.goal.distance_km)))
    lines.append(text.HEADER_CALIBRATION.format(on=inputs.calibration_on)
                 if inputs.calibration_on is not None else text.NO_CALIBRATION)
    if inputs.minutes_per_week is not None and inputs.easy_pace_s:
        lines.append(text.HEADER_TIME.format(minutes=inputs.minutes_per_week,
                                             pace=_clock(inputs.easy_pace_s)))
    return lines


def render(trames: list[Trame], refs: intensity.References,
           fitness: list[str] | None = None) -> list[str]:
    """The trames as the coach reads them, candidate by candidate."""
    lines = [*header(trames[0]), *(fitness or [])]
    for trame in trames:
        lines.append("")
        if trame.levers is not None:
            lines.append(text.CANDIDATE.format(candidate=trame.candidate,
                                               specific=trame.levers.specific_weeks,
                                               every=trame.levers.deload_every,
                                               second=text.SECOND[trame.levers.second_quality]))
        lines.append(_risks(trame))
        lines += _weeks(trame, refs)
    return lines


def references(store: Any, goal: race_model.Goal | None) -> intensity.References:
    """What a trame's intensities are rendered against today."""
    athlete = instance.load(paths.vault_root())
    speed = (goal.distance_km * 1000 / goal.goal_time_s
             if goal is not None and goal.goal_time_s else None)
    return calibration.references(calibration_model.in_force(store.calibration_rows()),
                                  critical_speed.settings(), speed, athlete.hr_rest,
                                  athlete.hr_max)


def _goal_ahead(store: Any, today: dt.date) -> race_model.Race | None:
    goal = race_model.active_goal(store)
    return goal if goal is not None and goal.goal is not None and goal.on >= today else None


def _last_race_km(store: Any) -> float:
    ran = [race for race in race_model.typed(store)
           if race.state in race_model.RECOVERED_FROM and race.distance_km is not None]
    if not ran:
        raise InputError(text.UNKNOWN_DISTANCE)
    distance = ran[-1].distance_km
    assert distance is not None
    return distance


def candidates(store: Any, today: dt.date, kind: CycleKind | None,
               level: GeneralLevel | None) -> list[str]:
    """The trames asked for, rendered: nothing is written."""
    templates = cycle_templates.load(paths.vault_root())
    inputs = cycle_inputs.inputs(store, templates, today)
    race = _goal_ahead(store, today)
    goal = race.goal if race is not None else None
    kind = kind or (CycleKind.PREPARATION if goal is not None else CycleKind.GENERAL)
    if kind == CycleKind.PREPARATION:
        if race is None or goal is None:
            raise InputError("no active goal ahead: `redline fact-write deadline --kind race "
                             "--priority a`")
        template = templates.preparation_for(goal.distance_km, goal.terrain)
        if template is None:
            raise ConfigError(f"no cycle template serves {goal.distance_km:g} km on "
                              f"{goal.terrain}")
        trames = generator.candidates(template, generator.Goal(race.on, goal.distance_km),
                                      inputs)
    elif kind == CycleKind.GENERAL:
        general = templates.general_at(level or templates.default_level)
        if general is None:
            raise ConfigError(f"no general template at the level {level}")
        trames = [generator.general(general, inputs)]
    elif kind == CycleKind.RECOVERY:
        recovery = templates.recovery_after(_last_race_km(store))
        if recovery is None:
            raise ConfigError("no recovery template serves the last race's distance")
        trames = [generator.recovery(recovery, inputs)]
    else:
        trames = [generator.rebuild(templates.rebuild, inputs)]
    return render(trames, references(store, goal), fitness_report.lines(store, today, goal))


def version_line(version: CycleVersion) -> str:
    level = text.LEVELS.get(str(version.level or ""), "")
    return text.VERSION.format(kind=text.KINDS[version.kind],
                               level=text.VERSION_LEVEL.format(level=level) if level else "",
                               version=version.version, cause=text.CAUSES[version.cause],
                               start=version.trame.weeks[0].label, end=version.last_week)


def shown(store: Any, version: CycleVersion, today: dt.date) -> list[str]:
    """The version in force, whole, as the coach reads it."""
    race = _goal_ahead(store, today)
    goal = race.goal if race is not None else None
    refs = references(store, goal)
    trame = version.trame
    return [version_line(version), *header(trame), *fitness_report.lines(store, today, goal),
            _risks(trame), *_weeks(trame, refs)]


def window_line(store: Any, templates: CycleTemplates, version: CycleVersion | None,
                today: dt.date) -> str | None:
    """The goal's preparation window, open while another cycle is in force."""
    race = _goal_ahead(store, today)
    if race is None or race.goal is None or (
            version is not None and version.kind == CycleKind.PREPARATION):
        return None
    template = templates.preparation_for(race.goal.distance_km, race.goal.terrain)
    if template is None:
        return None
    opens = generator.window_start(template, race.on)
    if iso_week.of(today) < iso_week.shift(opens, -1):
        return None
    return text.WINDOW_OPEN.format(race=race.name or text.UNNAMED_RACE, on=race.on, week=opens)


def review_bound(store: Any, week: str) -> tuple[CycleVersion, Week] | None:
    """The version in force and its week after `week`, the one the review of `week`
    prepares; None without a version or that week."""
    version = store.active_cycle()
    upcoming = iso_week.shift(week, 1)
    found = version.week(upcoming) if version is not None else None
    return None if version is None or found is None else (version, found)


def review_lines(store: Any, week: str, today: dt.date) -> list[str]:
    """What the Sunday review of `week` reads of the cycles: the version in force and the
    week it prepares within, or the open window of the goal's preparation."""
    version = store.active_cycle()
    lines = []
    if version is not None:
        lines.append(version_line(version))
        found = review_bound(store, week)
        if found is not None:
            _, bound = found
            race = _goal_ahead(store, today)
            lines.append(week_line(bound, version.keys_of(bound.label),
                                   references(store, race.goal if race else None)))
        else:
            lines.append(text.NO_WEEK.format(week=iso_week.shift(week, 1)))
    else:
        lines.append(text.NO_CYCLE)
    opened = window_line(store, cycle_templates.load(paths.vault_root()), version, today)
    return [*lines, *([opened] if opened else [])]
