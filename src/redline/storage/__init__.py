"""Operational persistence, loaded lazily.

PostgreSQL owns the facts. Markdown is only a readable projection and can be
fully regenerated.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import Database, StorageError
    from .repository import OperationalStore

__all__ = ["Database", "OperationalStore", "StorageError"]


def __getattr__(name: str):
    if name in ("Database", "StorageError"):
        from .database import Database, StorageError

        return {"Database": Database, "StorageError": StorageError}[name]
    if name == "OperationalStore":
        from .repository import OperationalStore

        return OperationalStore
    raise AttributeError(name)
