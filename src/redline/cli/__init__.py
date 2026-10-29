"""One module per command: `redline week-plan` runs `redline.cli.week_plan`.

The command names (`week-plan`) are what the coach's instructions, the agent's allowlist
and the operator spell; the modules carry the same names in Python form. `redline
<command>` runs a module exactly as `python -m redline.cli.<module>` would.
"""

from __future__ import annotations

import pkgutil
import runpy
import sys

COMMANDS = tuple(sorted(name.replace("_", "-") for _, name, _ in pkgutil.iter_modules(__path__)
                        if not name.startswith("_")))


def module(command: str) -> str:
    """`week-plan` -> `redline.cli.week_plan`."""
    return "redline.cli." + command.replace("-", "_")


def argv(command: str, *args: str) -> list[str]:
    """The arguments that run a command in a new interpreter.

    Commands run inside the vault, which a turn writes: `-P` keeps that working directory
    off `sys.path`, so a `redline/` folder left there can never shadow the engine.
    """
    return [sys.executable, "-P", "-m", module(command), *args]


def main() -> int:
    """The `redline` entry point; the console script exits with what it returns."""
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print("usage: redline <command> [options]\n\ncommands:\n  "
              + "\n  ".join(COMMANDS))
        return 0 if args else 2
    command = args[0]
    if command not in COMMANDS:
        print(f"redline: unknown command {command!r} (redline --help)", file=sys.stderr)
        return 2
    sys.argv = [f"redline {command}", *args[1:]]
    runpy.run_module(module(command), run_name="__main__")
    return 0

