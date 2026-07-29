"""Repository-wide pytest lifecycle hooks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.postgres import (
    TemporaryPostgresDatabase,
    TemporaryPostgresDatabaseError,
)

ROOT = Path(__file__).parents[1]


@dataclass(slots=True)
class _TemporaryDatabaseState:
    database: TemporaryPostgresDatabase
    original_url: str


_DATABASE_STATE: pytest.StashKey[_TemporaryDatabaseState] = pytest.StashKey()


def pytest_configure(config: pytest.Config) -> None:
    """Replace an explicitly configured shared DB with a fresh migrated sibling."""

    seed_url = os.getenv("OPERAMIND_TEST_DATABASE_URL")
    if seed_url is None:
        return
    try:
        database = TemporaryPostgresDatabase.create(
            seed_url=seed_url,
            migrations_root=ROOT / "migrations",
        )
    except (OSError, ValueError, TemporaryPostgresDatabaseError) as error:
        raise pytest.UsageError(
            "Could not initialize isolated PostgreSQL integration tests"
        ) from error
    config.stash[_DATABASE_STATE] = _TemporaryDatabaseState(
        database=database,
        original_url=seed_url,
    )
    os.environ["OPERAMIND_TEST_DATABASE_URL"] = database.database_url


def pytest_report_header(config: pytest.Config) -> str | None:
    state = config.stash.get(_DATABASE_STATE, None)
    if state is None:
        return None
    return (
        "PostgreSQL isolation: created and migrated "
        f"{state.database.database_name} ({len(state.database.applied_migrations)} migrations)"
    )


@pytest.fixture(scope="session")
def temporary_postgres_database(
    request: pytest.FixtureRequest,
) -> TemporaryPostgresDatabase:
    """Expose the session-owned DB without making integration tests depend on it."""

    state = request.config.stash.get(_DATABASE_STATE, None)
    if state is None:
        pytest.skip("OPERAMIND_TEST_DATABASE_URL is not set")
    return state.database


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Remove the session database and turn cleanup failure into a failed run."""

    del exitstatus
    state = session.config.stash.get(_DATABASE_STATE, None)
    if state is None:
        return
    try:
        state.database.close()
    except TemporaryPostgresDatabaseError as error:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep("!", f"PostgreSQL cleanup failed: {error}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    finally:
        os.environ["OPERAMIND_TEST_DATABASE_URL"] = state.original_url
