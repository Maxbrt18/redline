"""Every command named in a prompt must exist and accept what it is told.

A prompt that cites a wrong command does not break at deployment: it breaks later,
mid-conversation. A debrief trigger that pipes into `redline descent` is outside the
single authorised launcher, so it is refused and disguised as an access outage. A test
scenario calling `fact-write daily --day` when the option is `--date` is the same
kind of fault.

Both are invisible to unit tests: prompts are strings, nobody executes them. Here they
are read as what they are -- ORDERS -- and each is checked to be executable: the script
exists, its `--help` passes, and the cited subcommands and options really appear in it.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess

import pytest
from conftest import ENGINE_ROOT, cli_command

from redline.service import prompts

# Commands live in backtick `spans`. The prose is not parsed: only what the prompt
# itself presents as a command is read.
SPAN = re.compile(r"`([^`]+)`")
SCRIPT = re.compile(
    r"/usr/local/bin/redline-agent-cli\s+([a-z][a-z-]*)((?:\s+[a-z][a-z-]*)*)")


def _prompts() -> dict[str, str]:
    """All the texts injected into the coach, gathered under a readable name."""
    texts = {
        "STYLE_PROMPT": prompts.STYLE_PROMPT,
        "TRIAGE_RULES": prompts.TRIAGE_RULES,
        "EMOTION_NOTE": prompts.EMOTION_NOTE,
        "STRENGTH_CARD_POINTER": prompts.STRENGTH_CARD_POINTER,
        "STRENGTH_REPORT_NOTE": prompts.STRENGTH_REPORT_NOTE,
        "stale_plan_note": prompts.stale_plan_note("2026-W33"),
        # These cite commands, so they enter here like the others.
        "canonical_plan_note": prompts.canonical_plan_note(""),
        "active_revision_note": prompts.active_revision_note(""),
        "missing_cells_note": prompts.missing_cells_note(
            [{"date": "2026-08-18", "type": "easy", "missing": ["rpe", "dminus"]}],
            [{"date": "2026-08-12", "type": "easy", "missing": ["enjoyment"]}]),
    }
    texts.update({f"DEPTH_NOTES[{k}]": v for k, v in prompts.DEPTH_NOTES.items()})
    texts.update({f"PROACTIVE_TRIGGERS[{k}]": v.replace("{detail}", "42")
                   for k, v in prompts.PROACTIVE_TRIGGERS.items()})
    return texts


def _commands() -> list[tuple[str, str, str, tuple[str, ...]]]:
    """(origin, script, subcommands, options) for each cited command.

    Splitting on pipes matters: without it, the `--dplus` of `descent` would be
    attributed to `strava` in a debrief pipe, and the test would blame the wrong
    script for ignoring an option it never had.
    """
    found = []
    for origin, text_out in _prompts().items():
        flat = " ".join(text_out.split())
        for span in SPAN.findall(flat):
            for segment in span.split("|"):
                found_match = SCRIPT.search(segment)
                if not found_match:
                    continue
                script = found_match.group(1)
                suite = found_match.group(2)
                options = tuple(sorted(set(re.findall(r"(--[a-z][a-z-]+)", segment))))
                found.append((origin, script, suite.strip(), options))
    return found


COMMANDS = _commands()


def test_the_prompts_do_cite_commands():
    """Guard of the guard: if the extraction breaks, the whole file would pass green
    while checking nothing."""
    assert len(COMMANDS) >= 10
    scripts = {script for _, script, _, _ in COMMANDS}
    assert "strava" in scripts
    assert "week-plan" in scripts


@pytest.mark.parametrize("origin,script,suite,options", COMMANDS,
                         ids=[f"{o}:{s}" for o, s, _, _ in COMMANDS])
def test_every_cited_command_is_executable(origin, script, suite, options):
    cited_command = cli_command(script.removeprefix("scripts/").removesuffix(".py"))
    assert importlib.util.find_spec(cited_command[-1]), f"{origin} cites {script}, which does not exist"

    aide = subprocess.run([*cited_command, "--help"], cwd=ENGINE_ROOT,
                          capture_output=True, text=True, check=False)
    assert aide.returncode == 0, (
        f"{origin} cites {script}, whose --help fails: {aide.stderr[:200]}")

    help_text = aide.stdout + aide.stderr
    for subcommand in suite.split():
        # `show/check` and the like are written out in full in the prompts; a
        # subcommand absent from the help is a command the coach cannot run.
        assert subcommand in help_text, (
            f"{origin} cites `{script} {subcommand}`, missing from its help")

    for option in options:
        detailed = subprocess.run(
            [*cited_command, *suite.split(), "--help"], cwd=ENGINE_ROOT,
            capture_output=True, text=True, check=False)
        aide_complete = help_text + detailed.stdout + detailed.stderr
        assert option in aide_complete, (
            f"{origin} cites `{option}` for {script}, which does not know it")


def test_every_command_goes_through_the_allowed_launcher():
    """The prompt and ``dontAsk`` must describe exactly the same path.

    If the doctrine says ``redline ledger-add`` while Bash only allows
    ``redline-agent-cli``, the refusal gets reported as a save outage. Every mention of a
    business CLI is therefore normalised, not only the examples that carry options.
    """
    mistakes = []
    for origin, text_out in _prompts().items():
        flat = " ".join(text_out.split())
        for span in SPAN.findall(flat):
            for segment in span.split("|"):
                if re.match(r"(?:python3? )?redline [a-z]", segment.strip()) \
                        or (re.search(r"\bscripts/|\b[a-z-]+\.py\b", segment) \
                        and "coaching/" not in segment):
                    mistakes.append(f"{origin} : `{span[:90]}`")
    assert not mistakes, "commandes hors lanceur :\n  " + "\n  ".join(mistakes)


def test_every_cited_command_is_in_the_launcher_policy():
    allowed_commands = {
        line for line in (ENGINE_ROOT / "deploy" / "agent-cli" / "agent-cli-tools.txt").read_text().splitlines()
        if line and not line.startswith("#")
    }
    cited = {script.removeprefix("scripts/") for _, script, _, _ in COMMANDS}
    assert cited <= allowed_commands
