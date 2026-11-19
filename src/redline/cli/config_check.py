#!/usr/bin/env python3
"""Checks an instance's `redline.toml` and `rulebook.toml` against this engine.

Operator command only (kept out of `deploy/agent-cli/agent-cli-tools.txt`). The service
refuses to start on a declaration its engine does not accept: a key a new engine
requires, or one an older engine no longer knows. `deploy/release` runs the TARGET
engine's copy of this command on the instance's declarations before it moves the
checkout, so a mismatch stops the release instead of crash-looping the service.

    PYTHONPATH=<target checkout>/src python -P -m redline.cli.config_check --vault <dir>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from redline.model import entrypoint, instance, paths, rulebook
from redline.model.errors import ConfigError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", type=Path,
                        help="folder holding the declarations (default: VAULT_PATH)")
    args = parser.parse_args()
    vault = args.vault.expanduser().resolve() if args.vault else paths.vault_root()
    if not (vault / instance.FILENAME).is_file():
        raise ConfigError(f"{vault / instance.FILENAME} not found")
    book = rulebook.load(vault)
    instance.load(vault)
    print(f"{vault}: {instance.FILENAME} and {book.source.name} accepted by this engine")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
