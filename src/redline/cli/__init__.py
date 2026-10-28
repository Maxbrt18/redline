"""One module per command: `redline-agent-cli week-plan.py` runs `redline.cli.week_plan`.

The command names (`week-plan.py`) are what the coach's instructions and the agent's
allowlist spell; the modules carry the same names in Python form.
"""


def module(command: str) -> str:
    """`week-plan.py` -> `redline.cli.week_plan`."""
    return "redline.cli." + command.removesuffix(".py").replace("-", "_")
