"""The strength lineages, read from the ledger: what the progression engine proposes.

The report renders them with the run lineages (`redline.analysis.progression_runs`).

Read-only. A lineage is declared in `[progression]` of the instance's `rulebook.toml`,
one per exercise sheet. Its exposures come from the strength reports (`strength_results`
and the counts of `strength_sets`); its current dose is the sheet's `dose`, as the
strength page serves it. The proposals are handed to the coach, who cites them: nothing
here writes a dose, and nothing refuses a prescription.

Every exposure is read against the dose its session served: the session the app was
given is kept with the report (`strength_sessions.prescription`), each exercise with its
typed slots and the sheet's load. A report kept without it is read against the sheet's
dose of today.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redline.coach.fr import progression as text
from redline.model import iso_week, program, rulebook
from redline.model import race as race_model
from redline.model.cycles import LOW_KINDS
from redline.model.dose import Dose as SheetDose
from redline.model.errors import ConfigError
from redline.model.progression import (
    STRENGTH_FAMILIES,
    Dose,
    Exposure,
    Family,
    Lineage,
    Lineages,
    Proposal,
    Veto,
    VetoKind,
    default,
)

from . import ledger
from .progression import arbitrate, propose
from .progression_readings import strength_exposure


@dataclass(frozen=True)
class Coverage:
    """How the declared lineages and the exercise sheets match."""

    #: Lineages naming no exercise sheet.
    missing: tuple[str, ...] = ()
    #: Lineages whose family does not follow their sheet's measure: (slug, family, measure).
    mismatched: tuple[tuple[str, str, str], ...] = ()
    #: Active sheets no lineage follows.
    unlisted: tuple[str, ...] = ()

    @property
    def refused(self) -> list[str]:
        return ([f"lineage {slug}: no sheet coaching/exercises/{slug}.md" for slug in self.missing]
                + [f"lineage {slug}: family {family} does not follow a {measure} dose"
                   for slug, family, measure in self.mismatched])


def coverage(declared: Lineages, sheets: dict[str, dict]) -> Coverage:
    """The exercise lineages against the sheets, keyed by slug (`program.load(...).exercises`);
    a run or a fuelling lineage follows no sheet."""
    missing, mismatched = [], []
    for lineage in declared.exercises:
        sheet = sheets.get(lineage.slug)
        if sheet is None:
            missing.append(lineage.slug)
            continue
        measure = sheet["dose"]["measure"]
        if STRENGTH_FAMILIES.get(measure) != lineage.family:
            mismatched.append((lineage.slug, lineage.family, measure))
    followed = {lineage.slug for lineage in declared.lineages}
    unlisted = tuple(slug for slug, sheet in sorted(sheets.items())
                     if sheet.get("status") == "active" and slug not in followed)
    return Coverage(tuple(missing), tuple(mismatched), unlisted)


def current_dose(sheet: dict, slug: str) -> Dose | None:
    """The sheet's dose on the ladder of its family; a range reads as its top."""
    dose = SheetDose.read(sheet["dose"], slug)
    if dose.measure == "reps" and dose.reps is not None:
        load = sheet.get("load")
        return (Dose.of(reps=dose.reps.target, load_kg=float(load["kg"])) if load
                else Dose.of(reps=dose.reps.target))
    if dose.measure == "time" and dose.seconds is not None:
        return Dose.of(seconds=dose.seconds.target)
    if dose.measure == "contacts" and dose.contacts is not None:
        return Dose.of(contacts=dose.contacts)
    return None


def served_dose(session: dict, slug: str) -> Dose | None:
    """The dose a strength session served an exercise, read from its typed slots (reps
    and seconds per set, contacts per block) and the load it carried; None when the
    session kept no prescription or the exercise was not served on a ladder."""
    exercises = (session.get("prescription") or {}).get("exercises") or []
    served = next((item for item in exercises if item.get("slug") == slug), None)
    dose = (served or {}).get("dose") or {}
    targets = [slot["t"] for slot in dose.get("slots") or [] if slot.get("t") is not None]
    if not targets:
        return None
    measure = dose.get("measure")
    if measure == "reps":
        load = (served or {}).get("load")
        return (Dose.of(reps=max(targets), load_kg=float(load["kg"])) if load
                else Dose.of(reps=max(targets)))
    if measure == "time":
        return Dose.of(seconds=max(targets))
    if measure == "contacts":
        return Dose.of(contacts=sum(targets))
    return None


def vetoes(store: Any, book: rulebook.Rulebook, week: str, today: dt.date) -> tuple[Veto, ...]:
    """The states outside the lineages that stop a rise in `week`."""
    found = []
    race = race_model.active_goal(store)
    if race is not None:
        race_week = iso_week.of(race.on)
        taper = [iso_week.shift(race_week, -offset) for offset in range(race.taper_weeks)]
        if week in taper:
            found.append(Veto(VetoKind.TAPER, "taper_weeks",
                              f"race {race.on.isoformat()}, taper from {min(taper)}"))
    version = store.cycle_for_week(week)
    held = version.week(week) if version is not None else None
    if held is not None and (held.deload or version.kind in LOW_KINDS):
        found.append(Veto(VetoKind.ASSIMILATION, "n2", f"{week} planned low by its cycle"))
    return tuple(found)


def _arbitrated(proposals: list[Proposal], declared: Lineages,
                families: dict[str, Family]) -> list[Proposal]:
    """Each group's rises held to the weekly budget; ungrouped lineages rise alone."""
    groups = {lineage.slug: lineage.group for lineage in declared.lineages}
    out = {proposal.lineage: proposal for proposal in proposals}
    for group in dict.fromkeys(group for group in groups.values() if group):
        members = [proposal for proposal in proposals if groups[proposal.lineage] == group]
        for proposal in arbitrate(members, families, declared.rises_per_group_week,
                                  "rises_per_group_week",
                                  f"group {group}, {declared.rises_per_group_week} a week"):
            out[proposal.lineage] = proposal
    return [out[proposal.lineage] for proposal in proposals]


