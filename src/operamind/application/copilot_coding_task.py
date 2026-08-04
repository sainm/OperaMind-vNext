"""Transport-neutral Copilot Coding Task orchestration for the local POC Bridge."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from psycopg import Connection

from operamind.application.change_automation import CHANGE_FLOW_STATE_MACHINE
from operamind.application.change_coverage import ChangedLineCoverageEvidence
from operamind.application.command_execution import (
    ApprovedCommandRequest,
    ApprovedCommandService,
)
from operamind.application.copilot_document_change import (
    CopilotDocumentChangeService,
    DocumentFieldEdit,
)
from operamind.application.copilot_impact import CopilotImpactService
from operamind.application.copilot_task_context import (
    CopilotTaskContextRequest,
    CopilotTaskContextService,
)
from operamind.application.coverage_report import load_coverage_report
from operamind.application.edit_result import (
    EditResultRequest,
    EditResultService,
    EditValidationMode,
)
from operamind.application.hybrid_search import (
    RequirementDocumentDiscoveryRequest,
    RequirementDocumentDiscoveryService,
)
from operamind.application.planned_business_coverage import (
    assess_planned_business_coverage,
    canonical_artifact_refs_from_output,
    uncovered_business_rules,
)
from operamind.application.project_stack import detect_project_stack
from operamind.application.test_data_coverage import (
    validate_test_data_coverage_alignment,
)
from operamind.application.test_data_flow import (
    test_data_plan_channels,
    validate_test_data_plan_artifact,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import GitWorktreeDiffInspector
from operamind.infrastructure.embeddings import OpenAICompatibleEmbeddingProvider
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.canonical_repository import (
    CanonicalDocumentSlice,
    CanonicalRepository,
)
from operamind.infrastructure.postgres.change_automation_repository import (
    ChangeAutomationRepository,
)
from operamind.infrastructure.postgres.change_orchestration_repository import (
    ChangeOrchestrationRepository,
)
from operamind.infrastructure.postgres.copilot_coding_task_repository import (
    CopilotCodingTaskRepository,
)
from operamind.infrastructure.postgres.document_node_repository import DocumentNodeRepository
from operamind.infrastructure.postgres.profile_repository import ProfileRepository
from operamind.infrastructure.postgres.search_index_repository import SearchIndexRepository
from operamind.infrastructure.postgres.web_control_plane_repository import (
    WebControlPlaneRepository,
)
from operamind.infrastructure.test_data.target_data import (
    TargetDataProfile,
    TargetDataProfileRepository,
    TargetDataSecretStore,
)
from operamind.profiles import ProfileCatalog

REQUIRED_TASK_TOOLS = (
    "copilot_get_coding_task",
    "copilot_record_change_outputs",
    "copilot_run_task_command",
    "copilot_validate_task_diff",
    "copilot_record_task_result",
)
CHANGE_TASK_STAGE_ORDER = (
    "requirement",
    "document_change",
    "code_scope",
    "compile_test",
    "ui_validation",
    "final_report",
)
CHANGE_TASK_REQUIRED_OUTPUTS = (
    "document_diff",
    "code_diff",
    "test_plan",
    "test_data_plan",
)


class CodingTaskDeliveryProvider(Protocol):
    """The stable adapter boundary shared by local Bridge and future API delivery."""

    @property
    def contract(self) -> dict[str, str]: ...


@dataclass(frozen=True, slots=True)
class LocalBridgeCopilotProvider:
    """POC provider: delivery is local and execution remains in VS Code Copilot."""

    @property
    def contract(self) -> dict[str, str]:
        return {
            "interface": "coding_task_provider_v1",
            "route": "local_bridge",
            "provider_id": "vscode_github_copilot",
        }


@dataclass(frozen=True, slots=True)
class CopilotCodingTaskPublishRequest:
    coding_task_id: str
    change_request_id: str
    project_id: str
    workspace_root: Path
    task_summary: str
    actor: str
    idempotency_key: str
    edit_packet_id: str | None = None
    approval_grant_id: str | None = None
    retry_of_coding_task_id: str | None = None
    attempt_number: int = 1
    task_kind: str = "change_delivery"
    initial_stage: str = "document_change"
    plan_revision_context: dict[str, object] | None = None
    execution_basis: dict[str, object] | None = None

    def __post_init__(self) -> None:
        values = (
            self.coding_task_id,
            self.change_request_id,
            self.project_id,
            self.task_summary,
            self.actor,
            self.idempotency_key,
            self.task_kind,
            self.initial_stage,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Copilot Coding Task publish fields must not be blank")
        if len(self.task_summary) > 10_000:
            raise ValueError("Copilot Coding Task summary exceeds 10000 characters")
        if (self.edit_packet_id is None) != (self.approval_grant_id is None):
            raise ValueError("Edit Packet and Approval Grant must be supplied together")
        if self.edit_packet_id is not None and not self.edit_packet_id.strip():
            raise ValueError("edit_packet_id must not be blank")
        if self.approval_grant_id is not None and not self.approval_grant_id.strip():
            raise ValueError("approval_grant_id must not be blank")
        if self.retry_of_coding_task_id is not None and not self.retry_of_coding_task_id.strip():
            raise ValueError("retry_of_coding_task_id must not be blank")
        if self.attempt_number < 1:
            raise ValueError("Copilot Coding Task attempt_number must be positive")
        if self.task_kind not in {
            "change_delivery",
            "change_execution",
            "ui_test_plan_revision",
        }:
            raise ValueError("Unsupported Copilot Coding Task kind")
        if self.task_kind == "ui_test_plan_revision":
            if self.initial_stage != "ui_test_revision" or self.plan_revision_context is None:
                raise ValueError("UI TestPlan revision Task requires revision context")
            required_context = {
                "proposal_id",
                "source_orchestration_id",
                "source_test_plan_id",
                "instruction",
                "confirmed_operations_json",
                "selections_json",
            }
            if set(self.plan_revision_context) != required_context or any(
                not isinstance(value, str) or not value.strip()
                for value in self.plan_revision_context.values()
            ):
                raise ValueError("UI TestPlan revision context is incomplete")
            if self.edit_packet_id is not None or self.approval_grant_id is not None:
                raise ValueError("UI TestPlan revision Task must not receive code edit scope")
            if self.execution_basis is not None:
                raise ValueError("UI TestPlan revision Task must not receive execution basis")
        elif self.task_kind == "change_execution":
            if (
                self.initial_stage != "compile_test"
                or self.edit_packet_id is None
                or self.approval_grant_id is None
                or self.execution_basis is None
            ):
                raise ValueError(
                    "Change execution Task requires confirmed scope, execution basis, "
                    "and compile_test initial stage"
                )
            if self.plan_revision_context is not None:
                raise ValueError("Change execution Task must not receive revision-only fields")
            impact_id = self.execution_basis.get("impact_report_id")
            change_refs = self.execution_basis.get("document_change_refs")
            if (
                not isinstance(impact_id, str)
                or not impact_id.strip()
                or not isinstance(change_refs, list)
                or not change_refs
                or any(not isinstance(value, str) or not value.strip() for value in change_refs)
            ):
                raise ValueError("Change execution Task has incomplete execution basis")
        elif (
            self.initial_stage != "document_change"
            or self.plan_revision_context is not None
            or self.execution_basis is not None
        ):
            raise ValueError("Change delivery Task has invalid revision-only fields")


class CopilotCodingTaskService:
    """Publish, accept, execute, and report one bounded end-to-end Change Task."""

    def __init__(self, *, connection: Connection[Any], repository_root: Path) -> None:
        root = repository_root.resolve()
        self._root = root
        self._connection = connection
        self._contracts = ContractCatalog.load(root / "contracts")
        self._profiles = ProfileCatalog.load(root / "profiles")
        self._tasks = CopilotCodingTaskRepository(connection, self._contracts)
        self._artifacts = ArtifactRepository(connection, self._contracts)
        self._canonical = CanonicalRepository(connection, self._contracts)
        self._requests = WebControlPlaneRepository(connection, self._contracts)
        self._profile_repository = ProfileRepository(connection, self._profiles)
        self._index_repository = SearchIndexRepository(connection)
        self._document_nodes = DocumentNodeRepository(connection)
        self._provider: CodingTaskDeliveryProvider = LocalBridgeCopilotProvider()

    def publish(self, request: CopilotCodingTaskPublishRequest) -> dict[str, object]:
        try:
            existing_record = self._tasks.get(request.coding_task_id)
        except ValueError:
            existing_record = None
        if existing_record is not None:
            existing = self._tasks.view(request.coding_task_id)
            task = cast(dict[str, object], existing["task"])
            expected = (
                request.change_request_id,
                request.project_id,
                request.edit_packet_id,
                request.approval_grant_id,
                str(request.workspace_root.resolve(strict=True)),
                request.task_summary,
                request.actor,
                request.retry_of_coding_task_id,
                request.attempt_number,
                request.task_kind,
                request.initial_stage,
                request.plan_revision_context,
                request.execution_basis,
            )
            actual = (
                existing_record.change_request_id,
                existing_record.project_id,
                existing_record.edit_packet_id,
                existing_record.approval_grant_id,
                existing_record.workspace_root,
                task.get("task_summary"),
                task.get("created_by"),
                existing_record.retry_of_coding_task_id,
                existing_record.attempt_number,
                task.get("task_kind", "change_delivery"),
                task.get("initial_stage", "document_change"),
                task.get("plan_revision_context"),
                task.get("execution_basis"),
            )
            if actual != expected:
                raise ValueError("Copilot Coding Task replay payload differs")
            return {"created": False, **existing}
        change_request = self._requests.get_change_request(request.change_request_id)
        case_id = change_request.get("analysis_case_id")
        if change_request.get("project_id") != request.project_id:
            raise ValueError("Change Request is outside requested Project")
        change_artifact = cast(dict[str, object], change_request["artifact"])
        packet: dict[str, object] | None = None
        if request.edit_packet_id is not None and request.approval_grant_id is not None:
            if not isinstance(case_id, str):
                raise ValueError("Bound Change Task requires a ChangeSession")
            context = CopilotTaskContextService(
                connection=self._connection,
                contracts=self._contracts,
            ).get(
                CopilotTaskContextRequest(
                    project_id=request.project_id,
                    analysis_case_id=case_id,
                    edit_packet_id=request.edit_packet_id,
                    approval_grant_id=request.approval_grant_id,
                    workspace_root=request.workspace_root,
                )
            )
            packet = cast(dict[str, object], context["edit_packet"])
        request.workspace_root.resolve(strict=True)
        target_project = detect_project_stack(request.workspace_root).copilot_context()
        provider_contract = self._provider.contract
        if provider_contract != {
            "interface": "coding_task_provider_v1",
            "route": "local_bridge",
            "provider_id": "vscode_github_copilot",
        }:
            raise ValueError("POC only supports the local VS Code GitHub Copilot provider")
        artifact: dict[str, Any] = {
            "artifact_type": "CopilotCodingTask",
            "schema_version": "v2",
            "coding_task_id": request.coding_task_id,
            "change_session_id": case_id if packet is not None else None,
            "change_request_id": request.change_request_id,
            "project_id": request.project_id,
            "analysis_case_id": case_id if packet is not None else None,
            "repository_id": packet["repository_id"] if packet is not None else None,
            "edit_packet_id": request.edit_packet_id,
            "approval_grant_id": request.approval_grant_id,
            "base_repository_revision": (
                packet["base_repository_revision"] if packet is not None else None
            ),
            "attempt_number": request.attempt_number,
            "task_kind": request.task_kind,
            "execution_mode": "copilot_change_task",
            "initial_stage": request.initial_stage,
            "provider_contract": provider_contract,
            "task_summary": request.task_summary,
            "change_context": {
                "requirement_text": change_artifact.get("requirement_text"),
                "source_document_ref": change_artifact.get("source_document_ref"),
                "target_document_ref": change_artifact.get("target_document_ref"),
                "business_rules": change_artifact.get("business_rules", []),
                "ambiguity_status": change_artifact["ambiguity_status"],
            },
            "target_project": target_project,
            "workflow": {
                "stage_order": list(CHANGE_TASK_STAGE_ORDER),
                "required_outputs": list(CHANGE_TASK_REQUIRED_OUTPUTS),
            },
            "output_protocol": {
                "stage_order": [
                    "document_change",
                    "code_scope",
                    "test_planning",
                ],
                "tool": "copilot_record_change_outputs",
                "test_planning_requires_validated_diff": True,
            },
            "mcp_server_name": "operaMind",
            "required_mcp_tools": list(REQUIRED_TASK_TOOLS),
            "created_by": request.actor,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        if request.retry_of_coding_task_id is not None:
            artifact["retry_of_coding_task_id"] = request.retry_of_coding_task_id
        if request.plan_revision_context is not None:
            artifact["plan_revision_context"] = request.plan_revision_context
        if request.execution_basis is not None:
            artifact["execution_basis"] = request.execution_basis
        record = self._tasks.publish(
            artifact=artifact,
            workspace_root=request.workspace_root,
            idempotency_key=request.idempotency_key,
        )
        view = self._tasks.view(record.coding_task_id)
        return {"created": record.created, **view}

    def claim_next(
        self,
        *,
        workspace_root: Path,
        consumer_id: str,
        change_request_id: str | None = None,
    ) -> dict[str, object] | None:
        if not consumer_id.strip():
            raise ValueError("Bridge consumer_id must not be blank")
        return self._tasks.claim_next(
            workspace_root=workspace_root,
            consumer_id=consumer_id,
            change_request_id=change_request_id,
        )

    def accept(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        consumer_id: str,
        actor: str,
    ) -> dict[str, object]:
        return self._tasks.accept(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            consumer_id=consumer_id,
            actor=actor,
        )

    def resume(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        consumer_id: str,
    ) -> dict[str, object]:
        if not consumer_id.strip():
            raise ValueError("Bridge consumer_id must not be blank")
        return self._tasks.resume(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            consumer_id=consumer_id,
        )

    def cancel(
        self,
        *,
        coding_task_id: str,
        change_request_id: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        consumer_id: str | None = None,
    ) -> dict[str, object]:
        record = self._tasks.get(coding_task_id)
        if record.change_request_id != change_request_id:
            raise ValueError("Copilot Coding Task is outside requested Change Request")
        if not reason.strip():
            raise ValueError("Copilot Coding Task cancellation reason must not be blank")
        return self._tasks.cancel(
            coding_task_id=coding_task_id,
            actor=actor,
            reason=reason.strip(),
            idempotency_key=idempotency_key,
            consumer_id=consumer_id,
        )

    def retry(
        self,
        *,
        coding_task_id: str,
        retry_coding_task_id: str,
        change_request_id: str,
        actor: str,
        idempotency_key: str,
        edit_packet_id: str,
        approval_grant_id: str,
        workspace_root: Path,
    ) -> dict[str, object]:
        previous = self._tasks.get(coding_task_id)
        if previous.change_request_id != change_request_id:
            raise ValueError("Copilot Coding Task is outside requested Change Request")
        if previous.state not in {"cancelled", "failed"}:
            raise ValueError("Only a cancelled or failed Copilot Coding Task can be retried")
        previous_artifact = cast(dict[str, object], self._tasks.view(coding_task_id)["task"])
        return self.publish(
            CopilotCodingTaskPublishRequest(
                coding_task_id=retry_coding_task_id,
                change_request_id=change_request_id,
                project_id=previous.project_id,
                edit_packet_id=edit_packet_id,
                approval_grant_id=approval_grant_id,
                workspace_root=workspace_root,
                task_summary=str(previous_artifact["task_summary"]),
                actor=actor,
                idempotency_key=idempotency_key,
                retry_of_coding_task_id=coding_task_id,
                attempt_number=previous.attempt_number + 1,
            )
        )

    def view(self, coding_task_id: str) -> dict[str, object]:
        return self._tasks.view(coding_task_id)

    def latest_for_request(
        self,
        change_request_id: str,
        *,
        task_kind: str | None = None,
    ) -> dict[str, object] | None:
        return self._tasks.latest_for_request(change_request_id, task_kind=task_kind)

    def rollback_document_change(self, coding_task_id: str) -> dict[str, object]:
        """Rollback the exact document draft recorded by a rejected task."""

        task = self._tasks.get(coding_task_id)
        output = self._recorded_output(coding_task_id, "document_change")
        document_ids = tuple(str(value) for value in cast(list[object], output["document_ids"]))
        paths = CopilotDocumentChangeService(
            connection=self._connection,
            repository_root=self._root,
        ).rollback_materialized(
            project_id=task.project_id,
            source_snapshot_id=str(output["source_document_snapshot_id"]),
            target_snapshot_id=str(output["target_document_snapshot_id"]),
            document_ids=document_ids,
        )
        return {
            "coding_task_id": coding_task_id,
            "document_ids": list(document_ids),
            "restored_paths": [str(path) for path in paths],
        }

    def document_discovery_for_request(self, change_request_id: str) -> dict[str, object]:
        """Expose the same bounded RAG document set to Web and Copilot."""
        return _public_document_discovery(
            self.canonical_document_discovery_for_request(change_request_id)
        )

    def canonical_document_discovery_for_request(self, change_request_id: str) -> dict[str, object]:
        """Return discovery evidence that can be bound to an approved Change Task."""

        return self._document_discovery(change_request_id)

    def bind_document_discovery(
        self,
        *,
        coding_task_id: str,
        automation_run_id: str,
        subject_digest: str,
        discovery: dict[str, object],
        actor: str,
    ) -> None:
        task = self._tasks.get(coding_task_id)
        automation = ChangeAutomationRepository(self._connection).get(automation_run_id)
        if (
            automation.change_request_id != task.change_request_id
            or automation.project_id != task.project_id
        ):
            raise ValueError("RAG discovery Automation Run is outside Coding Task scope")
        if subject_digest != _payload_digest(discovery):
            raise ValueError("RAG discovery subject digest differs from its content")
        self._tasks.bind_document_discovery(
            coding_task_id=coding_task_id,
            automation_run_id=automation_run_id,
            subject_digest=subject_digest,
            discovery=discovery,
            actor=actor,
        )

    def bind_execution_scope(
        self,
        *,
        coding_task_id: str,
        analysis_case_id: str,
        edit_packet_id: str,
        approval_grant_id: str,
        workspace_root: Path,
        actor: str,
    ) -> dict[str, object]:
        task = self._tasks.get(coding_task_id)
        context = CopilotTaskContextService(
            connection=self._connection,
            contracts=self._contracts,
        ).get(
            CopilotTaskContextRequest(
                project_id=task.project_id,
                analysis_case_id=analysis_case_id,
                edit_packet_id=edit_packet_id,
                approval_grant_id=approval_grant_id,
                workspace_root=workspace_root,
            )
        )
        packet = cast(dict[str, object], context["edit_packet"])
        self._tasks.bind_execution_scope(
            coding_task_id=coding_task_id,
            analysis_case_id=analysis_case_id,
            repository_id=str(packet["repository_id"]),
            edit_packet_id=edit_packet_id,
            approval_grant_id=approval_grant_id,
            base_repository_revision=str(packet["base_repository_revision"]),
            actor=actor,
        )
        return self._tasks.view(coding_task_id)

    def get_mcp_context(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        actor: str = "mcp:github-copilot",
    ) -> dict[str, object]:
        if not actor.strip():
            raise ValueError("Coding Task context actor must not be blank")
        pending_task = self._tasks.get(coding_task_id)
        immutable_task = cast(dict[str, object], self._tasks.view(coding_task_id)["task"])
        if immutable_task.get("task_kind") == "ui_test_plan_revision":
            task = self._tasks.begin_mcp(
                coding_task_id=coding_task_id,
                workspace_root=workspace_root,
                actor=actor,
            )
            context = cast(dict[str, object], immutable_task["plan_revision_context"])
            bundle = ChangeOrchestrationRepository(self._connection, self._contracts).bundle(
                str(context["source_orchestration_id"])
            )
            return {
                "coding_task": _public_mcp_task(immutable_task),
                "stage_status": _ready_stage_status(
                    task_stage=task.current_stage,
                    task_state=task.state,
                ),
                "stage_contract": _stage_contract(task.current_stage),
                "workspace": {"root": task.workspace_root},
                "inputs": {
                    "source_ui_test_plan": bundle["test_plan"],
                    "source_test_data_plan": bundle["test_data_plan"],
                    "revision_instruction": context["instruction"],
                    "confirmed_change_summary": json.loads(
                        str(context["confirmed_operations_json"])
                    ),
                    "target_data_bindings": _public_target_data_profile(
                        TargetDataProfileRepository(self._connection).get(
                            str(immutable_task["project_id"])
                        )
                    ),
                },
                "constraints": {"execution_scope": {"bound": False, "read_only": True}},
            }
        automation_repository = ChangeAutomationRepository(self._connection)
        automation = automation_repository.latest_for_request(pending_task.change_request_id)
        review_feedback: dict[str, object] | None = None
        if (
            pending_task.current_stage == "code_scope"
            and automation is not None
            and automation.get("current_stage") == "impact_confirmation"
        ):
            confirmation = automation_repository.latest_confirmation(
                run_id=str(automation["automation_run_id"]),
                checkpoint="code_scope",
            )
            if _is_rejected_code_scope_revision(automation, confirmation):
                assert confirmation is not None
                review_feedback = {
                    "checkpoint": "code_scope",
                    "decision": "rejected",
                    "note": confirmation.get("note"),
                    "created_at": confirmation.get("created_at"),
                }
        if (
            pending_task.approval_grant_id is None
            and automation is not None
            and not CHANGE_FLOW_STATE_MACHINE.allows_copilot_stage(
                task_stage=pending_task.current_stage,
                automation_stage=automation.get("current_stage"),
                has_review_feedback=review_feedback is not None,
            )
        ):
            raise ValueError(
                "現在の人工確認が完了するまで Copilot Task を実行できません: "
                f"{automation.get('current_stage')}"
            )
        task = self._tasks.begin_mcp(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            actor=actor,
        )
        task_view = self._tasks.view(coding_task_id)
        if task.approval_grant_id is None:
            if task.current_stage in {"document_change", "code_scope"}:
                document_discovery = self._document_discovery_for_task(
                    coding_task_id, task.change_request_id
                )
            else:
                raise ValueError(
                    "Unbound Copilot Change Task has an invalid current stage: "
                    f"{task.current_stage}"
                )
            inputs: dict[str, object] = {
                "requirement": _public_requirement_context(immutable_task),
                "document_discovery": _public_document_discovery(document_discovery),
            }
            if task.current_stage == "code_scope":
                inputs["design_changes"] = self._public_document_changes(coding_task_id)
            if review_feedback is not None:
                inputs["review_feedback"] = review_feedback
            return {
                "coding_task": _public_mcp_task(cast(dict[str, object], task_view["task"])),
                "stage_status": _ready_stage_status(
                    task_stage=task.current_stage,
                    task_state=task.state,
                ),
                "stage_contract": _stage_contract(task.current_stage),
                "workspace": {"root": task.workspace_root},
                "inputs": inputs,
                "constraints": {"execution_scope": {"bound": False}},
            }
        case_id, edit_packet_id, approval_grant_id = _bound_task_scope(task)
        context = CopilotTaskContextService(
            connection=self._connection,
            contracts=self._contracts,
        ).get(
            CopilotTaskContextRequest(
                project_id=task.project_id,
                analysis_case_id=case_id,
                edit_packet_id=edit_packet_id,
                approval_grant_id=approval_grant_id,
                workspace_root=workspace_root,
                require_active_grant=task.current_stage != "test_planning",
            )
        )
        packet = cast(dict[str, object], context["edit_packet"])
        approval = cast(dict[str, object], context["approval"])
        task_workspace = cast(dict[str, object], context["workspace"])
        verification_only = not bool(packet.get("editable_files"))
        planning_input: dict[str, object] | None = None
        if task.current_stage == "test_planning":
            request_record = self._requests.get_change_request(task.change_request_id)
            request_artifact = cast(dict[str, Any], request_record["artifact"])
            code_refs = self._code_scope_output(coding_task_id)
            passed_command_refs = sorted(
                str(command["command_ref"])
                for command in cast(list[dict[str, Any]], task_view["commands"])
                if command.get("status") == "passed" and command.get("exit_code") == 0
            )
            planning_input = {
                "schema_source": "copilot_record_change_outputs.inputSchema",
                "required_ui_scenario_ids": [],
                "target_data_bindings": _public_target_data_profile(
                    TargetDataProfileRepository(self._connection).get(task.project_id)
                ),
                "business_coverage": {
                    "required_coverage_percent": 100,
                    "business_requirements": copy.deepcopy(
                        request_artifact.get("business_rules", [])
                    ),
                    "allowed_evidence": {
                        "code_test_files": sorted(
                            str(value)
                            for value in cast(list[object], approval.get("test_files", []))
                        ),
                        "passed_command_refs": passed_command_refs,
                        "canonical_artifact_refs": sorted(
                            canonical_artifact_refs_from_output(code_refs)
                        ),
                        "plan_component_refs": [
                            "ui_test_plan",
                            "test_data_plan",
                            "generation_flows",
                            "cleanup",
                            "playwright_observations",
                        ],
                    },
                },
                "test_data_coverage": {
                    "required_coverage_percent": 100,
                    "calculated_by": "operamind_execution_engine",
                    "required_binding": (
                        "every acceptance_criteria_ref x test_case_id x test_data_id"
                    ),
                    "allowed_condition_kinds": [
                        "field",
                        "status",
                        "boundary",
                        "relationship",
                    ],
                    "source": "actual reviewed SQL readback only",
                },
                "rules": [
                    (
                        "各 business_rule_id を実行可能な browser UI Case で覆い、"
                        "自然言語の全 step_id を TestDataPlan の Playwright Step に対応させる。"
                    ),
                    (
                        "テストデータの項目、列挙値、関連、HTTP 形式、Locator は"
                        "限定済み設計・コード根拠から取得し、推測しない。"
                    ),
                    (
                        "各 test_data_id は生成または接管後、確認済み SQL readback から"
                        "実 DB 主キー、業務 UNIQUE キー、画面キー、row_count=1 を"
                        " identity_binding に定義する。"
                    ),
                    (
                        "各 acceptance_criteria_ref x test_case_id x test_data_id に"
                        " coverage_conditions を定義する。Identity Key だけの存在確認は"
                        " Coverage に数えず、同じ SQL readback の業務項目、状態、境界値、"
                        "関連を機械判定できる path/operator/expected で示す。全条件を正式な"
                        " UI Step より前に実行する。Coverage 値は出力せず OperaMind が"
                        "実 DB 値から計算する。"
                    ),
                    (
                        "全 UI Step に operation_scope=screen|bound_record を明示する。"
                        "跨画面・表レコード操作は bound_record と data_binding_ref を"
                        "必須とし、画面キーの"
                        " exact scope 内だけを操作する。行番号、曖昧 text、推測 locator、"
                        "binding 対象の computer-use fallback は禁止する。"
                    ),
                    (
                        "SQL を使う場合は target_data_bindings の query_binding_id と"
                        "入力項目だけを使用し、SQL 文や接続情報を生成・要求しない。"
                    ),
                    (
                        "Playwright を優先し、DOM で実行できない確認済み操作だけに"
                        " computer_use_fallback を使う。Screenshot は機密表示を mask する。"
                    ),
                    (
                        "不足要件が返った場合は両計画の完全版を再生成する。"
                        "業務 Coverage 100% 未満では人工確認へ進まない。"
                    ),
                ],
            }
            impact = self._artifacts.get(str(code_refs["impact_report_id"]))
            if impact is None or impact.get("artifact_type") != "ImpactReport":
                raise RuntimeError("Copilot Change Task Impact Report Artifact is missing")
            planning_input["required_ui_scenario_ids"] = copy.deepcopy(
                impact.get("required_ui_scenario_refs", [])
            )
        inputs = {
            "requirement": _public_requirement_context(immutable_task),
            "design_changes": self._public_document_changes(coding_task_id),
        }
        if planning_input is not None:
            inputs["planning"] = planning_input
        return {
            "coding_task": _public_mcp_task(cast(dict[str, object], task_view["task"])),
            "stage_status": _ready_stage_status(
                task_stage=task.current_stage,
                task_state=task.state,
            ),
            "stage_contract": _stage_contract(
                task.current_stage,
                verification_only=verification_only,
            ),
            "workspace": _public_workspace(task_workspace),
            "inputs": inputs,
            "constraints": {
                "execution_scope": _public_execution_scope(packet, approval),
                "target_project": immutable_task.get("target_project", {}),
            },
        }

    def _document_discovery(self, change_request_id: str) -> dict[str, object]:
        request = self._requests.get_change_request(change_request_id)
        request_artifact = cast(dict[str, object], request["artifact"])
        explicit_refs = tuple(
            str(value)
            for value in (
                request_artifact.get("source_document_ref"),
                request_artifact.get("target_document_ref"),
            )
            if isinstance(value, str) and value.strip()
        )
        case_id = request.get("analysis_case_id")
        if isinstance(case_id, str):
            impact = self._requests.impact_report(
                project_id=str(request["project_id"]),
                case_id=case_id,
            )
            context_package_id = (
                impact.get("context_package_id") if isinstance(impact, dict) else None
            )
            if isinstance(context_package_id, str):
                context = self._artifacts.get(context_package_id)
                if context is not None and context.get("artifact_type") == "ContextPackage":
                    items = context.get("context_items")
                    if isinstance(items, list) and items:
                        candidates = [
                            {
                                "document_id": item.get("document_id"),
                                "section_id": item.get("section_id"),
                                "heading_path": item.get("heading_path"),
                                "summary": item.get("compressed_summary"),
                                "relevance_reason": item.get("relevance_reason"),
                                "evidence_refs": item.get("evidence_refs", []),
                            }
                            for item in items[:50]
                            if isinstance(item, dict)
                        ]
                        snapshot_id = context.get("document_snapshot_id")
                        if candidates and isinstance(snapshot_id, str):
                            candidates = self._bind_real_documents(
                                project_id=str(request["project_id"]),
                                snapshot_id=snapshot_id,
                                candidates=candidates,
                            )
                            return {
                                "status": "ready",
                                "mode": "canonical_hybrid_rag",
                                "context_package_id": context_package_id,
                                "document_snapshot_id": snapshot_id,
                                "search_index_build_id": context.get("search_index_build_id"),
                                "explicit_document_refs": list(explicit_refs),
                                "candidates": candidates,
                                "blocking_reason": None,
                            }
        requirement_text = request_artifact.get("requirement_text")
        if not isinstance(requirement_text, str) or not requirement_text.strip():
            rules = request_artifact.get("business_rules")
            rule_values = rules if isinstance(rules, list) else []
            requirement_text = " ".join(
                str(rule.get("text"))
                for rule in rule_values
                if isinstance(rule, dict) and isinstance(rule.get("text"), str)
            )
        try:
            bindings = self._profile_repository.list_active_by_type(
                project_id=str(request["project_id"]),
                profile_type="EmbeddingProfile",
            )
            if len(bindings) != 1:
                raise ValueError(
                    "Requirement discovery requires exactly one active EmbeddingProfile "
                    f"(found {len(bindings)})"
                )
            provider = OpenAICompatibleEmbeddingProvider.from_profile(bindings[0].profile)
            discovery = RequirementDocumentDiscoveryService(
                profiles=self._profiles,
                profile_repository=self._profile_repository,
                index_repository=self._index_repository,
                node_repository=self._document_nodes,
            ).run(
                RequirementDocumentDiscoveryRequest(
                    project_id=str(request["project_id"]),
                    query_text=requirement_text,
                ),
                provider=provider,
            )
        except (ValueError, RuntimeError) as error:
            discovery_error = str(error)
        else:
            candidates = self._bind_real_documents(
                project_id=str(request["project_id"]),
                snapshot_id=discovery.document_snapshot_id,
                candidates=[candidate.to_dict() for candidate in discovery.candidates],
            )
            return {
                "status": "ready",
                "mode": (
                    "requirement_hybrid_rag_with_explicit_refs"
                    if explicit_refs
                    else "requirement_hybrid_rag"
                ),
                "context_package_id": None,
                "document_snapshot_id": discovery.document_snapshot_id,
                "search_index_build_id": discovery.search_index_build_id,
                "embedding_profile_binding_key": (discovery.embedding_profile_binding_key),
                "explicit_document_refs": list(explicit_refs),
                "candidates": candidates,
                "blocking_reason": None,
            }
        return {
            "status": "blocked",
            "mode": "canonical_hybrid_rag",
            "context_package_id": None,
            "document_snapshot_id": None,
            "search_index_build_id": None,
            "explicit_document_refs": list(explicit_refs),
            "candidates": [],
            "blocking_reason": (
                f"Canonical requirement document discovery is unavailable: {discovery_error}"
            ),
        }

    def _document_discovery_for_task(
        self, coding_task_id: str, change_request_id: str
    ) -> dict[str, object]:
        automation = ChangeAutomationRepository(self._connection).latest_for_request(
            change_request_id
        )
        bound = next(
            (
                cast(dict[str, object], event["payload"])
                for event in reversed(
                    cast(list[dict[str, object]], self._tasks.view(coding_task_id)["events"])
                )
                if event.get("event_type") == "document_discovery_bound"
            ),
            None,
        )
        if bound is None:
            if automation is not None:
                raise ValueError("Copilot Coding Task has no user-confirmed RAG discovery binding")
            return self._document_discovery(change_request_id)
        if automation is not None and bound.get("automation_run_id") != automation.get(
            "automation_run_id"
        ):
            raise ValueError("Copilot Coding Task RAG discovery binding is stale")
        discovery = bound.get("discovery")
        if not isinstance(discovery, dict):
            raise RuntimeError("Bound RAG discovery lost its object shape")
        if bound.get("subject_digest") != _payload_digest(discovery):
            raise RuntimeError("Bound RAG discovery digest differs from its content")
        return cast(dict[str, object], discovery)

    def _bind_real_documents(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        candidates: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Resolve RAG fragments to their complete Canonical source documents."""

        documents: dict[str, CanonicalDocumentSlice] = {}
        resolved: list[dict[str, object]] = []
        for candidate in candidates:
            document_id = candidate.get("document_id")
            if not isinstance(document_id, str) or not document_id.strip():
                raise RuntimeError("RAG candidate has no Canonical document identity")
            document = documents.get(document_id)
            if document is None:
                document = self._canonical.get_document_slice(
                    project_id=project_id,
                    snapshot_id=snapshot_id,
                    document_id=document_id,
                )
                if document is None:
                    raise RuntimeError(
                        "RAG candidate cannot be resolved to its complete Canonical document"
                    )
                documents[document_id] = document
            resolved.append(
                {
                    **candidate,
                    "logical_name": document.logical_name,
                    "document_ref": document.source_ref,
                    "canonical_document": _public_canonical_document(document),
                }
            )
        return resolved

    def run_command(
        self,
        *,
        coding_task_id: str,
        command_execution_id: str,
        command_ref: str,
        workspace_root: Path,
        actor: str = "mcp:github-copilot",
    ) -> dict[str, object]:
        if not actor.strip():
            raise ValueError("Command actor must not be blank")
        task = self._tasks.get(coding_task_id)
        if task.state != "in_progress":
            raise ValueError("Copilot Coding Task context must be loaded before tests")
        case_id, edit_packet_id, approval_grant_id = _bound_task_scope(task)
        result = (
            ApprovedCommandService(
                connection=self._connection,
                contracts=self._contracts,
                profiles=self._profiles,
            )
            .run(
                ApprovedCommandRequest(
                    command_execution_id=command_execution_id,
                    approval_grant_id=approval_grant_id,
                    project_id=task.project_id,
                    analysis_case_id=case_id,
                    edit_packet_id=edit_packet_id,
                    workspace_root=workspace_root,
                    command_ref=command_ref,
                )
            )
            .to_dict()
        )
        self._tasks.bind_command(
            coding_task_id=coding_task_id,
            command_execution_id=command_execution_id,
            actor=actor,
            result=result,
        )
        return {**result, "coding_task_state": self._tasks.get(coding_task_id).state}

    def record_change_outputs(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        output_stage: str,
        document_ids: tuple[str, ...] = (),
        document_edits: tuple[DocumentFieldEdit, ...] = (),
        code_scope: tuple[dict[str, Any], ...] = (),
        test_plan: dict[str, Any] | None = None,
        test_data_plan: dict[str, Any] | None = None,
        actor: str = "mcp:github-copilot",
    ) -> dict[str, object]:
        if not actor.strip():
            raise ValueError("Change output actor must not be blank")
        task = self._tasks.get(coding_task_id)
        if task.state != "in_progress":
            raise ValueError("Copilot Change Task context must be loaded before recording outputs")
        if str(workspace_root.resolve(strict=True)) != task.workspace_root:
            raise ValueError("Copilot Change Task Workspace does not match output recording")
        if output_stage == "document_change":
            if code_scope or test_plan is not None or test_data_plan is not None:
                raise ValueError(
                    "Document output stage accepts only document_ids and document_edits"
                )
            return self._record_document_outputs(
                coding_task_id=coding_task_id,
                workspace_root=workspace_root,
                document_ids=document_ids,
                document_edits=document_edits,
                actor=actor,
            )
        if output_stage == "code_scope":
            if (
                document_ids
                or document_edits
                or test_plan is not None
                or test_data_plan is not None
            ):
                raise ValueError("Code scope output stage accepts only code_scope")
            return self._record_code_scope_output(
                coding_task_id=coding_task_id,
                workspace_root=workspace_root,
                code_scope=code_scope,
                actor=actor,
            )
        if output_stage == "test_planning":
            if (
                document_ids
                or document_edits
                or code_scope
                or test_plan is None
                or test_data_plan is None
            ):
                raise ValueError(
                    "Test planning output stage requires only test_plan and test_data_plan"
                )
            return self._record_test_planning_outputs(
                coding_task_id=coding_task_id,
                test_plan=test_plan,
                test_data_plan=test_data_plan,
                actor=actor,
            )
        if output_stage == "ui_test_revision":
            if (
                document_ids
                or document_edits
                or code_scope
                or test_plan is None
                or test_data_plan is None
            ):
                raise ValueError("UI TestPlan revision requires only test_plan and test_data_plan")
            return self._record_ui_test_revision_outputs(
                coding_task_id=coding_task_id,
                test_plan=test_plan,
                test_data_plan=test_data_plan,
                actor=actor,
            )
        raise ValueError(f"Unsupported Copilot Change Task output stage: {output_stage}")

    def _record_document_outputs(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        document_ids: tuple[str, ...],
        document_edits: tuple[DocumentFieldEdit, ...],
        actor: str = "mcp:github-copilot",
    ) -> dict[str, object]:
        task = self._tasks.get(coding_task_id)
        if not document_ids or len(document_ids) != len(set(document_ids)):
            raise ValueError("document_ids must be non-empty and unique")
        change_request = self._requests.get_change_request(task.change_request_id)
        case_id = change_request.get("analysis_case_id")
        if not isinstance(case_id, str):
            raise ValueError(
                "Copilot Change Task outputs require a bound Analysis Case before recording"
            )
        discovery = self._document_discovery_for_task(coding_task_id, task.change_request_id)
        source_snapshot_id = discovery.get("document_snapshot_id")
        candidates = discovery.get("candidates")
        candidate_document_ids = {
            str(candidate["document_id"])
            for candidate in (candidates if isinstance(candidates, list) else [])
            if isinstance(candidate, dict) and isinstance(candidate.get("document_id"), str)
        }
        if (
            discovery.get("status") != "ready"
            or not isinstance(source_snapshot_id, str)
            or not source_snapshot_id.strip()
        ):
            raise ValueError(
                "Document outputs require a ready Canonical RAG Snapshot; "
                "explicit file references alone are not sufficient"
            )
        if not set(document_ids).issubset(candidate_document_ids):
            raise ValueError("Document output is outside Canonical RAG candidate scope")
        document_changes = CopilotDocumentChangeService(
            connection=self._connection,
            repository_root=self._root,
        )
        if document_edits:
            materialized = document_changes.apply_and_materialize(
                project_id=task.project_id,
                analysis_case_id=case_id,
                coding_task_id=coding_task_id,
                source_snapshot_id=source_snapshot_id,
                document_ids=document_ids,
                document_edits=document_edits,
            )
        else:
            materialized = document_changes.materialize(
                project_id=task.project_id,
                analysis_case_id=case_id,
                coding_task_id=coding_task_id,
                source_snapshot_id=source_snapshot_id,
                document_ids=document_ids,
            )
        document_change_refs = materialized.change_refs
        search_index_build_id = discovery.get("search_index_build_id")
        if not isinstance(search_index_build_id, str) or not search_index_build_id.strip():
            raise ValueError("Document discovery has no Search Index evidence")
        output_refs: dict[str, object] = {
            "document_change_refs": list(document_change_refs),
            "document_ids": list(materialized.document_ids),
            "source_document_snapshot_id": materialized.source_snapshot_id,
            "target_document_snapshot_id": materialized.target_snapshot_id,
            "search_index_build_id": search_index_build_id,
        }
        self._tasks.record_change_outputs(
            coding_task_id=coding_task_id,
            actor=actor,
            output_stage="document_change",
            expected_stage="document_change",
            next_stage="code_scope",
            output_refs=output_refs,
        )
        return {
            **output_refs,
            "recorded_stage": "document_change",
            "next_stage": "code_scope",
            "coding_task_state": self._tasks.get(coding_task_id).state,
        }

    def _record_code_scope_output(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        code_scope: tuple[dict[str, Any], ...],
        actor: str = "mcp:github-copilot",
    ) -> dict[str, object]:
        task = self._tasks.get(coding_task_id)
        document_refs = self._recorded_output(coding_task_id, "document_change")
        document_change_refs = tuple(
            str(value) for value in cast(list[object], document_refs["document_change_refs"])
        )
        try:
            recorded_scope = self._recorded_output(coding_task_id, "code_scope")
        except ValueError:
            recorded_scope = {}
        current_task_has_recorded_scope = recorded_scope.get("output_stage") == "code_scope"
        case_id = self._bound_change_request_case(task.change_request_id)
        existing_impact = self._requests.impact_report(
            project_id=task.project_id,
            case_id=case_id,
        )
        automation_repository = ChangeAutomationRepository(self._connection)
        automation = automation_repository.latest_for_request(task.change_request_id)
        confirmation = (
            automation_repository.latest_confirmation(
                run_id=str(automation["automation_run_id"]),
                checkpoint="code_scope",
            )
            if automation is not None
            else None
        )
        revising_rejected_scope = _is_rejected_code_scope_revision(automation, confirmation)
        existing_impact_artifact: dict[str, Any] | None = None
        existing_impact_context: dict[str, Any] | None = None
        replacing_previous_task_impact = False
        if existing_impact is not None and not revising_rejected_scope:
            existing_impact_artifact = self._artifacts.get(str(existing_impact["impact_report_id"]))
            if existing_impact_artifact is None:
                raise RuntimeError("Existing Impact Report Artifact is missing")
            context_id = existing_impact_artifact.get("context_package_id")
            if not isinstance(context_id, str):
                raise RuntimeError("Existing Impact Report has no Context Package")
            existing_impact_context = self._artifacts.get(context_id)
            if existing_impact_context is None:
                raise RuntimeError("Existing Impact Context Artifact is missing")
            replacing_previous_task_impact = (
                not current_task_has_recorded_scope
                or existing_impact_context.get("coding_task_id") != coding_task_id
            )
        if existing_impact is None or revising_rejected_scope or replacing_previous_task_impact:
            impact = CopilotImpactService(
                connection=self._connection,
                repository_root=self._root,
            ).publish(
                project_id=task.project_id,
                analysis_case_id=case_id,
                change_request_id=task.change_request_id,
                coding_task_id=coding_task_id,
                workspace_root=workspace_root,
                source_document_snapshot_id=str(document_refs["source_document_snapshot_id"]),
                target_document_snapshot_id=str(document_refs["target_document_snapshot_id"]),
                search_index_build_id=str(document_refs["search_index_build_id"]),
                document_change_refs=document_change_refs,
                code_scope=code_scope,
                actor=actor,
                provider_id=(
                    "codex_fallback" if actor == "codex:fallback" else "vscode_github_copilot"
                ),
            )
        else:
            assert existing_impact is not None
            assert existing_impact_artifact is not None
            impact_change_refs = {
                str(reference)
                for item in cast(list[dict[str, Any]], existing_impact_artifact.get("items", []))
                for reference in cast(list[object], item.get("structured_change_refs", []))
            }
            requested_paths = {str(item.get("target_path") or "") for item in code_scope}
            impact_paths = {
                str(item.get("target_path") or "")
                for item in cast(list[dict[str, Any]], existing_impact_artifact.get("items", []))
            }
            if (
                impact_change_refs != set(document_change_refs)
                or not requested_paths
                or requested_paths != impact_paths
            ):
                raise ValueError(
                    "Existing Impact Report differs from Copilot document or code scope"
                )
            impact = {
                "created": False,
                "impact_report_id": existing_impact["impact_report_id"],
                "code_scope": code_scope,
            }
        output_refs = {
            **document_refs,
            "impact_report_id": impact["impact_report_id"],
            "code_scope": impact["code_scope"],
        }
        output_refs.pop("output_stage", None)
        next_stage = "compile_test" if task.approval_grant_id is not None else "code_scope"
        self._tasks.record_change_outputs(
            coding_task_id=coding_task_id,
            actor=actor,
            output_stage="code_scope",
            expected_stage="code_scope",
            next_stage=next_stage,
            output_refs=output_refs,
            revision_identity=(
                str(impact["impact_report_id"]) if revising_rejected_scope else None
            ),
        )
        return {
            **output_refs,
            "recorded_stage": "code_scope",
            "next_stage": "compile_test",
            "coding_task_state": self._tasks.get(coding_task_id).state,
        }

    def _record_test_planning_outputs(
        self,
        *,
        coding_task_id: str,
        test_plan: dict[str, Any],
        test_data_plan: dict[str, Any],
        actor: str = "mcp:github-copilot",
    ) -> dict[str, object]:
        task = self._tasks.get(coding_task_id)
        case_id, _edit_packet_id, _approval_grant_id = _bound_task_scope(task)
        if task.base_repository_revision is None:
            raise ValueError("Copilot Change Task has no bound Base Revision")
        approval = self._artifacts.get(str(task.approval_grant_id))
        if approval is None or approval.get("artifact_type") != "ApprovalGrant":
            raise RuntimeError("Copilot Change Task Approval Grant Artifact is missing")
        verification_only = not bool(approval.get("editable_files"))
        committed = GitWorktreeDiffInspector().inspect_committed(
            Path(task.workspace_root),
            base_sha=task.base_repository_revision,
            allow_unchanged_head=verification_only,
        )
        view = self._tasks.view(coding_task_id)
        if not any(
            result.get("validation_mode") == "committed"
            and (
                (result.get("status") == "in_scope" and bool(result.get("changed_paths")))
                or (
                    verification_only
                    and result.get("status") == "no_changes"
                    and not result.get("changed_paths")
                )
            )
            and result.get("tests_passed") is True
            and result.get("command_evidence_status") == "verified"
            and result.get("changed_line_coverage_status") in {"passed", "not_required"}
            and result.get("result_repository_revision") == committed.result_sha
            for result in cast(list[dict[str, Any]], view["edit_results"])
        ):
            raise ValueError(
                "TestPlan requires the current clean HEAD to match the committed code, "
                "compile/test evidence, and changed-line coverage"
            )
        _validate_planning_artifact_scope(
            artifact_name="UI TestPlan",
            artifact=test_plan,
            expected={
                "artifact_type": "TestPlan",
                "schema_version": "v2",
                "plan_kind": "ui",
                "project_id": task.project_id,
                "change_request_id": task.change_request_id,
                "status": "ready",
            },
        )
        test_plan_id = str(test_plan.get("test_plan_id") or "")
        _validate_planning_artifact_scope(
            artifact_name="TestDataPlan",
            artifact=test_data_plan,
            expected={
                "artifact_type": "TestDataPlan",
                "schema_version": "v2",
                "project_id": task.project_id,
                "test_plan_id": test_plan_id,
                "status": "ready",
            },
        )
        test_data_plan_id = str(test_data_plan.get("test_data_plan_id") or "")
        if not test_plan_id or not test_data_plan_id:
            raise ValueError("Change Task output identities must not be blank")
        required_commands = {
            str(value)
            for value in cast(list[object], approval.get("allowed_test_command_refs", []))
        }
        passed_commands = {
            str(command["command_ref"])
            for command in cast(list[dict[str, Any]], view["commands"])
            if command.get("status") == "passed" and command.get("exit_code") == 0
        }
        if not required_commands or not required_commands.issubset(passed_commands):
            missing = sorted(required_commands - passed_commands)
            raise ValueError(
                "UI TestPlan must be generated only after every required compile/test command "
                f"passes; missing={missing or ['required command profile']}"
            )
        code_refs = self._code_scope_output(coding_task_id)
        impact = self._artifacts.get(str(code_refs["impact_report_id"]))
        if impact is None or impact.get("artifact_type") != "ImpactReport":
            raise RuntimeError("Copilot Change Task Impact Report Artifact is missing")
        _validate_planning_alignment(
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            ui_impacted=impact.get("ui_impact_status") == "impacted",
        )
        _validate_required_ui_scenario_scope(test_plan=test_plan, impact=impact)
        blockers = validate_test_data_plan_artifact(test_data_plan)
        if "sql" in test_data_plan_channels(test_data_plan):
            blockers.extend(
                _project_target_data_blockers(
                    connection=self._connection,
                    project_id=task.project_id,
                    plan=test_data_plan,
                )
            )
        if test_data_plan_channels(test_data_plan) & {"http", "ui"} and not (
            self._requests.project_test_base_url(task.project_id)
        ):
            blockers.append("Project has no test_base_url for HTTP/UI TestDataPlan execution")
        if blockers:
            raise ValueError("TestDataPlan is not executable: " + "; ".join(blockers))
        request_record = self._requests.get_change_request(task.change_request_id)
        request_artifact = cast(dict[str, Any], request_record["artifact"])
        coverage = assess_planned_business_coverage(
            request=request_artifact,
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            scoped_test_files=frozenset(
                str(value) for value in cast(list[object], approval.get("test_files", []))
            ),
            passed_command_refs=frozenset(passed_commands),
            canonical_artifact_refs=canonical_artifact_refs_from_output(code_refs),
            required_ui_scenario_refs=tuple(
                str(value)
                for value in cast(list[object], impact.get("required_ui_scenario_refs", []))
            ),
        )
        if coverage["status"] != "passed" or coverage["coverage_percent"] != 100:
            uncovered = uncovered_business_rules(
                request=request_artifact,
                assessment=coverage,
            )
            raise ValueError(
                "Business coverage gate failed before human confirmation: "
                f"coverage_percent={coverage['coverage_percent']}; "
                "uncovered_business_rules="
                f"{json.dumps(uncovered, ensure_ascii=False, sort_keys=True)}. "
                "Regenerate the complete UI TestPlan and TestDataPlan, then resubmit them."
            )
        self._artifacts.store(
            artifact_id=test_plan_id,
            project_id=task.project_id,
            analysis_case_id=case_id,
            artifact=test_plan,
        )
        self._artifacts.store(
            artifact_id=test_data_plan_id,
            project_id=task.project_id,
            analysis_case_id=case_id,
            artifact=test_data_plan,
        )
        output_refs: dict[str, object] = {
            **code_refs,
            "test_plan_id": test_plan_id,
            "test_data_plan_id": test_data_plan_id,
        }
        output_refs.pop("output_stage", None)
        self._tasks.record_change_outputs(
            coding_task_id=coding_task_id,
            actor=actor,
            output_stage="test_planning",
            expected_stage="test_planning",
            next_stage="ui_validation",
            output_refs=output_refs,
            complete=True,
        )
        return {
            **output_refs,
            "recorded_stage": "test_planning",
            "next_stage": "ui_validation",
            "coding_task_state": self._tasks.get(coding_task_id).state,
        }

    def _record_ui_test_revision_outputs(
        self,
        *,
        coding_task_id: str,
        test_plan: dict[str, Any],
        test_data_plan: dict[str, Any],
        actor: str = "mcp:github-copilot",
    ) -> dict[str, object]:
        from operamind.application.test_case_revision_service import (
            TestCaseRevisionService,
        )

        task = self._tasks.get(coding_task_id)
        immutable_task = cast(dict[str, object], self._tasks.view(coding_task_id)["task"])
        if immutable_task.get("task_kind") != "ui_test_plan_revision":
            raise ValueError("Only a UI TestPlan revision Task accepts this output stage")
        context = cast(dict[str, object], immutable_task["plan_revision_context"])
        source_bundle = ChangeOrchestrationRepository(self._connection, self._contracts).bundle(
            str(context["source_orchestration_id"])
        )
        source_plan = cast(dict[str, Any], source_bundle["test_plan"])
        if source_plan.get("test_plan_id") != context["source_test_plan_id"]:
            raise ValueError("UI TestPlan revision source has changed")
        _validate_planning_artifact_scope(
            artifact_name="UI TestPlan",
            artifact=test_plan,
            expected={
                "artifact_type": "TestPlan",
                "schema_version": "v2",
                "plan_kind": "ui",
                "project_id": task.project_id,
                "change_request_id": task.change_request_id,
                "status": "ready",
            },
        )
        _validate_planning_artifact_scope(
            artifact_name="TestDataPlan",
            artifact=test_data_plan,
            expected={
                "artifact_type": "TestDataPlan",
                "schema_version": "v2",
                "project_id": task.project_id,
                "test_plan_id": str(test_plan.get("test_plan_id") or ""),
                "status": "ready",
            },
        )
        _validate_planning_alignment(
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            ui_impacted=True,
        )
        blockers = validate_test_data_plan_artifact(test_data_plan)
        if "sql" in test_data_plan_channels(test_data_plan):
            blockers.extend(
                _project_target_data_blockers(
                    connection=self._connection,
                    project_id=task.project_id,
                    plan=test_data_plan,
                )
            )
        if test_data_plan_channels(test_data_plan) & {"http", "ui"} and not (
            self._requests.project_test_base_url(task.project_id)
        ):
            blockers.append("Project has no test_base_url for HTTP/UI TestDataPlan execution")
        if blockers:
            raise ValueError("Regenerated TestDataPlan is not executable: " + "; ".join(blockers))
        applied = TestCaseRevisionService(
            connection=self._connection,
            repository_root=self._root,
        ).apply_ai_regeneration(
            change_request_id=task.change_request_id,
            proposal_id=str(context["proposal_id"]),
            source_orchestration_id=str(context["source_orchestration_id"]),
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            operations=cast(
                list[dict[str, Any]],
                json.loads(str(context["confirmed_operations_json"])),
            ),
            selections=cast(dict[str, str], json.loads(str(context["selections_json"]))),
            actor=actor,
        )
        revision = cast(dict[str, Any], applied["revision"])
        output_refs: dict[str, object] = {
            "revision_id": revision["revision_id"],
            "orchestration_id": revision["target_orchestration_id"],
            "test_plan_id": revision["target_test_plan_id"],
            "test_data_plan_id": cast(dict[str, Any], applied["bundle"])["test_data_plan"][
                "test_data_plan_id"
            ],
        }
        self._tasks.record_change_outputs(
            coding_task_id=coding_task_id,
            actor=actor,
            output_stage="ui_test_revision",
            expected_stage="ui_test_revision",
            next_stage="ui_validation",
            output_refs=output_refs,
            complete=True,
        )
        return {
            **output_refs,
            "recorded_stage": "ui_test_revision",
            "next_stage": "ui_validation",
            "coding_task_state": self._tasks.get(coding_task_id).state,
        }

    def _recorded_output(self, coding_task_id: str, output_stage: str) -> dict[str, object]:
        view = self._tasks.view(coding_task_id)
        event = next(
            (
                item
                for item in reversed(cast(list[dict[str, Any]], view["events"]))
                if item.get("event_type") == "outputs_recorded"
                and cast(dict[str, object], item.get("payload", {})).get("output_stage")
                == output_stage
            ),
            None,
        )
        if event is None:
            raise ValueError(f"Copilot Change Task has no recorded {output_stage} output")
        return dict(cast(dict[str, object], event["payload"]))

    def _code_scope_output(self, coding_task_id: str) -> dict[str, object]:
        """Return Graph scope evidence, including an approved follow-up execution basis."""

        try:
            return self._recorded_output(coding_task_id, "code_scope")
        except ValueError:
            task = cast(dict[str, object], self._tasks.view(coding_task_id)["task"])
            basis = task.get("execution_basis")
            if task.get("task_kind") != "change_execution" or not isinstance(basis, dict):
                raise
            return dict(cast(dict[str, object], basis))

    def _public_document_changes(self, coding_task_id: str) -> dict[str, object]:
        """Return the confirmed business diff needed by later Copilot stages."""

        try:
            output = self._recorded_output(coding_task_id, "document_change")
        except ValueError:
            output = self._code_scope_output(coding_task_id)
        references = [
            str(value) for value in cast(list[object], output.get("document_change_refs", []))
        ]
        changes: list[dict[str, object]] = []
        for reference in references:
            artifact = self._artifacts.get(reference)
            if artifact is None or artifact.get("artifact_type") != "StructuredChange":
                raise RuntimeError(
                    f"Copilot Change Task StructuredChange input is missing: {reference}"
                )
            changes.append(
                {
                    key: copy.deepcopy(artifact[key])
                    for key in (
                        "change_id",
                        "stable_key",
                        "fact_type",
                        "domain",
                        "change_type",
                        "before",
                        "after",
                        "summary",
                        "confidence",
                        "unknowns",
                    )
                    if key in artifact
                }
            )
        return {"artifact_refs": references, "changes": changes}

    def _bound_change_request_case(self, change_request_id: str) -> str:
        case_id = self._requests.get_change_request(change_request_id).get("analysis_case_id")
        if not isinstance(case_id, str):
            raise ValueError("Copilot Change Task requires a bound Analysis Case")
        return case_id

    def validate_diff(
        self, *, coding_task_id: str, edit_result_id: str, workspace_root: Path
    ) -> dict[str, object]:
        result = self._record_edit_result(
            coding_task_id=coding_task_id,
            edit_result_id=edit_result_id,
            workspace_root=workspace_root,
            mode=EditValidationMode.WORKING,
            test_result_refs=(),
            tests_passed=None,
        )
        return {
            **result,
            "committed_edit_result_id": f"{edit_result_id}-committed",
        }

    def is_verification_only(self, coding_task_id: str) -> bool:
        """Return whether the confirmed execution scope intentionally has no writable files."""

        task = self._tasks.get(coding_task_id)
        approval = self._artifacts.get(str(task.approval_grant_id))
        if approval is None or approval.get("artifact_type") != "ApprovalGrant":
            raise RuntimeError("Copilot Change Task Approval Grant Artifact is missing")
        return not bool(approval.get("editable_files"))

    def record_result(
        self,
        *,
        coding_task_id: str,
        edit_result_id: str,
        workspace_root: Path,
        test_result_refs: tuple[str, ...],
        tests_passed: bool,
        coverage_report_command_execution_id: str | None = None,
        actor: str = "mcp:github-copilot",
    ) -> dict[str, object]:
        task = self._tasks.get(coding_task_id)
        if task.base_repository_revision is None:
            raise ValueError("Copilot Change Task has no bound Base Revision")
        approval = self._artifacts.get(str(task.approval_grant_id))
        if approval is None or approval.get("artifact_type") != "ApprovalGrant":
            raise RuntimeError("Copilot Change Task Approval Grant Artifact is missing")
        verification_only = not bool(approval.get("editable_files"))
        committed = GitWorktreeDiffInspector().inspect_committed(
            workspace_root,
            base_sha=task.base_repository_revision,
            allow_unchanged_head=verification_only,
        )
        command_events = {
            str(payload.get("command_execution_id")): payload
            for event in cast(list[dict[str, Any]], self._tasks.view(coding_task_id)["events"])
            if event.get("event_type") == "command_recorded"
            and isinstance((payload := event.get("payload")), dict)
        }
        missing = sorted(set(test_result_refs) - set(command_events))
        stale = sorted(
            command_id
            for command_id in test_result_refs
            if command_id in command_events
            and command_events[command_id].get("tested_content_digest") != committed.content_digest
        )
        if missing or stale:
            raise ValueError(
                "Committed result is not bound to command evidence for the exact tested diff: "
                f"missing={missing}; stale={stale}"
            )
        changed_line_coverage: ChangedLineCoverageEvidence | None = None
        if coverage_report_command_execution_id is not None:
            if coverage_report_command_execution_id not in test_result_refs:
                raise ValueError("Coverage report command must be included in test_result_refs")
            event = command_events.get(coverage_report_command_execution_id)
            report = event.get("coverage_report") if event is not None else None
            if not isinstance(report, dict):
                raise ValueError("Selected command has no approved coverage report evidence")
            changed_line_coverage = load_coverage_report(
                workspace_root=workspace_root,
                report_path=str(report.get("path") or ""),
                report_format=str(report.get("format") or ""),
                expected_digest=str(report.get("digest") or ""),
                evidence_ref=coverage_report_command_execution_id,
            )
        return self._record_edit_result(
            coding_task_id=coding_task_id,
            edit_result_id=edit_result_id,
            workspace_root=workspace_root,
            mode=EditValidationMode.COMMITTED,
            test_result_refs=test_result_refs,
            tests_passed=tests_passed,
            changed_line_coverage=changed_line_coverage,
            actor=actor,
        )

    def _record_edit_result(
        self,
        *,
        coding_task_id: str,
        edit_result_id: str,
        workspace_root: Path,
        mode: EditValidationMode,
        test_result_refs: tuple[str, ...],
        tests_passed: bool | None,
        changed_line_coverage: ChangedLineCoverageEvidence | None = None,
        actor: str = "mcp:github-copilot",
    ) -> dict[str, object]:
        task = self._tasks.get(coding_task_id)
        case_id, edit_packet_id, approval_grant_id = _bound_task_scope(task)
        result = (
            EditResultService(connection=self._connection, contracts=self._contracts)
            .run(
                EditResultRequest(
                    edit_result_id=edit_result_id,
                    edit_packet_id=edit_packet_id,
                    approval_grant_id=approval_grant_id,
                    project_id=task.project_id,
                    analysis_case_id=case_id,
                    workspace_root=workspace_root,
                    mode=mode,
                    test_result_refs=test_result_refs,
                    tests_passed=tests_passed,
                    changed_line_coverage=changed_line_coverage,
                )
            )
            .to_dict()
        )
        self._tasks.bind_edit_result(
            coding_task_id=coding_task_id,
            edit_result_id=edit_result_id,
            actor=actor,
            result=result,
            committed=mode is EditValidationMode.COMMITTED,
        )
        return {**result, "coding_task_state": self._tasks.get(coding_task_id).state}


