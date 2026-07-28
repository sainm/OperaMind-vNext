from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from operamind.infrastructure.postgres import MigrationCatalog
from operamind.testing.postgres import (
    TemporaryPostgresDatabase,
    TemporaryPostgresDatabaseError,
)

ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.integration


def test_session_database_is_fresh_and_fully_migrated(
    temporary_postgres_database: TemporaryPostgresDatabase,
) -> None:
    database = temporary_postgres_database
    catalog = MigrationCatalog.load(ROOT / "migrations")

    with psycopg.connect(database.database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = tuple(str(row[0]) for row in cursor.fetchall())
        cursor.execute("SELECT to_regclass('public.fixture_pollution')")
        pollution = cursor.fetchone()

    assert versions == tuple(migration.version for migration in catalog.migrations)
    assert database.applied_migrations == versions
    assert pollution == (None,)


def test_nested_database_does_not_clone_dirty_seed_and_is_removed(
    temporary_postgres_database: TemporaryPostgresDatabase,
) -> None:
    seed_url = temporary_postgres_database.seed_url
    dirty = TemporaryPostgresDatabase.create(
        seed_url=seed_url,
        migrations_root=ROOT / "migrations",
        name_prefix="operamind_dirty_seed",
    )
    isolated: TemporaryPostgresDatabase | None = None
    try:
        with psycopg.connect(dirty.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("CREATE TABLE fixture_pollution (id integer PRIMARY KEY)")
            cursor.execute("INSERT INTO fixture_pollution (id) VALUES (1)")

        isolated = TemporaryPostgresDatabase.create(
            seed_url=dirty.database_url,
            migrations_root=ROOT / "migrations",
            name_prefix="operamind_isolated",
        )
        with psycopg.connect(isolated.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.fixture_pollution')")
            assert cursor.fetchone() == (None,)
            cursor.execute("SELECT count(*) FROM schema_migrations")
            migration_count = len(MigrationCatalog.load(ROOT / "migrations").migrations)
            assert cursor.fetchone() == (migration_count,)

        isolated_name = isolated.database_name
        active_connection = psycopg.connect(isolated.database_url)
        isolated.close()
        with pytest.raises(psycopg.Error):
            active_connection.execute("SELECT 1")
        active_connection.close()
        isolated.close()
        with (
            psycopg.connect(seed_url, autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (isolated_name,))
            assert cursor.fetchone() is None
    finally:
        if isolated is not None and not isolated.closed:
            isolated.close()
        dirty_name = dirty.database_name
        dirty.close()

    with psycopg.connect(seed_url, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dirty_name,))
        assert cursor.fetchone() is None


def test_failed_migration_does_not_leave_database(
    temporary_postgres_database: TemporaryPostgresDatabase,
    tmp_path: Path,
) -> None:
    seed_url = temporary_postgres_database.seed_url
    prefix = "operamind_failed_setup"
    with psycopg.connect(seed_url, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s ORDER BY datname",
            (f"{prefix}_%",),
        )
        before = cursor.fetchall()

    with pytest.raises(TemporaryPostgresDatabaseError, match="create and migrate"):
        TemporaryPostgresDatabase.create(
            seed_url=seed_url,
            migrations_root=tmp_path,
            name_prefix=prefix,
        )

    with psycopg.connect(seed_url, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s ORDER BY datname",
            (f"{prefix}_%",),
        )
        assert cursor.fetchall() == before
