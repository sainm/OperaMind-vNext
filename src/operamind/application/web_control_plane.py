"""Trusted Web control-plane use cases over existing Canonical services."""

from __future__ import annotations

import hashlib
import json
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
from operamind.application.change_closure_service import ChangeClosureService
from operamind.application.change_orchestration_service import ChangeOrchestrationService
from operamind.application.copilot_coding_task import (
    CopilotCodingTaskPublishRequest,
    CopilotCodingTaskService,
)
from operamind.application.failure_management import build_failure_management
from operamind.application.orchestration_task import (
    OrchestrationSchedulingPolicy,
    build_orchestration_task,
)
from operamind.application.test_case_revision_service import TestCaseRevisionService
from operamind.application.test_data_execution_service import (
    TestDataExecutionRecoveryRequest,
    TestDataExecutionService,
)
from operamind.application.ui_knowledge_review import (
    UiKnowledgeReviewRequest,
    UiKnowledgeReviewService,
)
from operamind.contracts import ContractCatalog
from operamind.domain.test_case_execution_scope import (
    compare_test_case_version_results,
)
from operamind.infrastructure.postgres import (
    ApprovalGrantRepository,
    ArtifactRepository,
    ChangeAutomationRepository,
    ChangeAutomationRunRecord,
    ChangeClosureRepository,
    ChangeOrchestrationRepository,
    ImpactRepository,
    OrchestrationTaskRepository,
    PersistenceConflictError,
    TestCaseExecutionAuthorizationRepository,
    TestDataExecutionEventWrite,
    TestDataExecutionRepository,
    TestDataExecutionRunWrite,
    UiKnowledgeReviewQueryRepository,
    UnresolvedEvidenceRepository,
    WebControlPlaneRepository,
)
from operamind.profiles import ProfileCatalog
from operamind.readiness import MvpReadinessValidator


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
class ImpactDecisionInput:
    report_id: str
    approved_item_ids: tuple[str, ...]
    rejected_item_ids: tuple[str, ...]
    note: str
    actor: str


