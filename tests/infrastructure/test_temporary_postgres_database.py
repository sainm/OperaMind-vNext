from __future__ import annotations

from pathlib import Path

import pytest

from operamind.testing import (
    TemporaryPostgresDatabase,
    TemporaryPostgresDatabaseError,
)


@pytest.mark.parametrize("prefix", ["", "UPPERCASE", "has-dash", "1starts_with_number"])
def test_create_rejects_unsafe_database_prefix(prefix: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prefix"):
        TemporaryPostgresDatabase.create(
            seed_url="postgresql:///postgres",
            migrations_root=tmp_path,
            name_prefix=prefix,
        )


def test_create_rejects_invalid_seed_url_without_exposing_it(tmp_path: Path) -> None:
    secret = "must-not-be-reported"

    with pytest.raises(TemporaryPostgresDatabaseError) as raised:
        TemporaryPostgresDatabase.create(
            seed_url=f"not a conninfo password={secret}",
            migrations_root=tmp_path,
        )

    assert secret not in str(raised.value)


def test_close_refuses_database_not_created_by_fixture() -> None:
    database = TemporaryPostgresDatabase(
        seed_url="postgresql:///postgres",
        database_name="operamind",
        database_url="postgresql:///operamind",
        applied_migrations=(),
    )

    with pytest.raises(TemporaryPostgresDatabaseError, match="Could not remove"):
        database.close()

    assert database.closed is False
