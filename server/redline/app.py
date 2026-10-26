"""Composition root: wires the bricks together and starts the service.

This is the only module that knows everyone. The public entry point is
`python3 -m redline` (see __main__.py), run from the server/ directory.
"""

# ruff: noqa: E402 -- repository path must exist before importing shared models

from __future__ import annotations

import logging
import sys
from pathlib import Path

# The installed systemd unit may lag one repository update behind. Bootstrap the
# repository root here so a new app remains importable during that rollout; shared
# modules themselves never mutate sys.path.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from .agent_factory import build_agent
from .attachments import AttachmentService
from .bot import CoachBot
from .config import OUTBOX_DIR, SERVER_DIR, Config, _load_dotenv
from .messenger import MessengerClient
from .notebook import NotebookService
from .outbox import Outbox
from .postgres_state import PostgresStateStore
from .proactive import ProactiveScheduler
from .vault import VaultRepo
from .web import create_app

log = logging.getLogger("redline")
MIN_PYTHON = (3, 12)


def require_supported_python(version_info: tuple[int, ...] | None = None) -> None:
    """Fail fast on a runtime that cannot satisfy the service dependencies."""
    version = tuple(sys.version_info) if version_info is None else version_info
    if version[:2] < MIN_PYTHON:
        raise SystemExit(
            "Redline requiert Python 3.12 ou plus récent "
            f"(version active : {version[0]}.{version[1]})."
        )


def main() -> None:
    require_supported_python()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Les événements structurés sortent en JSON nu, une ligne chacun, pour `jq`.
    event_handler = logging.StreamHandler()
    event_handler.setFormatter(logging.Formatter("%(message)s"))
    events_log = logging.getLogger("redline.events")
    events_log.addHandler(event_handler)
    events_log.propagate = False
    _load_dotenv(SERVER_DIR / ".env")
    config = Config.from_env()

    if not config.database_url:
        raise SystemExit("DATABASE_URL obligatoire : aucun stockage de secours n'existe")
    from scripts.storage import Database, OperationalStore
    from scripts.storage.database import expected_schema_revision
    database = Database(config.database_url, application_name="redline-server")
    expected_revision = expected_schema_revision(SERVER_DIR.parent / "alembic.ini")
    if not database.ready(expected_revision):
        raise SystemExit(
            "PostgreSQL indisponible ou schéma en retard : démarrage refusé sans "
            f"stockage canonique à la révision {expected_revision}")
    operational_store = OperationalStore(database)
    # Au démarrage, où aucun tour n'est en vol : les traces de mécanique périmées
    # Les marqueurs proactifs périmés s'effacent. Jamais un fait ni une clé d'idempotence.
    purged = operational_store.purge_expired()
    if purged:
        log.info("Traces périmées effacées : %s",
                 ", ".join(f"{name} {count}" for name, count in sorted(purged.items())))
    state = PostgresStateStore(database)
    recovered = state.proactive.recover_running_jobs()
    if recovered:
        log.warning("Jobs proactifs repris après redémarrage : %d", recovered)
    client = MessengerClient(config)
    repo = VaultRepo(config.vault_path, config.git_remote, config.git_branch,
                     operational_store=operational_store)
    agent = build_agent(config, state)
    attachments = AttachmentService(
        Path(config.attachment_dir), max_bytes=config.attachment_max_bytes,
        total_bytes=config.attachment_total_bytes)
    from scripts.storage.notebook import NotebookStore
    notebook = NotebookService(NotebookStore(database), repo, state,
                               Path(config.vault_path), config.coach_base_url,
                               operational=operational_store)
    bot = CoachBot(client, repo, agent, state, config, Outbox(OUTBOX_DIR),
                   attachments=attachments, notebook=notebook)
    bot.start()

    scheduler = ProactiveScheduler(bot, state, config)
    scheduler.start()
    log.info("Redline started. Vault: %s -- port %d -- proactive %s",
             config.vault_path, config.webhook_port,
             "ON" if config.proactive_enabled else "OFF")
    if config.proactive_enabled and not config.strava_verify_token:
        log.warning("Strava webhook disabled: STRAVA_VERIFY_TOKEN is empty.")
    if config.proactive_enabled and not config.strava_owner_id:
        log.warning("Strava webhook disabled: STRAVA_OWNER_ID is empty.")
    if config.proactive_enabled and not config.strava_webhook_secret:
        log.warning("Strava webhook disabled: STRAVA_WEBHOOK_SECRET is empty.")

    app = create_app(config, bot, state, operational_store=operational_store,
                     notebook=notebook, nudge=scheduler.nudge)
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=config.webhook_port, threads=4)
    finally:
        scheduler.stop()
        bot.stop()
        database.dispose()
