#!/usr/bin/env python3
"""Construit, prévole et promeut le runtime du fournisseur d'agent sélectionné.

Une seule transaction pour les deux fournisseurs : lire la version cible dans les
bornes du dépôt, construire un candidat à côté de l'actif, le prévoler réellement,
basculer un lien symbolique, vérifier, sinon restaurer. Seul le runtime du
fournisseur que `coach-messenger` utilise (`AGENT_PROVIDER`) est touché.

Ce fichier est copié dans `/usr/local/sbin` et tourne en root : il n'importe rien
du dépôt, que l'utilisateur du service peut modifier. Les prévols, eux, tournent en
`ubuntu` et réutilisent `redline.routing` pour tester les modèles réellement routés.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO = Path("/home/ubuntu/redline")
SERVICE = "coach-messenger"
SERVICE_UNIT = REPO / "deploy/systemd/coach-messenger.service"
SYSTEM_PYTHON = "/usr/bin/python3.12"
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
MARKER = ".redline-runtime-ready"
STARTUP_TIMEOUT = 90

# Codex : un venv Python complet par version. Le lien est le venv du service, pour
# les deux fournisseurs ; seul Codex y vit en tant que runtime.
VENVS = Path("/home/ubuntu/.venvs")
SERVICE_VENV = VENVS / "redline-current"
REQUIREMENTS = REPO / "requirements.txt"
DEV_REQUIREMENTS = REPO / "requirements-dev.txt"
CODEX_SMOKE = "/usr/local/libexec/redline-codex-runtime-smoke.py"
CODEX_CANDIDATE = re.compile(r"^redline-codex-[0-9]+(?:\.[0-9]+){1,3}-[a-f0-9]{10}$")

# Claude : un préfixe npm par version, le service trouve `claude` par son PATH.
CLAUDE_RUNTIMES = Path("/home/ubuntu/.claude-runtimes")
CLAUDE_LINK = CLAUDE_RUNTIMES / "current"
CLAUDE_PACKAGE = "@anthropic-ai/claude-code"
CLAUDE_MANIFEST = REPO / "deploy/claude-runtime.txt"
CLAUDE_CHANNEL = "stable"
CLAUDE_SMOKE = "/usr/local/libexec/redline-claude-runtime-smoke.py"
CLAUDE_CANDIDATE = re.compile(r"^claude-code-[0-9]+(?:\.[0-9]+){1,3}$")
CLAUDE_BIN = Path("node_modules/.bin/claude")


# --- Versions ---------------------------------------------------------------

def _version_key(value: str) -> tuple[int, int, int, int]:
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"version stable invalide: {value!r}")
    parts = [int(part) for part in value.split(".")]
    padded = [*parts, 0, 0, 0, 0][:4]
    return padded[0], padded[1], padded[2], padded[3]


def _manifest_bounds(manifest: str, package: str) -> tuple[str, str]:
    """Lit la seule contrainte `paquet>=min,<max` autorisée pour un runtime."""
    lines = [
        line.strip()
        for line in manifest.splitlines()
        if line.strip().startswith(package)
    ]
    pattern = re.compile(
        re.escape(package)
        + r">=([0-9]+(?:\.[0-9]+){1,3}),<([0-9]+(?:\.[0-9]+){1,3})"
    )
    match = pattern.fullmatch(lines[0]) if len(lines) == 1 else None
    if match is None:
        raise ValueError(f"le manifeste doit avoir une seule contrainte {package} stable")
    minimum, maximum = match.groups()
    if _version_key(minimum) >= _version_key(maximum):
        raise ValueError(f"borne {package} invalide")
    return minimum, maximum


def _in_bounds(version: str, minimum: str, maximum: str) -> bool:
    return _version_key(minimum) <= _version_key(version) < _version_key(maximum)


def _latest_allowed(versions: Sequence[str], minimum: str, maximum: str) -> str:
    allowed = [version for version in versions if _in_bounds(version, minimum, maximum)]
    if not allowed:
        raise ValueError("aucune version stable ne respecte le manifeste")
    return max(allowed, key=_version_key)


def _available_versions(output: str) -> list[str]:
    line = next(
        (line for line in output.splitlines() if line.strip().startswith("Available versions:")),
        None,
    )
    if line is None:
        raise ValueError("pip n'a pas retourné la liste des versions Codex")
    raw = line.split(":", 1)[1]
    return [version.strip() for version in raw.split(",") if VERSION_PATTERN.fullmatch(version.strip())]


def _channel_version(dist_tags: str, channel: str) -> str:
    try:
        tags = json.loads(dist_tags)
    except json.JSONDecodeError:
        raise ValueError("npm n'a pas retourné de dist-tags JSON") from None
    version = tags.get(channel) if isinstance(tags, dict) else None
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"canal npm {channel!r} absent ou invalide")
    return version


def _cli_version(output: str) -> str:
    """`claude --version` répond `2.1.285 (Claude Code)`."""
    words = output.split()
    if not words or not VERSION_PATTERN.fullmatch(words[0]):
        raise ValueError(f"version Claude illisible: {output[:80]!r}")
    return words[0]


# --- Exécution --------------------------------------------------------------

def _ubuntu_env() -> dict[str, str]:
    return {
        "HOME": "/home/ubuntu",
        "LOGNAME": "ubuntu",
        "USER": "ubuntu",
        "LANG": "C.UTF-8",
        "PATH": f"{SERVICE_VENV / 'bin'}:/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": f"{REPO}:{REPO / 'server'}",
        "PYTHONUNBUFFERED": "1",
        "DISABLE_AUTOUPDATER": "1",
    }


def _as_ubuntu(command: Sequence[str], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    pairs = [f"{key}={value}" for key, value in _ubuntu_env().items()]
    return subprocess.run(
        ["/usr/sbin/runuser", "-u", "ubuntu", "--", "/usr/bin/env", *pairs, *command],
        cwd=REPO,
        env={"PATH": "/usr/sbin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _failure_tail(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout).strip().splitlines()
    tail = detail[-1][:300] if detail else "aucun détail"
    tail = re.sub(r"(https?://)[^/@\s]+@", r"\1[identifiants masqués]@", tail)
    return re.sub(r"(?i)(password|token|secret|key)=([^&\s]+)", r"\1=[masqué]", tail)


def _run_as_ubuntu(command: Sequence[str], *, timeout: int = 600) -> str:
    result = _as_ubuntu(command, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"commande ubuntu échouée ({result.returncode}): {_failure_tail(result)}")
    return result.stdout.strip()


def _service_settings(unit: Path = SERVICE_UNIT) -> list[str]:
    """Les directives d'identité et d'environnement du service, telles que systemd les lit."""
    keep = ("User", "WorkingDirectory", "Environment", "EnvironmentFile")
    settings = []
    for line in unit.read_text(encoding="utf-8").splitlines():
        key, separator, _ = line.strip().partition("=")
        if separator and key in keep:
            settings.append(line.strip())
    return settings


def _service_run_command(command: Sequence[str], settings: Sequence[str]) -> list[str]:
    properties = [arg for setting in settings for arg in ("-p", setting)]
    return [
        "/usr/bin/systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        *properties, "-p", "Environment=DISABLE_AUTOUPDATER=1", *command,
    ]


def _run_in_service_env(command: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Exécute avec l'utilisateur et l'environnement exacts du coach.

    systemd compose lui-même `EnvironmentFile=` et `Environment=` : aucun secret ne
    transite par la ligne de commande et aucune lecture parallèle ne peut diverger.
    """
    return subprocess.run(
        _service_run_command(command, _service_settings()),
        env={"PATH": "/usr/sbin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _echo(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        print(result.stdout.rstrip(), flush=True)
    if result.stderr:
        print(result.stderr.rstrip()[-1000:], file=sys.stderr, flush=True)


# --- Service ----------------------------------------------------------------

def _check_ready(*, require_recent_boot: bool = False) -> bool:
    try:
        active = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", SERVICE],
            check=False,
            timeout=10,
        )
        if active.returncode:
            return False
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
            health = json.load(response)
        with urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=3) as response:
            ready = json.load(response)
        return (
            health.get("status") == "ok"
            and isinstance(health.get("uptime_s"), int)
            and (not require_recent_boot or health["uptime_s"] <= STARTUP_TIMEOUT)
            and ready.get("status") == "ready"
            and all(ready.get(key) is True for key in ("storage", "schema", "worker"))
            and ready.get("revision") == ready.get("expected_revision")
        )
    except (OSError, ValueError, urllib.error.URLError, TimeoutError):
        return False


