"""What the model calls cost over a window, and what that projects to over a month.

The figures are API prices as the provider reports them: what the service would pay
without a subscription. The projection divides by the days the window actually covers,
so a register younger than the window is not diluted by days it never saw.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from redline.storage.repository.agent_calls import CallTally, CallWindow

MONTH_DAYS = 30


@dataclass(frozen=True)
class Projection:
    days_covered: float
    cost_usd: float
    per_day_usd: float
    per_month_usd: float
    per_athlete_month_usd: float | None
    cache_read_share: float | None


def covered_days(window: CallWindow, now: dt.datetime) -> float:
    """Days between the window's start, or its first call when later, and now; at least one."""
    start = max(window.since, window.first_call) if window.first_call else window.since
    return max((now - start).total_seconds() / 86400, 1.0)


def project(window: CallWindow, now: dt.datetime) -> Projection:
    days = covered_days(window, now)
    cost = sum(tally.cost_usd for tally in window.tallies)
    per_day = cost / days
    per_month = per_day * MONTH_DAYS
    read = sum(tally.cache_read_tokens for tally in window.tallies)
    prompt = read + sum(tally.input_tokens + tally.cache_write_tokens for tally in window.tallies)
    return Projection(
        days_covered=days, cost_usd=cost, per_day_usd=per_day, per_month_usd=per_month,
        per_athlete_month_usd=per_month / window.athletes if window.athletes else None,
        cache_read_share=read / prompt if prompt else None)


def _label(tally: CallTally) -> str:
    return tally.route or tally.purpose


def render(window: CallWindow, now: dt.datetime) -> str:
    if not window.tallies:
        return f"No model call recorded since {window.since:%Y-%m-%d}."
    projection = project(window, now)
    header = ("route", "model", "calls", "turns", "cost USD", "input", "output",
              "cache read", "cache write")
    rows = [(_label(tally), tally.model or "-", str(tally.calls), str(tally.turns),
             f"{tally.cost_usd:.2f}", f"{tally.input_tokens:,}", f"{tally.output_tokens:,}",
             f"{tally.cache_read_tokens:,}", f"{tally.cache_write_tokens:,}")
            for tally in sorted(window.tallies, key=lambda tally: -tally.cost_usd)]
    widths = [max(len(row[index]) for row in (header, *rows)) for index in range(len(header))]

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(width) if index < 2 else cell.rjust(width)
                         for index, (cell, width) in enumerate(zip(cells, widths, strict=True)))

    lines = [f"Model calls since {window.since:%Y-%m-%d}, "
             f"{projection.days_covered:.1f} days with data (API prices)", "",
             line(header), *map(line, rows), "",
             f"total {projection.cost_usd:.2f} USD, {projection.per_day_usd:.2f} USD a day, "
             f"{projection.per_month_usd:.2f} USD per {MONTH_DAYS} days"]
    if projection.per_athlete_month_usd is not None:
        lines.append(f"{window.athletes} athlete(s): {projection.per_athlete_month_usd:.2f} USD "
                     f"per athlete per {MONTH_DAYS} days")
    if projection.cache_read_share is not None:
        lines.append(f"cache reads: {projection.cache_read_share:.0%} of prompt tokens")
    return "\n".join(lines)
