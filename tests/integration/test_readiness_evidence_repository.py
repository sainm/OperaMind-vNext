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
                    "copilot_record_change_outputs",
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
            cursor.execute(
                """
                INSERT INTO artifact_records (
                    artifact_id, artifact_type, schema_version, project_id,
                    analysis_case_id, payload, payload_digest
                ) VALUES (
                    'test-data-result-2', 'TestDataExecutionResult', 'v1',
                    'project-1', 'case-1',
                    '{"artifact_type":"TestDataExecutionResult","schema_version":"v1"}'::jsonb,
                    %s
                )
                """,
                ("d" * 64,),
            )
            cursor.execute(
                """
                INSERT INTO test_data_execution_runs (
                    run_id, execution_result_id, orchestration_id, test_data_plan_id,
                    approval_grant_id, project_id, analysis_case_id, status,
                    result_artifact_id, created_by, started_at, completed_at
                ) VALUES (
                    'run-2', 'test-data-result-2', 'orchestration-1',
                    'test-data-plan-1', 'grant-1', 'project-1', 'case-1',
                    'failed', 'test-data-result-2', 'automation:test',
                    now() + interval '1 second', now() + interval '2 seconds'
                )
                """
            )
        assert repository.deployment("project-1", "case-1") is None
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
            '\'{"artifact_type":"ChangeRequest","schema_version":"v1"}\'::jsonb, '
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
            '\'{"artifact_type":"CopilotCodingTask","schema_version":"v1"}\'::jsonb, '
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
            "approval_grant_id, command_evidence_status, changed_line_coverage, "
            "changed_line_coverage_status) VALUES ("
            "'edit-result-1', 'packet-1', 'project-1', 'case-1', 'committed', 'in_scope', "
            f"'{base_revision}', '{result_revision}', '[]'::jsonb, "
            "'[\"src/app.py\"]'::jsonb, '[]'::jsonb, '[\"command-1\"]'::jsonb, true, "
            "'grant-1', 'verified', jsonb_build_object("
            "'artifact_type', 'ChangedLineCoverageReport', "
            "'schema_version', 'v1', "
            "'changed_line_coverage_report_id', 'coverage-edit-result-1', "
            "'edit_result_id', 'edit-result-1', 'project_id', 'project-1', "
            f"'base_repository_revision', '{base_revision}', "
            f"'result_repository_revision', '{result_revision}', "
            "'minimum_coverage_percent', 80, 'changed_line_count', 1, "
            "'covered_changed_line_count', 1, 'coverage_percent', 100, "
            "'files', jsonb_build_array(jsonb_build_object("
            "'path', 'src/app.py', 'changed_line_count', 1, "
            "'covered_changed_line_count', 1, 'changed_lines', '[1]'::jsonb, "
            "'covered_changed_lines', '[1]'::jsonb, "
            "'uncovered_changed_lines', '[]'::jsonb)), "
            "'evidence_refs', '[\"command-1\"]'::jsonb, 'status', 'passed', "
            "'blocking_reasons', '[]'::jsonb), 'passed')"
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
            "INSERT INTO artifact_records ("
            "artifact_id, artifact_type, schema_version, project_id, analysis_case_id, "
            "payload, payload_digest) VALUES "
            "('acceptance-1', 'AcceptanceCriteria', 'v1', 'project-1', 'case-1', "
            '\'{"artifact_type":"AcceptanceCriteria","schema_version":"v1"}\'::jsonb, '
            f"'{digest}'), "
            "('test-plan-1', 'TestPlan', 'v1', 'project-1', 'case-1', "
            '\'{"artifact_type":"TestPlan","schema_version":"v1"}\'::jsonb, '
            f"'{digest}'), "
            "('test-data-plan-1', 'TestDataPlan', 'v1', 'project-1', 'case-1', "
            '\'{"artifact_type":"TestDataPlan","schema_version":"v1"}\'::jsonb, '
            f"'{digest}'), "
            "('coverage-1', 'BusinessCoverageReport', 'v1', 'project-1', 'case-1', "
            '\'{"artifact_type":"BusinessCoverageReport","schema_version":"v1"}\'::jsonb, '
            f"'{digest}'), "
            "('orchestration-1', 'ChangeOrchestrationPlan', 'v1', "
            "'project-1', 'case-1', "
            '\'{"artifact_type":"ChangeOrchestrationPlan","schema_version":"v1"}\'::jsonb, '
            f"'{digest}'), "
            "('test-data-result-1', 'TestDataExecutionResult', 'v1', "
            "'project-1', 'case-1', "
            '\'{"artifact_type":"TestDataExecutionResult","schema_version":"v1"}\'::jsonb, '
            f"'{digest}'), "
            "('verification-1', 'UiVerificationResult', 'v2', 'project-1', 'case-1', "
            "jsonb_build_object("
            "'artifact_type', 'UiVerificationResult', 'schema_version', 'v2', "
            "'orchestration_id', 'orchestration-1', "
            "'test_data_execution_result_id', 'test-data-result-1', "
            "'environment_id', 'environment-1', "
            f"'deployment_revision', '{result_revision}', "
            f"'repository_revision', '{result_revision}', "
            "'status', 'passed', "
            "'scenario_results', "
            '\'[{"scenario_id":"ui-case-1","status":"passed"}]\'::jsonb, '
            "'unresolved_impact_item_ids', '[]'::jsonb, "
            "'out_of_scope_files', '[]'::jsonb, "
            "'failure_reasons', '[]'::jsonb), "
            f"'{digest}')"
        ),
        (
            "INSERT INTO change_orchestrations ("
            "orchestration_id, change_request_id, project_id, analysis_case_id, "
            "impact_report_id, reviewed_case_id, reviewed_case_digest, status, "
            "acceptance_criteria_id, test_plan_id, test_data_plan_id, "
            "coverage_report_id, created_by) VALUES ("
            "'orchestration-1', 'change-request-1', 'project-1', 'case-1', "
            f"'report-1', 'reviewed-case-1', '{digest}', 'ready', "
            "'acceptance-1', 'test-plan-1', 'test-data-plan-1', "
            "'coverage-1', 'automation:test')"
        ),
        (
            "INSERT INTO test_data_execution_runs ("
            "run_id, execution_result_id, orchestration_id, test_data_plan_id, "
            "approval_grant_id, project_id, analysis_case_id, status, "
            "result_artifact_id, created_by, started_at, completed_at) VALUES ("
            "'run-1', 'test-data-result-1', 'orchestration-1', 'test-data-plan-1', "
            "'grant-1', 'project-1', 'case-1', 'passed', 'test-data-result-1', "
            "'automation:test', now(), now())"
        ),
        (
            "INSERT INTO test_data_flow_results ("
            "run_id, project_id, flow_id, execution_order, status, "
            "deferred_assertion_ids) VALUES ("
            "'run-1', 'project-1', 'flow-1', 1, 'passed', '[]'::jsonb)"
        ),
        (
            "INSERT INTO test_data_step_results ("
            "run_id, project_id, flow_id, phase, step_id, sequence, channel, "
            "status, output_variables, evidence_refs) VALUES ("
            "'run-1', 'project-1', 'flow-1', 'setup', 'ui-step-1', 1, 'ui', "
            "'passed', '[]'::jsonb, '[\"evidence/screenshot.png\"]'::jsonb)"
        ),
        (
            "INSERT INTO test_data_execution_evidence ("
            "evidence_id, run_id, project_id, flow_id, phase, step_id, "
            "evidence_type, evidence_ref, content_digest, sanitized) VALUES ("
            "'ui-evidence-1', 'run-1', 'project-1', 'flow-1', 'setup', "
            "'ui-step-1', 'screenshot', 'evidence/screenshot.png', "
            f"'{digest}', true)"
        ),
    )
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
