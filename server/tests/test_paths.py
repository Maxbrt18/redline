"""Le moteur et le vault sont deux racines, et un script les distingue.

Le jour où le moteur s'installe hors du vault qu'il sert, une lecture de note qui
partirait encore de `__file__` lirait le dépôt du moteur -- vide de notes -- sans que
rien ne casse bruyamment. Ces tests tiennent la frontière par un vrai lancement.
"""

import os
import subprocess
import sys

from conftest import ENGINE_ROOT

from scripts.model import paths


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
        [sys.executable, str(paths.ENGINE_ROOT / "scripts" / "knowledge-search.py"), "--query", "seuil"],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False)
    assert found.returncode == 0, found.stderr
    assert "concepts/seuil.md" in found.stdout
