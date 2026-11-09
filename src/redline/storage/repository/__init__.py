"""The operational store: one class per aggregate, composed into `OperationalStore`.

Every public call opens exactly one transaction; a method ending in `_in` joins the
caller's.
"""

from __future__ import annotations

from ..database import Database, from_environment
from ._common import ATHLETE, CORRECTION_ANNOUNCE_WINDOW
from .conversation import Conversation
from .maintenance import Maintenance
from .proactive import ProactiveMessages
from .turns import Submitted, TurnCommands

__all__ = ["ATHLETE", "CORRECTION_ANNOUNCE_WINDOW", "OperationalStore", "Submitted", "open_store"]


class OperationalStore(TurnCommands, Conversation, ProactiveMessages, Maintenance):
    """Single fact API. Public calls open exactly one transaction."""

    def __init__(self, database: Database, *, athlete_id: str = ATHLETE) -> None:
        self.database = database
        self.athlete_id = athlete_id

    def ready(self, expected_revision: str | None = None) -> bool:
        return self.database.ready(expected_revision)

    def schema_revision(self) -> str | None:
        return self.database.schema_revision()


def open_store(application: str) -> OperationalStore:
    """The operational store of a command, on the database `DATABASE_URL` names."""
    return OperationalStore(from_environment(application))
