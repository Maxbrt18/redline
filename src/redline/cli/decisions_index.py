#!/usr/bin/env python3
"""Find decisions in `decisions.md` without reading the whole journal.

A decision is a `## ` section of the file; its predictions live in PostgreSQL
(`redline fact-write prediction`), not in its prose.

Usage:
    redline decisions-index --find douleur
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from redline.model import entrypoint, paths, vault_layout

#: More matches than this and the search is too broad to read.
MAX_MATCHES = 10


def sections(text: str) -> list[str]:
    """Each `## ` section with its heading, in file order."""
    found: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            found.append([line])
        elif found:
            found[-1].append(line)
    return ["\n".join(section).strip() for section in found]


def find(text: str, query: str) -> list[str]:
    needle = query.casefold().strip()
    return [section for section in sections(text) if needle and needle in section.casefold()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=paths.vault_root(), type=Path)
    parser.add_argument("--find", metavar="TEXTE", required=True)
    args = parser.parse_args()

    matches = find((args.root / vault_layout.DECISIONS).read_text(encoding="utf-8"),
                   args.find)
    if not matches:
        print(f"Aucune décision pour {args.find!r}.", file=sys.stderr)
        return 1
    if len(matches) > MAX_MATCHES:
        print(f"{len(matches)} décisions correspondent ; précise la recherche.",
              file=sys.stderr)
        return 2
    print("\n\n---\n\n".join(matches))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