def _validate_planning_alignment(
    *,
    test_plan: dict[str, Any],
    test_data_plan: dict[str, Any],
    ui_impacted: bool,
) -> None:
    test_cases = cast(list[dict[str, Any]], test_plan.get("test_cases", []))
    case_ids = [str(case.get("test_case_id") or "") for case in test_cases]
    if not case_ids or any(not value for value in case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("TestPlan test_case_id values must be non-empty and unique")
    ui_case_ids = {str(case["test_case_id"]) for case in test_cases if case.get("level") == "ui"}
    if ui_case_ids != set(case_ids) or any(
        case.get("execution_mode") != "browser" for case in test_cases
    ):
        raise ValueError("UI TestPlan may contain only browser UI test cases")
    # The Code Graph flag describes whether the changed files directly touch UI
    # code.  The product workflow still requires end-to-end browser validation
    # for backend-only changes, so it must not make a non-empty UI plan
    # impossible.  Keep the argument for compatibility with existing callers.
    del ui_impacted
    case_step_ids: dict[str, set[str]] = {}
    all_test_step_ids: set[str] = set()
    for test_case in test_cases:
        case_id = str(test_case["test_case_id"])
        natural_steps = cast(list[object], test_case.get("steps", []))
        step_ids = [str(value) for value in cast(list[object], test_case.get("step_ids", []))]
        if len(step_ids) != len(natural_steps) or any(not value for value in step_ids):
            raise ValueError(
                f"Every TestPlan natural-language step requires one parallel step_id: {case_id}"
            )
        if len(step_ids) != len(set(step_ids)) or all_test_step_ids.intersection(step_ids):
            raise ValueError("TestPlan step_id values must be globally unique")
        for natural_step in natural_steps:
            if not _looks_like_natural_language(natural_step):
                raise ValueError(
                    "TestPlan steps must be natural-language actions, not opaque identifiers: "
                    f"{case_id}"
                )
        case_step_ids[case_id] = set(step_ids)
        all_test_step_ids.update(step_ids)
    data_sets = cast(list[dict[str, Any]], test_data_plan.get("data_sets", []))
    data_ids = [str(item.get("test_data_id") or "") for item in data_sets]
    if not data_ids or any(not value for value in data_ids) or len(data_ids) != len(set(data_ids)):
        raise ValueError("TestDataPlan test_data_id values must be non-empty and unique")
    data_id_set = set(data_ids)
    for test_case in test_cases:
        refs = {str(value) for value in cast(list[object], test_case.get("test_data_refs", []))}
        if not refs or not refs.issubset(data_id_set):
            raise ValueError(
                f"Test case has missing TestDataPlan data refs: {test_case['test_case_id']}"
            )
    flows = cast(list[dict[str, Any]], test_data_plan.get("generation_flows", []))
    covered_cases = {
        str(value) for flow in flows for value in cast(list[object], flow.get("test_case_refs", []))
    }
    if covered_cases != set(case_ids):
        raise ValueError("TestDataPlan flows must cover exactly every TestPlan case")
    executable_refs: set[str] = set()
    for flow in flows:
        flow_case_ids = {str(value) for value in cast(list[object], flow.get("test_case_refs", []))}
        allowed_refs = {
            step_id for case_id in flow_case_ids for step_id in case_step_ids.get(case_id, set())
        }
        for step in cast(list[dict[str, Any]], flow.get("steps", [])):
            if not _looks_like_natural_language(step.get("business_action")):
                raise ValueError(
                    "TestDataPlan business_action must describe the executable action in "
                    f"natural language: {step.get('step_id')}"
                )
            refs = {str(value) for value in cast(list[object], step.get("test_step_refs", []))}
            if not refs.issubset(allowed_refs):
                raise ValueError(
                    "TestDataPlan step references a TestPlan step outside its flow: "
                    f"{step.get('step_id')}"
                )
            if step.get("channel") == "ui":
                if not isinstance(step.get("playwright"), dict):
                    raise ValueError(
                        f"Referenced UI step has no Playwright action: {step.get('step_id')}"
                    )
                executable_refs.update(refs)
            elif refs:
                raise ValueError(
                    "Only executable Playwright UI steps may reference TestPlan natural-language "
                    f"steps: {step.get('step_id')}"
                )
        for step in cast(list[dict[str, Any]], flow.get("cleanup_steps", [])):
            if not _looks_like_natural_language(step.get("business_action")):
                raise ValueError(
                    "Cleanup business_action must describe the executable action in natural "
                    f"language: {step.get('step_id')}"
                )
    missing_executable_refs = all_test_step_ids - executable_refs
    if missing_executable_refs:
        raise ValueError(
            "Every TestPlan natural-language step requires a corresponding executable "
            "Playwright UI step; missing refs: " + ", ".join(sorted(missing_executable_refs))
        )
    for case_id in ui_case_ids:
        matching = [
            flow
            for flow in flows
            if case_id
            in {str(value) for value in cast(list[object], flow.get("test_case_refs", []))}
        ]
        if not matching:
            raise ValueError(f"UI test case has no TestDataPlan flow: {case_id}")
        if not any(
            any(
                step.get("channel") == "ui" and isinstance(step.get("playwright"), dict)
                for step in cast(list[dict[str, Any]], flow["steps"])
            )
            and any(
                assertion.get("observe_via") == "ui"
                for assertion in [
                    *cast(list[dict[str, Any]], flow["final_assertions"]),
                    *[
                        item
                        for step in cast(list[dict[str, Any]], flow["steps"])
                        for item in cast(list[dict[str, Any]], step["postconditions"])
                    ],
                ]
            )
            for flow in matching
        ):
            raise ValueError(
                "UI test case requires an executable Playwright UI step and bounded UI "
                f"assertion: {case_id}"
            )
    coverage_reasons = validate_test_data_coverage_alignment(
        test_plan=test_plan,
        test_data_plan=test_data_plan,
    )
    if coverage_reasons:
        raise ValueError(
            "Test data coverage alignment failed: " + "; ".join(coverage_reasons)
        )


def _validate_required_ui_scenario_scope(
    *, test_plan: dict[str, Any], impact: dict[str, Any]
) -> None:
    expected = {
        str(value) for value in cast(list[object], impact.get("required_ui_scenario_refs", []))
    }
    actual = {
        str(case["test_case_id"])
        for case in cast(list[dict[str, Any]], test_plan.get("test_cases", []))
        if case.get("level") == "ui"
    }
    if actual != expected:
        raise ValueError(
            "UI TestPlan scenario IDs must exactly match the confirmed Impact scope: "
            f"missing={sorted(expected - actual)}; unexpected={sorted(actual - expected)}"
        )


def _looks_like_natural_language(value: object) -> bool:
    """Reject opaque IDs/selectors while accepting concise Japanese, Chinese, or word phrases."""

    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) < 2 or any(ord(character) < 32 for character in text):
        return False
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text):
        return True
    return len(re.findall(r"[A-Za-z]+", text)) >= 2


