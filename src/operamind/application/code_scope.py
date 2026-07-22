"""Evidence-bound orchestration for deterministic Code Graph scope resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.domain import (
    ChangeReviewStatus,
    CodeAnchor,
    CodeAnchorMatch,
    CodeGraphPath,
    CodeGraphTraversalPlanner,
    CodeTraversalResult,
    relation_policy_for_domain,
)
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    CanonicalRepository,
    CodeGraphQueryRepository,
    CodeGraphQueryScope,
    CodeNodeLocation,
    CodeTestFileBinding,
    ProfileRepository,
    StructuredChangeReviewRepository,
)
from operamind.profiles import ProfileCatalog


class CodeScopeBlockedError(ValueError):
    """Raised when immutable Context, Graph, Revision, or Profile scope does not match."""


@dataclass(frozen=True, slots=True)
class CodeScopeLimits:
    """Explicit row/state ceilings; overflow is a blocking unknown, never silent."""

    max_matches_per_anchor: int = 100
    max_edges: int = 100_000
    max_traversal_states: int = 10_000
    max_unresolved_edges: int = 100

    def __post_init__(self) -> None:
        if not 1 <= self.max_matches_per_anchor <= 10_000:
            raise ValueError("max_matches_per_anchor must be between 1 and 10000")
        if not 1 <= self.max_edges <= 2_000_000:
            raise ValueError("max_edges must be between 1 and 2000000")
        if not 1 <= self.max_traversal_states <= 1_000_000:
            raise ValueError("max_traversal_states must be between 1 and 1000000")
        if not 1 <= self.max_unresolved_edges <= 100_000:
            raise ValueError("max_unresolved_edges must be between 1 and 100000")


@dataclass(frozen=True, slots=True)
class CodeScopeRequest:
    """Exact Context, Change, Graph, Revision, Profile binding, and typed anchors."""

    project_id: str
    analysis_case_id: str
    context_package_id: str
    structured_change_id: str
    code_graph_snapshot_id: str
    repository_revision_id: str
    profile_binding_key: str
    anchors: tuple[CodeAnchor, ...]
    limits: CodeScopeLimits = field(default_factory=CodeScopeLimits)

    def __post_init__(self) -> None:
        required = (
            self.project_id,
            self.analysis_case_id,
            self.context_package_id,
            self.structured_change_id,
            self.code_graph_snapshot_id,
            self.repository_revision_id,
            self.profile_binding_key,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Code Scope request fields must not be blank")
        if not self.anchors:
            raise ValueError("Code Scope requires at least one typed anchor")
        anchor_ids = [anchor.anchor_id for anchor in self.anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("Code Scope anchor IDs must be unique")


@dataclass(frozen=True, slots=True)
class CodeScopeCandidate:
    """One file candidate with all document and graph-path evidence preserved."""

    file_id: str
    path: str
    role: str
    classification: str
    distance: int
    score: float
    anchor_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    target_symbols: tuple[str, ...]
    graph_paths: tuple[CodeGraphPath, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "path": self.path,
            "role": self.role,
            "classification": self.classification,
            "distance": self.distance,
            "score": self.score,
            "anchor_ids": list(self.anchor_ids),
            "evidence_refs": list(self.evidence_refs),
            "target_symbols": list(self.target_symbols),
            "graph_path_refs": sorted(
                {edge_id for path in self.graph_paths for edge_id in path.edge_ids}
            ),
            "graph_paths": [
                {
                    "anchor_id": path.anchor_id,
                    "node_refs": list(path.node_refs),
                    "edge_ids": list(path.edge_ids),
                    "directions": list(path.directions),
                    "distance": path.distance,
                }
                for path in self.graph_paths
            ],
        }


@dataclass(frozen=True, slots=True)
class CodeScopeResolutionResult:
    """Small approved-workset candidate ledger; not yet an Impact decision."""

    project_id: str
    analysis_case_id: str
    context_package_id: str
    structured_change_id: str
    code_graph_snapshot_id: str
    repository_revision: str
    profile_ref: str
    relation_policy_domain: str | None
    candidates: tuple[CodeScopeCandidate, ...]
    editable_files: tuple[str, ...]
    read_only_files: tuple[str, ...]
    test_files: tuple[str, ...]
    unknown_items: tuple[str, ...]
    confirmation_blocked: bool
    out_of_scope_policy: str = "stop_and_reanalyze"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_format_version": "v1",
            "project_id": self.project_id,
            "analysis_case_id": self.analysis_case_id,
            "context_package_id": self.context_package_id,
            "structured_change_id": self.structured_change_id,
            "code_graph_snapshot_id": self.code_graph_snapshot_id,
            "repository_revision": self.repository_revision,
            "profile_ref": self.profile_ref,
            "relation_policy_domain": self.relation_policy_domain,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "editable_files": list(self.editable_files),
            "read_only_files": list(self.read_only_files),
            "test_files": list(self.test_files),
            "unknown_items": list(self.unknown_items),
            "confirmation_blocked": self.confirmation_blocked,
            "out_of_scope_policy": self.out_of_scope_policy,
        }


class CodeScopeResolverService:
    """Resolve typed anchors without AI guessing and classify a bounded file workset."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        profiles: ProfileCatalog,
    ) -> None:
        self._profiles = profiles
        self._artifacts = ArtifactRepository(connection, contracts)
        self._canonical = CanonicalRepository(connection, contracts)
        self._reviews = StructuredChangeReviewRepository(connection)
        self._profile_repository = ProfileRepository(connection, profiles)
        self._graphs = CodeGraphQueryRepository(connection, contracts)
        self._traversal = CodeGraphTraversalPlanner()

    def resolve(self, request: CodeScopeRequest) -> CodeScopeResolutionResult:
        """Validate immutable evidence, traverse the current graph, and expose unknowns."""

        context, change, allowed_evidence = self._validate_document_scope(request)
        graph = self._graphs.get_scope(
            project_id=request.project_id,
            code_graph_snapshot_id=request.code_graph_snapshot_id,
        )
        if graph is None:
            raise CodeScopeBlockedError("Code Graph Snapshot does not exist in the project")
        if not graph.is_current or graph.status not in {"complete", "truncated"}:
            raise CodeScopeBlockedError("Code Graph Snapshot is not current and queryable")
        if graph.repository_revision_id != request.repository_revision_id:
            raise CodeScopeBlockedError("Code Graph Repository Revision does not match")
        active = self._profile_repository.get_active(
            project_id=request.project_id,
            binding_key=request.profile_binding_key,
        )
        if active is None:
            raise CodeScopeBlockedError("Code Framework Profile binding is not active")
        self._profiles.validate_profile(active.profile)
        if active.profile.get("profile_type") != "CodeFrameworkProfile":
            raise CodeScopeBlockedError("Active Scope Profile is not a CodeFrameworkProfile")
        profile_ref = f"{active.profile['profile_id']}@{active.profile['profile_version']}"
        if (profile_ref, active.profile_version_id) not in graph.profile_versions:
            raise CodeScopeBlockedError("Active Code Framework Profile is outside Graph scope")
        for anchor in request.anchors:
            unknown_evidence = sorted(set(anchor.evidence_refs) - allowed_evidence)
            if unknown_evidence:
                raise CodeScopeBlockedError(
                    f"Code anchor evidence is outside Context Package: {unknown_evidence}"
                )

        change_domain = str(change["domain"])
        policy = relation_policy_for_domain(active.profile, change_domain)
        match_load = self._graphs.match_anchors(
            scope=graph,
            anchors=request.anchors,
            max_matches_per_anchor=request.limits.max_matches_per_anchor,
        )
        unknowns = {
            f"anchor_match_overflow:{anchor_id}" for anchor_id in match_load.overflow_anchor_ids
        }
        matched_anchor_ids = {match.anchor_id for match in match_load.matches}
        unknowns.update(
            f"anchor_not_found:{anchor.anchor_id}"
            for anchor in request.anchors
            if anchor.anchor_id not in matched_anchor_ids
        )
        if graph.status == "truncated":
            unknowns.update(f"code_graph_diagnostic:{value}" for value in graph.diagnostics)

        if policy is None:
            unknowns.add(f"missing_relation_policy:{change_domain}")
            traversal = _direct_only_traversal(
                match_load.matches,
                max_states=request.limits.max_traversal_states,
            )
        else:
            edge_load = self._graphs.load_resolved_edges(
                scope=graph,
                edge_types=policy.edge_types,
                max_edges=request.limits.max_edges,
            )
            if edge_load.truncated:
                unknowns.add("code_edge_load_truncated")
            traversal = self._traversal.traverse(
                anchors=request.anchors,
                matches=match_load.matches,
                edges=edge_load.edges,
                policy=policy,
                max_states=request.limits.max_traversal_states,
            )
        if traversal.truncated:
            unknowns.add("code_traversal_truncated")

        node_refs = tuple(sorted({path.node_ref for path in traversal.paths}))
        locations = self._graphs.hydrate_refs(scope=graph, node_refs=node_refs)
        location_by_ref = {location.node_ref: location for location in locations}
        missing_refs = sorted(set(node_refs) - set(location_by_ref))
        unknowns.update(f"unhydratable_code_ref:{value}" for value in missing_refs)
        unresolved = self._graphs.load_incident_unresolved_edge_ids(
            scope=graph,
            node_refs=node_refs,
            max_edges=request.limits.max_unresolved_edges,
        )
        unknowns.update(f"unresolved_code_edge:{edge_id}" for edge_id in unresolved.edge_ids)
        if unresolved.truncated:
            unknowns.add("unresolved_code_edge_ledger_truncated")

        candidates = self._build_candidates(
            request=request,
            graph=graph,
            paths=traversal.paths,
            location_by_ref=location_by_ref,
        )
        return CodeScopeResolutionResult(
            project_id=request.project_id,
            analysis_case_id=request.analysis_case_id,
            context_package_id=str(context["context_package_id"]),
            structured_change_id=request.structured_change_id,
            code_graph_snapshot_id=graph.code_graph_snapshot_id,
            repository_revision=graph.commit_sha,
            profile_ref=profile_ref,
            relation_policy_domain=policy.change_domain if policy is not None else None,
            candidates=candidates,
            editable_files=tuple(
                candidate.path for candidate in candidates if candidate.classification == "editable"
            ),
            read_only_files=tuple(
                candidate.path
                for candidate in candidates
                if candidate.classification == "read_only"
            ),
            test_files=tuple(
                candidate.path for candidate in candidates if candidate.classification == "test"
            ),
            unknown_items=tuple(sorted(unknowns)),
            confirmation_blocked=bool(unknowns),
        )

    def _validate_document_scope(
        self, request: CodeScopeRequest
    ) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
        context = self._artifacts.get(request.context_package_id)
        if context is None or context.get("artifact_type") != "ContextPackage":
            raise CodeScopeBlockedError("Context Package does not exist")
        expected_context = (
            request.project_id,
            request.analysis_case_id,
            [request.structured_change_id],
        )
        actual_context = (
            context.get("project_id"),
            context.get("analysis_case_id"),
            context.get("structured_change_refs"),
        )
        if actual_context != expected_context:
            raise CodeScopeBlockedError("Context Package is outside Code Scope request")
        change = self._canonical.get_change_artifact(request.structured_change_id)
        if change is None or change.get("project_id") != request.project_id:
            raise CodeScopeBlockedError("StructuredChange does not exist in the project")
        if change.get("target_snapshot_id") != context.get("document_snapshot_id"):
            raise CodeScopeBlockedError("StructuredChange Snapshot does not match Context Package")
        review = self._reviews.get_state(
            project_id=request.project_id,
            change_id=request.structured_change_id,
        )
        if review is None or review.status is not ChangeReviewStatus.ACCEPTED:
            raise CodeScopeBlockedError("StructuredChange is not currently accepted")
        context_items = cast(list[dict[str, Any]], context["context_items"])
        allowed_evidence = {
            str(value)
            for item in context_items
            for value in cast(list[object], item["evidence_refs"])
        }
        return context, change, allowed_evidence

    def _build_candidates(
        self,
        *,
        request: CodeScopeRequest,
        graph: CodeGraphQueryScope,
        paths: tuple[CodeGraphPath, ...],
        location_by_ref: dict[str, CodeNodeLocation],
    ) -> tuple[CodeScopeCandidate, ...]:
        anchor_by_id = {anchor.anchor_id: anchor for anchor in request.anchors}
        accumulators: dict[str, _CandidateAccumulator] = {}
        for path in paths:
            location = location_by_ref.get(path.node_ref)
            if location is None:
                continue
            accumulator = accumulators.setdefault(
                location.file_id,
                _CandidateAccumulator(
                    file_id=location.file_id,
                    path=location.path,
                    role=location.role,
                ),
            )
            accumulator.paths.append(path)
            accumulator.anchor_ids.add(path.anchor_id)
            accumulator.evidence_refs.update(anchor_by_id[path.anchor_id].evidence_refs)
            accumulator.distance = min(accumulator.distance, path.distance)
            if location.symbol_signature is not None:
                accumulator.symbols.add(location.symbol_signature)

        production_ids = tuple(
            sorted(file_id for file_id, value in accumulators.items() if value.role != "test")
        )
        bindings = self._graphs.load_test_bindings(
            scope=graph,
            production_file_ids=production_ids,
        )
        for binding in bindings:
            production = accumulators.get(binding.production_file_id)
            if production is None:
                continue
            test = accumulators.setdefault(
                binding.test_file_id,
                _CandidateAccumulator(
                    file_id=binding.test_file_id,
                    path=binding.test_path,
                    role="test",
                ),
            )
            for path in production.paths:
                test_path = _binding_path(path, binding)
                test.paths.append(test_path)
                test.anchor_ids.add(path.anchor_id)
                test.evidence_refs.update(anchor_by_id[path.anchor_id].evidence_refs)
                test.distance = min(test.distance, test_path.distance)

        candidates: list[CodeScopeCandidate] = []
        for value in accumulators.values():
            direct = any(path.distance == 0 for path in value.paths)
            classification = (
                "test" if value.role == "test" else "editable" if direct else "read_only"
            )
            candidates.append(
                CodeScopeCandidate(
                    file_id=value.file_id,
                    path=value.path,
                    role=value.role,
                    classification=classification,
                    distance=value.distance,
                    score=round(max(0.0, 1.0 - 0.2 * value.distance), 3),
                    anchor_ids=tuple(sorted(value.anchor_ids)),
                    evidence_refs=tuple(sorted(value.evidence_refs)),
                    target_symbols=tuple(sorted(value.symbols)),
                    graph_paths=tuple(
                        sorted(
                            set(value.paths),
                            key=lambda path: (
                                path.distance,
                                path.anchor_id,
                                path.node_ref,
                                path.edge_ids,
                            ),
                        )
                    ),
                )
            )
        return tuple(
            sorted(
                candidates,
                key=lambda value: (
                    {"editable": 0, "read_only": 1, "test": 2}[value.classification],
                    value.distance,
                    value.path,
                ),
            )
        )


