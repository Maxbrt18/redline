"""The progression engine: a lineage's history in, the next dose out, with its reasons.

Pure: no storage, no clock, no rulebook. The caller passes the family, the lineage, the
exposures, the vetoes in force and the day. What the engine guarantees:

- **a missing reading never passes**: a criterion without its reading is unknown, and an
  exposure without a required reading is left out of the evidence, listed as unread;
- **a rise cites its rule**: the clause, every verdict and the fact behind each reading;
- **a rise never passes a veto**, and a veto never blocks a step down;
- **safety is never excused**: an exposure disrupted by a cause outside training stops
  counting for or against the dose, except for the failures that call for a step down;
- **a comeback steps down**: past the transition window, the dose goes back before it
  goes up again.

The ceiling of a proposal is what a guard on write compares a prescribed dose with: a
plan may always prescribe less, never more.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from redline.model.progression import (
    Clause,
    Criterion,
    Dose,
    Exposure,
    Family,
    Judgement,
    Lineage,
    Move,
    Proposal,
    Severity,
    Side,
    Signal,
    StepMode,
    Unit,
    Verdict,
    Veto,
    VetoKind,
)

#: Float steps (0.5 kg, 2.5 g/h) accumulate rounding; a bound is reached within this.
EPSILON = 1e-6
#: Below this many rises read, what followed them says nothing about the step size.
TALLY_MIN_RISES = 5
#: At or above this share of rises tolerated, the step could grow.
TALLY_GROW_SHARE = 0.9
#: Below this share, the step should shrink.
TALLY_SHRINK_SHARE = 0.5


# ------------------------------------------------------------------ the ladder

def _key(family: Family, dose: Dose) -> tuple[float, ...]:
    """The order of the ladder: the outermost variable weighs most."""
    return tuple(dose.get(variable.name) or 0.0 for variable in reversed(family.ladder))


def above(family: Family, dose: Dose, other: Dose) -> bool:
    return _key(family, dose) > _key(family, other)


def step_up(family: Family, lineage: Lineage, dose: Dose) -> Dose | None:
    """The next rung, or None at the top (or when a bound is not declared).

    The innermost variable moves first. When it is at its maximum, the next one moves
    and the inner ones that reset go back to their minimum: reps after a load rise.
    """
    values = dict(dose.values)
    for index, variable in enumerate(family.ladder):
        bound, value = lineage.bound(variable.name), values.get(variable.name)
        if variable.mode == StepMode.FREE:
            return dose
        if bound is None or value is None:
            return None
        if value + bound.step <= bound.maximum + EPSILON:
            values[variable.name] = round(value + bound.step, 6)
            for inner in family.ladder[:index]:
                inner_bound = lineage.bound(inner.name)
                if inner.resets and inner_bound is not None:
                    values[inner.name] = inner_bound.minimum
            return Dose(tuple((name, values[name]) for name, _ in dose.values))
    return None


def step_down(family: Family, lineage: Lineage, dose: Dose,
              seen: Sequence[Dose] = ()) -> Dose | None:
    """The rung below, or None at the bottom.

    The innermost variable gives first; when it is at its minimum, the next one steps
    down and the inner ones that reset go to their maximum, the rung that earned the
    rise. A free variable steps back to the highest value already run below it.
    """
    values = dict(dose.values)
    for index, variable in enumerate(family.ladder):
        bound, value = lineage.bound(variable.name), values.get(variable.name)
        if value is None:
            return None
        if variable.mode == StepMode.FREE:
            lower = [past for item in seen
                     if (past := item.get(variable.name)) is not None and past < value]
            return dose.with_value(variable.name, max(lower)) if lower else None
        if bound is None:
            return None
        if value - bound.step >= bound.minimum - EPSILON:
            values[variable.name] = round(value - bound.step, 6)
            for inner in family.ladder[:index]:
                inner_bound = lineage.bound(inner.name)
                if inner.resets and inner_bound is not None:
                    values[inner.name] = inner_bound.maximum
            return Dose(tuple((name, values[name]) for name, _ in dose.values))
    return None


def _rises_freely(family: Family, lineage: Lineage, dose: Dose) -> bool:
    """Whether the next rise moves a free variable, whose size nothing bounds."""
    for variable in family.ladder:
        if variable.mode == StepMode.FREE:
            return True
        bound, value = lineage.bound(variable.name), dose.get(variable.name)
        if bound is not None and value is not None and value + bound.step <= bound.maximum + EPSILON:
            return False
    return False


# ------------------------------------------------------------------ the verdicts

def _threshold(criterion: Criterion, dose: Dose | None, lineage: Lineage) -> float | None:
    if criterion.ref is None:
        return criterion.threshold
    value = dose.get(criterion.ref) if dose is not None else None
    return value if value is not None else lineage.parameter(criterion.ref)


def _holds(criterion: Criterion, value: float, threshold: float) -> bool:
    if criterion.side == Side.AT_MOST:
        return value <= threshold + EPSILON
    return value >= threshold - EPSILON


def judge(family: Family, lineage: Lineage, exposure: Exposure) -> tuple[Judgement, ...]:
    """Every criterion that applies to the exposure, judged on all its readings.

    A criterion applies when the exposure carries at least one reading of its signal;
    one failing reading fails it (every watched zone must pass), and a reading not taken
    makes it unknown. Thresholds read the dose that was run.
    """
    dose = exposure.dose(family.anchor)
    judgements = []
    for criterion in family.criteria:
        readings = exposure.readings_of(criterion.signal)
        if not readings:
            continue
        threshold = _threshold(criterion, dose, lineage)
        known = [reading for reading in readings if reading.value is not None]
        if threshold is None:
            judgements.append(Judgement(criterion.id, Verdict.UNKNOWN, criterion.severity))
            continue
        failing = [reading for reading in known
                   if reading.value is not None and not _holds(criterion, reading.value, threshold)]
        pick = max if criterion.side == Side.AT_MOST else min
        if failing:
            worst = pick(failing, key=lambda reading: reading.value or 0.0)
            verdict = Verdict.FAIL
            if exposure.disrupted is not None and criterion.severity == Severity.HOLD:
                verdict = Verdict.UNKNOWN
            judgements.append(Judgement(criterion.id, verdict, criterion.severity,
                                        worst.value, threshold, worst.fact))
        elif len(known) < len(readings):
            judgements.append(Judgement(criterion.id, Verdict.UNKNOWN, criterion.severity,
                                        None, threshold))
        else:
            worst = pick(known, key=lambda reading: reading.value or 0.0)
            judgements.append(Judgement(criterion.id, Verdict.PASS, criterion.severity,
                                        worst.value, threshold, worst.fact))
    return tuple(judgements)


def _counts(family: Family, exposure: Exposure) -> bool:
    """Evidence at all: every required signal read, and known."""
    for signal in family.requires:
        readings = exposure.readings_of(signal)
        if not readings or any(reading.value is None for reading in readings):
            return False
    return True


def _missing(family: Family, exposure: Exposure) -> tuple[Signal, ...]:
    """The readings to ask for so that this exposure counts and is judged."""
    signals = [signal for signal in family.requires
               if not exposure.readings_of(signal)
               or any(reading.value is None for reading in exposure.readings_of(signal))]
    signals.extend(criterion.signal for criterion in family.criteria
                   if any(reading.value is None
                          for reading in exposure.readings_of(criterion.signal)))
    return tuple(dict.fromkeys(signals))


def _passed(judgements: Iterable[Judgement]) -> bool:
    listed = list(judgements)
    return bool(listed) and all(item.verdict == Verdict.PASS for item in listed)


def _severe(judgements: Iterable[Judgement]) -> bool:
    return any(item.verdict == Verdict.FAIL and item.severity == Severity.DOWN
               for item in judgements)


def _failed(judgements: Iterable[Judgement]) -> bool:
    return any(item.verdict == Verdict.FAIL for item in judgements)


# ------------------------------------------------------------------ the rule

@dataclass(frozen=True)
class _Judged:
    exposure: Exposure
    judgements: tuple[Judgement, ...]

    @property
    def transparent(self) -> bool:
        """A disrupted exposure without a safety failure: neither for nor against."""
        return self.exposure.disrupted is not None and not _severe(self.judgements)


def _passes_in_a_row(family: Family, judged: Sequence[_Judged], anchor: Dose) -> int:
    count = 0
    for item in reversed(judged):
        if item.transparent:
            continue
        if item.exposure.dose(family.anchor) != anchor or not _passed(item.judgements):
            break
        count += 1
    return count


def _fails_in_a_row(judged: Sequence[_Judged]) -> int:
    count = 0
    for item in reversed(judged):
        if item.transparent:
            continue
        if not _failed(item.judgements):
            break
        count += 1
    return count


def _clause_holds(clause: Clause, latest: tuple[Judgement, ...], passes: int,
                  fails: int) -> bool:
    if clause.severe:
        return _severe(latest)
    if clause.fails_at_least is not None:
        return sum(item.verdict == Verdict.FAIL for item in latest) >= clause.fails_at_least
    if clause.unknowns_at_least is not None:
        return sum(item.verdict == Verdict.UNKNOWN for item in latest) >= clause.unknowns_at_least
    if clause.passes_in_a_row is not None:
        return passes >= clause.passes_in_a_row
    if clause.fails_in_a_row is not None:
        return fails >= clause.fails_in_a_row
    return False


def propose(family: Family, lineage: Lineage, history: Sequence[Exposure], *,
            current: Dose | None, vetoes: Sequence[Veto] = (), today: dt.date,
            lookback_days: int | None = None) -> Proposal:
    """The next dose of `lineage`, from its exposures up to `today`.

    `current` is the dose the program prescribes now: it stands when there is no
    evidence yet. `lookback_days` is the transition window (rule n8): a session lineage
    whose last evidence is older comes back a step lower.
    """
    ordered = sorted((exposure for exposure in history if exposure.on <= today),
                     key=lambda exposure: exposure.on)
    evidence = [exposure for exposure in ordered if _counts(family, exposure)]
    unread = tuple(exposure.on for exposure in ordered if not _counts(family, exposure))
    missing = _missing(family, ordered[-1]) if ordered else family.requires
    base = Proposal(lineage=lineage.slug, family=family.slug, rule_move=Move.HOLD,
                    move=Move.HOLD, clause="no-evidence", anchor=current, dose=current,
                    ceiling=None, missing=missing, unread=unread)
    newest = ordered[-1] if ordered else None
    if newest is not None and not _counts(family, newest):
        hurt = judge(family, lineage, newest)
        severe = next((clause for clause in family.rule if clause.severe), None)
        dose = newest.dose(family.anchor) or current
        if severe is not None and _severe(hurt) and dose is not None:
            # A missing count leaves the dose unjudged, never a failure that calls for a
            # step down unheard.
            seen = [item for exposure in ordered if (item := exposure.dose(family.anchor))]
            lower = step_down(family, lineage, dose, seen)
            return replace(base, rule_move=Move.DOWN, move=Move.DOWN if lower else Move.HOLD,
                           clause=severe.id, anchor=dose, dose=lower or dose,
                           ceiling=lower or dose, judgements=hurt, ladder_end=lower is None)
    if not evidence:
        return base
    judged = [_Judged(exposure, judge(family, lineage, exposure)) for exposure in evidence]
    latest = judged[-1]
    anchor = latest.exposure.dose(family.anchor) or current
    seen = [dose for item in judged if (dose := item.exposure.dose(family.anchor))]
    passes = _passes_in_a_row(family, judged, anchor) if anchor is not None else 0
    base = replace(base, anchor=anchor, dose=anchor, ceiling=anchor,
                   judgements=latest.judgements, streak=passes,
                   evidence=tuple(item.exposure.on for item in judged))
    if anchor is None:
        return replace(base, clause="no-dose")

    if (lookback_days is not None and family.unit == Unit.SESSION and family.reentry_steps
            and (today - latest.exposure.on).days > lookback_days):
        dose = anchor
        for _ in range(family.reentry_steps):
            dose = step_down(family, lineage, dose, seen) or dose
        return replace(base, rule_move=Move.DOWN, move=Move.DOWN, clause="reentry",
                       dose=dose, ceiling=dose, reentry=True)

    fails = _fails_in_a_row(judged)
    clause = next((clause for clause in family.rule
                   if _clause_holds(clause, latest.judgements, passes, fails)), None)
    move = clause.move if clause is not None else Move.HOLD
    base = replace(base, rule_move=move, move=move,
                   clause=clause.id if clause is not None else "no-clause")
    if move == Move.DOWN:
        lower = step_down(family, lineage, anchor, seen)
        return replace(base, dose=lower or anchor, ceiling=lower or anchor,
                       move=Move.DOWN if lower else Move.HOLD, ladder_end=lower is None)
    if move == Move.HOLD:
        return base

    if not _counts(family, ordered[-1]):
        return replace(base, move=Move.HOLD, clause="await-reading")
    binding = tuple(veto for veto in vetoes if veto.kind in family.vetoes)
    if binding:
        return replace(base, move=Move.HOLD, vetoes=binding)
    higher = step_up(family, lineage, anchor)
    if higher is None:
        return replace(base, move=Move.HOLD, ladder_end=True)
    ceiling = None if _rises_freely(family, lineage, anchor) else higher
    return replace(base, dose=higher, ceiling=ceiling)


def allows(family: Family, proposal: Proposal, dose: Dose) -> bool:
    """Whether a plan may prescribe `dose`: never above the ceiling, always below it."""
    if proposal.ceiling is None:
        return True
    return not above(family, dose, proposal.ceiling)


# ------------------------------------------------------------------ the week

def arbitrate(proposals: Sequence[Proposal], families: dict[str, Family], budget: int,
              rule: str, fact: str) -> list[Proposal]:
    """At most `budget` rises among lineages that share a budget, the others held.

    The rises kept are those that waited longest (the longest streak at their dose),
    then the order given: two lineages rising together cannot tell which one a bad
    morning belongs to.
    """
    rising = [proposal for proposal in proposals if proposal.move == Move.UP
              and VetoKind.BUDGET in families[proposal.family].vetoes]
    kept = {id(proposal) for proposal in
            sorted(rising, key=lambda proposal: -proposal.streak)[:max(budget, 0)]}
    held = {id(proposal) for proposal in rising} - kept
    veto = Veto(VetoKind.BUDGET, rule, fact)
    out = []
    for proposal in proposals:
        if id(proposal) in held:
            proposal = replace(proposal, move=Move.HOLD, dose=proposal.anchor,
                               ceiling=proposal.anchor, vetoes=(*proposal.vetoes, veto))
        out.append(proposal)
    return out


# ------------------------------------------------------------------ the step itself

@dataclass(frozen=True)
class StepTally:
    """What followed each rise the lineage ran: the falsification of its step."""

    tolerated: tuple[dt.date, ...]
    failed: tuple[dt.date, ...]
    harmed: tuple[dt.date, ...]
    unread: tuple[dt.date, ...]

    @property
    def suggestion(self) -> Move | None:
        """UP: the step could grow; DOWN: it should shrink; None: too few rises read.

        A suggestion, never applied: the step changes in the declaration, by decision.
        """
        read = len(self.tolerated) + len(self.failed) + len(self.harmed)
        if read < TALLY_MIN_RISES:
            return None
        share = len(self.tolerated) / read
        if self.harmed or share < TALLY_SHRINK_SHARE:
            return Move.DOWN
        return Move.UP if share >= TALLY_GROW_SHARE else Move.HOLD


def tally(family: Family, lineage: Lineage, history: Sequence[Exposure],
          confounded: frozenset[dt.date] = frozenset()) -> StepTally:
    """For each first exposure at a raised dose: tolerated, failed or harmed.

    `confounded` holds the days another lineage also rose on: what followed cannot be
    attributed, and those rises are counted as unread.
    """
    evidence = sorted((exposure for exposure in history if _counts(family, exposure)),
                      key=lambda exposure: exposure.on)
    tolerated: list[dt.date] = []
    failed: list[dt.date] = []
    harmed: list[dt.date] = []
    unread: list[dt.date] = []
    for before, after in itertools.pairwise(evidence):
        dose_before, dose_after = before.dose(family.anchor), after.dose(family.anchor)
        if dose_before is None or dose_after is None or not above(family, dose_after,
                                                                  dose_before):
            continue
        judgements = judge(family, lineage, after)
        if after.on in confounded or after.disrupted is not None:
            unread.append(after.on)
        elif _severe(judgements):
            harmed.append(after.on)
        elif _failed(judgements):
            failed.append(after.on)
        elif _passed(judgements):
            tolerated.append(after.on)
        else:
            unread.append(after.on)
    return StepTally(tuple(tolerated), tuple(failed), tuple(harmed), tuple(unread))

