#!/usr/bin/env python3
"""Repère les fiches pertinentes du corpus pour le coach Messenger."""

# ruff: noqa: E402 -- la CLI directe amorce la racine du dépôt.

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import entrypoint
from scripts.model.knowledge_search import main

if __name__ == "__main__":
    entrypoint.run(main)