def _payload_digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _validate_planning_artifact_scope(
    *,
    artifact_name: str,
    artifact: dict[str, Any],
    expected: dict[str, str],
) -> None:
    mismatches = [
        f"{key} must be {expected_value!r} (received {artifact.get(key)!r})"
        for key, expected_value in expected.items()
        if artifact.get(key) != expected_value
    ]
    if mismatches:
        raise ValueError(f"{artifact_name} scope mismatch: " + "; ".join(mismatches))


def _bound_task_scope(
    task: Any,
) -> tuple[str, str, str]:
    values = (task.analysis_case_id, task.edit_packet_id, task.approval_grant_id)
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("Copilot Change Task code execution scope is not bound")
    return cast(tuple[str, str, str], values)


def _public_task_artifact(task: dict[str, object]) -> dict[str, object]:
    """Project the immutable task without internal authorization identifiers."""

    return {
        key: task[key]
        for key in (
            "coding_task_id",
            "change_request_id",
            "project_id",
            "execution_mode",
            "task_kind",
            "initial_stage",
            "task_summary",
            "change_context",
            "target_project",
            "workflow",
            "output_protocol",
            "required_mcp_tools",
            "attempt_number",
        )
        if key in task
    }


def _public_mcp_task(task: dict[str, object]) -> dict[str, object]:
    """Expose only task identity; stage-specific business data lives in inputs."""

    return {
        key: task[key]
        for key in (
            "coding_task_id",
            "change_request_id",
            "project_id",
            "task_summary",
            "attempt_number",
        )
        if key in task
    }