def _wait_ready() -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if _check_ready(require_recent_boot=True):
            return True
        time.sleep(3)
    return False


def _restart_and_check() -> None:
    subprocess.run(["/usr/bin/systemctl", "restart", SERVICE], check=True, timeout=120)
    if not _wait_ready():
        raise RuntimeError("coach-messenger n'a pas retrouvé /ready après redémarrage")


def _agent_provider(unit: Path = SERVICE_UNIT) -> str:
    """Le fournisseur que le coach utilise réellement, lu comme systemd le lit.

    Prévoler le runtime d'un fournisseur inutilisé mesure un compte qui ne sert pas :
    depuis le 2026-10-06, le compte ChatGPT refuse gpt-6-sol et le job Codex échouait
    toutes les nuits pendant que le coach répondait normalement sous Claude. Comme
    `Config`, un fournisseur absent vaut codex. Les fichiers de `EnvironmentFile=`
    priment sur `Environment=`, comme pour systemd.
    """
    provider = "codex"
    files: list[str] = []
    for line in unit.read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition("=")
        if key == "Environment":
            name, _, setting = value.strip().strip('"').partition("=")
            if name == "AGENT_PROVIDER":
                provider = setting.strip() or provider
        elif key == "EnvironmentFile":
            files.append(value.strip())
    for raw in files:
        try:
            text = Path(raw.lstrip("-")).read_text(encoding="utf-8")
        except OSError:
            if raw.startswith("-"):
                continue
            raise
        for line in text.splitlines():
            name, separator, setting = line.strip().partition("=")
            if separator and name.strip() == "AGENT_PROVIDER":
                provider = setting.strip().strip("'\"") or provider
    return provider.strip().lower()


