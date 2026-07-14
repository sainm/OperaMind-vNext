import os
from pathlib import Path

import psycopg
import pytest

from operamind.infrastructure.postgres import MigrationCatalog, MigrationRunner
from operamind.infrastructure.postgres.migrations import MigrationIntegrityError

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_migrations_apply_once_and_record_checksum() -> None:
    assert DATABASE_URL is not None
    catalog = MigrationCatalog.load(ROOT / "migrations")
    with psycopg.connect(DATABASE_URL) as connection:
        first = MigrationRunner(connection, catalog).apply()
        second = MigrationRunner(connection, catalog).apply()
        with connection.cursor() as cursor:
            cursor.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version")
            rows = cursor.fetchall()

    assert first == ("0001",)
    assert second == ()
    assert rows == [("0001", "p0_baseline", catalog.migrations[0].checksum)]


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_applied_migration_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    original = (ROOT / "migrations/0001_p0_baseline.sql").read_text(encoding="utf-8")
    (tmp_path / "0001_p0_baseline.sql").write_text(
        f"{original}\n-- forbidden rewrite\n", encoding="utf-8"
    )
    tampered_catalog = MigrationCatalog.load(tmp_path)

    with (
        psycopg.connect(DATABASE_URL) as connection,
        pytest.raises(MigrationIntegrityError, match="Checksum mismatch"),
    ):
        MigrationRunner(connection, tampered_catalog).apply()
