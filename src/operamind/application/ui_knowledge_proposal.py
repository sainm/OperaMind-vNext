"""Propose draft UI Knowledge from one persisted Canonical Snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.domain import UiKnowledgeProposal, UiKnowledgeProposalBuilder
from operamind.infrastructure.postgres import CanonicalRepository


@dataclass(frozen=True, slots=True)
class UiKnowledgeProposalRequest:
    project_id: str
    document_snapshot_id: str
    environment_id: str
    deployment_revision: str
    snapshot_id: str
    snapshot_version: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.project_id,
                self.document_snapshot_id,
                self.environment_id,
                self.deployment_revision,
                self.snapshot_id,
                self.snapshot_version,
            )
        ):
            raise ValueError("UI Knowledge Proposal request fields must not be blank")


class UiKnowledgeProposalService:
    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
    ) -> None:
        self._connection = connection
        self._canonical = CanonicalRepository(connection, contracts)
        self._builder = UiKnowledgeProposalBuilder()

    def propose(self, request: UiKnowledgeProposalRequest) -> UiKnowledgeProposal:
        source = self._canonical.get_snapshot(
            project_id=request.project_id,
            snapshot_id=request.document_snapshot_id,
        )
        if source is None:
            raise ValueError("Canonical document Snapshot does not exist in requested project")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM ui_deployments
                WHERE project_id = %s AND environment_id = %s
                  AND deployment_revision = %s AND status = 'ready'
                """,
                (request.project_id, request.environment_id, request.deployment_revision),
            )
            if cursor.fetchone() is None:
                raise ValueError("UI Knowledge Proposal requires the exact ready Deployment")
        return self._builder.build(
            source=source,
            snapshot_id=request.snapshot_id,
            project_id=request.project_id,
            environment_id=request.environment_id,
            deployment_revision=request.deployment_revision,
            snapshot_version=request.snapshot_version,
        )
