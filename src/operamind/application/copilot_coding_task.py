"""Transport-neutral Copilot Coding Task orchestration for the local POC Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from psycopg import Connection

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
from operamind.application.edit_result import (
    EditResultRequest,
    EditResultService,
    EditValidationMode,
)
from operamind.application.hybrid_search import (
    RequirementDocumentDiscoveryRequest,
    RequirementDocumentDiscoveryService,
)
from operamind.application.project_stack import detect_project_stack
from operamind.contracts import ContractCatalog
from operamind.infrastructure.embeddings import OpenAICompatibleEmbeddingProvider
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.canonical_repository import (
    CanonicalDocumentSlice,
    CanonicalRepository,
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

    def __post_init__(self) -> None:
        values = (
            self.coding_task_id,
            self.change_request_id,
            self.project_id,
            self.task_summary,
            self.actor,
            self.idempotency_key,
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
            "execution_mode": "copilot_change_task",
            "initial_stage": "document_change",
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
        record = self._tasks.publish(
            artifact=artifact,
            workspace_root=request.workspace_root,
            idempotency_key=request.idempotency_key,
        )
        view = self._tasks.view(record.coding_task_id)
        return {"created": record.created, **view}

    def claim_next(self, *, workspace_root: Path, consumer_id: str) -> dict[str, object] | None:
        if not consumer_id.strip():
            raise ValueError("Bridge consumer_id must not be blank")
        return self._tasks.claim_next(workspace_root=workspace_root, consumer_id=consumer_id)

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

    def latest_for_request(self, change_request_id: str) -> dict[str, object] | None:
        return self._tasks.latest_for_request(change_request_id)

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

    def get_mcp_context(self, *, coding_task_id: str, workspace_root: Path) -> dict[str, object]:
        task = self._tasks.begin_mcp(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            actor="mcp:github-copilot",
        )
        task_view = self._tasks.view(coding_task_id)
        if task.approval_grant_id is None:
            if task.current_stage == "document_change":
                document_discovery = self._document_discovery(task.change_request_id)
                discovery_ready = document_discovery["status"] == "ready"
                steps = [
                    "Read the requirement and business rules.",
                    (
                        "Use only the Canonical RAG candidates in document_discovery."
                        if discovery_ready
                        else "Stop before editing and report the document_discovery blocker."
                    ),
                    (
                        "For XLSX documents, derive bounded field updates from each "
                        "canonical_document fact; OperaMind applies them without requiring "
                        "the source file inside the code Workspace."
                    ),
                    (
                        "Call copilot_record_change_outputs with "
                        "output_stage=document_change, document_ids, and document_edits."
                    ),
                ]
            elif task.current_stage == "code_scope":
                document_discovery = self._document_discovery(task.change_request_id)
                steps = [
                    "Read the recorded design-document diff returned by OperaMind.",
                    "Inspect the code Workspace without modifying it.",
                    (
                        "Propose only Graph-verifiable production paths, symbols, test files, "
                        "actions, rationales, and whether each item affects UI."
                    ),
                    (
                        "Call copilot_record_change_outputs with "
                        "output_stage=code_scope and code_scope."
                    ),
                    "Wait for OperaMind to validate and bind the exact code execution scope.",
                ]
            else:
                raise ValueError(
                    "Unbound Copilot Change Task has an invalid current stage: "
                    f"{task.current_stage}"
                )
            return {
                "coding_task": _public_task_artifact(
                    cast(dict[str, object], task_view["task"])
                ),
                "current_stage": task.current_stage,
                "execution_scope": {"bound": False},
                "workspace": {"root": task.workspace_root},
                "context_package_available": False,
                "document_discovery": _public_document_discovery(document_discovery),
                "change_plan": {
                    "mode": "copilot_change_task",
                    "stage": task.current_stage,
                    "steps": steps,
                    "stage_order": list(CHANGE_TASK_STAGE_ORDER),
                    "required_outputs": list(CHANGE_TASK_REQUIRED_OUTPUTS),
                },
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
            )
        )
        packet = cast(dict[str, object], context["edit_packet"])
        approval = cast(dict[str, object], context["approval"])
        task_workspace = cast(dict[str, object], context["workspace"])
        return {
            "coding_task": _public_task_artifact(
                cast(dict[str, object], task_view["task"])
            ),
            "current_stage": task.current_stage,
            "execution_scope": _public_execution_scope(packet, approval),
            "workspace": _public_workspace(task_workspace),
            "context_package_available": False,
            "change_plan": {
                "mode": "copilot_change_task",
                "stage": task.current_stage,
                "steps": [
                    "Use only the editable and test paths in the validated execution scope.",
                    "Modify the code and tests required by the recorded design diff.",
                    "Call copilot_validate_task_diff before generating the final test plans.",
                    (
                        "Generate natural-language TestPlan and executable TestDataPlan from "
                        "the requirement, recorded design diff, and validated code diff."
                    ),
                    (
                        "Call copilot_record_change_outputs with "
                        "output_stage=test_planning, test_plan, and test_data_plan."
                    ),
                    "Run every required compile/test command with copilot_run_task_command.",
                    (
                        "Describe UI cases with TestDataPlan UI steps and UI assertions; "
                        "OperaMind executes them after test-data generation."
                    ),
                    "Commit the validated changes, then call copilot_record_task_result.",
                ],
                "stage_order": list(CHANGE_TASK_STAGE_ORDER),
                "required_outputs": list(CHANGE_TASK_REQUIRED_OUTPUTS),
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
                candidates=[
                    candidate.to_dict() for candidate in discovery.candidates
                ],
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
                "embedding_profile_binding_key": (
                    discovery.embedding_profile_binding_key
                ),
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
                "Canonical requirement document discovery is unavailable: "
                f"{discovery_error}"
            ),
        }

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
    ) -> dict[str, object]:
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
            actor="mcp:github-copilot",
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
    ) -> dict[str, object]:
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
            )
        raise ValueError(f"Unsupported Copilot Change Task output stage: {output_stage}")

    def _record_document_outputs(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        document_ids: tuple[str, ...],
        document_edits: tuple[DocumentFieldEdit, ...],
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
        discovery = self._document_discovery(task.change_request_id)
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
            actor="mcp:github-copilot",
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
    ) -> dict[str, object]:
        task = self._tasks.get(coding_task_id)
        document_refs = self._recorded_output(coding_task_id, "document_change")
        document_change_refs = tuple(
            str(value)
            for value in cast(list[object], document_refs["document_change_refs"])
        )
        case_id = self._bound_change_request_case(task.change_request_id)
        existing_impact = self._requests.impact_report(
            project_id=task.project_id,
            case_id=case_id,
        )
        if existing_impact is None:
            impact = CopilotImpactService(
                connection=self._connection,
                repository_root=self._root,
            ).publish(
                project_id=task.project_id,
                analysis_case_id=case_id,
                change_request_id=task.change_request_id,
                coding_task_id=coding_task_id,
                workspace_root=workspace_root,
                source_document_snapshot_id=str(
                    document_refs["source_document_snapshot_id"]
                ),
                target_document_snapshot_id=str(
                    document_refs["target_document_snapshot_id"]
                ),
                search_index_build_id=str(document_refs["search_index_build_id"]),
                document_change_refs=document_change_refs,
                code_scope=code_scope,
            )
        else:
            existing_impact_artifact = self._artifacts.get(
                str(existing_impact["impact_report_id"])
            )
            if existing_impact_artifact is None:
                raise RuntimeError("Existing Impact Report Artifact is missing")
            impact_change_refs = {
                str(reference)
                for item in cast(
                    list[dict[str, Any]], existing_impact_artifact.get("items", [])
                )
                for reference in cast(
                    list[object], item.get("structured_change_refs", [])
                )
            }
            requested_paths = {
                str(item.get("target_path") or "") for item in code_scope
            }
            impact_paths = {
                str(item.get("target_path") or "")
                for item in cast(
                    list[dict[str, Any]], existing_impact_artifact.get("items", [])
                )
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
        next_stage = (
            "compile_test" if task.approval_grant_id is not None else "code_scope"
        )
        self._tasks.record_change_outputs(
            coding_task_id=coding_task_id,
            actor="mcp:github-copilot",
            output_stage="code_scope",
            expected_stage="code_scope",
            next_stage=next_stage,
            output_refs=output_refs,
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
    ) -> dict[str, object]:
        task = self._tasks.get(coding_task_id)
        case_id, _edit_packet_id, _approval_grant_id = _bound_task_scope(task)
        view = self._tasks.view(coding_task_id)
        if not any(
            result.get("validation_mode") == "working"
            and result.get("status") == "in_scope"
            and bool(result.get("changed_paths"))
            for result in cast(list[dict[str, Any]], view["edit_results"])
        ):
            raise ValueError(
                "TestPlan must be generated after copilot_validate_task_diff "
                "has accepted the current code diff"
            )
        _validate_planning_artifact_scope(
            artifact_name="TestPlan",
            artifact=test_plan,
            expected={
                "artifact_type": "TestPlan",
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
                "project_id": task.project_id,
                "test_plan_id": test_plan_id,
                "status": "ready",
            },
        )
        test_data_plan_id = str(test_data_plan.get("test_data_plan_id") or "")
        if not test_plan_id or not test_data_plan_id:
            raise ValueError("Change Task output identities must not be blank")
        code_refs = self._recorded_output(coding_task_id, "code_scope")
        impact = self._artifacts.get(str(code_refs["impact_report_id"]))
        if impact is None or impact.get("artifact_type") != "ImpactReport":
            raise RuntimeError("Copilot Change Task Impact Report Artifact is missing")
        _validate_planning_alignment(
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            ui_impacted=impact.get("ui_impact_status") == "impacted",
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
            actor="mcp:github-copilot",
            output_stage="test_planning",
            expected_stage="compile_test",
            next_stage="compile_test",
            output_refs=output_refs,
        )
        return {
            **output_refs,
            "recorded_stage": "test_planning",
            "next_stage": "compile_test",
            "coding_task_state": self._tasks.get(coding_task_id).state,
        }

    def _recorded_output(
        self, coding_task_id: str, output_stage: str
    ) -> dict[str, object]:
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
            raise ValueError(
                f"Copilot Change Task has no recorded {output_stage} output"
            )
        return dict(cast(dict[str, object], event["payload"]))

    def _bound_change_request_case(self, change_request_id: str) -> str:
        case_id = self._requests.get_change_request(change_request_id).get(
            "analysis_case_id"
        )
        if not isinstance(case_id, str):
            raise ValueError("Copilot Change Task requires a bound Analysis Case")
        return case_id

    def validate_diff(
        self, *, coding_task_id: str, edit_result_id: str, workspace_root: Path
    ) -> dict[str, object]:
        return self._record_edit_result(
            coding_task_id=coding_task_id,
            edit_result_id=edit_result_id,
            workspace_root=workspace_root,
            mode=EditValidationMode.WORKING,
            test_result_refs=(),
            tests_passed=None,
        )

    def record_result(
        self,
        *,
        coding_task_id: str,
        edit_result_id: str,
        workspace_root: Path,
        test_result_refs: tuple[str, ...],
        tests_passed: bool,
        changed_line_coverage: ChangedLineCoverageEvidence | None = None,
    ) -> dict[str, object]:
        self._recorded_output(coding_task_id, "test_planning")
        return self._record_edit_result(
            coding_task_id=coding_task_id,
            edit_result_id=edit_result_id,
            workspace_root=workspace_root,
            mode=EditValidationMode.COMMITTED,
            test_result_refs=test_result_refs,
            tests_passed=tests_passed,
            changed_line_coverage=changed_line_coverage,
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
            actor="mcp:github-copilot",
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
    ui_case_ids = {
        str(case["test_case_id"]) for case in test_cases if case.get("level") == "ui"
    }
    if bool(ui_case_ids) != ui_impacted:
        raise ValueError("TestPlan UI cases do not match the Graph-validated UI impact")
    data_sets = cast(list[dict[str, Any]], test_data_plan.get("data_sets", []))
    data_ids = [str(item.get("test_data_id") or "") for item in data_sets]
    if not data_ids or any(not value for value in data_ids) or len(data_ids) != len(set(data_ids)):
        raise ValueError("TestDataPlan test_data_id values must be non-empty and unique")
    data_id_set = set(data_ids)
    for test_case in test_cases:
        refs = {
            str(value)
            for value in cast(list[object], test_case.get("test_data_refs", []))
        }
        if not refs or not refs.issubset(data_id_set):
            raise ValueError(
                f"Test case has missing TestDataPlan data refs: {test_case['test_case_id']}"
            )
    flows = cast(list[dict[str, Any]], test_data_plan.get("generation_flows", []))
    covered_cases = {
        str(value)
        for flow in flows
        for value in cast(list[object], flow.get("test_case_refs", []))
    }
    if covered_cases != set(case_ids):
        raise ValueError("TestDataPlan flows must cover exactly every TestPlan case")
    for case_id in ui_case_ids:
        matching = [
            flow
            for flow in flows
            if case_id
            in {
                str(value)
                for value in cast(list[object], flow.get("test_case_refs", []))
            }
        ]
        if not matching:
            raise ValueError(f"UI test case has no TestDataPlan flow: {case_id}")
        if not any(
            any(step.get("channel") == "ui" for step in cast(list[dict[str, Any]], flow["steps"]))
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
                f"UI test case requires a bounded UI step and UI assertion: {case_id}"
            )


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

    return {
        "document_id": document.document_id,
        "logical_name": document.logical_name,
        "document_ref": document.source_ref,
        "facts": [
            {
                "stable_key": item.fact.stable_key,
                "fact_type": item.fact.fact_type,
                "values": dict(item.fact.values),
            }
            for item in document.snapshot.facts
        ],
    }
