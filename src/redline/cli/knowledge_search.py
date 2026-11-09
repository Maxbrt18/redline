"""The `redline knowledge-search` command: the corpus notes relevant to a topic."""

from __future__ import annotations

import argparse

from redline.coach.fr import knowledge_search as text
from redline.model import entrypoint
from redline.model.knowledge_search import search


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="topic to search")
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.limit <= 12 or not 3 <= len(args.query.strip()) <= 160:
        parser.error("a query of 3 to 160 characters and a limit of 1 to 12 expected")
    found = search(args.query, args.limit)
    if not found:
        print(text.NOTHING_FOUND)
        return 0
    print(text.SPOTTING_ONLY)
    for row in found:
        print(f"{row['path']}:{row['line']} | score {row['score']} | {row['excerpt']}")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
