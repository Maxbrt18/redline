"""The operational store: one class per aggregate, composed into `OperationalStore`.

Every public call opens exactly one transaction; a method ending in `_in` joins the
caller's.
"""

from __future__ import annotations

from ..database import Database
from ._common import ATHLETE, CORRECTION_ANNOUNCE_WINDOW
from .conversation import Conversation
from .facts import Facts
from .maintenance import Maintenance
from .observations import Observations
from .plans import Plans
from .proactive import ProactiveMessages
from .session_cycles import SessionCycles
from .strava_intake import StravaIntake
from .strength import Strength
from .turns import TurnCommands

__all__ = ["ATHLETE", "CORRECTION_ANNOUNCE_WINDOW", "OperationalStore"]


class OperationalStore(TurnCommands, Conversation, Observations, StravaIntake,
                       SessionCycles, Strength, Plans, Facts, ProactiveMessages,
                       Maintenance):
    """API unique des faits. Les appels publics ouvrent exactement une transaction."""

    def __init__(self, database: Database, *, athlete_id: str = ATHLETE) -> None:
        self.database = database
        self.athlete_id = athlete_id

    def ready(self, expected_revision: str | None = None) -> bool:
        return self.database.ready(expected_revision)

    def schema_revision(self) -> str | None:
        return self.database.schema_revision()
