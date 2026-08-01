"""Trusted Web control-plane use cases over existing Canonical services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit

from psycopg import Connection

from operamind.application.approval_grant import (
    ApprovalGrantRequest,
    ApprovalGrantService,
)
from operamind.application.change_automation import (
    STAGE_LABELS,
    ChangeAutomationDecision,
    decide_change_automation,
)
from operamind.application.change_orchestration_service import ChangeOrchestrationService
from operamind.application.copilot_coding_task import (
    CopilotCodingTaskPublishRequest,
    CopilotCodingTaskService,
    build_bridge_task_view,
)
from operamind.application.edit_packet import EditPacketRequest, EditPacketService
from operamind.application.failure_management import build_failure_management
from operamind.application.main_change_flow import build_main_change_flow
from operamind.application.orchestration_task import (
    OrchestrationSchedulingPolicy,
    build_orchestration_task,
)
from operamind.application.project_document_baseline import ProjectDocumentBaselineService
from operamind.application.project_stack import ProjectProfileBootstrapper
from operamind.application.test_case_revision_service import TestCaseRevisionService
from operamind.contracts import ContractCatalog
from operamind.domain.test_case_execution_scope import (
    compare_test_case_version_results,
)
from operamind.infrastructure.code_graph import GitWorkspaceInspector
from operamind.infrastructure.postgres import (
    AnalysisRepository,
    ArtifactRepository,
    ChangeAutomationRepository,
    ChangeAutomationRunRecord,
    ChangeClosureRepository,
    ChangeOrchestrationRepository,
    CodeGraphSnapshotRepository,
    ImpactRepository,
    OrchestrationTaskRepository,
    PersistenceConflictError,
    ProfileRepository,
    TestCaseExecutionAuthorizationRepository,
    TestDataExecutionEventWrite,
    TestDataExecutionRepository,
    TestDataExecutionRunWrite,
    WebControlPlaneRepository,
)
from operamind.infrastructure.postgres.web_command_repository import WebCommandRepository
from operamind.profiles import ProfileCatalog

_AUTOMATIC_FORBIDDEN_GLOBS = (
    ".git/**",
    "**/.env",
    "**/.env.*",
    "**/*.key",
    "**/*.pem",
)


@dataclass(frozen=True, slots=True)
class BusinessRuleInput:
    business_rule_id: str
    text: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChangeRequestInput:
    change_request_id: str
    project_id: str
    analysis_case_id: str | None
    input_mode: str
    requirement_text: str | None
    source_document_ref: str | None
    target_document_ref: str | None
    business_rules: tuple[BusinessRuleInput, ...]
    ambiguity_status: str
    ambiguities: tuple[str, ...]
    submitted_by: str


@dataclass(frozen=True, slots=True)
class ProjectInitializationInput:
    project_id: str
    name: str
    workspace_root: Path
    document_roots: tuple[Path, ...]
    configured_by: str


class WebControlPlaneService:
    """Coordinate Web commands without weakening existing transaction boundaries."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        repository_root: Path,
        orchestration_scheduling_policy: OrchestrationSchedulingPolicy | None = None,
    ) -> None:
        self._connection = connection
        self._root = repository_root.resolve()
        self._contracts = ContractCatalog.load(self._root / "contracts")
        self._profiles = ProfileCatalog.load(self._root / "profiles")
        self._repository = WebControlPlaneRepository(connection, self._contracts)
        self._web_commands = WebCommandRepository(connection)
        self._artifacts = ArtifactRepository(connection, self._contracts)
        self._code_graphs = CodeGraphSnapshotRepository(connection, self._contracts)
        self._impacts = ImpactRepository(connection, self._contracts)
        self._grant_service = ApprovalGrantService(
            connection=connection,
            contracts=self._contracts,
            profiles=self._profiles,
        )
        self._orchestration_service = ChangeOrchestrationService(
            connection=connection, repository_root=self._root
        )
        self._orchestrations = ChangeOrchestrationRepository(connection, self._contracts)
        self._test_data_runs = TestDataExecutionRepository(connection, self._contracts)
        self._closures = ChangeClosureRepository(connection, self._contracts)
        self._automation_runs = ChangeAutomationRepository(connection)
        self._profile_repository = ProfileRepository(connection, self._profiles)
        self._orchestration_tasks = OrchestrationTaskRepository(
            connection, orchestration_scheduling_policy
        )
        self._test_case_revisions = TestCaseRevisionService(
            connection=connection,
            repository_root=self._root,
        )
        self._case_execution_authorizations = TestCaseExecutionAuthorizationRepository(
            connection, self._contracts
        )

    def execute_web_command(
        self,
        *,
        command_scope: str,
        idempotency_key: str,
        actor: str,
        payload: dict[str, object],
        operation: Callable[[], dict[str, object]],
    ) -> dict[str, object]:
        """Execute and replay one human command under a durable receipt."""
        return self._web_commands.execute(
            command_scope=command_scope,
            idempotency_key=idempotency_key,
            actor=actor,
            payload=payload,
            operation=operation,
        )

    def submit_change_request(self, value: ChangeRequestInput) -> dict[str, object]:
        if not value.business_rules:
            raise ValueError("Change Request requires at least one business rule")
        artifact: dict[str, Any] = {
            "artifact_type": "ChangeRequest",
            "schema_version": "v1",
            "change_request_id": value.change_request_id,
            "project_id": value.project_id,
            "input_mode": value.input_mode,
            "business_rules": [
                {
                    "business_rule_id": rule.business_rule_id,
                    "text": rule.text,
                    "source_refs": list(rule.source_refs),
                }
                for rule in value.business_rules
            ],
            "ambiguity_status": value.ambiguity_status,
            "confirmation_required": value.ambiguity_status == "needs_confirmation",
            "ambiguities": list(value.ambiguities),
        }
        optional = {
            "requirement_text": value.requirement_text,
            "source_document_ref": value.source_document_ref,
            "target_document_ref": value.target_document_ref,
        }
        artifact.update({key: item for key, item in optional.items() if item is not None})
        analysis_case_id = value.analysis_case_id
        case_blocker: str | None = None
        if analysis_case_id is None and value.ambiguity_status == "clear":
            try:
                analysis_case_id = self._ensure_change_request_case(value)
            except ValueError as error:
                case_blocker = str(error)
        if case_blocker is not None:
            raise ValueError(
                "変更要件を開始できません。Project のコード基線を準備してください: "
                f"{case_blocker}"
            )
        record = self._repository.submit_change_request(
            artifact=artifact,
            analysis_case_id=analysis_case_id,
            submitted_by=value.submitted_by,
        )
        response: dict[str, object] = {
            "created": record.created,
            "change_request": self._repository.get_change_request(record.change_request_id),
        }
        if value.ambiguity_status == "clear":
            registered_workspace = self._repository.project_workspace_root(value.project_id)
            if registered_workspace is None:
                response["copilot_task"] = None
                response["task_blocker"] = (
                    "Project has no registered Workspace for the VS Code Change Task"
                )
            else:
                task_id = _web_id(
                    "copilot-change-task",
                    value.project_id,
                    value.change_request_id,
                    "initial",
                )
                response["copilot_task"] = CopilotCodingTaskService(
                    connection=self._connection,
                    repository_root=self._root,
                ).publish(
                    CopilotCodingTaskPublishRequest(
                        coding_task_id=task_id,
                        change_request_id=value.change_request_id,
                        project_id=value.project_id,
                        workspace_root=Path(registered_workspace),
                        task_summary=value.requirement_text or value.business_rules[0].text,
                        actor=value.submitted_by,
                        idempotency_key="initial-change-task",
                    )
                )
        return response

    def initialize_project(self, value: ProjectInitializationInput) -> dict[str, object]:
        """Register local roots and prepare the Canonical RAG baseline when Git is available."""

        workspace_root = _resolved_local_directory(
            value.workspace_root,
            field_name="コード Workspace",
        )
        document_roots = tuple(
            _resolved_local_directory(root, field_name="設計書の場所")
            for root in value.document_roots
        )
        if len(document_roots) != len(set(document_roots)):
            raise ValueError("設計書の場所に同じフォルダーを重複して登録できません")
        source_control_kind = "git" if (workspace_root / ".git").exists() else "local_files"
        record = self._repository.initialize_project(
            project_id=value.project_id,
            name=value.name,
            workspace_root=str(workspace_root),
            source_control_kind=source_control_kind,
            document_roots=tuple(str(root) for root in document_roots),
            configured_by=value.configured_by,
        )
        response: dict[str, object] = {
            "created": record.created,
            "project": {
                "project_id": record.project_id,
                "name": record.name,
                "workspace_root": record.workspace_root,
                "document_roots": list(record.document_roots),
                "source_control_kind": record.source_control_kind,
            },
        }
        if source_control_kind == "git":
            baseline = ProjectDocumentBaselineService(
                connection=self._connection,
                repository_root=self._root,
            ).ensure(
                project_id=value.project_id,
                document_roots=document_roots,
                actor=value.configured_by,
            )
            response["document_baseline"] = {
                "status": "ready",
                "snapshot_id": baseline.snapshot_id,
                "document_count": baseline.document_count,
                "index_build_id": baseline.index_build_id,
                "generated_vector_count": baseline.generated_vector_count,
                "embedding_profile_binding_key": (
                    baseline.embedding_profile_binding_key
                ),
            }
        return response

    def _ensure_change_request_case(self, value: ChangeRequestInput) -> str:
        registration = self._repository.project_repository_registration(value.project_id)
        if registration is None:
            workspace = self._repository.project_workspace_registration(value.project_id)
            if workspace is None:
                raise ValueError("Project has no registered Workspace for automatic Analysis Case")
            if workspace["source_control_kind"] != "git":
                raise ValueError(
                    "ローカルファイル Workspace はファイル Digest 基線が完成するまで"
                    "自動 Analysis Case の対象外です"
                )
            workspace_root = Path(workspace["workspace_root"]).resolve(strict=True)
            git = GitWorkspaceInspector().inspect(workspace_root)
            repository_id = _web_id(
                "repository", value.project_id, git.remote_url, str(workspace_root)
            )
            self._repository.register_repository(
                repository_id=repository_id,
                project_id=value.project_id,
                remote_url=git.remote_url,
                workspace_root=str(workspace_root),
            )
            registration = {
                "project_name": workspace["project_name"],
                "repository_id": repository_id,
                "remote_url": git.remote_url,
                "workspace_root": str(workspace_root),
            }
        workspace_root = Path(registration["workspace_root"]).resolve(strict=True)
        git = GitWorkspaceInspector().inspect(workspace_root)
        if git.remote_url != registration["remote_url"]:
            raise ValueError("Registered Repository remote differs from Workspace Git remote")
        ProjectProfileBootstrapper(
            profiles=self._profiles,
            repository=self._profile_repository,
        ).ensure(
            project_id=value.project_id,
            repository_id=registration["repository_id"],
            workspace_root=workspace_root,
        )
        case_id = _web_id(
            "analysis-case",
            value.project_id,
            value.change_request_id,
            "automatic",
        )
        AnalysisRepository(self._connection).start(
            project_id=value.project_id,
            project_name=registration["project_name"],
            repository_id=registration["repository_id"],
            remote_url=registration["remote_url"],
            workspace_root=str(workspace_root),
            repository_revision_id=(
                self._repository.repository_revision_id(
                    repository_id=registration["repository_id"],
                    commit_sha=git.head_sha,
                )
                or _web_id(
                    "repository-revision",
                    registration["repository_id"],
                    git.head_sha,
                )
            ),
            commit_sha=git.head_sha,
            analysis_case_id=case_id,
        )
        return case_id

    def list_projects(self) -> dict[str, object]:
        projects = self._repository.list_projects()
        return {"projects": list(projects), "count": len(projects)}

    def list_change_requests(self, *, project_id: str) -> dict[str, object]:
        requests = self._repository.list_change_requests(project_id=project_id)
        return {"change_requests": list(requests), "count": len(requests)}

    def main_change_flow(self, request_id: str) -> dict[str, object]:
        """Return the six-stage product flow without internal control-plane state."""
        request = self._repository.get_change_request(request_id)
        case_id = request.get("analysis_case_id")
        workspace = (
            self._repository.case_workspace(
                project_id=str(request["project_id"]),
                case_id=str(case_id),
            )
            if case_id is not None
            else None
        )
        if workspace is not None:
            workspace["impact_artifact"] = self._repository.impact_report(
                project_id=str(request["project_id"]),
                case_id=str(case_id),
            )
            impact_artifact = workspace["impact_artifact"]
            if isinstance(impact_artifact, dict):
                graph_id = impact_artifact.get("code_graph_snapshot_id")
                if isinstance(graph_id, str) and graph_id:
                    workspace["code_graph_artifact"] = self._code_graphs.get(graph_id)
        automation = self._automation_runs.latest_for_request(request_id)
        task = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).latest_for_request(request_id)
        return build_main_change_flow(
            request=request,
            document_diff=self._repository.document_diff(request_id),
            workspace=workspace,
            automation=self._decorate_automation(automation) if automation is not None else None,
            copilot_task=task,
            execution=self.execution_management(request_id),
        )

    def propose_test_case_revision(
        self, *, request_id: str, instruction: str, actor: str
    ) -> dict[str, object]:
        """Preview a natural-language Test Case change without changing the active plan."""

        result = self._test_case_revisions.propose(
            change_request_id=request_id,
            instruction=instruction,
            actor=actor,
        )
        return {
            "state": result["state"],
            "proposal": _public_test_case_proposal(cast_dict(result["proposal"])),
        }

    def confirm_test_case_revision(
        self,
        *,
        request_id: str,
        proposal_id: str,
        selections: dict[str, str],
        actor: str,
    ) -> dict[str, object]:
        """Apply one reviewed proposal and restart only the downstream test flow."""

        result = self._test_case_revisions.confirm(
            change_request_id=request_id,
            proposal_id=proposal_id,
            selections=selections,
            actor=actor,
        )
        revision = cast_dict(result["revision"])
        revision_id = str(revision["revision_id"])
        self.start_change_automation(
            request_id=request_id,
            idempotency_key=f"test-case-revision:{revision_id}",
            actor="automation:operamind",
        )
        return {
            "state": "applied",
            "flow": self.main_change_flow(request_id),
        }

    def claim_copilot_task(self, *, workspace_root: Path, consumer_id: str) -> dict[str, object]:
        task = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).claim_next(workspace_root=workspace_root, consumer_id=consumer_id)
        return {"task": build_bridge_task_view(task) if task is not None else None}

    def accept_copilot_task(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        consumer_id: str,
        actor: str,
    ) -> dict[str, object]:
        view = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).accept(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            consumer_id=consumer_id,
            actor=actor,
        )
        return build_bridge_task_view(view)

    def resume_copilot_task(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        consumer_id: str,
    ) -> dict[str, object]:
        view = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).resume(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            consumer_id=consumer_id,
        )
        return build_bridge_task_view(view)

    def cancel_copilot_task_from_bridge(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        consumer_id: str,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        task = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        )
        resumed = task.resume(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            consumer_id=consumer_id,
        )
        artifact = cast_dict(resumed["task"])
        return build_bridge_task_view(
            task.cancel(
                coding_task_id=coding_task_id,
                change_request_id=str(artifact["change_request_id"]),
                actor=actor,
                reason=reason,
                idempotency_key=f"bridge:{consumer_id}",
                consumer_id=consumer_id,
            )
        )

    def start_change_automation(
        self, *, request_id: str, idempotency_key: str, actor: str
    ) -> dict[str, object]:
        request = self._repository.get_change_request(request_id)
        project_id = str(request["project_id"])
        run_id = _web_id("change-automation", project_id, request_id, idempotency_key)
        record = self._automation_runs.start(
            run_id=run_id,
            request_id=request_id,
            project_id=project_id,
            idempotency_key=idempotency_key,
            actor=actor,
        )
        return self._advance_change_automation(
            run_id=record.automation_run_id, actor=actor, created=record.created
        )

    def decide_change_checkpoint(
        self,
        *,
        request_id: str,
        checkpoint: str,
        decision: str,
        surface: str,
        actor: str,
        idempotency_key: str,
        note: str | None = None,
    ) -> dict[str, object]:
        """Record one shared human decision and resume deterministic work."""
        action_checkpoints = {
            "confirm_requirement": "requirement",
            "confirm_rag_documents": "rag_documents",
            "confirm_document_diff": "document_diff",
            "confirm_code_scope": "code_scope",
            "confirm_test_plan": "test_plan",
            "confirm_ui_test": "ui_test",
            "confirm_final_report": "final_report",
        }
        if decision not in {"confirmed", "rejected"}:
            raise ValueError("確認結果は confirmed または rejected を指定してください")
        run_view = self._automation_runs.latest_for_request(request_id)
        if run_view is None:
            raise ValueError("変更フローが開始されていません")
        run_id = str(run_view["automation_run_id"])
        record = self._automation_runs.get(run_id)
        request, diff, workspace, bundle, execution = self._automation_inputs(record)
        current, _discovery, subjects, _confirmations = self._automation_decision(
            record=record,
            request=request,
            diff=diff,
            workspace=workspace,
            bundle=bundle,
            execution=execution,
        )
        expected_checkpoint = action_checkpoints.get(str(current.next_action))
        if checkpoint != expected_checkpoint:
            raise ValueError(
                "現在の確認点と一致しません: "
                f"expected={expected_checkpoint or 'none'}, actual={checkpoint}"
            )
        subject_digest = subjects.get(checkpoint)
        if subject_digest is None:
            raise ValueError("現在の証跡から確認対象を作成できません")
        confirmation_id = _web_id(
            "change-checkpoint-confirmation",
            run_id,
            checkpoint,
            subject_digest,
            decision,
            idempotency_key,
        )
        if decision == "confirmed" and checkpoint == "document_diff":
            self._confirm_document_diff(
                record=record,
                confirmation_id=confirmation_id,
                actor=actor,
                note=note,
            )
        if decision == "confirmed" and checkpoint == "code_scope":
            if workspace is None:
                raise RuntimeError("コード範囲確認の Case workspace が失われました")
            self._confirm_impact(
                record=record,
                confirmation_id=confirmation_id,
                actor=actor,
                workspace=workspace,
                note=note,
            )
        confirmation = self._automation_runs.record_confirmation(
            confirmation_id=confirmation_id,
            run_id=run_id,
            checkpoint=checkpoint,
            subject_digest=subject_digest,
            decision=decision,
            surface=surface,
            actor=actor,
            note=note,
        )
        advanced = self._advance_change_automation(
            run_id=run_id,
            actor=actor,
            created=False,
        )
        return {"confirmation": confirmation, **advanced}

    def _advance_change_automation(
        self, *, run_id: str, actor: str, created: bool
    ) -> dict[str, object]:
        record = self._automation_runs.get(run_id)
        request, diff, workspace, bundle, execution = self._automation_inputs(record)
        decision, _discovery, _subjects, _confirmations = self._automation_decision(
            record=record,
            request=request,
            diff=diff,
            workspace=workspace,
            bundle=bundle,
            execution=execution,
        )
        if decision.next_action == "provision_execution_scope":
            decision = self._provision_execution_scope_or_block(
                record=record,
                run_id=run_id,
                workspace=workspace,
            )
            if decision.status != "blocked":
                request, diff, workspace, bundle, execution = self._automation_inputs(record)
                decision, _discovery, _subjects, _confirmations = self._automation_decision(
                    record=record,
                    request=request,
                    diff=diff,
                    workspace=workspace,
                    bundle=bundle,
                    execution=execution,
                )
        if decision.next_action == "start_test_data_execution" and bundle is not None:
            decision = self._authorize_test_data_execution_or_block(
                record=record,
                run_id=run_id,
                bundle=bundle,
            )
        current_task = self._sync_orchestration_task(record=record, decision=decision, actor=actor)
        if decision.stage == "planning" and current_task is None:
            raise RuntimeError("Planning decision did not produce an Orchestration Task")
        if (
            decision.stage == "planning"
            and decision.status == "running"
            and current_task is not None
            and current_task["state"] in {"submitted", "completed", "failed", "blocked"}
        ):
            task_state = str(current_task["state"])
            decision = ChangeAutomationDecision(
                stage="planning",
                status="blocked",
                next_action="resolve_blocker",
                blocking_reason=(
                    "編成 Task の結果と Canonical 編成 Artifact が一致していません: "
                    f"task_state={task_state}"
                ),
                message="編成 Task の結果を確認し、Canonical 状態を復旧してください。",
            )
        if (
            decision.stage == "planning"
            and decision.status == "running"
            and current_task is not None
            and current_task["state"] == "ready"
        ):
            internal_executor_id = (
                f"operamind-single-agent:{hashlib.sha256(run_id.encode()).hexdigest()[:24]}"
            )
            internal_registration = self._orchestration_tasks.register_worker(
                executor_kind="agent",
                executor_id=internal_executor_id,
                capabilities=("change_planning",),
                project_id=record.project_id,
                max_concurrent_tasks=1,
                lease_seconds=300,
            )
            internal_worker_token = str(internal_registration["worker_token"])
            claimed_task = self._orchestration_tasks.claim(
                task_id=str(current_task["orchestration_task_id"]),
                executor_kind="agent",
                executor_id=internal_executor_id,
                capabilities=("change_planning",),
                project_id=record.project_id,
                worker_token=internal_worker_token,
            )
            lease_token = str(claimed_task["lease_token"])
            self._orchestration_tasks.heartbeat(
                task_id=str(current_task["orchestration_task_id"]),
                executor_id=internal_executor_id,
                lease_token=lease_token,
            )
            self._automation_runs.transition(
                run_id=run_id,
                actor=actor,
                stage=decision.stage,
                status=decision.status,
                next_action=decision.next_action,
                blocking_reason=None,
                message=decision.message,
            )
            try:
                result = self._orchestration_service.orchestrate(
                    change_request_id=record.change_request_id, actor=actor
                )
            except Exception as error:
                blocking_reason = (
                    "自動編成の内部処理に失敗しました。再試行または原因確認が必要です。"
                    f" ({type(error).__name__})"
                )
                self._orchestration_tasks.record_result(
                    task_id=str(current_task["orchestration_task_id"]),
                    executor_id=internal_executor_id,
                    lease_token=lease_token,
                    outcome="blocked",
                    summary="自動編成を完了できませんでした。",
                    artifact_refs=(),
                    evidence={"blocking_reason": blocking_reason},
                )
                decision = type(decision)(
                    stage="planning",
                    status="blocked",
                    next_action="resolve_blocker",
                    blocking_reason=blocking_reason,
                    message="自動編成を完了できませんでした。Canonical 状態を確認してください。",
                )
            else:
                bundle = self._orchestrations.latest_bundle(record.change_request_id)
                decision, _discovery, _subjects, _confirmations = self._automation_decision(
                    record=record,
                    request=request,
                    diff=diff,
                    workspace=workspace,
                    bundle=bundle,
                    execution=self.execution_management(record.change_request_id),
                )
                orchestration = cast_dict(result.orchestration)
                artifact_refs = (
                    str(orchestration["orchestration_id"]),
                    *tuple(
                        str(value) for value in cast_dict(orchestration["artifact_refs"]).values()
                    ),
                )
                self._automation_runs.transition(
                    run_id=run_id,
                    actor=actor,
                    stage="planning",
                    status="completed",
                    next_action="inspect_generated_plan",
                    blocking_reason=None,
                    message=(
                        "コード範囲、Test Case、テストデータ、カバレッジ、"
                        "UI シナリオを生成しました。"
                    ),
                    artifact_refs=artifact_refs,
                )
                if decision.next_action == "provision_execution_scope":
                    decision = self._provision_execution_scope_or_block(
                        record=record,
                        run_id=run_id,
                        workspace=workspace,
                    )
                    if decision.status != "blocked":
                        request, diff, workspace, bundle, execution = self._automation_inputs(
                            record
                        )
                        decision, _discovery, _subjects, _confirmations = self._automation_decision(
                            record=record,
                            request=request,
                            diff=diff,
                            workspace=workspace,
                            bundle=bundle,
                            execution=execution,
                        )
            finally:
                self._orchestration_tasks.unregister_worker(
                    executor_kind="agent",
                    executor_id=internal_executor_id,
                    worker_token=internal_worker_token,
                )
        self._sync_orchestration_task(record=record, decision=decision, actor=actor)
        self._automation_runs.transition(
            run_id=run_id,
            actor=actor,
            stage=decision.stage,
            status=decision.status,
            next_action=decision.next_action,
            blocking_reason=decision.blocking_reason,
            message=decision.message,
        )
        view = self._automation_runs.view(run_id)
        return {"created": created, "run": self._decorate_automation(view)}

    def _authorize_test_data_execution_or_block(
        self,
        *,
        record: ChangeAutomationRunRecord,
        run_id: str,
        bundle: dict[str, object],
    ) -> ChangeAutomationDecision:
        orchestration = cast_dict(bundle["orchestration"])
        try:
            authorization = self._case_execution_authorizations.confirm_deterministic_scope(
                target_orchestration_id=str(orchestration["orchestration_id"]),
                actor="automation:operamind",
                at=datetime.now(UTC),
            )
            management = self.execution_management(record.change_request_id)
            controls = cast_dict(management["controls"])
            if controls.get("can_start") is not True:
                reason = str(
                    controls.get("blocking_reason") or "TestDataPlan execution is not authorized"
                )
                raise ValueError(reason)
        except (ValueError, PersistenceConflictError) as error:
            reason = f"テスト実行範囲を自動確定できません: {error}"
            return ChangeAutomationDecision(
                stage="test_data_execution",
                status="blocked",
                next_action="resolve_blocker",
                blocking_reason=reason,
                message=reason,
            )
        artifact_refs = (authorization.authorization_id,) if authorization is not None else ()
        self._automation_runs.transition(
            run_id=run_id,
            actor="automation:operamind",
            stage="test_data_execution",
            status="waiting",
            next_action="start_test_data_execution",
            blocking_reason=None,
            message="テスト実行範囲を内部で確定しました。TestDataPlan の実行を開始できます。",
            artifact_refs=artifact_refs,
        )
        return ChangeAutomationDecision(
            stage="test_data_execution",
            status="waiting",
            next_action="start_test_data_execution",
            blocking_reason=None,
            message="テスト実行範囲を内部で確定しました。TestDataPlan の実行を開始できます。",
        )

    def _provision_execution_scope_or_block(
        self,
        *,
        record: ChangeAutomationRunRecord,
        run_id: str,
        workspace: dict[str, object] | None,
    ) -> ChangeAutomationDecision:
        try:
            artifact_refs = self._provision_execution_scope(record=record, run_id=run_id)
        except (ValueError, PersistenceConflictError) as error:
            reason = f"コード実行範囲を自動準備できません: {error}"
            return ChangeAutomationDecision(
                stage="execution_approval",
                status="blocked",
                next_action="resolve_blocker",
                blocking_reason=reason,
                message=reason,
            )
        self._automation_runs.transition(
            run_id=run_id,
            actor="automation:operamind",
            stage="execution_approval",
            status="completed",
            next_action=None,
            blocking_reason=None,
            message="確認済み Impact からコード変更とテストの実行範囲を自動準備しました。",
            artifact_refs=artifact_refs,
        )
        return ChangeAutomationDecision(
            stage="execution_approval",
            status="completed",
            next_action=None,
            blocking_reason=None,
            message="コード実行範囲の自動準備が完了しました。",
        )

    def _provision_execution_scope(
        self, *, record: ChangeAutomationRunRecord, run_id: str
    ) -> tuple[str, ...]:
        automatic_actor = "automation:operamind"
        request = self._repository.get_change_request(record.change_request_id)
        case_id_value = request.get("analysis_case_id")
        if not isinstance(case_id_value, str):
            raise ValueError("Change Request has no bound Analysis Case")
        case_id = case_id_value
        workspace_root_value = self._repository.project_workspace_root(record.project_id)
        if workspace_root_value is None:
            raise ValueError("Project has no registered Workspace")
        workspace_root = Path(workspace_root_value)
        workspace = self._repository.case_workspace(
            project_id=record.project_id,
            case_id=case_id,
        )
        impact = cast_dict(workspace["impact_report"])
        confirmation = cast_dict(workspace["confirmation"])
        impact_report_id = impact.get("id")
        confirmation_id = confirmation.get("id")
        if not isinstance(impact_report_id, str) or not isinstance(confirmation_id, str):
            raise ValueError("Confirmed Impact and confirmation are required")

        packet = cast_dict(workspace["edit_packet"])
        packet_id = packet.get("id")
        if not isinstance(packet_id, str):
            packet_id = _web_id(
                "copilot-edit-packet",
                record.project_id,
                case_id,
                impact_report_id,
                run_id,
            )
            EditPacketService(
                connection=self._connection,
                contracts=self._contracts,
            ).run(
                EditPacketRequest(
                    edit_packet_id=packet_id,
                    project_id=record.project_id,
                    analysis_case_id=case_id,
                    impact_report_id=impact_report_id,
                    confirmation_id=confirmation_id,
                    workspace_root=workspace_root,
                    forbidden_globs=_AUTOMATIC_FORBIDDEN_GLOBS,
                )
            )
        packet_artifact = self._artifacts.get(packet_id)
        if packet_artifact is None:
            raise RuntimeError("Automatic Edit Packet has no immutable Artifact")
        test_files = packet_artifact.get("test_files")
        if not isinstance(test_files, list) or not test_files:
            raise ValueError("Confirmed code scope has no test files")

        command_bindings = self._profile_repository.list_active_by_type(
            project_id=record.project_id,
            profile_type="CommandExecutionProfile",
        )
        if len(command_bindings) != 1:
            raise ValueError(
                "Project must have exactly one active CommandExecutionProfile "
                f"(found {len(command_bindings)})"
            )
        command_binding = command_bindings[0]
        templates = command_binding.profile.get("templates")
        if not isinstance(templates, list):
            raise RuntimeError("Validated CommandExecutionProfile lost its templates")
        command_refs = tuple(
            str(template["command_ref"])
            for template in templates
            if isinstance(template, dict) and isinstance(template.get("command_ref"), str)
        )
        if not command_refs:
            raise ValueError("Active CommandExecutionProfile has no command templates")

        workspace = self._repository.case_workspace(
            project_id=record.project_id,
            case_id=case_id,
        )
        grant = cast_dict(workspace["approval_grant"])
        grant_id = grant.get("id")
        if not isinstance(grant_id, str):
            grant_id = _web_id(
                "automatic-approval-grant",
                record.project_id,
                case_id,
                packet_id,
                run_id,
            )
            self._grant_service.issue(
                ApprovalGrantRequest(
                    grant_id=grant_id,
                    project_id=record.project_id,
                    analysis_case_id=case_id,
                    edit_packet_id=packet_id,
                    approved_by=automatic_actor,
                    expires_at=datetime.now(UTC) + timedelta(hours=8),
                    command_profile_binding_key=command_binding.binding_key,
                    allowed_test_command_refs=command_refs,
                )
            )

        task_service = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        )
        task_view = task_service.latest_for_request(record.change_request_id)
        if task_view is None:
            task_view = task_service.publish(
                CopilotCodingTaskPublishRequest(
                    coding_task_id=_web_id(
                        "copilot-change-task",
                        record.project_id,
                        record.change_request_id,
                        "initial",
                    ),
                    change_request_id=record.change_request_id,
                    project_id=record.project_id,
                    edit_packet_id=packet_id,
                    approval_grant_id=grant_id,
                    workspace_root=workspace_root,
                    task_summary=str(
                        cast_dict(request["artifact"]).get("requirement_text")
                        or "Confirmed change request"
                    ),
                    actor=automatic_actor,
                    idempotency_key="automatic-execution-scope",
                )
            )
        task_artifact = cast_dict(task_view["task"])
        coding_task_id = str(task_artifact["coding_task_id"])
        task_service.bind_execution_scope(
            coding_task_id=coding_task_id,
            analysis_case_id=case_id,
            edit_packet_id=packet_id,
            approval_grant_id=grant_id,
            workspace_root=workspace_root,
            actor=automatic_actor,
        )
        return packet_id, grant_id, coding_task_id

    def _automation_inputs(
        self, record: ChangeAutomationRunRecord
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        dict[str, object] | None,
        dict[str, object] | None,
        dict[str, object] | None,
    ]:
        request = self._repository.get_change_request(record.change_request_id)
        diff = self._repository.document_diff(record.change_request_id)
        case_id = request["analysis_case_id"]
        workspace: dict[str, object] | None = None
        if case_id is not None:
            workspace = self._repository.case_workspace(
                project_id=record.project_id, case_id=str(case_id)
            )
            workspace["impact_artifact"] = self._repository.impact_report(
                project_id=record.project_id,
                case_id=str(case_id),
            )
        bundle = self._orchestrations.latest_bundle(record.change_request_id)
        execution = (
            self.execution_management(record.change_request_id) if bundle is not None else None
        )
        return request, diff, workspace, bundle, execution

    def _automation_decision(
        self,
        *,
        record: ChangeAutomationRunRecord,
        request: dict[str, object],
        diff: dict[str, object],
        workspace: dict[str, object] | None,
        bundle: dict[str, object] | None,
        execution: dict[str, object] | None,
    ) -> tuple[
        ChangeAutomationDecision,
        dict[str, object],
        dict[str, str],
        dict[str, dict[str, object]],
    ]:
        requirement_subjects = _checkpoint_subjects(
            request=request,
            diff=diff,
            workspace=workspace,
            bundle=bundle,
            execution=execution,
            rag_discovery=None,
        )
        current = self._automation_runs.current_confirmations(
            run_id=record.automation_run_id,
            subject_digests=requirement_subjects,
        )
        requirement_confirmation = current.get("requirement")
        requirement_confirmed = (
            isinstance(requirement_confirmation, dict)
            and requirement_confirmation.get("decision") == "confirmed"
        )
        discovery = (
            CopilotCodingTaskService(
                connection=self._connection,
                repository_root=self._root,
            ).document_discovery_for_request(record.change_request_id)
            if requirement_confirmed
            else {
                "status": "pending",
                "candidates": [],
                "blocking_reason": None,
            }
        )
        subjects = _checkpoint_subjects(
            request=request,
            diff=diff,
            workspace=workspace,
            bundle=bundle,
            execution=execution,
            rag_discovery=discovery,
        )
        current = self._automation_runs.current_confirmations(
            run_id=record.automation_run_id,
            subject_digests=subjects,
        )
        decisions = {
            checkpoint: str(confirmation["decision"])
            for checkpoint, confirmation in current.items()
        }
        return (
            decide_change_automation(
                request=request,
                diff=diff,
                workspace=workspace,
                has_orchestration=bundle is not None,
                execution=execution,
                confirmations=decisions,
                rag_discovery=discovery,
            ),
            discovery,
            subjects,
            current,
        )

    def _confirm_document_diff(
        self,
        *,
        record: ChangeAutomationRunRecord,
        confirmation_id: str,
        actor: str,
        note: str | None,
    ) -> None:
        self._repository.record_document_review(
            event_id=_web_id(
                "document-review",
                record.project_id,
                record.change_request_id,
                confirmation_id,
            ),
            request_id=record.change_request_id,
            project_id=record.project_id,
            decision="confirmed",
            actor=actor,
            note=note or "統一確認フローで設計書差分を確認",
        )

    def _confirm_impact(
        self,
        *,
        record: ChangeAutomationRunRecord,
        confirmation_id: str,
        actor: str,
        workspace: dict[str, object],
        note: str | None,
    ) -> None:
        report = cast_dict(workspace["impact_artifact"])
        report_id = str(report["impact_report_id"])
        case_id = str(workspace["analysis_case_id"])
        item_ids = [str(item["impact_item_id"]) for item in cast_list(report.get("items", []))]
        artifact = {
            "artifact_type": "ImpactConfirmation",
            "schema_version": "v1",
            "confirmation_id": _web_id(
                "impact-confirmation",
                record.project_id,
                case_id,
                report_id,
                confirmation_id,
            ),
            "impact_report_id": report_id,
            "confirmed_by": actor,
            "approved_item_ids": item_ids,
            "rejected_item_ids": [],
            "user_note": note or "統一確認フローでコード影響範囲を確認",
            "confirmed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        self._impacts.confirm(
            project_id=record.project_id,
            analysis_case_id=case_id,
            artifact=artifact,
        )

    def _sync_orchestration_task(
        self,
        *,
        record: ChangeAutomationRunRecord,
        decision: ChangeAutomationDecision,
        actor: str,
    ) -> dict[str, object] | None:
        definition = build_orchestration_task(
            automation_run_id=record.automation_run_id,
            change_request_id=record.change_request_id,
            project_id=record.project_id,
            decision=decision,
        )
        if definition is None:
            self._orchestration_tasks.complete_open_for_run(
                automation_run_id=record.automation_run_id, actor=actor
            )
            return None
        return self._orchestration_tasks.ensure_current(definition=definition, actor=actor)

    def _resume_latest_automation(self, *, request_id: str, actor: str) -> None:
        run = self._automation_runs.latest_for_request(request_id)
        if run is not None and run["status"] != "completed":
            self._advance_change_automation(
                run_id=str(run["automation_run_id"]), actor=actor, created=False
            )

    def resume_pending_change_automation(
        self, *, request_id: str, actor: str
    ) -> dict[str, object] | None:
        """Resume the internal workflow after an external Copilot MCP result."""

        self._repository.get_change_request(request_id)
        self._resume_latest_automation(request_id=request_id, actor=actor)
        run = self._automation_runs.latest_for_request(request_id)
        return self._decorate_automation(run) if run is not None else None

    def next_change_confirmation(self, *, workspace_root: Path) -> dict[str, object]:
        """Return the same pending confirmation shown by Web for one Workspace."""
        resolved = _resolved_local_directory(workspace_root, field_name="コード Workspace")
        project_id = self._repository.project_id_for_workspace(str(resolved))
        if project_id is None:
            return {"confirmation": None}
        run = self._automation_runs.latest_confirmation_for_project(project_id)
        if run is None:
            return {"confirmation": None}
        decorated = self._decorate_automation(run)
        return {"confirmation": decorated.get("pending_confirmation")}

    def _decorate_automation(self, run: dict[str, object]) -> dict[str, object]:
        value = dict(run)
        value["current_stage_label"] = STAGE_LABELS.get(
            str(run["current_stage"]), str(run["current_stage"])
        )
        event_stages = {
            str(event["stage"]): str(event["status"]) for event in cast_list(run.get("events", []))
        }
        order = tuple(STAGE_LABELS)
        current = str(run["current_stage"])
        current_index = order.index(current) if current in order else 0
        value["steps"] = [
            {
                "stage": stage,
                "label": label,
                "status": (
                    str(run["status"])
                    if stage == current
                    else event_stages.get(
                        stage, "completed" if index < current_index else "pending"
                    )
                ),
            }
            for index, (stage, label) in enumerate(STAGE_LABELS.items())
        ]
        tasks = self._orchestration_tasks.list_for_run(str(run["automation_run_id"]))
        value["orchestration_tasks"] = tasks
        value["current_task"] = next(
            (
                task
                for task in reversed(tasks)
                if task["state"]
                in {"ready", "claimed", "running", "submitted", "blocked", "failed"}
            ),
            None,
        )
        record = self._automation_runs.get(str(run["automation_run_id"]))
        request, diff, workspace, bundle, execution = self._automation_inputs(record)
        decision, discovery, subjects, confirmations = self._automation_decision(
            record=record,
            request=request,
            diff=diff,
            workspace=workspace,
            bundle=bundle,
            execution=execution,
        )
        value["confirmations"] = [confirmations[key] for key in confirmations]
        action_checkpoints = {
            "confirm_requirement": "requirement",
            "confirm_rag_documents": "rag_documents",
            "confirm_document_diff": "document_diff",
            "confirm_code_scope": "code_scope",
            "confirm_test_plan": "test_plan",
            "confirm_ui_test": "ui_test",
            "confirm_final_report": "final_report",
        }
        checkpoint = action_checkpoints.get(str(decision.next_action))
        value["pending_confirmation"] = (
            {
                "change_request_id": record.change_request_id,
                "automation_run_id": record.automation_run_id,
                "checkpoint": checkpoint,
                "stage": decision.stage,
                "stage_label": STAGE_LABELS.get(decision.stage, decision.stage),
                "message": decision.message,
                "subject_digest": subjects[checkpoint],
                "details": _confirmation_details(
                    checkpoint=checkpoint,
                    request=request,
                    diff=diff,
                    workspace=workspace,
                    execution=execution,
                    rag_discovery=discovery,
                ),
            }
            if checkpoint is not None and checkpoint in subjects
            else None
        )
        return value

    def execution_management(self, request_id: str) -> dict[str, object]:
        """Build one request-scoped read model for Test Data and final closure."""
        request = self._repository.get_change_request(request_id)
        bundle = self._orchestrations.latest_bundle(request_id)
        if bundle is None:
            controls = {
                "can_start": False,
                "can_recover": False,
                "can_rerun": False,
                "is_revised_version": False,
                "cleanup_mode": "automatic",
                "blocking_reason": "TestDataPlan is missing",
            }
            return {
                "change_request_id": request_id,
                "project_id": request["project_id"],
                "analysis_case_id": request["analysis_case_id"],
                "orchestration_id": None,
                "test_plan": None,
                "test_data_plan": None,
                "test_data_execution": None,
                "business_coverage": None,
                "changed_line_coverage": None,
                "change_closure": None,
                "screenshots": [],
                "stale_history": self._test_case_revisions.state(request_id)["history"],
                "failure_management": build_failure_management(
                    test_data_plan=None,
                    test_data_execution=None,
                    ui_result=None,
                    coverage=None,
                    closure=None,
                    controls=controls,
                ),
                "controls": controls,
            }
        orchestration = cast_dict(bundle["orchestration"])
        orchestration_id = str(orchestration["orchestration_id"])
        closure = self._closures.latest_for_orchestration(orchestration_id)
        execution = self._test_data_runs.latest_for_orchestration(orchestration_id)
        project_id = str(orchestration["project_id"])
        case_id = str(orchestration["analysis_case_id"])
        coverage_loader = getattr(self._closures, "latest_changed_line_coverage", None)
        changed_line_coverage = (
            coverage_loader(
                project_id=project_id,
                analysis_case_id=case_id,
                orchestration_id=orchestration_id,
            )
            if callable(coverage_loader)
            else None
        )
        closure_stale = closure is not None and not _closure_matches_edit_result(
            closure, changed_line_coverage
        )
        if closure_stale:
            closure = None
        revision_state = self._test_case_revisions.state(request_id)
        current_revision = next(
            (
                value
                for value in cast_list(revision_state["history"])
                if value.get("status") == "current"
            ),
            None,
        )
        screenshots = self._screenshot_items(
            request_id=request_id,
            execution=execution,
        )
        authorization = self._case_execution_authorizations.state(
            target_orchestration_id=orchestration_id,
            at=datetime.now(UTC),
        )
        authorization_error = cast(str | None, authorization["blocking_reason"])
        if authorization_error is None and closure_stale:
            authorization_error = "Change Closure is stale for current Edit Result"
        running = execution is not None and execution["status"] == "running"
        recoverable = running and _execution_is_stale(execution, datetime.now(UTC))
        version_comparison = self._version_comparison(
            authorization=authorization,
            target_bundle=bundle,
            target_execution=execution,
            target_closure=closure,
        )
        controls = {
            "can_start": authorization["authorized"] is True and execution is None,
            "can_recover": recoverable,
            "can_rerun": (
                authorization["authorized"] is True and execution is not None and not running
            ),
            "is_revised_version": current_revision is not None,
            "requires_scope_confirmation": (authorization["status"] == "confirmation_required"),
            "cleanup_mode": "automatic",
            "approval_grant_id": authorization["approval_grant_id"],
            "blocking_reason": authorization_error,
        }
        ui_result = self._ui_result_for_closure(closure)
        return {
            "change_request_id": request_id,
            "project_id": project_id,
            "analysis_case_id": case_id,
            "orchestration_id": orchestration_id,
            "test_plan": bundle["test_plan"],
            "test_data_plan": bundle["test_data_plan"],
            "test_data_execution": execution,
            "business_coverage": bundle["coverage_report"],
            "changed_line_coverage": changed_line_coverage,
            "change_closure": closure,
            "screenshots": screenshots,
            "stale_history": revision_state["history"],
            "execution_authorization": authorization,
            "version_result_comparison": version_comparison,
            "failure_management": build_failure_management(
                test_data_plan=cast(dict[str, Any], bundle["test_data_plan"]),
                test_data_execution=execution,
                ui_result=ui_result,
                coverage=cast(dict[str, Any], bundle["coverage_report"]),
                closure=closure,
                controls=controls,
            ),
            "controls": controls,
        }

    def _ui_result_for_closure(self, closure: dict[str, Any] | None) -> dict[str, Any] | None:
        if closure is None:
            return None
        for reference in cast(list[object], closure.get("artifact_refs", [])):
            artifact = self._artifacts.get(str(reference))
            if artifact is not None and artifact.get("artifact_type") == "UiVerificationResult":
                return artifact
        if closure.get("ui_status") not in {None, "not_impacted"}:
            return {
                "status": "blocked",
                "scenario_results": [],
                "failure_reasons": [
                    "ChangeClosureResult has no canonical UiVerificationResult reference"
                ],
            }
        return None

    def _version_comparison(
        self,
        *,
        authorization: dict[str, Any],
        target_bundle: dict[str, Any],
        target_execution: dict[str, Any] | None,
        target_closure: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        source_id = authorization.get("source_orchestration_id")
        if source_id is None:
            return None
        source_bundle = self._orchestrations.bundle(str(source_id))
        return compare_test_case_version_results(
            source_orchestration_id=str(source_id),
            target_orchestration_id=str(
                cast_dict(target_bundle["orchestration"])["orchestration_id"]
            ),
            source_run=self._test_data_runs.latest_for_orchestration(str(source_id)),
            target_run=target_execution,
            source_closure=self._closures.latest_for_orchestration(str(source_id)),
            target_closure=target_closure,
            source_coverage=cast_dict(source_bundle["coverage_report"]),
            target_coverage=cast_dict(target_bundle["coverage_report"]),
        )

    def start_test_data_run(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        actor: str,
        replay_of_run_id: str | None = None,
    ) -> dict[str, object]:
        bundle = self._orchestrations.latest_bundle(request_id)
        if bundle is None:
            raise ValueError("Change Orchestration does not exist")
        orchestration = cast_dict(bundle["orchestration"])
        project_id = str(orchestration["project_id"])
        orchestration_id = str(orchestration["orchestration_id"])
        plan = cast_dict(bundle["test_data_plan"])
        run_id = _web_id(
            "test-data-run",
            project_id,
            request_id,
            orchestration_id,
            idempotency_key,
        )
        result_id = _web_id("test-data-result", project_id, run_id)
        latest = self._test_data_runs.latest_for_orchestration(orchestration_id)
        if latest is not None and latest["run_id"] == run_id:
            return {
                "created": False,
                "run_id": run_id,
                "execution_result_id": result_id,
                "status": latest["status"],
                "replay_of_run_id": latest.get("replay_of_run_id"),
                "background_required": False,
            }
        if latest is not None and latest["status"] == "running":
            raise ValueError("A Test data Run is already running")
        if replay_of_run_id is None and latest is not None:
            raise ValueError("A completed Test data Run must be rerun explicitly")
        scope = self._test_data_runs.latest_active_scope(
            orchestration_id=orchestration_id,
            project_id=project_id,
            at=datetime.now(UTC),
        )
        reservation = self._test_data_runs.reserve(
            TestDataExecutionRunWrite(
                run_id=run_id,
                execution_result_id=result_id,
                orchestration_id=orchestration_id,
                test_data_plan_id=str(plan["test_data_plan_id"]),
                approval_grant_id=str(scope["approval_grant_id"]),
                project_id=project_id,
                created_by=actor,
                started_at=datetime.now(UTC),
                replay_of_run_id=replay_of_run_id,
            )
        )
        if reservation.created:
            self._test_data_runs.append_event(
                TestDataExecutionEventWrite(
                    run_id=run_id,
                    project_id=project_id,
                    event_type="reserved",
                    status="running",
                    message="Internal coordinator reserved the approved TestDataPlan Run.",
                )
            )
        return {
            "created": reservation.created,
            "run_id": run_id,
            "execution_result_id": result_id,
            "status": reservation.record.status,
            "replay_of_run_id": replay_of_run_id,
            "background_required": reservation.created,
        }

    def screenshot_path(self, *, request_id: str, evidence_id: str) -> Path:
        management = self.execution_management(request_id)
        for item in cast_list(management["screenshots"]):
            if item.get("evidence_id") == evidence_id:
                path = self._local_evidence_path(str(item["evidence_ref"]))
                if path is not None:
                    return path
        raise ValueError("Screenshot evidence does not exist")

    def _screenshot_items(
        self,
        *,
        request_id: str,
        execution: dict[str, Any] | None,
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        result = execution.get("result") if execution is not None else None
        if isinstance(result, dict):
            for value in cast_list(result.get("evidence", [])):
                if value.get("evidence_type") != "screenshot":
                    continue
                items.append(
                    self._screenshot_item(
                        request_id=request_id,
                        evidence_id=str(value["evidence_id"]),
                        evidence_ref=str(value["evidence_ref"]),
                        digest=str(value["content_digest"]),
                        context={
                            "flow_id": value.get("flow_id"),
                            "step_id": value.get("step_id"),
                            "phase": value.get("phase"),
                        },
                    )
                )
        return items

    def _screenshot_item(
        self,
        *,
        request_id: str,
        evidence_id: str,
        evidence_ref: str,
        digest: str,
        context: dict[str, object],
    ) -> dict[str, object]:
        available = self._local_evidence_path(evidence_ref) is not None
        return {
            "evidence_id": evidence_id,
            "evidence_ref": evidence_ref,
            "sha256": digest,
            "available": available,
            "content_url": (
                "/api/v1/change-requests/"
                f"{quote(request_id, safe='')}/screenshots/{quote(evidence_id, safe='')}"
                if available
                else None
            ),
            **context,
        }

    def _local_evidence_path(self, evidence_ref: str) -> Path | None:
        if not evidence_ref.strip():
            return None
        if evidence_ref.startswith("evidence://"):
            parsed = urlsplit(evidence_ref)
            components = [parsed.netloc, *parsed.path.strip("/").split("/")]
            if len(components) != 3 or any(
                not value or value in {".", ".."} or "/" in value for value in components
            ):
                return None
            root = (self._root / "readiness" / "evidence" / "test-data").resolve()
            base = (root / components[0] / components[1]).resolve()
            if not base.is_relative_to(root):
                return None
            for suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                candidate = (base / f"{components[2]}{suffix}").resolve()
                if candidate.is_relative_to(root) and candidate.is_file():
                    return candidate
            return None
        if "://" in evidence_ref:
            return None
        value = Path(evidence_ref)
        candidate = value if value.is_absolute() else self._root / value
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._root):
            return None
        if resolved.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            return None
        return resolved if resolved.is_file() else None


def _closure_matches_edit_result(
    closure: dict[str, Any], changed_line_coverage: dict[str, Any] | None
) -> bool:
    if changed_line_coverage is None:
        return "Committed Edit Result is missing" in {
            str(value) for value in cast(list[object], closure.get("unresolved_items", []))
        }
    edit_result_id = str(changed_line_coverage.get("edit_result_id") or "")
    return bool(edit_result_id) and edit_result_id in {
        str(value) for value in cast(list[object], closure.get("artifact_refs", []))
    }


def _public_test_case_proposal(proposal: dict[str, Any]) -> dict[str, object]:
    def operation(value: dict[str, Any]) -> dict[str, object]:
        return {
            "case_title": value["case_title"],
            "field": value["field"],
            "action": value["action"],
            "summary_before": value["summary_before"],
            "summary_after": value["summary_after"],
        }

    return {
        "proposal_id": proposal["proposal_id"],
        "instruction": proposal["instruction"],
        "analysis_status": proposal["analysis_status"],
        "operations": [
            operation(value) for value in cast(list[dict[str, Any]], proposal["operations"])
        ],
        "ambiguities": [
            {
                "ambiguity_id": ambiguity["ambiguity_id"],
                "question": ambiguity["question"],
                "options": [
                    {
                        "option_id": option["option_id"],
                        "label": option["label"],
                        "operations": [
                            operation(value)
                            for value in cast(list[dict[str, Any]], option["operations"])
                        ],
                    }
                    for option in cast(list[dict[str, Any]], ambiguity["options"])
                ],
            }
            for ambiguity in cast(list[dict[str, Any]], proposal["ambiguities"])
        ],
        "blocking_reasons": list(cast(list[str], proposal["blocking_reasons"])),
    }


def _resolved_local_directory(value: Path, *, field_name: str) -> Path:
    if not value.is_absolute():
        raise ValueError(f"{field_name}には絶対パスを入力してください")
    try:
        resolved = value.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(
            f"{field_name}が存在しないか、読み取ることができません: {value}"
        ) from error
    if not resolved.is_dir():
        raise ValueError(f"{field_name}にはフォルダーを指定してください: {value}")
    return resolved


def _checkpoint_subjects(
    *,
    request: dict[str, object],
    diff: dict[str, object],
    workspace: dict[str, object] | None,
    bundle: dict[str, object] | None,
    execution: dict[str, object] | None,
    rag_discovery: dict[str, object] | None,
) -> dict[str, str]:
    """Bind every confirmation to the exact evidence visible at that step."""
    subjects: dict[str, object] = {"requirement": cast_dict(request["artifact"])}
    if rag_discovery is not None and rag_discovery.get("status") == "ready":
        subjects["rag_documents"] = rag_discovery
    diff_total = diff.get("total")
    if isinstance(diff_total, int) and diff_total > 0:
        subjects["document_diff"] = {
            "change_request_id": request.get("change_request_id"),
            "changes": diff.get("changes", []),
        }
    impact_value = workspace.get("impact_artifact") if workspace is not None else None
    impact = impact_value if isinstance(impact_value, dict) else {}
    if impact.get("impact_report_id") is not None:
        subjects["code_scope"] = impact
    management = execution or {}
    test_plan = management.get("test_plan")
    if bundle is not None and isinstance(test_plan, dict) and test_plan.get("status") == "ready":
        subjects["test_plan"] = {
            "test_plan": test_plan,
            "test_data_plan": management.get("test_data_plan"),
            "business_coverage": management.get("business_coverage"),
            "changed_line_coverage": management.get("changed_line_coverage"),
        }
    if bundle is not None and isinstance(test_plan, dict) and test_plan.get("status") == "ready":
        subjects["ui_test"] = {
            "test_data_plan": management.get("test_data_plan"),
            "test_plan": test_plan,
        }
    closure = management.get("change_closure")
    if isinstance(closure, dict) and closure.get("status") == "passed":
        subjects["final_report"] = {
            "change_closure": closure,
            "business_coverage": management.get("business_coverage"),
            "changed_line_coverage": management.get("changed_line_coverage"),
            "screenshots": management.get("screenshots", []),
        }
    return {
        checkpoint: hashlib.sha256(
            json.dumps(
                subject,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        for checkpoint, subject in subjects.items()
    }


def _confirmation_details(
    *,
    checkpoint: str,
    request: dict[str, object],
    diff: dict[str, object],
    workspace: dict[str, object] | None,
    execution: dict[str, object] | None,
    rag_discovery: dict[str, object],
) -> dict[str, object]:
    if checkpoint == "requirement":
        artifact = cast_dict(request["artifact"])
        return {
            "requirement_text": artifact.get("requirement_text"),
            "business_rules": artifact.get("business_rules", []),
            "ambiguities": artifact.get("ambiguities", []),
        }
    if checkpoint == "rag_documents":
        return rag_discovery
    if checkpoint == "document_diff":
        return {"changes": diff.get("changes", []), "total": diff.get("total", 0)}
    if checkpoint == "code_scope":
        impact = cast_dict(workspace.get("impact_artifact")) if workspace is not None else {}
        return {
            "ui_impact_status": impact.get("ui_impact_status"),
            "blocking_unknowns": impact.get("blocking_unknowns", []),
            "items": [
                {
                    key: item[key]
                    for key in (
                        "target_path",
                        "target_symbols",
                        "recommended_action",
                        "test_file_refs",
                        "rationale",
                        "unknowns",
                    )
                    if key in item
                }
                for item in cast_list(impact.get("items", []))
            ],
        }
    management = execution or {}
    if checkpoint == "test_plan":
        return {
            "test_plan": management.get("test_plan"),
            "test_data_plan": management.get("test_data_plan"),
            "business_coverage": management.get("business_coverage"),
            "changed_line_coverage": management.get("changed_line_coverage"),
        }
    if checkpoint == "ui_test":
        return {
            "test_data_plan": management.get("test_data_plan"),
            "test_plan": management.get("test_plan"),
        }
    if checkpoint == "final_report":
        return {
            "change_closure": management.get("change_closure"),
            "business_coverage": management.get("business_coverage"),
            "changed_line_coverage": management.get("changed_line_coverage"),
            "screenshots": management.get("screenshots", []),
        }
    raise ValueError(f"Unknown change confirmation checkpoint: {checkpoint}")


def _web_id(prefix: str, *values: str) -> str:
    if any(not value.strip() for value in values):
        raise ValueError("Idempotency identity values must not be blank")
    digest = hashlib.sha256("\0".join(values).encode()).hexdigest()[:24]
    return f"web-{prefix}-{digest}"


def _execution_is_stale(execution: dict[str, Any] | None, now: datetime) -> bool:
    if execution is None or execution.get("status") != "running":
        return False
    timestamps = [str(execution["started_at"])]
    timestamps.extend(
        str(event["created_at"])
        for event in cast_list(execution.get("events", []))
        if event.get("created_at") is not None
    )
    latest = max(datetime.fromisoformat(value.replace("Z", "+00:00")) for value in timestamps)
    return latest <= now - timedelta(seconds=30)


def cast_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("Web progress read model lost its object shape")
    return value


def cast_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError("Web execution read model lost its array shape")
    return value
