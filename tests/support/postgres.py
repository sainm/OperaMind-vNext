"""Create, migrate, and remove one isolated PostgreSQL test database."""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from operamind.infrastructure.postgres.migrations import MigrationCatalog, MigrationRunner

_SAFE_DATABASE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_OWNED_DATABASE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}_[0-9]{1,10}_[0-9a-f]{12}$")


class TemporaryPostgresDatabaseError(RuntimeError):
    """Raised when the isolated test database lifecycle cannot be completed safely."""


@dataclass(slots=True)
class TemporaryPostgresDatabase:
    """An owned PostgreSQL database that is always created from ``template0``."""

    seed_url: str
    database_name: str
    database_url: str
    applied_migrations: tuple[str, ...]
    _closed: bool = field(default=False, init=False, repr=False)

    @classmethod
    def create(
        cls,
        *,
        seed_url: str,
        migrations_root: Path,
        name_prefix: str = "operamind_pytest",
    ) -> TemporaryPostgresDatabase:
        """Create a sibling database, migrate it once, and return its isolated URL."""

        if not seed_url.strip():
            raise ValueError("PostgreSQL test seed URL must not be blank")
        database_name = _temporary_database_name(name_prefix)
        try:
            parameters = conninfo_to_dict(seed_url)
            database_url = make_conninfo("", **{**parameters, "dbname": database_name})
        except psycopg.Error as error:
            raise TemporaryPostgresDatabaseError(
                "PostgreSQL test seed URL is invalid"
            ) from error
        created = False
        try:
            with (
                psycopg.connect(seed_url, autocommit=True) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                        sql.Identifier(database_name)
                    )
                )
                created = True
            with psycopg.connect(database_url) as connection:
                applied = MigrationRunner(
                    connection,
                    MigrationCatalog.load(migrations_root),
                ).apply()
        except Exception as error:
            if created:
                try:
                    _drop_database(seed_url=seed_url, database_name=database_name)
                except Exception as cleanup_error:
                    raise TemporaryPostgresDatabaseError(
                        "Temporary PostgreSQL setup failed and cleanup also failed"
                    ) from cleanup_error
            raise TemporaryPostgresDatabaseError(
                "Could not create and migrate the temporary PostgreSQL test database"
            ) from error
        return cls(
            seed_url=seed_url,
            database_name=database_name,
            database_url=database_url,
            applied_migrations=applied,
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Force remaining clients off the owned database and remove it."""

        if self._closed:
            return
        try:
            _drop_database(seed_url=self.seed_url, database_name=self.database_name)
        except Exception as error:
            raise TemporaryPostgresDatabaseError(
                "Could not remove the temporary PostgreSQL test database"
            ) from error
        self._closed = True


def _temporary_database_name(prefix: str) -> str:
    normalized = prefix.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", normalized):
        raise ValueError("PostgreSQL test database prefix must use lowercase safe characters")
    suffix = f"{os.getpid()}_{secrets.token_hex(6)}"
    maximum_prefix_length = min(40, 63 - len(suffix) - 1)
    database_name = f"{normalized[:maximum_prefix_length]}_{suffix}"
    if _SAFE_DATABASE_NAME.fullmatch(database_name) is None:
        raise ValueError("Generated PostgreSQL test database name is invalid")
    return database_name


def _drop_database(*, seed_url: str, database_name: str) -> None:
    if _OWNED_DATABASE_NAME.fullmatch(database_name) is None:
        raise ValueError("Refusing to drop an unowned PostgreSQL database name")
    with (
        psycopg.connect(seed_url, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            )
        )
