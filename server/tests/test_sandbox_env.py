"""Ce que l'agent a le droit de voir dans son environnement.

Les deux SDK lancent l'agent avec une copie de notre environnement. Tout ce que
systemd charge depuis son fichier d'environnement se retrouvait donc dans le bac à sable, puis sur
le disque : le 2026-08-14, `~/.codex/shell_snapshots/` contenait en clair, et lisible
par tous, le jeton de page Messenger, l'app secret, DATABASE_URL, le secret HMAC des
liens de renfo et le jeton OAuth Claude. Neuf fichiers accumulés depuis le passage à
Codex, qu'aucun test ne regardait et qu'aucun log ne mentionnait.

L'agent n'a jamais eu besoin de ces valeurs : il lit le vault et appelle des scripts.
Ce n'est pas lui qui poste les messages ni qui signe les liens.
"""

import os
import re
import subprocess
from pathlib import Path

from redline.config import SANDBOX_NEEDS, sandbox_overrides


def test_the_messenger_credentials_never_reach_the_agent():
    """Avec le jeton de page, on écrit à Sam sous l'identité du coach."""
    hidden = sandbox_overrides({
        "MESSENGER_PAGE_TOKEN": "EAAG-secret",
        "MESSENGER_APP_SECRET": "abcdef",
        "MESSENGER_VERIFY_TOKEN": "coach-2026",
    })
    assert hidden == {"MESSENGER_PAGE_TOKEN": "", "MESSENGER_APP_SECRET": "",
                      "MESSENGER_VERIFY_TOKEN": ""}


def test_a_variable_nobody_thought_about_is_hidden_by_default():
    """LE test qui compte. La règle est fermée par défaut : une variable ajoutée
    demain, à laquelle personne n'aura pensé, est masquée sur son seul nom. Une liste
    noire tenue à la main aurait laissé passer exactement celle qu'on oublie."""
    for name in ("UN_NOUVEAU_TOKEN", "FOO_SECRET", "DB_PASSWORD", "X_API_KEY",
                 "SOME_CREDENTIAL", "MESSENGER_ALLOWED_PSID"):
        assert sandbox_overrides({name: "valeur"}) == {name: ""}, name


def test_what_the_sandbox_genuinely_needs_still_gets_through():
    """Masquer DATABASE_URL couperait PostgreSQL, masquer les identifiants Strava
    rendrait le coach aveugle : la panne du 14/08 réintroduite par son correctif."""
    environ = {
        "DATABASE_URL": "postgresql+psycopg://u:p@/redline",
        "STRAVA_CLIENT_ID": "42",
        "STRAVA_CLIENT_SECRET": "s3cr3t",
        "REDLINE_TURN_ID": "uuid",
        "CHROMIUM_PATH": "/usr/bin/chromium",
    }
    assert sandbox_overrides(environ) == {}


def test_a_path_that_merely_sounds_like_a_secret_is_not_hidden():
    """`STRAVA_TOKEN_CACHE` est un CHEMIN. Son nom porte « TOKEN », donc la règle le
    masquerait ; l'exception est nommée, sinon le cache de jetons devient introuvable
    et chaque appel refait une autorisation."""
    assert sandbox_overrides({"STRAVA_TOKEN_CACHE": "/chemin/cache.json"}) == {}
    assert "STRAVA_TOKEN_CACHE" in SANDBOX_NEEDS


def test_ordinary_variables_are_left_alone():
    """On ne neutralise que des secrets : casser PATH ou TZ arrêterait tout."""
    assert sandbox_overrides({"PATH": "/usr/bin", "TZ": "Europe/Paris",
                              "VAULT_PATH": "/home/ubuntu/redline",
                              "COACH_BASE_URL": "https://coach.example.com"}) == {}


def test_an_engine_can_name_the_one_secret_it_needs():
    """Le lanceur Claude s'authentifie avec son jeton OAuth ; Codex n'en a que faire.
    L'exception est portée par l'appelant, pas par la règle."""
    environ = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01"}
    assert sandbox_overrides(environ) == {"CLAUDE_CODE_OAUTH_TOKEN": ""}
    assert sandbox_overrides(environ, keep=("CLAUDE_CODE_OAUTH_TOKEN",)) == {}


def test_the_healthcheck_url_is_hidden_although_its_name_says_nothing():
    """Elle porte un identifiant qui suffit à faire taire la surveillance."""
    assert sandbox_overrides({"HEALTHCHECK_PING_URL": "https://hc-ping.com/uuid"}) == {
        "HEALTHCHECK_PING_URL": ""}


def test_the_override_blanks_rather_than_deletes():
    """Les SDK font `os.environ.copy()` puis `update()` : on ne peut qu'écraser. Rendre
    une liste de noms à supprimer donnerait un filtre qui ne filtre rien."""
    hidden = sandbox_overrides({"MESSENGER_PAGE_TOKEN": "EAAG-secret"})
    assert hidden["MESSENGER_PAGE_TOKEN"] == ""
    assert all(value == "" for value in hidden.values())


# --------------------------------------------------------------- le garde-fou amont

# Les deux tests ci-dessous sont LE porteur du correctif du 2026-08-18. Les autres
# vérifient des noms écrits à la main, donc ils ne peuvent attraper que les oublis
# auxquels on a déjà pensé. Ceux-ci LISENT le code qui lit l'environnement : la
# prochaine variable ajoutée à un script est couverte sans que personne y pense.

