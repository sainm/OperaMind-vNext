"""Build Contract-valid Impact Reports from one evidence-bound Code Scope."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any, cast

from psycopg import Connection

from operamind.application.code_scope import (
    CodeScopeCandidate,
    CodeScopeRequest,
    CodeScopeResolutionResult,
    CodeScopeResolverService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    CodeGraphQueryRepository,
    GoldenRagQualityRepository,
    ImpactReportPublishResult,
    ImpactRepository,
)
from operamind.profiles import ProfileCatalog


class UiImpactStatus(StrEnum):
    """Explicit UI impact decision; unknown remains blocking."""

    IMPACTED = "impacted"
    NOT_IMPACTED = "not_impacted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ImpactReportRequest:
    """Exact report identity plus the already bounded Scope request."""

    impact_report_id: str
    scope: CodeScopeRequest
    ui_impact_status: UiImpactStatus
    required_ui_scenario_refs: tuple[str, ...] = ()
    planned_test_files: tuple[str, ...] = ()
    analysis_policy_version: str = "scope-impact-v1"

    def __post_init__(self) -> None:
        if not self.impact_report_id.strip() or not self.analysis_policy_version.strip():
            raise ValueError("Impact Report identity and policy version must not be blank")
        if any(not value.strip() for value in self.required_ui_scenario_refs):
            raise ValueError("UI Scenario refs must not be blank")
        if len(self.required_ui_scenario_refs) != len(set(self.required_ui_scenario_refs)):
            raise ValueError("UI Scenario refs must be unique")
        if len(self.planned_test_files) != len(set(self.planned_test_files)):
            raise ValueError("Planned test files must be unique")
        for value in self.planned_test_files:
            path = PurePosixPath(value)
            folded_parts = {part.casefold() for part in path.parts}
            if (
                not value.strip()
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in value
                or not folded_parts.intersection({"test", "tests"})
                or not path.suffix
            ):
                raise ValueError(
                    f"Planned test file must be a safe test path with an extension: {value}"
                )
        if self.ui_impact_status is UiImpactStatus.NOT_IMPACTED and self.required_ui_scenario_refs:
            raise ValueError("not_impacted reports must not require UI Scenarios")


@dataclass(frozen=True, slots=True)
class ImpactReportResult:
    """Published report plus the exact Scope ledger used to construct it."""

    artifact: dict[str, Any]
    publication: ImpactReportPublishResult
    scope: CodeScopeResolutionResult


class ImpactReportService:
    """Convert deterministic Scope candidates into a persisted review boundary."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        profiles: ProfileCatalog,
    ) -> None:
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)
        self._graphs = CodeGraphQueryRepository(connection, contracts)
        self._rag_quality = GoldenRagQualityRepository(connection, contracts)
        self._scope = CodeScopeResolverService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        )
        self._reports = ImpactRepository(connection, contracts)

    def run(self, request: ImpactReportRequest) -> ImpactReportResult:
        """Build and publish a blocked or awaiting-confirmation report."""

        context = self._artifacts.get(request.scope.context_package_id)
        if context is None or context.get("artifact_type") != "ContextPackage":
            raise ValueError("Impact Report Context Package does not exist")
        retrieval_policy = cast(dict[str, object], context["retrieval_policy"])
        self._rag_quality.require_passed_gate(
            project_id=request.scope.project_id,
            document_snapshot_id=str(context["document_snapshot_id"]),
            embedding_profile_version_id=str(retrieval_policy["embedding_profile_version_id"]),
            search_index_build_id=str(context["search_index_build_id"]),
        )
        scope = self._scope.resolve(request.scope)
        graph = self._graphs.get_scope(
            project_id=request.scope.project_id,
            code_graph_snapshot_id=request.scope.code_graph_snapshot_id,
        )
        if graph is None:
            raise ValueError("Impact Report Code Graph does not exist")

        blocking_unknowns = set(scope.unknown_items)
        blocking_unknowns.update(
            f"context_unknown:{value}" for value in cast(list[object], context.get("unknowns", []))
        )
        if request.ui_impact_status is UiImpactStatus.UNKNOWN:
            blocking_unknowns.add("ui_impact_unknown")
        if (
            request.ui_impact_status is UiImpactStatus.IMPACTED
            and not request.required_ui_scenario_refs
        ):
            blocking_unknowns.add("missing_required_ui_scenario")
        production_candidates = tuple(
            candidate for candidate in scope.candidates if candidate.classification != "test"
        )
        if not any(candidate.classification == "editable" for candidate in production_candidates):
            blocking_unknowns.add("no_editable_code_candidate")

        items = [
            self._impact_item(
                report_id=request.impact_report_id,
                change_id=request.scope.structured_change_id,
                candidate=candidate,
                scope=scope,
            )
            for candidate in production_candidates
        ]
        candidate_paths = {candidate.path for candidate in scope.candidates}
        overlapping_tests = sorted(set(request.planned_test_files) & candidate_paths)
        if overlapping_tests:
            raise ValueError(
                f"Planned test additions already exist in the Code Graph: {overlapping_tests}"
            )
        evidence_refs = tuple(
            sorted(
                {
                    evidence_ref
                    for anchor in request.scope.anchors
                    for evidence_ref in anchor.evidence_refs
                }
            )
        )
        items.extend(
            self._planned_test_item(
                report_id=request.impact_report_id,
                change_id=request.scope.structured_change_id,
                target_path=path,
                evidence_refs=evidence_refs,
            )
            for path in request.planned_test_files
        )
        status = "blocked" if blocking_unknowns else "awaiting_confirmation"
        artifact: dict[str, Any] = {
            "artifact_type": "ImpactReport",
            "schema_version": "v1",
            "impact_report_id": request.impact_report_id,
            "analysis_case_id": request.scope.analysis_case_id,
            "project_id": request.scope.project_id,
            "document_snapshot_id": context["document_snapshot_id"],
            "context_package_id": request.scope.context_package_id,
            "code_graph_snapshot_id": request.scope.code_graph_snapshot_id,
            "repository_revision": scope.repository_revision,
            "analysis_policy_version": request.analysis_policy_version,
            "status": status,
            "summary": str(context["business_summary"]),
            "items": items,
            "ui_impact_status": request.ui_impact_status.value,
            "required_ui_scenario_refs": list(request.required_ui_scenario_refs),
            "blocking_unknowns": sorted(blocking_unknowns),
        }
        self._contracts.validate_artifact(artifact)
        publication = self._reports.publish_report(
            artifact=artifact,
            repository_id=graph.repository_id,
            repository_revision_id=graph.repository_revision_id,
        )
        return ImpactReportResult(artifact=artifact, publication=publication, scope=scope)

    @staticmethod
    def _impact_item(
        *,
        report_id: str,
        change_id: str,
        candidate: CodeScopeCandidate,
        scope: CodeScopeResolutionResult,
    ) -> dict[str, Any]:
        test_files = sorted(
            test.path
            for test in scope.candidates
            if test.classification == "test" and set(test.anchor_ids) & set(candidate.anchor_ids)
        )
        editable = candidate.classification == "editable"
        material = "\x00".join((report_id, change_id, str(candidate.path)))
        return {
            "impact_item_id": f"impact-item-{sha256(material.encode()).hexdigest()[:24]}",
            "structured_change_refs": [change_id],
            "target_path": candidate.path,
            "target_symbols": list(candidate.target_symbols),
            "impact_level": "high" if editable else "medium",
            "impact_score": candidate.score,
            "recommended_action": "modify" if editable else "review_only",
            "rationale": (
                "Direct typed-anchor match backed by Context Package evidence."
                if editable
                else "Profile-allowed Code Graph expansion from a typed anchor."
            ),
            "evidence_refs": list(candidate.evidence_refs),
            "graph_path_refs": sorted(
                {edge_id for path in candidate.graph_paths for edge_id in path.edge_ids}
            ),
            "test_file_refs": test_files,
            "requires_confirmation": editable,
            "unknowns": [],
        }

    @staticmethod
    def _planned_test_item(
        *,
        report_id: str,
        change_id: str,
        target_path: str,
        evidence_refs: tuple[str, ...],
    ) -> dict[str, Any]:
        material = "\x00".join((report_id, change_id, target_path))
        return {
            "impact_item_id": f"impact-item-{sha256(material.encode()).hexdigest()[:24]}",
            "structured_change_refs": [change_id],
            "target_path": target_path,
            "target_symbols": [],
            "impact_level": "high",
            "impact_score": 1.0,
            "recommended_action": "add",
            "rationale": "Confirmed Draft verification plan requires a new executable test file.",
            "evidence_refs": list(evidence_refs),
            "graph_path_refs": [],
            "test_file_refs": [target_path],
            "requires_confirmation": True,
            "unknowns": [],
        }
