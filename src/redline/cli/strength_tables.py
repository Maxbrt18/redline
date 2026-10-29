#!/usr/bin/env python3
"""Regenerate the exercise tables of coaching/strength.md from the notes.

`--check` reports a drift and writes nothing, which is what `redline vault-check` calls.

Usage:  redline strength-tables [--check]
"""

import argparse

from redline.model import (
    entrypoint,
    paths,
)
from redline.model.errors import InputError
from redline.model.notes import NoteError
from redline.model.program import load
from redline.model.strength_tables import refresh, target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="signale une dérive et n'écrit rien (sortie non nulle)")
    args = parser.parse_args()

    try:
        program = load(paths.vault_root())
    except NoteError as exc:
        raise InputError(f"Programme illisible :\n{exc}") from exc

    drifted = refresh(paths.vault_root(), program, check=args.check)
    if not drifted:
        print("Tables à jour.")
        return
    if args.check:
        raise InputError(f"Tables périmées dans {target(paths.vault_root()).name} : {', '.join(drifted)}\n"
                 f"Régénère avec : redline strength-tables")
    print(f"Tables régénérées : {', '.join(drifted)}")


if __name__ == "__main__":
    entrypoint.run(main)
