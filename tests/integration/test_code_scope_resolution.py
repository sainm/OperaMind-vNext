import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest

from operamind.application import (
    ApprovalGrantRequest,
    ApprovalGrantService,
    ApprovedCommandRequest,
    ApprovedCommandService,
    BrowserExecutionRequest,
    BrowserExecutionRuntimeError,
    BrowserExecutionService,
    BrowserPreflightRequest,
    BrowserPreflightService,
    ChangedLineCoverageEvidence,
    CodeScopeRequest,
    CodeScopeResolverService,
    CommandExecutionRecoveryRequest,
    CommandExecutionRecoveryService,
    CopilotCodingTaskPublishRequest,
    CopilotCodingTaskService,
    EditPacketRequest,
    EditPacketService,
    EditResultRequest,
    EditResultService,
    EditValidationMode,
    ImpactReportRequest,
    ImpactReportService,
    UiImpactStatus,
    UiKnowledgeProposalRequest,
    UiKnowledgeProposalService,
    UiKnowledgeReviewRequest,
    UiKnowledgeReviewService,
    UiRunRecovery,
    UiRuntimeObservationRequest,
    UiRuntimeObservationService,
    UiVerificationService,
)
from operamind.application.web_control_plane import (
    BusinessRuleInput,
    ChangeRequestInput,
    WebControlPlaneService,
)
from operamind.contracts import ContractCatalog
from operamind.domain import (
    BrowserExecutionManifest,
    CanonicalDocumentNodeBuilder,
    CanonicalFact,
    CanonicalSnapshot,
    CodeAnchor,
    CodeAnchorKind,
    SnapshotFact,
    StructuredChangeBuilder,
    UiKnowledgeSnapshot,
    UiLocatorObservationStatus,
    UiRuntimeLocatorObservation,
    UiRuntimeObservationMerger,
    UiRuntimeObservationResult,
    runtime_observation_id,
)
from operamind.infrastructure.browser import (
    BrowserExecutionOutput,
    BrowserPreflightObservation,
    BrowserScenarioOutcome,
    StoredBrowserEvidence,
)
from operamind.infrastructure.postgres import (
    ApprovalGrantRepository,
    ArtifactRepository,
    CanonicalRepository,
    CodeGraphSnapshotRepository,
    DocumentNodeRepository,
    DocumentSnapshotWrite,
    EditPacketRepository,
    ImpactRepository,
    MigrationCatalog,
    MigrationRunner,
    PersistenceConflictError,
    ProfileRepository,
    SnapshotStatus,
    StructuredChangeReviewDecision,
    StructuredChangeReviewRepository,
    UiBrowserManifestRepository,
    UiDeploymentWrite,
    UiExecutionEvidenceWrite,
    UiExecutionPlanWrite,
    UiKnowledgeRepository,
    UiLocatorObservationRepository,
    UiPreflightCheckWrite,
    UiScenarioResultWrite,
    UiVerificationRepository,
    VerificationScenarioWrite,
)
from operamind.mcp import MCP_PROTOCOL_VERSION, CopilotToolDispatcher, OperaMindMcpServer
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_scope_resolver_binds_document_evidence_to_edit_and_test_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    repository_id = f"repository-{suffix}"
    revision_id = f"revision-{suffix}"
    case_id = f"case-{suffix}"
    context_id = f"context-{suffix}"
    graph_id = f"code-graph-{suffix}"
    code_profile_version_id = f"code-profile-{suffix}"
    command_profile_version_id = f"command-profile-{suffix}"
    document_profile_version_id = f"document-profile-{suffix}"
    profile_binding_key = f"code-framework:{repository_id}"
    command_profile_binding_key = f"command-execution:{repository_id}"
    evidence_ref = f"document-node-{suffix}"
    workspace, commit_sha, remote_url = _create_edit_workspace(tmp_path, suffix)

    contracts = ContractCatalog.load(ROOT / "contracts")
    profiles = ProfileCatalog.load(ROOT / "profiles")
    code_profile = _load_json(ROOT / "profiles/code-framework-profile.example.json")
    command_profile: dict[str, Any] = {
        "profile_type": "CommandExecutionProfile",
        "profile_id": "git-test-commands",
        "profile_version": "1.0.0",
        "templates": [
            {
                "command_ref": "targeted-unit",
                "argv": ["git", "status", "--short"],
                "working_directory": ".",
                "timeout_seconds": 10,
                "expected_exit_codes": [0],
                "environment_keys": ["PATH", "LANG"],
                "output_limit_bytes": 4096,
                "failure_policy": "record_and_block",
            }
        ],
    }
    document_profile = _load_json(ROOT / "profiles/screen-design-convention-profile.example.json")

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        _insert_project_case(
            connection,
            project_id=project_id,
            repository_id=repository_id,
            revision_id=revision_id,
            case_id=case_id,
            commit_sha=commit_sha,
            remote_url=remote_url,
            workspace_root=workspace,
        )
        profile_repository = ProfileRepository(connection, profiles)
        profile_repository.store_version(
            profile_version_id=document_profile_version_id,
            profile=document_profile,
        )
        profile_repository.store_version(
            profile_version_id=code_profile_version_id,
            profile=code_profile,
        )
        profile_repository.store_version(
            profile_version_id=command_profile_version_id,
            profile=command_profile,
        )
        profile_repository.activate(
            activation_event_id=f"code-profile-activation-{suffix}",
            project_id=project_id,
            binding_key=profile_binding_key,
            profile_version_id=code_profile_version_id,
            activated_by="reviewer@example.invalid",
            reason="Approved framework extraction and traversal policy",
        )
        profile_repository.activate(
            activation_event_id=f"command-profile-activation-{suffix}",
            project_id=project_id,
            binding_key=command_profile_binding_key,
            profile_version_id=command_profile_version_id,
            activated_by="reviewer@example.invalid",
            reason="Approved fixed command templates for the edit session",
        )

        canonical = CanonicalRepository(connection, contracts)
        before, after = _store_document_snapshots(
            canonical,
            connection=connection,
            suffix=suffix,
            project_id=project_id,
            profile_version_id=document_profile_version_id,
            evidence_ref=evidence_ref,
        )
        changes = StructuredChangeBuilder().diff(
            project_id=project_id,
            source=before,
            target=after,
            domain="ui",
        )
        assert len(changes) == 1
        change = changes[0]
        canonical.store_changes(changes)
        ArtifactRepository(connection, contracts).store(
            artifact_id=change.change_id,
            project_id=project_id,
            analysis_case_id=case_id,
            artifact=change.to_artifact(),
        )
        StructuredChangeReviewRepository(connection).review(
            review_event_id=f"review-{suffix}",
            project_id=project_id,
            change_id=change.change_id,
            decision=StructuredChangeReviewDecision.ACCEPTED,
            reviewed_by="reviewer@example.invalid",
            reason="Approved the document change for code impact analysis",
            expected_previous_review_event_id=None,
        )
        ArtifactRepository(connection, contracts).store(
            artifact_id=context_id,
            project_id=project_id,
            analysis_case_id=case_id,
            artifact=_context_artifact(
                context_id=context_id,
                case_id=case_id,
                project_id=project_id,
                snapshot_id=after.snapshot_id,
                change_id=change.change_id,
                evidence_ref=evidence_ref,
                suffix=suffix,
            ),
        )
        CodeGraphSnapshotRepository(connection, contracts).publish(
            artifact=_code_graph_artifact(
                graph_id=graph_id,
                project_id=project_id,
                repository_id=repository_id,
                commit_sha=commit_sha,
                suffix=suffix,
            ),
            repository_revision_id=revision_id,
            profile_version_ids={"spring-web-example@1.0.0": code_profile_version_id},
        )

        service = CodeScopeResolverService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        )
        request = CodeScopeRequest(
            project_id=project_id,
            analysis_case_id=case_id,
            context_package_id=context_id,
            structured_change_id=change.change_id,
            code_graph_snapshot_id=graph_id,
            repository_revision_id=revision_id,
            profile_binding_key=profile_binding_key,
            anchors=(
                CodeAnchor(
                    anchor_id="expense-list-endpoint",
                    kind=CodeAnchorKind.ENDPOINT,
                    value="GET /expenses",
                    evidence_refs=(evidence_ref,),
                ),
            ),
        )

        result = service.resolve(request)

        assert not result.confirmation_blocked
        assert result.unknown_items == ()
        assert result.relation_policy_domain == "ui"
        assert result.editable_files == ("src/main/java/example/ExpenseService.java",)
        assert result.read_only_files == ()
        assert result.test_files == ("src/test/java/example/ExpenseServiceTest.java",)
        assert [candidate.classification for candidate in result.candidates] == [
            "editable",
            "test",
        ]
        assert result.candidates[0].target_symbols == ("search(String status)",)
        assert "edge-exposes" in result.candidates[0].to_dict()["graph_path_refs"][0]

        method_agnostic = service.resolve(
            CodeScopeRequest(
                project_id=request.project_id,
                analysis_case_id=request.analysis_case_id,
                context_package_id=request.context_package_id,
                structured_change_id=request.structured_change_id,
                code_graph_snapshot_id=request.code_graph_snapshot_id,
                repository_revision_id=request.repository_revision_id,
                profile_binding_key=request.profile_binding_key,
                anchors=(
                    CodeAnchor(
                        anchor_id="expense-list-path",
                        kind=CodeAnchorKind.ENDPOINT,
                        value="/expenses",
                        evidence_refs=(evidence_ref,),
                    ),
                ),
            )
        )
        assert method_agnostic.editable_files == result.editable_files
        assert not method_agnostic.confirmation_blocked

        missing = service.resolve(
            CodeScopeRequest(
                project_id=request.project_id,
                analysis_case_id=request.analysis_case_id,
                context_package_id=request.context_package_id,
                structured_change_id=request.structured_change_id,
                code_graph_snapshot_id=request.code_graph_snapshot_id,
                repository_revision_id=request.repository_revision_id,
                profile_binding_key=request.profile_binding_key,
                anchors=(
                    CodeAnchor(
                        anchor_id="missing-endpoint",
                        kind=CodeAnchorKind.ENDPOINT,
                        value="GET /not-present",
                        evidence_refs=(evidence_ref,),
                    ),
                ),
            )
        )
        assert missing.confirmation_blocked
        assert missing.unknown_items == ("anchor_not_found:missing-endpoint",)
        assert missing.candidates == ()

        impact_service = ImpactReportService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        )
        impact_service._rag_quality = MagicMock()
        impact_request = ImpactReportRequest(
            impact_report_id=f"impact-report-{suffix}",
            scope=request,
            ui_impact_status=UiImpactStatus.IMPACTED,
            required_ui_scenario_refs=("expense-filter-default-all",),
            planned_test_files=("src/test/scripts/expense-status-search.sh",),
        )
        impact = impact_service.run(impact_request)
        replay = impact_service.run(impact_request)
        impact_repository = ImpactRepository(connection, contracts)
        impact_repository._rag_quality = MagicMock()

        assert impact.publication.created
        assert not replay.publication.created
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            impact_repository.publish_report(
                artifact=impact.artifact,
                repository_id="different-repository",
                repository_revision_id=revision_id,
            )
        assert impact.artifact["status"] == "awaiting_confirmation"
        assert impact.artifact["blocking_unknowns"] == []
        assert len(impact.artifact["items"]) == 2
        impact_item = next(
            item for item in impact.artifact["items"] if item["recommended_action"] == "modify"
        )
        assert impact_item["target_path"] == "src/main/java/example/ExpenseService.java"
        assert impact_item["recommended_action"] == "modify"
        assert impact_item["test_file_refs"] == ["src/test/java/example/ExpenseServiceTest.java"]
        item_id = str(impact_item["impact_item_id"])
        test_item = next(
            item for item in impact.artifact["items"] if item["recommended_action"] == "add"
        )
        assert test_item["target_path"] == "src/test/scripts/expense-status-search.sh"
        assert test_item["test_file_refs"] == ["src/test/scripts/expense-status-search.sh"]
        test_item_id = str(test_item["impact_item_id"])
        confirmation = {
            "artifact_type": "ImpactConfirmation",
            "schema_version": "v1",
            "confirmation_id": f"confirmation-{suffix}",
            "impact_report_id": impact_request.impact_report_id,
            "confirmed_by": "developer@example.invalid",
            "approved_item_ids": [item_id, test_item_id],
            "rejected_item_ids": [],
            "user_note": "Approved the bounded expense-list change.",
            "confirmed_at": "2026-07-15T12:00:00Z",
        }
        future_confirmation = {
            **confirmation,
            "confirmation_id": f"future-confirmation-{suffix}",
            "confirmed_at": "2999-01-01T00:00:00Z",
        }
        with pytest.raises(ValueError, match="must not be in the future"):
            impact_repository.confirm(
                project_id=project_id,
                analysis_case_id=case_id,
                artifact=future_confirmation,
            )
        confirmed = impact_repository.confirm(
            project_id=project_id,
            analysis_case_id=case_id,
            artifact=confirmation,
        )
        confirmation_replay = impact_repository.confirm(
            project_id=project_id,
            analysis_case_id=case_id,
            artifact=confirmation,
        )
        assert confirmed.created
        assert not confirmation_replay.created
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT confirmation_identity_probe")
            cursor.execute(
                """
                UPDATE impact_confirmations SET confirmed_by = 'drift@example.invalid'
                WHERE confirmation_id = %s
                """,
                (confirmation["confirmation_id"],),
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            impact_repository.confirm(
                project_id=project_id,
                analysis_case_id=case_id,
                artifact=confirmation,
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            impact_repository.get_state(impact_request.impact_report_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT confirmation_identity_probe")
            cursor.execute("RELEASE SAVEPOINT confirmation_identity_probe")
        confirmed_state = impact_repository.get_state(impact_request.impact_report_id)
        assert confirmed_state is not None
        assert confirmed_state.status == "confirmed"
        immutable_report = ArtifactRepository(connection, contracts).get(
            impact_request.impact_report_id
        )
        assert immutable_report is not None
        assert immutable_report["status"] == "awaiting_confirmation"

        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT impact_item_ledger_drift_probe")
            cursor.execute(
                """
                UPDATE impact_items
                SET rationale = 'drifted rationale'
                WHERE impact_report_id = %s
                """,
                (impact_request.impact_report_id,),
            )
        with pytest.raises(PersistenceConflictError, match="Item ledger differs"):
            impact_repository.get_state(impact_request.impact_report_id)
        with pytest.raises(PersistenceConflictError, match="Item ledger differs"):
            EditPacketRepository(connection, contracts).load_source(
                project_id=project_id,
                analysis_case_id=case_id,
                impact_report_id=impact_request.impact_report_id,
                confirmation_id=str(confirmation["confirmation_id"]),
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT impact_item_ledger_drift_probe")
            cursor.execute("RELEASE SAVEPOINT impact_item_ledger_drift_probe")

        packet_service = EditPacketService(connection=connection, contracts=contracts)
        with pytest.raises(ValueError, match="match forbidden globs"):
            packet_service.run(
                EditPacketRequest(
                    edit_packet_id=f"edit-packet-forbidden-{suffix}",
                    project_id=project_id,
                    analysis_case_id=case_id,
                    impact_report_id=impact_request.impact_report_id,
                    confirmation_id=str(confirmation["confirmation_id"]),
                    workspace_root=workspace,
                    forbidden_globs=("**/ExpenseService.java",),
                )
            )
        packet_request = EditPacketRequest(
            edit_packet_id=f"edit-packet-{suffix}",
            project_id=project_id,
            analysis_case_id=case_id,
            impact_report_id=impact_request.impact_report_id,
            confirmation_id=str(confirmation["confirmation_id"]),
            workspace_root=workspace,
            forbidden_globs=("**/.env", "**/pom.xml"),
            implementation_constraints=(
                (item_id, ("Keep the repository query contract unchanged.",)),
            ),
        )
        packet = packet_service.run(packet_request)
        packet_replay = packet_service.run(packet_request)
        assert packet.publication.created
        assert not packet_replay.publication.created
        assert packet.artifact["editable_files"] == ["src/main/java/example/ExpenseService.java"]
        assert packet.artifact["test_files"] == [
            "src/test/java/example/ExpenseServiceTest.java",
            "src/test/scripts/expense-status-search.sh",
        ]
        assert packet.artifact["read_only_files"] == []
        assert packet.artifact["must_not_fetch_context_package"] is True
        packet_repository = EditPacketRepository(connection, contracts)
        packet_source = packet_repository.load_source(
            project_id=project_id,
            analysis_case_id=case_id,
            impact_report_id=impact_request.impact_report_id,
            confirmation_id=str(confirmation["confirmation_id"]),
        )
        widened_packet = json.loads(json.dumps(packet.artifact))
        widened_packet["edit_packet_id"] = f"edit-packet-widened-{suffix}"
        widened_packet["editable_files"] = ["src/main/java/example/Unapproved.java"]
        with pytest.raises(ValueError, match="file scope is not derived"):
            packet_repository.publish(artifact=widened_packet, source=packet_source)
        approval_repository = ApprovalGrantRepository(connection, contracts)
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT packet_normalized_scope_probe")
            cursor.execute(
                """
                UPDATE edit_packets
                SET editable_files = '["src/main/java/example/Unapproved.java"]'::jsonb
                WHERE edit_packet_id = %s
                """,
                (packet_request.edit_packet_id,),
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            packet_repository.get(packet_request.edit_packet_id)
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            approval_repository.load_source(
                project_id=project_id,
                analysis_case_id=case_id,
                edit_packet_id=packet_request.edit_packet_id,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT packet_normalized_scope_probe")
            cursor.execute("RELEASE SAVEPOINT packet_normalized_scope_probe")
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT packet_replay_status_probe")
            cursor.execute(
                "UPDATE edit_packets SET status = 'superseded' WHERE edit_packet_id = %s",
                (packet_request.edit_packet_id,),
            )
        superseded_replay = packet_repository.publish(
            artifact=packet.artifact,
            source=packet_source,
        )
        assert not superseded_replay.created
        assert superseded_replay.status == "superseded"
        with pytest.raises(ValueError, match="active Edit Packet"):
            approval_repository.load_source(
                project_id=project_id,
                analysis_case_id=case_id,
                edit_packet_id=packet_request.edit_packet_id,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT packet_replay_status_probe")
            cursor.execute("RELEASE SAVEPOINT packet_replay_status_probe")
            cursor.execute("SAVEPOINT grant_case_state_probe")
            cursor.execute(
                """
                UPDATE analysis_cases SET status = 'awaiting_confirmation'
                WHERE analysis_case_id = %s AND project_id = %s
                """,
                (case_id, project_id),
            )
        with pytest.raises(ValueError, match="editing state"):
            approval_repository.load_source(
                project_id=project_id,
                analysis_case_id=case_id,
                edit_packet_id=packet_request.edit_packet_id,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT grant_case_state_probe")
            cursor.execute("RELEASE SAVEPOINT grant_case_state_probe")
        grant_id = f"approval-grant-{suffix}"
        grant_request = ApprovalGrantRequest(
            grant_id=grant_id,
            project_id=project_id,
            analysis_case_id=case_id,
            edit_packet_id=packet_request.edit_packet_id,
            approved_by="reviewer@example.invalid",
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            command_profile_binding_key=command_profile_binding_key,
            allowed_test_command_refs=("targeted-unit",),
        )
        grant_service = ApprovalGrantService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        )
        grant = grant_service.issue(grant_request)
        grant_replay = grant_service.issue(grant_request)
        assert grant.record.created
        assert not grant_replay.record.created
        assert grant.record.state == "active_editing"
        grant_repository = ApprovalGrantRepository(connection, contracts)
        assert grant_repository.inspect(grant_id).state == "active_editing"
        assert grant.artifact["command_profile_version_id"] == command_profile_version_id

        copilot_workspace = tmp_path / f"copilot-linked-{suffix}"
        _git_workspace(
            workspace,
            "worktree",
            "add",
            "--detach",
            str(copilot_workspace),
            commit_sha,
        )
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT copilot_coding_task_poc")
        change_request_id = f"change-request-copilot-{suffix}"
        WebControlPlaneService(connection=connection, repository_root=ROOT).submit_change_request(
            ChangeRequestInput(
                change_request_id=change_request_id,
                project_id=project_id,
                analysis_case_id=case_id,
                input_mode="natural_language",
                requirement_text="差戻し検索を承認済み範囲で実装する",
                source_document_ref=None,
                target_document_ref=None,
                business_rules=(
                    BusinessRuleInput(
                        business_rule_id=f"business-rule-copilot-{suffix}",
                        text="差戻し状態を検索できる",
                        source_refs=(),
                    ),
                ),
                ambiguity_status="clear",
                ambiguities=(),
                submitted_by="reviewer@example.invalid",
            )
        )
        cancelled_task_id = f"copilot-coding-task-cancelled-{suffix}"
        coding_tasks = CopilotCodingTaskService(
            connection=connection,
            repository_root=ROOT,
        )
        task_request = CopilotCodingTaskPublishRequest(
            coding_task_id=cancelled_task_id,
            change_request_id=change_request_id,
            project_id=project_id,
            edit_packet_id=packet_request.edit_packet_id,
            approval_grant_id=grant_id,
            workspace_root=copilot_workspace,
            task_summary="差戻し検索を実装し、承認済みテストを実行する",
            actor="reviewer@example.invalid",
            idempotency_key="coding-task-poc",
        )
        published_task = coding_tasks.publish(task_request)
        replayed_task = coding_tasks.publish(task_request)
        assert published_task["created"] is True
        assert replayed_task["created"] is False
        assert published_task["task"]["execution_mode"] == "copilot_coding_plan"
        claimed_task = coding_tasks.claim_next(
            workspace_root=copilot_workspace,
            consumer_id="vscode-integration",
        )
        assert claimed_task is not None
        assert claimed_task["state"] == "pending_confirmation"
        assert claimed_task["claim_expires_at"] is not None
        assert (
            coding_tasks.claim_next(
                workspace_root=copilot_workspace,
                consumer_id="vscode-other",
            )
            is None
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE copilot_coding_tasks
                SET claimed_at = now() - interval '2 minutes',
                    claim_expires_at = now() - interval '1 second'
                WHERE coding_task_id = %s
                """,
                (cancelled_task_id,),
            )
        recovered_task = coding_tasks.claim_next(
            workspace_root=copilot_workspace,
            consumer_id="vscode-recovered",
        )
        assert recovered_task is not None
        assert recovered_task["claimed_by"] == "vscode-recovered"
        assert recovered_task["events"][-1]["event_type"] == "claim_recovered"
        with pytest.raises(ValueError, match="lease is not held"):
            coding_tasks.accept(
                coding_task_id=cancelled_task_id,
                workspace_root=copilot_workspace,
                consumer_id="vscode-integration",
                actor="developer@example.invalid",
            )
        cancelled_task = coding_tasks.cancel(
            coding_task_id=cancelled_task_id,
            change_request_id=change_request_id,
            actor="developer@example.invalid",
            reason="Test the explicit VS Code cancellation and retry path",
            idempotency_key="cancel-before-retry",
            consumer_id="vscode-recovered",
        )
        assert cancelled_task["state"] == "cancelled"
        coding_task_id = f"copilot-coding-task-retry-{suffix}"
        retried_task = coding_tasks.retry(
            coding_task_id=cancelled_task_id,
            retry_coding_task_id=coding_task_id,
            change_request_id=change_request_id,
            actor="developer@example.invalid",
            idempotency_key="retry-after-cancel",
            edit_packet_id=packet_request.edit_packet_id,
            approval_grant_id=grant_id,
            workspace_root=copilot_workspace,
        )
        assert retried_task["task"]["retry_of_coding_task_id"] == cancelled_task_id
        assert retried_task["attempt_number"] == 2
        claimed_task = coding_tasks.claim_next(
            workspace_root=copilot_workspace,
            consumer_id="vscode-integration",
        )
        assert claimed_task is not None
        with pytest.raises(ValueError, match="requires VS Code user confirmation"):
            coding_tasks.get_mcp_context(
                coding_task_id=coding_task_id,
                workspace_root=copilot_workspace,
            )
        accepted_task = coding_tasks.accept(
            coding_task_id=coding_task_id,
            workspace_root=copilot_workspace,
            consumer_id="vscode-integration",
            actor="developer@example.invalid",
        )
        assert accepted_task["state"] == "accepted"
        task_dispatcher = CopilotToolDispatcher(connection=connection, root=ROOT)
        task_context = task_dispatcher.call(
            "copilot_get_coding_task",
            {
                "coding_task_id": coding_task_id,
                "workspace_root": str(copilot_workspace),
            },
        )
        assert task_context["coding_task"]["coding_task_id"] == coding_task_id
        assert task_context["coding_plan"]["mode"] == "copilot_coding_plan"
        assert task_context["context_package_available"] is False
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE copilot_coding_tasks
                SET claimed_at = now() - interval '2 minutes',
                    claim_expires_at = now() - interval '1 second'
                WHERE coding_task_id = %s
                """,
                (coding_task_id,),
            )
        resumed_task = coding_tasks.resume(
            coding_task_id=coding_task_id,
            workspace_root=copilot_workspace,
            consumer_id="vscode-after-disconnect",
        )
        assert resumed_task["state"] == "in_progress"
        assert resumed_task["claimed_by"] == "vscode-after-disconnect"

        copilot_service_path = copilot_workspace / "src/main/java/example/ExpenseService.java"
        copilot_test_path = copilot_workspace / "src/test/java/example/ExpenseServiceTest.java"
        copilot_service_path.write_text(
            "class ExpenseService { int bridgeUpdated; }\n", encoding="utf-8"
        )
        copilot_test_path.write_text(
            "class ExpenseServiceTest { int bridgeUpdated; }\n", encoding="utf-8"
        )
        task_command_id = f"copilot-task-command-{suffix}"
        task_command = task_dispatcher.call(
            "copilot_run_task_command",
            {
                "coding_task_id": coding_task_id,
                "workspace_root": str(copilot_workspace),
                "command_execution_id": task_command_id,
                "command_ref": "targeted-unit",
            },
        )
        assert task_command["status"] == "passed"
        task_working_result_id = f"copilot-task-working-{suffix}"
        task_diff = task_dispatcher.call(
            "copilot_validate_task_diff",
            {
                "coding_task_id": coding_task_id,
                "workspace_root": str(copilot_workspace),
                "edit_result_id": task_working_result_id,
            },
        )
        assert task_diff["status"] == "in_scope"
        assert task_diff["coding_task_state"] == "in_progress"
        _git_workspace(copilot_workspace, "add", "-A")
        _git_workspace(
            copilot_workspace,
            "-c",
            "user.name=OperaMind Test",
            "-c",
            "user.email=operamind@example.invalid",
            "commit",
            "-q",
            "-m",
            "Copilot Coding Task POC",
        )
        task_committed_result_id = f"copilot-task-committed-{suffix}"
        task_result = task_dispatcher.call(
            "copilot_record_task_result",
            {
                "coding_task_id": coding_task_id,
                "workspace_root": str(copilot_workspace),
                "edit_result_id": task_committed_result_id,
                "test_result_refs": [task_command_id],
                "tests_passed": True,
                "changed_line_coverage": {
                    "evidence_refs": [task_command_id],
                    "executable_lines": {
                        "src/main/java/example/ExpenseService.java": [1],
                        "src/test/java/example/ExpenseServiceTest.java": [1],
                    },
                    "covered_lines": {
                        "src/main/java/example/ExpenseService.java": [1],
                        "src/test/java/example/ExpenseServiceTest.java": [1],
                    },
                    "minimum_coverage_percent": 80,
                },
            },
        )
        assert task_result["status"] == "in_scope"
        assert task_result["coding_task_state"] == "completed"
        final_task = coding_tasks.view(coding_task_id)
        assert final_task["state"] == "completed"
        assert [item["status"] for item in final_task["commands"]] == ["passed"]
        assert [item["validation_mode"] for item in final_task["edit_results"]] == [
            "working",
            "committed",
        ]
        assert [event["event_type"] for event in final_task["events"]] == [
            "published",
            "claimed",
            "accepted",
            "context_loaded",
            "claim_recovered",
            "command_recorded",
            "diff_recorded",
            "result_recorded",
        ]
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT copilot_coding_task_poc")
            cursor.execute("RELEASE SAVEPOINT copilot_coding_task_poc")
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT grant_identity_probe")
            cursor.execute(
                """
                UPDATE approval_grants
                SET allowed_actions = allowed_actions || '["record_evidence"]'::jsonb
                WHERE approval_grant_id = %s
                """,
                (grant_id,),
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            grant_repository.inspect(grant_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT grant_identity_probe")
            cursor.execute("RELEASE SAVEPOINT grant_identity_probe")
            cursor.execute("SAVEPOINT grant_command_profile_probe")
            cursor.execute(
                """
                UPDATE profile_versions
                SET payload = jsonb_set(payload, '{templates,0,timeout_seconds}', '11'::jsonb)
                WHERE profile_version_id = %s
                """,
                (command_profile_version_id,),
            )
        with pytest.raises(PersistenceConflictError, match="Command Profile Version"):
            grant_repository.inspect(grant_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT grant_command_profile_probe")
            cursor.execute("RELEASE SAVEPOINT grant_command_profile_probe")
            cursor.execute("SAVEPOINT grant_packet_scope_probe")
            cursor.execute(
                """
                UPDATE edit_packets
                SET allowed_items = jsonb_set(
                    allowed_items,
                    '{0,business_summary}',
                    '"drifted summary"'::jsonb
                )
                WHERE edit_packet_id = %s
                """,
                (packet_request.edit_packet_id,),
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            grant_repository.inspect(grant_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT grant_packet_scope_probe")
            cursor.execute("RELEASE SAVEPOINT grant_packet_scope_probe")

        invalidation_updates = (
            (
                "UPDATE edit_packets SET status = 'superseded' WHERE edit_packet_id = %s",
                packet_request.edit_packet_id,
            ),
            (
                "UPDATE impact_reports SET status = 'superseded' WHERE impact_report_id = %s",
                impact_request.impact_report_id,
            ),
            (
                "UPDATE code_graph_snapshots SET status = 'stale', is_current = false "
                "WHERE code_graph_snapshot_id = %s",
                graph_id,
            ),
            (
                "UPDATE analysis_cases SET status = 'reanalysis_required' "
                "WHERE analysis_case_id = %s",
                case_id,
            ),
        )
        for index, (statement, identity) in enumerate(invalidation_updates):
            savepoint = f"grant_source_invalidation_{index}"
            with connection.cursor() as cursor:
                cursor.execute(f"SAVEPOINT {savepoint}")
                cursor.execute(statement, (identity,))
            with pytest.raises(ValueError, match="source is no longer current"):
                grant_repository.authorize_edit(
                    grant_id=grant_id,
                    project_id=project_id,
                    analysis_case_id=case_id,
                    edit_packet_id=packet_request.edit_packet_id,
                )
            with connection.cursor() as cursor:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                cursor.execute(f"RELEASE SAVEPOINT {savepoint}")

        replacement_profile = json.loads(json.dumps(command_profile))
        replacement_profile["profile_version"] = "2.0.0"
        replacement_profile["templates"][0]["argv"] = [
            "git",
            "rev-parse",
            "--verify",
            "definitely-missing-ref",
        ]
        replacement_profile_version_id = f"command-profile-replacement-{suffix}"
        profile_repository.store_version(
            profile_version_id=replacement_profile_version_id,
            profile=replacement_profile,
        )
        profile_repository.activate(
            activation_event_id=f"command-profile-replacement-activation-{suffix}",
            project_id=project_id,
            binding_key=command_profile_binding_key,
            profile_version_id=replacement_profile_version_id,
            activated_by="reviewer@example.invalid",
            reason="Exercise grant-bound Profile version isolation",
        )
        mcp_server = OperaMindMcpServer(CopilotToolDispatcher(connection=connection, root=ROOT))
        initialized = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "integration-test", "version": "1.0.0"},
                },
            }
        )
        assert initialized is not None
        assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        ready_cases_response = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "analysis_list_ready_cases",
                    "arguments": {"workspace_root": str(workspace), "limit": 10},
                },
            }
        )
        assert ready_cases_response is not None
        ready_cases = ready_cases_response["result"]["structuredContent"]["cases"]
        assert [case["analysis_case_id"] for case in ready_cases] == [case_id]
        assert ready_cases[0]["approval_grant_state"] == "active_editing"
        impact_query_response = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "impact_get_report",
                    "arguments": {
                        "project_id": project_id,
                        "analysis_case_id": case_id,
                        "impact_report_id": impact_request.impact_report_id,
                    },
                },
            }
        )
        assert impact_query_response is not None
        impact_query = impact_query_response["result"]["structuredContent"]
        assert impact_query["artifact"]["impact_report_id"] == impact_request.impact_report_id
        assert impact_query["current_status"] == "confirmed"
        handoff_response = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "copilot_get_edit_packet",
                    "arguments": {
                        "project_id": project_id,
                        "analysis_case_id": case_id,
                        "edit_packet_id": packet_request.edit_packet_id,
                        "approval_grant_id": grant_id,
                        "workspace_root": str(workspace),
                    },
                },
            }
        )
        assert handoff_response is not None
        handoff_result = handoff_response["result"]
        assert handoff_result["isError"] is False
        handoff = handoff_result["structuredContent"]
        assert handoff["edit_packet"]["edit_packet_id"] == packet_request.edit_packet_id
        assert handoff["approval"]["command_profile_version_id"] == command_profile_version_id
        assert handoff["context_package_available"] is False
        command_execution_id = f"command-execution-{suffix}"
        command_request = ApprovedCommandRequest(
            command_execution_id=command_execution_id,
            approval_grant_id=grant_id,
            project_id=project_id,
            analysis_case_id=case_id,
            edit_packet_id=packet_request.edit_packet_id,
            workspace_root=workspace,
            command_ref="targeted-unit",
        )
        command_service = ApprovedCommandService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        )
        command_result = command_service.run(command_request)
        command_replay = command_service.run(command_request)
        assert command_result.record.created
        assert command_result.record.status == "passed"
        assert command_result.record.exit_code == 0
        assert command_result.command_profile_version_id == command_profile_version_id
        assert not command_replay.record.created
        mcp_command_response = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "copilot_run_approved_command",
                    "arguments": {
                        "project_id": project_id,
                        "analysis_case_id": case_id,
                        "edit_packet_id": packet_request.edit_packet_id,
                        "approval_grant_id": grant_id,
                        "workspace_root": str(workspace),
                        "command_execution_id": command_execution_id,
                        "command_ref": "targeted-unit",
                    },
                },
            }
        )
        assert mcp_command_response is not None
        assert mcp_command_response["result"]["isError"] is False
        assert mcp_command_response["result"]["structuredContent"]["created"] is False
        with pytest.raises(ValueError, match="does not allow command_ref"):
            command_service.run(
                ApprovedCommandRequest(
                    command_execution_id=f"unapproved-command-execution-{suffix}",
                    approval_grant_id=grant_id,
                    project_id=project_id,
                    analysis_case_id=case_id,
                    edit_packet_id=packet_request.edit_packet_id,
                    workspace_root=workspace,
                    command_ref="module-integration",
                )
            )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT command_profile_version_id, command_ref, workspace_root,
                       stdout_digest, stderr_digest
                FROM command_execution_requests AS request
                JOIN command_execution_results AS result
                  USING (command_execution_id, project_id)
                WHERE command_execution_id = %s
                """,
                (command_execution_id,),
            )
            command_audit = cursor.fetchone()
        assert command_audit is not None
        assert command_audit[:3] == (
            command_profile_version_id,
            "targeted-unit",
            str(workspace),
        )
        assert all(len(str(value)) == 64 for value in command_audit[3:])
        incomplete_request = ApprovedCommandRequest(
            command_execution_id=f"incomplete-command-execution-{suffix}",
            approval_grant_id=grant_id,
            project_id=project_id,
            analysis_case_id=case_id,
            edit_packet_id=packet_request.edit_packet_id,
            workspace_root=workspace,
            command_ref="targeted-unit",
        )
        with monkeypatch.context() as execution_patch:
            execution_patch.setattr(
                "operamind.application.command_execution._execute",
                lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated runner crash")),
            )
            with pytest.raises(RuntimeError, match="simulated runner crash"):
                command_service.run(incomplete_request)
        with pytest.raises(RuntimeError, match="operator review is required"):
            command_service.run(incomplete_request)
        recovery_service = CommandExecutionRecoveryService(
            connection=connection,
            contracts=contracts,
        )
        with pytest.raises(ValueError, match="newer than the recovery boundary"):
            recovery_service.run(
                CommandExecutionRecoveryRequest(
                    recovery_id=f"future-command-recovery-{suffix}",
                    command_execution_id=incomplete_request.command_execution_id,
                    project_id=project_id,
                    actor="operator@example.invalid",
                    reason="must not recover against a future boundary",
                    stale_before=datetime.now(UTC) + timedelta(minutes=1),
                )
            )
        command_stale_before = datetime.now(UTC)
        command_recovery_request = CommandExecutionRecoveryRequest(
            recovery_id=f"command-recovery-{suffix}",
            command_execution_id=incomplete_request.command_execution_id,
            project_id=project_id,
            actor="operator@example.invalid",
            reason="approved command worker process was interrupted",
            stale_before=command_stale_before,
        )
        interrupted = recovery_service.run(command_recovery_request)
        interrupted_replay = recovery_service.run(command_recovery_request)
        interrupted_command_replay = command_service.run(incomplete_request)
        assert interrupted.created
        assert not interrupted_replay.created
        assert interrupted.status == "interrupted"
        assert interrupted.recovery_id == command_recovery_request.recovery_id
        assert interrupted.recovery_actor == "operator@example.invalid"
        assert interrupted.recovery_stale_before == command_stale_before
        assert interrupted.stdout_digest == hashlib.sha256(b"").hexdigest()
        assert interrupted_command_replay.record.status == "interrupted"
        assert not interrupted_command_replay.record.created
        with pytest.raises(RuntimeError, match="different terminal result"):
            recovery_service.run(
                CommandExecutionRecoveryRequest(
                    recovery_id=command_recovery_request.recovery_id,
                    command_execution_id=incomplete_request.command_execution_id,
                    project_id=project_id,
                    actor="operator@example.invalid",
                    reason="different recovery reason",
                    stale_before=command_stale_before,
                )
            )
        assert (
            grant_repository.inspect(
                grant_id,
                at=grant_request.expires_at + timedelta(seconds=1),
            ).state
            == "expired"
        )
        with pytest.raises(ValueError, match="state: expired"):
            grant_repository.authorize_edit(
                grant_id=grant_id,
                project_id=project_id,
                analysis_case_id=case_id,
                edit_packet_id=packet_request.edit_packet_id,
                at=grant_request.expires_at + timedelta(seconds=1),
            )
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT grant_revoke_probe")
        revoked = grant_service.revoke(
            event_id=f"approval-revocation-{suffix}",
            grant_id=grant_id,
            project_id=project_id,
            revoked_by="reviewer@example.invalid",
            reason="Exercise the append-only revocation gate",
        )
        revoked_replay = grant_service.revoke(
            event_id=f"approval-revocation-{suffix}",
            grant_id=grant_id,
            project_id=project_id,
            revoked_by="reviewer@example.invalid",
            reason="Exercise the append-only revocation gate",
        )
        assert revoked
        assert not revoked_replay
        assert grant_repository.inspect(grant_id).state == "revoked"
        with pytest.raises(ValueError, match="state: revoked"):
            grant_repository.authorize_edit(
                grant_id=grant_id,
                project_id=project_id,
                analysis_case_id=case_id,
                edit_packet_id=packet_request.edit_packet_id,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT grant_revoke_probe")
            cursor.execute("RELEASE SAVEPOINT grant_revoke_probe")
        dirty_file = workspace / "untracked.txt"
        dirty_file.write_text("outside revision\n", encoding="utf-8")
        with pytest.raises(ValueError, match="clean Git worktree"):
            packet_service.run(
                EditPacketRequest(
                    edit_packet_id=f"edit-packet-dirty-{suffix}",
                    project_id=project_id,
                    analysis_case_id=case_id,
                    impact_report_id=impact_request.impact_report_id,
                    confirmation_id=str(confirmation["confirmation_id"]),
                    workspace_root=workspace,
                    forbidden_globs=("**/.env",),
                )
            )
        dirty_file.unlink()
        edit_result_service = EditResultService(connection=connection, contracts=contracts)
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT out_of_scope_probe")
        dirty_file.write_text("outside packet\n", encoding="utf-8")
        out_of_scope = edit_result_service.run(
            EditResultRequest(
                edit_result_id=f"edit-result-out-of-scope-{suffix}",
                edit_packet_id=packet_request.edit_packet_id,
                approval_grant_id=grant_id,
                project_id=project_id,
                analysis_case_id=case_id,
                workspace_root=workspace,
                mode=EditValidationMode.WORKING,
            )
        )
        assert out_of_scope.record.status == "out_of_scope"
        assert out_of_scope.out_of_scope_files == ("untracked.txt",)
        assert grant_repository.inspect(grant_id).state == "revoked"
        dirty_file.unlink()
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT out_of_scope_probe")
            cursor.execute("RELEASE SAVEPOINT out_of_scope_probe")

        service_path = workspace / "src/main/java/example/ExpenseService.java"
        test_path = workspace / "src/test/java/example/ExpenseServiceTest.java"
        service_path.write_text("class ExpenseService { int updated; }\n", encoding="utf-8")
        test_path.write_text("class ExpenseServiceTest { int updated; }\n", encoding="utf-8")
        working_request = EditResultRequest(
            edit_result_id=f"edit-result-working-{suffix}",
            edit_packet_id=packet_request.edit_packet_id,
            approval_grant_id=grant_id,
            project_id=project_id,
            analysis_case_id=case_id,
            workspace_root=workspace,
            mode=EditValidationMode.WORKING,
        )
        working = edit_result_service.run(working_request)
        working_replay = edit_result_service.run(working_request)
        assert working.record.created
        assert not working_replay.record.created
        assert working.record.status == "in_scope"
        assert working.out_of_scope_files == ()

        _git_workspace(workspace, "add", "-A")
        _git_workspace(
            workspace,
            "-c",
            "user.name=OperaMind Test",
            "-c",
            "user.email=operamind@example.invalid",
            "commit",
            "-q",
            "-m",
            "approved edit",
        )
        with pytest.raises(ValueError, match="command evidence does not exist"):
            edit_result_service.run(
                EditResultRequest(
                    edit_result_id=f"edit-result-unverified-test-{suffix}",
                    edit_packet_id=packet_request.edit_packet_id,
                    approval_grant_id=grant_id,
                    project_id=project_id,
                    analysis_case_id=case_id,
                    workspace_root=workspace,
                    mode=EditValidationMode.COMMITTED,
                    test_result_refs=("unverified-test-result",),
                    tests_passed=True,
                )
            )
        committed = edit_result_service.run(
            EditResultRequest(
                edit_result_id=f"edit-result-committed-{suffix}",
                edit_packet_id=packet_request.edit_packet_id,
                approval_grant_id=grant_id,
                project_id=project_id,
                analysis_case_id=case_id,
                workspace_root=workspace,
                mode=EditValidationMode.COMMITTED,
                test_result_refs=(command_execution_id,),
                tests_passed=True,
                changed_line_coverage=ChangedLineCoverageEvidence(
                    evidence_refs=(command_execution_id,),
                    executable_lines=(
                        ("src/main/java/example/ExpenseService.java", (1,)),
                        ("src/test/java/example/ExpenseServiceTest.java", (1,)),
                    ),
                    covered_lines=(
                        ("src/main/java/example/ExpenseService.java", (1,)),
                        ("src/test/java/example/ExpenseServiceTest.java", (1,)),
                    ),
                ),
            )
        )
        assert committed.record.status == "in_scope"
        assert committed.record.case_status == "verifying_ui"
        assert committed.record.command_evidence_status == "verified"
        assert grant_repository.inspect(grant_id).state == "ui_pending"
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT grant_event_identity_probe")
            cursor.execute(
                """
                UPDATE approval_grant_events SET reason = 'drifted event reason'
                WHERE approval_grant_id = %s AND event_type = 'edit_completed'
                """,
                (grant_id,),
            )
        with pytest.raises(PersistenceConflictError, match="Event normalized identity differs"):
            grant_repository.inspect(grant_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT grant_event_identity_probe")
            cursor.execute("RELEASE SAVEPOINT grant_event_identity_probe")
        assert committed.result_repository_revision == _git_workspace(
            workspace, "rev-parse", "HEAD"
        )
        assert (
            grant_repository.authorize_ui(
                grant_id=grant_id,
                project_id=project_id,
                edit_packet_id=packet_request.edit_packet_id,
                scenario_refs=("expense-filter-default-all",),
            ).state
            == "ui_pending"
        )
        ui_invalidation_updates = (
            (
                "UPDATE impact_reports SET status = 'superseded' WHERE impact_report_id = %s",
                impact_request.impact_report_id,
            ),
            (
                "UPDATE code_graph_snapshots SET status = 'stale', is_current = false "
                "WHERE code_graph_snapshot_id = %s",
                graph_id,
            ),
            (
                "UPDATE analysis_cases SET status = 'reanalysis_required' "
                "WHERE analysis_case_id = %s",
                case_id,
            ),
            (
                "UPDATE edit_results SET command_evidence_status = 'legacy_unverified' "
                "WHERE edit_result_id = %s",
                committed.record.edit_result_id,
            ),
        )
        for index, (statement, identity) in enumerate(ui_invalidation_updates):
            savepoint = f"grant_ui_source_invalidation_{index}"
            with connection.cursor() as cursor:
                cursor.execute(f"SAVEPOINT {savepoint}")
                cursor.execute(statement, (identity,))
            with pytest.raises(ValueError, match="current for UI verification"):
                grant_repository.authorize_ui(
                    grant_id=grant_id,
                    project_id=project_id,
                    edit_packet_id=packet_request.edit_packet_id,
                    scenario_refs=("expense-filter-default-all",),
                )
            with connection.cursor() as cursor:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
        verifying_cases_response = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 61,
                "method": "tools/call",
                "params": {
                    "name": "analysis_list_ready_cases",
                    "arguments": {"workspace_root": str(workspace), "limit": 10},
                },
            }
        )
        assert verifying_cases_response is not None
        verifying_cases = verifying_cases_response["result"]["structuredContent"]["cases"]
        assert [case["analysis_case_id"] for case in verifying_cases] == [case_id]
        assert verifying_cases[0]["base_revision"] == commit_sha
        assert verifying_cases[0]["head_revision"] == committed.result_repository_revision
        assert verifying_cases[0]["edit_packet_status"] == "superseded"
        assert verifying_cases[0]["approval_grant_state"] == "ui_pending"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT command_execution_id
                FROM edit_result_command_executions
                WHERE edit_result_id = %s
                """,
                (committed.record.edit_result_id,),
            )
            assert cursor.fetchall() == [(command_execution_id,)]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM analysis_cases WHERE analysis_case_id = %s",
                (case_id,),
            )
            assert cursor.fetchone() == ("verifying_ui",)

        ui_service = UiVerificationService(connection=connection, contracts=contracts)
        scenario_id = "expense-filter-default-all"
        scenario_version_id = f"scenario-version-{suffix}"
        assert ui_service.register_scenario(
            VerificationScenarioWrite(
                scenario_version_id=scenario_version_id,
                project_id=project_id,
                scenario_id=scenario_id,
                scenario_version="v1",
                title="既定値ですべての経費を表示する",
                preconditions=("四件の経費データが存在する",),
                steps=("経費一覧画面を開く",),
                expected_visible_results=("All が選択され、四件すべてが表示される",),
                evidence_requirements=("screenshot", "assertion"),
                trigger_path="/expenses",
                data_recipe_ref="expense-seed-v1",
                review_status="approved",
                activate=True,
            )
        )
        assert not ui_service.register_scenario(
            VerificationScenarioWrite(
                scenario_version_id=scenario_version_id,
                project_id=project_id,
                scenario_id=scenario_id,
                scenario_version="v1",
                title="既定値ですべての経費を表示する",
                preconditions=("四件の経費データが存在する",),
                steps=("経費一覧画面を開く",),
                expected_visible_results=("All が選択され、四件すべてが表示される",),
                evidence_requirements=("screenshot", "assertion"),
                trigger_path="/expenses",
                data_recipe_ref="expense-seed-v1",
                review_status="approved",
                activate=True,
            )
        )
        assert committed.result_repository_revision is not None
        plan_id = f"ui-plan-{suffix}"
        deployment_revision = f"deployment-{suffix}"
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT legacy_command_evidence_probe")
            cursor.execute(
                """
                UPDATE edit_results SET command_evidence_status = 'legacy_unverified'
                WHERE edit_result_id = %s
                """,
                (committed.record.edit_result_id,),
            )
        with pytest.raises(ValueError, match="verified command evidence"):
            ui_service.build_plan(
                deployment=UiDeploymentWrite(
                    project_id=project_id,
                    environment_id=f"environment-legacy-{suffix}",
                    base_url="http://127.0.0.1:8081",
                    deployment_revision=f"deployment-legacy-{suffix}",
                    repository_revision=committed.result_repository_revision,
                ),
                plan=UiExecutionPlanWrite(
                    plan_id=f"ui-plan-legacy-{suffix}",
                    project_id=project_id,
                    analysis_case_id=case_id,
                    edit_packet_id=packet_request.edit_packet_id,
                    edit_result_id=committed.record.edit_result_id,
                    environment_id=f"environment-legacy-{suffix}",
                    deployment_revision=f"deployment-legacy-{suffix}",
                ),
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT legacy_command_evidence_probe")
            cursor.execute("RELEASE SAVEPOINT legacy_command_evidence_probe")
        deployment_write = UiDeploymentWrite(
            project_id=project_id,
            environment_id=f"environment-{suffix}",
            base_url="http://127.0.0.1:8080",
            deployment_revision=deployment_revision,
            repository_revision=committed.result_repository_revision,
        )
        plan_write = UiExecutionPlanWrite(
            plan_id=plan_id,
            project_id=project_id,
            analysis_case_id=case_id,
            edit_packet_id=packet_request.edit_packet_id,
            edit_result_id=committed.record.edit_result_id,
            environment_id=f"environment-{suffix}",
            deployment_revision=deployment_revision,
        )
        plan = ui_service.build_plan(deployment=deployment_write, plan=plan_write)
        plan_replay = ui_service.build_plan(deployment=deployment_write, plan=plan_write)
        assert plan.created
        assert not plan_replay.created
        assert plan.status == "preflight_pending"
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT plan_identity_probe")
            cursor.execute(
                """
                UPDATE ui_execution_plans SET repository_revision = 'drifted-revision'
                WHERE ui_execution_plan_id = %s
                """,
                (plan_id,),
            )
        with pytest.raises(PersistenceConflictError, match="different scope"):
            ui_service.build_plan(deployment=deployment_write, plan=plan_write)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT plan_identity_probe")
            cursor.execute("RELEASE SAVEPOINT plan_identity_probe")
        plan_query_response = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "verification_get_ui_plan",
                    "arguments": {"project_id": project_id, "plan_id": plan_id},
                },
            }
        )
        assert plan_query_response is not None
        plan_query = plan_query_response["result"]["structuredContent"]
        assert plan_query["deployment_revision"] == deployment_revision
        assert plan_query["repository_revision"] == committed.result_repository_revision
        assert plan_query["repository_binding_status"] == "verified"
        assert plan_query["scenario_versions"] == [
            {
                "scenario_id": scenario_id,
                "scenario_version_id": scenario_version_id,
                "execution_order": 1,
            }
        ]
        canonical_proposal = UiKnowledgeProposalService(
            connection=connection,
            contracts=contracts,
        ).propose(
            UiKnowledgeProposalRequest(
                project_id=project_id,
                document_snapshot_id=after.snapshot_id,
                environment_id=f"environment-{suffix}",
                deployment_revision=deployment_revision,
                snapshot_id=f"ui-knowledge-proposal-{suffix}",
                snapshot_version="proposal-1",
            )
        )
        assert canonical_proposal.snapshot is None
        assert {item.code for item in canonical_proposal.issues} == {
            "business_name_missing",
            "screen_name_missing",
        }
        checks = tuple(
            UiPreflightCheckWrite(
                check_id=f"preflight-{check_type}-{suffix}",
                check_type=check_type,
                status="passed",
                evidence_ref=f"preflight-evidence:{check_type}",
            )
            for check_type in (
                "environment",
                "authentication",
                "test_data",
                "trigger_path",
                "locator",
            )
        )
        with pytest.raises(ValueError, match="each required check"):
            ui_service.record_preflight(
                project_id=project_id,
                plan_id=plan_id,
                attempt_id=f"preflight-incomplete-{suffix}",
                checks=checks[:-1],
            )
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT preflight_source_probe")
            cursor.execute(
                "UPDATE ui_environments SET status = 'inactive' WHERE environment_id = %s",
                (deployment_write.environment_id,),
            )
        with pytest.raises(ValueError, match="source is no longer current"):
            ui_service.record_preflight(
                project_id=project_id,
                plan_id=plan_id,
                attempt_id=f"preflight-stale-source-{suffix}",
                checks=checks,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT preflight_source_probe")
            cursor.execute("RELEASE SAVEPOINT preflight_source_probe")
        blocked_checks = tuple(
            UiPreflightCheckWrite(
                check_id=f"preflight-blocked-{check.check_type}-{suffix}",
                check_type=check.check_type,
                status="blocked" if check.check_type == "environment" else "passed",
                evidence_ref=check.evidence_ref,
                reason="Target is starting" if check.check_type == "environment" else None,
            )
            for check in checks
        )
        blocked_preflight = ui_service.record_preflight(
            project_id=project_id,
            plan_id=plan_id,
            attempt_id=f"preflight-attempt-blocked-{suffix}",
            checks=blocked_checks,
        )
        assert blocked_preflight.status == "blocked"
        ready = ui_service.record_preflight(
            project_id=project_id,
            plan_id=plan_id,
            attempt_id=f"preflight-attempt-passed-{suffix}",
            checks=checks,
        )
        assert ready.status == "ready"
        ready_replay = ui_service.record_preflight(
            project_id=project_id,
            plan_id=plan_id,
            attempt_id=f"preflight-attempt-passed-{suffix}",
            checks=checks,
        )
        assert ready_replay.status == "ready"
        packet_test_files = set(packet.artifact["test_files"])
        impact_item_id = str(
            next(
                item["impact_item_id"]
                for item in packet.artifact["allowed_items"]
                if item["target_path"] not in packet_test_files
            )
        )
        knowledge_snapshot_id = f"ui-knowledge-{suffix}"
        knowledge = UiKnowledgeSnapshot.from_dict(
            {
                "snapshot_id": knowledge_snapshot_id,
                "project_id": project_id,
                "environment_id": f"environment-{suffix}",
                "deployment_revision": deployment_revision,
                "snapshot_version": "1.0.0",
                "review_status": "approved",
                "reviewed_by": "qa@example.com",
                "activate": True,
                "targets": [
                    {
                        "target_ref": "expense.result-rows",
                        "business_name": "経費検索結果",
                        "screen_name": "経費一覧",
                        "trigger_path": "/expenses",
                        "source_fact_refs": [evidence_ref],
                        "candidates": [
                            {
                                "candidate_id": f"locator-expense-rows-{suffix}",
                                "locator": {
                                    "strategy": "test_id",
                                    "value": "expense-row",
                                },
                                "priority": 1,
                                "reliability_score": 0.97,
                                "source": "screen_design_and_runtime",
                            }
                        ],
                    }
                ],
            }
        )
        knowledge_repository = UiKnowledgeRepository(connection)
        assert knowledge_repository.store(knowledge).created
        assert not knowledge_repository.store(knowledge).created
        assert (
            knowledge_repository.load_approved(
                project_id=project_id,
                snapshot_id=knowledge_snapshot_id,
                environment_id=f"environment-{suffix}",
                deployment_revision=deployment_revision,
            )
            == knowledge
        )
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT ui_knowledge_identity_probe")
            cursor.execute(
                """
                UPDATE ui_locator_candidates SET locator_value = 'drifted-expense-row'
                WHERE ui_knowledge_snapshot_id = %s
                """,
                (knowledge_snapshot_id,),
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            knowledge_repository.load_approved(
                project_id=project_id,
                snapshot_id=knowledge_snapshot_id,
                environment_id=f"environment-{suffix}",
                deployment_revision=deployment_revision,
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            knowledge_repository.store(knowledge)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT ui_knowledge_identity_probe")
            cursor.execute("RELEASE SAVEPOINT ui_knowledge_identity_probe")
        replacement_payload: dict[str, Any] = json.loads(json.dumps(knowledge.to_dict()))
        replacement_payload["snapshot_id"] = f"ui-knowledge-replacement-{suffix}"
        replacement_payload["snapshot_version"] = "1.1.0"
        replacement_payload["targets"][0]["candidates"][0]["candidate_id"] = (
            f"locator-expense-rows-replacement-{suffix}"
        )
        replacement = UiKnowledgeSnapshot.from_dict(replacement_payload)
        assert knowledge_repository.store(replacement).active
        assert not knowledge_repository.store(knowledge).active
        assert not knowledge_repository.load_approved(
            project_id=project_id,
            snapshot_id=knowledge_snapshot_id,
        ).activate
        runtime_observation_request = UiRuntimeObservationRequest(
            project_id=project_id,
            source_snapshot_id=knowledge_snapshot_id,
            observation_run_id=f"ui-observation-run-{suffix}",
            result_snapshot_id=f"ui-knowledge-observed-{suffix}",
            result_snapshot_version="1.0.1-draft",
        )
        runtime_service = UiRuntimeObservationService(
            connection=connection,
            observer=_PassingUiKnowledgeRuntimeObserver(),
        )
        runtime_observed = runtime_service.observe(runtime_observation_request)
        runtime_replay = runtime_service.observe(runtime_observation_request)
        assert runtime_observed.record.created
        assert not runtime_replay.record.created
        assert runtime_observed.record.status == "completed"
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT runtime_observation_identity_probe")
            cursor.execute(
                """
                UPDATE ui_locator_observations SET locator_value = 'drifted-runtime-locator'
                WHERE ui_locator_observation_run_id = %s
                """,
                (runtime_observation_request.observation_run_id,),
            )
        with pytest.raises(PersistenceConflictError, match="Observation rows differ"):
            UiLocatorObservationRepository(connection).store(
                run_id=runtime_observation_request.observation_run_id,
                source=knowledge_repository.load(
                    project_id=project_id,
                    snapshot_id=knowledge_snapshot_id,
                ),
                result=runtime_observed.observation,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT runtime_observation_identity_probe")
            cursor.execute("RELEASE SAVEPOINT runtime_observation_identity_probe")
        observed_snapshot = knowledge_repository.load(
            project_id=project_id,
            snapshot_id=runtime_observation_request.result_snapshot_id,
        )
        assert observed_snapshot.review_status == "draft"
        assert observed_snapshot.targets[0].candidates[0].reliability_score == 0.97
        knowledge_review_request = UiKnowledgeReviewRequest(
            project_id=project_id,
            source_snapshot_id=runtime_observation_request.result_snapshot_id,
            review_event_id=f"ui-knowledge-review-{suffix}",
            result_snapshot_id=f"ui-knowledge-reviewed-{suffix}",
            result_snapshot_version="1.0.1",
            decision="approved",
            reviewed_by="qa@example.invalid",
            reason="runtime Locator observation passed",
            activate=True,
        )
        knowledge_review_service = UiKnowledgeReviewService(connection=connection)
        knowledge_review = knowledge_review_service.review(knowledge_review_request)
        knowledge_review_replay = knowledge_review_service.review(knowledge_review_request)
        assert knowledge_review.record.created
        assert not knowledge_review_replay.record.created
        assert knowledge_review.record.active
        assert knowledge_review.snapshot.review_status == "approved"
        assert knowledge_review.snapshot.activate
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT knowledge_review_result_identity_probe")
            cursor.execute(
                """
                UPDATE ui_locator_candidates SET reliability_score = 0.81
                WHERE ui_knowledge_snapshot_id = %s
                """,
                (knowledge_review_request.result_snapshot_id,),
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            knowledge_review_service.review(knowledge_review_request)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT knowledge_review_result_identity_probe")
            cursor.execute("RELEASE SAVEPOINT knowledge_review_result_identity_probe")
        assert (
            knowledge_review.snapshot.targets[0].candidates[0].candidate_id
            == observed_snapshot.targets[0].candidates[0].candidate_id
        )
        assert (
            knowledge_repository.load_approved(
                project_id=project_id,
                snapshot_id=knowledge_review_request.result_snapshot_id,
            )
            == knowledge_review.snapshot
        )
        manifest_id = f"browser-manifest-{suffix}"
        browser_manifest = BrowserExecutionManifest.from_dict(
            {
                "manifest_id": manifest_id,
                "plan_id": plan_id,
                "project_id": project_id,
                "browser": {
                    "name": "chromium",
                    "channel": "chrome",
                    "headless": True,
                    "viewport": {"width": 1280, "height": 720},
                },
                "review_status": "approved",
                "reviewed_by": "qa@example.com",
                "ui_knowledge_snapshot_id": knowledge_review_request.result_snapshot_id,
                "scenarios": [
                    {
                        "scenario_id": scenario_id,
                        "trigger_path": "/expenses",
                        "impact_item_refs": [impact_item_id],
                        "actions": [],
                        "assertions": [
                            {
                                "assertion_id": "all-expenses-visible",
                                "kind": "count_equals",
                                "locator": {"target_ref": "expense.result-rows"},
                                "expected": {"source": "literal", "value": "4"},
                                "failure_category": "business_assertion",
                            }
                        ],
                        "preflight_assertions": [
                            {
                                "assertion_id": "expense-data-ready",
                                "kind": "count_equals",
                                "locator": {"target_ref": "expense.result-rows"},
                                "expected": {"source": "literal", "value": "4"},
                                "failure_category": "test_data",
                            }
                        ],
                        "redaction_locators": [],
                    }
                ],
            }
        )
        browser_registration = BrowserExecutionService(
            connection=connection,
            contracts=contracts,
        )
        wrong_trigger: dict[str, Any] = json.loads(json.dumps(browser_manifest.to_dict()))
        wrong_trigger["manifest_id"] = f"browser-manifest-wrong-trigger-{suffix}"
        wrong_trigger["review_status"] = "draft"
        wrong_trigger["reviewed_by"] = None
        wrong_trigger["scenarios"][0]["trigger_path"] = "/unapproved-route"
        with pytest.raises(ValueError, match="trigger_path differs"):
            browser_registration.register_manifest(
                BrowserExecutionManifest.from_dict(wrong_trigger)
            )
        wrong_coverage: dict[str, Any] = json.loads(json.dumps(browser_manifest.to_dict()))
        wrong_coverage["manifest_id"] = f"browser-manifest-wrong-coverage-{suffix}"
        wrong_coverage["review_status"] = "draft"
        wrong_coverage["reviewed_by"] = None
        wrong_coverage["scenarios"][0]["impact_item_refs"] = ["impact-item-outside-packet"]
        with pytest.raises(ValueError, match="cover every Packet Impact Item"):
            browser_registration.register_manifest(
                BrowserExecutionManifest.from_dict(wrong_coverage)
            )
        assert browser_registration.register_manifest(browser_manifest).created
        assert not browser_registration.register_manifest(browser_manifest).created
        manifest_repository = UiBrowserManifestRepository(connection)
        assert (
            manifest_repository.load_approved(
                project_id=project_id,
                plan_id=plan_id,
            ).manifest.manifest_id
            == manifest_id
        )
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT manifest_identity_probe")
            cursor.execute(
                """
                UPDATE ui_browser_scenario_specs SET trigger_path = '/drifted-route'
                WHERE browser_manifest_id = %s
                """,
                (manifest_id,),
            )
        with pytest.raises(PersistenceConflictError, match="normalized Scenario content"):
            browser_registration.register_manifest(browser_manifest)
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            manifest_repository.load_approved(project_id=project_id, plan_id=plan_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT manifest_identity_probe")
            cursor.execute("RELEASE SAVEPOINT manifest_identity_probe")

        automated_plan_id = f"ui-plan-automated-preflight-{suffix}"
        automated_plan = ui_service.build_plan(
            deployment=UiDeploymentWrite(
                project_id=project_id,
                environment_id=f"environment-{suffix}",
                base_url="http://127.0.0.1:8080",
                deployment_revision=deployment_revision,
                repository_revision=committed.result_repository_revision,
            ),
            plan=UiExecutionPlanWrite(
                plan_id=automated_plan_id,
                project_id=project_id,
                analysis_case_id=case_id,
                edit_packet_id=packet_request.edit_packet_id,
                edit_result_id=committed.record.edit_result_id,
                environment_id=f"environment-{suffix}",
                deployment_revision=deployment_revision,
            ),
        )
        assert automated_plan.status == "preflight_pending"
        automated_manifest_payload: dict[str, Any] = json.loads(
            json.dumps(browser_manifest.to_dict())
        )
        automated_manifest_payload["manifest_id"] = f"browser-manifest-automated-{suffix}"
        automated_manifest_payload["plan_id"] = automated_plan_id
        automated_manifest = BrowserExecutionManifest.from_dict(automated_manifest_payload)
        assert browser_registration.register_manifest(automated_manifest).created
        automated_preflight = BrowserPreflightService(
            connection=connection,
            probe=_PassingBrowserPreflightProbe(),
        ).inspect(
            BrowserPreflightRequest(
                project_id=project_id,
                plan_id=automated_plan_id,
                manifest_id=automated_manifest.manifest_id,
                attempt_id=f"preflight-attempt-automated-{suffix}",
            )
        )
        assert automated_preflight.status == "ready"

        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT run_deployment_source_probe")
            cursor.execute(
                "UPDATE ui_deployments SET status = 'stale' "
                "WHERE environment_id = %s AND deployment_revision = %s",
                (deployment_write.environment_id, deployment_revision),
            )
        with pytest.raises(ValueError, match="source is no longer current"):
            ui_service.start_run(
                project_id=project_id,
                plan_id=plan_id,
                run_id=f"ui-run-retired-deployment-{suffix}",
                approval_grant_id=grant_id,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT run_deployment_source_probe")
            cursor.execute("RELEASE SAVEPOINT run_deployment_source_probe")

        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT revoked_ui_completion_probe")
        revoked_run_id = f"ui-run-revoked-after-start-{suffix}"
        ui_service.start_run(
            project_id=project_id,
            plan_id=plan_id,
            run_id=revoked_run_id,
            approval_grant_id=grant_id,
        )
        assert grant_service.revoke(
            event_id=f"approval-revocation-after-ui-start-{suffix}",
            grant_id=grant_id,
            project_id=project_id,
            revoked_by="reviewer@example.invalid",
            reason="Exercise completion-time UI reauthorization",
        )
        revoked_screenshot_id = f"evidence-revoked-screenshot-{suffix}"
        revoked_assertion_id = f"evidence-revoked-assertion-{suffix}"
        with pytest.raises(ValueError, match="state: revoked"):
            ui_service.complete_run(
                verification_result_id=f"ui-verification-revoked-{suffix}",
                project_id=project_id,
                run_id=revoked_run_id,
                scenario_results=(
                    UiScenarioResultWrite(
                        scenario_id=scenario_id,
                        status="passed",
                        impact_item_refs=(impact_item_id,),
                        evidence_refs=(revoked_screenshot_id, revoked_assertion_id),
                        failure_category="none",
                    ),
                ),
                evidence=(
                    UiExecutionEvidenceWrite(
                        evidence_id=revoked_screenshot_id,
                        scenario_id=scenario_id,
                        evidence_type="screenshot",
                        evidence_ref=f"evidence://{revoked_screenshot_id}",
                        content_digest=hashlib.sha256(b"revoked screenshot").hexdigest(),
                        sanitized=True,
                    ),
                    UiExecutionEvidenceWrite(
                        evidence_id=revoked_assertion_id,
                        scenario_id=scenario_id,
                        evidence_type="assertion",
                        evidence_ref=f"evidence://{revoked_assertion_id}",
                        content_digest=hashlib.sha256(b"revoked assertion").hexdigest(),
                        sanitized=True,
                    ),
                ),
            )
        revoked_recovery_result_id = f"ui-recovery-revoked-{suffix}"
        revoked_recovery = ui_service.recover_run(
            verification_result_id=revoked_recovery_result_id,
            project_id=project_id,
            run_id=revoked_run_id,
            recovery=UiRunRecovery(
                recovery_id=revoked_recovery_result_id,
                actor="operator@example.invalid",
                reason="Grant was revoked while the browser Run was active",
                stale_before=datetime.now(UTC),
            ),
        )
        assert revoked_recovery.record.status == "blocked"
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT revoked_ui_completion_probe")
            cursor.execute("RELEASE SAVEPOINT revoked_ui_completion_probe")

        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT failed_ui_probe")
        failed_run_id = f"ui-run-failed-{suffix}"
        ui_service.start_run(
            project_id=project_id,
            plan_id=plan_id,
            run_id=failed_run_id,
            approval_grant_id=grant_id,
        )
        failed_evidence_id = f"evidence-failed-assertion-{suffix}"
        failed = ui_service.complete_run(
            verification_result_id=f"ui-verification-failed-{suffix}",
            project_id=project_id,
            run_id=failed_run_id,
            scenario_results=(
                UiScenarioResultWrite(
                    scenario_id=scenario_id,
                    status="failed",
                    impact_item_refs=(impact_item_id,),
                    evidence_refs=(failed_evidence_id,),
                    failure_category="business_assertion",
                    summary="The default filter did not show every expense.",
                ),
            ),
            evidence=(
                UiExecutionEvidenceWrite(
                    evidence_id=failed_evidence_id,
                    scenario_id=scenario_id,
                    evidence_type="assertion",
                    evidence_ref=f"evidence://{failed_evidence_id}",
                    content_digest=hashlib.sha256(b"assertion failed").hexdigest(),
                    sanitized=True,
                ),
            ),
        )
        assert failed.record.status == "failed"
        assert failed.record.case_status == "failed"
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT failed_ui_probe")
            cursor.execute("RELEASE SAVEPOINT failed_ui_probe")

        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT reanalysis_ui_probe")
        reanalysis_run_id = f"ui-run-reanalysis-{suffix}"
        ui_service.start_run(
            project_id=project_id,
            plan_id=plan_id,
            run_id=reanalysis_run_id,
            approval_grant_id=grant_id,
        )
        reanalysis_screenshot_id = f"evidence-reanalysis-screenshot-{suffix}"
        reanalysis_assertion_id = f"evidence-reanalysis-assertion-{suffix}"
        reanalysis = ui_service.complete_run(
            verification_result_id=f"ui-verification-reanalysis-{suffix}",
            project_id=project_id,
            run_id=reanalysis_run_id,
            scenario_results=(
                UiScenarioResultWrite(
                    scenario_id=scenario_id,
                    status="passed",
                    impact_item_refs=(impact_item_id,),
                    evidence_refs=(reanalysis_screenshot_id, reanalysis_assertion_id),
                    failure_category="none",
                ),
            ),
            evidence=(
                UiExecutionEvidenceWrite(
                    evidence_id=reanalysis_screenshot_id,
                    scenario_id=scenario_id,
                    evidence_type="screenshot",
                    evidence_ref=f"evidence://{reanalysis_screenshot_id}",
                    content_digest=hashlib.sha256(b"reanalysis screenshot").hexdigest(),
                    sanitized=True,
                ),
                UiExecutionEvidenceWrite(
                    evidence_id=reanalysis_assertion_id,
                    scenario_id=scenario_id,
                    evidence_type="assertion",
                    evidence_ref=f"evidence://{reanalysis_assertion_id}",
                    content_digest=hashlib.sha256(b"reanalysis assertion").hexdigest(),
                    sanitized=True,
                ),
            ),
            out_of_scope_files=("src/main/java/example/Unapproved.java",),
        )
        assert reanalysis.record.status == "reanalysis_required"
        assert reanalysis.record.case_status == "reanalysis_required"
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT reanalysis_ui_probe")
            cursor.execute("RELEASE SAVEPOINT reanalysis_ui_probe")

        blocked_run_id = f"ui-run-blocked-{suffix}"
        untrusted_execution = BrowserExecutionService(
            connection=connection,
            contracts=contracts,
            executor=_UntrustedBrowserExecutor(scenario_id),
        ).execute(
            BrowserExecutionRequest(
                project_id=project_id,
                plan_id=plan_id,
                manifest_id=manifest_id,
                run_id=blocked_run_id,
                verification_result_id=f"ui-verification-blocked-{suffix}",
                approval_grant_id=grant_id,
            )
        )
        ui_blocked = untrusted_execution.verification
        assert ui_blocked.record.status == "blocked"
        assert ui_blocked.record.case_status == "verifying_ui"

        executor_error_run_id = f"ui-run-executor-error-{suffix}"
        with pytest.raises(BrowserExecutionRuntimeError, match="recorded as blocked"):
            BrowserExecutionService(
                connection=connection,
                contracts=contracts,
                executor=_ExplodingBrowserExecutor(),
            ).execute(
                BrowserExecutionRequest(
                    project_id=project_id,
                    plan_id=plan_id,
                    manifest_id=manifest_id,
                    run_id=executor_error_run_id,
                    verification_result_id=f"ui-verification-executor-error-{suffix}",
                    approval_grant_id=grant_id,
                )
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM ui_execution_runs WHERE ui_execution_run_id = %s",
                (executor_error_run_id,),
            )
            assert cursor.fetchone() == ("blocked",)

        interrupted_run_id = f"ui-run-interrupted-{suffix}"
        interrupted_result_id = f"ui-verification-interrupted-{suffix}"
        ui_service.start_run(
            project_id=project_id,
            plan_id=plan_id,
            run_id=interrupted_run_id,
            approval_grant_id=grant_id,
        )
        recovery = UiRunRecovery(
            recovery_id=interrupted_result_id,
            actor="operator@example.invalid",
            reason="browser worker process was interrupted",
            stale_before=datetime.now(UTC),
        )
        recovered = ui_service.recover_run(
            verification_result_id=interrupted_result_id,
            project_id=project_id,
            run_id=interrupted_run_id,
            recovery=recovery,
        )
        recovered_replay = ui_service.recover_run(
            verification_result_id=interrupted_result_id,
            project_id=project_id,
            run_id=interrupted_run_id,
            recovery=recovery,
        )
        assert recovered.record.created
        assert not recovered_replay.record.created
        assert recovered.artifact["status"] == "blocked"
        assert recovered.artifact["recovery"] == recovery.to_dict()

        run_id = f"ui-run-{suffix}"
        result_id = f"ui-verification-result-{suffix}"
        browser_execution = BrowserExecutionService(
            connection=connection,
            contracts=contracts,
            executor=_PassingBrowserExecutor(scenario_id, impact_item_id),
        ).execute(
            BrowserExecutionRequest(
                project_id=project_id,
                plan_id=plan_id,
                manifest_id=manifest_id,
                run_id=run_id,
                verification_result_id=result_id,
                approval_grant_id=grant_id,
            )
        )
        ui_result = browser_execution.verification
        assert ui_result.record.created
        assert browser_execution.run.created
        assert ui_result.artifact["status"] == "passed"
        assert ui_result.artifact["repository_revision"] == committed.result_repository_revision
        assert ui_result.artifact["unresolved_impact_item_ids"] == []
        assert grant_repository.inspect(grant_id).state == "completed"

        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ui_execution_plans
                SET status = 'blocked',
                    blocking_reasons = '["retired_for_revalidation"]'::jsonb
                WHERE ui_execution_plan_id = %s AND status = 'ready'
                """,
                (automated_plan_id,),
            )
        revalidation_plan_id = f"ui-plan-revalidation-{suffix}"
        revalidation_plan = ui_service.build_plan(
            deployment=deployment_write,
            plan=UiExecutionPlanWrite(
                plan_id=revalidation_plan_id,
                project_id=project_id,
                analysis_case_id=case_id,
                edit_packet_id=packet_request.edit_packet_id,
                edit_result_id=committed.record.edit_result_id,
                environment_id=deployment_write.environment_id,
                deployment_revision=deployment_write.deployment_revision,
            ),
        )
        assert revalidation_plan.status == "preflight_pending"
        revalidation_manifest_payload: dict[str, Any] = json.loads(
            json.dumps(browser_manifest.to_dict())
        )
        revalidation_manifest_id = f"browser-manifest-revalidation-{suffix}"
        revalidation_manifest_payload["manifest_id"] = revalidation_manifest_id
        revalidation_manifest_payload["plan_id"] = revalidation_plan_id
        revalidation_manifest = BrowserExecutionManifest.from_dict(revalidation_manifest_payload)
        assert browser_registration.register_manifest(revalidation_manifest).created
        revalidation_preflight = BrowserPreflightService(
            connection=connection,
            probe=_PassingBrowserPreflightProbe(),
        ).inspect(
            BrowserPreflightRequest(
                project_id=project_id,
                plan_id=revalidation_plan_id,
                manifest_id=revalidation_manifest_id,
                attempt_id=f"browser-preflight-revalidation-{suffix}",
            )
        )
        assert revalidation_preflight.status == "ready"
        revalidation_execution = BrowserExecutionService(
            connection=connection,
            contracts=contracts,
            executor=_PassingBrowserExecutor(scenario_id, impact_item_id),
        ).execute(
            BrowserExecutionRequest(
                project_id=project_id,
                plan_id=revalidation_plan_id,
                manifest_id=revalidation_manifest_id,
                run_id=f"ui-run-revalidation-{suffix}",
                verification_result_id=f"ui-verification-revalidation-{suffix}",
                approval_grant_id=grant_id,
            )
        )
        assert revalidation_execution.verification.artifact["status"] == "passed"
        assert grant_repository.inspect(grant_id).state == "completed"
        validation_query_response = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "validation_get_result",
                    "arguments": {
                        "project_id": project_id,
                        "verification_result_id": result_id,
                    },
                },
            }
        )
        assert validation_query_response is not None
        validation_query = validation_query_response["result"]["structuredContent"]
        assert validation_query["artifact"] == ui_result.artifact
        assert validation_query["current_status"] == "passed"
        grant_after_completion_replay = grant_service.issue(grant_request)
        assert not grant_after_completion_replay.record.created
        assert grant_after_completion_replay.record.state == "completed"
        run_after_completion_replay = ui_service.start_run(
            project_id=project_id,
            plan_id=plan_id,
            run_id=run_id,
            approval_grant_id=grant_id,
        )
        assert not run_after_completion_replay.created
        assert run_after_completion_replay.status == "completed"
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT completed_run_identity_probe")
            cursor.execute(
                """
                UPDATE ui_execution_plans SET repository_revision = 'drifted-after-run'
                WHERE ui_execution_plan_id = %s
                """,
                (plan_id,),
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            UiVerificationRepository(connection).find_run(
                project_id=project_id,
                plan_id=plan_id,
                run_id=run_id,
                approval_grant_id=grant_id,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT completed_run_identity_probe")
            cursor.execute("RELEASE SAVEPOINT completed_run_identity_probe")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM analysis_cases WHERE analysis_case_id = %s",
                (case_id,),
            )
            assert cursor.fetchone() == ("passed",)

        blocked = impact_service.run(
            ImpactReportRequest(
                impact_report_id=f"impact-report-blocked-{suffix}",
                scope=CodeScopeRequest(
                    project_id=request.project_id,
                    analysis_case_id=request.analysis_case_id,
                    context_package_id=request.context_package_id,
                    structured_change_id=request.structured_change_id,
                    code_graph_snapshot_id=request.code_graph_snapshot_id,
                    repository_revision_id=request.repository_revision_id,
                    profile_binding_key=request.profile_binding_key,
                    anchors=(
                        CodeAnchor(
                            anchor_id="blocked-missing-endpoint",
                            kind=CodeAnchorKind.ENDPOINT,
                            value="GET /still-not-present",
                            evidence_refs=(evidence_ref,),
                        ),
                    ),
                ),
                ui_impact_status=UiImpactStatus.IMPACTED,
                required_ui_scenario_refs=("expense-filter-default-all",),
            )
        )
        assert blocked.artifact["status"] == "blocked"
        assert blocked.artifact["blocking_unknowns"] == [
            "anchor_not_found:blocked-missing-endpoint",
            "no_editable_code_candidate",
        ]
        superseded_state = impact_repository.get_state(impact_request.impact_report_id)
        assert superseded_state is not None
        assert superseded_state.status == "superseded"
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM edit_packets WHERE edit_packet_id = %s",
                (packet_request.edit_packet_id,),
            )
            assert cursor.fetchone() == ("superseded",)
            cursor.execute(
                "SELECT status FROM analysis_cases WHERE analysis_case_id = %s",
                (case_id,),
            )
            assert cursor.fetchone() == ("reanalysis_required",)
        connection.rollback()


def _insert_project_case(
    connection: psycopg.Connection[Any],
    *,
    project_id: str,
    repository_id: str,
    revision_id: str,
    case_id: str,
    commit_sha: str,
    remote_url: str,
    workspace_root: Path,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, %s)",
            (project_id, "Code Scope integration test"),
        )
        cursor.execute(
            """
            INSERT INTO repositories (
                repository_id, project_id, remote_url, workspace_root
            ) VALUES (%s, %s, %s, %s)
            """,
            (repository_id, project_id, remote_url, str(workspace_root.resolve())),
        )
        cursor.execute(
            """
            INSERT INTO repository_revisions (
                repository_revision_id, repository_id, commit_sha
            ) VALUES (%s, %s, %s)
            """,
            (revision_id, repository_id, commit_sha),
        )
        cursor.execute(
            """
            INSERT INTO analysis_cases (
                analysis_case_id, project_id, repository_revision_id, status
            ) VALUES (%s, %s, %s, 'ready_for_impact')
            """,
            (case_id, project_id, revision_id),
        )


def _store_document_snapshots(
    repository: CanonicalRepository,
    *,
    connection: psycopg.Connection[Any],
    suffix: str,
    project_id: str,
    profile_version_id: str,
    evidence_ref: str,
) -> tuple[CanonicalSnapshot, CanonicalSnapshot]:
    before = _snapshot(
        suffix=suffix, label="before", default_value="申請中", evidence_ref=evidence_ref
    )
    after = _snapshot(
        suffix=suffix, label="after", default_value="すべて", evidence_ref=evidence_ref
    )
    for snapshot in (before, after):
        label = snapshot.snapshot_id.split("-")[1]
        document_version_id = f"version-{label}-{suffix}"
        repository.store_snapshot(
            DocumentSnapshotWrite(
                project_id=project_id,
                document_id=f"document-{suffix}",
                document_version_id=document_version_id,
                logical_name="02_画面設計書_経費一覧.xlsx",
                source_ref=f"immutable://design/{suffix}/{label}.xlsx",
                content_digest=hashlib.sha256(f"{label}-{suffix}".encode()).hexdigest(),
                extractor_ref="manual-canonical@1",
                profile_version_id=profile_version_id,
                selected_variant_id="screen-item-table-ja",
                status=SnapshotStatus.COMMITTED,
                snapshot=snapshot,
            )
        )
        nodes = CanonicalDocumentNodeBuilder().build(
            snapshot=snapshot,
            document_version_id=document_version_id,
            logical_name="02_画面設計書_経費一覧.xlsx",
            document_type="screen_design",
        )
        DocumentNodeRepository(connection).store_nodes(
            project_id=project_id,
            snapshot_id=snapshot.snapshot_id,
            nodes=nodes,
        )
    return before, after


def _snapshot(
    *, suffix: str, label: str, default_value: str, evidence_ref: str
) -> CanonicalSnapshot:
    return CanonicalSnapshot(
        snapshot_id=f"snapshot-{label}-{suffix}",
        facts=(
            SnapshotFact(
                fact_ref=f"fact-{label}-{suffix}",
                fact=CanonicalFact(
                    fact_type="screen_element",
                    stable_key="screen_element:expense-list/status",
                    values={
                        "screen_id": "EXPENSE_LIST",
                        "element_id": "status",
                        "default_value": default_value,
                    },
                    source_refs=(evidence_ref,),
                    field_evidence=(),
                ),
            ),
        ),
    )


def _context_artifact(
    *,
    context_id: str,
    case_id: str,
    project_id: str,
    snapshot_id: str,
    change_id: str,
    evidence_ref: str,
    suffix: str,
) -> dict[str, Any]:
    return {
        "artifact_type": "ContextPackage",
        "schema_version": "v1",
        "context_package_id": context_id,
        "analysis_case_id": case_id,
        "project_id": project_id,
        "document_snapshot_id": snapshot_id,
        "ingestion_batch_id": f"ingestion-batch-{suffix}",
        "document_ingestion_result_event_id": f"ingestion-ready-{suffix}",
        "document_profile_refs": ["screen-design-conventions-example@1.0.0"],
        "embedding_profile_ref": "embedding-example@1.0.0",
        "search_index_build_id": f"search-index-{suffix}",
        "ranking_policy_version": "hybrid-rrf-v1",
        "query_plan_version": "structured-change-query-v1",
        "retrieval_policy": {
            "embedding_profile_version_id": f"embedding-profile-{suffix}",
            "embedding_profile_binding_key": "embedding:document_search",
            "vector_top_k": 10,
            "keyword_top_k": 10,
            "final_top_k": 10,
            "adjacent_distance": 1,
        },
        "structured_change_refs": [change_id],
        "business_summary": "The expense status default changes to All.",
        "context_items": [
            {
                "section_id": f"section-{suffix}",
                "document_id": f"document-{suffix}",
                "compressed_summary": "The expense list is served by GET /expenses.",
                "relevance_reason": "direct_change",
                "evidence_refs": [evidence_ref],
            }
        ],
        "retrieval_trace": [],
        "token_budget": 1000,
        "estimated_tokens": 50,
        "unknowns": [],
    }


def _code_graph_artifact(
    *,
    graph_id: str,
    project_id: str,
    repository_id: str,
    commit_sha: str,
    suffix: str,
) -> dict[str, Any]:
    service_file_id = f"file-service-{suffix}"
    test_file_id = f"file-test-{suffix}"
    service_symbol_id = f"symbol-search-{suffix}"
    test_symbol_id = f"symbol-test-{suffix}"
    profile_ref = "spring-web-example@1.0.0"
    service_path = "src/main/java/example/ExpenseService.java"
    test_path = "src/test/java/example/ExpenseServiceTest.java"
    return {
        "artifact_type": "CodeGraphSnapshot",
        "schema_version": "v1",
        "code_graph_snapshot_id": graph_id,
        "project_id": project_id,
        "repository_id": repository_id,
        "repository_revision": commit_sha,
        "framework_profile_refs": [profile_ref],
        "scan_roots": ["src/main", "src/test"],
        "scan_status": "complete",
        "framework_markers_found": ["org.springframework.web.bind.annotation"],
        "diagnostics": [],
        "files": [
            {
                "file_id": service_file_id,
                "path": service_path,
                "language": "java",
                "role": "production",
                "content_hash": f"sha256:service-{suffix}",
                "symbols": [
                    {
                        "symbol_id": service_symbol_id,
                        "symbol_type": "method",
                        "name": "search",
                        "signature": "search(String status)",
                        "start_line": 10,
                        "end_line": 20,
                    }
                ],
            },
            {
                "file_id": test_file_id,
                "path": test_path,
                "language": "java",
                "role": "test",
                "content_hash": f"sha256:test-{suffix}",
                "symbols": [
                    {
                        "symbol_id": test_symbol_id,
                        "symbol_type": "method",
                        "name": "findsExpenses",
                        "signature": "findsExpenses()",
                        "start_line": 8,
                        "end_line": 14,
                    }
                ],
            },
        ],
        "edges": [
            {
                "edge_id": f"edge-exposes-{suffix}",
                "edge_type": "exposes",
                "from_ref": service_symbol_id,
                "to_ref": "http:GET:/expenses",
                "resolution_status": "external",
                "confidence": "high",
                "extractor": "spring_endpoint",
                "profile_version": profile_ref,
                "source_location": {
                    "path": service_path,
                    "start_line": 10,
                    "end_line": 10,
                },
            },
            {
                "edge_id": f"edge-tests-{suffix}",
                "edge_type": "tests",
                "from_ref": test_symbol_id,
                "to_ref": service_symbol_id,
                "resolution_status": "resolved",
                "confidence": "high",
                "extractor": "junit_test",
                "profile_version": profile_ref,
                "source_location": {
                    "path": test_path,
                    "start_line": 8,
                    "end_line": 14,
                },
            },
        ],
    }


class _ExplodingBrowserExecutor:
    def execute(
        self,
        *,
        manifest: BrowserExecutionManifest,
        base_url: str,
        run_id: str,
        storage_state: Path | None = None,
    ) -> BrowserExecutionOutput:
        raise ValueError("simulated Evidence Store failure")


class _PassingBrowserPreflightProbe:
    def inspect(
        self,
        *,
        manifest: BrowserExecutionManifest,
        base_url: str,
        attempt_id: str,
        storage_state: Path | None = None,
    ) -> tuple[BrowserPreflightObservation, ...]:
        locator = manifest.scenarios[0].assertions[0].locator
        assert locator.target_ref is None
        assert locator.strategy is not None and locator.strategy.value == "test_id"
        assert locator.value == "expense-row"
        assert base_url == "http://127.0.0.1:8080"
        return tuple(
            BrowserPreflightObservation(
                check_type=check_type,
                status="passed",
                evidence_ref=f"preflight://{attempt_id}/{check_type}",
            )
            for check_type in (
                "environment",
                "authentication",
                "test_data",
                "trigger_path",
                "locator",
            )
        )


class _PassingUiKnowledgeRuntimeObserver:
    def observe(
        self,
        *,
        source: UiKnowledgeSnapshot,
        base_url: str,
        observation_run_id: str,
        result_snapshot_id: str,
        result_snapshot_version: str,
        storage_state: Path | None = None,
    ) -> UiRuntimeObservationResult:
        assert base_url == "http://127.0.0.1:8080"
        assert storage_state is None
        candidate = source.targets[0].candidates[0]
        observation = UiRuntimeLocatorObservation(
            observation_id=runtime_observation_id(
                observation_run_id,
                source.targets[0].target_ref,
                candidate.candidate_id,
            ),
            target_ref=source.targets[0].target_ref,
            candidate_id=candidate.candidate_id,
            locator=candidate.locator,
            status=UiLocatorObservationStatus.UNIQUE_VISIBLE,
            match_count=1,
            visible_count=1,
            discovered=False,
        )
        snapshot = UiRuntimeObservationMerger().merge(
            source=source,
            observations=(observation,),
            result_snapshot_id=result_snapshot_id,
            result_snapshot_version=result_snapshot_version,
        )
        return UiRuntimeObservationResult(
            status="completed",
            snapshot=snapshot,
            observations=(observation,),
            issues=(),
        )


class _UntrustedBrowserExecutor:
    def __init__(self, scenario_id: str) -> None:
        self._scenario_id = scenario_id

    def execute(
        self,
        *,
        manifest: BrowserExecutionManifest,
        base_url: str,
        run_id: str,
        storage_state: Path | None = None,
    ) -> BrowserExecutionOutput:
        return BrowserExecutionOutput(
            scenario_results=(
                BrowserScenarioOutcome(
                    scenario_id=self._scenario_id,
                    status="passed",
                    impact_item_refs=("impact-item-outside-approved-manifest",),
                    evidence_refs=(),
                    failure_category="none",
                    summary="Untrusted executor tried to claim success.",
                ),
            ),
            evidence=(),
        )


class _PassingBrowserExecutor:
    def __init__(self, scenario_id: str, impact_item_id: str) -> None:
        self._scenario_id = scenario_id
        self._impact_item_id = impact_item_id

    def execute(
        self,
        *,
        manifest: BrowserExecutionManifest,
        base_url: str,
        run_id: str,
        storage_state: Path | None = None,
    ) -> BrowserExecutionOutput:
        assert manifest.scenarios[0].scenario_id == self._scenario_id
        assert base_url == "http://127.0.0.1:8080"
        assert storage_state is None
        screenshot_id = f"evidence-screenshot-{run_id}"
        assertion_id = f"evidence-assertion-{run_id}"
        evidence = (
            StoredBrowserEvidence(
                evidence_id=screenshot_id,
                scenario_id=self._scenario_id,
                evidence_type="screenshot",
                evidence_ref=f"evidence://{manifest.project_id}/{run_id}/{screenshot_id}",
                content_digest=hashlib.sha256(b"sanitized screenshot").hexdigest(),
            ),
            StoredBrowserEvidence(
                evidence_id=assertion_id,
                scenario_id=self._scenario_id,
                evidence_type="assertion",
                evidence_ref=f"evidence://{manifest.project_id}/{run_id}/{assertion_id}",
                content_digest=hashlib.sha256(b"assertion passed").hexdigest(),
            ),
        )
        return BrowserExecutionOutput(
            scenario_results=(
                BrowserScenarioOutcome(
                    scenario_id=self._scenario_id,
                    status="passed",
                    impact_item_refs=(self._impact_item_id,),
                    evidence_refs=(screenshot_id, assertion_id),
                    failure_category="none",
                    summary="All filter selected and every seeded expense is visible.",
                ),
            ),
            evidence=evidence,
        )


def _load_json(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _create_edit_workspace(tmp_path: Path, suffix: str) -> tuple[Path, str, str]:
    workspace = tmp_path / f"edit-workspace-{suffix}"
    workspace.mkdir()
    remote_url = f"https://example.invalid/{suffix}.git"

    def git(*arguments: str) -> str:
        result = subprocess.run(
            ("git", "-C", str(workspace), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    git("init", "-q")
    git("remote", "add", "origin", remote_url)
    service = workspace / "src/main/java/example/ExpenseService.java"
    test = workspace / "src/test/java/example/ExpenseServiceTest.java"
    service.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    service.write_text("class ExpenseService {}\n", encoding="utf-8")
    test.write_text("class ExpenseServiceTest {}\n", encoding="utf-8")
    git("add", ".")
    git(
        "-c",
        "user.name=OperaMind Test",
        "-c",
        "user.email=operamind@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    return workspace, git("rev-parse", "HEAD"), remote_url


def _git_workspace(workspace: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(workspace), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
