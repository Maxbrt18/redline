"""A turn hands its JSON to a CLI through `--input`, in a file of its own.

The bare form `--input <path>` no longer depends on the parsing of shell redirections
(`... < /tmp/x.json` can be refused by the agent's permission layer); the per-turn
file name prevents reading the JSON of another turn.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest
from conftest import ENGINE_ROOT, cli_command

from redline.model import agent_input
from redline.service import persona
from redline.service.agent import RunResult
from redline.service.claude import ClaudeCodeRunner
from redline.service.prompts import STYLE_PROMPT
from redline.service.routing import Route, RoutingConfig
from redline.service.turns import TurnExecutor

TURN = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"


def _write(root, name: str, payload: object = None) -> str:
    path = root / agent_input.DIRECTORY / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload if payload is not None else {"ok": 1}))
    return f"{agent_input.DIRECTORY}/{name}"


def test_a_turn_reads_its_own_file_and_its_suffixed_second(tmp_path):
    for name in (f"{TURN}.json", f"{TURN}-carte.json"):
        relative = _write(tmp_path, name)
        assert agent_input.resolve(relative, root=tmp_path, turn_id=TURN) == (
            tmp_path / relative).resolve()


@pytest.mark.parametrize("path", [
    f"{agent_input.DIRECTORY}/{OTHER}.json",   # le JSON d'un autre tour
    "/tmp/x.json",                              # l'ancienne forme
    f"{agent_input.DIRECTORY}/../{TURN}.json",  # hors du dossier
    f"{agent_input.DIRECTORY}/{TURN}-.json",
    f"{agent_input.DIRECTORY}/{TURN}.txt",
])
def test_a_turn_cannot_hand_over_any_other_file(tmp_path, path):
    with pytest.raises(agent_input.AgentInputError, match="ce tour ne lit que"):
        agent_input.resolve(path, root=tmp_path, turn_id=TURN)


def test_outside_a_turn_the_operator_passes_any_path(tmp_path):
    target = tmp_path / "revision.json"
    assert agent_input.resolve(str(target), root=tmp_path, turn_id="") == target.resolve()


def test_a_missing_file_says_to_write_it_first(monkeypatch):
    monkeypatch.setenv("REDLINE_TURN_ID", TURN)
    with pytest.raises(agent_input.AgentInputError, match="écris-le avec Write"):
        agent_input.read_json(agent_input.turn_path(TURN))


def test_clear_removes_only_the_files_of_that_turn(tmp_path):
    _write(tmp_path, f"{TURN}.json")
    _write(tmp_path, f"{TURN}-carte.json")
    _write(tmp_path, f"{OTHER}.json")
    agent_input.clear(TURN, root=tmp_path)
    assert sorted(p.name for p in (tmp_path / agent_input.DIRECTORY).iterdir()) == [
        f"{OTHER}.json"]


def test_the_turn_directory_is_ignored_by_git():
    """The service runs `git add -A`: without this, every JSON would end up in a commit."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", f"{agent_input.DIRECTORY}/{TURN}.json"],
        cwd=ENGINE_ROOT, check=False)
    assert result.returncode == 0


def test_week_plan_reads_the_revision_from_input():
    """`revise` and `preview` declare `--input`: the prompt dictates it to them."""
    result = subprocess.run(
        [*cli_command("week-plan"), "--help"], cwd=ENGINE_ROOT,
        capture_output=True, text=True, check=True)
    assert "--input" in result.stdout


def test_a_resumed_claude_turn_names_its_own_file_in_its_message(make_config):
    """The agent freezes the system prompt at the first turn of the session and replays
        it on every `--resume`. A per-turn path placed there would still designate the
        first turn, and `week-plan revise` would refuse the file. The path therefore
        travels in the message; the system prompt no longer depends on the turn."""
    runner = ClaudeCodeRunner(make_config(agent_provider="claude"), state=None)
    first = runner._command("bilan", "sess-42", turn_id=OTHER)
    command = runner._command("Fais le changement", "sess-42", turn_id=TURN)
    assert "--resume" in command
    system = command[command.index("--append-system-prompt") + 1]
    assert system == first[first.index("--append-system-prompt") + 1]
    assert system == persona.render(STYLE_PROMPT, "Sam")
    message = command[command.index("-p") + 1]
    assert f"`--input {agent_input.turn_path(TURN)}`" in message
    assert OTHER not in message
    assert message.endswith("Fais le changement")


def test_a_refused_path_says_how_to_succeed_with_write(tmp_path):
    """A refusal, a forbidden `cp`, an authorized revision abandoned."""
    with pytest.raises(agent_input.AgentInputError,
                       match=f"Réécris le même JSON avec Write dans "
                             f"{agent_input.DIRECTORY}/{TURN}.json"):
        agent_input.resolve(f"{agent_input.DIRECTORY}/{OTHER}.json",
                            root=tmp_path, turn_id=TURN)


def test_every_turn_clears_its_files_whatever_the_outcome():
    agent = MagicMock(provider="claude")
    agent.run.return_value = RunResult(full_text="ok")
    repo = MagicMock()
    repo.head.return_value = "base"

    outcome = TurnExecutor(agent, repo, MagicMock()).execute(
        "4242", "bilan", plan=RoutingConfig.default("claude").plan(Route.HEAVY))

    assert agent.run.call_args.args[1] == "bilan"
    repo.clear_turn_inputs.assert_called_once_with(outcome.turn_id)
