"""Public Web routes for the single six-stage change flow."""

import re
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import FileResponse

from operamind.application.main_flow_execution import execute_reserved_test_data_run
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
_REQUIREMENT_SENTENCE_BOUNDARY = re.compile(
    r"(?:\r\n?|\n)+|(?<=[。！？!?；;])"  # noqa: RUF001 - intentional CJK punctuation
)
_REQUIREMENT_CLAUSE_BOUNDARY = re.compile(
    r"(?:して|し)[、,，]\s*|[、,，]\s*(?=(?:さらに|また|かつ|および|そして|同時に|并且|同时|另外|以及))"  # noqa: RUF001 - intentional CJK punctuation
)
_LEADING_CONNECTOR = re.compile(
    r"^(?:さらに|また|かつ|および|そして|同時に|并且|同时|另外|以及)\s*"
)


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
        business_rules=_business_change_points(
            body.change_request_id, body.requirement_text
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


def _business_change_points(
    change_request_id: str, requirement_text: str
) -> tuple[BusinessRuleInput, ...]:
    """Preserve independently confirmable change points from natural-language input."""

    normalized = requirement_text.strip()
    sentences = _REQUIREMENT_SENTENCE_BOUNDARY.split(normalized)
    candidates = (
        clause
        for sentence in sentences
        for clause in _REQUIREMENT_CLAUSE_BOUNDARY.split(sentence)
    )
    points = tuple(
        point
        for value in candidates
        if (
            point := _LEADING_CONNECTOR.sub(
                "",
                re.sub(r"^\s*(?:[-*・]|\d+[.)、])\s*", "", value)
                .strip()
                .rstrip("。！？!?；;"),  # noqa: RUF001 - intentional CJK punctuation
            ).strip()
        )
    )
    if not points:
        points = (normalized,)
    return tuple(
        BusinessRuleInput(
            business_rule_id=f"{change_request_id}-change-point-{position}",
            text=point,
            source_refs=(),
        )
        for position, point in enumerate(points, start=1)
    )


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


@router.post("/{request_id}/test-data-runs/{run_id}/rerun")
def rerun_test_data(
    request_id: str,
    run_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    """Replay one terminal Run against the same confirmed plan and scope."""

    result = service.execute_web_command(
        command_scope=f"test-data-rerun:{request_id}:{run_id}",
        idempotency_key=key,
        actor=actor,
        payload={"replay_of_run_id": run_id},
        operation=lambda: service.start_test_data_run(
            request_id=request_id,
            idempotency_key=key,
            actor=actor,
            replay_of_run_id=run_id,
        ),
    )
    if result.get("background_required") is True:
        background_tasks.add_task(
            execute_reserved_test_data_run,
            database_url=str(request.app.state.database_url),
            repository_root=request.app.state.repository_root,
            run_id=str(result["run_id"]),
            executor_factory=request.app.state.test_data_executor_factory,
        )
    return result


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
