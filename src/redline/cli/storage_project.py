#!/usr/bin/env python3
"""Régénère toutes les vues Obsidian depuis PostgreSQL."""

from __future__ import annotations

import argparse
from pathlib import Path

from redline.model import (
    entrypoint,
    paths,
)
from redline.storage import open_store
from redline.storage.projector import project_all


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    project_all(open_store("storage-project"), args.root or paths.vault_root())
    print("Obsidian projections regenerated from PostgreSQL.")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
