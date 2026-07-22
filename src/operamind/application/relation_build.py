"""Build a current Profile-derived relation graph for one Document Snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.domain import DocumentRelationPlan, DocumentRelationPlanner
from operamind.infrastructure.postgres import (
    DocumentRelationBuildResult,
    DocumentRelationBuildSpec,
    DocumentRelationRepository,
    ProfileRepository,
)
from operamind.profiles import ProfileCatalog


@dataclass(frozen=True, slots=True)
class DocumentRelationBuildRequest:
    """Build identity, Profile binding, and activation audit fields."""

    build_id: str
    project_id: str
    snapshot_id: str
    profile_version_id: str
    profile_binding_key: str
    profile_activation_event_id: str
    activated_by: str
    activation_reason: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.build_id,
                self.project_id,
                self.snapshot_id,
                self.profile_version_id,
                self.profile_binding_key,
                self.profile_activation_event_id,
                self.activated_by,
                self.activation_reason,
            )
        ):
            raise ValueError("Document relation Build request fields must not be blank")


@dataclass(frozen=True, slots=True)
class DocumentRelationBuildServiceResult:
    """Published Build state plus its complete deterministic plan."""

    publication: DocumentRelationBuildResult
    plan: DocumentRelationPlan


class DocumentRelationBuildService:
    """Validate Profile, derive exact edges, activate, and publish atomically."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        profiles: ProfileCatalog,
    ) -> None:
        self._connection = connection
        self._profiles = profiles
        self._profile_repository = ProfileRepository(connection, profiles)
        self._relations = DocumentRelationRepository(connection)

    def run(
        self,
        request: DocumentRelationBuildRequest,
        *,
        profile: dict[str, Any],
    ) -> DocumentRelationBuildServiceResult:
        """Publish one Build; replaying a stale Build does not reactivate its Profile."""

        self._profiles.validate_profile(profile)
        if profile.get("profile_type") != "DocumentRelationProfile":
            raise ValueError("Document relation Build requires a DocumentRelationProfile")
        planner = DocumentRelationPlanner.from_validated_profile(profile)
        facts = self._relations.load_facts(
            project_id=request.project_id,
            snapshot_id=request.snapshot_id,
        )
        plan = planner.plan(facts)
        spec = DocumentRelationBuildSpec(
            build_id=request.build_id,
            project_id=request.project_id,
            snapshot_id=request.snapshot_id,
            profile_version_id=request.profile_version_id,
        )
        if self._relations.get_build(request.build_id) is not None:
            publication = self._relations.publish(spec=spec, plan=plan)
            return DocumentRelationBuildServiceResult(publication=publication, plan=plan)

        with self._connection.transaction():
            self._profile_repository.store_version(
                profile_version_id=request.profile_version_id,
                profile=profile,
            )
            self._profile_repository.activate(
                activation_event_id=request.profile_activation_event_id,
                project_id=request.project_id,
                binding_key=request.profile_binding_key,
                profile_version_id=request.profile_version_id,
                activated_by=request.activated_by,
                reason=request.activation_reason,
            )
            active = self._profile_repository.get_active(
                project_id=request.project_id,
                binding_key=request.profile_binding_key,
            )
            if active is None or active.profile_version_id != request.profile_version_id:
                raise ValueError("DocumentRelationProfile is not the active project binding")
            publication = self._relations.publish(spec=spec, plan=plan)
        return DocumentRelationBuildServiceResult(publication=publication, plan=plan)
