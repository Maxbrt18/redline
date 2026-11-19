"""`redline config-check`: the release preflight on an instance's declarations."""

import shutil
import subprocess
from pathlib import Path

from conftest import ENGINE_ROOT

from redline import cli

FIXTURE = Path(__file__).parent / "fixtures" / "vault"


def _declarations(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    for name in ("redline.toml", "rulebook.toml"):
        shutil.copy(FIXTURE / name, vault / name)
    return vault


def _check(vault: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cli.argv("config-check", "--vault", str(vault)), cwd=ENGINE_ROOT,
                          capture_output=True, text=True, timeout=60)


def test_the_fictional_instance_is_accepted(tmp_path):
    done = _check(_declarations(tmp_path))
    assert done.returncode == 0, done.stderr
    assert "accepted by this engine" in done.stdout


def test_a_key_this_engine_does_not_know_stops_the_release(tmp_path):
    vault = _declarations(tmp_path)
    rulebook = vault / "rulebook.toml"
    rulebook.write_text(rulebook.read_text(encoding="utf-8").replace(
        "[parameters]\n", "[parameters]\nkey_from_a_later_engine = 1\n", 1), encoding="utf-8")
    done = _check(vault)
    assert done.returncode == 1
    assert "key_from_a_later_engine" in done.stderr


def test_an_instance_without_its_declarations_is_refused(tmp_path):
    done = _check(tmp_path)
    assert done.returncode == 1
    assert "redline.toml not found" in done.stderr
