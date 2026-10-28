#!/usr/bin/env python3
"""Régénère les blocs générés de `ai/hot-cache.md` depuis leurs propriétaires.

Deux blocs, calculés depuis PostgreSQL : Forme (volumes, jauge, tendance douleur) et
État (trajectoire de la sortie longue, échéances échues, capacités, prédictions à
solder). Le reste du hot-cache est écrit à la main : c'est du jugement éditorial, pas de
l'état dérivé, et il ne doit porter aucun état qui a un propriétaire.

    python3 scripts/hot-cache.py           # écrit
    python3 scripts/hot-cache.py --check   # signale une dérive, n'écrit rien (CI)
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
        raise InputError("Blocs du hot-cache dérivés — régénère avec `python3 scripts/hot-cache.py`.")
    else:
        print("Blocs Forme et État régénérés.")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