def _public_requirement_context(task: dict[str, object]) -> dict[str, object]:
    value = task.get("change_context")
    context = cast(dict[str, object], value) if isinstance(value, dict) else {}
    return {
        key: copy.deepcopy(context[key])
        for key in (
            "requirement_text",
            "source_document_ref",
            "target_document_ref",
            "business_rules",
            "ambiguity_status",
        )
        if key in context
    }


def _ready_stage_status(*, task_stage: str, task_state: str) -> dict[str, object]:
    return {
        "task_stage": task_stage,
        "task_state": task_state,
        "outcome": "ready",
        "requires_confirmation": False,
        "next_action": "perform_current_stage",
        "message": "現在工程の入力と制約を取得しました。",
        "blocking_reasons": [],
    }


def _public_target_data_profile(profile: TargetDataProfile | None) -> dict[str, object]:
    """Expose binding names and input contracts to Copilot, never SQL or secrets."""

    if profile is None:
        return {
            "available": False,
            "connection_alias": None,
            "transaction_policy": None,
            "bindings": [],
        }
    return {
        "available": True,
        "connection_alias": profile.connection_alias,
        "transaction_policy": profile.transaction_policy,
        "bindings": [
            {
                "query_binding_id": value.query_binding_id,
                "operation": value.operation,
                "target": f"{value.target_schema}.{value.target_table}",
                "input_constraints": {
                    key: dict(item) for key, item in value.input_constraints.items()
                },
                "read_assertion": dict(value.read_assertion),
                "identity_contract": dict(value.identity_contract),
                "cleanup_binding_id": value.cleanup_binding_id,
                "idempotency_policy": value.idempotency_policy,
            }
            for value in profile.bindings
        ],
    }


