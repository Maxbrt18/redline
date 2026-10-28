"""What the agent may see in its environment.

Both SDKs launch the agent with a copy of our environment. Everything systemd loads from its
environment file would therefore land in the sandbox, and then on disk (shell snapshots), in
clear text and world-readable: the page token, the app secret, DATABASE_URL, the HMAC secret of
the strength links and the OAuth token.

The agent never needs these values: it reads the vault and calls scripts. It neither posts the
messages nor signs the links.
"""

import os
import re
import subprocess
from pathlib import Path

from conftest import ENGINE_ROOT, OPERATOR_TOOLS

from redline.service.config import SANDBOX_NEEDS, sandbox_overrides


def test_the_messenger_credentials_never_reach_the_agent():
    """With the page token, one can write to the athlete under the coach's identity."""
    hidden = sandbox_overrides({
        "MESSENGER_PAGE_TOKEN": "EAAG-secret",
        "MESSENGER_APP_SECRET": "abcdef",
        "MESSENGER_VERIFY_TOKEN": "coach-2026",
    })
    assert hidden == {"MESSENGER_PAGE_TOKEN": "", "MESSENGER_APP_SECRET": "",
                      "MESSENGER_VERIFY_TOKEN": ""}


def test_a_variable_nobody_thought_about_is_hidden_by_default():
    """The rule is closed by default: a variable added tomorrow, which nobody thought of,
    is masked on its name alone. A hand-kept blacklist would let through exactly the one
    that gets forgotten."""
    for name in ("UN_NOUVEAU_TOKEN", "FOO_SECRET", "DB_PASSWORD", "X_API_KEY",
                 "SOME_CREDENTIAL", "MESSENGER_ALLOWED_PSID"):
        assert sandbox_overrides({name: "valeur"}) == {name: ""}, name


def test_what_the_sandbox_genuinely_needs_still_gets_through():
    """Masking DATABASE_URL would cut PostgreSQL off, and masking the Strava credentials
    would blind the coach: the fix must not reintroduce the outage."""
    environ = {
        "DATABASE_URL": "postgresql+psycopg://u:p@/redline",
        "STRAVA_CLIENT_ID": "42",
        "STRAVA_CLIENT_SECRET": "s3cr3t",
        "REDLINE_TURN_ID": "uuid",
        "CHROMIUM_PATH": "/usr/bin/chromium",
    }
    assert sandbox_overrides(environ) == {}


def test_a_path_that_merely_sounds_like_a_secret_is_not_hidden():
    """`STRAVA_TOKEN_CACHE` is a PATH. Its name contains "TOKEN", so the rule would mask
    it; the exception is named explicitly, otherwise the token cache becomes unreachable
    and every call re-authorizes."""
    assert sandbox_overrides({"STRAVA_TOKEN_CACHE": "/chemin/cache.json"}) == {}
    assert "STRAVA_TOKEN_CACHE" in SANDBOX_NEEDS


def test_ordinary_variables_are_left_alone():
    """Only secrets are neutralized: breaking PATH or TZ would stop everything."""
    assert sandbox_overrides({"PATH": "/usr/bin", "TZ": "Europe/Paris",
                              "VAULT_PATH": "/home/ubuntu/redline",
                              "COACH_BASE_URL": "https://coach.example.com"}) == {}


def test_an_engine_can_name_the_one_secret_it_needs():
    """The Claude launcher authenticates with its OAuth token; Codex has no use for it.
    The exception is carried by the caller, not by the rule."""
    environ = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01"}
    assert sandbox_overrides(environ) == {"CLAUDE_CODE_OAUTH_TOKEN": ""}
    assert sandbox_overrides(environ, keep=("CLAUDE_CODE_OAUTH_TOKEN",)) == {}


def test_the_healthcheck_url_is_hidden_although_its_name_says_nothing():
    """It carries an identifier that is enough to silence the monitoring."""
    assert sandbox_overrides({"HEALTHCHECK_PING_URL": "https://hc-ping.com/uuid"}) == {
        "HEALTHCHECK_PING_URL": ""}


