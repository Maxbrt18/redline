"""The engine and the vault are two roots, and a script tells them apart.

When the engine is installed outside the vault it serves, a note read that still started
from `__file__` would read the engine repository -- empty of notes -- without anything
failing loudly. These tests hold the boundary through a real launch.
"""

import os
import subprocess

import pytest
from conftest import ENGINE_ROOT, cli_command

from redline.model import paths
from redline.model.errors import ConfigError


def test_without_configuration_there_is_no_vault(monkeypatch):
    monkeypatch.delenv("VAULT_PATH", raising=False)
    with pytest.raises(ConfigError, match="VAULT_PATH is not set"):
        paths.vault_root()


def test_the_engine_checkout_is_never_a_vault(monkeypatch):
    """Its CLAUDE.md and AGENTS.md are for developers, not the coach."""
    monkeypatch.setenv("VAULT_PATH", str(ENGINE_ROOT))
    with pytest.raises(ConfigError, match="engine checkout"):
        paths.vault_root()


def test_the_service_refuses_to_start_without_a_vault(monkeypatch):
    from redline.service.config import Config
    for key in ("MESSENGER_PAGE_TOKEN", "MESSENGER_VERIFY_TOKEN", "MESSENGER_APP_SECRET",
                "MESSENGER_ALLOWED_PSID"):
        monkeypatch.setenv(key, "x")
    monkeypatch.delenv("VAULT_PATH", raising=False)
    with pytest.raises(SystemExit, match="VAULT_PATH"):
        Config.from_env()


def test_the_agent_wrapper_refuses_to_run_without_a_vault():
    wrapper = (ENGINE_ROOT / "deploy" / "redline-agent-cli").read_text(encoding="utf-8")
    assert "vault=${VAULT_PATH:-}" in wrapper
    assert "VAULT_PATH absent" in wrapper


def test_vault_path_moves_the_vault_and_nothing_else(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    assert paths.vault_root() == tmp_path.resolve()
    assert paths.ENGINE_ROOT == ENGINE_ROOT


def test_a_cli_launched_from_elsewhere_reads_the_configured_vault(tmp_path):
    (tmp_path / "concepts").mkdir()
    (tmp_path / "episodes").mkdir()
    (tmp_path / "concepts" / "seuil.md").write_text(
        "# Seuil lactique\nLe seuil se travaille en côte.\n", encoding="utf-8")
    environment = {**os.environ, "VAULT_PATH": str(tmp_path)}
    found = subprocess.run(
        [*cli_command("knowledge-search"), "--query", "seuil"],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False)
    assert found.returncode == 0, found.stderr
    assert "concepts/seuil.md" in found.stdout


def test_the_state_directory_holds_the_outbox_and_follows_its_variable(monkeypatch, tmp_path):
    monkeypatch.delenv("REDLINE_STATE_DIR", raising=False)
    assert paths.state_dir() == paths.ENGINE_ROOT / "var"
    monkeypatch.setenv("REDLINE_STATE_DIR", str(tmp_path))
    assert paths.outbox_dir() == tmp_path.resolve() / "outbox"


def test_a_card_named_without_a_directory_lands_in_the_outbox(monkeypatch, tmp_path):
    from redline.cards import cli
    monkeypatch.setenv("REDLINE_STATE_DIR", str(tmp_path))
    assert cli.output_path("week-29.png") == str(tmp_path.resolve() / "outbox" / "week-29.png")
    assert cli.output_path("/tmp/x.png") == "/tmp/x.png"
