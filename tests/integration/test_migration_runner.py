import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from operamind.infrastructure.postgres import MigrationCatalog, MigrationRunner
from operamind.infrastructure.postgres.migrations import MigrationIntegrityError

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


def create_isolated_schema(connection: psycopg.Connection[object]) -> str:
    schema_name = f"migration_test_{uuid4().hex}"
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
    return schema_name


def drop_isolated_schema(connection: psycopg.Connection[object], schema_name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET search_path TO public")
        cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_migrations_apply_once_and_record_checksum() -> None:
    assert DATABASE_URL is not None
    catalog = MigrationCatalog.load(ROOT / "migrations")
    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        first = MigrationRunner(connection, catalog).apply()
        second = MigrationRunner(connection, catalog).apply()
        with connection.cursor() as cursor:
            cursor.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version")
            rows = cursor.fetchall()
        drop_isolated_schema(connection, schema_name)

    assert first == (
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
    )
    assert second == ()
    assert rows == [
        ("0001", "p0_baseline", catalog.migrations[0].checksum),
        ("0002", "p1_canonical_documents", catalog.migrations[1].checksum),
        ("0003", "structured_change_reviews", catalog.migrations[2].checksum),
        (
            "0004",
            "structured_change_review_chain_guards",
            catalog.migrations[3].checksum,
        ),
        (
            "0005",
            "rag_document_nodes_and_search_index",
            catalog.migrations[4].checksum,
        ),
        (
            "0006",
            "document_ingestion_result_events",
            catalog.migrations[5].checksum,
        ),
        (
            "0007",
            "document_relation_builds",
            catalog.migrations[6].checksum,
        ),
        (
            "0008",
            "code_graph_snapshots",
            catalog.migrations[7].checksum,
        ),
        (
            "0009",
            "impact_reports_and_confirmations",
            catalog.migrations[8].checksum,
        ),
        (
            "0010",
            "edit_packets",
            catalog.migrations[9].checksum,
        ),
        (
            "0011",
            "edit_results",
            catalog.migrations[10].checksum,
        ),
        (
            "0012",
            "ui_verification",
            catalog.migrations[11].checksum,
        ),
        (
            "0013",
            "ui_browser_manifests",
            catalog.migrations[12].checksum,
        ),
        (
            "0014",
            "ui_preflight_attempts",
            catalog.migrations[13].checksum,
        ),
        (
            "0015",
            "ui_knowledge",
            catalog.migrations[14].checksum,
        ),
        (
            "0016",
            "ui_locator_observations",
            catalog.migrations[15].checksum,
        ),
        (
            "0017",
            "ui_knowledge_reviews",
            catalog.migrations[16].checksum,
        ),
        (
            "0018",
            "approval_grants",
            catalog.migrations[17].checksum,
        ),
        (
            "0019",
            "command_execution",
            catalog.migrations[18].checksum,
        ),
        (
            "0020",
            "edit_result_command_evidence",
            catalog.migrations[19].checksum,
        ),
        (
            "0021",
            "edit_result_evidence_state",
            catalog.migrations[20].checksum,
        ),
        (
            "0022",
            "quarantine_legacy_ui_plans",
            catalog.migrations[21].checksum,
        ),
        (
            "0023",
            "ui_plan_repository_binding",
            catalog.migrations[22].checksum,
        ),
        (
            "0024",
            "document_extractor_provenance",
            catalog.migrations[23].checksum,
        ),
        (
            "0025",
            "search_index_failure_audit",
            catalog.migrations[24].checksum,
        ),
        (
            "0026",
            "command_execution_recovery",
            catalog.migrations[25].checksum,
        ),
        (
            "0027",
            "relation_build_plan_digest",
            catalog.migrations[26].checksum,
        ),
        (
            "0028",
            "search_index_entry_ledger_digest",
            catalog.migrations[27].checksum,
        ),
        (
            "0029",
            "readiness_observations",
            catalog.migrations[28].checksum,
        ),
        (
            "0030",
            "web_control_plane",
            catalog.migrations[29].checksum,
        ),
        (
            "0031",
            "change_orchestrations",
            catalog.migrations[30].checksum,
        ),
        (
            "0032",
            "test_data_execution",
            catalog.migrations[31].checksum,
        ),
        (
            "0033",
            "change_closure_results",
            catalog.migrations[32].checksum,
        ),
        (
            "0034",
            "web_test_data_execution_control",
            catalog.migrations[33].checksum,
        ),
        (
            "0035",
            "ui_scenario_test_case_mapping",
            catalog.migrations[34].checksum,
        ),
        (
            "0036",
            "test_case_natural_language_revisions",
            catalog.migrations[35].checksum,
        ),
        (
            "0037",
            "test_case_execution_authorizations",
            catalog.migrations[36].checksum,
        ),
        (
            "0038",
            "ui_knowledge_review_evidence",
            catalog.migrations[37].checksum,
        ),
        (
            "0039",
            "test_case_revision_undo",
            catalog.migrations[38].checksum,
        ),
        (
            "0040",
            "change_automation_runs",
            catalog.migrations[39].checksum,
        ),
        (
            "0041",
            "change_request_case_bindings",
            catalog.migrations[40].checksum,
        ),
        (
            "0042",
            "code_graph_incremental_lineage",
            catalog.migrations[41].checksum,
        ),
        (
            "0043",
            "copilot_coding_task_bridge",
            catalog.migrations[42].checksum,
        ),
        (
            "0044",
            "copilot_bridge_recovery",
            catalog.migrations[43].checksum,
        ),
        (
            "0045",
            "runtime_route_evidence",
            catalog.migrations[44].checksum,
        ),
        (
            "0046",
            "unresolved_evidence_reports",
            catalog.migrations[45].checksum,
        ),
        (
            "0047",
            "agent_neutral_orchestration_tasks",
            catalog.migrations[46].checksum,
        ),
        (
            "0048",
            "orchestration_worker_registry",
            catalog.migrations[47].checksum,
        ),
        (
            "0049",
            "orchestration_worker_operations",
            catalog.migrations[48].checksum,
        ),
        (
            "0050",
            "orchestration_task_priority",
            catalog.migrations[49].checksum,
        ),
    ]


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_applied_migration_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    original = (ROOT / "migrations/0001_p0_baseline.sql").read_text(encoding="utf-8")
    (tmp_path / "0001_p0_baseline.sql").write_text(
        f"{original}\n-- forbidden rewrite\n", encoding="utf-8"
    )
    (tmp_path / "0002_p1_canonical_documents.sql").write_text(
        (ROOT / "migrations/0002_p1_canonical_documents.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0003_structured_change_reviews.sql").write_text(
        (ROOT / "migrations/0003_structured_change_reviews.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0004_structured_change_review_chain_guards.sql").write_text(
        (ROOT / "migrations/0004_structured_change_review_chain_guards.sql").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (tmp_path / "0005_rag_document_nodes_and_search_index.sql").write_text(
        (ROOT / "migrations/0005_rag_document_nodes_and_search_index.sql").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (tmp_path / "0006_document_ingestion_result_events.sql").write_text(
        (ROOT / "migrations/0006_document_ingestion_result_events.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0007_document_relation_builds.sql").write_text(
        (ROOT / "migrations/0007_document_relation_builds.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0008_code_graph_snapshots.sql").write_text(
        (ROOT / "migrations/0008_code_graph_snapshots.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0009_impact_reports_and_confirmations.sql").write_text(
        (ROOT / "migrations/0009_impact_reports_and_confirmations.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0010_edit_packets.sql").write_text(
        (ROOT / "migrations/0010_edit_packets.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0011_edit_results.sql").write_text(
        (ROOT / "migrations/0011_edit_results.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0012_ui_verification.sql").write_text(
        (ROOT / "migrations/0012_ui_verification.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0013_ui_browser_manifests.sql").write_text(
        (ROOT / "migrations/0013_ui_browser_manifests.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0014_ui_preflight_attempts.sql").write_text(
        (ROOT / "migrations/0014_ui_preflight_attempts.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0015_ui_knowledge.sql").write_text(
        (ROOT / "migrations/0015_ui_knowledge.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0016_ui_locator_observations.sql").write_text(
        (ROOT / "migrations/0016_ui_locator_observations.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0017_ui_knowledge_reviews.sql").write_text(
        (ROOT / "migrations/0017_ui_knowledge_reviews.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0018_approval_grants.sql").write_text(
        (ROOT / "migrations/0018_approval_grants.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0019_command_execution.sql").write_text(
        (ROOT / "migrations/0019_command_execution.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0020_edit_result_command_evidence.sql").write_text(
        (ROOT / "migrations/0020_edit_result_command_evidence.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0021_edit_result_evidence_state.sql").write_text(
        (ROOT / "migrations/0021_edit_result_evidence_state.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0022_quarantine_legacy_ui_plans.sql").write_text(
        (ROOT / "migrations/0022_quarantine_legacy_ui_plans.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0023_ui_plan_repository_binding.sql").write_text(
        (ROOT / "migrations/0023_ui_plan_repository_binding.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0024_document_extractor_provenance.sql").write_text(
        (ROOT / "migrations/0024_document_extractor_provenance.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0025_search_index_failure_audit.sql").write_text(
        (ROOT / "migrations/0025_search_index_failure_audit.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0026_command_execution_recovery.sql").write_text(
        (ROOT / "migrations/0026_command_execution_recovery.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0027_relation_build_plan_digest.sql").write_text(
        (ROOT / "migrations/0027_relation_build_plan_digest.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0028_search_index_entry_ledger_digest.sql").write_text(
        (ROOT / "migrations/0028_search_index_entry_ledger_digest.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0029_readiness_observations.sql").write_text(
        (ROOT / "migrations/0029_readiness_observations.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0030_web_control_plane.sql").write_text(
        (ROOT / "migrations/0030_web_control_plane.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0031_change_orchestrations.sql").write_text(
        (ROOT / "migrations/0031_change_orchestrations.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0032_test_data_execution.sql").write_text(
        (ROOT / "migrations/0032_test_data_execution.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0033_change_closure_results.sql").write_text(
        (ROOT / "migrations/0033_change_closure_results.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0034_web_test_data_execution_control.sql").write_text(
        (ROOT / "migrations/0034_web_test_data_execution_control.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0035_ui_scenario_test_case_mapping.sql").write_text(
        (ROOT / "migrations/0035_ui_scenario_test_case_mapping.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0036_test_case_natural_language_revisions.sql").write_text(
        (ROOT / "migrations/0036_test_case_natural_language_revisions.sql").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (tmp_path / "0037_test_case_execution_authorizations.sql").write_text(
        (ROOT / "migrations/0037_test_case_execution_authorizations.sql").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (tmp_path / "0038_ui_knowledge_review_evidence.sql").write_text(
        (ROOT / "migrations/0038_ui_knowledge_review_evidence.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0039_test_case_revision_undo.sql").write_text(
        (ROOT / "migrations/0039_test_case_revision_undo.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0040_change_automation_runs.sql").write_text(
        (ROOT / "migrations/0040_change_automation_runs.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0041_change_request_case_bindings.sql").write_text(
        (ROOT / "migrations/0041_change_request_case_bindings.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0042_code_graph_incremental_lineage.sql").write_text(
        (ROOT / "migrations/0042_code_graph_incremental_lineage.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0043_copilot_coding_task_bridge.sql").write_text(
        (ROOT / "migrations/0043_copilot_coding_task_bridge.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0044_copilot_bridge_recovery.sql").write_text(
        (ROOT / "migrations/0044_copilot_bridge_recovery.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0045_runtime_route_evidence.sql").write_text(
        (ROOT / "migrations/0045_runtime_route_evidence.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "0046_unresolved_evidence_reports.sql").write_text(
        (ROOT / "migrations/0046_unresolved_evidence_reports.sql").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (tmp_path / "0047_agent_neutral_orchestration_tasks.sql").write_text(
        (ROOT / "migrations/0047_agent_neutral_orchestration_tasks.sql").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    for version in (
        "0048_orchestration_worker_registry.sql",
        "0049_orchestration_worker_operations.sql",
        "0050_orchestration_task_priority.sql",
    ):
        (tmp_path / version).write_text(
            (ROOT / "migrations" / version).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    tampered_catalog = MigrationCatalog.load(tmp_path)

    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        MigrationRunner(
            connection,
            MigrationCatalog.load(ROOT / "migrations"),
        ).apply()
        with pytest.raises(MigrationIntegrityError, match="Checksum mismatch"):
            MigrationRunner(connection, tampered_catalog).apply()
        drop_isolated_schema(connection, schema_name)


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_preflight_attempt_migration_backfills_existing_checks() -> None:
    assert DATABASE_URL is not None
    migration = (ROOT / "migrations/0014_ui_preflight_attempts.sql").read_text(encoding="utf-8")
    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE ui_execution_plans (
                    ui_execution_plan_id text PRIMARY KEY,
                    project_id text NOT NULL,
                    status text NOT NULL,
                    CONSTRAINT ui_execution_plans_scope_identity_unique UNIQUE (
                        ui_execution_plan_id, project_id
                    )
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE ui_preflight_checks (
                    ui_preflight_check_id text PRIMARY KEY,
                    ui_execution_plan_id text NOT NULL,
                    project_id text NOT NULL,
                    check_type text NOT NULL,
                    status text NOT NULL,
                    evidence_ref text,
                    reason text,
                    CONSTRAINT ui_preflight_checks_type_unique UNIQUE (
                        ui_execution_plan_id, check_type
                    )
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO ui_execution_plans (
                    ui_execution_plan_id, project_id, status
                ) VALUES ('plan-legacy', 'project-legacy', 'blocked')
                """
            )
            for check_type in (
                "environment",
                "authentication",
                "test_data",
                "trigger_path",
                "locator",
            ):
                status = "blocked" if check_type == "environment" else "passed"
                reason = "target unavailable" if status == "blocked" else None
                cursor.execute(
                    """
                    INSERT INTO ui_preflight_checks (
                        ui_preflight_check_id, ui_execution_plan_id, project_id,
                        check_type, status, reason
                    ) VALUES (%s, 'plan-legacy', 'project-legacy', %s, %s, %s)
                    """,
                    (f"check-{check_type}", check_type, status, reason),
                )
            cursor.execute(migration)
            cursor.execute(
                """
                SELECT status, blocking_reasons FROM ui_preflight_attempts
                WHERE ui_execution_plan_id = 'plan-legacy'
                """
            )
            attempt = cursor.fetchone()
            cursor.execute(
                """
                SELECT count(DISTINCT ui_preflight_attempt_id), count(*)
                FROM ui_preflight_checks WHERE ui_execution_plan_id = 'plan-legacy'
                """
            )
            migrated_checks = cursor.fetchone()
        drop_isolated_schema(connection, schema_name)

    assert attempt == ("blocked", ["environment:blocked:target unavailable"])
    assert migrated_checks == (1, 5)


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_edit_result_evidence_state_marks_unproven_legacy_records() -> None:
    assert DATABASE_URL is not None
    migration = (ROOT / "migrations/0021_edit_result_evidence_state.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE edit_results (
                    edit_result_id text PRIMARY KEY,
                    project_id text NOT NULL,
                    validation_mode text NOT NULL,
                    test_result_refs jsonb NOT NULL,
                    tests_passed boolean
                );
                CREATE TABLE command_execution_results (
                    command_execution_id text PRIMARY KEY,
                    status text NOT NULL
                );
                CREATE TABLE edit_result_command_executions (
                    edit_result_id text NOT NULL,
                    command_execution_id text NOT NULL,
                    project_id text NOT NULL
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO edit_results VALUES
                    ('working', 'project', 'working', '[]', NULL),
                    ('legacy', 'project', 'committed', '["missing-command"]', true),
                    ('verified-pass', 'project', 'committed', '["command-pass"]', true),
                    ('verified-fail', 'project', 'committed', '["command-fail"]', false);
                INSERT INTO command_execution_results VALUES
                    ('command-pass', 'passed'),
                    ('command-fail', 'failed');
                INSERT INTO edit_result_command_executions VALUES
                    ('verified-pass', 'command-pass', 'project'),
                    ('verified-fail', 'command-fail', 'project')
                """
            )
            cursor.execute(migration)
            cursor.execute(
                """
                SELECT edit_result_id, command_evidence_status
                FROM edit_results ORDER BY edit_result_id
                """
            )
            states = cursor.fetchall()
        drop_isolated_schema(connection, schema_name)

    assert states == [
        ("legacy", "legacy_unverified"),
        ("verified-fail", "verified"),
        ("verified-pass", "verified"),
        ("working", "not_applicable"),
    ]


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_legacy_command_evidence_quarantines_unfinished_ui_work() -> None:
    assert DATABASE_URL is not None
    migration = (ROOT / "migrations/0022_quarantine_legacy_ui_plans.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE edit_results (
                    edit_result_id text PRIMARY KEY,
                    project_id text NOT NULL,
                    command_evidence_status text NOT NULL
                );
                CREATE TABLE ui_execution_plans (
                    ui_execution_plan_id text PRIMARY KEY,
                    edit_result_id text NOT NULL,
                    project_id text NOT NULL,
                    status text NOT NULL,
                    blocking_reasons jsonb NOT NULL
                );
                CREATE TABLE ui_execution_runs (
                    ui_execution_run_id text PRIMARY KEY,
                    ui_execution_plan_id text NOT NULL,
                    project_id text NOT NULL,
                    status text NOT NULL,
                    completed_at timestamptz
                );
                INSERT INTO edit_results VALUES
                    ('legacy-result', 'project', 'legacy_unverified');
                INSERT INTO ui_execution_plans VALUES
                    ('ready-plan', 'legacy-result', 'project', 'ready', '[]'),
                    ('completed-plan', 'legacy-result', 'project', 'completed', '[]');
                INSERT INTO ui_execution_runs VALUES
                    ('running-run', 'ready-plan', 'project', 'running', NULL)
                """
            )
            cursor.execute(migration)
            cursor.execute(
                """
                SELECT ui_execution_plan_id, status, blocking_reasons
                FROM ui_execution_plans ORDER BY ui_execution_plan_id
                """
            )
            plans = cursor.fetchall()
            cursor.execute(
                """
                SELECT status, completed_at IS NOT NULL
                FROM ui_execution_runs WHERE ui_execution_run_id = 'running-run'
                """
            )
            run = cursor.fetchone()
        drop_isolated_schema(connection, schema_name)

    assert plans == [
        ("completed-plan", "completed", []),
        (
            "ready-plan",
            "blocked",
            ["edit_result_command_evidence:legacy_unverified"],
        ),
    ]
    assert run == ("blocked", True)


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_ui_plan_repository_binding_migration_quarantines_invalid_legacy_plans() -> None:
    assert DATABASE_URL is not None
    migration = (ROOT / "migrations/0023_ui_plan_repository_binding.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE edit_results (
                    edit_result_id text PRIMARY KEY,
                    project_id text NOT NULL,
                    result_repository_revision text NOT NULL
                );
                CREATE TABLE ui_deployments (
                    environment_id text NOT NULL,
                    deployment_revision text NOT NULL,
                    project_id text NOT NULL,
                    repository_revision text NOT NULL
                );
                CREATE TABLE ui_execution_plans (
                    ui_execution_plan_id text PRIMARY KEY,
                    edit_result_id text NOT NULL,
                    project_id text NOT NULL,
                    environment_id text NOT NULL,
                    deployment_revision text NOT NULL,
                    repository_revision text NOT NULL,
                    status text NOT NULL,
                    blocking_reasons jsonb NOT NULL
                );
                CREATE TABLE ui_execution_runs (
                    ui_execution_run_id text PRIMARY KEY,
                    ui_execution_plan_id text NOT NULL,
                    project_id text NOT NULL,
                    status text NOT NULL,
                    completed_at timestamptz
                );
                INSERT INTO edit_results VALUES
                    ('result', 'project', 'commit-result');
                INSERT INTO ui_deployments VALUES
                    ('environment', 'deployment', 'project', 'commit-result');
                INSERT INTO ui_execution_plans VALUES
                    ('valid-plan', 'result', 'project', 'environment', 'deployment',
                     'commit-result', 'ready', '[]'),
                    ('invalid-plan', 'result', 'project', 'environment', 'deployment',
                     'verified', 'ready', '[]'),
                    ('invalid-completed-plan', 'result', 'project', 'environment', 'deployment',
                     'verified', 'completed', '[]');
                INSERT INTO ui_execution_runs VALUES
                    ('invalid-run', 'invalid-plan', 'project', 'running', NULL)
                """
            )
            cursor.execute(migration)
            cursor.execute(
                """
                SELECT ui_execution_plan_id, repository_binding_status,
                       status, blocking_reasons
                FROM ui_execution_plans ORDER BY ui_execution_plan_id
                """
            )
            plans = cursor.fetchall()
            cursor.execute(
                """
                SELECT status, completed_at IS NOT NULL
                FROM ui_execution_runs WHERE ui_execution_run_id = 'invalid-run'
                """
            )
            run = cursor.fetchone()
        drop_isolated_schema(connection, schema_name)

    assert plans == [
        ("invalid-completed-plan", "legacy_invalid", "completed", []),
        (
            "invalid-plan",
            "legacy_invalid",
            "blocked",
            ["ui_plan_repository_binding:legacy_invalid"],
        ),
        ("valid-plan", "verified", "ready", []),
    ]
    assert run == ("blocked", True)


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_locator_observation_migration_upgrades_candidate_identity_with_existing_data() -> None:
    assert DATABASE_URL is not None
    catalog = MigrationCatalog.load(ROOT / "migrations")
    through_ui_knowledge = MigrationCatalog(catalog.migrations[:15])
    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        MigrationRunner(connection, through_ui_knowledge).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES ('project-ui', 'UI Project')"
            )
            cursor.execute(
                """
                INSERT INTO ui_environments (
                    environment_id, project_id, base_url, status
                ) VALUES ('environment-ui', 'project-ui', 'http://127.0.0.1:8080', 'active')
                """
            )
            cursor.execute(
                """
                INSERT INTO ui_deployments (
                    deployment_revision, environment_id, project_id,
                    repository_revision, status
                ) VALUES (
                    'deployment-ui', 'environment-ui', 'project-ui', 'commit-ui', 'ready'
                )
                """
            )
            _insert_ui_knowledge_fixture(cursor, "knowledge-v1", "1.0.0")
        applied = MigrationRunner(connection, catalog).apply()
        with connection.cursor() as cursor:
            _insert_ui_knowledge_fixture(cursor, "knowledge-v2", "1.1.0")
            cursor.execute(
                """
                SELECT ui_knowledge_snapshot_id, locator_candidate_id
                FROM ui_locator_candidates
                WHERE locator_candidate_id = 'shared-status-label'
                ORDER BY ui_knowledge_snapshot_id
                """
            )
            candidates = cursor.fetchall()
        drop_isolated_schema(connection, schema_name)

    assert applied == (
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
    )
    assert candidates == [
        ("knowledge-v1", "shared-status-label"),
        ("knowledge-v2", "shared-status-label"),
    ]


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_document_extractor_provenance_migration_marks_legacy_versions() -> None:
    assert DATABASE_URL is not None
    migration = (ROOT / "migrations/0024_document_extractor_provenance.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE document_versions (
                    document_version_id text PRIMARY KEY
                )
                """
            )
            cursor.execute("INSERT INTO document_versions VALUES ('legacy-document-version')")
            cursor.execute(migration)
            cursor.execute(
                """
                SELECT extractor_ref, is_nullable
                FROM document_versions
                JOIN information_schema.columns
                  ON table_schema = current_schema()
                 AND table_name = 'document_versions'
                 AND column_name = 'extractor_ref'
                WHERE document_version_id = 'legacy-document-version'
                """
            )
            row = cursor.fetchone()
        drop_isolated_schema(connection, schema_name)

    assert row == ("legacy-unversioned@0", "NO")


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_search_index_failure_audit_migration_marks_legacy_failures() -> None:
    assert DATABASE_URL is not None
    migration = (ROOT / "migrations/0025_search_index_failure_audit.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE search_index_builds (
                    search_index_build_id text PRIMARY KEY,
                    status text NOT NULL,
                    failure_reason text,
                    completed_at timestamptz
                );
                INSERT INTO search_index_builds VALUES
                    ('failed-blank', 'failed', '', now()),
                    ('failed-build', 'failed', 'legacy provider failure', now()),
                    ('ready-build', 'ready', NULL, now()),
                    ('building-build', 'building', NULL, NULL)
                """
            )
            cursor.execute(migration)
            cursor.execute(
                """
                SELECT search_index_build_id, failure_event_id, failure_kind,
                       failure_actor, failure_reason, failure_stale_before
                FROM search_index_builds ORDER BY search_index_build_id
                """
            )
            rows = cursor.fetchall()
        drop_isolated_schema(connection, schema_name)

    assert rows == [
        ("building-build", None, None, None, None, None),
        (
            "failed-blank",
            "legacy-search-index-failure:failed-blank",
            "legacy_unversioned",
            "migration-0025",
            "legacy-unversioned failure reason unavailable",
            None,
        ),
        (
            "failed-build",
            "legacy-search-index-failure:failed-build",
            "legacy_unversioned",
            "migration-0025",
            "legacy provider failure",
            None,
        ),
        ("ready-build", None, None, None, None, None),
    ]


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_command_execution_recovery_migration_preserves_results_and_requires_audit() -> None:
    assert DATABASE_URL is not None
    migration = (ROOT / "migrations/0026_command_execution_recovery.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(DATABASE_URL) as connection:
        schema_name = create_isolated_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE command_execution_results (
                    command_execution_id text PRIMARY KEY,
                    status text NOT NULL,
                    exit_code integer,
                    CONSTRAINT command_execution_results_status_valid CHECK (
                        status IN ('passed', 'failed', 'timed_out', 'launch_failed')
                    ),
                    CONSTRAINT command_execution_results_exit_consistent CHECK (
                        (status IN ('passed', 'failed') AND exit_code IS NOT NULL)
                        OR (status IN ('timed_out', 'launch_failed') AND exit_code IS NULL)
                    )
                );
                INSERT INTO command_execution_results VALUES ('passed-command', 'passed', 0)
                """
            )
            cursor.execute(migration)
            cursor.execute(
                """
                INSERT INTO command_execution_results (
                    command_execution_id, status, exit_code, recovery_id,
                    recovery_actor, recovery_reason, recovery_stale_before
                ) VALUES (
                    'interrupted-command', 'interrupted', NULL, 'recovery-1',
                    'operator', 'worker interrupted', '2026-07-16T12:00:00Z'
                )
                """
            )
            cursor.execute(
                """
                SELECT command_execution_id, status, recovery_id
                FROM command_execution_results ORDER BY command_execution_id
                """
            )
            rows = cursor.fetchall()
        drop_isolated_schema(connection, schema_name)

    assert rows == [
        ("interrupted-command", "interrupted", "recovery-1"),
        ("passed-command", "passed", None),
    ]


def _insert_ui_knowledge_fixture(
    cursor: psycopg.Cursor[object],
    snapshot_id: str,
    version: str,
) -> None:
    cursor.execute(
        """
        INSERT INTO ui_knowledge_snapshots (
            ui_knowledge_snapshot_id, project_id, environment_id,
            deployment_revision, snapshot_version, review_status,
            payload_digest, is_active
        ) VALUES (
            %s, 'project-ui', 'environment-ui', 'deployment-ui',
            %s, 'draft', %s, false
        )
        """,
        (snapshot_id, version, "a" * 64),
    )
    cursor.execute(
        """
        INSERT INTO ui_knowledge_targets (
            ui_knowledge_snapshot_id, project_id, target_ref,
            business_name, screen_name, trigger_path, source_fact_refs
        ) VALUES (
            %s, 'project-ui', 'expense.status-filter',
            'Status filter', 'Expenses', '/expenses', '["fact-status"]'::jsonb
        )
        """,
        (snapshot_id,),
    )
    cursor.execute(
        """
        INSERT INTO ui_locator_candidates (
            locator_candidate_id, ui_knowledge_snapshot_id, project_id,
            target_ref, strategy, locator_value, exact_match,
            priority, reliability_score, source
        ) VALUES (
            'shared-status-label', %s, 'project-ui', 'expense.status-filter',
            'label', 'Status', true, 1, 0.9, 'fixture'
        )
        """,
        (snapshot_id,),
    )
