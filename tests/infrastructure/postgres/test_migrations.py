from pathlib import Path

import pytest

from operamind.infrastructure.postgres.migrations import MigrationCatalog, MigrationError

ROOT = Path(__file__).parents[3]


def test_repository_migrations_are_sequential_and_transaction_free() -> None:
    catalog = MigrationCatalog.load(ROOT / "migrations")

    assert [migration.version for migration in catalog.migrations] == ["0001"]
    assert all(len(migration.checksum) == 64 for migration in catalog.migrations)


def test_migration_cannot_control_its_own_transaction(tmp_path: Path) -> None:
    (tmp_path / "0001_invalid.sql").write_text("BEGIN;\nSELECT 1;\nCOMMIT;\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="must not manage transactions"):
        MigrationCatalog.load(tmp_path)