def test_the_override_blanks_rather_than_deletes():
    """The SDKs do `os.environ.copy()` then `update()`, so one can only overwrite. Returning
    a list of names to delete would give a filter that filters nothing."""
    hidden = sandbox_overrides({"MESSENGER_PAGE_TOKEN": "EAAG-secret"})
    assert hidden["MESSENGER_PAGE_TOKEN"] == ""
    assert all(value == "" for value in hidden.values())


# --------------------------------------------------------------- upstream guard

# The two tests below carry the guarantee. The others check hand-written names, so they can
# only catch the omissions already thought of. These READ the code that reads the
# environment: the next variable added to a script is covered without anyone thinking of it.

ENV_READ = re.compile(
    r"""os\.environ\.get\(\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)["']"""
)

ROOT = ENGINE_ROOT

PACKAGE = ROOT / "src" / "redline"
# Service modules a command may import (leaves: standard library only). What they read is
# read BY a command, so it must cross the sandbox like everything else.
SCRIPT_LEAVES = (PACKAGE / "service" / "strength.py",)


def _names_read_by(path: Path) -> set:
    return {name for match in ENV_READ.finditer(path.read_text(encoding="utf-8"))
            for name in match.groups() if name}


def sandbox_side_sources():
    """Every file whose code runs INSIDE the agent's sandbox."""
    return sorted({path for path in PACKAGE.rglob("*.py")
                   if not {"service", "evaluation"} & set(path.parts)
                   and path.name not in OPERATOR_TOOLS} | set(SCRIPT_LEAVES))


def test_every_variable_a_script_reads_survives_the_sandbox():
    """A variable that `scripts/` reads and that `sandbox_overrides` blanks is a silent
    failure: the script does not crash, it returns a degraded version of its work (for
    example strength cards without a button, with no log line).

    The test keeps no list: it re-reads the sources. Adding an
    `os.environ.get("UN_NOUVEAU_TOKEN")` to a script will fail CI, and the fix is to NAME the
    variable in SANDBOX_NEEDS and say who reads it.
    """
    for source in sandbox_side_sources():
        names = _names_read_by(source)
        if not names:
            continue
        hidden = sorted(sandbox_overrides(dict.fromkeys(names, "valeur")))
        assert not hidden, (
            f"{source.relative_to(ROOT)} lit {hidden}, que le bac à sable vide. "
            "Ajoute-les à SANDBOX_NEEDS en justifiant l'appel, ou cesse de les lire.")


def test_the_scan_actually_finds_something():
    """A regex that stops matching would make the previous test pass vacuously --
    exactly the failure it exists to prevent."""
    found = set()
    for source in sandbox_side_sources():
        found |= _names_read_by(source)
    assert {"DATABASE_URL", "STRAVA_CLIENT_SECRET", "COACH_BASE_URL"} <= found, found


def test_production_secret_file_lives_outside_the_agent_workspace():
    unit = (ROOT / "deploy" / "systemd" / "coach-messenger.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/redline/runtime/coach-messenger.env" in unit
    assert "EnvironmentFile=/home/ubuntu/redline/.env" not in unit


def _agent_tool_policy() -> list[str]:
    return [line for raw in (ROOT / "deploy" / "agent-cli-tools.txt").read_text(
        encoding="utf-8").splitlines() if (line := raw.strip()) and not line.startswith("#")]


def test_agent_cli_policy_is_sorted_unique_and_points_to_tracked_scripts():
    tools = _agent_tool_policy()
    assert tools == sorted(set(tools))
    for tool in tools:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*\.py", tool)
        source = f"src/redline/cli/{tool.removesuffix('.py').replace('-', '_')}.py"
        assert (ROOT / source).is_file(), tool
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", source],
            cwd=ROOT, capture_output=True, check=False)
        assert tracked.returncode == 0, f"{tool} n'est pas suivi par Git"


def test_every_cli_named_to_the_live_agent_is_in_the_root_owned_policy():
    references = set()
    texts = sorted(path for path in (PACKAGE / "coach").rglob("*")
                   if path.suffix in {".md", ".py"})
    assert texts
    for path in (ROOT / "CLAUDE.md", *texts):
        references.update(re.findall(r"scripts/([a-z0-9-]+\.py)", path.read_text(
            encoding="utf-8")))
    assert references <= set(_agent_tool_policy())


