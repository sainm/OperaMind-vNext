"""Authorize P6 execution from the persisted P2-P5 Canonical chain."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from psycopg import Connection

from operamind.application.change_loop import ChangeLoopBlockedError, ChangeLoopPlan
from operamind.application.change_loop_execution import CanonicalExecutionBinding
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.approval_grant_repository import (
    ApprovalGrantAuthorization,
    ApprovalGrantRepository,
)
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.edit_packet_repository import EditPacketRepository
from operamind.infrastructure.postgres.impact_repository import ImpactRepository


class PostgresCanonicalExecutionAuthorizer:
    """Fail closed unless the plan is an exact view of one active persisted Grant chain."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        approval_grant_id: str,
        edit_packet_id: str,
    ) -> None:
        if not approval_grant_id.strip() or not edit_packet_id.strip():
            raise ValueError("Canonical execution Grant and Packet IDs must not be blank")
        self._approval_grant_id = approval_grant_id
        self._edit_packet_id = edit_packet_id
        self._artifacts = ArtifactRepository(connection, contracts)
        self._grants = ApprovalGrantRepository(connection, contracts)
        self._packets = EditPacketRepository(connection, contracts)
        self._impacts = ImpactRepository(connection, contracts)

    def authorize(self, *, plan: ChangeLoopPlan) -> CanonicalExecutionBinding:
        """Re-read all immutable artifacts and current normalized state before execution."""

        try:
            grant, packet, impact, confirmation, _, graph = self._load_chain(plan)
            self._require_plan_artifact(plan, packet)
            self._require_plan_artifact(plan, impact)
            self._require_plan_artifact(plan, confirmation)
            self._require_plan_artifact(plan, graph)
        except (KeyError, RuntimeError, ValueError) as error:
            raise ChangeLoopBlockedError(
                f"Canonical execution authorization failed: {error}"
            ) from error

        return CanonicalExecutionBinding(
            project_id=grant.project_id,
            analysis_case_id=grant.analysis_case_id,
            context_package_id=str(impact["context_package_id"]),
            code_graph_snapshot_id=str(impact["code_graph_snapshot_id"]),
            impact_report_id=grant.impact_report_id,
            confirmation_id=grant.confirmation_id,
            edit_packet_id=grant.edit_packet_id,
            approval_grant_id=grant.grant_id,
            base_revision=grant.base_repository_revision,
        )

    def hydrate(self, *, plan: ChangeLoopPlan) -> ChangeLoopPlan:
        """Replace legacy planner copies with the exact persisted Canonical artifacts."""

        try:
            grant, packet, impact, confirmation, _, graph = self._load_chain(plan)
            self._require_plan_artifact(plan, graph)
            editable_files = frozenset(str(value) for value in packet["editable_files"])
            replacement_paths = frozenset(value.path for value in plan.replacements)
            if editable_files != plan.allowed_edit_paths or replacement_paths != editable_files:
                raise ValueError("P6 replacement paths differ from the Canonical Edit Packet")
            if frozenset(cast(list[str], packet["forbidden_globs"])) != plan.forbidden_paths:
                raise ValueError("P6 forbidden paths differ from the Canonical Edit Packet")
            if packet.get("base_repository_revision") != grant.base_repository_revision:
                raise ValueError("Canonical Edit Packet revision differs from the Grant")
        except (KeyError, RuntimeError, ValueError) as error:
            raise ChangeLoopBlockedError(
                f"Canonical plan hydration failed: {error}"
            ) from error

        canonical_by_type = {
            value["artifact_type"]: value for value in (packet, impact, confirmation, graph)
        }
        return replace(
            plan,
            artifacts=tuple(
                canonical_by_type.get(str(value["artifact_type"]), value)
                for value in plan.artifacts
            ),
        )

    def _load_chain(
        self, plan: ChangeLoopPlan
    ) -> tuple[
        ApprovalGrantAuthorization,
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        grant = self._grants.authorize_edit(
            grant_id=self._approval_grant_id,
            project_id=plan.request.project_id,
            analysis_case_id=plan.request.change_request_id,
            edit_packet_id=self._edit_packet_id,
            required_action="modify",
        )
        packet_record = self._packets.get(grant.edit_packet_id)
        if packet_record is None:
            raise ValueError("Canonical Edit Packet does not exist")
        if (
            packet_record.status != "active"
            or packet_record.impact_report_status != "confirmed"
        ):
            raise ValueError("Canonical Edit Packet is no longer active and confirmed")
        impact_state = self._impacts.get_state(grant.impact_report_id)
        if impact_state is None or impact_state.status != "confirmed":
            raise ValueError("Canonical Impact Report is no longer confirmed")

        packet = self._require_artifact(grant.edit_packet_id, "CopilotEditPacket")
        impact = self._require_artifact(grant.impact_report_id, "ImpactReport")
        confirmation = self._require_artifact(grant.confirmation_id, "ImpactConfirmation")
        context = self._require_artifact(str(impact["context_package_id"]), "ContextPackage")
        graph = self._require_artifact(
            str(impact["code_graph_snapshot_id"]), "CodeGraphSnapshot"
        )
        self._require_real_rag_context(
            context,
            project_id=grant.project_id,
            analysis_case_id=grant.analysis_case_id,
        )
        if graph.get("scan_status") != "complete":
            raise ValueError("Canonical Code Graph is not complete")
        if graph.get("repository_revision") != grant.base_repository_revision:
            raise ValueError("Canonical Code Graph revision differs from the Grant")
        return grant, packet, impact, confirmation, context, graph

    def _require_artifact(self, artifact_id: str, artifact_type: str) -> dict[str, Any]:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None or artifact.get("artifact_type") != artifact_type:
            raise ValueError(f"Canonical {artifact_type} Artifact is missing: {artifact_id}")
        return artifact

    @staticmethod
    def _require_plan_artifact(plan: ChangeLoopPlan, canonical: dict[str, Any]) -> None:
        artifact_type = str(canonical["artifact_type"])
        if plan.artifact(artifact_type) != canonical:
            raise ValueError(f"P6 plan differs from persisted {artifact_type}")

    @staticmethod
    def _require_real_rag_context(
        context: dict[str, Any],
        *,
        project_id: str,
        analysis_case_id: str,
    ) -> None:
        if (context.get("project_id"), context.get("analysis_case_id")) != (
            project_id,
            analysis_case_id,
        ):
            raise ValueError("Canonical Context Package is outside Grant scope")
        if cast(list[object], context.get("unknowns", [])):
            raise ValueError("Canonical Context Package still has unresolved unknowns")
        if not cast(list[object], context.get("context_items", [])):
            raise ValueError("Canonical Context Package contains no retrieved evidence")
        trace = cast(list[dict[str, Any]], context.get("retrieval_trace", []))
        if not trace or any(value.get("retrieval_mode") != "hybrid" for value in trace):
            raise ValueError("Canonical Context Package has no real hybrid retrieval trace")
