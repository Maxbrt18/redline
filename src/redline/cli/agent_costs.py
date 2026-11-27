"""The `redline agent-costs` command: what the model calls cost at API prices over the
last days, by route and model, and what that projects to per athlete over a month."""

from __future__ import annotations

import argparse
import datetime as dt

from redline.analysis import agent_costs
from redline.model import entrypoint
from redline.storage import open_store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30,
                        help="how many days back the window starts (default 30)")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be at least 1")
    now = dt.datetime.now(dt.UTC)
    window = open_store("agent-costs").call_window(now - dt.timedelta(days=args.days))
    print(agent_costs.render(window, now))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
