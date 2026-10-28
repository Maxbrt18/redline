#!/usr/bin/env python3
"""Regenerates ALL generated views of the vault in a single call.

The entry point exists so the list of generators has one owner: a copied list is the
drift that `CLAUDE.md` forbids for facts. It lives here, callers read it.

    redline regen-views           # writes
    redline regen-views --check   # reports drift, writes nothing
    redline regen-views --check --static-only  # CI, without PostgreSQL

Each generator stays responsible for its own error message: they are all chained --
even after a failure -- and the result is 1 as soon as one of them failed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from redline import cli

# The ENGINE root: the generators live here. The vault they regenerate is `VAULT_PATH`
# (src/redline/model/paths.py), passed unchanged to the subprocesses.
from redline.model import (
    entrypoint,
    paths,
)

GENERATOR_TIMEOUT = 120

# Every script that writes a block between markers belongs here: a generator missing
# from this list is one nobody reruns.
OPERATIONAL_GENERATORS = ("hot-cache",)
STATIC_GENERATORS = ("strength-tables",)
GENERATORS = OPERATIONAL_GENERATORS + STATIC_GENERATORS


def run(root: Path | None = None, check: bool = False, *, static_only: bool = False) -> int:
    """Chains the generators. Returns how many of them failed.

    It does not stop at the first failure: if the ledger and the plan are both broken,
    both are reported at once.
    """
    vault = root or paths.vault_root()
    environ = {**os.environ, "VAULT_PATH": str(vault)}
    failed = 0
    generators = STATIC_GENERATORS if static_only else GENERATORS
    for name in generators:
        command = [sys.executable, "-m", cli.module(name)]
        if check:
            command.append("--check")
        try:
            returncode = subprocess.run(
                command, cwd=vault, env=environ, timeout=GENERATOR_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            returncode = 124
        if returncode != 0:
            failed += 1
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, type=Path)
    parser.add_argument("--check", action="store_true",
                        help="signale une dérive sans rien écrire")
    parser.add_argument("--static-only", action="store_true",
                        help="ignore les vues qui nécessitent PostgreSQL")
    args = parser.parse_args()
    return 1 if run(args.root, check=args.check, static_only=args.static_only) else 0


if __name__ == "__main__":
    entrypoint.run(main)
