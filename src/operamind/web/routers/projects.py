"""Project initialization and selection for the single-flow Web application."""

import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends

from operamind.application.web_control_plane import (
    ProjectInitializationInput,
    ProjectSettingsUpdateInput,
    WebControlPlaneService,
)
from operamind.web.dependencies import command_actor, get_service, idempotency_key
from operamind.web.models import (
    DataIdentityProfilesUpdate,
    ExistingTestDataCreate,
    ProjectCreate,
    ProjectDocumentLearningConfirm,
    ProjectOnboardingRequest,
    ProjectUpdate,
    TargetDataProfileUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["projects"])
Service = Annotated[WebControlPlaneService, Depends(get_service)]
Actor = Annotated[str, Depends(command_actor)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"project:initialize:{body.project_id}",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.initialize_project(
            ProjectInitializationInput(
                project_id=body.project_id,
                name=body.name,
                workspace_root=Path(body.workspace_root),
                document_roots=tuple(Path(root) for root in body.document_roots),
                configured_by=actor,
                test_base_url=body.test_base_url,
            )
        ),
    )


@router.get("/projects")
def list_projects(service: Service) -> dict[str, object]:
    result = service.list_projects()
    raw_projects = result.get("projects")
    projects = raw_projects if isinstance(raw_projects, list | tuple) else []
    public_projects = [
        {
            "project_id": item.get("project_id"),
            "name": item.get("name"),
            "workspace_root": item.get("workspace_root"),
            "document_roots": item.get("document_roots"),
            "source_control_kind": item.get("source_control_kind"),
            "source_git_baselines": item.get("source_git_baselines") or [],
            "test_base_url": item.get("test_base_url"),
            "settings_revision": item.get("settings_revision"),
            "onboarding": item.get("onboarding"),
            "document_learning": item.get("document_learning"),
            "target_data_profile": item.get("target_data_profile"),
            **(
                {"target_project": item["target_project"]}
                if isinstance(item.get("target_project"), dict)
                else {}
            ),
        }
        for item in projects
        if isinstance(item, dict) and isinstance(item.get("project_id"), str)
    ]
    return {"projects": public_projects, "count": len(public_projects)}


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    body: ProjectUpdate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"project:update:{project_id}",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.update_project_settings(
            ProjectSettingsUpdateInput(
                project_id=project_id,
                name=body.name,
                document_roots=tuple(Path(root) for root in body.document_roots),
                test_base_url=body.test_base_url,
                expected_revision=body.expected_revision,
                updated_by=actor,
            )
        ),
    )


@router.get("/projects/{project_id}/onboarding")
def project_onboarding(project_id: str, service: Service) -> dict[str, object]:
    return service.project_onboarding(project_id)


@router.get("/projects/{project_id}/document-learning")
def project_document_learning(project_id: str, service: Service) -> dict[str, object]:
    return service.project_document_learning(project_id)


@router.get("/projects/{project_id}/target-data-profile")
def project_target_data_profile(project_id: str, service: Service) -> dict[str, object]:
    return service.project_target_data_profile(project_id, include_statements=True)


@router.get("/projects/{project_id}/existing-test-data")
def existing_test_data(project_id: str, service: Service) -> dict[str, object]:
    return service.existing_test_data(project_id)


@router.get("/projects/{project_id}/data-identity-profiles")
def project_data_identity_profiles(
    project_id: str, service: Service
) -> dict[str, object]:
    return service.project_data_identity_profiles(project_id)


@router.put("/projects/{project_id}/data-identity-profiles")
def update_project_data_identity_profiles(
    project_id: str,
    body: DataIdentityProfilesUpdate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"project:data-identity-profiles:{project_id}",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.configure_project_data_identity_profiles(
            project_id=project_id,
            profiles=tuple(body.profiles),
            actor=actor,
        ),
    )


@router.post("/projects/{project_id}/existing-test-data", status_code=201)
def register_existing_test_data(
    project_id: str,
    body: ExistingTestDataCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"project:existing-test-data:{project_id}:register",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.register_existing_test_data(
            project_id=project_id,
            data_name=body.data_name,
            business_unique_value=body.business_unique_value,
            test_case_ref=body.test_case_ref,
            retain_after_test=body.retain_after_test,
            actor=actor,
        ),
    )


@router.post("/projects/{project_id}/existing-test-data/{registration_id}/confirm")
def confirm_existing_test_data(
    project_id: str,
    registration_id: str,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=(
            f"project:existing-test-data:{project_id}:{registration_id}:confirm"
        ),
        idempotency_key=key,
        actor=actor,
        payload={"registration_id": registration_id},
        operation=lambda: service.confirm_existing_test_data(
            project_id=project_id,
            registration_id=registration_id,
            actor=actor,
        ),
    )


@router.get("/projects/{project_id}/fixed-data-identifiers")
def fixed_data_identifiers(project_id: str, service: Service) -> dict[str, object]:
    return service.fixed_data_identifiers(project_id)


@router.put("/projects/{project_id}/target-data-profile")
def update_project_target_data_profile(
    project_id: str,
    body: TargetDataProfileUpdate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    # The receipt binds only a secret digest, never the connection string itself.
    secret = body.connection_dsn.get_secret_value() if body.connection_dsn is not None else None
    secret_digest = hashlib.sha256(secret.encode()).hexdigest() if secret else None
    return service.execute_web_command(
        command_scope=f"project:target-data:{project_id}",
        idempotency_key=key,
        actor=actor,
        payload={
            "dialect": body.dialect,
            "connection_alias": body.connection_alias,
            "connection_secret_digest": secret_digest,
            "transaction_policy": body.transaction_policy,
            "bindings": body.bindings,
        },
        operation=lambda: service.configure_project_target_data_profile(
            project_id=project_id,
            connection_alias=body.connection_alias,
            dialect=body.dialect,
            connection_dsn=secret,
            transaction_policy=body.transaction_policy,
            bindings=tuple(body.bindings),
            actor=actor,
        ),
    )


@router.post("/projects/{project_id}/document-learning/confirm")
def confirm_project_document_learning(
    project_id: str,
    body: ProjectDocumentLearningConfirm,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"project:document-learning:{project_id}:confirm",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.confirm_project_document_learning(
            project_id=project_id,
            learning_run_id=body.learning_run_id,
            actor=actor,
        ),
    )


@router.get("/projects/{project_id}/preflight")
def project_preflight(project_id: str, service: Service) -> dict[str, object]:
    return service.project_preflight(project_id)


@router.post("/projects/{project_id}/onboarding", status_code=202)
def request_project_onboarding(
    project_id: str,
    body: ProjectOnboardingRequest,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"project:onboarding:{project_id}:{body.action}",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.request_project_onboarding(
            project_id=project_id,
            action=body.action,
            actor=actor,
        ),
    )


@router.post("/projects/{project_id}/onboarding/retry", status_code=202)
def retry_project_onboarding(
    project_id: str,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"project:onboarding:{project_id}:retry",
        idempotency_key=key,
        actor=actor,
        payload={"project_id": project_id},
        operation=lambda: service.retry_project_onboarding(project_id=project_id, actor=actor),
    )
