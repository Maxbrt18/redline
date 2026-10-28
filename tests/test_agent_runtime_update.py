"""Tests purs des garde-fous du metteur à jour du runtime d'agent (Codex ou Claude)."""

import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import DEPLOY_DIR, ENGINE_ROOT

SERVER = DEPLOY_DIR
SCRIPT = SERVER / "agent-runtime-update.py"
SPEC = importlib.util.spec_from_file_location("agent_runtime_update", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
updater = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = updater  # requis par @dataclass
SPEC.loader.exec_module(updater)


def test_requirement_bounds_are_read_from_the_runtime_manifest():
    assert updater._manifest_bounds(
        "requests==2.32.4\nopenai-codex>=0.156.1,<1.0.0\n", "openai-codex"
    ) == ("0.156.1", "1.0.0")


@pytest.mark.parametrize(
    "requirements",
    [
        "openai-codex==0.156.1\n",
        "openai-codex>=0.156.1\n",
        "openai-codex>=1.0.0,<0.9.0\n",
        "openai-codex>=0.156.1,<1.0.0\nopenai-codex>=0.156.1,<1.0.0\n",
        "openai-codex>=0.156.1,<1.0.0\nopenai-codex==0.157.0\n",
    ],
)
def test_requirement_bounds_fail_closed_for_unknown_policies(requirements):
    with pytest.raises(ValueError):
        updater._manifest_bounds(requirements, "openai-codex")


def test_both_tracked_manifests_hold_a_single_valid_range():
    """Les deux runtimes lisent leur borne dans le dépôt, avec la même règle."""
    requirements = (ENGINE_ROOT / "requirements.txt").read_text(encoding="utf-8")
    claude = (SERVER / "claude-runtime.txt").read_text(encoding="utf-8")
    assert updater._manifest_bounds(requirements, "openai-codex")
    assert updater._manifest_bounds(claude, updater.CLAUDE_PACKAGE)


def test_only_stable_numeric_versions_are_considered():
    versions = updater._available_versions(
        "openai-codex (0.157.0)\n"
        "Available versions: 0.157.0, 0.156.1, 0.157.0rc1, 1.0.0, 0.9\n"
    )
    assert versions == ["0.157.0", "0.156.1", "1.0.0", "0.9"]


def test_version_selection_respects_the_manifest_range():
    assert updater._latest_allowed(
        ["0.156.1", "0.157.0", "1.0.0"],
        "0.156.1",
        "1.0.0",
    ) == "0.157.0"


def test_version_selection_refuses_an_empty_range():
    with pytest.raises(ValueError):
        updater._latest_allowed(["0.156.1"], "0.160.0", "1.0.0")


def test_codex_candidate_installs_runtime_and_validation_dependencies():
    command = updater.CodexRuntime.pip_install_command("/tmp/candidate/bin/python")
    assert command[-4:] == [
        "-r",
        str(updater.REQUIREMENTS),
        "-r",
        str(updater.DEV_REQUIREMENTS),
    ]


def test_codex_candidate_installs_the_engine_from_the_checkout():
    command = updater.CodexRuntime.package_install_command("/tmp/candidate/bin/python")
    assert command[-3:] == ["--no-deps", "-e", str(updater.REPO)]


def test_unexpected_pip_index_output_is_refused():
    with pytest.raises(ValueError):
        updater._available_versions("ERROR: connection unavailable\n")


def test_claude_follows_the_stable_channel_never_latest():
    tags = '{"stable": "2.1.285", "latest": "2.1.293", "next": "2.1.294"}'
    assert updater._channel_version(tags, updater.CLAUDE_CHANNEL) == "2.1.285"


@pytest.mark.parametrize("tags", ["", "[]", '{"latest": "2.1.293"}', '{"stable": "2.1.x"}'])
def test_an_unreadable_claude_channel_fails_closed(tags):
    with pytest.raises(ValueError):
        updater._channel_version(tags, "stable")


def test_claude_cli_version_is_parsed_strictly():
    assert updater._cli_version("2.1.285 (Claude Code)\n") == "2.1.285"
    with pytest.raises(ValueError):
        updater._cli_version("command not found")


def test_claude_candidate_installs_the_exact_version_in_its_own_prefix():
    command = updater.ClaudeRuntime.npm_install_command(Path("/r/claude-code-2.1.285"), "2.1.285")
    assert command[:4] == ["npm", "install", "--prefix", "/r/claude-code-2.1.285"]
    assert command[-1] == "@anthropic-ai/claude-code@2.1.285"
    assert updater.CLAUDE_CANDIDATE.fullmatch("claude-code-2.1.285")


def test_the_service_path_serves_the_managed_claude_before_the_global_one():
    """Le lien géré précède /usr/bin : sans lui, le Claude global reste le recours."""
    unit = (SERVER / "systemd" / "coach-messenger.service").read_text(encoding="utf-8")
    path = next(line.split("=", 2)[2] for line in unit.splitlines()
                if line.startswith("Environment=PATH="))
    entries = path.split(":")
    managed = str(updater.CLAUDE_LINK / updater.CLAUDE_BIN.parent)
    assert entries.index(managed) < entries.index("/usr/bin")
    assert "Environment=DISABLE_AUTOUPDATER=1" in unit


def test_runtime_link_switch_is_atomic_and_points_to_the_candidate(tmp_path):
    link = tmp_path / "redline-current"
    previous = tmp_path / "redline-3.12"
    candidate = tmp_path / "redline-codex-0.157.0"
    previous.mkdir()
    candidate.mkdir()
    link.symlink_to(previous)

    updater._atomic_link(link, candidate)

    assert link.is_symlink()
    assert link.resolve() == candidate.resolve()


def test_a_link_outside_its_runtime_directory_is_refused(tmp_path):
    root = tmp_path / "runtimes"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "current").symlink_to(elsewhere)
    with pytest.raises(RuntimeError):
        updater._link_target(root / "current")


