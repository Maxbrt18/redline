"""What the provider calls consumed, summed over a window."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import sqlalchemy as sa

from ..schema import agent_calls, agent_turns
from ._common import StoreBase


@dataclass(frozen=True)
class CallTally:
    """The calls of one purpose, route and model over a window.

    ``route`` is the route of the turn a call belongs to; None for a triage call, or for a
    turn whose identity row is missing. ``cost_usd`` sums the calls that reported a price.
    """

    purpose: str
    route: str | None
    model: str | None
    calls: int
    turns: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


@dataclass(frozen=True)
class CallWindow:
    """Every tally of a window, with what is needed to project it over a month."""

    since: dt.datetime
    first_call: dt.datetime | None
    athletes: int
    tallies: tuple[CallTally, ...]


class AgentCalls(StoreBase):
    def call_window(self, since: dt.datetime) -> CallWindow:
        joined = agent_calls.outerjoin(agent_turns, agent_turns.c.turn_id == agent_calls.c.turn_id)
        keys = (agent_calls.c.purpose, agent_turns.c.route, agent_calls.c.model)
        tallies = sa.select(
            *keys,
            sa.func.count().label("calls"),
            sa.func.count(sa.distinct(agent_calls.c.turn_id)).label("turns"),
            sa.func.coalesce(sa.func.sum(agent_calls.c.cost_usd), 0).label("cost_usd"),
            sa.func.sum(agent_calls.c.input_tokens).label("input_tokens"),
            sa.func.sum(agent_calls.c.output_tokens).label("output_tokens"),
            sa.func.sum(agent_calls.c.cache_read_tokens).label("cache_read_tokens"),
            sa.func.sum(agent_calls.c.cache_write_tokens).label("cache_write_tokens"),
        ).select_from(joined).where(agent_calls.c.created_at >= since).group_by(*keys)
        span = sa.select(
            sa.func.min(agent_calls.c.created_at),
            sa.func.count(sa.distinct(agent_calls.c.psid)),
        ).where(agent_calls.c.created_at >= since)
        with self.database.engine.connect() as connection:
            rows = connection.execute(tallies).mappings().all()
            first_call, athletes = connection.execute(span).one()
        return CallWindow(since=since, first_call=first_call, athletes=athletes, tallies=tuple(
            CallTally(purpose=row["purpose"], route=row["route"], model=row["model"],
                      calls=row["calls"], turns=row["turns"], cost_usd=float(row["cost_usd"]),
                      input_tokens=int(row["input_tokens"]),
                      output_tokens=int(row["output_tokens"]),
                      cache_read_tokens=int(row["cache_read_tokens"]),
                      cache_write_tokens=int(row["cache_write_tokens"]))
            for row in rows))
