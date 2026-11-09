"""Reorganising a week: the placements of its remaining sessions that the athlete's days
allow, ranked. The same week and the same days always give the same variants.

Only dates change, and a session may be dropped when the days run short. What a session
contains belongs to the coach: a reorganisation never touches it.

Every placement is enumerated, then filtered, then ranked. A week holds a handful of
sessions, so the search is exhaustive and the best variant is never missed; each one
filtered out is filtered by a rule that can be named.

Filters (a variant breaking one is never shown):
  - the sessions stay in their week, nothing goes before today, what is settled stays;
  - per day, at most `runs_per_day` runs and `sessions_per_day` sessions;
  - the athlete's days: closed, short (minutes), easy only, a group to run with;
  - never two hard days in a row, a demanding other sport included;
  - the gaps each strength routine declares (`min_gap_h`);
  - a session held by a dated precondition does not go before its due date;
  - with a pain signal, a demanding session may move later, never earlier;
  - a key session is never dropped.
Preferences (bent, the variant stays, ranked after, and says so):
  - an impact strength routine on the eve or the day of a demanding run;
  - a 3-day load above the plan's own peak by more than the rulebook's margin;
  - the long run off its weekdays, unless the athlete pinned it;
  - a group run left without a session, when it was not required;
  - a session on a day the plan kept for rest.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from redline.model import session_kinds
from redline.model.program import Program
from redline.model.vocabulary import DayKind, GroupPace, SessionPriority
from redline.model.week_days import Request

#: A session fits a group when it runs this close to the group's duration.
GROUP_FIT = 0.2
#: The load window a reorganisation compares with the plan's.
LOAD_WINDOW_DAYS = 3
SHOWN = 3
_WEIGHT = {SessionPriority.OPTIONAL: 0, SessionPriority.SUPPORT: 1, SessionPriority.KEY: 2}


class Remark(StrEnum):
    """A preference a variant bends, said in one line next to it."""

    IMPACT_NEAR_HARD = "impact_near_hard"
    LOAD_ABOVE_PLAN = "load_above_plan"
    LONG_OFF_DAYS = "long_off_days"
    GROUP_UNCARRIED = "group_uncarried"
    REST_DAY_USED = "rest_day_used"


@dataclass(frozen=True)
class Item:
    """A session of the plan as the search sees it."""

    id: str
    title: str
    workout: str
    home: dt.date
    demanding: bool
    priority: SessionPriority
    minutes: int | None
    load: float
    strength: bool
    impact: bool
    gaps: tuple[tuple[str, int], ...]
    held_until: dt.date | None

    @property
    def long(self) -> bool:
        return self.workout == session_kinds.LONG

    @property
    def easy(self) -> bool:
        kind = session_kinds.BY_SLUG.get(self.workout)
        return bool(kind and kind.easy)


@dataclass(frozen=True)
class Rules:
    runs_per_day: int = 1
    sessions_per_day: int = 2
    load_margin_pct: int | None = None
    long_run_weekdays: tuple[int, ...] = ()
    pain_signal: bool = False


@dataclass(frozen=True)
class Note:
    remark: Remark
    session: str | None = None
    date: dt.date | None = None
    value: int | None = None


@dataclass(frozen=True)
class Move:
    session: str
    before: dt.date
    after: dt.date


@dataclass(frozen=True)
class Variant:
    moves: tuple[Move, ...]
    dropped: tuple[str, ...]
    notes: tuple[Note, ...]
    #: The session the group carries, and how long the athlete runs around it.
    carried: str | None
    around_min: int | None
    placement: tuple[tuple[str, dt.date], ...]


@dataclass(frozen=True)
class Outcome:
    variants: tuple[Variant, ...]
    #: Valid variants not shown: they change more, or bend more.
    others: int
    #: Demanding sessions a variant would move earlier, refused because of a pain signal.
    held_back: tuple[str, ...]


def _minutes(session: dict, program: Program | None) -> int | None:
    if session.get("duration_cap_min"):
        return int(session["duration_cap_min"])
    spec = session.get("strength") or {}
    if spec.get("duration_min"):
        return int(spec["duration_min"])
    if program and session.get("routine") in program.sessions:
        return program.sessions[session["routine"]].get("duration_min")
    return None


def _effort(session: dict, program: Program | None,
            targets: dict[tuple[str, str | None], float]) -> float:
    """The session's planned effort on 10: its RPE band, else its routine's target."""
    if session.get("rpe_max") is not None:
        low = session.get("rpe_min", session["rpe_max"])
        return (low + session["rpe_max"]) / 2
    if session.get("routine"):
        target = targets.get((session_kinds.STRENGTH, session["routine"]))
        if target is not None:
            return target
        if program and session["routine"] in program.sessions:
            return float(program.sessions[session["routine"]].get("target_rpe") or 0)
    return targets.get((session["workout"], None), 0.0)


def items(sessions: list[dict], gates: list[dict], program: Program | None,
          targets: dict[tuple[str, str | None], float]) -> list[Item]:
    """The sessions a search works with; a rest day is a day, not a session."""
    held: dict[str, dt.date] = {}
    for gate in gates:
        if gate.get("when") == "before":
            for session_id in gate.get("holds") or ():
                due = dt.date.fromisoformat(gate["due"])
                held[session_id] = max(due, held.get(session_id, due))
    out = []
    for session in sessions:
        if session["workout"] == "rest":
            continue
        minutes = _minutes(session, program)
        drop = tuple((session.get("strength") or {}).get("drop") or ())
        routine = session.get("routine") if program is not None else None
        note = program.sessions.get(routine) if program is not None and routine else None
        priority = session.get("priority") or (
            SessionPriority.KEY if session["demanding"] else SessionPriority.SUPPORT)
        out.append(Item(
            id=session["id"], title=session["title"], workout=session["workout"],
            home=dt.date.fromisoformat(session["date"]), demanding=session["demanding"],
            priority=SessionPriority(priority), minutes=minutes,
            load=(minutes or 0) * _effort(session, program, targets),
            strength=session["kind"] == session_kinds.STRENGTH,
            impact=bool(note and program and routine and program.impact(routine, drop)),
            gaps=tuple(sorted((note.get("min_gap_h") or {}).items())) if note else (),
            held_until=held.get(session["id"])))
    return out


@dataclass
class _Week:
    dates: tuple[dt.date, ...]
    today: dt.date
    request: Request
    rules: Rules
    fixed: list[Item]
    rest_days: frozenset[dt.date]
    no_earlier: bool

    def open_days(self) -> list[dt.date]:
        return [date for date in self.dates if date >= self.today]


def _fits_day(item: Item, date: dt.date, week: _Week, pinned: dt.date | None) -> bool:
    if pinned is not None and date != pinned:
        return False
    day = week.request.day(date)
    if day.closed:
        return False
    if item.held_until and date < item.held_until:
        return False
    if week.no_earlier and item.demanding and date < item.home:
        return False
    if day.kind is DayKind.EASY and item.demanding:
        return False
    return not (day.kind is DayKind.SHORT
                and (item.minutes is None or item.minutes > (day.minutes or 0)))


def _hard_days(placed: dict[str, dt.date], by_id: dict[str, Item], week: _Week) -> list[dt.date]:
    days = {date for item_id, date in placed.items() if by_id[item_id].demanding}
    days |= {item.home for item in week.fixed if item.demanding}
    days |= {date for date, day in week.request.days.items() if day.hard}
    return sorted(days)


def _consecutive(days: list[dt.date]) -> bool:
    return any((b - a).days == 1 for a, b in itertools.pairwise(days))


def _gaps_ok(placed: dict[str, dt.date], by_id: dict[str, Item]) -> bool:
    """The routines' declared gaps, read the way the plan's own check reads them."""
    entries = list(placed.items())
    for (a_id, a_date), (b_id, b_date) in itertools.combinations(entries, 2):
        a, b = by_id[a_id], by_id[b_id]
        hours = abs((a_date - b_date).days) * 24
        for one, other in ((a, b), (b, a)):
            gap = dict(one.gaps).get(other.workout)
            if gap is not None and hours < gap:
                return False
    return True


def _runs(items_: list[Item], week: _Week, pins: dict[str, dt.date]) -> Iterator[dict[str, dt.date]]:
    """Every placement of the runs on the open days, filters applied as it goes."""
    by_id = {item.id: item for item in items_}
    fixed_runs: dict[dt.date, int] = {}
    for item in week.fixed:
        if not item.strength:
            fixed_runs[item.home] = fixed_runs.get(item.home, 0) + 1
    choices = [[date for date in week.open_days() if _fits_day(item, date, week, pins.get(item.id))]
               for item in items_]

    def place(index: int, placed: dict[str, dt.date], count: dict[dt.date, int]):
        if index == len(items_):
            yield dict(placed)
            return
        item = items_[index]
        for date in choices[index]:
            if count.get(date, 0) + fixed_runs.get(date, 0) >= week.rules.runs_per_day:
                continue
            placed[item.id] = date
            if item.demanding and _consecutive(_hard_days(placed, by_id, week)):
                del placed[item.id]
                continue
            count[date] = count.get(date, 0) + 1
            yield from place(index + 1, placed, count)
            count[date] -= 1
            del placed[item.id]

    yield from place(0, {}, {})


def _strength(items_: list[Item], runs: dict[str, dt.date], all_items: dict[str, Item],
              week: _Week, pins: dict[str, dt.date]) -> dict[str, dt.date] | None:
    """The best placement of the strength sessions around these runs, or None."""
    if not items_:
        return {}
    counts: dict[dt.date, int] = {}
    for date in runs.values():
        counts[date] = counts.get(date, 0) + 1
    for item in week.fixed:
        counts[item.home] = counts.get(item.home, 0) + 1
    hard = set(_hard_days(runs, all_items, week))
    choices = [[date for date in week.open_days() if _fits_day(item, date, week, pins.get(item.id))]
               for item in items_]
    best: tuple[tuple, dict[str, dt.date]] | None = None
    for dates in itertools.product(*choices):
        per_day = dict(counts)
        ok = True
        for date in dates:
            per_day[date] = per_day.get(date, 0) + 1
            if per_day[date] > week.rules.sessions_per_day:
                ok = False
        if not ok:
            continue
        placed = {**runs, **{item.id: date for item, date in zip(items_, dates, strict=True)}}
        if not _gaps_ok(placed, all_items):
            continue
        if not _short_days_ok(placed, all_items, week):
            continue
        near_hard = sum(1 for item, date in zip(items_, dates, strict=True)
                        if item.impact and (date in hard or date + dt.timedelta(days=1) in hard))
        moved = sum(date != item.home for item, date in zip(items_, dates, strict=True))
        alone = sum(date not in runs.values() for date in dates)
        cost = (near_hard, moved, alone, tuple(dates))
        if best is None or cost < best[0]:
            best = (cost, {item.id: date for item, date in zip(items_, dates, strict=True)})
    return best[1] if best else None


def _short_days_ok(placed: dict[str, dt.date], by_id: dict[str, Item], week: _Week) -> bool:
    for date, day in week.request.days.items():
        if day.kind is DayKind.SHORT:
            total = sum(by_id[item_id].minutes or 0 for item_id, at in placed.items() if at == date)
            if total > (day.minutes or 0):
                return False
    return True


def _carries(item: Item, pace: GroupPace | None, group_min: int) -> int | None:
    """How long the athlete runs around the group when `item` goes with it, or None when
    it does not fit: an easy pace carries easy runs, a workout carries any run; the
    session runs as long as the group, or a long run wraps around it."""
    if pace is not GroupPace.WORKOUT and not item.easy:
        return None
    if item.minutes is None:
        return None
    if abs(item.minutes - group_min) <= GROUP_FIT * group_min:
        return 0
    if item.long and item.minutes > group_min:
        return item.minutes - group_min
    return None


def _load_peak(placed: dict[str, dt.date], by_id: dict[str, Item], week: _Week) -> float:
    daily = dict.fromkeys(week.dates, 0.0)
    for item_id, date in placed.items():
        daily[date] += by_id[item_id].load
    for item in week.fixed:
        daily[item.home] = daily.get(item.home, 0.0) + item.load
    loads = [daily[date] for date in week.dates]
    return max(sum(loads[start:start + LOAD_WINDOW_DAYS])
               for start in range(len(loads) - LOAD_WINDOW_DAYS + 1))


def _assess(placed: dict[str, dt.date], dropped: tuple[Item, ...], by_id: dict[str, Item],
            week: _Week, pins: dict[str, dt.date], plan_peak: float) -> tuple[tuple, Variant] | None:
    notes: list[Note] = []
    carried, around = None, None
    for date, day in sorted(week.request.days.items()):
        if day.kind is not DayKind.WITH_OTHERS:
            continue
        options = sorted(((fit, item_id) for item_id, at in placed.items() if at == date
                          for fit in [_carries(by_id[item_id], day.pace, day.minutes or 0)]
                          if fit is not None and not by_id[item_id].strength))
        if options:
            around, carried = options[0][0] or None, options[0][1]
        elif day.required:
            return None
        else:
            notes.append(Note(Remark.GROUP_UNCARRIED, date=date))
    hard = set(_hard_days(placed, by_id, week))
    for item_id, date in placed.items():
        item = by_id[item_id]
        if item.impact and (date in hard or date + dt.timedelta(days=1) in hard):
            notes.append(Note(Remark.IMPACT_NEAR_HARD, session=item_id, date=date))
        if (item.long and week.rules.long_run_weekdays and item_id not in pins
                and date.isoweekday() not in week.rules.long_run_weekdays):
            notes.append(Note(Remark.LONG_OFF_DAYS, session=item_id, date=date))
        if date in week.rest_days and date != item.home:
            notes.append(Note(Remark.REST_DAY_USED, session=item_id, date=date))
    if week.rules.load_margin_pct is not None and plan_peak > 0:
        peak = _load_peak(placed, by_id, week)
        excess = round(100 * (peak / plan_peak - 1))
        if excess > week.rules.load_margin_pct:
            notes.append(Note(Remark.LOAD_ABOVE_PLAN, value=excess))
    long_off = sum(note.remark is Remark.LONG_OFF_DAYS for note in notes)
    uncarried = sum(note.remark is Remark.GROUP_UNCARRIED for note in notes)
    bent = len(notes) - long_off - uncarried
    key_moved = sum(date != by_id[i].home for i, date in placed.items()
                    if by_id[i].priority is SessionPriority.KEY)
    key_on_more = sum(week.request.day(date).kind is DayKind.MORE for i, date in placed.items()
                      if by_id[i].priority is SessionPriority.KEY)
    moves = tuple(Move(i, by_id[i].home, date) for i, date in sorted(placed.items(), key=lambda p: (p[1], p[0]))
                  if date != by_id[i].home)
    rank = (len(dropped), tuple(sorted((_WEIGHT[item.priority] for item in dropped), reverse=True)),
            sum(item.load for item in dropped), long_off, uncarried, key_moved, bent, -key_on_more, len(moves),
            tuple(sorted((date, i) for i, date in placed.items())))
    return rank, Variant(moves=moves, dropped=tuple(item.id for item in dropped),
                         notes=tuple(notes), carried=carried, around_min=around,
                         placement=tuple(sorted(placed.items())))


def _signature(variant: Variant, by_id: dict[str, Item]) -> tuple:
    """Two variants that put the same kinds of session on the same days are one."""
    return tuple(sorted((date, by_id[i].workout, by_id[i].title) for i, date in variant.placement))


def _search(items_: list[Item], week: _Week, pins: dict[str, dt.date]) -> list[tuple[tuple, Variant]]:
    by_id = {item.id: item for item in items_}
    plan_peak = _load_peak({item.id: item.home for item in items_}, by_id, week)
    runs = [item for item in items_ if not item.strength]
    strength = [item for item in items_ if item.strength]
    droppable = sorted((item for item in runs if item.priority is not SessionPriority.KEY
                        and item.id not in pins),
                       key=lambda item: (_WEIGHT[item.priority], item.load, item.id))
    for count in range(len(droppable) + 1):
        found: list[tuple[tuple, Variant]] = []
        for dropped in itertools.combinations(droppable, count):
            kept = [item for item in runs if item not in dropped]
            for placed_runs in _runs(kept, week, pins):
                placed_strength = _strength(strength, placed_runs, by_id, week, pins)
                if placed_strength is None:
                    continue
                assessed = _assess({**placed_runs, **placed_strength}, dropped, by_id,
                                   week, pins, plan_peak)
                if assessed:
                    found.append(assessed)
        if found:
            return found
    return []


def reorganise(sessions: list[Item], dates: tuple[dt.date, ...], today: dt.date,
               request: Request, rules: Rules, rest_days: frozenset[dt.date] = frozenset(),
               settled: frozenset[str] = frozenset()) -> Outcome:
    """The variants of the week, best first. `settled` names the sessions that happened
    or were cancelled: they keep their day and count as such."""
    fixed = [item for item in sessions if item.id in settled or item.home < today]
    movable = [item for item in sessions if item not in fixed]
    week = _Week(dates, today, request, rules, fixed, rest_days, rules.pain_signal)
    by_id = {item.id: item for item in sessions}
    found = _search(movable, week, request.pins)
    held_back: tuple[str, ...] = ()
    if rules.pain_signal:
        free = _search(movable, _Week(dates, today, request, rules, fixed, rest_days, False),
                       request.pins)
        if free:
            best = min(free, key=lambda pair: pair[0])[1]
            held_back = tuple(move.session for move in best.moves
                              if by_id[move.session].demanding and move.after < move.before)
    found.sort(key=lambda pair: pair[0])
    shown: list[Variant] = []
    seen: set[tuple] = set()
    distinct = 0
    for _, variant in found:
        signature = _signature(variant, by_id)
        if signature in seen:
            continue
        seen.add(signature)
        distinct += 1
        if len(shown) < SHOWN:
            shown.append(variant)
    return Outcome(variants=tuple(shown), others=distinct - len(shown), held_back=held_back)


def as_dict(outcome: Outcome) -> dict:
    """The outcome as the notebook and the service read it."""
    return {
        "variants": [{
            "moves": [{"session": move.session, "from": move.before.isoformat(),
                       "to": move.after.isoformat()} for move in variant.moves],
            "dropped": list(variant.dropped),
            "notes": [{"remark": note.remark.value, "session": note.session,
                       "date": note.date.isoformat() if note.date else None,
                       "value": note.value} for note in variant.notes],
            "carried": variant.carried, "around_min": variant.around_min,
        } for variant in outcome.variants],
        "others": outcome.others,
        "held_back": list(outcome.held_back),
    }
