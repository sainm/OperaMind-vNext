"""Transport-neutral Copilot Coding Task orchestration for the local POC Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from psycopg import Connection

from operamind.application.command_execution import (
    ApprovedCommandRequest,
    ApprovedCommandService,
)
from operamind.application.copilot_handoff import (
    CopilotHandoffRequest,
    CopilotHandoffService,
)
from operamind.application.edit_result import (
    EditResultRequest,
    EditResultService,
    EditValidationMode,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.copilot_coding_task_repository import (
    CopilotCodingTaskRepository,
)
from operamind.infrastructure.postgres.web_control_plane_repository import (
    WebControlPlaneRepository,
)
from operamind.profiles import ProfileCatalog

REQUIRED_TASK_TOOLS = (
    "copilot_get_coding_task",
    "copilot_run_task_command",
    "copilot_validate_task_diff",
    "copilot_record_task_result",
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
    edit_packet_id: str
    approval_grant_id: str
    workspace_root: Path
    task_summary: str
    actor: str
    idempotency_key: str
    retry_of_coding_task_id: str | None = None
    attempt_number: int = 1

    def __post_init__(self) -> None:
        values = (
            self.coding_task_id,
            self.change_request_id,
            self.project_id,
            self.edit_packet_id,
            self.approval_grant_id,
            self.task_summary,
            self.actor,
            self.idempotency_key,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Copilot Coding Task publish fields must not be blank")
        if len(self.task_summary) > 10_000:
            raise ValueError("Copilot Coding Task summary exceeds 10000 characters")
        if self.retry_of_coding_task_id is not None and not self.retry_of_coding_task_id.strip():
            raise ValueError("retry_of_coding_task_id must not be blank")
        if self.attempt_number < 1:
            raise ValueError("Copilot Coding Task attempt_number must be positive")


class CopilotCodingTaskService:
    """Publish, accept, execute, and report one bounded Coding Plan task."""

    def __init__(self, *, connection: Connection[Any], repository_root: Path) -> None:
        root = repository_root.resolve()
        self._connection = connection
        self._contracts = ContractCatalog.load(root / "contracts")
        self._profiles = ProfileCatalog.load(root / "profiles")
        self._tasks = CopilotCodingTaskRepository(connection, self._contracts)
        self._requests = WebControlPlaneRepository(connection, self._contracts)
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
        if change_request.get("project_id") != request.project_id or not isinstance(case_id, str):
            raise ValueError("Change Request has no bound ChangeSession in requested Project")
        handoff = CopilotHandoffService(
            connection=self._connection,
            contracts=self._contracts,
        ).get(
            CopilotHandoffRequest(
                project_id=request.project_id,
                analysis_case_id=case_id,
                edit_packet_id=request.edit_packet_id,
                approval_grant_id=request.approval_grant_id,
                workspace_root=request.workspace_root,
            )
        )
        workspace = cast(dict[str, object], handoff["workspace"])
        if workspace.get("isolated_worktree") is not True:
            raise ValueError(
                "Copilot Coding Task requires an isolated linked worktree, not the registered root"
            )
        packet = cast(dict[str, object], handoff["edit_packet"])
        provider_contract = self._provider.contract
        if provider_contract != {
            "interface": "coding_task_provider_v1",
            "route": "local_bridge",
            "provider_id": "vscode_github_copilot",
        }:
            raise ValueError("POC only supports the local VS Code GitHub Copilot provider")
        artifact: dict[str, Any] = {
            "artifact_type": "CopilotCodingTask",
            "schema_version": "v1",
            "coding_task_id": request.coding_task_id,
            "change_session_id": case_id,
            "change_request_id": request.change_request_id,
            "project_id": request.project_id,
            "analysis_case_id": case_id,
            "repository_id": packet["repository_id"],
            "edit_packet_id": request.edit_packet_id,
            "approval_grant_id": request.approval_grant_id,
            "base_repository_revision": packet["base_repository_revision"],
            "attempt_number": request.attempt_number,
            "execution_mode": "copilot_coding_plan",
            "provider_contract": provider_contract,
            "task_summary": request.task_summary,
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
        previous_artifact = cast(
            dict[str, object], self._tasks.view(coding_task_id)["task"]
        )
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

    def get_mcp_context(self, *, coding_task_id: str, workspace_root: Path) -> dict[str, object]:
        task = self._tasks.begin_mcp(
            coding_task_id=coding_task_id,
            workspace_root=workspace_root,
            actor="mcp:github-copilot",
        )
        handoff = CopilotHandoffService(
            connection=self._connection,
            contracts=self._contracts,
        ).get(
            CopilotHandoffRequest(
                project_id=task.project_id,
                analysis_case_id=task.analysis_case_id,
                edit_packet_id=task.edit_packet_id,
                approval_grant_id=task.approval_grant_id,
                workspace_root=workspace_root,
            )
        )
        return {
            "coding_task": self._tasks.view(coding_task_id)["task"],
            **handoff,
            "coding_plan": {
                "mode": "copilot_coding_plan",
                "steps": [
                    "Read only the Edit Packet editable, read-only, and test files.",
                    "Modify only approved editable/test paths and stop on scope expansion.",
                    "Run every required command with copilot_run_task_command.",
                    "Call copilot_validate_task_diff before committing.",
                    "Commit the approved change, then call copilot_record_task_result.",
                ],
            },
        }

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
        result = (
            ApprovedCommandService(
                connection=self._connection,
                contracts=self._contracts,
                profiles=self._profiles,
            )
            .run(
                ApprovedCommandRequest(
                    command_execution_id=command_execution_id,
                    approval_grant_id=task.approval_grant_id,
                    project_id=task.project_id,
                    analysis_case_id=task.analysis_case_id,
                    edit_packet_id=task.edit_packet_id,
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
    ) -> dict[str, object]:
        return self._record_edit_result(
            coding_task_id=coding_task_id,
            edit_result_id=edit_result_id,
            workspace_root=workspace_root,
            mode=EditValidationMode.COMMITTED,
            test_result_refs=test_result_refs,
            tests_passed=tests_passed,
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
    ) -> dict[str, object]:
        task = self._tasks.get(coding_task_id)
        result = (
            EditResultService(connection=self._connection, contracts=self._contracts)
            .run(
                EditResultRequest(
                    edit_result_id=edit_result_id,
                    edit_packet_id=task.edit_packet_id,
                    approval_grant_id=task.approval_grant_id,
                    project_id=task.project_id,
                    analysis_case_id=task.analysis_case_id,
                    workspace_root=workspace_root,
                    mode=mode,
                    test_result_refs=test_result_refs,
                    tests_passed=tests_passed,
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
