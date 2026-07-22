from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import conninfo, sql

from operamind.application.local_environment_diagnostics import (
    LocalEnvironmentDiagnosticsService,
)
from operamind.infrastructure.postgres import MigrationCatalog, MigrationRunner

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_diagnostics_check_real_postgresql_and_current_migration_catalog() -> None:
    assert DATABASE_URL is not None
    schema_name = f"diagnostic_test_{uuid4().hex}"
    with (
        psycopg.connect(DATABASE_URL, autocommit=True) as admin,
        admin.cursor() as cursor,
    ):
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
    schema_url = conninfo.make_conninfo(DATABASE_URL, options=f"-csearch_path={schema_name}")
    try:
        catalog = MigrationCatalog.load(ROOT / "migrations")
        with psycopg.connect(schema_url) as connection:
            MigrationRunner(connection, catalog).apply()
        service = LocalEnvironmentDiagnosticsService(
            repository_root=ROOT,
            database_url=schema_url,
            bridge_enabled=False,
        )

        result = service.inspect()
        checks = {item["check_id"]: item for item in result["checks"]}  # type: ignore[union-attr]

        assert checks["postgresql_connection"]["status"] == "passed"
        assert checks["migration"]["status"] == "passed"
        assert checks["migration"]["code"] == "migration_current"
    finally:
        with (
            psycopg.connect(DATABASE_URL, autocommit=True) as admin,
            admin.cursor() as cursor,
        ):
            statement = sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            cursor.execute(statement)
