"""Automated browser readiness checks bound to approved UI Knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

from operamind.domain import UiKnowledgeSnapshot
from operamind.infrastructure.browser import BrowserPreflightProbe
from operamind.infrastructure.postgres import (
    UiBrowserManifestRepository,
    UiExecutionPlanRecord,
    UiKnowledgeRepository,
    UiKnowledgeSnapshotRecord,
    UiPreflightCheckWrite,
    UiVerificationRepository,
)


@dataclass(frozen=True, slots=True)
class BrowserPreflightRequest:
    project_id: str
    plan_id: str
    manifest_id: str
    attempt_id: str
    storage_state: Path | None = None

    def __post_init__(self) -> None:
        required = (self.project_id, self.plan_id, self.manifest_id, self.attempt_id)
        if any(not value.strip() for value in required):
            raise ValueError("Browser Preflight request fields must not be blank")


class BrowserPreflightService:
    """Resolve reviewed targets, inspect the deployment, and append one Attempt."""

    def __init__(self, *, connection: Connection[Any], probe: BrowserPreflightProbe) -> None:
        self._manifests = UiBrowserManifestRepository(connection)
        self._knowledge = UiKnowledgeRepository(connection)
        self._verification = UiVerificationRepository(connection)
        self._probe = probe

    def register_knowledge(self, snapshot: UiKnowledgeSnapshot) -> UiKnowledgeSnapshotRecord:
        return self._knowledge.store(snapshot)

    def inspect(self, request: BrowserPreflightRequest) -> UiExecutionPlanRecord:
        approved = self._manifests.load_for_preflight(
            project_id=request.project_id,
            plan_id=request.plan_id,
        )
        if approved.manifest.manifest_id != request.manifest_id:
            raise ValueError("Requested Browser Manifest is not the approved Plan Manifest")
        observations = self._probe.inspect(
            manifest=approved.manifest,
            base_url=approved.base_url,
            attempt_id=request.attempt_id,
            storage_state=request.storage_state,
        )
        checks = tuple(
            UiPreflightCheckWrite(
                check_id=f"{request.attempt_id}-{item.check_type}",
                check_type=item.check_type,
                status=item.status,
                evidence_ref=item.evidence_ref,
                reason=item.reason,
            )
            for item in observations
        )
        return self._verification.record_preflight(
            project_id=request.project_id,
            plan_id=request.plan_id,
            attempt_id=request.attempt_id,
            checks=checks,
        )