def _project_target_data_blockers(
    *, connection: Connection[Any], project_id: str, plan: Mapping[str, object]
) -> list[str]:
    repository = TargetDataProfileRepository(connection)
    reasons = repository.validate_plan(project_id=project_id, plan=plan)
    if "sql" not in test_data_plan_channels(cast(dict[str, Any], plan)):
        return reasons
    profile = repository.get(project_id)
    if profile is not None and not TargetDataSecretStore().configured(
        project_id=project_id,
        connection_alias=profile.connection_alias,
    ):
        reasons.append(
            "Project Target Data connection Secret is not configured for SQL execution"
        )
    return sorted(set(reasons))


def _stage_contract(stage: str, *, verification_only: bool = False) -> dict[str, object]:
    contracts: dict[str, dict[str, object]] = {
        "document_change": {
            "label": "設計書変更",
            "goal": "Canonical RAG の対象設計書を業務要件に合わせて更新する。",
            "input_fields": ["requirement", "document_discovery"],
            "output": {
                "tool": "copilot_record_change_outputs",
                "output_stage": "document_change",
            },
            "stop_condition": "設計書差分の記録後は人工確認を待つ。",
            "rules": [
                "document_discovery の ready 候補だけを使う。",
                "XLSX は canonical_document の stable_key、field、new_value で限定更新する。",
            ],
        },
        "code_scope": {
            "label": "コード影響範囲",
            "goal": "確認済み設計差分に対応するコードとテストを特定する。",
            "input_fields": ["requirement", "design_changes", "document_discovery"],
            "output": {
                "tool": "copilot_record_change_outputs",
                "output_stage": "code_scope",
            },
            "stop_condition": "影響範囲の記録後は人工確認を待つ。",
            "rules": [
                "Workspace のコードを変更せず読み取る。",
                "Code Graph で検証できる Path、Symbol、Test、根拠だけを返す。",
            ],
        },
        "compile_test": {
            "label": "コード変更・コンパイル・テスト",
            "goal": (
                "確認済み範囲だけを変更し、差分、必須 Command、変更行 Coverage、commit を検証する。"
            ),
            "input_fields": ["requirement", "design_changes", "execution_scope"],
            "output": {"tool": "copilot_record_task_result"},
            "stop_condition": (
                "stage_status.next_action が reload_current_task なら Task を再取得する。"
            ),
            "rules": [
                (
                    "編集可能 Path と Test Path のみを変更し、差分検証後に"
                    "全 required_command_refs を実行する。"
                ),
                "全 Command 成功後に commit し、committed_edit_result_id と Evidence を記録する。",
            ],
        },
        "test_planning": {
            "label": "UI テスト計画",
            "goal": "全業務要件を覆う実ブラウザ用 UI TestPlan と TestDataPlan を作成する。",
            "input_fields": ["requirement", "design_changes", "planning"],
            "output": {
                "tool": "copilot_record_change_outputs",
                "output_stage": "test_planning",
            },
            "stop_condition": "業務 Coverage 100% で計画受理後は人工確認を待つ。",
            "rules": [
                "copilot_record_change_outputs の inputSchema を計画形式の正とする。",
                (
                    "全 test_data_id に SQL readback identity_binding を定義し、"
                    "全 UI Step の operation_scope を明示し、record UI Step は"
                    " bound_record と data_binding_ref で一意に scope する。"
                ),
                (
                    "全 AcceptanceCriteria/TestCase/TestData の組合せに、実 SQL"
                    " readback を使う coverage_conditions を定義する。Coverage は"
                    "自己申告せず OperaMind の実行結果に従う。"
                ),
                "不足要件が返った場合は両計画の完全版を再送信する。",
            ],
        },
        "ui_test_revision": {
            "label": "UI テスト計画の再作成",
            "goal": "確認済み自然言語修正を反映し、完全な両計画を再作成する。",
            "input_fields": [
                "source_ui_test_plan",
                "source_test_data_plan",
                "revision_instruction",
                "confirmed_change_summary",
            ],
            "output": {
                "tool": "copilot_record_change_outputs",
                "output_stage": "ui_test_revision",
            },
            "stop_condition": "再作成した計画の記録後は人工確認を待つ。",
            "rules": [
                "コード、設計書、Git 履歴を変更しない。",
                "既存の identity_binding と data_binding_ref の決定性を維持する。",
                (
                    "全 AcceptanceCriteria/TestCase/TestData に対応する"
                    " coverage_conditions を完全版で維持し、Coverage を自己申告しない。"
                ),
                "Playwright を優先し、確認済みの capability gap だけを fallback にする。",
            ],
        },
    }
    if stage not in contracts:
        raise ValueError(f"Unsupported Copilot Change Task stage: {stage}")
    result = copy.deepcopy(contracts[stage])
    result["id"] = stage
    if stage == "compile_test" and verification_only:
        result["goal"] = "ファイルを変更せず、差分なしと必須 Command の成功を検証する。"
        result["rules"] = [
            "ファイルを変更せず、copilot_validate_task_diff の no_changes を必須とする。",
            *cast(list[str], result["rules"])[1:],
        ]
    return result


