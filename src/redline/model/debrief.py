"""Computed gaps of an outing: what the watch does not tell the athlete.

Useful debriefs rest on computed comparisons (planned vs done, HR drift at comparable
pace), not on figures recited from the watch. The service computes these gaps before the
turn and attaches them to the snapshot, so the coach does not rebuild them.

Pure functions: the ledger row, the planned session and the HR drift go in, short
numeric phrases come out. Nothing is written.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from redline.coach.fr import debrief as text
from redline.model.vocabulary import HrDoubt

from . import rulebook, session_kinds
from .activity import Lap


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
    # HR recorded without the armband is never compared to a cap.
    hr = row.hr_avg if getattr(row, "hr_doubt", None) != HrDoubt.NO_SENSOR else None
    if hr is not None and session.get("hr_cap_bpm") is not None:
        out.append(text.HR_GAP.format(hr=hr, hr_cap_bpm=session["hr_cap_bpm"], signed=text.signed(hr - session["hr_cap_bpm"])))
    if row.rpe is not None and session.get("rpe_max") is not None:
        low = session.get("rpe_min")
        span = f"{text.number(low)}-{text.number(session["rpe_max"])}" if low is not None else \
            text.AT_MOST.format(high=text.number(session["rpe_max"]))
        out.append(text.RPE_GAP.format(rpe=text.number(row.rpe), span=span))
    return out


def gaps(row: Any, session: dict | None, drift: Any | None) -> list[str]:
    """The gaps of an outing, in the order they are used: planned/done, then HR."""
    out = planned_vs_done(row, session)
    if drift is not None:
        out.append(text.HR_AT_PACE + str(drift))
    return out


# ------------------------------------------------------------------ repetitions

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
