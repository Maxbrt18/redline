"""Model profiles and session isolation in ClaudeCodeRunner."""

import json
import subprocess
from dataclasses import replace
from functools import partial

import pytest

from redline.service.agent import RunSpec
from redline.service.claude import ClaudeCodeRunner
from redline.service.prompts import instruction_digest
from redline.service.routing import Route, RoutingConfig


@pytest.fixture
def make_config(make_config):
    """These tests target the Claude adapter: they declare its provider explicitly
        instead of relying on the default of the other tests."""
    return partial(make_config, agent_provider="claude")


# ---- _command: which flags land on the CLI

def test_command_uses_default_model(make_config):
    runner = ClaudeCodeRunner(make_config(), state=None)
    cmd = runner._command("salut", None)
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "sonnet"
    assert "--resume" not in cmd


def test_command_model_override(make_config):
    runner = ClaudeCodeRunner(make_config(), state=None)
    cmd = runner._command("bilan", None, "opus")
    assert cmd[cmd.index("--model") + 1] == "opus"


def test_command_resume_carries_session(make_config):
    runner = ClaudeCodeRunner(make_config(), state=None)
    cmd = runner._command("salut", "sess-42")
    assert cmd[cmd.index("--resume") + 1] == "sess-42"


def test_command_can_fork_a_resumed_session(make_config):
    runner = ClaudeCodeRunner(make_config(), state=None)
    cmd = runner._command("creuse", "sess-42", "opus", "high", fork_session=True)
    assert cmd[cmd.index("--resume") + 1] == "sess-42"
    assert "--fork-session" in cmd


def test_command_no_model_flag_when_unset(make_config):
    base = RoutingConfig.default("claude")
    plans = dict(base.plans)
    n1 = base.plan(Route.N1)
    plans[Route.N1] = replace(n1, primary=replace(n1.primary, model=None))
    runner = ClaudeCodeRunner(
        make_config(routing=RoutingConfig("claude", plans)), state=None)
    cmd = runner._command("salut", None)
    assert "--model" not in cmd


def test_command_carries_effort(make_config):
    runner = ClaudeCodeRunner(make_config(), state=None)
    cmd = runner._command("bilan", None, "claude-opus-4-8", "medium")
    assert cmd[cmd.index("--effort") + 1] == "medium"


def test_every_turn_carries_the_style_prompt(make_config):
    """The wiring, not the text. `STYLE_PROMPT` is the only text injected on EVERY
        turn: a behaviour rule that is not in it is not applied, it is merely archived in
        the vault. Nothing else tested this `--append-system-prompt`: the line could be
        deleted from claude.py with the whole suite staying green while the mandate to
        contradict disappeared from Messenger.
        The two pinned excerpts are the pair that must never be split: the right to
        contradict, and the firmness backed by READING. The second alone would be lukewarm;
        the first alone builds an obstructive and wrong coach, the failure mode the rest of
        the architecture fights.

    """
    runner = ClaudeCodeRunner(make_config(), state=None)
    cmd = runner._command("je fais un fractionné ce soir", None)
    assert "--append-system-prompt" in cmd
    injected = cmd[cmd.index("--append-system-prompt") + 1]
    assert "droit de contredire" in injected
    assert "Ne présente jamais une croyance comme un fait lu" in injected


def test_attachment_turn_has_read_tools_but_no_write_or_bypass(make_config):
    runner = ClaudeCodeRunner(make_config(), state=None)
    cmd = runner._command("analyse", "sess-42", read_only=True)
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--tools") + 1] == "Read,Glob,Grep"
    assert "--resume" in cmd


def test_normal_turn_allows_vault_edits_and_owned_clis_without_permission_bypass(make_config):
    runner = ClaudeCodeRunner(make_config(), state=None)
    cmd = runner._command("bilan", None)
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert "Edit" in allowed and "Write" in allowed
    assert "Bash(/usr/local/bin/redline-agent-cli *)" in allowed
    assert "Bash(python3 scripts/*)" not in allowed
    assert "Bash(scripts/*)" not in allowed
    assert "Bash(git *)" not in allowed


