import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from operamind.infrastructure.postgres import (
    MigrationCatalog,
    MigrationRunner,
    PersistenceConflictError,
    ReadinessEvidenceRepository,
)

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_full_regression_observation_round_trip_and_replay() -> None:
    assert DATABASE_URL is not None
    schema_name = f"readiness_test_{uuid4().hex}"
    observed_at = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    subject: dict[str, object] = {
        "source_tree_algorithm": "operamind-source-tree-v1",
        "source_tree_sha256": "a" * 64,
        "test_command": [".venv/bin/pytest", "-q"],
        "excluded_tests": [],
        "collected": 3,
        "passed": 3,
        "failed": 0,
        "skipped": 0,
        "database_version": "PostgreSQL test",
        "browser_version": "Chromium test",
    }
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
            cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        repository = ReadinessEvidenceRepository(connection)

        created = repository.record_observation(
            observation_id="full-regression-a",
            gate_id="full_local_regression",
            evidence_type="test_report",
            project_id=None,
            analysis_case_id=None,
            observed_at=observed_at,
            review_status="verified",
            reviewed_by=("automation:operamind-readiness",),
            subject=subject,
        )
        replayed = repository.record_observation(
            observation_id="full-regression-a",
            gate_id="full_local_regression",
            evidence_type="test_report",
            project_id=None,
            analysis_case_id=None,
            observed_at=observed_at,
            review_status="verified",
            reviewed_by=("automation:operamind-readiness",),
            subject=subject,
        )
        loaded = repository.full_regression()

        assert created
        assert not replayed
        assert loaded is not None
        assert loaded.evidence_id == "full-regression-a"
        assert loaded.subject == subject
        with pytest.raises(PersistenceConflictError):
            repository.record_observation(
                observation_id="full-regression-a",
                gate_id="full_local_regression",
                evidence_type="test_report",
                project_id=None,
                analysis_case_id=None,
                observed_at=observed_at,
                review_status="verified",
                reviewed_by=("automation:different",),
                subject=subject,
            )
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
        connection.commit()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_human_copilot_and_deployment_are_derived_from_canonical_chain() -> None:
    assert DATABASE_URL is not None
    schema_name = f"readiness_chain_test_{uuid4().hex}"
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
            cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        _seed_completed_change(connection)
        repository = ReadinessEvidenceRepository(connection)
        observed_at = datetime.now(UTC)
        repository.record_observation(
            observation_id="copilot-observation-1",
            gate_id="github_copilot_live",
            evidence_type="copilot_session",
            project_id="project-1",
            analysis_case_id="case-1",
            observed_at=observed_at,
            review_status="reviewed",
            reviewed_by=("reviewer:copilot",),
            subject={
                "project_id": "project-1",
                "analysis_case_id": "case-1",
                "coding_task_id": "coding-task-1",
                "vscode_session_id": "vscode-session-1",
                "vscode_request_id": "vscode-request-1",
                "vscode_response_id": "vscode-response-1",
                "copilot_extension_version": "1.0.0",
                "copilot_model_id": "copilot/auto",
                "session_transcript_sha256": "c" * 64,
                "completed_mcp_tools": [
                    "copilot_get_coding_task",
                    "copilot_run_task_command",
                    "copilot_validate_task_diff",
                    "copilot_record_task_result",
                ],
                "edit_packet_id": "packet-1",
                "approval_grant_id": "grant-1",
                "base_repository_revision": "a" * 40,
                "result_repository_revision": "b" * 40,
                "mcp_protocol_version": "2025-11-25",
                "tool_approval_status": "confirmed",
            },
        )

        human = repository.human_approval("project-1", "case-1")
        receipt_subject = repository.copilot_task_receipt_subject("coding-task-1")
        copilot = repository.copilot("project-1", "case-1")
        deployment = repository.deployment("project-1", "case-1")

        assert human is not None
        assert human.subject["confirmation_id"] == "confirmation-1"
        assert human.reviewed_by == ("reviewer:approval", "reviewer:impact")
        assert copilot is not None
        assert copilot.evidence_id == "copilot-observation-1"
        assert receipt_subject == {
            "project_id": "project-1",
            "analysis_case_id": "case-1",
            "coding_task_id": "coding-task-1",
            "edit_packet_id": "packet-1",
            "approval_grant_id": "grant-1",
            "base_repository_revision": "a" * 40,
            "result_repository_revision": "b" * 40,
        }
        assert deployment is not None
        assert deployment.review_status == "verified"
        assert deployment.subject["evidence_ids"] == ["ui-evidence-1"]
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
        connection.commit()


