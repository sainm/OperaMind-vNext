"""Public Web routes for the single six-stage change flow."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from operamind.application.web_control_plane import (
    BusinessRuleInput,
    ChangeRequestInput,
    WebControlPlaneService,
)
from operamind.web.dependencies import command_actor, get_service, idempotency_key
from operamind.web.models import (
    ChangeCheckpointDecisionInput,
    ChangeRequestCreate,
    TestCaseRevisionConfirm,
    TestCaseRevisionProposalCreate,
)

router = APIRouter(prefix="/api/v1/change-requests", tags=["change-requests"])
Service = Annotated[WebControlPlaneService, Depends(get_service)]
Actor = Annotated[str, Depends(command_actor)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]


@router.get("")
def list_requests(
    project_id: Annotated[str, Query(min_length=1)], service: Service
) -> dict[str, object]:
    result = service.list_change_requests(project_id=project_id)
    raw_requests = result.get("change_requests")
    requests = raw_requests if isinstance(raw_requests, list | tuple) else []
    public_requests = [
        {
            "change_request_id": item.get("change_request_id"),
            "requirement_text": item.get("requirement_text"),
        }
        for item in requests
        if isinstance(item, dict) and isinstance(item.get("change_request_id"), str)
    ]
    return {"change_requests": public_requests, "count": len(public_requests)}


@router.post("", status_code=201)
def create_request(
    body: ChangeRequestCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    value = ChangeRequestInput(
        change_request_id=body.change_request_id,
        project_id=body.project_id,
        analysis_case_id=None,
        input_mode="natural_language",
        requirement_text=body.requirement_text,
        source_document_ref=None,
        target_document_ref=None,
        business_rules=(
            BusinessRuleInput(
                f"{body.change_request_id}-rule-1",
                body.requirement_text,
                (),
            ),
        ),
        ambiguity_status="clear",
        ambiguities=(),
        submitted_by=actor,
    )
    submission = service.execute_web_command(
        command_scope=f"change-request:create:{body.change_request_id}",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.submit_change_request(value),
    )
    service.start_change_automation(
        request_id=body.change_request_id,
        idempotency_key="automatic-main-flow",
        actor="automation:operamind",
    )
    return {
        "created": submission.get("created") is True,
        "flow": service.main_change_flow(body.change_request_id),
    }


@router.get("/{request_id}/flow")
def get_main_change_flow(request_id: str, service: Service) -> dict[str, object]:
    return service.main_change_flow(request_id)


@router.post("/{request_id}/confirmations/{checkpoint}")
def decide_change_checkpoint(
    request_id: str,
    checkpoint: str,
    body: ChangeCheckpointDecisionInput,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"change-confirmation:{request_id}:{checkpoint}",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.decide_change_checkpoint(
            request_id=request_id,
            checkpoint=checkpoint,
            decision=body.decision,
            surface="web",
            actor=actor,
            idempotency_key=key,
            note=body.note,
        ),
    )


@router.post("/{request_id}/test-case-revisions")
def propose_test_case_revision(
    request_id: str,
    body: TestCaseRevisionProposalCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"test-case-revision:propose:{request_id}",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.propose_test_case_revision(
            request_id=request_id,
            instruction=body.instruction,
            actor=actor,
        ),
    )


@router.post("/{request_id}/test-case-revisions/{proposal_id}/confirm")
def confirm_test_case_revision(
    request_id: str,
    proposal_id: str,
    body: TestCaseRevisionConfirm,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"test-case-revision:confirm:{request_id}:{proposal_id}",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.confirm_test_case_revision(
            request_id=request_id,
            proposal_id=proposal_id,
            selections=body.selections,
            actor=actor,
        ),
    )


@router.get("/{request_id}/screenshots/{evidence_id}")
def get_screenshot(
    request_id: str,
    evidence_id: str,
    service: Service,
) -> FileResponse:
    path = service.screenshot_path(
        request_id=request_id,
        evidence_id=evidence_id,
    )
    return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})