# ---- run(): isolation of the interactive session

def _fake_stream(session_id: str):
    return [
        json.dumps({"type": "system", "session_id": session_id}),
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "text", "text": "ok"}]}}),
        json.dumps({"type": "result", "result": "ok", "is_error": False}),
    ]


class _FakeProc:
    def __init__(self, stdout_lines):
        self.stdout = iter(stdout_lines)
        self.stderr = iter(())
        self.returncode = 0

    def wait(self):
        return 0

    def kill(self):  # pragma: no cover - timeout path not exercised here
        pass

    def terminate(self):  # pragma: no cover - /cancel path not exercised here
        pass


def _patch_popen(monkeypatch, stdout_lines):
    """Replace subprocess.Popen; return a list that will hold the launched command."""
    captured = []

    def fake_popen(command, **kwargs):
        captured.append(command)
        return _FakeProc(stdout_lines)

    monkeypatch.setattr("redline.service.claude.subprocess.Popen", fake_popen)
    return captured


def _spec(runner, route=Route.N1, **overrides) -> RunSpec:
    return replace(runner._config.routing.plan(route).primary, **overrides)


def test_isolated_run_leaves_interactive_session_untouched(make_config, store, monkeypatch):
    store.set_session("4242", "claude", "interactive-sess", instruction_digest("."))
    captured = _patch_popen(monkeypatch, _fake_stream("heavy-sess"))
    runner = ClaudeCodeRunner(make_config(), store)

    result = runner.run(
        "4242", "bilan hebdo", spec=_spec(
            runner, Route.HEAVY, model="opus"))

    # The heavy run reported its own session, but the persisted interactive one
    # must be exactly what it was before: no overwrite, no clear.
    assert store.session_id("4242", "claude") == "interactive-sess"
    assert result.full_text == "ok"
    cmd = captured[0]
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert "--resume" not in cmd  # fresh session, not resumed


def test_forked_run_keeps_context_without_replacing_interactive_session(
        make_config, store, monkeypatch):
    store.set_session("4242", "claude", "interactive-sess", instruction_digest("."))
    captured = _patch_popen(monkeypatch, _fake_stream("deep-fork"))
    runner = ClaudeCodeRunner(make_config(), store)

    result = runner.run("4242", "creuse", spec=_spec(
        runner, Route.DEEP, model="opus"))

    assert result.session_id == "deep-fork"
    assert store.session_id("4242", "claude") == "interactive-sess"
    cmd = captured[0]
    assert cmd[cmd.index("--resume") + 1] == "interactive-sess"
    assert "--fork-session" in cmd
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_shared_run_resumes_and_persists_session(make_config, store, monkeypatch):
    store.set_session("4242", "claude", "interactive-sess", instruction_digest("."))
    captured = _patch_popen(monkeypatch, _fake_stream("interactive-sess"))
    runner = ClaudeCodeRunner(make_config(), store)

    runner.run("4242", "coucou")

    cmd = captured[0]
    assert cmd[cmd.index("--resume") + 1] == "interactive-sess"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "low"
    assert store.session_id("4242", "claude") == "interactive-sess"


def test_writable_claude_process_receives_the_turn_id(make_config, store, monkeypatch):
    environments = []

    def fake_popen(_command, **kwargs):
        environments.append(kwargs["env"])
        return _FakeProc(_fake_stream("session"))

    monkeypatch.setattr("redline.service.claude.subprocess.Popen", fake_popen)
    runner = ClaudeCodeRunner(make_config(), store)

    runner.run("4242", "salut", turn_id="turn-44")

    assert environments[0]["REDLINE_TURN_ID"] == "turn-44"


def test_final_response_has_no_execution_or_write_tools(make_config):
    runner = ClaudeCodeRunner(make_config(), state=None)
    cmd = runner._command("rédige après application", "sess-42", read_only=True,
                          fork_session=True, shell_enabled=False)
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--allowedTools" not in cmd
    assert "--fork-session" in cmd