def _is_rejected_code_scope_revision(
    automation: dict[str, object] | None,
    confirmation: dict[str, object] | None,
) -> bool:
    """Allow revision only for the currently blocked, explicitly rejected scope."""

    return bool(
        automation is not None
        and automation.get("current_stage") == "impact_confirmation"
        and automation.get("status") == "blocked"
        and automation.get("next_action") == "resolve_blocker"
        and confirmation is not None
        and confirmation.get("checkpoint") == "code_scope"
        and confirmation.get("decision") == "rejected"
    )


def build_bridge_task_view(view: dict[str, object]) -> dict[str, object]:
    """Project one Bridge notification without claims or authorization records."""

    return {
        "task": _public_task_artifact(cast(dict[str, object], view["task"])),
        "state": view.get("state"),
        "attempt_number": view.get("attempt_number"),
        "current_stage": view.get("current_stage"),
    }


def _public_execution_scope(
    packet: dict[str, object],
    approval: dict[str, object],
) -> dict[str, object]:
    """Expose effective constraints, never the Packet or Approval records."""

    allowed_items = [
        {
            key: item[key]
            for key in (
                "target_path",
                "target_symbols",
                "allowed_actions",
                "business_summary",
                "implementation_constraints",
            )
            if key in item
        }
        for item in cast(list[dict[str, object]], packet.get("allowed_items", []))
    ]
    return {
        "bound": True,
        "base_repository_revision": packet.get("base_repository_revision"),
        "editable_files": packet.get("editable_files", []),
        "read_only_files": packet.get("read_only_files", []),
        "test_files": packet.get("test_files", []),
        "forbidden_globs": packet.get("forbidden_globs", []),
        "allowed_items": allowed_items,
        "required_command_refs": approval.get("allowed_test_command_refs", []),
        "out_of_scope_policy": packet.get("out_of_scope_policy"),
    }


