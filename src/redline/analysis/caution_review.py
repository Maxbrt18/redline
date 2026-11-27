"""The coach's cautious decisions, with what followed: what the Sunday review re-examines.

A cautious decision is a typed restriction of a plan revision (`changes[].restriction`):
a session removed, cancelled or cut, by a rule of the rulebook or by the coach's
judgment. A restriction the athlete asked for (`request`) is theirs, not the coach's
caution, and is left out.

What followed is read from the ledger, never inferred: the next morning against the
zone's rest baseline, and the next session of the same kind (run or not, its RPE against
the range its dose expects, its effort pain). A good morning after a cancellation does
not show the cancellation was needed, and a hard one does not show it was not: the facts
are put side by side for the coach to re-examine its over-caution as well as its
over-reach, never to score it.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from redline.coach.fr import caution_review as text
from redline.coach.fr import session_labels
from redline.model import iso_week, session_kinds, weekly_plan
from redline.model.vocabulary import Cut, RpeSide

from . import expected_rpe, ledger

#: The closed week and the weeks before it whose restrictions are re-examined.
WEEKS = 4


@dataclass(frozen=True)
class Restriction:
    """One cautious decision: the session it cut, how, and its typed premise."""

    day: dt.date
    title: str
    workout: str
    cuts: tuple[Cut, ...]
    rule: str
    fact: str


@dataclass(frozen=True)
class Followed:
    """What the ledger holds after a restricted session; None: not read, or not yet."""

    #: The next morning's trace minus the rest baseline, the highest over the zones.
    morning_above_base: float | None
    next_on: dt.date | None
    next_rpe: float | None
    next_side: RpeSide
    next_pain: float | None


def restrictions(plan: dict) -> list[Restriction]:
    """The typed restrictions of a week's revisions decided by rule or judgment."""
    found = []
    revisions = sorted(plan.get("revisions") or [], key=lambda item: item["revision"])
    for before, after in itertools.pairwise(revisions):
        old = {session["id"]: session for session in before.get("sessions") or []}
        new = {session["id"]: session for session in after.get("sessions") or []}
        for change in after.get("changes") or []:
            premise = change.get("restriction")
            session = old.get(change["session_id"])
            if not premise or premise["rule"] == weekly_plan.REQUEST_RULE or session is None:
                continue
            cuts = weekly_plan.restrictions(session, new.get(change["session_id"]))
            if cuts:
                found.append(Restriction(dt.date.fromisoformat(session["date"]),
                                         session["title"], session["workout"], tuple(cuts),
                                         premise["rule"], premise["fact"]))
    return found


def followed(item: Restriction, rows: Sequence[ledger.Row], zones: Sequence[str],
             expectation: Callable[[ledger.Row], expected_rpe.Expectation | None]) -> Followed:
    """The next morning and the next session of the same kind after `item`."""
    morning = item.day + dt.timedelta(days=1)
    above = []
    for zone in zones:
        wake = next((value for row in rows if row.date == morning
                     and row.type == session_kinds.WAKE_TRACE
                     and (value := row.pain_at(zone)) is not None), None)
        base = ledger.rest_peak(list(rows), morning, zone)
        if wake is not None and base is not None:
            above.append(round(wake - base, 2))
    after = next((row for row in sorted(rows, key=lambda row: row.date)
                  if row.date > item.day and row.type == item.workout), None)
    if after is None:
        return Followed(max(above) if above else None, None, None, RpeSide.UNKNOWN, None)
    found = expectation(after)
    pains = [value for zone in zones if (value := after.pain_at(zone)) is not None]
    return Followed(max(above) if above else None, after.date, after.rpe,
                    found.side(after.rpe) if found is not None else RpeSide.UNKNOWN,
                    max(pains) if pains else None)


def _number(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def render_line(item: Restriction, after: Followed) -> str:
    morning = (text.MORNING_UNREAD if after.morning_above_base is None
               else text.MORNING_AT_BASE if after.morning_above_base <= 0
               else text.MORNING_ABOVE.format(above=_number(after.morning_above_base)))
    if after.next_on is None:
        following = text.NEXT_NONE.format(kind=session_labels.name(item.workout).lower())
    else:
        parts = [text.NEXT_ON.format(kind=session_labels.name(item.workout).lower(),
                                     on=after.next_on)]
        if after.next_rpe is not None:
            parts.append(text.NEXT_RPE.format(rpe=_number(after.next_rpe),
                                              side=text.SIDES[after.next_side]))
        if after.next_pain is not None:
            parts.append(text.NEXT_PAIN.format(pain=_number(after.next_pain)))
        following = ", ".join(parts)
    return text.LINE.format(day=item.day, title=item.title,
                            cuts=", ".join(text.CUTS[cut] for cut in item.cuts),
                            rule=item.rule, fact=item.fact, morning=morning,
                            following=following)


def report(store: Any, rows: Sequence[ledger.Row], week: str) -> list[str]:
    """The cautious decisions of the closed week and the weeks before it, with what
    followed; nothing when the coach decided none."""
    found = [item for offset in range(WEEKS)
             for plan in [store.load_plan(iso_week.shift(week, -offset))] if plan
             for item in restrictions(plan)]
    if not found:
        return []
    zones = [zone.slug for zone in ledger.pain_zones(store)]
    config = expected_rpe.settings()
    history = expected_rpe.history(store, rows, config) if config is not None else None

    def expectation(row: ledger.Row) -> expected_rpe.Expectation | None:
        return history.expectation(row) if history is not None else None

    return [text.HEADER, *(render_line(item, followed(item, rows, zones, expectation))
                           for item in sorted(found, key=lambda item: item.day))]
