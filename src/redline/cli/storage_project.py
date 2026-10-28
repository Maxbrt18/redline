#!/usr/bin/env python3
"""Régénère toutes les vues Obsidian depuis PostgreSQL."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from redline.model import (
    entrypoint,
    paths,
)
from redline.storage import Database, OperationalStore
from redline.storage.projector import project_all


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=paths.vault_root())
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL obligatoire")
    project_all(OperationalStore(Database(
        args.database_url, application_name="storage-project")), args.root)
    print("Projections Obsidian régénérées depuis PostgreSQL.")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