def _public_workspace(workspace: dict[str, object]) -> dict[str, object]:
    """Expose the active local workspace without repository registration details."""

    return {
        key: workspace[key]
        for key in (
            "root",
            "isolated_worktree",
            "head_revision",
            "changed_paths",
            "result_committed",
        )
        if key in workspace
    }


def _public_document_discovery(discovery: dict[str, object]) -> dict[str, object]:
    """Expose selected document candidates without index implementation identifiers."""

    candidates = [
        {
            key: candidate[key]
            for key in (
                "document_id",
                "section_id",
                "heading_path",
                "summary",
                "logical_name",
                "document_ref",
                "canonical_document",
                "relevance_reason",
                "evidence_refs",
            )
            if key in candidate
        }
        for candidate in cast(
            list[dict[str, object]],
            discovery.get("candidates", []),
        )
    ]
    return {
        "status": discovery.get("status"),
        "mode": discovery.get("mode"),
        "explicit_document_refs": discovery.get("explicit_document_refs", []),
        "candidates": candidates,
        "blocking_reason": discovery.get("blocking_reason"),
    }


def _public_canonical_document(document: CanonicalDocumentSlice) -> dict[str, object]:
    """Expose complete normalized business content without parser-internal provenance."""

    result: dict[str, object] = {
        "document_id": document.document_id,
        "logical_name": document.logical_name,
        "document_ref": document.source_ref,
        "facts": [_public_canonical_fact(item.fact) for item in document.snapshot.facts],
    }
    optional = {
        "document_version_id": getattr(document, "document_version_id", None),
        "content_digest": getattr(document, "content_digest", None),
        "extractor_ref": getattr(document, "extractor_ref", None),
        "canonical_snapshot_id": getattr(document.snapshot, "snapshot_id", None),
    }
    result.update({key: value for key, value in optional.items() if value is not None})
    return result


def _public_canonical_fact(fact: Any) -> dict[str, object]:
    result: dict[str, object] = {
        "stable_key": fact.stable_key,
        "fact_type": fact.fact_type,
        "values": dict(fact.values),
    }
    if hasattr(fact, "source_refs"):
        result["source_refs"] = list(fact.source_refs)
    if hasattr(fact, "field_evidence"):
        result["field_evidence"] = [
            {
                "canonical_field": evidence.canonical_field,
                "source_aliases": list(evidence.source_aliases),
                "source_refs": list(evidence.source_refs),
            }
            for evidence in fact.field_evidence
        ]
    return result
