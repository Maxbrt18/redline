"""`deploy/release`: preflight first, the previous commit back on any failure.

The script runs against a small engine repository with stand-ins for sudo, systemctl,
curl and the venv: what a privileged command would do is logged, not done.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ENGINE_ROOT

from redline.service.vault import VaultRepo

SCRIPT = ENGINE_ROOT / "deploy" / "release"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("bash", "git", "sha256sum")),
    reason="bash, git and sha256sum are needed")

# A preflight that refuses a rulebook containing the word "refused", as the real
# config-check refuses a key it does not know, and one without "needed" once the engine
# declares that it requires it.
CONFIG_CHECK = """\
import pathlib, sys
vault = pathlib.Path(sys.argv[sys.argv.index("--vault") + 1])
text = (vault / "rulebook.toml").read_text()
if "refused" in text:
    sys.exit("rulebook.toml: field 'refused' unknown")
if (pathlib.Path(__file__).parents[1] / "requires_needed").exists() and "needed" not in text:
    sys.exit("rulebook.toml: field 'needed' missing")
"""

STUBS = {
    # `sudo -u <user> cmd` runs cmd; any other sudo is logged, never run.
    "sudo": 'if [ "$1" = -u ]; then shift 2; exec "$@"; fi\necho "sudo $*" >> "$LOG"\n',
    "systemctl": 'echo "systemctl $*" >> "$LOG"\nexit 1\n',
    "curl": 'case "$*" in *"/health"*) cat "$FAKE_HEALTH";; esac\n',
}


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def site(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    first = _commit(origin, "first", {
        "deploy/agent-cli/redline-agent-cli": "#!/bin/sh\n",
        "deploy/agent-cli/agent-cli-tools.txt": "fact-read\n",
        "deploy/systemd/coach-messenger.service": "[Service]\n",
        "src/redline/__init__.py": "",
        "src/redline/cli/__init__.py": "",
        "src/redline/cli/config_check.py": CONFIG_CHECK,
        "migrations/0001.py": "",
        "requirements.txt": "",
    })
    second = _commit(origin, "second", {"src/redline/feature.py": "VALUE = 2\n"})
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(origin), str(checkout))
    _git(checkout, "checkout", "-q", "--detach", first)

    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, body in STUBS.items():
        (stubs / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")
        (stubs / name).chmod(0o755)
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text(
        '#!/bin/sh\nif [ "$1" = -m ] && [ "$2" = pip ]; then echo "pip" >> "$LOG"; exit 0; fi\n'
        f'exec {sys.executable} "$@"\n', encoding="utf-8")
    (venv / "python").chmod(0o755)
    (venv / "alembic").write_text('#!/bin/sh\necho "alembic $*" >> "$LOG"\n',
                                  encoding="utf-8")
    (venv / "alembic").chmod(0o755)
    vault = tmp_path / "vault"
    vault.mkdir()
    _git(vault, "init", "-q", "-b", "main")
    _commit(vault, "instance", {"redline.toml": "[athlete]\n",
                                "rulebook.toml": "[parameters]\n"})

    class Site:
        pass
    s = Site()
    s.origin, s.checkout, s.vault, s.first, s.second = origin, checkout, vault, first, second
    s.log = tmp_path / "log"
    s.log.touch()
    s.health = tmp_path / "health.json"
    s.env = {
        "PATH": f"{stubs}:{os.environ['PATH']}", "HOME": str(tmp_path),
        "LOG": str(s.log), "FAKE_HEALTH": str(s.health),
        "REDLINE_CHECKOUT": str(checkout), "REDLINE_VENV": str(venv.parent),
        "REDLINE_VAULT": str(vault), "REDLINE_SERVICE_USER": "nobody",
        "REDLINE_READY_ATTEMPTS": "1",
        "GIT_AUTHOR_NAME": "coach", "GIT_AUTHOR_EMAIL": "coach@example.invalid",
        "GIT_COMMITTER_NAME": "coach", "GIT_COMMITTER_EMAIL": "coach@example.invalid",
    }
    s.serves = lambda commit: s.health.write_text(json.dumps({
        "runtime_tree": VaultRepo(str(checkout), "origin", "main",
                                  engine_root=checkout).runtime_tree(commit)}))
    s.run = lambda *args: subprocess.run(["bash", str(SCRIPT), *args], env=s.env,
                                         capture_output=True, text=True, timeout=120)
    s.head = lambda: _git(checkout, "rev-parse", "HEAD")
    s.vault_log = lambda: _git(vault, "log", "--format=%s")
    return s


def test_a_release_moves_the_checkout_and_records_the_way_back(site):
    site.serves(site.second)
    done = site.run(site.second)
    assert done.returncode == 0, done.stderr
    assert site.head() == site.second
    log = site.log.read_text()
    assert "sudo systemctl start coach-messenger" in log and "pip" in log
    assert site.vault_log() == "instance"  # declarations unchanged: no vault commit
    assert "alembic" not in log  # migrations did not change

    site.serves(site.first)
    back = site.run("--rollback")
    assert back.returncode == 0, back.stderr
    assert site.head() == site.first


def test_declarations_the_target_refuses_stop_the_release_before_anything_moves(site):
    (site.vault / "rulebook.toml").write_text("[parameters]\nrefused = 1\n")
    site.serves(site.second)
    done = site.run(site.second)
    assert done.returncode == 1
    assert "field 'refused' unknown" in done.stderr
    assert site.head() == site.first
    assert site.log.read_text() == ""


def test_code_that_does_not_come_up_puts_the_previous_commit_back(site):
    site.health.write_text(json.dumps({"runtime_tree": "000000000000"}))
    done = site.run(site.second)
    assert done.returncode == 1
    assert "putting" in done.stderr
    assert site.head() == site.first


def test_a_migration_without_the_migrator_role_stops_before_anything_moves(site):
    third = _commit(site.origin, "third", {"migrations/0002.py": ""})
    done = site.run(third)
    assert done.returncode == 1
    assert "MIGRATOR_DATABASE_URL" in done.stderr
    assert site.head() == site.first


def _requiring(site, tmp_path):
    third = _commit(site.origin, "third", {"src/redline/requires_needed": ""})
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "rulebook.toml").write_text("[parameters]\nneeded = 1\n", encoding="utf-8")
    return third, staged


def test_declarations_the_target_needs_go_in_with_the_release(site, tmp_path):
    third, staged = _requiring(site, tmp_path)
    assert "field 'needed' missing" in site.run(third).stderr
    assert site.head() == site.first

    site.serves(third)
    done = site.run(third, "--declarations", str(staged))
    assert done.returncode == 0, done.stderr
    assert "needed = 1" in (site.vault / "rulebook.toml").read_text()
    assert site.vault_log().splitlines()[0].startswith("Declare what engine")
    assert (site.vault / "redline.toml").read_text() == "[athlete]\n"


def test_a_failed_release_puts_the_previous_declarations_back(site, tmp_path):
    third, staged = _requiring(site, tmp_path)
    site.health.write_text(json.dumps({"runtime_tree": "000000000000"}))
    done = site.run(third, "--declarations", str(staged))
    assert done.returncode == 1
    assert site.head() == site.first
    assert (site.vault / "rulebook.toml").read_text() == "[parameters]\n"
    assert site.vault_log().splitlines()[0].startswith("Restore the declarations")
