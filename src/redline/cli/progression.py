"""The `redline progression` command: what the progression engine proposes for strength.

For each lineage declared in `[progression]` of `rulebook.toml`: the move (up, hold,
down), the clause that decided it, the verdicts and the facts behind them, the readings
still missing and the ceiling, the weekly rise budget of its group applied. Read-only:
it writes no dose and refuses nothing.

    redline progression                    # for the current week
    redline progression --week 2030-W41    # for the week being planned
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from pathlib import Path

from redline.analysis import progression_strength
from redline.model import entrypoint, paths
from redline.model.errors import ConfigError


def _week(value: str) -> str:
    if not re.fullmatch(r"\d{4}-W(0[1-9]|[1-4]\d|5[0-3])", value):
        raise argparse.ArgumentTypeError(f"week expected as YYYY-WNN, got {value!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=None, type=Path, help="vault root (default: VAULT_PATH)")
    parser.add_argument("--week", type=_week, default=None, metavar="YYYY-WNN",
                        help="week the proposals are for (default: the current week)")
    parser.add_argument("--date", type=dt.date.fromisoformat, default=None,
                        help="reference day YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise ConfigError("DATABASE_URL is required: strength reports live in PostgreSQL")
    from redline.storage import Database, OperationalStore
    store = OperationalStore(Database(url, application_name="progression"))
    root = args.root or paths.vault_root()
    print("\n".join(progression_strength.report(store, root, args.date or dt.date.today(),
                                                args.week)))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
