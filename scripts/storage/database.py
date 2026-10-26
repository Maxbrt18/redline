"""Connexion PostgreSQL et frontière transactionnelle unique."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError


def expected_schema_revision(config_path: Path) -> str:
    """La tête Alembic portée par le code en cours d'exécution."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(config_path))
    revision = ScriptDirectory.from_config(config).get_current_head()
    if not revision:
        raise StorageError("aucune tête Alembic dans le code déployé")
    return revision


class StorageError(RuntimeError):
    """Une opération de persistance a échoué sans exposer de secret de connexion."""


class Database:
    def __init__(self, url: str, *, application_name: str = "redline") -> None:
        if not url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise StorageError("DATABASE_URL doit désigner PostgreSQL")
        self.engine: Engine = sa.create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={
                "application_name": application_name,
                "connect_timeout": 5,
                # Une panne DB doit rendre un 503 rapidement, pas immobiliser le
                # seul worker HTTP jusqu'à la fin d'un timeout réseau implicite.
                "options": "-c statement_timeout=30000 -c lock_timeout=10000",
            },
        )

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """Une commande métier = une transaction, rollback automatique sur erreur."""
        try:
            with self.engine.begin() as connection:
                yield connection
        except SQLAlchemyError as exc:
            raise StorageError(f"transaction PostgreSQL refusée : {exc.__class__.__name__}") from exc

    def schema_revision(self) -> str | None:
        try:
            with self.engine.connect() as connection:
                return connection.execute(sa.text(
                    "SELECT version_num FROM alembic_version"
                )).scalar_one_or_none()
        except SQLAlchemyError:
            return None

    def ready(self, expected_revision: str | None = None) -> bool:
        current = self.schema_revision()
        return bool(current and (expected_revision is None or current == expected_revision))

    def dispose(self) -> None:
        self.engine.dispose()
