"""Build one Contract-validated ContextPackage from formal hybrid retrieval."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, cast

from psycopg import Connection

from operamind.application.hybrid_search import (
    HybridSearchRequest,
    HybridSearchResult,
    HybridSearchService,
)
from operamind.contracts import ContractCatalog
from operamind.domain import (
    ChangeReviewStatus,
    DocumentNodeType,
    RagQueryPlan,
    RagQueryPurpose,
    StructuredChangeQueryPlanner,
)
from operamind.infrastructure.embeddings import EmbeddingProvider
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    CanonicalRepository,
    DocumentIngestionResultEvent,
    DocumentIngestionResultRepository,
    DocumentIngestionStatus,
    DocumentNodeRecord,
    DocumentNodeRepository,
    DocumentRelationBuildState,
    DocumentRelationRepository,
    ProfileRepository,
    SearchIndexBuildStatus,
    SearchIndexRepository,
    StructuredChangeReviewRepository,
)
from operamind.profiles import ProfileCatalog


class ContextPackageBlockedError(ValueError):
    """Raised when current evidence cannot produce a formal ContextPackage."""


class ContextPackageBudgetError(ContextPackageBlockedError):
    """Raised instead of silently truncating the Canonical candidate ledger."""


@dataclass(frozen=True, slots=True)
class ContextPackageRequest:
    """Exact analysis, readiness, retrieval, and budget scope."""

    context_package_id: str
    project_id: str
    analysis_case_id: str
    ingestion_batch_id: str
    ingestion_result_event_id: str
    target_snapshot_id: str
    change_id: str
    embedding_profile_version_id: str
    embedding_profile_binding_key: str
    token_budget: int
    vector_top_k: int = 10
    keyword_top_k: int = 10
    final_top_k: int = 10
    adjacent_distance: int = 1

    def __post_init__(self) -> None:
        required = (
            self.context_package_id,
            self.project_id,
            self.analysis_case_id,
            self.ingestion_batch_id,
            self.ingestion_result_event_id,
            self.target_snapshot_id,
            self.change_id,
            self.embedding_profile_version_id,
            self.embedding_profile_binding_key,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Context Package request fields must not be blank")
        if not 1 <= self.token_budget <= 1_000_000:
            raise ValueError("token_budget must be between 1 and 1000000")
        for name, value in (
            ("vector_top_k", self.vector_top_k),
            ("keyword_top_k", self.keyword_top_k),
            ("final_top_k", self.final_top_k),
        ):
            if not 1 <= value <= 1_000:
                raise ValueError(f"{name} must be between 1 and 1000")
        if not 0 <= self.adjacent_distance <= 10:
            raise ValueError("adjacent_distance must be between 0 and 10")


@dataclass(frozen=True, slots=True)
class ContextPackageResult:
    """Persisted ContextPackage and the deterministic Query Plan used."""

    created: bool
    artifact: dict[str, Any]
    query_plan: RagQueryPlan


class ContextPackageService:
    """Plan, retrieve IDs, rehydrate Canonical sections, and persist evidence."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        profiles: ProfileCatalog,
    ) -> None:
        self._connection = connection
        self._contracts = contracts
        self._profiles = profiles
        self._artifacts = ArtifactRepository(connection, contracts)
        self._canonical = CanonicalRepository(connection, contracts)
        self._ingestion = DocumentIngestionResultRepository(connection, contracts)
        self._nodes = DocumentNodeRepository(connection)
        self._relations = DocumentRelationRepository(connection)
        self._profile_repository = ProfileRepository(connection, profiles)
        self._reviews = StructuredChangeReviewRepository(connection)
        self._indexes = SearchIndexRepository(connection)
        self._search = HybridSearchService(
            profiles=profiles,
            profile_repository=self._profile_repository,
            review_repository=self._reviews,
            index_repository=self._indexes,
        )
        self._planner = StructuredChangeQueryPlanner()

    def run(
        self,
        request: ContextPackageRequest,
        *,
        provider: EmbeddingProvider,
    ) -> ContextPackageResult:
        """Create one immutable package; exact persisted replay skips retrieval."""

        existing = self._artifacts.get(request.context_package_id)
        if existing is not None:
            self._validate_existing(request, existing)
            change = self._load_change(request)
            return ContextPackageResult(
                created=False,
                artifact=existing,
                query_plan=self._planner.plan(change),
            )

        readiness, change, embedding_profile = self._validate_scope(request)
        relation_build = self._relations.get_current_build(
            project_id=request.project_id,
            snapshot_id=request.target_snapshot_id,
        )
        plan = self._planner.plan(change)
        search_results = tuple(
            self._search.run(
                HybridSearchRequest(
                    project_id=request.project_id,
                    target_snapshot_id=request.target_snapshot_id,
                    change_id=request.change_id,
                    embedding_profile_version_id=request.embedding_profile_version_id,
                    profile_binding_key=request.embedding_profile_binding_key,
                    source_query_id=query.query_id,
                    query_text=query.text,
                    vector_top_k=request.vector_top_k,
                    keyword_top_k=request.keyword_top_k,
                    final_top_k=request.final_top_k,
                ),
                provider=provider,
            )
            for query in plan.queries
        )
        build_ids = {result.search_index_build_id for result in search_results}
        ranking_versions = {result.ranking_policy_version for result in search_results}
        if build_ids != {readiness.search_index_build_id} or len(ranking_versions) != 1:
            raise ContextPackageBlockedError("Retrieval scope drifted from readiness evidence")

        with self._connection.transaction():
            self._lock_scope(request, readiness, relation_build=relation_build)
            locked_readiness, locked_change, locked_profile = self._validate_scope(request)
            if locked_readiness.event_id != readiness.event_id or locked_change != change:
                raise ContextPackageBlockedError("Context evidence changed during retrieval")
            if locked_profile != embedding_profile:
                raise ContextPackageBlockedError("Embedding Profile changed during retrieval")
            locked_relation_build = self._relations.get_current_build(
                project_id=request.project_id,
                snapshot_id=request.target_snapshot_id,
            )
            if locked_relation_build != relation_build:
                raise ContextPackageBlockedError("Document Relation Build changed during retrieval")
            artifact = self._build_artifact(
                request=request,
                readiness=locked_readiness,
                change=locked_change,
                embedding_profile=locked_profile,
                plan=plan,
                search_results=search_results,
                ranking_policy_version=next(iter(ranking_versions)),
                relation_build=locked_relation_build,
            )
            self._contracts.validate_artifact(artifact)
            concurrent = self._artifacts.get(request.context_package_id)
            created = concurrent is None
            self._artifacts.store(
                artifact_id=request.context_package_id,
                project_id=request.project_id,
                analysis_case_id=request.analysis_case_id,
                artifact=artifact,
            )
        return ContextPackageResult(created=created, artifact=artifact, query_plan=plan)

    def _validate_scope(
        self,
        request: ContextPackageRequest,
    ) -> tuple[DocumentIngestionResultEvent, dict[str, Any], dict[str, Any]]:
        readiness = self._ingestion.get_latest(
            project_id=request.project_id,
            ingestion_batch_id=request.ingestion_batch_id,
        )
        if readiness is None or readiness.event_id != request.ingestion_result_event_id:
            raise ContextPackageBlockedError("Current ingestion readiness event does not match")
        if (
            readiness.analysis_case_id != request.analysis_case_id
            or readiness.status is not DocumentIngestionStatus.READY_FOR_IMPACT
            or readiness.search_index_build_id is None
            or readiness.artifact.get("target_snapshot_id") != request.target_snapshot_id
        ):
            raise ContextPackageBlockedError("Ingestion evidence is not ready in requested scope")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, status
                FROM analysis_cases
                WHERE analysis_case_id = %s
                """,
                (request.analysis_case_id,),
            )
            case = cursor.fetchone()
        if case != (request.project_id, "ready_for_impact"):
            raise ContextPackageBlockedError("Analysis Case is not ready for impact")
        change = self._load_change(request)
        review = self._reviews.get_state(
            project_id=request.project_id,
            change_id=request.change_id,
        )
        if review is None or review.status is not ChangeReviewStatus.ACCEPTED:
            raise ContextPackageBlockedError("StructuredChange is not currently accepted")
        active = self._profile_repository.get_active(
            project_id=request.project_id,
            binding_key=request.embedding_profile_binding_key,
        )
        if active is None or active.profile_version_id != request.embedding_profile_version_id:
            raise ContextPackageBlockedError("Embedding Profile is not the active binding")
        self._profiles.validate_profile(active.profile)
        if active.profile.get("profile_type") != "EmbeddingProfile":
            raise ContextPackageBlockedError("Active binding is not an EmbeddingProfile")
        build = self._indexes.get_build(readiness.search_index_build_id)
        if (
            build is None
            or build.status is not SearchIndexBuildStatus.READY
            or not build.is_current
        ):
            raise ContextPackageBlockedError("Readiness Search Index build is not current")
        expected_build_scope = (
            request.project_id,
            request.target_snapshot_id,
            request.embedding_profile_version_id,
        )
        actual_build_scope = (
            build.spec.project_id,
            build.spec.snapshot_id,
            build.spec.profile_version_id,
        )
        if actual_build_scope != expected_build_scope:
            raise ContextPackageBlockedError("Readiness Search Index build is outside scope")
        return readiness, change, active.profile

    def _load_change(self, request: ContextPackageRequest) -> dict[str, Any]:
        change = self._canonical.get_change_artifact(request.change_id)
        if change is None:
            raise ContextPackageBlockedError("StructuredChange does not exist")
        expected = (request.project_id, request.target_snapshot_id)
        actual = (change.get("project_id"), change.get("target_snapshot_id"))
        if actual != expected:
            raise ContextPackageBlockedError("StructuredChange is outside Context scope")
        return change

    def _lock_scope(
        self,
        request: ContextPackageRequest,
        readiness: DocumentIngestionResultEvent,
        *,
        relation_build: DocumentRelationBuildState | None,
    ) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT project_id FROM analysis_cases WHERE analysis_case_id = %s FOR UPDATE",
                (request.analysis_case_id,),
            )
            cursor.fetchone()
            if relation_build is not None:
                cursor.execute(
                    """
                    SELECT document_relation_build_id
                    FROM document_relation_builds
                    WHERE document_relation_build_id = %s
                    FOR SHARE
                    """,
                    (relation_build.spec.build_id,),
                )
                cursor.fetchone()
            cursor.execute(
                """
                SELECT structured_change_id
                FROM structured_changes
                WHERE structured_change_id = %s
                FOR SHARE
                """,
                (request.change_id,),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT search_index_build_id
                FROM search_index_builds
                WHERE search_index_build_id = %s
                FOR SHARE
                """,
                (readiness.search_index_build_id,),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT active_profile_version_id
                FROM project_profile_bindings
                WHERE project_id = %s AND binding_key = %s
                FOR SHARE
                """,
                (request.project_id, request.embedding_profile_binding_key),
            )
            cursor.fetchone()

    def _build_artifact(
        self,
        *,
        request: ContextPackageRequest,
        readiness: DocumentIngestionResultEvent,
        change: dict[str, Any],
        embedding_profile: dict[str, Any],
        plan: RagQueryPlan,
        search_results: tuple[HybridSearchResult, ...],
        ranking_policy_version: str,
        relation_build: DocumentRelationBuildState | None,
    ) -> dict[str, Any]:
        reasons: dict[str, str] = {}
        direct = self._nodes.find_by_business_key(
            project_id=request.project_id,
            snapshot_id=request.target_snapshot_id,
            business_key=str(change["stable_key"]),
        )
        if change["change_type"] != "deleted" and len(direct) != 1:
            raise ContextPackageBlockedError(
                "Non-deleted StructuredChange must resolve to one exact target Slice"
            )
        for record in direct:
            reasons[record.node.node_id] = "direct_change"
        unknowns = {str(value) for value in cast(list[object], change.get("unknowns", []))}
        if relation_build is not None and relation_build.unresolved_count:
            unknowns.add(f"unresolved_document_relations:{relation_build.unresolved_count}")
        for query, result in zip(plan.queries, search_results, strict=True):
            if not result.candidates:
                unknowns.add(f"no_candidates:{query.purpose.value}")
            candidate_reason = (
                "acceptance"
                if query.purpose is RagQueryPurpose.ACCEPTANCE_CRITERIA
                else "rag_candidate"
            )
            for candidate in result.candidates:
                _set_reason(reasons, candidate.target_id, candidate_reason)

        seed_ids = tuple(sorted(reasons))
        seeds = self._nodes.get_nodes_with_documents(
            project_id=request.project_id,
            snapshot_id=request.target_snapshot_id,
            node_ids=seed_ids,
        )
        if {record.node.node_id for record in seeds} != set(seed_ids):
            raise ContextPackageBlockedError("Search candidate cannot be rehydrated in Snapshot")
        records = {record.node.node_id: record for record in seeds}
        for expansion in self._nodes.expand_neighborhood(
            project_id=request.project_id,
            snapshot_id=request.target_snapshot_id,
            seed_node_ids=seed_ids,
            adjacent_distance=request.adjacent_distance,
        ):
            records[expansion.record.node.node_id] = expansion.record
            _set_reason(reasons, expansion.record.node.node_id, expansion.reason.value)

        parent_ids = tuple(
            sorted(
                {
                    record.node.parent_node_id
                    for record in records.values()
                    if record.node.parent_node_id is not None
                }
            )
        )
        parents = self._nodes.get_nodes_with_documents(
            project_id=request.project_id,
            snapshot_id=request.target_snapshot_id,
            node_ids=parent_ids,
        )
        if {record.node.node_id for record in parents} != set(parent_ids):
            raise ContextPackageBlockedError("Canonical parent Section cannot be rehydrated")
        context_items = _context_items(records, reasons, parents)
        document_profile_refs = self._nodes.list_document_profile_refs(
            project_id=request.project_id,
            snapshot_id=request.target_snapshot_id,
        )
        artifact: dict[str, Any] = {
            "artifact_type": "ContextPackage",
            "schema_version": "v1",
            "context_package_id": request.context_package_id,
            "analysis_case_id": request.analysis_case_id,
            "project_id": request.project_id,
            "document_snapshot_id": request.target_snapshot_id,
            "ingestion_batch_id": request.ingestion_batch_id,
            "document_ingestion_result_event_id": readiness.event_id,
            "document_profile_refs": list(document_profile_refs),
            "embedding_profile_ref": (
                f"{embedding_profile['profile_id']}@{embedding_profile['profile_version']}"
            ),
            "search_index_build_id": readiness.search_index_build_id,
            "ranking_policy_version": ranking_policy_version,
            "query_plan_version": plan.planner_version,
            "retrieval_policy": {
                "embedding_profile_version_id": request.embedding_profile_version_id,
                "embedding_profile_binding_key": request.embedding_profile_binding_key,
                "vector_top_k": request.vector_top_k,
                "keyword_top_k": request.keyword_top_k,
                "final_top_k": request.final_top_k,
                "adjacent_distance": request.adjacent_distance,
            },
            "structured_change_refs": [request.change_id],
            "business_summary": str(change["summary"]),
            "context_items": context_items,
            "retrieval_trace": [
                {
                    "query_id": query.query_id,
                    "query_purpose": query.purpose.value,
                    "retrieval_mode": "hybrid",
                    "candidate_refs": [candidate.target_id for candidate in result.candidates],
                }
                for query, result in zip(plan.queries, search_results, strict=True)
            ],
            "token_budget": request.token_budget,
            "estimated_tokens": 0,
            "unknowns": sorted(unknowns),
        }
        if relation_build is not None:
            artifact["document_relation_build_id"] = relation_build.spec.build_id
        artifact["estimated_tokens"] = _stable_estimated_tokens(artifact)
        if int(artifact["estimated_tokens"]) > request.token_budget:
            raise ContextPackageBudgetError(
                "Context Package exceeds token budget; split the change group"
            )
        return artifact

    @staticmethod
    def _validate_existing(request: ContextPackageRequest, artifact: dict[str, Any]) -> None:
        expected = (
            "ContextPackage",
            request.context_package_id,
            request.project_id,
            request.analysis_case_id,
            request.target_snapshot_id,
            request.ingestion_batch_id,
            request.ingestion_result_event_id,
            {
                "embedding_profile_version_id": request.embedding_profile_version_id,
                "embedding_profile_binding_key": request.embedding_profile_binding_key,
                "vector_top_k": request.vector_top_k,
                "keyword_top_k": request.keyword_top_k,
                "final_top_k": request.final_top_k,
                "adjacent_distance": request.adjacent_distance,
            },
            [request.change_id],
            request.token_budget,
        )
        actual = (
            artifact.get("artifact_type"),
            artifact.get("context_package_id"),
            artifact.get("project_id"),
            artifact.get("analysis_case_id"),
            artifact.get("document_snapshot_id"),
            artifact.get("ingestion_batch_id"),
            artifact.get("document_ingestion_result_event_id"),
            artifact.get("retrieval_policy"),
            artifact.get("structured_change_refs"),
            artifact.get("token_budget"),
        )
        if actual != expected:
            raise ContextPackageBlockedError(
                "Context Package ID has different persisted request scope"
            )


_REASON_PRIORITY = {
    "direct_change": 0,
    "acceptance": 1,
    "rag_candidate": 2,
    "cross_document": 3,
    "related": 4,
    "adjacent": 5,
    "parent": 6,
}


def _set_reason(reasons: dict[str, str], node_id: str, reason: str) -> None:
    current = reasons.get(node_id)
    if current is None or _REASON_PRIORITY[reason] < _REASON_PRIORITY[current]:
        reasons[node_id] = reason


def _context_items(
    records: dict[str, DocumentNodeRecord],
    reasons: dict[str, str],
    parents: tuple[DocumentNodeRecord, ...],
) -> list[dict[str, Any]]:
    sections = {record.node.node_id: record for record in parents}
    evidence_by_section: dict[str, list[DocumentNodeRecord]] = {}
    for record in records.values():
        section_id = (
            record.node.node_id
            if record.node.node_type is DocumentNodeType.SECTION
            else record.node.parent_node_id
        )
        if section_id is None:
            raise ContextPackageBlockedError("Context evidence has no parent Section")
        if record.node.node_type is DocumentNodeType.SECTION:
            sections[section_id] = record
        evidence_by_section.setdefault(section_id, []).append(record)

    items: list[dict[str, Any]] = []
    for section_id in sorted(evidence_by_section):
        section = sections.get(section_id)
        if section is None:
            raise ContextPackageBlockedError("Context Section is missing")
        evidence = sorted(
            evidence_by_section[section_id],
            key=lambda record: (record.node.ordinal, record.node.node_id),
        )
        reason = min(
            (reasons[record.node.node_id] for record in evidence),
            key=_REASON_PRIORITY.__getitem__,
        )
        details = " | ".join(f"{record.node.summary}: {record.node.content}" for record in evidence)
        items.append(
            {
                "section_id": section_id,
                "document_id": section.document_id,
                "heading_path": "/".join(section.node.heading_path),
                "compressed_summary": f"{section.node.summary}. {details}",
                "relevance_reason": reason,
                "evidence_refs": [record.node.node_id for record in evidence],
            }
        )
    return items


def _stable_estimated_tokens(artifact: dict[str, Any]) -> int:
    previous = -1
    estimate = 0
    while estimate != previous:
        previous = estimate
        artifact["estimated_tokens"] = estimate
        rendered = json.dumps(
            artifact,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        estimate = math.ceil(sum(1.0 if ord(character) > 127 else 0.25 for character in rendered))
    return estimate
