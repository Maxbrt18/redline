"""Le moteur et le vault sont deux racines, et un script les distingue.

Le jour où le moteur s'installe hors du vault qu'il sert, une lecture de note qui
partirait encore de `__file__` lirait le dépôt du moteur -- vide de notes -- sans que
rien ne casse bruyamment. Ces tests tiennent la frontière par un vrai lancement.
"""

import os
import subprocess

from conftest import ENGINE_ROOT, cli_command

from redline.model import paths


def test_without_configuration_the_vault_is_the_engine_itself(monkeypatch):
    monkeypatch.delenv("VAULT_PATH", raising=False)
    assert paths.vault_root() == paths.ENGINE_ROOT == ENGINE_ROOT


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