# ---- one_shot: the evaluation bench runs on Claude

def _completed(stdout, returncode=0):
    import subprocess
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_one_shot_runs_without_tools_session_or_vault_constitution(make_config, monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(command=command, cwd=kwargs["cwd"])
        return _completed(json.dumps({"result": "Réponse.", "is_error": False}))

    monkeypatch.setattr("redline.service.claude.subprocess.run", fake_run)
    config = make_config()
    runner = ClaudeCodeRunner(config, state=None)
    answer = runner.one_shot("Message", spec=RunSpec("sonnet", "high", 60),
                             instructions="Consignes du banc")
    cmd = seen["command"]
    assert answer == "Réponse."
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--system-prompt") + 1] == "Consignes du banc"
    assert "--resume" not in cmd and "--append-system-prompt" not in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    # An empty directory: the vault CLAUDE.md is not loaded.
    assert seen["cwd"] != str(config.vault_path)


def test_one_shot_returns_the_structured_output_when_a_schema_is_given(make_config,
                                                                        monkeypatch):
    verdict = {"approved": True, "reason": "none", "detail": ""}
    monkeypatch.setattr("redline.service.claude.subprocess.run", lambda command, **kwargs: _completed(
        json.dumps({"result": "", "is_error": False, "structured_output": verdict})))
    raw = ClaudeCodeRunner(make_config(), state=None).one_shot(
        "Juge", spec=RunSpec("haiku", "medium", 60), output_schema={"type": "object"})
    assert json.loads(raw) == verdict


@pytest.mark.parametrize("stdout, code", [
    (json.dumps({"result": "quota", "is_error": True}), 0),
    ("", 1),
    (json.dumps({"result": "texte libre", "is_error": False}), 0),
])
def test_one_shot_raises_instead_of_returning_an_empty_answer(make_config, monkeypatch,
                                                              stdout, code):
    monkeypatch.setattr("redline.service.claude.subprocess.run",
                        lambda command, **kwargs: _completed(stdout, code))
    with pytest.raises(RuntimeError):
        ClaudeCodeRunner(make_config(), state=None).one_shot(
            "Juge", spec=RunSpec("haiku", "medium", 60), output_schema={"type": "object"})


def test_the_bench_follows_the_production_provider(make_config):
    from redline.evaluation.runner import bench_runner, check_model

    config = make_config()
    assert isinstance(bench_runner(config), ClaudeCodeRunner)
    check_model(config, config.routing.plan(Route.N2).primary)
    with pytest.raises(ValueError, match="not allowed"):
        check_model(config, RunSpec("gpt-6-sol", "high", 60))


# ---- confinement: no settings file, no MCP server, file tools inside the vault

def _confined(command):
    return "--restricted" in command and "--strict-mcp-config" in command


@pytest.mark.parametrize("options", [
    {}, {"read_only": True}, {"shell_enabled": False}, {"structured": True}])
def test_every_turn_is_confined(make_config, options):
    """A turn that wrote `.claude/settings.json` in the vault would otherwise have its
    hooks run by the next call."""
    command = ClaudeCodeRunner(make_config(), state=None)._command("bilan", None, **options)
    assert _confined(command)


def test_an_attachment_turn_reads_only_the_attachment_folder_outside_the_vault(make_config):
    config = make_config()
    command = ClaudeCodeRunner(config, state=None)._command("photo", None, read_only=True)
    assert command[command.index("--add-dir") + 1] == config.attachment_dir
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep"


def test_triage_and_the_bench_are_confined_too(make_config, monkeypatch):
    seen = []

    def run(command, **_):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(
            {"result": "N1 | - | - | - | - | none", "structured_output": {}}), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    runner = ClaudeCodeRunner(make_config(), state=None)
    runner.classify("salut")
    runner.one_shot("ok", spec=RunSpec(model="sonnet", effort="low", timeout=10))
    assert len(seen) == 2 and all(_confined(command) for command in seen)