def proposals(store: Any, root: Path, today: dt.date, week: str | None = None) -> list[Proposal]:
    """One proposal per declared lineage, in declaration order, for `week`.

    Raises ConfigError when a lineage names no sheet, or a sheet of another measure.
    """
    book = rulebook.load(root)
    declared = book.progression
    exercises = declared.exercises
    if not exercises:
        return []
    sheets = program.load(root).exercises
    if refused := coverage(declared, sheets).refused:
        raise ConfigError("\n  - ".join(["[progression] of rulebook.toml:", *refused]))
    families = default()
    rows = ledger.parse_ledger(store)
    zones = [zone.slug for zone in ledger.pain_zones(store)]
    history = store.strength_history()
    vetoed = vetoes(store, book, week or iso_week.of(today), today)
    found = []
    for lineage in exercises:
        current = current_dose(sheets[lineage.slug], lineage.slug)
        found.append(propose(families[lineage.family], lineage,
                             exposures(rows, history, lineage.slug, current, zones),
                             current=current, vetoes=vetoed, today=today,
                             lookback_days=book.parameters.transition_lookback_days))
    return _arbitrated(found, declared, families)


def exposures(rows: Sequence[ledger.Row], history: Sequence[dict], slug: str,
              current: Dose | None, zones: Sequence[str]) -> list[Exposure]:
    """The reports of an exercise, each read against the dose its session served, else
    the sheet's dose of today."""
    found = []
    for session in history:
        dose = served_dose(session, slug) or current
        if dose is not None and (exposure := strength_exposure(rows, session, slug, dose,
                                                               zones)) is not None:
            found.append(exposure)
    return found


def _verdicts(proposal: Proposal) -> str:
    return " ; ".join(
        text.VERDICT.format(
            criterion=item.criterion, verdict=text.VERDICT_WORDS[item.verdict],
            comparison=(text.COMPARISON.format(value=item.value, threshold=item.threshold)
                        if item.value is not None and item.threshold is not None else ""),
            fact=text.FACT.format(fact=item.fact) if item.fact else "")
        for item in proposal.judgements)


def render(found: Sequence[Proposal], declared: Lineages) -> list[str]:
    """The proposals as the coach reads them, one block per lineage."""
    if not declared.lineages:
        return [text.NONE_DECLARED]
    groups = {lineage.slug: lineage.group for lineage in declared.lineages}
    lines = [text.HEADER]
    for proposal in found:
        group = groups.get(proposal.lineage) or ""
        lines.append(text.LINE.format(
            lineage=proposal.lineage, family=proposal.family,
            group=text.GROUP.format(group=group) if group else "",
            move=text.MOVES[proposal.move], clause=proposal.clause,
            overruled=(text.OVERRULED.format(move=text.MOVES[proposal.rule_move])
                       if proposal.rule_move != proposal.move else "")))
        if proposal.anchor is None or proposal.dose is None:
            lines.append(text.NO_DOSE)
        else:
            lines.append(text.DOSE.format(anchor=proposal.anchor, dose=proposal.dose))
        lines.append(text.CEILING.format(ceiling=proposal.ceiling)
                     if proposal.ceiling is not None else text.NO_CEILING)
        lines.append(text.VERDICTS.format(verdicts=_verdicts(proposal))
                     if proposal.judgements else text.NO_VERDICT)
        if proposal.missing:
            lines.append(text.MISSING.format(
                signals=", ".join(text.SIGNALS[signal] for signal in proposal.missing)))
        if proposal.unread:
            lines.append(text.UNREAD.format(
                days=", ".join(day.isoformat() for day in proposal.unread)))
        if proposal.vetoes:
            lines.append(text.VETOES.format(vetoes=" ; ".join(
                text.VETO.format(kind=text.VETO_KINDS[veto.kind], rule=veto.rule, fact=veto.fact)
                for veto in proposal.vetoes)))
        if proposal.reentry:
            lines.append(text.REENTRY)
        if proposal.stale and proposal.evidence:
            lines.append(text.STALE.format(day=proposal.evidence[-1]))
        if proposal.ladder_end:
            lines.append(text.LADDER_END)
    return lines


def report(store: Any, root: Path, today: dt.date, week: str | None = None) -> list[str]:
    """`render` of the exercise and run proposals, then the tally of the run lineages:
    what the command prints and the review dossier carries."""
    from .progression_runs import proposals as run_proposals
    from .progression_runs import render_reviews, reviews

    book = rulebook.load(root)
    return [*render([*proposals(store, root, today, week),
                     *run_proposals(store, book, today, week)], book.progression),
            *render_reviews(reviews(store, book, followed(store, root)))]


def followed(store: Any, root: Path) -> list[tuple[Lineage, list[Exposure]]]:
    """Each declared exercise lineage with its reports, for the tally of its steps."""
    book = rulebook.load(root)
    exercises = book.progression.exercises
    if not exercises:
        return []
    sheets = program.load(root).exercises
    rows = ledger.parse_ledger(store)
    zones = [zone.slug for zone in ledger.pain_zones(store)]
    history = store.strength_history()
    return [(lineage, exposures(rows, history, lineage.slug,
                                current_dose(sheets[lineage.slug], lineage.slug), zones))
            for lineage in exercises if lineage.slug in sheets]