@dataclass(frozen=True, slots=True)
class GrantInput:
    edit_packet_id: str
    expires_at: datetime
    command_profile_binding_key: str
    test_command_refs: tuple[str, ...]
    actor: str


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
        self._artifacts = ArtifactRepository(connection, self._contracts)
        self._impacts = ImpactRepository(connection, self._contracts)
        self._grants = ApprovalGrantRepository(connection, self._contracts)
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
        self._orchestration_tasks = OrchestrationTaskRepository(
            connection, orchestration_scheduling_policy
        )
        self._closure_service = ChangeClosureService(connection, self._contracts)
        self._test_case_revisions = TestCaseRevisionService(
            connection=connection,
            repository_root=self._root,
        )
        self._case_execution_authorizations = TestCaseExecutionAuthorizationRepository(
            connection, self._contracts
        )
        self._ui_knowledge_reviews = UiKnowledgeReviewQueryRepository(connection)
        self._unresolved_evidence = UnresolvedEvidenceRepository(connection, self._contracts)
        self._ui_knowledge_review_service = UiKnowledgeReviewService(connection=connection)

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
        record = self._repository.submit_change_request(
            artifact=artifact,
            analysis_case_id=value.analysis_case_id,
            submitted_by=value.submitted_by,
        )
        return {
            "created": record.created,
            "change_request": self._repository.get_change_request(record.change_request_id),
        }

    def list_projects(self) -> dict[str, object]:
        projects = self._repository.list_projects()
        return {"projects": list(projects), "count": len(projects)}

    def unresolved_evidence_management(
        self, *, project_id: str, history_limit: int = 50
    ) -> dict[str, object]:
        """Return current unresolved findings plus immutable report history."""

        return self._unresolved_evidence.management_view(
            project_id=project_id,
            history_limit=history_limit,
        )

    def list_change_requests(self, *, project_id: str) -> dict[str, object]:
        requests = self._repository.list_change_requests(project_id=project_id)
        return {"change_requests": list(requests), "count": len(requests)}

    def ui_knowledge_review_queue(self, *, project_id: str) -> dict[str, object]:
        queue = self._ui_knowledge_reviews.review_queue(project_id=project_id)
        for draft in cast_list(queue["drafts"]):
            snapshot_id = str(draft["snapshot_id"])
            for target in cast_list(draft["targets"]):
                evidence = target.get("evidence")
                if not isinstance(evidence, dict):
                    continue
                evidence_ref = str(evidence["evidence_ref"])
                available = self._local_evidence_path(evidence_ref) is not None
                evidence["available"] = available
                evidence["content_url"] = (
                    "/api/v1/projects/"
                    f"{quote(project_id, safe='')}/ui-knowledge/reviews/"
                    f"{quote(snapshot_id, safe='')}/screenshots/"
                    f"{quote(str(evidence['evidence_id']), safe='')}"
                    if available
                    else None
                )
        return queue

    def review_ui_knowledge(
        self,
        *,
        project_id: str,
        source_snapshot_id: str,
        result_snapshot_version: str,
        decision: str,
        reason: str,
        activate: bool,
        idempotency_key: str,
        actor: str,
    ) -> dict[str, object]:
        identity = _web_id(
            "ui-knowledge-review",
            project_id,
            source_snapshot_id,
            result_snapshot_version,
            decision,
            idempotency_key,
        )
        result_snapshot_id = _web_id(
            "ui-knowledge-snapshot",
            project_id,
            source_snapshot_id,
            result_snapshot_version,
            decision,
            idempotency_key,
        )
        reviewed = self._ui_knowledge_review_service.review(
            UiKnowledgeReviewRequest(
                project_id=project_id,
                source_snapshot_id=source_snapshot_id,
                result_snapshot_id=result_snapshot_id,
                result_snapshot_version=result_snapshot_version,
                review_event_id=identity,
                decision=decision,
                reviewed_by=actor,
                activate=activate,
                reason=reason,
            )
        )
        return {
            "created": reviewed.record.created,
            "review_event_id": reviewed.record.review_event_id,
            "source_snapshot_id": reviewed.record.source_snapshot_id,
            "result_snapshot_id": reviewed.record.result_snapshot_id,
            "result_snapshot_version": reviewed.snapshot.snapshot_version,
            "decision": reviewed.record.decision,
            "active": reviewed.record.active,
            "reviewed_by": actor,
            "reason": reason,
        }

    def ui_knowledge_screenshot_path(
        self, *, project_id: str, snapshot_id: str, evidence_id: str
    ) -> Path:
        evidence = self._ui_knowledge_reviews.evidence(
            project_id=project_id,
            snapshot_id=snapshot_id,
            evidence_id=evidence_id,
        )
        if evidence["sanitized"] is not True:
            raise ValueError("UI Knowledge review Evidence is not sanitized")
        path = self._local_evidence_path(str(evidence["evidence_ref"]))
        if path is None:
            raise ValueError("UI Knowledge review screenshot does not exist")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != evidence["content_digest"]:
            raise PersistenceConflictError("UI Knowledge review screenshot digest differs")
        return path

    def get_change_request(self, request_id: str) -> dict[str, object]:
        return self._repository.get_change_request(request_id)

    def publish_copilot_task(
        self,
        *,
        request_id: str,
        project_id: str,
        edit_packet_id: str,
        approval_grant_id: str,
        workspace_root: Path,
        task_summary: str,
        idempotency_key: str,
        actor: str,
    ) -> dict[str, object]:
        task_id = _web_id("copilot-coding-task", project_id, request_id, idempotency_key)
        return CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).publish(
            CopilotCodingTaskPublishRequest(
                coding_task_id=task_id,
                change_request_id=request_id,
                project_id=project_id,
                edit_packet_id=edit_packet_id,
                approval_grant_id=approval_grant_id,
                workspace_root=workspace_root,
                task_summary=task_summary,
                actor=actor,
                idempotency_key=idempotency_key,
            )
        )

    def copilot_task(self, request_id: str) -> dict[str, object]:
        self._repository.get_change_request(request_id)
        task = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).latest_for_request(request_id)
        return {"task": task}

    def cancel_copilot_task(
        self,
        *,
        request_id: str,
        coding_task_id: str,
        reason: str,
        idempotency_key: str,
        actor: str,
    ) -> dict[str, object]:
        return CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).cancel(
            coding_task_id=coding_task_id,
            change_request_id=request_id,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def retry_copilot_task(
        self,
        *,
        request_id: str,
        coding_task_id: str,
        idempotency_key: str,
        actor: str,
        edit_packet_id: str,
        approval_grant_id: str,
        workspace_root: Path,
    ) -> dict[str, object]:
        retry_id = _web_id("copilot-coding-task-retry", request_id, coding_task_id, idempotency_key)
        return CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).retry(
            coding_task_id=coding_task_id,
            retry_coding_task_id=retry_id,
            change_request_id=request_id,
            actor=actor,
            idempotency_key=idempotency_key,
            edit_packet_id=edit_packet_id,
            approval_grant_id=approval_grant_id,
            workspace_root=workspace_root,
        )

    def claim_copilot_task(self, *, workspace_root: Path, consumer_id: str) -> dict[str, object]:
        task = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).claim_next(workspace_root=workspace_root, consumer_id=consumer_id)
        return {"task": task}

    def accept_copilot_task(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        consumer_id: str,
        actor: str,
    ) -> dict[str, object]:
        return CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).accept(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            consumer_id=consumer_id,
            actor=actor,
        )

    def resume_copilot_task(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        consumer_id: str,
    ) -> dict[str, object]:
        return CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._root,
        ).resume(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            consumer_id=consumer_id,
        )

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
        return task.cancel(
            coding_task_id=coding_task_id,
            change_request_id=str(artifact["change_request_id"]),
            actor=actor,
            reason=reason,
            idempotency_key=f"bridge:{consumer_id}",
            consumer_id=consumer_id,
        )

    def orchestrate_change_request(self, *, request_id: str, actor: str) -> dict[str, object]:
        result = self._orchestration_service.orchestrate(change_request_id=request_id, actor=actor)
        return {
            "created": result.created,
            "bundle": self._orchestrations.latest_bundle(request_id),
        }

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

    def resume_change_automation(
        self, *, request_id: str, run_id: str, actor: str
    ) -> dict[str, object]:
        record = self._automation_runs.get(run_id)
        if record.change_request_id != request_id:
            raise ValueError("Change Automation Run does not belong to Change Request")
        return self._advance_change_automation(run_id=run_id, actor=actor, created=False)

    def change_automation(self, request_id: str) -> dict[str, object]:
        self._repository.get_change_request(request_id)
        run = self._automation_runs.latest_for_request(request_id)
        return {"run": self._decorate_automation(run) if run is not None else None}

    def orchestration_tasks(self, run_id: str) -> dict[str, object]:
        self._automation_runs.get(run_id)
        return {"tasks": self._orchestration_tasks.list_for_run(run_id)}

    def orchestration_task(self, task_id: str) -> dict[str, object]:
        return {"task": self._orchestration_tasks.view(task_id)}

    def orchestration_task_management(
        self,
        *,
        project_id: str | None,
        states: tuple[str, ...],
        capability: str | None,
        blocking_reason: str | None,
        limit: int,
    ) -> dict[str, object]:
        tasks = self._orchestration_tasks.list_management(
            project_id=project_id,
            states=states,
            capability=capability,
            blocking_reason=blocking_reason,
            limit=limit,
        )
        return {"tasks": tasks, "count": len(tasks)}

    def orchestration_task_dependency_graph(
        self,
        *,
        project_id: str | None,
        automation_run_id: str | None,
        limit: int,
    ) -> dict[str, object]:
        return self._orchestration_tasks.dependency_graph(
            project_id=project_id,
            automation_run_id=automation_run_id,
            limit=limit,
        )

    def orchestration_task_runtime_monitoring(
        self, *, project_id: str | None, window_hours: int
    ) -> dict[str, object]:
        return self._orchestration_tasks.runtime_monitoring(
            project_id=project_id,
            window_hours=window_hours,
        )

    def orchestration_workers(self, *, project_id: str | None) -> dict[str, object]:
        workers = self._orchestration_tasks.list_worker_registrations(
            project_id=project_id
        )
        return {"workers": workers, "count": len(workers), "project_id": project_id}

    def update_orchestration_worker_configuration(
        self,
        *,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        max_concurrent_tasks: int,
        actor: str,
    ) -> dict[str, object]:
        return {
            "worker": self._orchestration_tasks.update_worker_configuration(
                executor_kind=executor_kind,
                executor_id=executor_id,
                capabilities=capabilities,
                max_concurrent_tasks=max_concurrent_tasks,
                actor=actor,
            )
        }

    def operate_orchestration_worker(
        self,
        *,
        executor_kind: str,
        executor_id: str,
        operation: str,
        actor: str,
    ) -> dict[str, object]:
        statuses = {"enable": "online", "disable": "offline", "drain": "draining"}
        if operation not in statuses:
            raise ValueError("Worker operation must be enable, disable, or drain")
        return {
            "worker": self._orchestration_tasks.set_worker_status(
                executor_kind=executor_kind,
                executor_id=executor_id,
                status=statuses[operation],
                actor=actor,
            )
        }

    def ready_orchestration_tasks(
        self,
        *,
        executor_kind: str,
        capabilities: tuple[str, ...],
        project_id: str | None,
    ) -> dict[str, object]:
        return {
            "tasks": self._orchestration_tasks.list_ready(
                executor_kind=executor_kind,
                capabilities=capabilities,
                project_id=project_id,
            )
        }

    def claim_orchestration_task(
        self,
        *,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        project_id: str | None,
        worker_token: str | None = None,
    ) -> dict[str, object]:
        return {
            "task": self._orchestration_tasks.claim_next(
                executor_kind=executor_kind,
                executor_id=executor_id,
                capabilities=capabilities,
                project_id=project_id,
                worker_token=worker_token,
            )
        }

    def claim_selected_orchestration_task(
        self,
        *,
        task_id: str,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        project_id: str | None,
        worker_token: str | None = None,
    ) -> dict[str, object]:
        return {
            "task": self._orchestration_tasks.claim(
                task_id=task_id,
                executor_kind=executor_kind,
                executor_id=executor_id,
                capabilities=capabilities,
                project_id=project_id,
                worker_token=worker_token,
            )
        }

    def heartbeat_orchestration_task(
        self, *, task_id: str, executor_id: str, lease_token: str
    ) -> dict[str, object]:
        return {
            "task": self._orchestration_tasks.heartbeat(
                task_id=task_id, executor_id=executor_id, lease_token=lease_token
            )
        }

    def release_orchestration_task(
        self, *, task_id: str, executor_id: str, lease_token: str, reason: str
    ) -> dict[str, object]:
        return {
            "task": self._orchestration_tasks.release(
                task_id=task_id,
                executor_id=executor_id,
                lease_token=lease_token,
                reason=reason,
            )
        }

    def complete_orchestration_task(
        self,
        *,
        task_id: str,
        executor_id: str,
        lease_token: str,
        outcome: str,
        summary: str,
        artifact_refs: tuple[str, ...],
        evidence: dict[str, object],
    ) -> dict[str, object]:
        return {
            "task": self._orchestration_tasks.record_result(
                task_id=task_id,
                executor_id=executor_id,
                lease_token=lease_token,
                outcome=outcome,
                summary=summary,
                artifact_refs=artifact_refs,
                evidence=evidence,
            )
        }

    def requeue_orchestration_task(
        self, *, task_id: str, actor: str, reason: str
    ) -> dict[str, object]:
        return {
            "task": self._orchestration_tasks.requeue(
                task_id=task_id, actor=actor, reason=reason
            )
        }

    def update_orchestration_task_priority(
        self, *, task_id: str, priority: int, actor: str
    ) -> dict[str, object]:
        return {
            "task": self._orchestration_tasks.update_priority(
                task_id=task_id, priority=priority, actor=actor
            )
        }

    def bind_change_request_case(
        self,
        *,
        request_id: str,
        project_id: str,
        case_id: str,
        idempotency_key: str,
        actor: str,
    ) -> dict[str, object]:
        event_id = _web_id("change-case-binding", project_id, request_id, case_id, idempotency_key)
        created = self._repository.bind_analysis_case(
            event_id=event_id,
            request_id=request_id,
            project_id=project_id,
            case_id=case_id,
            idempotency_key=idempotency_key,
            actor=actor,
        )
        self._resume_latest_automation(request_id=request_id, actor=actor)
        return {
            "created": created,
            "binding_event_id": event_id,
            "change_request_id": request_id,
            "analysis_case_id": case_id,
        }

    def _advance_change_automation(
        self, *, run_id: str, actor: str, created: bool
    ) -> dict[str, object]:
        record = self._automation_runs.get(run_id)
        request = self._repository.get_change_request(record.change_request_id)
        diff = self._repository.document_diff(record.change_request_id)
        case_id = request["analysis_case_id"]
        workspace: dict[str, object] | None = None
        if case_id is not None:
            workspace = self._repository.case_workspace(
                project_id=record.project_id, case_id=str(case_id)
            )
        bundle = self._orchestrations.latest_bundle(record.change_request_id)
        execution = (
            self.execution_management(record.change_request_id) if bundle is not None else None
        )
        decision = decide_change_automation(
            request=request,
            diff=diff,
            workspace=workspace,
            has_orchestration=bundle is not None,
            execution=execution,
        )
        current_task = self._sync_orchestration_task(
            record=record, decision=decision, actor=actor
        )
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
                "operamind-single-agent:"
                f"{hashlib.sha256(run_id.encode()).hexdigest()[:24]}"
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
            except (ValueError, RuntimeError) as error:
                self._orchestration_tasks.record_result(
                    task_id=str(current_task["orchestration_task_id"]),
                    executor_id=internal_executor_id,
                    lease_token=lease_token,
                    outcome="blocked",
                    summary="自動編成を完了できませんでした。",
                    artifact_refs=(),
                    evidence={"blocking_reason": str(error)},
                )
                decision = type(decision)(
                    stage="planning",
                    status="blocked",
                    next_action="resolve_blocker",
                    blocking_reason=str(error),
                    message=f"自動編成を完了できませんでした: {error}",
                )
            else:
                bundle = self._orchestrations.latest_bundle(record.change_request_id)
                decision = decide_change_automation(
                    request=request,
                    diff=diff,
                    workspace=workspace,
                    has_orchestration=True,
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
        return value

    def change_orchestration(self, request_id: str) -> dict[str, object]:
        return {"bundle": self._orchestrations.latest_bundle(request_id)}

    def modify_test_case(
        self, *, request_id: str, instruction: str, actor: str
    ) -> dict[str, object]:
        return self._test_case_revisions.propose(
            change_request_id=request_id,
            instruction=instruction,
            actor=actor,
        )

    def confirm_test_case_modification(
        self,
        *,
        request_id: str,
        proposal_id: str,
        selections: dict[str, str],
        actor: str,
    ) -> dict[str, object]:
        return self._test_case_revisions.confirm(
            change_request_id=request_id,
            proposal_id=proposal_id,
            selections=selections,
            actor=actor,
        )

    def test_case_modification_state(self, request_id: str) -> dict[str, object]:
        return self._test_case_revisions.state(request_id)

    def undo_test_case_revision(
        self,
        *,
        request_id: str,
        revision_id: str,
        idempotency_key: str,
        actor: str,
    ) -> dict[str, object]:
        return self._test_case_revisions.undo(
            change_request_id=request_id,
            revision_id=revision_id,
            idempotency_key=idempotency_key,
            actor=actor,
        )

    def confirm_test_case_execution_scope(
        self,
        *,
        request_id: str,
        approval_grant_id: str,
        target_scope_digest: str,
        actor: str,
    ) -> dict[str, object]:
        bundle = self._orchestrations.latest_bundle(request_id)
        if bundle is None:
            raise ValueError("Change Orchestration does not exist")
        orchestration = cast_dict(bundle["orchestration"])
        record = self._case_execution_authorizations.confirm(
            target_orchestration_id=str(orchestration["orchestration_id"]),
            approval_grant_id=approval_grant_id,
            target_scope_digest=target_scope_digest,
            actor=actor,
            at=datetime.now(UTC),
        )
        return {
            "created": record.created,
            "authorization_id": record.authorization_id,
            "approval_grant_id": record.approval_grant_id,
            "decision": record.decision,
            "confirmed_by": record.confirmed_by,
            "created_at": record.created_at.isoformat(),
        }

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
                "test_data_plan": None,
                "test_data_execution": None,
                "business_coverage": None,
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
        revision_state = self._test_case_revisions.state(request_id)
        current_revision = next(
            (
                value
                for value in cast_list(revision_state["history"])
                if value.get("status") == "current"
            ),
            None,
        )
        evidence = self._repository.evidence(project_id=project_id, case_id=case_id)
        stale_evidence_refs = _latest_stale_evidence_refs(revision_state)
        current_ui_evidence = [
            value
            for value in cast_list(evidence["ui_evidence"])
            if str(value.get("evidence_ref")) not in stale_evidence_refs
        ]
        screenshots = self._screenshot_items(
            request_id=request_id,
            execution=execution,
            ui_evidence=current_ui_evidence,
        )
        authorization = self._case_execution_authorizations.state(
            target_orchestration_id=orchestration_id,
            at=datetime.now(UTC),
        )
        authorization_error = cast(str | None, authorization["blocking_reason"])
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
            "test_data_plan": bundle["test_data_plan"],
            "test_data_execution": execution,
            "business_coverage": bundle["coverage_report"],
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
                    message="Web request reserved the approved TestDataPlan Run.",
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

    def recover_test_data_run(
        self,
        *,
        request_id: str,
        run_id: str,
        idempotency_key: str,
        actor: str,
        reason: str,
        stale_before: datetime,
    ) -> dict[str, object]:
        bundle = self._orchestrations.latest_bundle(request_id)
        if bundle is None:
            raise ValueError("Change Orchestration does not exist")
        orchestration = cast_dict(bundle["orchestration"])
        record = self._test_data_runs.get_record(run_id)
        if record is None or record.orchestration_id != str(orchestration["orchestration_id"]):
            raise ValueError("Test data recovery Run does not exist")
        recovery_id = _web_id("test-data-recovery", record.project_id, run_id, idempotency_key)
        result = TestDataExecutionService(
            connection=self._connection,
            contracts=self._contracts,
            executors={},
        ).recover(
            TestDataExecutionRecoveryRequest(
                recovery_id=recovery_id,
                run_id=run_id,
                project_id=record.project_id,
                actor=actor,
                reason=reason,
                stale_before=stale_before,
            )
        )
        closure = self._closure_service.close(
            orchestration_id=record.orchestration_id,
            actor=actor,
        )
        self._test_data_runs.append_event(
            TestDataExecutionEventWrite(
                run_id=record.run_id,
                project_id=record.project_id,
                event_type="closure_generated",
                message=f"ChangeClosureResult: {closure.record.closure_result_id}",
            )
        )
        return {
            "created": result.created,
            "run_id": run_id,
            "status": result.record.status,
            "recovery_id": recovery_id,
            "closure_result_id": closure.record.closure_result_id,
            "closure_status": closure.record.status,
        }

    def screenshot_path(self, *, request_id: str, origin: str, evidence_id: str) -> Path:
        if origin not in {"test_data", "ui"}:
            raise ValueError("Screenshot evidence origin does not exist")
        management = self.execution_management(request_id)
        for item in cast_list(management["screenshots"]):
            if item.get("origin") == origin and item.get("evidence_id") == evidence_id:
                path = self._local_evidence_path(str(item["evidence_ref"]))
                if path is not None:
                    return path
        raise ValueError("Screenshot evidence does not exist")

    def document_diff(self, request_id: str) -> dict[str, object]:
        return self._repository.document_diff(request_id)

    def review_document_diff(
        self,
        *,
        idempotency_key: str,
        request_id: str,
        project_id: str,
        decision: str,
        actor: str,
        note: str | None,
    ) -> dict[str, object]:
        event_id = _web_id("document-review", project_id, request_id, idempotency_key)
        record = self._repository.record_document_review(
            event_id=event_id,
            request_id=request_id,
            project_id=project_id,
            decision=decision,
            actor=actor,
            note=note,
        )
        response = {
            "created": record.created,
            "review_event_id": record.review_event_id,
            "decision": record.decision,
            "actor": record.actor,
            "created_at": record.created_at.isoformat(),
        }
        self._resume_latest_automation(request_id=request_id, actor=actor)
        return response

    def case_detail(self, *, project_id: str, case_id: str) -> dict[str, object]:
        progress = self._repository.case_workspace(project_id=project_id, case_id=case_id)
        report = self._repository.impact_report(project_id=project_id, case_id=case_id)
        evidence = self._repository.evidence(project_id=project_id, case_id=case_id)
        grant_value = progress["approval_grant"]
        grant_state: str | None = None
        if isinstance(grant_value, dict) and grant_value.get("id") is not None:
            grant_state = self._grants.inspect(str(grant_value["id"])).state
            grant_value["state"] = grant_state
        progress["steps"] = _progress_steps(progress, grant_state)
        return {"progress": progress, "impact_report": report, "evidence": evidence}

    def confirm_impact(
        self,
        *,
        idempotency_key: str,
        request_id: str,
        project_id: str,
        case_id: str,
        value: ImpactDecisionInput,
    ) -> dict[str, object]:
        self._repository.require_confirmed_document_review(
            request_id=request_id, project_id=project_id, case_id=case_id
        )
        confirmation_id = _web_id(
            "impact-confirmation", project_id, case_id, value.report_id, idempotency_key
        )
        existing = self._artifacts.get(confirmation_id)
        if existing is not None:
            expected = (
                existing.get("impact_report_id"),
                existing.get("confirmed_by"),
                existing.get("approved_item_ids"),
                existing.get("rejected_item_ids"),
                existing.get("user_note", ""),
            )
            requested = (
                value.report_id,
                value.actor,
                list(value.approved_item_ids),
                list(value.rejected_item_ids),
                value.note,
            )
            if expected != requested:
                raise ValueError("Impact confirmation replay payload differs")
            artifact = existing
        else:
            artifact = {
                "artifact_type": "ImpactConfirmation",
                "schema_version": "v1",
                "confirmation_id": confirmation_id,
                "impact_report_id": value.report_id,
                "confirmed_by": value.actor,
                "approved_item_ids": list(value.approved_item_ids),
                "rejected_item_ids": list(value.rejected_item_ids),
                "user_note": value.note,
                "confirmed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        result = self._impacts.confirm(
            project_id=project_id,
            analysis_case_id=case_id,
            artifact=artifact,
        )
        response = {
            "created": result.created,
            "confirmation_id": result.confirmation_id,
            "impact_report_id": result.impact_report_id,
            "report_status": result.report_status,
        }
        self._resume_latest_automation(request_id=request_id, actor=value.actor)
        return response

    def issue_grant(
        self,
        *,
        idempotency_key: str,
        request_id: str,
        project_id: str,
        case_id: str,
        value: GrantInput,
    ) -> dict[str, object]:
        self._repository.require_confirmed_document_review(
            request_id=request_id, project_id=project_id, case_id=case_id
        )
        grant_id = _web_id("approval-grant", project_id, case_id, idempotency_key)
        result = self._grant_service.issue(
            ApprovalGrantRequest(
                grant_id=grant_id,
                project_id=project_id,
                analysis_case_id=case_id,
                edit_packet_id=value.edit_packet_id,
                approved_by=value.actor,
                expires_at=value.expires_at,
                command_profile_binding_key=value.command_profile_binding_key,
                allowed_test_command_refs=value.test_command_refs,
            )
        )
        response = {
            "created": result.record.created,
            "state": result.record.state,
            "approval_grant": result.artifact,
        }
        self._resume_latest_automation(request_id=request_id, actor=value.actor)
        return response

    def readiness(self) -> dict[str, object]:
        path = self._root / "readiness/mvp-readiness.json"
        validator = MvpReadinessValidator(self._root)
        report = validator.validate(path)
        summary = validator.summarize(path)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        stale_regression = any(
            issue.code == "readiness.source_tree_digest_mismatch" for issue in report.issues
        )
        gates = [
            {
                "gate_id": gate.gate_id,
                "status": (
                    "pending"
                    if stale_regression and gate.gate_id == "full_local_regression"
                    else gate.status
                ),
                "blocking_reason": (
                    "Current source tree requires a fresh full regression."
                    if stale_regression and gate.gate_id == "full_local_regression"
                    else gate.blocking_reason
                ),
                "evidence_count": (
                    0
                    if stale_regression and gate.gate_id == "full_local_regression"
                    else gate.evidence_count
                ),
            }
            for gate in summary.gates
        ]
        return {
            "readiness_stage": summary.readiness_stage,
            "manifest_status": summary.manifest_status if report.is_valid else "stale",
            "manifest_version": manifest["manifest_version"],
            "validation_issues": [issue.code for issue in report.issues],
            "passed_gates": [gate["gate_id"] for gate in gates if gate["status"] == "passed"],
            "pending_gates": [gate["gate_id"] for gate in gates if gate["status"] == "pending"],
            "gates": gates,
        }

    def _screenshot_items(
        self,
        *,
        request_id: str,
        execution: dict[str, Any] | None,
        ui_evidence: list[dict[str, Any]],
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
                        origin="test_data",
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
        for value in ui_evidence:
            if value.get("evidence_type") != "screenshot":
                continue
            items.append(
                self._screenshot_item(
                    request_id=request_id,
                    origin="ui",
                    evidence_id=str(value["evidence_id"]),
                    evidence_ref=str(value["evidence_ref"]),
                    digest=str(value["sha256"]),
                    context={"scenario_id": value.get("scenario_id")},
                )
            )
        return items

    def _screenshot_item(
        self,
        *,
        request_id: str,
        origin: str,
        evidence_id: str,
        evidence_ref: str,
        digest: str,
        context: dict[str, object],
    ) -> dict[str, object]:
        available = self._local_evidence_path(evidence_ref) is not None
        return {
            "origin": origin,
            "evidence_id": evidence_id,
            "evidence_ref": evidence_ref,
            "sha256": digest,
            "available": available,
            "content_url": (
                "/api/v1/change-requests/"
                f"{quote(request_id, safe='')}/screenshots/{origin}/"
                f"{quote(evidence_id, safe='')}"
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


def _latest_stale_evidence_refs(state: dict[str, object]) -> frozenset[str]:
    latest = state.get("latest")
    if not isinstance(latest, dict):
        return frozenset()
    revision = latest.get("revision")
    if not isinstance(revision, dict):
        return frozenset()
    values = revision.get("stale_evidence_refs", [])
    if not isinstance(values, list):
        raise RuntimeError("Test Case revision stale Evidence lost its array shape")
    return frozenset(str(value) for value in values)


def _progress_steps(
    progress: dict[str, object],
    grant_state: str | None,
) -> list[dict[str, str]]:
    report_status = str(cast_dict(progress["impact_report"]).get("status") or "pending")
    edit_status = str(cast_dict(progress["edit_result"]).get("status") or "pending")
    validation_status = str(cast_dict(progress["validation"]).get("status") or "pending")
    return [
        {
            "step": "impact_scope",
            "label": "影響範囲の確認",
            "status": "completed" if report_status == "confirmed" else report_status,
        },
        {
            "step": "approval_grant",
            "label": "実行範囲の承認",
            "status": grant_state or "pending",
        },
        {
            "step": "code_execution",
            "label": "コード変更とテスト",
            "status": edit_status,
        },
        {
            "step": "ui_verification",
            "label": "UI 検証",
            "status": validation_status,
        },
    ]


def cast_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("Web progress read model lost its object shape")
    return value


def cast_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError("Web execution read model lost its array shape")
    return value