# --- Liens et candidats -----------------------------------------------------

def _atomic_link(link: Path, target: Path) -> None:
    temp_link = link.with_name(f".{link.name}.{os.getpid()}.new")
    if temp_link.exists() or temp_link.is_symlink():
        temp_link.unlink()
    temp_link.symlink_to(target)
    os.replace(temp_link, link)


def _link_target(link: Path) -> Path:
    if not link.is_symlink():
        raise RuntimeError(f"{link} doit être un lien symbolique")
    target = (link.parent / os.readlink(link)).resolve(strict=True)
    if target.parent != link.parent.resolve() or not target.is_dir():
        raise RuntimeError(f"{link} ne pointe pas vers un runtime attendu")
    return target


def _chown_ubuntu(path: Path) -> None:
    ubuntu = pwd.getpwnam("ubuntu")
    os.chown(path, ubuntu.pw_uid, ubuntu.pw_gid)


def _build_candidate(
    candidate: Path,
    version: str,
    marker_value: str,
    build: Callable[[], None],
    installed_version: Callable[[], str],
) -> Path:
    """Réutilise un candidat complet, sinon le reconstruit entièrement."""
    marker = candidate / MARKER
    if candidate.exists() or candidate.is_symlink():
        if candidate.is_symlink() or not candidate.is_dir():
            raise RuntimeError("candidat existant de type inattendu")
        try:
            complete = (marker.read_text(encoding="utf-8") == marker_value
                        and installed_version() == version)
        except (OSError, RuntimeError, ValueError):
            complete = False
        if complete:
            return candidate
        shutil.rmtree(candidate)

    try:
        build()
        if installed_version() != version:
            raise RuntimeError("la version construite diffère de la version demandée")
        marker.write_text(marker_value, encoding="utf-8")
        _chown_ubuntu(marker)
        return candidate
    except Exception:
        if candidate.exists() and not candidate.is_symlink():
            shutil.rmtree(candidate, ignore_errors=True)
        raise


def _prune_candidates(root: Path, pattern: re.Pattern[str], keep: set[Path]) -> None:
    # L'actif et son prédécesseur restent ; les anciens candidats ne sont supprimés
    # qu'après une promotion vérifiée.
    keep_resolved = {path.resolve() for path in keep}
    for path in root.iterdir():
        if not pattern.fullmatch(path.name):
            continue
        if path.is_symlink() or not path.is_dir():
            continue
        if path.resolve() in keep_resolved:
            continue
        shutil.rmtree(path)
        print(f"ancien candidat supprimé: {path.name}", flush=True)


# --- Runtimes ---------------------------------------------------------------

@dataclass(frozen=True)
class Active:
    version: str
    target: Path