@dataclass(slots=True)
class _CandidateAccumulator:
    file_id: str
    path: str
    role: str
    distance: int = 1_000_000
    anchor_ids: set[str] = field(default_factory=set)
    evidence_refs: set[str] = field(default_factory=set)
    symbols: set[str] = field(default_factory=set)
    paths: list[CodeGraphPath] = field(default_factory=list)


def _direct_only_traversal(
    matches: tuple[CodeAnchorMatch, ...], *, max_states: int
) -> CodeTraversalResult:
    paths = tuple(
        CodeGraphPath(
            anchor_id=match.anchor_id,
            node_ref=match.node_ref,
            node_refs=(match.node_ref,),
            edge_ids=match.via_edge_ids,
            directions=tuple("anchor" for _ in match.via_edge_ids),
            distance=0,
        )
        for match in matches[:max_states]
    )
    return CodeTraversalResult(paths=paths, truncated=len(matches) > max_states)


def _binding_path(path: CodeGraphPath, binding: CodeTestFileBinding) -> CodeGraphPath:
    return CodeGraphPath(
        anchor_id=path.anchor_id,
        node_ref=binding.test_file_id,
        node_refs=(*path.node_refs, binding.test_file_id),
        edge_ids=(*path.edge_ids, binding.source_edge_id),
        directions=(*path.directions, "test_binding"),
        distance=path.distance + 1,
    )