def test_pruning_keeps_the_active_runtime_and_its_predecessor(tmp_path):
    for name in ("claude-code-2.1.197", "claude-code-2.1.280", "claude-code-2.1.285", "other"):
        (tmp_path / name).mkdir()
    keep = {tmp_path / "claude-code-2.1.280", tmp_path / "claude-code-2.1.285"}
    updater._prune_candidates(tmp_path, updater.CLAUDE_CANDIDATE, keep)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "claude-code-2.1.280", "claude-code-2.1.285", "other"]


def test_preflight_runs_with_the_service_identity_and_environment(tmp_path):
    """Le prévol Claude doit voir le jeton du service sans qu'il passe par argv."""
    unit = tmp_path / "coach-messenger.service"
    unit.write_text(
        "[Service]\nType=simple\nUser=ubuntu\nWorkingDirectory=/srv/repo\n"
        "EnvironmentFile=/etc/redline/runtime/coach-messenger.env\n"
        "Environment=TZ=Europe/Paris\nExecStart=/bin/false\n",
        encoding="utf-8",
    )
    command = updater._service_run_command(["/bin/smoke"], updater._service_settings(unit))
    assert command[0] == "/usr/bin/systemd-run" and "--wait" in command and "--pipe" in command
    assert "User=ubuntu" in command
    assert "EnvironmentFile=/etc/redline/runtime/coach-messenger.env" in command
    assert "ExecStart=/bin/false" not in command and "Type=simple" not in command
    assert command[-1] == "/bin/smoke"


def _unit(tmp_path, env_text, *, environment=""):
    env = tmp_path / "coach.env"
    env.write_text(env_text, encoding="utf-8")
    unit = tmp_path / "coach-messenger.service"
    unit.write_text("[Service]\n" + environment + f"EnvironmentFile={env}\n", encoding="utf-8")
    return unit


def test_the_nightly_check_follows_the_provider_the_coach_really_uses(tmp_path):
    """Sous Claude, prévoler Codex mesurait un compte inutilisé (2026-10-06)."""
    assert updater._agent_provider(_unit(tmp_path, "AGENT_PROVIDER=claude\n")) == "claude"
    assert updater._agent_provider(_unit(tmp_path, "AGENT_PROVIDER='Codex'\n")) == "codex"
    assert updater._agent_provider(_unit(tmp_path, "OTHER=1\n")) == "codex"
    assert updater._agent_provider(_unit(
        tmp_path, "AGENT_PROVIDER=claude\n",
        environment="Environment=AGENT_PROVIDER=codex\n")) == "claude"


def test_every_provider_has_exactly_one_runtime():
    assert set(updater.RUNTIMES) == {"codex", "claude"}


def test_a_missing_required_environment_file_fails_closed(tmp_path):
    unit = tmp_path / "coach-messenger.service"
    unit.write_text(f"EnvironmentFile={tmp_path / 'absent.env'}\n", encoding="utf-8")
    with pytest.raises(OSError):
        updater._agent_provider(unit)
    unit.write_text(f"EnvironmentFile=-{tmp_path / 'absent.env'}\n", encoding="utf-8")
    assert updater._agent_provider(unit) == "codex"
