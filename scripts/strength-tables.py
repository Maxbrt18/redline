#!/usr/bin/env python3
"""Regenerate the exercise tables of coaching/strength.md from the notes.

`--check` reports a drift and writes nothing, which is what `vault-check.py` calls.

Usage:  python3 scripts/strength-tables.py [--check]
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import (  # noqa: E402
    entrypoint,
    paths,
)
from scripts.model.errors import InputError  # noqa: E402
from scripts.model.notes import NoteError  # noqa: E402
from scripts.model.programme import load  # noqa: E402
from scripts.model.strength_tables import refresh, target  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="signale une dérive et n'écrit rien (sortie non nulle)")
    args = parser.parse_args()

    try:
        programme = load(paths.vault_root())
    except NoteError as exc:
        raise InputError(f"Programme illisible :\n{exc}") from exc

    drifted = refresh(paths.vault_root(), programme, check=args.check)
    if not drifted:
        print("Tables à jour.")
        return
    if args.check:
        raise InputError(f"Tables périmées dans {target(paths.vault_root()).name} : {', '.join(drifted)}\n"
                 f"Régénère avec : python3 scripts/strength-tables.py")
    print(f"Tables régénérées : {', '.join(drifted)}")


if __name__ == "__main__":
    entrypoint.run(main)
