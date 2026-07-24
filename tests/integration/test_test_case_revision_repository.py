from __future__ import annotations

import copy
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from operamind.application.change_closure_service import ChangeClosureService
from operamind.application.change_orchestration import ChangeOrchestrationResult
from operamind.application.test_case_revision import (
    TestCaseChangeAnalyzer as ChangeAnalyzer,
)
from operamind.application.test_case_revision import (
    TestCaseRevisionPlanner as RevisionPlanner,
)
from operamind.application.test_case_revision_service import (
    TestCaseRevisionService as RevisionService,
)
from operamind.application.test_data_execution import (
    TestDataExecutionEvidence as DataExecutionEvidence,
)
from operamind.application.test_data_execution import (
    TestDataExecutionRequest as DataExecutionRequest,
)
from operamind.application.test_data_execution import (
    TestDataStepExecution as DataStepExecution,
)
from operamind.application.test_data_execution_service import (
    TestDataExecutionService as DataExecutionService,
)
from operamind.application.test_data_execution_service import (
    TestDataExecutionServiceRequest as DataExecutionServiceRequest,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    ChangeClosureRepository,
    ChangeOrchestrationRepository,
    MigrationCatalog,
    MigrationRunner,
)
from operamind.infrastructure.postgres import (
    TestCaseExecutionAuthorizationRepository as ExecutionAuthorizationRepository,
)
from operamind.infrastructure.postgres import (
    TestCaseRevisionRepository as RevisionRepository,
)
from operamind.infrastructure.postgres import (
    TestDataExecutionRepository as DataExecutionRepository,
)
from operamind.infrastructure.postgres import (
    TestDataExecutionRunWrite as DataExecutionRunWrite,
)

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


