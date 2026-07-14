"""Discover and apply immutable PostgreSQL migrations."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
TRANSACTION_CONTROL = re.compile(r"(?im)^\s*(begin|commit|rollback)\s*;")
MIGRATION_LOCK_NAME = "operamind.schema_migrations"


class MigrationError(RuntimeError):
    """Base error for migration discovery and application failures."""


class MigrationIntegrityError(MigrationError):
    """Raised when an applied migration differs from the immutable source file."""


@dataclass(frozen=True, slots=True)
class Migration:
    """An immutable SQL migration loaded from disk."""

    version: str
    name: str
    path: Path
    sql: str
    checksum: str


@dataclass(frozen=True, slots=True)
class MigrationCatalog:
    """Ordered collection of migration files with deterministic checksums."""

    migrations: tuple[Migration, ...]

    @classmethod
    def load(cls, migrations_root: Path) -> MigrationCatalog:
        """Load sequential `NNNN_name.sql` files and reject embedded transactions."""

        root = migrations_root.resolve()
        if not root.is_dir():
            raise MigrationError(f"Migration directory does not exist: {root}")

        migrations: list[Migration] = []
        for path in sorted(root.glob("*.sql")):
            match = MIGRATION_NAME.fullmatch(path.name)
            if match is None:
                raise MigrationError(f"Invalid migration filename: {path.name}")
            sql = path.read_text(encoding="utf-8")
            if TRANSACTION_CONTROL.search(sql):
                raise MigrationError(
                    f"Migration must not manage transactions directly: {path.name}"
                )
            migrations.append(
                Migration(
                    version=match.group("version"),
                    name=match.group("name"),
                    path=path,
                    sql=sql,
                    checksum=hashlib.sha256(sql.encode()).hexdigest(),
                )
            )

        if not migrations:
            raise MigrationError(f"No migration files found in: {root}")
        versions = [migration.version for migration in migrations]
        if len(versions) != len(set(versions)):
            raise MigrationError("Migration versions must be unique")
        expected = [f"{index:04d}" for index in range(1, len(migrations) + 1)]
        if versions != expected:
            raise MigrationError(f"Migration versions must be sequential: expected {expected}")
        return cls(tuple(migrations))


class MigrationRunner:
    """Apply pending migrations atomically under a PostgreSQL advisory lock."""

    def __init__(self, connection: Connection[Any], catalog: MigrationCatalog) -> None:
        self._connection = connection
        self._catalog = catalog

    def apply(self) -> tuple[str, ...]:
        """Apply pending migrations and return the versions applied in this call."""

        applied_now: list[str] = []
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (MIGRATION_LOCK_NAME,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    name text NOT NULL,
                    checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute("SELECT version, checksum FROM schema_migrations")
            applied = {str(version): str(checksum) for version, checksum in cursor.fetchall()}
            known_versions = {migration.version for migration in self._catalog.migrations}
            unknown_versions = sorted(set(applied) - known_versions)
            if unknown_versions:
                raise MigrationIntegrityError(
                    f"Database contains unknown migration versions: {unknown_versions}"
                )

            for migration in self._catalog.migrations:
                applied_checksum = applied.get(migration.version)
                if applied_checksum is not None:
                    if applied_checksum != migration.checksum:
                        raise MigrationIntegrityError(
                            f"Checksum mismatch for applied migration {migration.version}"
                        )
                    continue
                cursor.execute(migration.sql)
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (version, name, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.name, migration.checksum),
                )
                applied_now.append(migration.version)
        return tuple(applied_now)
