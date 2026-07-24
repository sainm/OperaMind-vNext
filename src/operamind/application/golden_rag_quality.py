"""Execute frozen Golden queries against one exact current Search Index scope."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, cast

from psycopg import Connection

from operamind.application.hybrid_search import _reciprocal_rank_fusion
from operamind.contracts import ContractCatalog
from operamind.domain import RagQueryPurpose
from operamind.golden import RagQualityEvaluation, RagQualityEvaluator
from operamind.infrastructure.embeddings import EmbeddingProvider, EmbeddingProviderError
from operamind.infrastructure.postgres import (
    GOLDEN_SEMANTIC_BINDING_VERSION,
    ArtifactRepository,
    GoldenRagQualityRepository,
    GoldenRagQualityState,
    GoldenSemanticBinding,
    GoldenSemanticBindingRepository,
    ProfileRepository,
    SearchIndexBuildState,
    SearchIndexBuildStatus,
    SearchIndexRepository,
)
from operamind.profiles import ProfileCatalog


class GoldenRagQualityBlockedError(ValueError):
    """Raised when a Golden run cannot bind its exact Canonical scope."""


@dataclass(frozen=True, slots=True)
class GoldenRagQualityRequest:
    report_id: str
    case_id: str
    dataset_id: str
    dataset_version: str
    project_id: str
    document_snapshot_id: str
    embedding_profile_version_id: str
    embedding_profile_binding_key: str
    search_index_build_id: str
    expected: dict[str, Any]
    query_plan_version: str
    query_texts: tuple[str, str, str]
    created_by: str
    vector_top_k: int = 10
    keyword_top_k: int = 10
    final_top_k: int = 10
    rrf_k: int = 60

    def __post_init__(self) -> None:
        required = (
            self.report_id,
            self.case_id,
            self.dataset_id,
            self.dataset_version,
            self.project_id,
            self.document_snapshot_id,
            self.embedding_profile_version_id,
            self.embedding_profile_binding_key,
            self.search_index_build_id,
            self.query_plan_version,
            self.created_by,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Golden RAG quality request fields must not be blank")
        for name, value in (
            ("vector_top_k", self.vector_top_k),
            ("keyword_top_k", self.keyword_top_k),
            ("final_top_k", self.final_top_k),
        ):
            if not 10 <= value <= 1_000:
                raise ValueError(f"{name} must be between 10 and 1000 for Recall@10")
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be greater than zero")
        if len(self.query_texts) != len(tuple(RagQueryPurpose)) or any(
            not text.strip() for text in self.query_texts
        ):
            raise ValueError("Golden RAG request requires three non-blank query texts")


@dataclass(frozen=True, slots=True)
class GoldenRagQualityResult:
    created: bool
    artifact: dict[str, Any]
    state: GoldenRagQualityState


class GoldenRagQualityService:
    """Run real hybrid retrieval and persist the latest formal quality decision."""

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
        self._profile_repository = ProfileRepository(connection, profiles)
        self._indexes = SearchIndexRepository(connection)
        self._reports = GoldenRagQualityRepository(connection, contracts)
        self._bindings = GoldenSemanticBindingRepository(connection)
        self._evaluator = RagQualityEvaluator()

    def run(
        self,
        request: GoldenRagQualityRequest,
        *,
        provider: EmbeddingProvider,
    ) -> GoldenRagQualityResult:
        expected_digest = _validate_expected(request)
        existing = self._artifacts.get(request.report_id)
        if existing is not None:
            _validate_existing(request, expected_digest, existing)
            state = self._reports.get(request.report_id)
            if state is None:
                raise GoldenRagQualityBlockedError(
                    "Golden RAG report Artifact has no normalized quality state"
                )
            return GoldenRagQualityResult(False, existing, state)

        build, profile = self._validate_scope(request)
        bindings: tuple[GoldenSemanticBinding, ...] = ()
        try:
            bindings = self._bindings.resolve(
                project_id=request.project_id,
                snapshot_id=request.document_snapshot_id,
                search_index_build_id=request.search_index_build_id,
                expected=request.expected,
            )
            search_results = self._execute_queries(
                request=request,
                build=build,
                provider=provider,
                bindings=bindings,
            )
            artifact = self._evaluated_artifact(
                request=request,
                expected_digest=expected_digest,
                build=build,
                search_results=search_results,
                bindings=bindings,
            )
        except (EmbeddingProviderError, ValueError) as error:
            artifact = self._blocked_artifact(
                request=request,
                expected_digest=expected_digest,
                build=build,
                bindings=bindings,
                reason=f"retrieval_execution_failed:{type(error).__name__}:{error}",
            )
        self._contracts.validate_artifact(artifact)

        with self._connection.transaction():
            self._lock_scope(request)
            locked_build, locked_profile = self._validate_scope(request)
            if locked_build != build or locked_profile != profile:
                raise GoldenRagQualityBlockedError(
                    "Golden RAG Snapshot/Profile/Index scope changed during retrieval"
                )
            if (
                bindings
                and self._bindings.resolve(
                    project_id=request.project_id,
                    snapshot_id=request.document_snapshot_id,
                    search_index_build_id=request.search_index_build_id,
                    expected=request.expected,
                )
                != bindings
            ):
                raise GoldenRagQualityBlockedError(
                    "Golden semantic bindings changed during retrieval"
                )
            created, state = self._reports.publish(
                artifact=artifact,
                created_by=request.created_by,
            )
        stored = self._artifacts.get(request.report_id)
        if stored is None:
            raise RuntimeError("Golden RAG report was not persisted")
        return GoldenRagQualityResult(created, stored, state)

    def _validate_scope(
        self, request: GoldenRagQualityRequest
    ) -> tuple[SearchIndexBuildState, dict[str, Any]]:
        build = self._indexes.get_build(request.search_index_build_id)
        if build is None:
            raise GoldenRagQualityBlockedError("Golden Search Index build does not exist")
        expected_scope = (
            request.project_id,
            request.document_snapshot_id,
            request.embedding_profile_version_id,
        )
        actual_scope = (
            build.spec.project_id,
            build.spec.snapshot_id,
            build.spec.profile_version_id,
        )
        if actual_scope != expected_scope:
            raise GoldenRagQualityBlockedError("Golden Search Index is outside fixed scope")
        if build.status is not SearchIndexBuildStatus.READY or not build.is_current:
            raise GoldenRagQualityBlockedError("Golden Search Index is not ready/current")
        if build.indexed_target_count != build.eligible_target_count:
            raise GoldenRagQualityBlockedError("Golden Search Index coverage is incomplete")
        active = self._profile_repository.get_active(
            project_id=request.project_id,
            binding_key=request.embedding_profile_binding_key,
        )
        if active is None or active.profile_version_id != request.embedding_profile_version_id:
            raise GoldenRagQualityBlockedError(
                "Golden Search Index Embedding Profile is not the current binding"
            )
        self._profiles.validate_profile(active.profile)
        if active.profile.get("profile_type") != "EmbeddingProfile":
            raise GoldenRagQualityBlockedError("Golden quality binding is not EmbeddingProfile")
        expected_profile = (
            int(active.profile["expected_dimensions"]),
            str(active.profile["preprocessing_version"]),
            str(active.profile["ranking_policy_version"]),
        )
        actual_profile = (
            build.spec.dimensions,
            build.spec.preprocessing_version,
            build.spec.ranking_policy_version,
        )
        if actual_profile != expected_profile:
            raise GoldenRagQualityBlockedError(
                "Current Embedding Profile differs from Golden Search Index"
            )
        return build, active.profile

    def _execute_queries(
        self,
        *,
        request: GoldenRagQualityRequest,
        build: SearchIndexBuildState,
        provider: EmbeddingProvider,
        bindings: tuple[GoldenSemanticBinding, ...],
    ) -> tuple[dict[str, object], ...]:
        expectations = cast(list[dict[str, object]], request.expected["query_expectations"])
        texts = request.query_texts
        batch = provider.embed(texts)
        refs_by_node = {binding.canonical_node_id: (binding.semantic_ref,) for binding in bindings}
        if batch.model != build.spec.model or len(batch.vectors) != len(texts):
            raise GoldenRagQualityBlockedError(
                "Golden query embedding model/count does not match Search Index"
            )
        results: list[dict[str, object]] = []
        for expectation, query_text, query_vector in zip(
            expectations,
            texts,
            batch.vectors,
            strict=True,
        ):
            if len(query_vector) != build.spec.dimensions:
                raise GoldenRagQualityBlockedError(
                    "Golden query embedding dimensions do not match Search Index"
                )
            purpose = str(expectation["query_purpose"])
            candidates = _reciprocal_rank_fusion(
                vector_hits=self._indexes.vector_search(
                    state=build,
                    query_vector=query_vector,
                    top_k=request.vector_top_k,
                ),
                keyword_hits=self._indexes.keyword_search(
                    state=build,
                    query_text=query_text,
                    top_k=request.keyword_top_k,
                ),
                source_query_id=f"golden:{request.case_id}:{purpose}",
                rrf_k=request.rrf_k,
                final_top_k=request.final_top_k,
            )
            target_projects = self._indexes.resolve_target_projects(
                tuple(candidate.target_id for candidate in candidates)
            )
            results.append(
                {
                    "query_purpose": purpose,
                    "query_text": query_text,
                    "candidates": [
                        {
                            "rank": rank,
                            "target_type": candidate.target_type,
                            "target_id": candidate.target_id,
                            "semantic_refs": list(refs_by_node.get(candidate.target_id, ())),
                            "project_id": target_projects[candidate.target_id],
                            "score": candidate.score,
                            "channels": [channel.value for channel in candidate.channels],
                        }
                        for rank, candidate in enumerate(candidates, start=1)
                    ],
                }
            )
        return tuple(results)

    def _evaluated_artifact(
        self,
        *,
        request: GoldenRagQualityRequest,
        expected_digest: str,
        build: SearchIndexBuildState,
        search_results: tuple[dict[str, object], ...],
        bindings: tuple[GoldenSemanticBinding, ...],
    ) -> dict[str, Any]:
        observed = {
            "case_id": request.case_id,
            "project_id": request.project_id,
            "query_results": [
                {
                    "query_purpose": result["query_purpose"],
                    "candidates": [
                        {
                            "target_id": candidate["target_id"],
                            "semantic_refs": candidate["semantic_refs"],
                            "project_id": candidate["project_id"],
                        }
                        for candidate in cast(list[dict[str, object]], result["candidates"])
                    ],
                }
                for result in search_results
            ],
        }
        evaluation = self._evaluator.evaluate(expected=request.expected, observed=observed)
        return self._artifact_from_evaluation(
            request=request,
            expected_digest=expected_digest,
            build=build,
            search_results=search_results,
            evaluation=evaluation,
            bindings=bindings,
        )

    @staticmethod
    def _artifact_from_evaluation(
        *,
        request: GoldenRagQualityRequest,
        expected_digest: str,
        build: SearchIndexBuildState,
        search_results: tuple[dict[str, object], ...],
        evaluation: RagQualityEvaluation,
        bindings: tuple[GoldenSemanticBinding, ...],
    ) -> dict[str, Any]:
        query_details = {value.purpose.value: value for value in evaluation.queries}
        query_results = []
        for result in search_results:
            purpose = str(result["query_purpose"])
            detail = query_details[purpose]
            query_text = str(result["query_text"])
            query_results.append(
                {
                    **result,
                    "query_text_digest": _digest(query_text),
                    "required_hits_at_5": list(detail.required_hits_at_5),
                    "required_hits_at_10": list(detail.required_hits_at_10),
                    "missing_required_refs": list(detail.missing_required_refs),
                    "irrelevant_hits": list(detail.irrelevant_hits),
                    "cross_project_leaks": list(detail.cross_project_leaks),
                    "failure_reasons": list(detail.failure_reasons),
                }
            )
        failures = [f"quality_threshold_failed:{name}" for name in evaluation.failures]
        return {
            **_report_envelope(request, expected_digest, build),
            "status": "passed" if evaluation.passed else "failed",
            "quality_thresholds": request.expected["quality_thresholds"],
            "semantic_binding_version": GOLDEN_SEMANTIC_BINDING_VERSION,
            "semantic_bindings": [binding.to_artifact() for binding in bindings],
            "query_results": query_results,
            "metrics": asdict(evaluation.metrics),
            "threshold_failures": list(evaluation.failures),
            "failure_reasons": failures,
        }

    @staticmethod
    def _blocked_artifact(
        *,
        request: GoldenRagQualityRequest,
        expected_digest: str,
        build: SearchIndexBuildState,
        bindings: tuple[GoldenSemanticBinding, ...],
        reason: str,
    ) -> dict[str, Any]:
        expectations = cast(list[dict[str, object]], request.expected["query_expectations"])
        return {
            **_report_envelope(request, expected_digest, build),
            "status": "blocked",
            "quality_thresholds": request.expected["quality_thresholds"],
            "semantic_binding_version": GOLDEN_SEMANTIC_BINDING_VERSION,
            "semantic_bindings": [binding.to_artifact() for binding in bindings],
            "query_results": [
                {
                    "query_purpose": expectation["query_purpose"],
                    "query_text": query_text,
                    "query_text_digest": _digest(query_text),
                    "candidates": [],
                    "required_hits_at_5": [],
                    "required_hits_at_10": [],
                    "missing_required_refs": sorted(
                        cast(list[str], expectation["required_candidate_refs"])
                    ),
                    "irrelevant_hits": [],
                    "cross_project_leaks": [],
                    "failure_reasons": [reason],
                }
                for expectation, query_text in zip(
                    expectations,
                    request.query_texts,
                    strict=True,
                )
            ],
            "metrics": None,
            "threshold_failures": [],
            "failure_reasons": [reason],
        }

    def _lock_scope(self, request: GoldenRagQualityRequest) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT document_snapshot_id FROM document_snapshots
                WHERE document_snapshot_id = %s AND project_id = %s FOR SHARE
                """,
                (request.document_snapshot_id, request.project_id),
            )
            if cursor.fetchone() is None:
                raise GoldenRagQualityBlockedError("Golden Document Snapshot does not exist")
            cursor.execute(
                """
                SELECT search_index_build_id FROM search_index_builds
                WHERE search_index_build_id = %s FOR SHARE
                """,
                (request.search_index_build_id,),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT active_profile_version_id FROM project_profile_bindings
                WHERE project_id = %s AND binding_key = %s FOR SHARE
                """,
                (request.project_id, request.embedding_profile_binding_key),
            )
            cursor.fetchone()


def _validate_expected(request: GoldenRagQualityRequest) -> str:
    expected = request.expected
    if (
        expected.get("case_id") != request.case_id
        or expected.get("project_id") != request.project_id
    ):
        raise GoldenRagQualityBlockedError("Golden expectation case/project scope differs")
    if (
        expected.get("dataset_stage") != "golden"
        or expected.get("canonical_id_status") != "frozen"
        or expected.get("review_status") != "approved"
    ):
        raise GoldenRagQualityBlockedError("Golden expectation is not frozen and approved")
    values = expected.get("query_expectations")
    if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
        raise GoldenRagQualityBlockedError("Golden query expectations are invalid")
    purposes = tuple(str(value.get("query_purpose")) for value in values)
    if purposes != tuple(purpose.value for purpose in RagQueryPurpose):
        raise GoldenRagQualityBlockedError(
            "Golden queries must contain three purposes in canonical order"
        )
    return _digest(_json(expected))


def _report_envelope(
    request: GoldenRagQualityRequest,
    expectation_digest: str,
    build: SearchIndexBuildState,
) -> dict[str, object]:
    return {
        "artifact_type": "GoldenRagQualityReport",
        "schema_version": "v1",
        "report_id": request.report_id,
        "case_id": request.case_id,
        "dataset_id": request.dataset_id,
        "dataset_version": request.dataset_version,
        "project_id": request.project_id,
        "document_snapshot_id": request.document_snapshot_id,
        "embedding_profile_version_id": request.embedding_profile_version_id,
        "embedding_profile_binding_key": request.embedding_profile_binding_key,
        "search_index_build_id": request.search_index_build_id,
        "ranking_policy_version": build.spec.ranking_policy_version,
        "query_plan_version": request.query_plan_version,
        "expectation_digest": expectation_digest,
    }


def _validate_existing(
    request: GoldenRagQualityRequest,
    expectation_digest: str,
    artifact: dict[str, Any],
) -> None:
    expected = (
        "GoldenRagQualityReport",
        request.report_id,
        request.case_id,
        request.dataset_id,
        request.dataset_version,
        request.project_id,
        request.document_snapshot_id,
        request.embedding_profile_version_id,
        request.embedding_profile_binding_key,
        request.search_index_build_id,
        request.query_plan_version,
        request.query_texts,
        expectation_digest,
    )
    actual = (
        artifact.get("artifact_type"),
        artifact.get("report_id"),
        artifact.get("case_id"),
        artifact.get("dataset_id"),
        artifact.get("dataset_version"),
        artifact.get("project_id"),
        artifact.get("document_snapshot_id"),
        artifact.get("embedding_profile_version_id"),
        artifact.get("embedding_profile_binding_key"),
        artifact.get("search_index_build_id"),
        artifact.get("query_plan_version"),
        tuple(
            str(result.get("query_text"))
            for result in cast(list[dict[str, object]], artifact.get("query_results", []))
        ),
        artifact.get("expectation_digest"),
    )
    if actual != expected:
        raise GoldenRagQualityBlockedError(
            "Golden RAG report replay differs from persisted scope or expectation"
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = [
    "GoldenRagQualityBlockedError",
    "GoldenRagQualityRequest",
    "GoldenRagQualityResult",
    "GoldenRagQualityService",
]