class CodexRuntime:
    """`openai-codex` dans un venv dédié ; le service redémarre sur le nouveau lien."""

    name = "Codex"
    link = SERVICE_VENV
    root = VENVS
    candidate_pattern = CODEX_CANDIDATE

    def bounds(self) -> tuple[str, str]:
        return _manifest_bounds(REQUIREMENTS.read_text(encoding="utf-8"), "openai-codex")

    @staticmethod
    def _installed_version(python_bin: str) -> str:
        return _run_as_ubuntu([
            python_bin,
            "-c",
            "from importlib.metadata import version; print(version('openai-codex'))",
        ], timeout=30)

    def active(self) -> Active:
        # Le venv historique sert de point de départ : Codex a toujours un actif.
        return Active(self._installed_version(str(self.link / "bin/python")),
                      _link_target(self.link))

    def target_version(self, minimum: str, maximum: str) -> str:
        index = _as_ubuntu([
            str(self.link / "bin/python"), "-m", "pip", "index", "versions", "openai-codex",
        ], timeout=60)
        if index.returncode:
            raise RuntimeError(f"lecture PyPI impossible (code {index.returncode})")
        return _latest_allowed(_available_versions(index.stdout), minimum, maximum)

    @staticmethod
    def _requirements_digest() -> str:
        digest = hashlib.sha256()
        for path in (REQUIREMENTS, DEV_REQUIREMENTS):
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    @staticmethod
    def pip_install_command(python_bin: str) -> list[str]:
        return [
            python_bin, "-m", "pip", "install", "--disable-pip-version-check",
            "-r", str(REQUIREMENTS), "-r", str(DEV_REQUIREMENTS),
        ]

    @staticmethod
    def package_install_command(python_bin: str) -> list[str]:
        """The engine itself, editable: the checkout stays the code the service runs."""
        return [python_bin, "-m", "pip", "install", "--disable-pip-version-check",
                "--no-deps", "-e", str(REPO)]

    def build(self, version: str) -> Path:
        req_digest = self._requirements_digest()
        candidate = self.root / f"redline-codex-{version}-{req_digest[:10]}"
        python_bin = str(candidate / "bin/python")

        def build() -> None:
            _run_as_ubuntu([SYSTEM_PYTHON, "-m", "venv", str(candidate)], timeout=120)
            _run_as_ubuntu(self.pip_install_command(python_bin), timeout=900)
            _run_as_ubuntu(self.package_install_command(python_bin), timeout=300)

        return _build_candidate(candidate, version, f"{version}\n{req_digest}\n", build,
                                lambda: self._installed_version(python_bin))

    def preflight(self, candidate: Path, version: str, active: Active | None) -> None:
        result = _as_ubuntu([str(candidate / "bin/python"), CODEX_SMOKE, "--version", version],
                            timeout=480)
        _echo(result)
        if result.returncode:
            raise RuntimeError(f"prévol des modèles Codex échoué ({result.returncode})")

    def activate(self, version: str | None) -> None:
        _restart_and_check()
        if version is not None and self._installed_version(str(self.link / "bin/python")) != version:
            raise RuntimeError("le runtime actif après redémarrage n'a pas la version attendue")


class ClaudeRuntime:
    """Claude Code dans un préfixe npm dédié.

    Chaque tour lance un nouveau processus `claude` trouvé par le PATH du service :
    basculer le lien suffit, sans redémarrage, et un tour en cours garde son binaire.
    Le canal npm `stable` d'Anthropic sert de délai de mûrissement ; `latest` et `next`
    ne sont jamais pris. Sans lien géré (amorçage), le PATH retombe sur le Claude
    global, qui reste le dernier recours.
    """

    name = "Claude"
    link = CLAUDE_LINK
    root = CLAUDE_RUNTIMES
    candidate_pattern = CLAUDE_CANDIDATE

    def bounds(self) -> tuple[str, str]:
        return _manifest_bounds(CLAUDE_MANIFEST.read_text(encoding="utf-8"), CLAUDE_PACKAGE)

    @staticmethod
    def _installed_version(binary: Path) -> str:
        return _cli_version(_run_as_ubuntu([str(binary), "--version"], timeout=30))

    def active(self) -> Active | None:
        if not self.link.exists() and not self.link.is_symlink():
            return None
        target = _link_target(self.link)
        return Active(self._installed_version(target / CLAUDE_BIN), target)

    def target_version(self, minimum: str, maximum: str) -> str:
        tags = _run_as_ubuntu(["npm", "view", CLAUDE_PACKAGE, "dist-tags", "--json"], timeout=60)
        version = _channel_version(tags, CLAUDE_CHANNEL)
        if not _in_bounds(version, minimum, maximum):
            raise ValueError(f"canal {CLAUDE_CHANNEL} hors manifeste: {version}")
        return version

    @staticmethod
    def npm_install_command(prefix: Path, version: str) -> list[str]:
        # Construit en `ubuntu`, comme les venvs Codex : le postinstall npm, qui
        # copie le binaire natif de la plateforme, ne s'exécute jamais en root.
        return [
            "npm", "install", "--prefix", str(prefix), "--no-audit", "--no-fund",
            "--loglevel=error", f"{CLAUDE_PACKAGE}@{version}",
        ]

    def build(self, version: str) -> Path:
        if not self.root.exists():
            self.root.mkdir(mode=0o755)
            _chown_ubuntu(self.root)
        candidate = self.root / f"claude-code-{version}"
        return _build_candidate(
            candidate, version, f"{version}\n",
            lambda: _run_as_ubuntu(self.npm_install_command(candidate, version), timeout=600),
            lambda: self._installed_version(candidate / CLAUDE_BIN),
        )

    def preflight(self, candidate: Path, version: str, active: Active | None) -> None:
        command = [str(SERVICE_VENV / "bin/python"), CLAUDE_SMOKE,
                   "--binary", str(candidate / CLAUDE_BIN), "--version", version]
        if active is not None:
            command += ["--previous", str(active.target / CLAUDE_BIN)]
        result = _run_in_service_env(command, timeout=600)
        _echo(result)
        if result.returncode:
            raise RuntimeError(f"prévol des modèles Claude échoué ({result.returncode})")

    def activate(self, version: str | None) -> None:
        if version is not None and self._installed_version(self.link / CLAUDE_BIN) != version:
            raise RuntimeError("le lien Claude ne sert pas la version attendue")


