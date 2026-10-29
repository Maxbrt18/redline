"""Configuration: reads the .env and the environment, parsed ONCE at startup.

Any unreadable value stops startup with a clear message rather than a traceback
(or worse, an error on the 1000th scheduler tick).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time as dtime
from pathlib import Path

from redline.model import instance as instance_model
from redline.model import paths
from redline.model import rulebook as rulebook_model
from redline.model.errors import ConfigError
from redline.model.instance import Instance

from .routing import RoutingConfig

# `secrets.token_urlsafe(32)` yields 43; no example value reaches 32.
SHARED_SECRET_MIN_LENGTH = 32


# --------------------------------------------------------- sandbox environment

# The SDKs copy the service environment into the agent sandbox. Secret-looking names
# are therefore blanked by default and exceptions are declared explicitly below.
SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PSID", "CREDENTIAL", "API_KEY")

# No marker in their name, but to be masked all the same.
EXTRA_SECRETS = frozenset({"HEALTHCHECK_PING_URL"})

# Values required by executable tools inside the sandbox:
#   DATABASE_URL         → fact-read, ledger, week-plan (application role, no DDL)
#   STRAVA_CLIENT_*      → redline strava
#   STRAVA_TOKEN_CACHE   → a PATH, not a secret, but its name contains "TOKEN"
#   REDLINE_TURN_ID      → the turn's transactional boundary
#   CHROMIUM_PATH        → card rendering
# COACH_BASE_URL is not secret. The service mints the opaque token itself; no session
# secret may cross the sandbox.
# Known residual: PostgreSQL and Strava remain direct capabilities of the coach until
# a semantic broker exists. They never justify `full_access` or bypassing the
# engine's permissions.
SANDBOX_NEEDS = frozenset({
    "DATABASE_URL",
    "STRAVA_CLIENT_ID",
    "STRAVA_CLIENT_SECRET",
    "STRAVA_TOKEN_CACHE",
    "REDLINE_TURN_ID",
    "CHROMIUM_PATH",
})


def sandbox_overrides(environ: Mapping[str, str] | None = None, *,
                      keep: tuple[str, ...] = ()) -> dict[str, str]:
    """Return secrets to blank; ``keep`` declares provider-specific exceptions."""
    source: Mapping[str, str] = os.environ if environ is None else environ
    autorise = SANDBOX_NEEDS.union(keep)
    return {
        name: ""
        for name in source
        if name not in autorise
        and (name in EXTRA_SECRETS or any(marker in name for marker in SECRET_MARKERS))
    }


def _load_dotenv(path: Path) -> None:
    """Load a minimal .env (key=value) without overriding the environment."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _envbool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on", "oui"):
        return True
    if value in ("0", "false", "no", "off", "non"):
        return False
    raise ConfigError(f"{name} invalid: {raw!r} (boolean expected)")


def _envint(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} invalid: {raw!r} (integer expected)") from None


def _envfloat(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} invalid: {raw!r} (number expected)") from None


def _envtime(name: str, default: str) -> dtime | None:
    """"07:45" -> time(7, 45). Explicit empty string -> None (trigger disabled)."""
    raw = (os.environ.get(name, default) or "").strip()
    if not raw:
        return None
    try:
        hh, _, mm = raw.partition(":")
        return dtime(int(hh), int(mm or 0))
    except ValueError:
        raise ConfigError(f"{name} invalid: {raw!r} (HH:MM format expected)") from None


def _load_instance(vault_path: str) -> Instance:
    """The vault's `redline.toml`: without it the service does not know who it serves.

    The rulebook is checked at the same time: a non-conforming `rulebook.toml` would
    otherwise break the first turn that reads it, in front of the athlete, instead of
    failing at startup.
    """
    try:
        rulebook_model.load(Path(vault_path))
        return instance_model.load(Path(vault_path))
    except (instance_model.InstanceError, rulebook_model.RulebookError) as exc:
        raise ConfigError(str(exc)) from None


