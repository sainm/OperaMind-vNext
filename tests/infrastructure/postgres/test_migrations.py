from pathlib import Path

import pytest

from operamind.infrastructure.postgres.migrations import MigrationCatalog, MigrationError

ROOT = Path(__file__).parents[3]


def test_repository_migrations_are_sequential_and_transaction_free() -> None:
    catalog = MigrationCatalog.load(ROOT / "migrations")

    assert [migration.version for migration in catalog.migrations] == [
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0010",
        "0011",
        "0012",
        "0013",
        "0014",
        "0015",
        "0016",
        "0017",
        "0018",
        "0019",
        "0020",
        "0021",
        "0022",
        "0023",
        "0024",
        "0025",
        "0026",
        "0027",
        "0028",
        "0029",
        "0030",
        "0031",
        "0032",
        "0033",
        "0034",
        "0035",
        "0036",
        "0037",
        "0038",
        "0039",
        "0040",
        "0041",
        "0042",
        "0043",
        "0044",
        "0045",
        "0046",
        "0047",
        "0048",
        "0049",
        "0050",
        "0051",
        "0052",
        "0053",
        "0054",
        "0055",
        "0056",
    ]
    assert all(len(migration.checksum) == 64 for migration in catalog.migrations)


def test_migration_cannot_control_its_own_transaction(tmp_path: Path) -> None:
    (tmp_path / "0001_invalid.sql").write_text("BEGIN;\nSELECT 1;\nCOMMIT;\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="must not manage transactions"):
        MigrationCatalog.load(tmp_path)