RUNTIMES: dict[str, Callable[[], CodexRuntime | ClaudeRuntime]] = {
    "codex": CodexRuntime,
    "claude": ClaudeRuntime,
}


# --- Transaction ------------------------------------------------------------

def _restore(runtime: CodexRuntime | ClaudeRuntime, active: Active | None) -> None:
    if active is not None:
        _atomic_link(runtime.link, active.target)
    else:
        runtime.link.unlink()
    runtime.activate(None if active is None else active.version)


def promote(runtime: CodexRuntime | ClaudeRuntime) -> None:
    minimum, maximum = runtime.bounds()
    active = runtime.active()
    if active is not None and _version_key(active.version) >= _version_key(maximum):
        raise RuntimeError(f"version active hors plage autorisée: {active.version}")
    target = runtime.target_version(minimum, maximum)
    shown = active.version if active is not None else "non géré"
    print(f"runtime {runtime.name} actif={shown}, cible={target}", flush=True)
    if active is not None and _version_key(target) <= _version_key(active.version):
        print(f"aucune mise à jour {runtime.name} disponible", flush=True)
        return
    if not _check_ready():
        raise RuntimeError("coach-messenger n'est pas prêt; promotion reportée")

    candidate = runtime.build(target)
    print(f"runtime candidat construit: {runtime.name} {target}", flush=True)
    runtime.preflight(candidate, target, active)

    # Re-vérifier le service juste avant la bascule : le prévol ne doit pas
    # promouvoir un runtime si un incident indépendant a arrêté le coach.
    if not _check_ready():
        raise RuntimeError("coach-messenger a perdu sa readiness avant la promotion")
    _atomic_link(runtime.link, candidate)
    try:
        runtime.activate(target)
    except Exception as promotion_error:
        print(f"promotion échouée: {promotion_error}; restauration de {shown}",
              file=sys.stderr, flush=True)
        try:
            _restore(runtime, active)
            print(f"ancien runtime restauré: {runtime.name} {shown}", flush=True)
        except Exception as rollback_error:
            raise RuntimeError(
                f"promotion échouée ({promotion_error}); restauration échouée ({rollback_error})"
            ) from rollback_error
        raise RuntimeError("nouveau runtime refusé; ancien runtime restauré") from promotion_error

    keep = {candidate} if active is None else {candidate, active.target}
    _prune_candidates(runtime.root, runtime.candidate_pattern, keep)
    print(f"runtime {runtime.name} promu et vérifié: {target}", flush=True)


def main() -> int:
    if os.geteuid() != 0:
        print("mise à jour runtime: exécution root requise", file=sys.stderr)
        return 2
    try:
        provider = _agent_provider()
        factory = RUNTIMES.get(provider)
        if factory is None:
            raise ValueError(f"AGENT_PROVIDER inconnu: {provider!r}")
        promote(factory())
        return 0
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
        print(f"mise à jour runtime échouée sans promotion: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