def _routing_file() -> Path | None:
    """The operator's routing overrides (`REDLINE_ROUTING`), if any."""
    raw = (os.environ.get("REDLINE_ROUTING") or "").strip()
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True)
class Config:
    page_token: str
    verify_token: str
    app_secret: str
    allowed_psid: str
    graph_version: str
    webhook_port: int
    vault_path: str
    agent_provider: str
    claude_bin: str
    codex_bin: str | None
    routing: RoutingConfig
    agent_timeout: int
    agent_timeout_deep: int
    agent_timeout_heavy: int
    triage_enabled: bool
    triage_timeout: int
    git_remote: str
    git_branch: str
    database_url: str
    session_ttl_hours: float
    debounce_seconds: float
    debounce_max_seconds: float
    proactive_enabled: bool
    proactive_active_start: dtime | None
    proactive_min_gap_hours: float
    proactive_silence_days: float
    proactive_morning: dtime | None
    proactive_weekly: dtime | None
    proactive_monthly: dtime | None
    strava_debrief_delay: int
    strava_verify_token: str
    strava_owner_id: str
    healthcheck_ping_url: str
    coach_base_url: str
    strength_reports_per_day: int
    attachment_dir: str
    attachment_max_bytes: int
    attachment_total_bytes: int
    #: What distinguishes this instance (`redline.toml` at the vault root).
    instance: Instance
    #: Secret segment of the Strava callback URL: the route's only authentication.
    strava_webhook_secret: str = ""
    #: `127.0.0.1` behind a proxy on the same host; `0.0.0.0` in a container.
    webhook_host: str = "127.0.0.1"

    @classmethod
    def from_env(cls) -> Config:
        required = ("MESSENGER_PAGE_TOKEN", "MESSENGER_VERIFY_TOKEN",
                    "MESSENGER_APP_SECRET", "MESSENGER_ALLOWED_PSID", "VAULT_PATH")
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise ConfigError(f"Incomplete configuration, missing variables: {', '.join(missing)}")
        provider = (os.environ.get("AGENT_PROVIDER") or "codex").strip().lower()
        if provider not in {"claude", "codex"}:
            raise ConfigError(
                f"AGENT_PROVIDER invalid: {provider!r} (expected claude or codex)")
        agent_timeout = _envint("AGENT_TIMEOUT", 600)
        agent_timeout_deep = _envint("AGENT_TIMEOUT_DEEP", 1800)
        agent_timeout_heavy = _envint("AGENT_TIMEOUT_HEAVY", 5400)
        triage_timeout = _envint("TRIAGE_TIMEOUT", 10)
        vault_path = str(paths.vault_root())
        config = cls(
            page_token=os.environ["MESSENGER_PAGE_TOKEN"],
            verify_token=os.environ["MESSENGER_VERIFY_TOKEN"],
            app_secret=os.environ.get("MESSENGER_APP_SECRET", ""),
            allowed_psid=os.environ.get("MESSENGER_ALLOWED_PSID", "").strip(),
            graph_version=os.environ.get("GRAPH_API_VERSION", "v21.0"),
            webhook_port=_envint("WEBHOOK_PORT", 8000),
            webhook_host=(os.environ.get("WEBHOOK_HOST") or "127.0.0.1").strip(),
            vault_path=vault_path,
            agent_provider=provider,
            claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
            # None lets the SDK use its pinned runtime: CODEX_BIN is an explicit
            # escape hatch, not the normal path.
            codex_bin=(os.environ.get("CODEX_BIN") or "").strip() or None,
            routing=RoutingConfig.from_file(
                provider, _routing_file(),
                agent_timeout=agent_timeout,
                agent_timeout_deep=agent_timeout_deep,
                agent_timeout_heavy=agent_timeout_heavy,
                triage_timeout=triage_timeout,
            ),
            agent_timeout=agent_timeout,
            agent_timeout_deep=agent_timeout_deep,
            agent_timeout_heavy=agent_timeout_heavy,
            triage_enabled=_envbool("TRIAGE_ENABLED", True),
            triage_timeout=triage_timeout,
            git_remote=os.environ.get("GIT_REMOTE", "origin"),
            git_branch=os.environ.get("GIT_BRANCH", "main"),
            database_url=os.environ.get("DATABASE_URL", "").strip(),
            session_ttl_hours=_envfloat("SESSION_TTL_HOURS", 6),
            debounce_seconds=_envfloat("DEBOUNCE_SECONDS", 5),
            debounce_max_seconds=_envfloat("DEBOUNCE_MAX_SECONDS", 20),
            proactive_enabled=_envbool("PROACTIVE_ENABLED", True),
            proactive_active_start=_envtime("PROACTIVE_ACTIVE_START", "07:30"),
            proactive_min_gap_hours=_envfloat("PROACTIVE_MIN_GAP_HOURS", 4),
            proactive_silence_days=_envfloat("PROACTIVE_SILENCE_DAYS", 3),
            proactive_morning=_envtime("PROACTIVE_MORNING", "07:45"),
            proactive_weekly=_envtime("PROACTIVE_WEEKLY", "19:00"),
            proactive_monthly=_envtime("PROACTIVE_MONTHLY", "18:30"),
            strava_debrief_delay=_envint("STRAVA_DEBRIEF_DELAY", 300),
            strava_verify_token=os.environ.get("STRAVA_VERIFY_TOKEN", "").strip(),
            strava_owner_id=os.environ.get("STRAVA_OWNER_ID", "").strip(),
            healthcheck_ping_url=os.environ.get("HEALTHCHECK_PING_URL", "").strip(),
            # Interactive strength session (strength.py). Empty = feature off end to
            # end: no link on the card, no resolution, no report route.
            coach_base_url=os.environ.get("COACH_BASE_URL", "").strip().rstrip("/"),
            strength_reports_per_day=_envint("STRENGTH_REPORTS_PER_DAY", 8),
            attachment_dir=os.path.expanduser(os.environ.get(
                "ATTACHMENT_DIR", str(paths.state_dir() / "attachments"))),
            attachment_max_bytes=_envint("ATTACHMENT_MAX_BYTES", 12 * 1024 * 1024),
            attachment_total_bytes=_envint("ATTACHMENT_TOTAL_BYTES", 20 * 1024 * 1024),
            instance=_load_instance(vault_path),
            strava_webhook_secret=os.environ.get("STRAVA_WEBHOOK_SECRET", "").strip(),
        )
        config._validate()
        config._validate_shared_secrets()
        return config

    def _validate_shared_secrets(self) -> None:
        """The secrets WE choose must be unpredictable.

        Example values from `.env.example` must never survive into production: the
        Meta handshake token could open an HTML reflection on the notebook origin.
        Empty stays allowed for Strava (route disabled).
        """
        secrets = {"MESSENGER_VERIFY_TOKEN": (self.verify_token, True),
                   "STRAVA_VERIFY_TOKEN": (self.strava_verify_token, False),
                   "STRAVA_WEBHOOK_SECRET": (self.strava_webhook_secret, False)}
        for name, (value, required) in secrets.items():
            if (value or required) and len(value) < SHARED_SECRET_MIN_LENGTH:
                raise ConfigError(
                    f"{name} trop faible : {SHARED_SECRET_MIN_LENGTH} caractères minimum. "
                    "Génère-le avec python3 -c 'import secrets; "
                    "print(secrets.token_urlsafe(32))'")

    def _validate(self) -> None:
        ranges = {
            "WEBHOOK_PORT": (self.webhook_port, 1, 65535),
            "AGENT_TIMEOUT": (self.agent_timeout, 1, None),
            "AGENT_TIMEOUT_DEEP": (self.agent_timeout_deep, 1, None),
            "AGENT_TIMEOUT_HEAVY": (self.agent_timeout_heavy, 1, None),
            "TRIAGE_TIMEOUT": (self.triage_timeout, 1, None),
            "SESSION_TTL_HOURS": (self.session_ttl_hours, 0.000001, None),
            "DEBOUNCE_SECONDS": (self.debounce_seconds, 0, None),
            "DEBOUNCE_MAX_SECONDS": (self.debounce_max_seconds, 0, None),
            "PROACTIVE_MIN_GAP_HOURS": (self.proactive_min_gap_hours, 0, None),
            "PROACTIVE_SILENCE_DAYS": (self.proactive_silence_days, 0.000001, None),
            "STRAVA_DEBRIEF_DELAY": (self.strava_debrief_delay, 0, None),
            "STRENGTH_REPORTS_PER_DAY": (self.strength_reports_per_day, 1, None),
            "ATTACHMENT_MAX_BYTES": (self.attachment_max_bytes, 1024, 25 * 1024 * 1024),
            "ATTACHMENT_TOTAL_BYTES": (self.attachment_total_bytes, 1024, 50 * 1024 * 1024),
        }
        for name, (value, minimum, maximum) in ranges.items():
            below = value < minimum
            above = maximum is not None and value > maximum
            if below or above:
                interval = f"{minimum}..{maximum}" if maximum is not None else f"> {minimum}"
                raise ConfigError(f"{name} invalid: {value!r} (expected {interval})")
        if self.debounce_max_seconds < self.debounce_seconds:
            raise ConfigError("DEBOUNCE_MAX_SECONDS invalid: must be >= DEBOUNCE_SECONDS")
        if self.attachment_total_bytes < self.attachment_max_bytes:
            raise ConfigError(
                "ATTACHMENT_TOTAL_BYTES invalid: must be >= ATTACHMENT_MAX_BYTES")
        if self.routing.provider != self.agent_provider:
            raise ConfigError(
                "AGENT_PROVIDER invalid: the routing belongs to "
                f"{self.routing.provider!r}, not {self.agent_provider!r}")
        self.routing.validate()
        for route in self.routing.plans:
            plan = self.routing.plan(route)
            # Route validation is repeated here for a Config built directly by tests
            # or a Python caller, without going through from_env.
            for spec in plan.attempts:
                if spec.timeout < 1:
                    route_name = getattr(route, "value", route)
                    raise ConfigError(
                        f"route {route_name}: timeout invalid: {spec.timeout!r}")

    @property
    def session_ttl(self) -> float:
        return self.session_ttl_hours * 3600

    def in_active_window(self, moment: dtime) -> bool:
        """An initiative may go out from the start time until midnight.

        There is deliberately no end of window: a debrief at 23:00 after a late run
        is still relevant. An initiative ready after midnight is not discarded: it is
        kept prepared until the next opening.
        """
        return moment >= (self.proactive_active_start or dtime(0, 0))
