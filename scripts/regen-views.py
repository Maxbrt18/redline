#!/usr/bin/env python3
"""Régénère TOUTES les vues générées du vault, en un seul appel.

Le point d'entrée existe parce que la liste des générateurs avait deux copies (le
hook `pre-commit` et rien d'autre) et qu'un troisième appelant — le bot du VPS —
en aurait fait une troisième. Une liste de générateurs recopiée est exactement la
dérive que `CLAUDE.md` interdit aux faits : elle vit ici, les appelants la lisent.

    python3 scripts/regen-views.py           # écrit
    python3 scripts/regen-views.py --check   # signale une dérive, n'écrit rien
    python3 scripts/regen-views.py --check --static-only  # CI, sans PostgreSQL

Chaque générateur reste responsable de son propre message d'erreur : on les enchaîne
tous — même après un échec — et on rend 1 dès que l'un d'eux a échoué.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# La racine du MOTEUR : les générateurs vivent ici. Le vault qu'ils régénèrent est
# `VAULT_PATH` (scripts/model/paths.py), transmis tel quel aux sous-processus.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import (  # noqa: E402
    entrypoint,
    paths,
)

GENERATOR_TIMEOUT = 120

# Tout script qui écrit un bloc entre marqueurs a sa place ici. Un générateur absent
# de cette liste est un générateur que personne ne relance — c'était le cas de
# `decisions-index.py` du 23 au 29/07 : il annonçait un `--check (CI)` que ni la CI,
# ni le hook, ni ce script n'appelaient. L'index n'était à jour que par chance.
OPERATIONAL_GENERATORS = ("hot-cache.py",)
STATIC_GENERATORS = ("strength-tables.py", "decisions-index.py")
GENERATORS = OPERATIONAL_GENERATORS + STATIC_GENERATORS


def run(root: Path | None = None, check: bool = False, *, static_only: bool = False) -> int:
    """Enchaîne les générateurs. Retourne le nombre de ceux qui ont échoué.

    On ne s'arrête pas au premier échec : si le registre et le programme sont tous
    les deux cassés, autant les voir tous les deux d'un coup.
    """
    vault = root or paths.vault_root()
    environ = {**os.environ, "VAULT_PATH": str(vault)}
    failed = 0
    generators = STATIC_GENERATORS if static_only else GENERATORS
    for name in generators:
        command = [sys.executable, str(ROOT / "scripts" / name)]
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
