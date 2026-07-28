import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from operamind.application import (
    ApprovalGrantRequest,
    ApprovalGrantService,
    ApprovedCommandRequest,
    ApprovedCommandService,
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
)
from operamind.application.web_control_plane import (
    BusinessRuleInput,
    ChangeRequestInput,
    WebControlPlaneService,
)
from operamind.contracts import ContractCatalog
from operamind.domain import (
    CanonicalDocumentNodeBuilder,
    CanonicalFact,
    CanonicalSnapshot,
    CodeAnchor,
    CodeAnchorKind,
    SnapshotFact,
    StructuredChangeBuilder,
)
from operamind.infrastructure.postgres import (
    ApprovalGrantRepository,
    ArtifactRepository,
    CanonicalRepository,
    ChangeAutomationRunRecord,
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
)
from operamind.mcp import CopilotToolDispatcher
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
            },
            {
                "command_ref": "ui-e2e",
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

        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT automatic_case_creation_probe")
        new_request_id = f"automatic-case-request-{suffix}"
        new_submission = WebControlPlaneService(
            connection=connection,
            repository_root=ROOT,
        ).submit_change_request(
            ChangeRequestInput(
                change_request_id=new_request_id,
                project_id=project_id,
                analysis_case_id=None,
                input_mode="natural_language",
                requirement_text="自然言語要件から Analysis Case を自動作成する",
                source_document_ref=None,
                target_document_ref=None,
                business_rules=(
                    BusinessRuleInput(
                        business_rule_id=f"automatic-case-rule-{suffix}",
                        text="登録済み Repository の現在 Revision を使用する",
                        source_refs=(),
                    ),
                ),
                ambiguity_status="clear",
                ambiguities=(),
                submitted_by="developer@example.invalid",
            )
        )
        generated_case_id = new_submission["change_request"]["analysis_case_id"]
        assert isinstance(generated_case_id, str)
        assert generated_case_id != case_id
        assert new_submission.get("case_blocker") is None
        assert isinstance(new_submission["copilot_task"], dict)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT automatic_case_creation_probe")
            cursor.execute("RELEASE SAVEPOINT automatic_case_creation_probe")

        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT automatic_copilot_scope_probe")
        automatic_request_id = f"automatic-scope-request-{suffix}"
        automatic_submission = WebControlPlaneService(
            connection=connection,
            repository_root=ROOT,
        ).submit_change_request(
            ChangeRequestInput(
                change_request_id=automatic_request_id,
                project_id=project_id,
                analysis_case_id=case_id,
                input_mode="natural_language",
                requirement_text="確認済み Impact から実行範囲を自動準備する",
                source_document_ref="document://expense-design",
                target_document_ref=None,
                business_rules=(
                    BusinessRuleInput(
                        business_rule_id=f"automatic-scope-rule-{suffix}",
                        text="承認操作なしで限定範囲を Copilot Task に設定する",
                        source_refs=("document://expense-design",),
                    ),
                ),
                ambiguity_status="clear",
                ambiguities=(),
                submitted_by="developer@example.invalid",
            )
        )
        automatic_task = automatic_submission["copilot_task"]
        assert isinstance(automatic_task, dict)
        automatic_task_id = str(automatic_task["task"]["coding_task_id"])
        automatic_service = WebControlPlaneService(
            connection=connection,
            repository_root=ROOT,
        )
        with pytest.raises(ValueError, match="requires recorded code scope"):
            automatic_service._provision_execution_scope(
                record=ChangeAutomationRunRecord(
                    automation_run_id=f"automatic-scope-run-{suffix}",
                    change_request_id=automatic_request_id,
                    project_id=project_id,
                    status="running",
                    current_stage="execution_approval",
                    next_action="provision_execution_scope",
                    blocking_reason=None,
                    created=True,
                ),
                run_id=f"automatic-scope-run-{suffix}",
            )
        automatic_bound_task = CopilotCodingTaskService(
            connection=connection,
            repository_root=ROOT,
        ).view(automatic_task_id)
        assert automatic_bound_task["execution_scope"]["bound"] is False
        assert automatic_bound_task["current_stage"] == "document_change"
        automatic_tasks = CopilotCodingTaskService(
            connection=connection,
            repository_root=ROOT,
        )
        claimed_automatic = automatic_tasks.claim_next(
            workspace_root=workspace,
            consumer_id="vscode-explicit-document-ref",
        )
        assert claimed_automatic is not None
        assert claimed_automatic["task"]["coding_task_id"] == automatic_task_id
        automatic_tasks.accept(
            coding_task_id=automatic_task_id,
            workspace_root=workspace,
            consumer_id="vscode-explicit-document-ref",
            actor="developer@example.invalid",
        )
        explicit_context = automatic_tasks.get_mcp_context(
            coding_task_id=automatic_task_id,
            workspace_root=workspace,
        )
        assert explicit_context["document_discovery"]["status"] == "ready"
        assert explicit_context["document_discovery"]["mode"] == "canonical_hybrid_rag"
        assert explicit_context["document_discovery"]["explicit_document_refs"] == [
            "document://expense-design"
        ]
        assert explicit_context["document_discovery"]["candidates"]
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT automatic_copilot_scope_probe")
            cursor.execute("RELEASE SAVEPOINT automatic_copilot_scope_probe")

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
            allowed_test_command_refs=("targeted-unit", "ui-e2e"),
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
        submitted_request = WebControlPlaneService(
            connection=connection, repository_root=ROOT
        ).submit_change_request(
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
        initial_task = submitted_request["copilot_task"]
        assert isinstance(initial_task, dict)
        initial_task_id = str(initial_task["task"]["coding_task_id"])
        assert initial_task["current_stage"] == "document_change"
        assert initial_task["execution_scope"]["bound"] is False
        initial_tasks = CopilotCodingTaskService(
            connection=connection,
            repository_root=ROOT,
        )
        claimed_initial = initial_tasks.claim_next(
            workspace_root=workspace,
            consumer_id="vscode-document-phase",
        )
        assert claimed_initial is not None
        assert claimed_initial["task"]["coding_task_id"] == initial_task_id
        initial_tasks.accept(
            coding_task_id=initial_task_id,
            workspace_root=workspace,
            consumer_id="vscode-document-phase",
            actor="developer@example.invalid",
        )
        initial_context = initial_tasks.get_mcp_context(
            coding_task_id=initial_task_id,
            workspace_root=workspace,
        )
        assert initial_context["change_plan"]["stage"] == "document_change"
        assert initial_context["execution_scope"]["bound"] is False
        assert initial_context["document_discovery"]["status"] == "ready"
        assert initial_context["document_discovery"]["mode"] == "canonical_hybrid_rag"
        assert initial_context["document_discovery"]["candidates"]
        discovered_document = initial_context["document_discovery"]["candidates"][0]
        assert discovered_document["logical_name"] == "02_画面設計書_経費一覧.xlsx"
        assert discovered_document["document_ref"].startswith("immutable://design/")
        with pytest.raises(ValueError, match="requires recorded code scope"):
            WebControlPlaneService(
                connection=connection,
                repository_root=ROOT,
            )._provision_execution_scope(
                record=ChangeAutomationRunRecord(
                    automation_run_id=f"automation-run-{suffix}",
                    change_request_id=change_request_id,
                    project_id=project_id,
                    status="running",
                    current_stage="execution_approval",
                    next_action="provision_execution_scope",
                    blocking_reason=None,
                    created=True,
                ),
                run_id=f"automation-run-{suffix}",
            )
        bound_initial = initial_tasks.view(initial_task_id)
        assert bound_initial["current_stage"] == "document_change"
        assert bound_initial["execution_scope"]["bound"] is False
        initial_tasks.cancel(
            coding_task_id=initial_task_id,
            change_request_id=change_request_id,
            actor="developer@example.invalid",
            reason="Integration fixture continues with a pre-bound retry task",
            idempotency_key="cancel-initial-document-phase",
            consumer_id="vscode-document-phase",
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
        assert published_task["task"]["execution_mode"] == "copilot_change_task"
        assert published_task["task"]["schema_version"] == "v2"
        assert published_task["current_stage"] == "document_change"
        assert published_task["task"]["workflow"]["stage_order"] == [
            "requirement",
            "document_change",
            "code_scope",
            "compile_test",
            "ui_validation",
            "final_report",
        ]
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
        assert "approval_grant_id" not in task_context["coding_task"]
        assert "edit_packet_id" not in task_context["coding_task"]
        assert "edit_packet" not in task_context
        assert "approval" not in task_context
        assert set(task_context["execution_scope"]) == {
            "bound",
            "base_repository_revision",
            "editable_files",
            "read_only_files",
            "test_files",
            "forbidden_globs",
            "allowed_items",
            "required_command_refs",
            "out_of_scope_policy",
        }
        assert task_context["change_plan"]["mode"] == "copilot_change_task"
        assert task_context["context_package_available"] is False
        generated_test_plan = _load_json(
            ROOT / "contracts/examples/test-plan.v1.example.json"
        )
        generated_test_plan.update(
            {
                "test_plan_id": f"copilot-test-plan-{suffix}",
                "change_request_id": change_request_id,
                "project_id": project_id,
            }
        )
        generated_test_plan["test_cases"][0].update(
            {
                "level": "ui",
                "execution_mode": "browser",
                "steps": ["経費検索画面で差戻し状態を検索する"],
                "expected_results": ["差戻し状態の経費だけが表示される"],
            }
        )
        generated_test_data_plan = _load_json(
            ROOT / "contracts/examples/test-data-plan.v1.example.json"
        )
        generated_test_data_plan.update(
            {
                "test_data_plan_id": f"copilot-test-data-plan-{suffix}",
                "test_plan_id": generated_test_plan["test_plan_id"],
                "project_id": project_id,
            }
        )
        generated_test_data_plan["generation_flows"][0]["steps"].append(
            {
                "step_id": "search-returned-expense",
                "sequence": 2,
                "channel": "ui",
                "business_action": "差戻し状態の経費を検索する",
                "screen_ref": "expense-list",
                "ui_action_ref": "search-created-expense",
                "inputs": {"status": "差戻し"},
                "depends_on": ["load-default-seed"],
                "output_bindings": [],
                "postconditions": [
                    {
                        "assertion_id": "returned-expense-visible",
                        "observe_via": "ui",
                        "subject": "visible_expense_count",
                        "operator": "count_equals",
                        "expected": 1,
                    }
                ],
            }
        )
        monkeypatch.setattr(
            "operamind.application.copilot_coding_task."
            "CopilotDocumentChangeService.materialize",
            lambda _service, **_values: SimpleNamespace(
                change_refs=(change.change_id,),
                document_ids=(f"document-{suffix}",),
                source_snapshot_id=after.snapshot_id,
                target_snapshot_id=after.snapshot_id,
            ),
        )
        document_outputs = task_dispatcher.call(
            "copilot_record_change_outputs",
            {
                "coding_task_id": coding_task_id,
                "workspace_root": str(copilot_workspace),
                "output_stage": "document_change",
                "document_ids": [f"document-{suffix}"],
            },
        )
        assert document_outputs["recorded_stage"] == "document_change"
        assert document_outputs["document_count"] == 1
        assert document_outputs["document_change_count"] == 1
        assert set(document_outputs) == {
            "recorded_stage",
            "next_stage",
            "coding_task_state",
            "document_count",
            "document_change_count",
            "flow_status",
            "next_context",
        }
        assert set(document_outputs["flow_status"]) == {
            "status",
            "current_stage",
            "progress_percent",
            "blocking_reasons",
        }
        assert "automation" not in document_outputs
        assert document_outputs["next_context"]["current_stage"] == "code_scope"
        change_outputs = task_dispatcher.call(
            "copilot_record_change_outputs",
            {
                "coding_task_id": coding_task_id,
                "workspace_root": str(copilot_workspace),
                "output_stage": "code_scope",
                "code_scope": [
                        {
                            "target_path": "src/main/java/example/ExpenseService.java",
                            "target_symbols": ["search(String status)"],
                        "recommended_action": "modify",
                        "test_file_refs": [
                            "src/test/java/example/ExpenseServiceTest.java"
                        ],
                            "rationale": "差戻し検索のサービス分岐と回帰テストが影響範囲です。",
                            "ui_impact": True,
                        },
                        {
                            "target_path": "src/test/scripts/expense-status-search.sh",
                            "target_symbols": [],
                            "recommended_action": "add",
                            "test_file_refs": [
                                "src/test/scripts/expense-status-search.sh"
                            ],
                            "rationale": "差戻し検索の UI 回帰テストを追加します。",
                            "ui_impact": False,
                        },
                    ],
                },
            )
        assert change_outputs["recorded_stage"] == "code_scope"
        assert change_outputs["coding_task_state"] == "in_progress"
        assert change_outputs["code_scope"][0]["target_path"] == (
            "src/main/java/example/ExpenseService.java"
        )
        assert set(change_outputs) == {
            "recorded_stage",
            "next_stage",
            "coding_task_state",
            "code_scope",
            "flow_status",
            "next_context",
        }
        assert change_outputs["next_context"]["execution_scope"]["bound"] is True
        assert change_outputs["next_context"]["current_stage"] == "compile_test"
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
        assert set(task_diff) == {
            "edit_result_id",
            "created",
            "status",
            "command_evidence_status",
            "changed_paths",
            "out_of_scope_files",
            "result_repository_revision",
            "coding_task_state",
            "changed_line_coverage",
        }
        planning_outputs = task_dispatcher.call(
            "copilot_record_change_outputs",
            {
                "coding_task_id": coding_task_id,
                "workspace_root": str(copilot_workspace),
                "output_stage": "test_planning",
                "test_plan": generated_test_plan,
                "test_data_plan": generated_test_data_plan,
            },
        )
        assert planning_outputs["test_plan_id"] == generated_test_plan["test_plan_id"]
        assert planning_outputs["recorded_stage"] == "test_planning"
        assert planning_outputs["next_stage"] == "compile_test"
        assert planning_outputs["flow_status"]["status"] == "in_progress"
        assert planning_outputs["flow_status"]["current_stage"] == "compile_test"
        assert planning_outputs["next_context"] is not None
        assert planning_outputs["next_context"]["current_stage"] == "compile_test"
        assert set(planning_outputs) == {
            "recorded_stage",
            "next_stage",
            "coding_task_state",
            "test_plan_id",
            "test_data_plan_id",
            "flow_status",
            "next_context",
        }
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
        assert set(task_command) == {
            "command_execution_id",
            "created",
            "command_ref",
            "status",
            "exit_code",
            "stdout_digest",
            "stderr_digest",
            "stdout_bytes",
            "stderr_bytes",
            "output_truncated",
            "started_at",
            "completed_at",
            "coding_task_state",
        }
        task_ui_command_id = f"copilot-task-ui-command-{suffix}"
        task_ui_command = task_dispatcher.call(
            "copilot_run_task_command",
            {
                "coding_task_id": coding_task_id,
                "workspace_root": str(copilot_workspace),
                "command_execution_id": task_ui_command_id,
                "command_ref": "ui-e2e",
            },
        )
        assert task_ui_command["status"] == "passed"
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
                "test_result_refs": [task_command_id, task_ui_command_id],
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
        assert set(task_result) == {
            "edit_result_id",
            "created",
            "status",
            "command_evidence_status",
            "changed_paths",
            "out_of_scope_files",
            "result_repository_revision",
            "coding_task_state",
            "changed_line_coverage",
            "flow_status",
        }
        assert set(task_result["flow_status"]) == {
            "status",
            "current_stage",
            "progress_percent",
            "blocking_reasons",
        }
        assert "automation" not in task_result
        final_task = coding_tasks.view(coding_task_id)
        assert final_task["state"] == "completed"
        assert [item["status"] for item in final_task["commands"]] == [
            "passed",
            "passed",
        ]
        assert [item["validation_mode"] for item in final_task["edit_results"]] == [
            "working",
            "committed",
        ]
        assert [event["event_type"] for event in final_task["events"]] == [
            "published",
                "claimed",
                "accepted",
                "context_loaded",
                "outputs_recorded",
                "outputs_recorded",
                "claim_recovered",
                "diff_recorded",
                "outputs_recorded",
                "command_recorded",
                "command_recorded",
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
        ui_command_execution_id = f"ui-command-execution-{suffix}"
        ui_command_result = command_service.run(
            ApprovedCommandRequest(
                command_execution_id=ui_command_execution_id,
                approval_grant_id=grant_id,
                project_id=project_id,
                analysis_case_id=case_id,
                edit_packet_id=packet_request.edit_packet_id,
                workspace_root=workspace,
                command_ref="ui-e2e",
            )
        )
        assert ui_command_result.record.status == "passed"
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
        with pytest.raises(ValueError, match="exact required command set"):
            edit_result_service.run(
                EditResultRequest(
                    edit_result_id=f"edit-result-incomplete-tests-{suffix}",
                    edit_packet_id=packet_request.edit_packet_id,
                    approval_grant_id=grant_id,
                    project_id=project_id,
                    analysis_case_id=case_id,
                    workspace_root=workspace,
                    mode=EditValidationMode.COMMITTED,
                    test_result_refs=(command_execution_id,),
                    tests_passed=True,
                )
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
                test_result_refs=(command_execution_id, ui_command_execution_id),
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
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT command_execution_id
                FROM edit_result_command_executions
                WHERE edit_result_id = %s
                """,
                (committed.record.edit_result_id,),
            )
            assert {str(row[0]) for row in cursor.fetchall()} == {
                command_execution_id,
                ui_command_execution_id,
            }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM analysis_cases WHERE analysis_case_id = %s",
                (case_id,),
            )
            assert cursor.fetchone() == ("verifying_ui",)

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