class FixtureExecutor:
    def execute(
        self,
        *,
        request: DataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> DataStepExecution:
        del resolved_inputs, variables
        return DataStepExecution(
            source_values={"fixture": {"ready": True}},
            evidence=(
                DataExecutionEvidence(
                    evidence_id=f"{request.run_id}-fixture",
                    flow_id=flow_id,
                    step_id=str(step["step_id"]),
                    phase=phase,
                    evidence_type="fixture",
                    evidence_ref=f"evidence://visiondemo/{request.run_id}/fixture",
                    content_digest="f" * 64,
                ),
            ),
        )


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_outer_worker_failure_is_persisted_as_canonical_failed_result() -> None:
    assert DATABASE_URL is not None
    schema_name = f"test_data_background_failure_{uuid4().hex}"
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
            cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        contracts = ContractCatalog.load(ROOT / "contracts")
        bundle = _source_bundle()
        _seed_scope(connection, contracts, bundle)
        plan = bundle["test_data_plan"]
        started_at = datetime.now(UTC)
        repository = DataExecutionRepository(connection, contracts)
        repository.reserve(
            DataExecutionRunWrite(
                run_id="background-failed-run",
                execution_result_id="background-failed-result",
                orchestration_id=str(bundle["orchestration"]["orchestration_id"]),
                test_data_plan_id=str(plan["test_data_plan_id"]),
                approval_grant_id="grant-1",
                project_id="visiondemo",
                created_by="web-test-data-worker",
                started_at=started_at,
            )
        )
        service = DataExecutionService(
            connection=connection,
            contracts=contracts,
            executors={},
        )
        result = service.fail_reserved(
            DataExecutionServiceRequest(
                execution_result_id="background-failed-result",
                run_id="background-failed-run",
                orchestration_id=str(bundle["orchestration"]["orchestration_id"]),
                test_data_plan_id=str(plan["test_data_plan_id"]),
                approval_grant_id="grant-1",
                project_id="visiondemo",
                actor="web-test-data-worker",
                started_at=started_at,
            ),
            reason="Background TestDataPlan worker failed before completion (TimeoutError)",
        )

        stored = repository.get_result("background-failed-run")
        assert result.artifact["status"] == "failed"
        assert result.artifact["cleanup_status"] == "failed"
        assert stored == result.artifact


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_natural_language_revision_supersedes_case_and_stales_old_evidence() -> None:
    assert DATABASE_URL is not None
    schema_name = f"test_case_revision_{uuid4().hex}"
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
            cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        contracts = ContractCatalog.load(ROOT / "contracts")
        bundle = _source_bundle()
        _seed_scope(connection, contracts, bundle)
        _seed_old_execution_and_closure(connection, contracts, bundle)

        service = RevisionService(connection=connection, repository_root=ROOT)
        preview = service.propose(
            change_request_id="change-request-1",
            instruction=(
                "ケース「経費一覧を確認」のステップ「一覧を開く」を「経費一覧画面を開く」に変更"
            ),
            actor="qa-user",
        )
        assert preview["state"] == "ready_for_confirmation"
        assert preview["revision"] is None
        applied = service.confirm(
            change_request_id="change-request-1",
            proposal_id=str(preview["proposal"]["proposal_id"]),
            selections={},
            actor="qa-user",
        )

        revision = applied["revision"]
        target_id = revision["target_orchestration_id"]
        assert applied["state"] == "applied"
        assert applied["bundle"]["test_plan"]["test_cases"][0]["steps"][0] == ("経費一覧画面を開く")
        assert revision["stale_run_ids"] == ["run-v1"]
        assert revision["stale_evidence_refs"] == ["evidence://run-v1/setup"]
        assert revision["stale_closure_result_ids"] == ["closure-v1"]
        assert "execution-result-v1" in revision["stale_artifact_refs"]

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT orchestration_id, status, superseded_by_orchestration_id
                FROM change_orchestrations
                ORDER BY orchestration_id
                """
            )
            rows = {str(row[0]): (str(row[1]), row[2]) for row in cursor.fetchall()}
        assert rows["orchestration-v1"] == ("superseded", target_id)
        assert rows[target_id] == ("ready", None)

        orchestrations = ChangeOrchestrationRepository(connection, contracts)
        assert (
            orchestrations.latest_bundle("change-request-1")["orchestration"]["orchestration_id"]
            == target_id
        )
        closures = ChangeClosureRepository(connection, contracts)
        assert (
            closures.latest_for_orchestration("orchestration-v1")["closure_result_id"]
            == "closure-v1"
        )
        assert closures.latest_for_orchestration(target_id) is None

        authorizations = ExecutionAuthorizationRepository(connection, contracts)
        authorization = authorizations.state(
            target_orchestration_id=target_id,
            at=datetime.now(UTC),
        )
        assert authorization["status"] == "confirmation_required"
        assert authorization["authorized"] is False
        assert authorization["scope_comparison"]["changed_dimensions"] == [
            "ui_scenarios",
        ]
        runs = DataExecutionRepository(connection, contracts)
        with pytest.raises(ValueError, match="requires confirmation"):
            runs.latest_active_scope(
                orchestration_id=target_id,
                project_id="visiondemo",
                at=datetime.now(UTC),
            )
        confirmed = authorizations.confirm(
            target_orchestration_id=target_id,
            approval_grant_id="grant-1",
            target_scope_digest=str(authorization["scope_comparison"]["target_scope_digest"]),
            actor="qa-user",
            at=datetime.now(UTC),
        )
        assert confirmed.decision == "reconfirmed"
        assert (
            authorizations.state(
                target_orchestration_id=target_id,
                at=datetime.now(UTC),
            )["authorized"]
            is True
        )

        target_bundle = orchestrations.bundle(target_id)
        target_plan = target_bundle["test_data_plan"]
        executed = DataExecutionService(
            connection=connection,
            contracts=contracts,
            executors={"fixture": FixtureExecutor()},
        ).execute(
            DataExecutionServiceRequest(
                execution_result_id="execution-result-v2",
                run_id="run-v2",
                orchestration_id=target_id,
                test_data_plan_id=str(target_plan["test_data_plan_id"]),
                approval_grant_id="grant-1",
                project_id="visiondemo",
                actor="qa-user",
                started_at=datetime.now(UTC),
            )
        )
        assert executed.artifact["status"] == "passed"
        assert executed.artifact["evidence"][0]["evidence_ref"] == (
            "evidence://visiondemo/run-v2/fixture"
        )
        new_closure = ChangeClosureService(connection, contracts).close(
            orchestration_id=target_id,
            actor="qa-user",
        )
        assert new_closure.artifact["closure_result_id"] != "closure-v1"
        assert "execution-result-v2" in new_closure.artifact["artifact_refs"]
        assert (
            closures.latest_for_orchestration(target_id)["closure_result_id"]
            == (new_closure.artifact["closure_result_id"])
        )

        undone = service.undo(
            change_request_id="change-request-1",
            revision_id=str(revision["revision_id"]),
            idempotency_key="undo-revision-1",
            actor="qa-user",
        )
        undo_revision = undone["revision"]
        undo_target_id = str(undo_revision["target_orchestration_id"])
        assert undone["state"] == "applied"
        assert undo_revision["revision_kind"] == "undo"
        assert undo_revision["undo_of_revision_id"] == revision["revision_id"]
        assert undone["bundle"]["test_plan"]["test_cases"][0]["steps"][0] == ("一覧を開く")
        assert undo_revision["stale_run_ids"] == ["run-v2"]
        assert (
            new_closure.artifact["closure_result_id"] in undo_revision["stale_closure_result_ids"]
        )
        replayed_undo = service.undo(
            change_request_id="change-request-1",
            revision_id=str(revision["revision_id"]),
            idempotency_key="undo-revision-1",
            actor="qa-user",
        )
        assert replayed_undo["created"] is False
        assert replayed_undo["revision"]["revision_id"] == undo_revision["revision_id"]
        revisions = RevisionRepository(connection, contracts)
        history = revisions.revision_history("change-request-1")
        assert history[0]["revision_kind"] == "undo"
        assert history[0]["status"] == "current"
        assert history[0]["can_undo"] is True
        assert history[1]["status"] == "undone"
        assert history[1]["can_undo"] is False
        assert (
            orchestrations.latest_bundle("change-request-1")["orchestration"]["orchestration_id"]
            == undo_target_id
        )

        proposal = applied["proposal"]
        stale = revisions.stale_scope(
            source_orchestration_id="orchestration-v1", source_bundle=bundle
        )
        replay_plan = RevisionPlanner(repository_root=ROOT).plan(
            source_bundle=bundle,
            proposal=proposal,
            operations=proposal["operations"],
            applied_by="qa-user",
            selections={},
            stale_run_ids=list(stale.run_ids),
            stale_artifact_refs=list(stale.artifact_refs),
            stale_evidence_refs=list(stale.evidence_refs),
            stale_closure_result_ids=list(stale.closure_result_ids),
        )
        assert revisions.persist_revision(plan=replay_plan).created is False

        competing = (
            ChangeAnalyzer(repository_root=ROOT)
            .analyze(
                bundle=bundle,
                instruction=(
                    "ケース「経費一覧を確認」の期待結果「4 件を表示する」を「5 件を表示する」に変更"
                ),
            )
            .proposal
        )
        revisions.store_proposal(proposal=competing, created_by="qa-user")
        competing_plan = RevisionPlanner(repository_root=ROOT).plan(
            source_bundle=bundle,
            proposal=competing,
            operations=competing["operations"],
            applied_by="qa-user",
            selections={},
        )
        with pytest.raises(ValueError, match="already stale"):
            revisions.persist_revision(plan=competing_plan)

        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
        connection.commit()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_unchanged_execution_scope_reuses_completed_grant_for_new_run() -> None:
    assert DATABASE_URL is not None
    schema_name = f"test_case_scope_reuse_{uuid4().hex}"
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
            cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        contracts = ContractCatalog.load(ROOT / "contracts")
        bundle = _source_bundle(ui=False)
        _seed_scope(connection, contracts, bundle, ui_scenario_ids=())

        service = RevisionService(connection=connection, repository_root=ROOT)
        preview = service.propose(
            change_request_id="change-request-1",
            instruction=(
                "ケース「経費一覧を確認」のステップ「一覧を開く」を「対象一覧を開く」に変更"
            ),
            actor="qa-user",
        )
        applied = service.confirm(
            change_request_id="change-request-1",
            proposal_id=str(preview["proposal"]["proposal_id"]),
            selections={},
            actor="qa-user",
        )
        target_id = str(applied["revision"]["target_orchestration_id"])
        authorizations = ExecutionAuthorizationRepository(connection, contracts)
        before_run = authorizations.state(
            target_orchestration_id=target_id,
            at=datetime.now(UTC),
        )
        assert before_run["status"] == "reusable"
        assert before_run["authorized"] is True
        assert before_run["scope_comparison"]["changed_dimensions"] == []
        assert before_run["approval_grant_id"] == "grant-1"

        active_scope = DataExecutionRepository(connection, contracts).latest_active_scope(
            orchestration_id=target_id,
            project_id="visiondemo",
            at=datetime.now(UTC),
        )
        assert active_scope["authorization_status"] == "reusable"
        assert active_scope["authorization_id"] is None

        target_plan = ChangeOrchestrationRepository(connection, contracts).bundle(target_id)[
            "test_data_plan"
        ]
        executed = DataExecutionService(
            connection=connection,
            contracts=contracts,
            executors={"fixture": FixtureExecutor()},
        ).execute(
            DataExecutionServiceRequest(
                execution_result_id="execution-result-reused",
                run_id="run-reused",
                orchestration_id=target_id,
                test_data_plan_id=str(target_plan["test_data_plan_id"]),
                approval_grant_id="grant-1",
                project_id="visiondemo",
                actor="qa-user",
                started_at=datetime.now(UTC),
            )
        )
        assert executed.artifact["status"] == "passed"
        after_run = authorizations.state(
            target_orchestration_id=target_id,
            at=datetime.now(UTC),
        )
        assert after_run["status"] == "reused"
        assert after_run["confirmed_by"] == "system:scope-unchanged"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT decision, confirmed_by
                FROM test_case_execution_authorizations
                WHERE target_orchestration_id = %s
                """,
                (target_id,),
            )
            assert cursor.fetchone() == ("reused", "system:scope-unchanged")
            cursor.execute("SET search_path TO public")
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
        connection.commit()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_one_preview_persists_multiple_case_changes_as_one_version() -> None:
    assert DATABASE_URL is not None
    schema_name = f"test_case_multi_revision_{uuid4().hex}"
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
            cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        contracts = ContractCatalog.load(ROOT / "contracts")
        bundle = _two_case_source_bundle()
        _seed_scope(
            connection,
            contracts,
            bundle,
            ui_scenario_ids=("expense-case", "employee-case"),
        )
        service = RevisionService(connection=connection, repository_root=ROOT)
        preview = service.propose(
            change_request_id="change-request-1",
            instruction="\n".join(
                (
                    "ケース「経費一覧を確認」のステップ「一覧を開く」を"
                    "「経費一覧画面を開く」に変更",
                    "ケース「社員一覧を確認」のステップ「一覧を開く」を"
                    "「社員一覧画面を開く」に変更",
                    "ケース「経費一覧を確認」のテストデータ「expense-data」の項目"
                    "「expected_count」を「5」に変更",
                    "ケース「社員一覧を確認」の業務アサーション「3 件を表示する」を"
                    "「5 件を表示する」に変更",
                )
            ),
            actor="qa-user",
        )

        assert preview["state"] == "ready_for_confirmation"
        assert preview["revision"] is None
        assert {operation["test_case_id"] for operation in preview["proposal"]["operations"]} == {
            "expense-case",
            "employee-case",
        }
        assert (
            ChangeOrchestrationRepository(connection, contracts).latest_bundle("change-request-1")[
                "orchestration"
            ]["orchestration_id"]
            == "orchestration-v1"
        )

        applied = service.confirm(
            change_request_id="change-request-1",
            proposal_id=str(preview["proposal"]["proposal_id"]),
            selections={},
            actor="qa-user",
        )
        cases = {
            case["test_case_id"]: case for case in applied["bundle"]["test_plan"]["test_cases"]
        }
        data_sets = {
            item["test_data_id"]: item for item in applied["bundle"]["test_data_plan"]["data_sets"]
        }
        flows = {
            item["flow_id"]: item
            for item in applied["bundle"]["test_data_plan"]["generation_flows"]
        }
        assert cases["expense-case"]["steps"][0] == "経費一覧画面を開く"
        assert cases["employee-case"]["steps"][0] == "社員一覧画面を開く"
        assert data_sets["expense-data"]["setup_actions"][0]["payload"]["expected_count"] == 5
        assert flows["employee-flow"]["final_assertions"][0]["expected"] == ("5 件を表示する")
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM test_case_revisions")
            assert cursor.fetchone() == (1,)
            cursor.execute(
                "SELECT count(*) FROM change_orchestrations WHERE status IN ('ready', 'blocked')"
            )
            assert cursor.fetchone() == (1,)
            cursor.execute("SET search_path TO public")
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
        connection.commit()


