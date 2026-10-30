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
                        help="report drift and write nothing (non-zero exit)")
    args = parser.parse_args()

    try:
        program = load(paths.vault_root())
    except NoteError as exc:
        raise InputError(f"unreadable program:\n{exc}") from exc

    drifted = refresh(paths.vault_root(), program, check=args.check)
    if not drifted:
        print("tables up to date.")
        return
    if args.check:
        raise InputError(f"stale tables in {target(paths.vault_root()).name}: "
                         f"{", ".join(drifted)}\nRegenerate with: redline strength-tables")
    print(f"tables regenerated: {", ".join(drifted)}")


if __name__ == "__main__":
    entrypoint.run(main)