ENV_READ = re.compile(
    r"""os\.environ\.get\(\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)["']"""
)

ROOT = Path(__file__).resolve().parents[2]

# Les modules de `server/` que `scripts/` a le droit d'importer (des feuilles :
# bibliothèque standard uniquement). Ce qu'ils lisent est lu PAR un script, donc doit
# traverser le bac à sable au même titre.
SCRIPT_LEAVES = (ROOT / "server" / "redline" / "strength.py",)


def _names_read_by(path: Path) -> set:
    return {name for match in ENV_READ.finditer(path.read_text(encoding="utf-8"))
            for name in match.groups() if name}


def sandbox_side_sources():
    """Tout fichier dont le code tourne DANS le bac à sable de l'agent."""
    return sorted(set((ROOT / "scripts").rglob("*.py")) | set(SCRIPT_LEAVES))


def test_every_variable_a_script_reads_survives_the_sandbox():
    """⭐ LE test du 2026-08-18. Une variable que `scripts/` lit et que
    `sandbox_overrides` vide est une panne muette : le script ne plante pas, il rend
    une version dégradée de son travail. C'est comme ça que les cartes de renfo sont
    parties sans bouton du 14 au 18/08 sans qu'une ligne de log s'en émeuve.

    Le test ne tient aucune liste : il relit les sources. Ajouter demain un
    `os.environ.get("UN_NOUVEAU_TOKEN")` dans un script fera tomber la CI, et le
    correctif sera de NOMMER la variable dans SANDBOX_NEEDS en disant qui la lit.
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
    """Une regex qui ne matche plus rendrait le test précédent vert par vacuité --
    exactement la panne qu'il existe pour empêcher."""
    found = set()
    for source in sandbox_side_sources():
        found |= _names_read_by(source)
    assert {"DATABASE_URL", "STRAVA_CLIENT_SECRET", "COACH_BASE_URL"} <= found, found


def test_production_secret_file_lives_outside_the_agent_workspace():
    unit = (ROOT / "server" / "coach-messenger.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/redline/runtime/coach-messenger.env" in unit
    assert "EnvironmentFile=/home/ubuntu/redline/server/.env" not in unit


def _agent_tool_policy() -> list[str]:
    return [line for raw in (ROOT / "server" / "agent-cli-tools.txt").read_text(
        encoding="utf-8").splitlines() if (line := raw.strip()) and not line.startswith("#")]


def test_agent_cli_policy_is_sorted_unique_and_points_to_tracked_scripts():
    tools = _agent_tool_policy()
    assert tools == sorted(set(tools))
    for tool in tools:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*\.py", tool)
        path = ROOT / "scripts" / tool
        assert path.is_file(), tool
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", f"scripts/{tool}"],
            cwd=ROOT, capture_output=True, check=False)
        assert tracked.returncode == 0, f"{tool} n'est pas suivi par Git"


def test_every_cli_named_to_the_live_agent_is_in_the_root_owned_policy():
    references = set()
    for path in (ROOT / "CLAUDE.md", ROOT / "server" / "redline" / "prompts.py"):
        references.update(re.findall(r"scripts/([a-z0-9-]+\.py)", path.read_text(
            encoding="utf-8")))
    assert references <= set(_agent_tool_policy())


def test_systemd_requires_both_root_owned_agent_cli_files():
    unit = (ROOT / "server" / "coach-messenger.service").read_text(encoding="utf-8")
    assert "ExecStartPre=/usr/bin/test -x /usr/local/bin/redline-agent-cli" in unit
    assert ("ExecStartPre=/usr/bin/test -r "
            "/usr/local/share/redline/agent-cli-tools.txt") in unit


def test_root_owned_launcher_refuses_a_cli_modified_during_the_turn(tmp_path):
    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    script_dir.mkdir(parents=True)
    target = script_dir / "ledger.py"
    target.write_text("print('version déployée')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "scripts/ledger.py"], cwd=repo, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "initial",
    ], cwd=repo, check=True)

    fake_bin = tmp_path / ".venvs" / "redline" / "bin"
    fake_bin.mkdir(parents=True)
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    fake_python.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "REDLINE_REPO": str(repo),
        "REDLINE_TURN_ID": "0f6b1c2e-3a4d-4e5f-8a9b-0c1d2e3f4a5b",
    }
    launcher = ROOT / "server" / "redline-agent-cli"

    # RWF-015 : sans identité de tour, le lanceur du coach n'ouvre aucun chemin
    # d'écriture directe. Vider la variable ne suffit plus à sortir de la transaction.
    for turn_id in ("", "pas-un-uuid"):
        outside = subprocess.run(
            [launcher, "fact-write.py", "daily"],
            env={**environment, "REDLINE_TURN_ID": turn_id},
            text=True, capture_output=True, check=False)
        assert outside.returncode == 2
        assert "aucun tour en cours" in outside.stderr

    clean = subprocess.run(
        [launcher, "ledger.py", "--semaine", "2026-W35"],
        env=environment, text=True, capture_output=True, check=False)
    assert clean.returncode == 0
    assert "scripts/ledger.py" in clean.stdout

    target.write_text("print('code injecté')\n", encoding="utf-8")
    dirty = subprocess.run(
        [launcher, "ledger.py", "--semaine", "2026-W35"],
        env=environment, text=True, capture_output=True, check=False)
    assert dirty.returncode == 1
    assert "runtime différent du commit déployé" in dirty.stderr
