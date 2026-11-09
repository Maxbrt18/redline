"""Operational persistence.

PostgreSQL owns the facts. Markdown is only a readable projection and can be
fully regenerated.
"""

from .database import Database, StorageError
from .repository import OperationalStore, open_store

__all__ = ["Database", "OperationalStore", "StorageError", "open_store"]
