"""Computed gaps of an outing: what the watch does not tell the athlete.

Useful debriefs rest on computed comparisons (planned vs done, HR drift at comparable
pace), not on figures recited from the watch. The service computes these gaps before the
turn and attaches them to the snapshot, so the coach does not rebuild them.

Pure functions: the ledger row, the planned session and the HR drift go in, short
numeric phrases come out. Nothing is written.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from redline.coach.fr import debrief as text
from redline.model.vocabulary import HrDoubt

from . import prescription, rulebook, session_kinds, terrain
from .activity import Lap
from .notebook_views import KmSplit


def planned_vs_done(row: Any, session: dict | None) -> list[str]:
    """Each typed target of the session set against what was done."""
    if not session:
        return []
    out = []
    if row.km is not None:
        for key, label in (("distance_target_km", text.TARGET), ("distance_cap_km", text.CAP)):
            if session.get(key) is not None:
                out.append(text.DISTANCE_GAP.format(done=text.number(row.km), label=label, planned=text.number(session[key]), signed=text.signed(row.km - session[key])))
                break
    # A duration cap is compared only if it is the one the athlete read: the rendering
    # (`weekly_plan.distance_of`) omits it as soon as a distance is committed.
    shown_by_distance = (session.get("distance_cap_km") is not None
                         or session.get("distance_target_km") is not None)
    if (row.hours is not None and session.get("duration_cap_min") is not None
            and not shown_by_distance):
        minutes = row.hours * 60
        out.append(text.DURATION_GAP.format(minutes=minutes, duration_cap_min=session["duration_cap_min"], signed=text.signed(round(minutes - session["duration_cap_min"]))))
    # HR recorded without the armband is never compared to a cap. Nor is the session's
    # mean over relief or prescribed efforts: the climbs and the efforts raise it whatever
    # the athlete chose, and the flat kilometres are judged on their own.
    hr = row.hr_avg if getattr(row, "hr_doubt", None) != HrDoubt.NO_SENSOR else None
    if (hr is not None and session.get("hr_cap_bpm") is not None
            and not prescription.has_efforts(session) and _flat_run(row)):
        out.append(text.HR_GAP.format(hr=hr, hr_cap_bpm=session["hr_cap_bpm"], signed=text.signed(hr - session["hr_cap_bpm"])))
    return out


def _flat_run(row: Any) -> bool:
    """A run with no relief to speak of, by its gain and loss per kilometre."""
    km = getattr(row, "km", None)
    if not km:
        return True
    return terrain.of(km * 1000, getattr(row, "dplus", 0) or 0,
                      getattr(row, "dminus", 0) or 0) is terrain.Terrain.FLAT


def gaps(row: Any, session: dict | None, drift: Any | None,
         splits: Sequence[KmSplit] = ()) -> list[str]:
    """The gaps of an outing, in the order they are used: planned/done, HR, kilometres."""
    out = planned_vs_done(row, session)
    if drift is not None:
        out.append(text.HR_AT_PACE + str(drift))
    workout = session.get("workout") if session else getattr(row, "type", None)
    if workout in session_kinds.EASY_RUNS:
        trusted = getattr(row, "hr_doubt", None) != HrDoubt.NO_SENSOR
        cap = session.get("hr_cap_bpm") if session else None
        line = relief(splits)
        if line:
            out.append(line)
        out.extend(render(gap) for gap in split_gaps(splits, cap if trusted else None,
                                                    hr_trusted=trusted))
    return out


class SplitGapKind(StrEnum):
    HR_ABOVE_CAP = "hr_above_cap"
    FAST_START = "fast_start"
    FADE = "fade"
    DECOUPLING = "decoupling"


@dataclass(frozen=True)
class SplitGap:
    """What the kilometres of an easy or long run show against its own rules.

    `value` is what was done and `reference` what it is compared with: bpm for
    `HR_ABOVE_CAP` (peak and cap), s/km for `FAST_START` and `FADE`, the speed-to-HR
    ratio drop in % for `DECOUPLING` (reference: the threshold). `kms` names the
    kilometres concerned.
    """

    kind: SplitGapKind
    value: float
    reference: float
    kms: tuple[int, ...] = ()


def _pace(rows: Sequence[KmSplit]) -> float | None:
    metres = sum(row["dist_m"] for row in rows)
    return sum(row["moving_s"] for row in rows) * 1000 / metres if metres else None


def _efficiency(rows: Sequence[KmSplit]) -> float | None:
    """Metres per heartbeat over the rows: speed over moving-time-weighted HR."""
    seconds = sum(row["moving_s"] for row in rows)
    if not seconds or any(row["hr"] is None for row in rows):
        return None
    hr = sum((row["hr"] or 0) * row["moving_s"] for row in rows) / seconds
    return sum(row["dist_m"] for row in rows) / seconds / hr if hr else None


def _percent(value: float, reference: float) -> float:
    return (value - reference) / reference * 100


def _terrain(row: KmSplit) -> terrain.Terrain:
    return terrain.of(row["dist_m"], row["dplus"], row["dminus"])


def relief(splits: Sequence[KmSplit]) -> str | None:
    """The kilometres the gaps leave out because the relief drives them, or None."""
    by_kind: dict[terrain.Terrain, list[int]] = {}
    for row in splits:
        by_kind.setdefault(_terrain(row), []).append(row["n"])
    flat = len(by_kind.pop(terrain.Terrain.FLAT, []))
    if not by_kind:
        return None
    parts = ", ".join(text.RELIEF_KMS[kind].format(kms=text.kilometres(tuple(numbers)))
                      for kind, numbers in sorted(by_kind.items(), key=lambda item: item[1][0]))
    return (text.RELIEF.format(parts=parts, count=flat) if flat
            else text.RELIEF_NO_FLAT.format(parts=parts))


def split_gaps(splits: Sequence[KmSplit], hr_cap: int | None, *,
               hr_trusted: bool = True) -> list[SplitGap]:
    """The kilometres of an easy or long run read against the rulebook's thresholds.

    Only the flat kilometres are read (`redline.model.terrain`): a climb raises the HR and
    slows the pace whatever the effort, a descent speeds it up. A threshold absent from
    the rulebook reads no gap of its kind. HR gaps need a cap (above cap) or a trusted HR
    (decoupling); start, fade and decoupling need `split_min_km` flat kilometres, and a
    fast start a flat first one.
    """
    book = rulebook.current().parameters
    rows = [row for row in splits if _terrain(row) is terrain.Terrain.FLAT]
    out: list[SplitGap] = []
    margin = book.split_hr_margin_bpm
    if hr_cap is not None and margin is not None:
        above = [row for row in rows if row["hr"] is not None and row["hr"] > hr_cap + margin]
        if above:
            peak = max(above, key=lambda row: row["hr"] or 0)
            out.append(SplitGap(SplitGapKind.HR_ABOVE_CAP, float(peak["hr"] or 0),
                                float(hr_cap), tuple(row["n"] for row in above)))
    if book.split_min_km is None or len(rows) < book.split_min_km:
        return out
    if book.split_fast_start_pct is not None and rows[0]["n"] == 1:
        first, median = rows[0]["pace_s"], statistics.median(row["pace_s"] for row in rows[1:])
        if median and -_percent(first, median) >= book.split_fast_start_pct:
            out.append(SplitGap(SplitGapKind.FAST_START, float(first), float(median), (1,)))
    third = max(1, len(rows) // 3)
    before, last = _pace(rows[:-third]), _pace(rows[-third:])
    if (book.split_fade_pct is not None and before and last
            and _percent(last, before) >= book.split_fade_pct):
        out.append(SplitGap(SplitGapKind.FADE, last, before,
                            tuple(row["n"] for row in rows[-third:])))
    half = len(rows) // 2
    if hr_trusted and book.split_decoupling_pct is not None:
        early, late = _efficiency(rows[:half]), _efficiency(rows[half:])
        if early and late and -_percent(late, early) >= book.split_decoupling_pct:
            out.append(SplitGap(SplitGapKind.DECOUPLING, -_percent(late, early),
                                book.split_decoupling_pct))
    return out


def render(gap: SplitGap) -> str:
    """The gap as the coach reads it in `ecarts`."""
    if gap.kind is SplitGapKind.HR_ABOVE_CAP:
        return text.SPLIT_HR_ABOVE_CAP.format(cap=round(gap.reference), count=len(gap.kms),
                                              kms=text.kilometres(gap.kms),
                                              peak=round(gap.value))
    if gap.kind is SplitGapKind.FAST_START:
        return text.SPLIT_FAST_START.format(
            first=text.pace(gap.value), median=text.pace(gap.reference),
            signed=text.signed(round(_percent(gap.value, gap.reference))))
    if gap.kind is SplitGapKind.FADE:
        return text.SPLIT_FADE.format(
            kms=text.kilometres(gap.kms), last=text.pace(gap.value), before=text.pace(gap.reference),
            signed=text.signed(round(_percent(gap.value, gap.reference))))
    return text.SPLIT_DECOUPLING.format(value=text.number(round(gap.value, 1)))


#: Sessions whose debrief carries the repetitions card. A long run or a race is demanding
#: without being fractioned: its laps are kilometres, not reps.
QUALITY_WORKOUTS = session_kinds.REPETITIONS
#: Minimal pace ratio between the slowest fast lap and the fastest slow lap. Below it, the
#: laps are automatic laps of a steady pace, not an effort/recovery alternation.
MIN_SPLIT_RATIO = 1.12
#: A shorter lap is a double button press, not a phase of the session.
MIN_LAP_S = 10


def is_quality(session: dict | None) -> bool:
    if not session:
        return False
    return (session.get("workout") in QUALITY_WORKOUTS
            # A transition the rulebook marks `quality` counts too, whatever its workout.
            or session.get("transition") in rulebook.current().quality_kinds)


def _phases(laps: Sequence[Lap]) -> list[dict]:
    """The readable laps, in order: duration, distance, HR, pace (s/km)."""
    out = []
    for index, lap in enumerate(laps):
        time_s = lap.moving_s
        if not time_s or time_s < MIN_LAP_S:
            continue
        dist_m = float(lap.distance_m)
        out.append({"time_s": time_s, "dist_m": dist_m, "hr": lap.hr_avg_bpm,
                    "pace": time_s / (dist_m / 1000) if dist_m > 0 else float("inf"),
                    "index": index})
    return out


def _merge(phases: list[dict]) -> dict:
    time_s = sum(phase["time_s"] for phase in phases)
    merged = {"time_s": float(time_s),
              "dist_m": float(round(sum(phase["dist_m"] for phase in phases)))}
    if all(phase["hr"] for phase in phases):
        merged["hr"] = float(round(sum(phase["hr"] * phase["time_s"] for phase in phases)
                                   / time_s))
    return merged


def lap_blocks(laps: Sequence[Lap]) -> list[tuple[bool, list[dict]]] | None:
    """The readable laps grouped into effort (`True`) and recovery blocks.

    Model-free: laps are sorted by pace and cut at the largest jump; the fast side is the
    effort. Contiguous fast laps form ONE block (automatic laps during a repetition). Each
    lap keeps its rank (`index`) in the list received. `None` when the laps do not show a
    clear alternation of at least two efforts.
    """
    phases = _phases(laps)
    if len(phases) < 3:
        return None
    # A stopped recovery (zero distance) is slow by nature: it does not set the cut.
    paces = sorted(phase["pace"] for phase in phases if phase["pace"] != float("inf"))
    ratio, cut = max(((paces[i + 1] / paces[i], i) for i in range(len(paces) - 1)
                      if paces[i] > 0), default=(0.0, -1))
    if ratio < MIN_SPLIT_RATIO:
        return None
    threshold = paces[cut]
    blocks: list[tuple[bool, list[dict]]] = []
    for phase in phases:
        fast = phase["pace"] <= threshold
        if blocks and blocks[-1][0] == fast:
            blocks[-1][1].append(phase)
        else:
            blocks.append((fast, [phase]))
    if sum(1 for fast, _ in blocks if fast) < 2:
        return None
    return blocks


def workout_reps(laps: Sequence[Lap]) -> list[dict] | None:
    """The repetitions read from the Strava laps, each with the recovery that follows it.

    The slow laps between two repetitions form its recovery; warm-up and cool-down stay
    off the card (`lap_blocks` says how blocks are read).
    """
    blocks = lap_blocks(laps)
    if blocks is None:
        return None
    efforts = [index for index, (fast, _) in enumerate(blocks) if fast]
    reps = []
    for number, index in enumerate(efforts):
        rep = _merge(blocks[index][1])
        if rep["dist_m"] < 1:
            return None
        rep["label"] = f"R{number + 1}"
        if number + 1 < len(efforts):
            recovery = _merge(blocks[index + 1][1])
            rep["recovery"] = {"time_s": recovery["time_s"], "dist_m": recovery["dist_m"]}
        reps.append(rep)
    return reps


def quality_card(laps: Sequence[Lap], session: dict | None,
                 date: str = "") -> dict | None:
    """The `redline chart-workout` payload for a quality session, else None."""
    if session is None or not is_quality(session):
        return None
    reps = workout_reps(laps)
    if reps is None:
        return None
    card: dict[str, Any] = {"title": session.get("title") or text.SESSION, "reps": reps}
    if date:
        card["date"] = date
    return card
