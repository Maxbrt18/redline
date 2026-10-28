#!/usr/bin/env python3
"""Repère les fiches pertinentes du corpus ; la fiche reste à lire avant de la citer."""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path
from typing import TypedDict

from scripts.model import entrypoint, paths

STOP = frozenset({"avec", "dans", "pour", "plus", "moins", "comment", "quelle",
                  "quelles", "quel", "quels", "leurs", "notre", "cette", "sont",
                  "avoir", "faire", "entre", "chez", "depuis", "apres", "avant"})


class SearchHit(TypedDict):
    path: str
    line: int
    score: int
    excerpt: str
    heading: str


def words(value: str) -> set[str]:
    ascii_text = unicodedata.normalize("NFKD", value.lower())
    ascii_text = "".join(char for char in ascii_text if not unicodedata.combining(char))
    return {word for word in re.findall(r"[a-z0-9]{3,}", ascii_text) if word not in STOP}


def search(query: str, limit: int = 6, *, root: Path | None = None) -> list[SearchHit]:
    root = root or paths.vault_root()
    terms = words(query)
    if not terms:
        return []
    results: list[SearchHit] = []
    for folder in ("concepts", "episodes"):
        for path in (root / folder).glob("*.md"):
            lines = path.read_text(encoding="utf-8").splitlines()
            heading_lines = [line for line in lines[:20]
                             if line.startswith(("# ", "aliases:", "title:"))]
            title = " ".join(heading_lines) + " " + path.stem
            heading = " ".join(heading_lines)
            heading_matches = len(terms & words(title))
            matches = [(number, line) for number, line in enumerate(lines, 1)
                       if terms & words(line)]
            if not matches:
                continue
            coverage = len(set().union(*(terms & words(line) for _, line in matches)))
            best_passage = max(len(terms & words(line)) for _, line in matches)
            # Une longue fiche qui disperse les mots sur 500 lignes ne doit pas
            # battre une fiche qui les rapproche dans le même passage.
            best_lines = [line for _, line in matches
                          if len(terms & words(line)) == best_passage]
            # Un paragraphe très long peut contenir tous les mots par hasard.
            # La densité départage ces faux positifs sans évincer la fiche longue.
            density_penalty = min(len(words(line)) // 8 for line in best_lines)
            score = best_passage * 16 + coverage * 3 + heading_matches * 8
            score -= min(density_penalty, 12)
            # Une fiche de concept ouvre la lecture ; les épisodes en donnent
            # la preuve détaillée quand la synthèse laisse une incertitude.
            score += 4 if folder == "concepts" else 0
            line_number, excerpt = max(
                matches, key=lambda item: (len(terms & words(item[1])),
                                           -len(words(item[1])), item[0] <= 20, -item[0]))
            results.append({"path": str(path.relative_to(root)), "line": line_number,
                            "score": score, "excerpt": excerpt[:280],
                            "heading": heading[:180]})
    return sorted(results, key=lambda row: (-row["score"], row["path"]))[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="sujet à rechercher")
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.limit <= 12 or not 3 <= len(args.query.strip()) <= 160:
        parser.error("recherche de 3 à 160 caractères et limite 1 à 12 attendues")
    found = search(args.query, args.limit)
    if not found:
        print("Aucune fiche trouvée ; essaie d'autres termes. Cela ne prouve aucune absence de source.")
        return 0
    print("Repérage seulement : ouvre et lis la fiche avant de citer un énoncé ou un chiffre.")
    for row in found:
        print(f"{row['path']}:{row['line']} | score {row['score']} | {row['excerpt']}")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