def _seed_scope(
    connection: psycopg.Connection[Any],
    contracts: ContractCatalog,
    bundle: dict[str, Any],
    *,
    ui_scenario_ids: tuple[str, ...] = ("expense-case",),
) -> None:
    digest = "d" * 64
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES ('visiondemo', 'VisionDemo')"
        )
        cursor.execute(
            """
            INSERT INTO repositories (repository_id, project_id, remote_url)
            VALUES ('repository-1', 'visiondemo', 'https://example.invalid/visiondemo.git')
            """
        )
        cursor.execute(
            """
            INSERT INTO repository_revisions (
                repository_revision_id, repository_id, commit_sha
            ) VALUES ('revision-1', 'repository-1', %s)
            """,
            ("a" * 40,),
        )
        cursor.execute(
            """
            INSERT INTO analysis_cases (
                analysis_case_id, project_id, repository_revision_id, status
            ) VALUES ('analysis-case-1', 'visiondemo', 'revision-1', 'verifying_ui')
            """
        )
        cursor.execute(
            """
            INSERT INTO document_snapshots (
                document_snapshot_id, project_id, status, committed_at
            ) VALUES ('snapshot-1', 'visiondemo', 'committed', now())
            """
        )
        cursor.execute(
            """
            INSERT INTO code_graph_snapshots (
                code_graph_snapshot_id, project_id, repository_id,
                repository_revision_id, status, scan_roots, file_count,
                symbol_count, edge_count, unresolved_edge_count, is_current
            ) VALUES (
                'graph-1', 'visiondemo', 'repository-1', 'revision-1', 'complete',
                '["src"]'::jsonb, 0, 0, 0, 0, true
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO impact_reports (
                impact_report_id, project_id, analysis_case_id, document_snapshot_id,
                context_package_id, code_graph_snapshot_id, repository_id,
                repository_revision_id, repository_revision, analysis_policy_version,
                status, summary, blocking_unknowns, confirmed_at
            ) VALUES (
                'impact-1', 'visiondemo', 'analysis-case-1', 'snapshot-1', 'context-1',
                'graph-1', 'repository-1', 'revision-1', %s, 'v1', 'confirmed',
                'Confirmed impact', '[]'::jsonb, now()
            )
            """,
            ("a" * 40,),
        )
    request = {
        "artifact_type": "ChangeRequest",
        "schema_version": "v1",
        "change_request_id": "change-request-1",
        "project_id": "visiondemo",
        "input_mode": "natural_language",
        "requirement_text": "経費一覧の表示件数を確認する",
        "business_rules": [
            {
                "business_rule_id": "rule-list",
                "text": "経費一覧を表示する",
                "source_refs": [],
            }
        ],
        "ambiguity_status": "clear",
        "confirmation_required": False,
        "ambiguities": [],
    }
    ArtifactRepository(connection, contracts).store(
        artifact_id="change-request-1",
        project_id="visiondemo",
        analysis_case_id="analysis-case-1",
        artifact=request,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO change_requests (
                change_request_id, project_id, analysis_case_id, input_mode, submitted_by
            ) VALUES (
                'change-request-1', 'visiondemo', 'analysis-case-1',
                'natural_language', 'qa-user'
            )
            """
        )
    result = ChangeOrchestrationResult(
        orchestration=bundle["orchestration"],
        acceptance_criteria=bundle["acceptance_criteria"],
        test_plan=bundle["test_plan"],
        test_data_plan=bundle["test_data_plan"],
        coverage_report=bundle["coverage_report"],
    )
    ChangeOrchestrationRepository(connection, contracts).persist(
        result=result, created_by="qa-user"
    )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO impact_confirmations (
                confirmation_id, project_id, analysis_case_id, impact_report_id,
                confirmed_by, approved_item_ids, rejected_item_ids, confirmed_at
            ) VALUES (
                'confirmation-1', 'visiondemo', 'analysis-case-1', 'impact-1',
                'reviewer', '["impact-item-1"]'::jsonb, '[]'::jsonb, now()
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO edit_packets (
                edit_packet_id, project_id, analysis_case_id, impact_report_id,
                confirmation_id, repository_id, repository_revision_id,
                base_repository_revision, status, editable_files, read_only_files,
                test_files, forbidden_globs, allowed_items, required_ui_scenario_refs
            ) VALUES (
                'packet-1', 'visiondemo', 'analysis-case-1', 'impact-1',
                'confirmation-1', 'repository-1', 'revision-1', %s, 'active',
                '["src/app.py"]'::jsonb, '[]'::jsonb, '["tests/test_app.py"]'::jsonb,
                '["**/.git/**"]'::jsonb, '["impact-item-1"]'::jsonb,
                %s
            )
            """,
            ("a" * 40, Jsonb(list(ui_scenario_ids))),
        )
        cursor.execute(
            """
            INSERT INTO approval_grants (
                approval_grant_id, project_id, analysis_case_id, edit_packet_id,
                impact_report_id, confirmation_id, repository_id,
                base_repository_revision, editable_files, read_only_files, test_files,
                allowed_actions, allowed_test_command_refs, allowed_ui_scenarios,
                forbidden_globs, approved_by, expires_at, out_of_scope_policy,
                payload_digest
            ) VALUES (
                'grant-1', 'visiondemo', 'analysis-case-1', 'packet-1', 'impact-1',
                'confirmation-1', 'repository-1', %s, '["src/app.py"]'::jsonb,
                '[]'::jsonb, '["tests/test_app.py"]'::jsonb,
                '["edit", "run_test", "record_evidence"]'::jsonb,
                '[]'::jsonb, %s,
                '["**/.git/**"]'::jsonb, 'reviewer',
                '2099-01-01T00:00:00Z', 'collect_and_request_once', %s
            )
            """,
            ("a" * 40, Jsonb(list(ui_scenario_ids)), digest),
        )
        cursor.execute(
            """
            INSERT INTO approval_grant_events (
                approval_grant_event_id, approval_grant_id, project_id,
                event_type, actor, reason, payload_digest
            ) VALUES (
                'grant-completed-1', 'grant-1', 'visiondemo', 'completed',
                'qa-user', 'Original Case verification completed', %s
            )
            """,
            ("c" * 64,),
        )