def _seed_completed_change(connection: psycopg.Connection[object]) -> None:
    digest = "d" * 64
    base_revision = "a" * 40
    result_revision = "b" * 40
    statements = (
        ("INSERT INTO projects (project_id, name) VALUES ('project-1', 'Readiness test')"),
        (
            "INSERT INTO repositories (repository_id, project_id, remote_url) "
            "VALUES ('repository-1', 'project-1', 'https://example.invalid/test.git')"
        ),
        (
            "INSERT INTO repository_revisions "
            "(repository_revision_id, repository_id, commit_sha) "
            f"VALUES ('revision-1', 'repository-1', '{base_revision}')"
        ),
        (
            "INSERT INTO analysis_cases "
            "(analysis_case_id, project_id, repository_revision_id, status) "
            "VALUES ('case-1', 'project-1', 'revision-1', 'passed')"
        ),
        (
            "INSERT INTO artifact_records ("
            "artifact_id, artifact_type, schema_version, project_id, analysis_case_id, "
            "payload, payload_digest) VALUES ("
            "'change-request-1', 'ChangeRequest', 'v1', 'project-1', 'case-1', "
            "'{\"artifact_type\":\"ChangeRequest\",\"schema_version\":\"v1\"}'::jsonb, "
            f"'{digest}')"
        ),
        (
            "INSERT INTO change_requests ("
            "change_request_id, project_id, analysis_case_id, input_mode, submitted_by) "
            "VALUES ('change-request-1', 'project-1', 'case-1', 'natural_language', "
            "'reviewer:copilot')"
        ),
        (
            "INSERT INTO profile_versions "
            "(profile_version_id, profile_type, profile_id, semantic_version, payload, "
            "payload_digest) VALUES ("
            "'command-profile-1', 'CommandExecutionProfile', 'command-profile', '1.0.0', "
            '\'{"profile_type":"CommandExecutionProfile","profile_id":'
            '"command-profile","profile_version":"1.0.0"}\'::jsonb, '
            f"'{digest}')"
        ),
        (
            "INSERT INTO document_snapshots "
            "(document_snapshot_id, project_id, status, committed_at) "
            "VALUES ('snapshot-1', 'project-1', 'committed', now())"
        ),
        (
            "INSERT INTO code_graph_snapshots ("
            "code_graph_snapshot_id, project_id, repository_id, repository_revision_id, "
            "status, scan_roots, file_count, symbol_count, edge_count, "
            "unresolved_edge_count, is_current) VALUES ("
            "'graph-1', 'project-1', 'repository-1', 'revision-1', 'complete', "
            "'[\"src\"]'::jsonb, 0, 0, 0, 0, true)"
        ),
        (
            "INSERT INTO impact_reports ("
            "impact_report_id, project_id, analysis_case_id, document_snapshot_id, "
            "context_package_id, code_graph_snapshot_id, repository_id, "
            "repository_revision_id, repository_revision, analysis_policy_version, "
            "status, summary, blocking_unknowns, confirmed_at) VALUES ("
            "'report-1', 'project-1', 'case-1', 'snapshot-1', 'context-1', 'graph-1', "
            "'repository-1', 'revision-1', "
            f"'{base_revision}', 'v1', 'confirmed', 'Confirmed impact', '[]'::jsonb, now())"
        ),
        (
            "INSERT INTO impact_items ("
            "impact_report_id, project_id, impact_item_id, structured_change_refs, "
            "target_path, target_symbols, impact_level, recommended_action, evidence_refs, "
            "graph_path_refs, test_file_refs, requires_confirmation, unknowns) VALUES ("
            "'report-1', 'project-1', 'impact-1', '[\"change-1\"]'::jsonb, "
            "'src/app.py', '[]'::jsonb, 'high', 'modify', '[]'::jsonb, '[]'::jsonb, "
            "'[]'::jsonb, true, '[]'::jsonb)"
        ),
        (
            "INSERT INTO impact_confirmations ("
            "confirmation_id, project_id, analysis_case_id, impact_report_id, "
            "confirmed_by, approved_item_ids, rejected_item_ids, confirmed_at) VALUES ("
            "'confirmation-1', 'project-1', 'case-1', 'report-1', 'reviewer:impact', "
            "'[\"impact-1\"]'::jsonb, '[]'::jsonb, now())"
        ),
        (
            "INSERT INTO edit_packets ("
            "edit_packet_id, project_id, analysis_case_id, impact_report_id, confirmation_id, "
            "repository_id, repository_revision_id, base_repository_revision, status, "
            "editable_files, read_only_files, test_files, forbidden_globs, allowed_items, "
            "required_ui_scenario_refs) VALUES ("
            "'packet-1', 'project-1', 'case-1', 'report-1', 'confirmation-1', "
            "'repository-1', 'revision-1', "
            f"'{base_revision}', 'superseded', '[\"src/app.py\"]'::jsonb, '[]'::jsonb, "
            "'[\"tests/test_app.py\"]'::jsonb, '[\"**/.git/**\"]'::jsonb, "
            "'[\"impact-1\"]'::jsonb, '[\"scenario-1\"]'::jsonb)"
        ),
        (
            "INSERT INTO approval_grants ("
            "approval_grant_id, project_id, analysis_case_id, edit_packet_id, "
            "impact_report_id, confirmation_id, repository_id, base_repository_revision, "
            "editable_files, read_only_files, test_files, allowed_actions, "
            "allowed_test_command_refs, allowed_ui_scenarios, forbidden_globs, approved_by, "
            "expires_at, out_of_scope_policy, payload_digest, command_profile_version_id) "
            "VALUES ('grant-1', 'project-1', 'case-1', 'packet-1', 'report-1', "
            "'confirmation-1', 'repository-1', "
            f"'{base_revision}', '[\"src/app.py\"]'::jsonb, '[]'::jsonb, "
            "'[\"tests/test_app.py\"]'::jsonb, '[\"edit\"]'::jsonb, "
            "'[\"test\"]'::jsonb, '[\"scenario-1\"]'::jsonb, "
            "'[\"**/.git/**\"]'::jsonb, 'reviewer:approval', '2099-01-01T00:00:00Z', "
            f"'collect_and_request_once', '{digest}', 'command-profile-1')"
        ),
        (
            "INSERT INTO artifact_records ("
            "artifact_id, artifact_type, schema_version, project_id, analysis_case_id, "
            "payload, payload_digest) VALUES ("
            "'coding-task-1', 'CopilotCodingTask', 'v1', 'project-1', 'case-1', "
            "'{\"artifact_type\":\"CopilotCodingTask\",\"schema_version\":\"v1\"}'::jsonb, "
            f"'{digest}')"
        ),
        (
            "INSERT INTO copilot_coding_tasks ("
            "coding_task_id, project_id, change_request_id, analysis_case_id, repository_id, "
            "edit_packet_id, approval_grant_id, base_repository_revision, execution_mode, "
            "provider_route, provider_id, workspace_root, state, payload_digest, created_by) "
            "VALUES ('coding-task-1', 'project-1', 'change-request-1', 'case-1', "
            "'repository-1', 'packet-1', 'grant-1', "
            f"'{base_revision}', 'copilot_coding_plan', 'local_bridge', "
            f"'vscode_github_copilot', '/tmp/worktree', 'completed', '{digest}', "
            "'reviewer:copilot')"
        ),
        (
            "INSERT INTO edit_results ("
            "edit_result_id, edit_packet_id, project_id, analysis_case_id, validation_mode, "
            "status, base_repository_revision, result_repository_revision, path_changes, "
            "changed_paths, out_of_scope_files, test_result_refs, tests_passed, "
            "approval_grant_id, command_evidence_status) VALUES ("
            "'edit-result-1', 'packet-1', 'project-1', 'case-1', 'committed', 'in_scope', "
            f"'{base_revision}', '{result_revision}', '[]'::jsonb, "
            "'[\"src/app.py\"]'::jsonb, '[]'::jsonb, '[\"command-1\"]'::jsonb, true, "
            "'grant-1', 'verified')"
        ),
        (
            "INSERT INTO copilot_coding_task_edit_results ("
            "coding_task_id, project_id, edit_result_id) "
            "VALUES ('coding-task-1', 'project-1', 'edit-result-1')"
        ),
        (
            "INSERT INTO command_execution_requests ("
            "command_execution_id, approval_grant_id, project_id, analysis_case_id, "
            "edit_packet_id, repository_id, command_profile_version_id, command_ref, "
            "base_repository_revision, remote_url, workspace_root, template_digest, "
            "request_digest) VALUES ('command-1', 'grant-1', 'project-1', 'case-1', "
            "'packet-1', 'repository-1', 'command-profile-1', 'test', "
            f"'{base_revision}', 'https://example.invalid/test.git', '/tmp/worktree', "
            f"'{digest}', '{digest}')"
        ),
        (
            "INSERT INTO command_execution_results ("
            "command_execution_id, project_id, status, exit_code, executable_path, "
            "working_directory, stdout_digest, stderr_digest, stdout_bytes, stderr_bytes, "
            "output_truncated, result_digest, started_at, completed_at) VALUES ("
            "'command-1', 'project-1', 'passed', 0, '/usr/bin/true', '/tmp/worktree', "
            f"'{digest}', '{digest}', 0, 0, false, '{digest}', now(), now())"
        ),
        (
            "INSERT INTO edit_result_command_executions "
            "(edit_result_id, command_execution_id, project_id) "
            "VALUES ('edit-result-1', 'command-1', 'project-1')"
        ),
        (
            "INSERT INTO approval_grant_events ("
            "approval_grant_event_id, approval_grant_id, project_id, event_type, actor, "
            "reason, payload_digest) VALUES ('grant-event-1', 'grant-1', 'project-1', "
            f"'edit_completed', 'worker:copilot', 'Edit completed', '{digest}')"
        ),
        (
            "INSERT INTO ui_environments (environment_id, project_id, base_url, status) "
            "VALUES ('environment-1', 'project-1', 'https://example.invalid', 'active')"
        ),
        (
            "INSERT INTO ui_deployments ("
            "deployment_revision, environment_id, project_id, repository_revision, status) "
            f"VALUES ('deployment-1', 'environment-1', 'project-1', '{result_revision}', 'ready')"
        ),
        (
            "INSERT INTO verification_scenarios ("
            "scenario_version_id, project_id, scenario_id, scenario_version, title, "
            "preconditions, steps, expected_visible_results, evidence_requirements, "
            "trigger_path, review_status, is_active) VALUES ("
            "'scenario-version-1', 'project-1', 'scenario-1', '1.0.0', 'Scenario', "
            "'[]'::jsonb, '[{\"action\":\"open\"}]'::jsonb, "
            "'[{\"result\":\"visible\"}]'::jsonb, '[\"screenshot\"]'::jsonb, '/', "
            "'approved', true)"
        ),
        (
            "INSERT INTO ui_execution_plans ("
            "ui_execution_plan_id, project_id, analysis_case_id, edit_packet_id, "
            "edit_result_id, environment_id, deployment_revision, repository_revision, "
            "status, scenario_refs, blocking_reasons, repository_binding_status) VALUES ("
            "'plan-1', 'project-1', 'case-1', 'packet-1', 'edit-result-1', 'environment-1', "
            f"'deployment-1', '{result_revision}', 'completed', "
            "'[\"scenario-1\"]'::jsonb, '[]'::jsonb, 'verified')"
        ),
        (
            "INSERT INTO ui_execution_plan_scenarios ("
            "ui_execution_plan_id, project_id, scenario_id, scenario_version_id, "
            "execution_order) VALUES ("
            "'plan-1', 'project-1', 'scenario-1', 'scenario-version-1', 1)"
        ),
        (
            "INSERT INTO ui_execution_runs ("
            "ui_execution_run_id, ui_execution_plan_id, project_id, status, completed_at, "
            "approval_grant_id) VALUES ("
            "'run-1', 'plan-1', 'project-1', 'completed', now(), 'grant-1')"
        ),
        (
            "INSERT INTO ui_execution_evidence ("
            "evidence_id, ui_execution_run_id, project_id, scenario_id, evidence_type, "
            "evidence_ref, content_digest, sanitized) VALUES ("
            "'ui-evidence-1', 'run-1', 'project-1', 'scenario-1', 'screenshot', "
            f"'evidence/screenshot.png', '{digest}', true)"
        ),
        (
            "INSERT INTO ui_scenario_results ("
            "ui_execution_run_id, project_id, scenario_id, status, impact_item_refs, "
            "evidence_refs, failure_category, summary) VALUES ("
            "'run-1', 'project-1', 'scenario-1', 'passed', '[\"impact-1\"]'::jsonb, "
            "'[\"ui-evidence-1\"]'::jsonb, 'none', 'Passed')"
        ),
        (
            "INSERT INTO artifact_records ("
            "artifact_id, artifact_type, schema_version, project_id, analysis_case_id, "
            "payload, payload_digest) VALUES ("
            "'verification-1', 'UiVerificationResult', 'v1', 'project-1', 'case-1', "
            '\'{"artifact_type":"UiVerificationResult","schema_version":"v1"}\'::jsonb, '
            f"'{digest}')"
        ),
        (
            "INSERT INTO change_validations ("
            "verification_result_id, project_id, analysis_case_id, ui_execution_plan_id, "
            "ui_execution_run_id, status, unresolved_impact_item_ids, out_of_scope_files, "
            "failure_reasons) VALUES ('verification-1', 'project-1', 'case-1', 'plan-1', "
            "'run-1', 'passed', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb)"
        ),
    )
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
