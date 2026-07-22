"""Human-confirmation and bounded authorization routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from operamind.application.web_control_plane import (
    GrantInput,
    ImpactDecisionInput,
    WebControlPlaneService,
)
from operamind.web.dependencies import command_actor, get_service, idempotency_key
from operamind.web.models import ApprovalGrantCreate, ImpactConfirmationCreate

router = APIRouter(prefix="/api/v1/projects/{project_id}/cases/{case_id}", tags=["commands"])
Service = Annotated[WebControlPlaneService, Depends(get_service)]
Actor = Annotated[str, Depends(command_actor)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]


@router.post("/impact-confirmation")
def confirm_impact(
    project_id: str,
    case_id: str,
    body: ImpactConfirmationCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.confirm_impact(
        idempotency_key=key,
        request_id=body.change_request_id,
        project_id=project_id,
        case_id=case_id,
        value=ImpactDecisionInput(
            body.report_id,
            tuple(body.approved_item_ids),
            tuple(body.rejected_item_ids),
            body.note,
            actor,
        ),
    )


@router.post("/approval-grants", status_code=201)
def issue_grant(
    project_id: str,
    case_id: str,
    body: ApprovalGrantCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.issue_grant(
        idempotency_key=key,
        request_id=body.change_request_id,
        project_id=project_id,
        case_id=case_id,
        value=GrantInput(
            body.edit_packet_id,
            body.expires_at,
            body.command_profile_binding_key,
            tuple(body.test_command_refs),
            actor,
        ),
    )