def test_systemd_requires_both_root_owned_agent_cli_files():
    unit = (ROOT / "deploy" / "systemd" / "coach-messenger.service").read_text(encoding="utf-8")
    assert "ExecStartPre=/usr/bin/test -x /usr/local/bin/redline-agent-cli" in unit
    assert ("ExecStartPre=/usr/bin/test -r "
            "/usr/local/share/redline/agent-cli-tools.txt") in unit


def test_root_owned_launcher_refuses_a_cli_modified_during_the_turn(tmp_path):
    repo = tmp_path / "repo"
    script_dir = repo / "src" / "redline" / "cli"
    script_dir.mkdir(parents=True)
    target = script_dir / "ledger.py"
    target.write_text("print('version déployée')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "src/redline/cli/ledger.py"], cwd=repo, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "initial",
    ], cwd=repo, check=True)

    fake_bin = tmp_path / ".venvs" / "redline" / "bin"
    fake_bin.mkdir(parents=True)
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" \"cwd=$(pwd -P)\"\n",
                           encoding="utf-8")
    fake_python.chmod(0o755)
    (fake_bin.parent / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    environment = {
        **{name: value for name, value in os.environ.items() if name != "VAULT_PATH"},
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "REDLINE_REPO": str(repo),
        "REDLINE_TURN_ID": "0f6b1c2e-3a4d-4e5f-8a9b-0c1d2e3f4a5b",
    }
    launcher = ROOT / "deploy" / "redline-agent-cli"

    # Without a turn identity, the coach launcher opens no direct write path.
    # Blanking the variable is not enough to leave the transaction.
    for turn_id in ("", "pas-un-uuid"):
        outside = subprocess.run(
            [launcher, "fact-write.py", "daily"],
            env={**environment, "REDLINE_TURN_ID": turn_id},
            text=True, capture_output=True, check=False)
        assert outside.returncode == 2
        assert "aucun tour en cours" in outside.stderr

    # Code or a venv the service user can write is refused before anything runs.
    def read_only(*folders, mode=0o555):
        for folder in folders:
            folder.chmod(mode)

    vault = tmp_path / "vault"
    vault.mkdir()
    writable = subprocess.run(
        [launcher, "ledger.py", "--semaine", "2030-W35"],
        env={**environment, "VAULT_PATH": str(vault)},
        text=True, capture_output=True, check=False)
    assert writable.returncode == 1
    assert "code du moteur modifiable" in writable.stderr
    read_only(script_dir, script_dir.parent, repo / "src")
    venv_open = subprocess.run(
        [launcher, "ledger.py", "--semaine", "2030-W35"],
        env={**environment, "VAULT_PATH": str(vault)},
        text=True, capture_output=True, check=False)
    assert venv_open.returncode == 1
    assert "venv modifiable" in venv_open.stderr
    site = fake_bin.parent / "lib" / "python3.12" / "site-packages"
    read_only(fake_bin, site, fake_bin.parent)

    # Without a vault the launcher refuses: the engine checkout is never one.
    no_vault = subprocess.run(
        [launcher, "ledger.py", "--semaine", "2026-W35"],
        env=environment, text=True, capture_output=True, check=False)
    assert no_vault.returncode == 1
    assert "VAULT_PATH absent" in no_vault.stderr

    # The script stays the verified engine's, and runs in the vault, where the turn put
    # its files.
    environment["VAULT_PATH"] = str(vault)
    split = subprocess.run(
        [launcher, "ledger.py", "--semaine", "2026-W35"],
        env=environment, text=True, capture_output=True, check=False)
    assert split.returncode == 0
    assert "-m\nredline.cli.ledger" in split.stdout
    assert f"cwd={vault.resolve()}" in split.stdout

    read_only(script_dir, mode=0o755)
    target.write_text("print('code injecté')\n", encoding="utf-8")
    read_only(script_dir)
    dirty = subprocess.run(
        [launcher, "ledger.py", "--semaine", "2026-W35"],
        env=environment, text=True, capture_output=True, check=False)
    for folder in (script_dir, script_dir.parent, repo / "src", fake_bin, site,
                   fake_bin.parent):
        folder.chmod(0o755)
    assert dirty.returncode == 1
    assert "runtime différent du commit déployé" in dirty.stderr
