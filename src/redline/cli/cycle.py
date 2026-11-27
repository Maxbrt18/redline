"""The `redline cycle` command: the trames the engine generates for a cycle, read-only.

`candidates` generates them from the athlete's real state, the store read once
(`redline.analysis.cycle_inputs`): for the active goal, the preparation's candidates;
without one, a block of the general preparation at the default level. `--kind` asks for
another kind of cycle, `--level` for another general level. Each week shows its phase,
deload, volume, long run and key sessions, with today's paces rendered from the
calibration in force; nothing is written.

    redline cycle candidates
    redline cycle candidates --kind general --level progress
"""

from __future__ import annotations

import argparse
import datetime as dt
from typing import Any

from redline.analysis import calibration, critical_speed, cycle_inputs
from redline.coach.fr import cycle as text
from redline.model import calibration as calibration_model
from redline.model import cycle_templates, entrypoint, instance, intensity, paths
from redline.model import plan_generator as generator
from redline.model import race as race_model
from redline.model.cycle_templates import Intensity
from redline.model.errors import ConfigError, InputError
from redline.model.plan_generator import Key, Trame, Week
from redline.model.progression import Dose
from redline.model.vocabulary import CycleKind, GeneralLevel, KeyRole

ACTOR = "cycle"


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
    return text.KEY.format(role=text.FLAGSHIP if key.role == KeyRole.FLAGSHIP else "",
                           workout=key.workout, dose=_dose(key.dose),
                           intensity=_intensity(key.intensity, refs)).strip()


def _week(week: Week, keys: list[Key], refs: intensity.References) -> str:
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


def render(trames: list[Trame], refs: intensity.References) -> list[str]:
    """The trames as the coach reads them, candidate by candidate."""
    first = trames[0]
    inputs = first.hypotheses
    template = text.LEVELS.get(first.template, first.template)
    lines = [text.HEADER.format(kind=text.KINDS[first.kind], template=template,
                                origin=first.weeks[0].label if first.weeks else inputs.origin,
                                start=text.number(inputs.start_km),
                                held=text.number(inputs.held_km))]
    if first.goal is not None:
        lines.append(text.HEADER_GOAL.format(on=first.goal.on,
                                             distance=text.number(first.goal.distance_km)))
    lines.append(text.HEADER_CALIBRATION.format(on=inputs.calibration_on)
                 if inputs.calibration_on is not None else text.NO_CALIBRATION)
    if inputs.minutes_per_week is not None and inputs.easy_pace_s:
        lines.append(text.HEADER_TIME.format(minutes=inputs.minutes_per_week,
                                             pace=_clock(inputs.easy_pace_s)))
    for trame in trames:
        lines.append("")
        if trame.levers is not None:
            lines.append(text.CANDIDATE.format(candidate=trame.candidate,
                                               specific=trame.levers.specific_weeks,
                                               every=trame.levers.deload_every,
                                               second=text.SECOND[trame.levers.second_quality]))
        lines.append(_risks(trame))
        lines += [_week(week, [key for key in trame.keys if key.week == week.label], refs)
                  for week in trame.weeks]
    return lines


def _last_race_km(store: Any) -> float:
    ran = [race for race in race_model.typed(store)
           if race.state in race_model.RECOVERED_FROM and race.goal is not None]
    if not ran:
        raise InputError(text.UNKNOWN_DISTANCE)
    goal = ran[-1].goal
    assert goal is not None
    return goal.distance_km


def candidates(store: Any, today: dt.date, kind: CycleKind | None,
               level: GeneralLevel | None) -> tuple[list[Trame], intensity.References]:
    """The trames asked for, and what their intensities are rendered against."""
    root = paths.vault_root()
    templates = cycle_templates.load(root)
    inputs = cycle_inputs.inputs(store, templates, today)
    goal = race_model.active_goal(store)
    target = goal.goal if goal is not None and goal.on >= today else None
    kind = kind or (CycleKind.PREPARATION if target is not None else CycleKind.GENERAL)
    goal_speed = None
    if kind == CycleKind.PREPARATION:
        if goal is None or target is None:
            raise InputError("no active goal ahead: `redline fact-write deadline --kind race "
                             "--priority a`")
        template = templates.preparation_for(target.distance_km, target.terrain)
        if template is None:
            raise ConfigError(f"no cycle template serves {target.distance_km:g} km on "
                              f"{target.terrain}")
        trames = generator.candidates(template, generator.Goal(goal.on, target.distance_km),
                                      inputs)
        if target.goal_time_s:
            goal_speed = target.distance_km * 1000 / target.goal_time_s
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
    athlete = instance.load(root)
    refs = calibration.references(calibration_model.in_force(store.calibration_rows()),
                                  critical_speed.settings(), goal_speed, athlete.hr_rest,
                                  athlete.hr_max)
    return trames, refs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    listed = commands.add_parser("candidates", help="the trames generated from today's state")
    listed.add_argument("--kind", choices=tuple(CycleKind), default=None,
                        help="the kind of cycle (default: the goal's preparation, else general)")
    listed.add_argument("--level", choices=tuple(GeneralLevel), default=None,
                        help="the general preparation's level (default: the templates')")
    listed.add_argument("--date", type=dt.date.fromisoformat, default=None,
                        help="reference day YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    from redline.storage import open_store

    store = open_store(ACTOR)
    trames, refs = candidates(store, args.date or dt.date.today(),
                              CycleKind(args.kind) if args.kind else None,
                              GeneralLevel(args.level) if args.level else None)
    print("\n".join(render(trames, refs)))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
