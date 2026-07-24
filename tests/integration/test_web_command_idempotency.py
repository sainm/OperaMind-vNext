from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from operamind.infrastructure.postgres import MigrationCatalog, MigrationRunner
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.web_command_repository import WebCommandRepository

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_web_command_receipt_replays_exact_response_and_rejects_payload_drift() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    calls: list[str] = []
    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        repository = WebCommandRepository(connection)

        def operation() -> dict[str, object]:
            calls.append("called")
            return {"created": True, "receipt": suffix}

        first = repository.execute(
            command_scope=f"test:create:{suffix}",
            idempotency_key="same-key",
            actor="integration-reviewer",
            payload={"value": 1},
            operation=operation,
        )
        replay = repository.execute(
            command_scope=f"test:create:{suffix}",
            idempotency_key="same-key",
            actor="integration-reviewer",
            payload={"value": 1},
            operation=operation,
        )

        assert first == replay == {"created": True, "receipt": suffix}
        assert calls == ["called"]
        with pytest.raises(PersistenceConflictError, match="different request content"):
            repository.execute(
                command_scope=f"test:create:{suffix}",
                idempotency_key="same-key",
                actor="integration-reviewer",
                payload={"value": 2},
                operation=operation,
            )


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_failed_web_command_does_not_leave_an_incomplete_receipt() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        repository = WebCommandRepository(connection)

        def fail() -> dict[str, object]:
            raise RuntimeError("simulated command failure")

        with pytest.raises(RuntimeError, match="simulated command failure"):
            repository.execute(
                command_scope=f"test:rollback:{suffix}",
                idempotency_key="retry-key",
                actor="integration-reviewer",
                payload={},
                operation=fail,
            )
        completed = repository.execute(
            command_scope=f"test:rollback:{suffix}",
            idempotency_key="retry-key",
            actor="integration-reviewer",
            payload={},
            operation=lambda: {"created": True},
        )

        assert completed == {"created": True}