def _seed_old_execution_and_closure(
    connection: psycopg.Connection[Any],
    contracts: ContractCatalog,
    bundle: dict[str, Any],
) -> None:
    started_at = "2026-07-19T00:00:00Z"
    completed_at = "2026-07-19T00:00:01Z"
    digest = "e" * 64
    result = {
        "artifact_type": "TestDataExecutionResult",
        "schema_version": "v1",
        "execution_result_id": "execution-result-v1",
        "run_id": "run-v1",
        "test_data_plan_id": "test-data-plan-v1",
        "project_id": "visiondemo",
        "status": "passed",
        "started_at": started_at,
        "completed_at": completed_at,
        "flow_results": [
            {
                "flow_id": "expense-flow",
                "status": "passed",
                "step_results": [
                    {
                        "step_id": "setup-expense-data",
                        "sequence": 1,
                        "channel": "fixture",
                        "phase": "setup",
                        "status": "passed",
                        "output_variables": [],
                        "evidence_refs": ["evidence://run-v1/setup"],
                    }
                ],
                "cleanup_results": [],
                "deferred_assertion_ids": [],
            }
        ],
        "evidence": [
            {
                "evidence_id": "evidence-v1",
                "flow_id": "expense-flow",
                "step_id": "setup-expense-data",
                "phase": "setup",
                "evidence_type": "fixture",
                "evidence_ref": "evidence://run-v1/setup",
                "content_digest": digest,
                "sanitized": True,
            }
        ],
        "failure_reasons": [],
        "cleanup_status": "not_required",
    }
    ArtifactRepository(connection, contracts).store(
        artifact_id="execution-result-v1",
        project_id="visiondemo",
        analysis_case_id="analysis-case-1",
        artifact=result,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO test_data_execution_runs (
                run_id, execution_result_id, orchestration_id, test_data_plan_id,
                approval_grant_id, project_id, analysis_case_id, status,
                result_artifact_id, created_by, started_at, completed_at
            ) VALUES (
                'run-v1', 'execution-result-v1', 'orchestration-v1',
                'test-data-plan-v1', 'grant-1', 'visiondemo', 'analysis-case-1',
                'passed', 'execution-result-v1', 'qa-user', %s, %s
            )
            """,
            (datetime(2026, 7, 19, tzinfo=UTC), datetime(2026, 7, 19, 0, 0, 1, tzinfo=UTC)),
        )
        cursor.execute(
            """
            INSERT INTO test_data_flow_results (
                run_id, project_id, flow_id, execution_order, status,
                deferred_assertion_ids
            ) VALUES (
                'run-v1', 'visiondemo', 'expense-flow', 1, 'passed', '[]'::jsonb
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO test_data_step_results (
                run_id, project_id, flow_id, phase, step_id, sequence, channel,
                status, output_variables, evidence_refs
            ) VALUES (
                'run-v1', 'visiondemo', 'expense-flow', 'setup',
                'setup-expense-data', 1, 'fixture', 'passed', '[]'::jsonb,
                '["evidence://run-v1/setup"]'::jsonb
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO test_data_execution_evidence (
                evidence_id, run_id, project_id, flow_id, phase, step_id,
                evidence_type, evidence_ref, content_digest, sanitized
            ) VALUES (
                'evidence-v1', 'run-v1', 'visiondemo', 'expense-flow', 'setup',
                'setup-expense-data', 'fixture', 'evidence://run-v1/setup', %s, true
            )
            """,
            (digest,),
        )
    closure = {
        "artifact_type": "ChangeClosureResult",
        "schema_version": "v2",
        "closure_result_id": "closure-v1",
        "change_request_id": "change-request-1",
        "project_id": "visiondemo",
        "input_mode": "natural_language",
        "artifact_refs": [
            "orchestration-v1",
            "test-plan-v1",
            "test-data-plan-v1",
            "coverage-v1",
            "execution-result-v1",
        ],
        "structured_change_refs": ["change-1"],
        "modified_paths": [],
        "test_results": [
            {
                "test_case_id": "expense-case",
                "status": "passed",
                "evidence_refs": ["evidence://run-v1/setup"],
            }
        ],
        "ui_status": "not_impacted",
        "business_coverage_percent": 100,
        "changed_line_coverage_percent": 0,
        "changed_line_coverage_status": "missing",
        "status": "blocked",
        "unresolved_items": ["Changed-line coverage evidence is missing"],
    }
    repository = ChangeClosureRepository(connection, contracts)
    repository.persist(
        evidence=repository.load_evidence("orchestration-v1"),
        artifact=closure,
        created_by="qa-user",
    )


def _two_case_source_bundle() -> dict[str, Any]:
    bundle = copy.deepcopy(_source_bundle())
    employee_case = {
        "test_case_id": "employee-case",
        "title": "社員一覧を確認",
        "level": "ui",
        "execution_mode": "browser",
        "business_rule_refs": ["rule-list"],
        "acceptance_criteria_refs": ["employee-criterion"],
        "preconditions": ["ログイン済み"],
        "steps": ["一覧を開く", "社員を確認する"],
        "expected_results": ["3 件を表示する"],
        "test_data_refs": ["employee-data"],
    }
    bundle["test_plan"]["test_cases"].append(employee_case)
    bundle["acceptance_criteria"]["criteria"].append(
        {
            "criterion_id": "employee-criterion",
            "business_rule_refs": ["rule-list"],
            "assertion_type": "ui",
            "subject": "社員一覧件数",
            "operator": "equals",
            "expected": "3 件を表示する",
            "test_case_refs": ["employee-case"],
        }
    )
    bundle["test_data_plan"]["data_sets"].append(
        {
            "test_data_id": "employee-data",
            "test_case_refs": ["employee-case"],
            "setup_actions": [
                {
                    "action_id": "setup-employee-data",
                    "action_type": "fixture",
                    "target": "visiondemo.default-seed",
                    "payload": {"expected_count": 3},
                }
            ],
            "cleanup_policy": "isolated_environment",
        }
    )
    employee_flow = copy.deepcopy(bundle["test_data_plan"]["generation_flows"][0])
    employee_flow.update(
        {
            "flow_id": "employee-flow",
            "title": "社員データを生成",
            "test_data_refs": ["employee-data"],
            "test_case_refs": ["employee-case"],
        }
    )
    employee_flow["steps"][0]["step_id"] = "setup-employee-data"
    employee_flow["final_assertions"][0].update(
        {
            "assertion_id": "employee-result",
            "subject": "employee-case",
            "expected": "3 件を表示する",
        }
    )
    bundle["test_data_plan"]["generation_flows"].append(employee_flow)
    bundle["orchestration"]["ui_scenarios"].append(
        {
            "scenario_id": "employee-case",
            "title": "社員一覧を確認",
            "test_data_refs": ["employee-data"],
            "steps": ["一覧を開く", "社員を確認する"],
            "expected_results": ["3 件を表示する"],
        }
    )
    coverage = bundle["coverage_report"]["items"][0]
    coverage["test_case_refs"].append("employee-case")
    coverage["criterion_refs"].append("employee-criterion")
    return bundle


def _source_bundle(*, ui: bool = True) -> dict[str, Any]:
    case = {
        "test_case_id": "expense-case",
        "title": "経費一覧を確認",
        "level": "ui" if ui else "source",
        "execution_mode": "browser" if ui else "deterministic",
        "business_rule_refs": ["rule-list"],
        "acceptance_criteria_refs": ["expense-criterion"],
        "preconditions": ["ログイン済み"],
        "steps": ["一覧を開く", "ステータスを確認する"],
        "expected_results": ["4 件を表示する"],
        "test_data_refs": ["expense-data"],
    }
    criterion = {
        "criterion_id": "expense-criterion",
        "business_rule_refs": ["rule-list"],
        "assertion_type": "ui" if ui else "source",
        "subject": "一覧件数",
        "operator": "equals",
        "expected": "4 件を表示する",
        "test_case_refs": ["expense-case"],
    }
    return {
        "orchestration": {
            "artifact_type": "ChangeOrchestrationPlan",
            "schema_version": "v1",
            "orchestration_id": "orchestration-v1",
            "change_request_id": "change-request-1",
            "project_id": "visiondemo",
            "analysis_case_id": "analysis-case-1",
            "status": "ready",
            "structured_change_refs": ["change-1"],
            "impact_report_id": "impact-1",
            "reviewed_case_id": "golden-case-1",
            "reviewed_case_digest": "a" * 64,
            "repository_revision": "a" * 40,
            "code_scope": [
                {
                    "impact_item_id": "impact-item-1",
                    "target_path": "src/ExpenseService.java",
                    "target_symbols": ["ExpenseService.search"],
                    "recommended_action": "modify",
                    "test_file_refs": ["tests/ExpenseServiceTest.java"],
                }
            ],
            "artifact_refs": {
                "acceptance_criteria_id": "acceptance-v1",
                "test_plan_id": "test-plan-v1",
                "test_data_plan_id": "test-data-plan-v1",
                "coverage_report_id": "coverage-v1",
            },
            "ui_scenarios": [
                {
                    "scenario_id": "expense-case",
                    "title": "経費一覧を確認",
                    "test_data_refs": ["expense-data"],
                    "steps": ["一覧を開く", "ステータスを確認する"],
                    "expected_results": ["4 件を表示する"],
                }
            ]
            if ui
            else [],
            "blocking_reasons": [],
        },
        "acceptance_criteria": {
            "artifact_type": "AcceptanceCriteria",
            "schema_version": "v1",
            "acceptance_criteria_id": "acceptance-v1",
            "change_request_id": "change-request-1",
            "project_id": "visiondemo",
            "criteria": [criterion],
        },
        "test_plan": {
            "artifact_type": "TestPlan",
            "schema_version": "v1",
            "test_plan_id": "test-plan-v1",
            "change_request_id": "change-request-1",
            "project_id": "visiondemo",
            "status": "ready",
            "test_cases": [case],
            "blocking_reasons": [],
        },
        "test_data_plan": {
            "artifact_type": "TestDataPlan",
            "schema_version": "v1",
            "test_data_plan_id": "test-data-plan-v1",
            "test_plan_id": "test-plan-v1",
            "project_id": "visiondemo",
            "status": "ready",
            "data_sets": [
                {
                    "test_data_id": "expense-data",
                    "test_case_refs": ["expense-case"],
                    "setup_actions": [
                        {
                            "action_id": "setup-expense-data",
                            "action_type": "fixture",
                            "target": "visiondemo.default-seed",
                            "payload": {"expected_count": 4},
                        }
                    ],
                    "cleanup_policy": "isolated_environment",
                }
            ],
            "generation_flows": [
                {
                    "flow_id": "expense-flow",
                    "title": "経費データを生成",
                    "test_data_refs": ["expense-data"],
                    "test_case_refs": ["expense-case"],
                    "steps": [
                        {
                            "step_id": "setup-expense-data",
                            "sequence": 1,
                            "channel": "fixture",
                            "business_action": "既定データを準備する",
                            "target": "visiondemo.default-seed",
                            "inputs": {},
                            "depends_on": [],
                            "output_bindings": [],
                            "postconditions": [
                                {
                                    "assertion_id": "setup-ready",
                                    "observe_via": "fixture",
                                    "subject": "ready",
                                    "operator": "equals",
                                    "expected": True,
                                }
                            ],
                        }
                    ],
                    "final_assertions": [
                        {
                            "assertion_id": "expense-result",
                            "observe_via": "test",
                            "subject": "expense-case",
                            "operator": "satisfies",
                            "expected": "4 件を表示する",
                        }
                    ],
                    "cleanup_policy": "isolated_environment",
                    "cleanup_steps": [],
                }
            ],
            "blocking_reasons": [],
        },
        "coverage_report": {
            "artifact_type": "BusinessCoverageReport",
            "schema_version": "v1",
            "coverage_report_id": "coverage-v1",
            "change_request_id": "change-request-1",
            "test_plan_id": "test-plan-v1",
            "acceptance_criteria_id": "acceptance-v1",
            "project_id": "visiondemo",
            "business_rule_count": 1,
            "covered_rule_count": 1,
            "coverage_percent": 100,
            "items": [
                {
                    "business_rule_id": "rule-list",
                    "test_case_refs": ["expense-case"],
                    "criterion_refs": ["expense-criterion"],
                    "status": "covered",
                }
            ],
            "status": "passed",
        },
    }
