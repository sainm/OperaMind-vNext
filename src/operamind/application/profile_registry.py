"""Application service for Canonical Profile activation and drift management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.infrastructure.postgres import (
    ProfileDriftRepository,
    ProfileRebuildTaskQueue,
    ProfileRepository,
)
from operamind.profiles import ProfileCatalog


@dataclass(frozen=True, slots=True)
class ProfileActivationRequest:
    activation_event_id: str
    project_id: str
    binding_key: str
    profile_version_id: str
    activated_by: str
    reason: str


class CanonicalProfileRegistryService:
    """Manage active Profile versions and expose effective downstream drift state."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        profiles: ProfileCatalog,
    ) -> None:
        self._profiles = ProfileRepository(connection, profiles)
        self._drift = ProfileDriftRepository(connection)
        self._rebuilds = ProfileRebuildTaskQueue(connection)

    def management_view(self, *, project_id: str) -> dict[str, object]:
        return self._drift.management_view(project_id=project_id)

    def activate(self, request: ProfileActivationRequest) -> dict[str, object]:
        values = (
            request.activation_event_id,
            request.project_id,
            request.binding_key,
            request.profile_version_id,
            request.activated_by,
            request.reason,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Profile activation fields must not be blank")
        profile = self._profiles.get_version(request.profile_version_id)
        if profile is None:
            raise ValueError("Profile version does not exist")
        _validate_binding_key(str(profile["profile_type"]), request.binding_key)
        created = self._profiles.activate(
            activation_event_id=request.activation_event_id,
            project_id=request.project_id,
            binding_key=request.binding_key,
            profile_version_id=request.profile_version_id,
            activated_by=request.activated_by,
            reason=request.reason,
        )
        detection = self._drift.detect_activation(activation_event_id=request.activation_event_id)
        return {
            "created": created,
            "activation_event_id": request.activation_event_id,
            "profile_type": profile["profile_type"],
            "profile_version_id": request.profile_version_id,
            "drift_event_id": detection.drift_event_id,
            "affected_artifact_count": detection.impact_count,
            "registry": self.management_view(project_id=request.project_id),
        }

    def request_rebuild(
        self,
        *,
        rebuild_request_id: str,
        project_id: str,
        drift_event_id: str,
        artifact_type: str,
        artifact_id: str,
        requested_by: str,
    ) -> dict[str, object]:
        scheduled = self._drift.request_rebuild(
            rebuild_request_id=rebuild_request_id,
            project_id=project_id,
            drift_event_id=drift_event_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            requested_by=requested_by,
        )
        return {
            "created": scheduled.created,
            "rebuild_batch_id": scheduled.batch_id,
            "rebuild_request_id": scheduled.requested_request_id,
            "rebuild_request_ids": list(scheduled.request_ids),
            "registry": self.management_view(project_id=project_id),
        }

    def requeue_rebuild(
        self,
        *,
        rebuild_request_id: str,
        project_id: str,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        request = self._rebuilds.requeue(
            task_id=rebuild_request_id,
            project_id=project_id,
            actor=actor,
            reason=reason,
        )
        return {
            "rebuild_request_id": rebuild_request_id,
            "status": request["status"],
            "registry": self.management_view(project_id=project_id),
        }


_BINDING_PREFIXES = {
    "DocumentConventionProfile": ("document:",),
    "DocumentRelationProfile": ("relation:",),
    "EmbeddingProfile": ("embedding:",),
    "CodeFrameworkProfile": ("code-framework:",),
    "CommandExecutionProfile": ("command:", "command-execution:"),
    "UiLocatorProfile": ("ui-locator:",),
}


def _validate_binding_key(profile_type: str, binding_key: str) -> None:
    prefixes = _BINDING_PREFIXES.get(profile_type)
    if prefixes is None or not binding_key.startswith(prefixes):
        raise ValueError(f"Binding key does not match {profile_type}")


__all__ = ["CanonicalProfileRegistryService", "ProfileActivationRequest"]
