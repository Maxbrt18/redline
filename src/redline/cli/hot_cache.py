#!/usr/bin/env python3
"""Regenerates the generated blocks of `ai/hot-cache.md` from their owners.

Two blocks, computed from PostgreSQL: Form (volumes, gauge, pain trend) and State
(long-run trajectory, overdue deadlines, capabilities, predictions to settle). The rest
of the hot cache is hand-written: editorial judgement, not derived state, and it must
carry no state that has an owner.

    redline hot-cache           # writes
    redline hot-cache --check   # reports drift, writes nothing (CI)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from redline.analysis import ledger
from redline.model import (
    entrypoint,
    hot_cache,
    paths,
)
from redline.model.errors import InputError
from redline.model.views import ViewDrift


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=paths.vault_root(), type=Path)
    parser.add_argument("--check", action="store_true",
                        help="signale une dérive sans rien écrire")
    args = parser.parse_args()

    store = ledger.open_store()
    rows = ledger.parse_ledger(store)
    instruments = ledger.load_instruments(store)
    try:
        drifted = hot_cache.refresh(args.root, ledger, rows, instruments,
                                    store=store, check=args.check)
    except ViewDrift as exc:
        raise InputError(str(exc)) from exc

    if not drifted:
        print("Blocs Forme et État déjà à jour.")
    elif args.check:
        raise InputError("Blocs du hot-cache dérivés — régénère avec `redline hot-cache`.")
    else:
        print("Blocs Forme et État régénérés.")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
