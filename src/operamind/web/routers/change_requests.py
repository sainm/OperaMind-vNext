"""Change Request and document-review routes."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import FileResponse

from operamind.application.web_control_plane import (
    BusinessRuleInput,
    ChangeRequestInput,
    WebControlPlaneService,
)
from operamind.application.web_test_data_execution import execute_reserved_test_data_run
from operamind.web.dependencies import command_actor, get_service, idempotency_key
from operamind.web.models import (
    ChangeRequestCaseBindingCreate,
    ChangeRequestCreate,
    CopilotCodingTaskCancel,
    CopilotCodingTaskCreate,
    CopilotCodingTaskRetry,
    DocumentReviewCreate,
    TestCaseExecutionScopeConfirm,
    TestCaseModificationConfirm,
    TestCaseModificationCreate,
    TestDataRecoveryCreate,
)

router = APIRouter(prefix="/api/v1/change-requests", tags=["change-requests"])
Service = Annotated[WebControlPlaneService, Depends(get_service)]
Actor = Annotated[str, Depends(command_actor)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]


@router.get("")
def list_requests(
    project_id: Annotated[str, Query(min_length=1)], service: Service
) -> dict[str, object]:
    return service.list_change_requests(project_id=project_id)


@router.post("", status_code=201)
def create_request(body: ChangeRequestCreate, service: Service, actor: Actor) -> dict[str, object]:
    value = ChangeRequestInput(
        change_request_id=body.change_request_id,
        project_id=body.project_id,
        analysis_case_id=body.analysis_case_id,
        input_mode=body.input_mode,
        requirement_text=body.requirement_text,
        source_document_ref=body.source_document_ref,
        target_document_ref=body.target_document_ref,
        business_rules=tuple(
            BusinessRuleInput(rule.business_rule_id, rule.text, tuple(rule.source_refs))
            for rule in body.business_rules
        ),
        ambiguity_status=body.ambiguity_status,
        ambiguities=tuple(body.ambiguities),
        submitted_by=actor,
    )
    return service.submit_change_request(value)


@router.get("/{request_id}")
def get_request(request_id: str, service: Service) -> dict[str, object]:
    return service.get_change_request(request_id)


@router.get("/{request_id}/document-diff")
def get_diff(request_id: str, service: Service) -> dict[str, object]:
    return service.document_diff(request_id)


@router.get("/{request_id}/orchestration")
def get_orchestration(request_id: str, service: Service) -> dict[str, object]:
    return service.change_orchestration(request_id)


@router.get("/{request_id}/automation")
def get_automation(request_id: str, service: Service) -> dict[str, object]:
    return service.change_automation(request_id)


@router.get("/{request_id}/copilot-task")
def get_copilot_task(request_id: str, service: Service) -> dict[str, object]:
    return service.copilot_task(request_id)


@router.post("/{request_id}/copilot-task", status_code=201)
def publish_copilot_task(
    request_id: str,
    body: CopilotCodingTaskCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.publish_copilot_task(
        request_id=request_id,
        project_id=body.project_id,
        edit_packet_id=body.edit_packet_id,
        approval_grant_id=body.approval_grant_id,
        workspace_root=Path(body.workspace_root),
        task_summary=body.task_summary,
        idempotency_key=key,
        actor=actor,
    )


@router.post("/{request_id}/copilot-task/{coding_task_id}/cancel")
def cancel_copilot_task(
    request_id: str,
    coding_task_id: str,
    body: CopilotCodingTaskCancel,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.cancel_copilot_task(
        request_id=request_id,
        coding_task_id=coding_task_id,
        reason=body.reason,
        idempotency_key=key,
        actor=actor,
    )


@router.post("/{request_id}/copilot-task/{coding_task_id}/retry", status_code=201)
def retry_copilot_task(
    request_id: str,
    coding_task_id: str,
    body: CopilotCodingTaskRetry,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.retry_copilot_task(
        request_id=request_id,
        coding_task_id=coding_task_id,
        idempotency_key=key,
        actor=actor,
        edit_packet_id=body.edit_packet_id,
        approval_grant_id=body.approval_grant_id,
        workspace_root=Path(body.workspace_root),
    )


@router.post("/{request_id}/case-binding")
def bind_case(
    request_id: str,
    body: ChangeRequestCaseBindingCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.bind_change_request_case(
        request_id=request_id,
        project_id=body.project_id,
        case_id=body.analysis_case_id,
        idempotency_key=key,
        actor=actor,
    )


@router.post("/{request_id}/automation", status_code=201)
def start_automation(
    request_id: str, service: Service, actor: Actor, key: IdempotencyKey
) -> dict[str, object]:
    return service.start_change_automation(request_id=request_id, idempotency_key=key, actor=actor)


@router.post("/{request_id}/automation/{run_id}/resume")
def resume_automation(
    request_id: str, run_id: str, service: Service, actor: Actor
) -> dict[str, object]:
    return service.resume_change_automation(request_id=request_id, run_id=run_id, actor=actor)


@router.get("/{request_id}/execution-management")
def get_execution_management(request_id: str, service: Service) -> dict[str, object]:
    return service.execution_management(request_id)


@router.get("/{request_id}/test-case-modifications")
def get_test_case_modifications(request_id: str, service: Service) -> dict[str, object]:
    return service.test_case_modification_state(request_id)


@router.post("/{request_id}/test-case-modifications", status_code=201)
def create_test_case_modification(
    request_id: str,
    body: TestCaseModificationCreate,
    service: Service,
    actor: Actor,
) -> dict[str, object]:
    return service.modify_test_case(
        request_id=request_id,
        instruction=body.instruction,
        actor=actor,
    )


@router.post("/{request_id}/test-case-modifications/{proposal_id}/confirm")
def confirm_test_case_modification(
    request_id: str,
    proposal_id: str,
    body: TestCaseModificationConfirm,
    service: Service,
    actor: Actor,
) -> dict[str, object]:
    return service.confirm_test_case_modification(
        request_id=request_id,
        proposal_id=proposal_id,
        selections=body.selections,
        actor=actor,
    )


@router.post("/{request_id}/test-case-revisions/{revision_id}/undo")
def undo_test_case_revision(
    request_id: str,
    revision_id: str,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.undo_test_case_revision(
        request_id=request_id,
        revision_id=revision_id,
        idempotency_key=key,
        actor=actor,
    )


@router.post("/{request_id}/test-case-execution-authorization")
def confirm_test_case_execution_scope(
    request_id: str,
    body: TestCaseExecutionScopeConfirm,
    service: Service,
    actor: Actor,
) -> dict[str, object]:
    return service.confirm_test_case_execution_scope(
        request_id=request_id,
        approval_grant_id=body.approval_grant_id,
        target_scope_digest=body.target_scope_digest,
        actor=actor,
    )


@router.post("/{request_id}/test-data-runs", status_code=202)
def start_test_data_run(
    request_id: str,
    request: Request,
    background: BackgroundTasks,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    result = service.start_test_data_run(
        request_id=request_id,
        idempotency_key=key,
        actor=actor,
    )
    _schedule_test_data_run(request=request, background=background, result=result)
    return result


@router.post("/{request_id}/test-data-runs/{run_id}/rerun", status_code=202)
def rerun_test_data_run(
    request_id: str,
    run_id: str,
    request: Request,
    background: BackgroundTasks,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    result = service.start_test_data_run(
        request_id=request_id,
        idempotency_key=key,
        actor=actor,
        replay_of_run_id=run_id,
    )
    _schedule_test_data_run(request=request, background=background, result=result)
    return result


@router.post("/{request_id}/test-data-runs/{run_id}/recover")
def recover_test_data_run(
    request_id: str,
    run_id: str,
    body: TestDataRecoveryCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.recover_test_data_run(
        request_id=request_id,
        run_id=run_id,
        idempotency_key=key,
        actor=actor,
        reason=body.reason,
        stale_before=body.stale_before,
    )


@router.get("/{request_id}/screenshots/{origin}/{evidence_id}")
def get_screenshot(
    request_id: str, origin: str, evidence_id: str, service: Service
) -> FileResponse:
    path = service.screenshot_path(request_id=request_id, origin=origin, evidence_id=evidence_id)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})


@router.post("/{request_id}/orchestration", status_code=201)
def orchestrate_request(request_id: str, service: Service, actor: Actor) -> dict[str, object]:
    return service.orchestrate_change_request(request_id=request_id, actor=actor)


@router.post("/{request_id}/document-review")
def review_diff(
    request_id: str,
    body: DocumentReviewCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.review_document_diff(
        idempotency_key=key,
        request_id=request_id,
        project_id=body.project_id,
        decision=body.decision,
        actor=actor,
        note=body.note,
    )


def _schedule_test_data_run(
    *, request: Request, background: BackgroundTasks, result: dict[str, object]
) -> None:
    if result.get("background_required") is not True:
        return
    background.add_task(
        execute_reserved_test_data_run,
        database_url=str(request.app.state.database_url),
        repository_root=request.app.state.repository_root,
        run_id=str(result["run_id"]),
        executor_factory=request.app.state.test_data_executor_factory,
    )
