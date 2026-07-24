"""Review an immutable draft UI Knowledge Snapshot into a new version."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.domain import UiKnowledgeSnapshot
from operamind.infrastructure.postgres import (
    ProfileRepository,
    UiKnowledgeRepository,
    UiKnowledgeReviewRecord,
)
from operamind.profiles import ProfileCatalog


@dataclass(frozen=True, slots=True)
class UiKnowledgeReviewRequest:
    project_id: str
    source_snapshot_id: str
    result_snapshot_id: str
    result_snapshot_version: str
    review_event_id: str
    decision: str
    reviewed_by: str
    activate: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.project_id,
                self.source_snapshot_id,
                self.result_snapshot_id,
                self.result_snapshot_version,
                self.review_event_id,
                self.reviewed_by,
            )
        ):
            raise ValueError("UI Knowledge Review request fields must not be blank")
        if self.decision not in {"approved", "rejected"}:
            raise ValueError("UI Knowledge Review decision is invalid")
        if self.activate and self.decision != "approved":
            raise ValueError("Only approved UI Knowledge may become active")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("UI Knowledge Review reason must not be blank")


@dataclass(frozen=True, slots=True)
class UiKnowledgeReviewServiceResult:
    record: UiKnowledgeReviewRecord
    snapshot: UiKnowledgeSnapshot


class UiKnowledgeReviewService:
    def __init__(
        self,
        *,
        connection: Connection[Any],
        profiles: ProfileCatalog | None = None,
    ) -> None:
        self._connection = connection
        self._knowledge = UiKnowledgeRepository(connection)
        self._profiles = ProfileRepository(connection, profiles) if profiles is not None else None

    def review(
        self,
        request: UiKnowledgeReviewRequest,
    ) -> UiKnowledgeReviewServiceResult:
        with self._connection.transaction():
            record = self._knowledge.review(
                project_id=request.project_id,
                source_snapshot_id=request.source_snapshot_id,
                result_snapshot_id=request.result_snapshot_id,
                result_snapshot_version=request.result_snapshot_version,
                review_event_id=request.review_event_id,
                decision=request.decision,
                reviewed_by=request.reviewed_by,
                activate=request.activate,
                reason=request.reason,
            )
            snapshot = self._knowledge.load(
                project_id=request.project_id,
                snapshot_id=request.result_snapshot_id,
            )
            if request.decision == "approved" and self._profiles is not None:
                profile_version_id, profile = _ui_locator_profile(snapshot)
                self._profiles.store_version(
                    profile_version_id=profile_version_id,
                    profile=profile,
                )
                if request.activate:
                    self._profiles.activate(
                        activation_event_id=_stable_id(
                            "ui-locator-activation", request.review_event_id
                        ),
                        project_id=request.project_id,
                        binding_key=(
                            f"ui-locator:{snapshot.environment_id}:{snapshot.deployment_revision}"
                        ),
                        profile_version_id=profile_version_id,
                        activated_by=request.reviewed_by,
                        reason=request.reason or "Reviewed UI Locator Profile activation",
                    )
        return UiKnowledgeReviewServiceResult(record=record, snapshot=snapshot)


def _ui_locator_profile(snapshot: UiKnowledgeSnapshot) -> tuple[str, dict[str, object]]:
    identity = f"{snapshot.project_id}\0{snapshot.environment_id}\0{snapshot.deployment_revision}"
    profile_id = f"ui-locator-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
    version = snapshot.snapshot_version
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        version = f"0.0.{int(hashlib.sha256(version.encode()).hexdigest()[:8], 16)}"
    profile_version_id = _stable_id("ui-locator-profile", snapshot.snapshot_id)
    return profile_version_id, {
        "profile_type": "UiLocatorProfile",
        "profile_id": profile_id,
        "profile_version": version,
        "environment_id": snapshot.environment_id,
        "deployment_revision": snapshot.deployment_revision,
        "ui_knowledge_snapshot_id": snapshot.snapshot_id,
        "locator_policy_version": "semantic-first-v1",
        "minimum_reliability": 0.8,
        "target_refs": sorted(target.target_ref for target in snapshot.targets),
    }


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\0".join(values).encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"
