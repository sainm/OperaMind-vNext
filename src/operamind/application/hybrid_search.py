"""Formal vector + keyword retrieval with review and index readiness gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operamind.domain import ChangeReviewStatus, SearchCandidate, SearchChannel
from operamind.infrastructure.embeddings import EmbeddingProvider
from operamind.infrastructure.postgres import (
    ProfileRepository,
    RankedSearchHit,
    SearchIndexBuildSpec,
    SearchIndexRepository,
    StructuredChangeReviewRepository,
)
from operamind.profiles import ProfileCatalog


class HybridSearchBlockedError(ValueError):
    """Raised when formal retrieval scope or readiness is incomplete."""


@dataclass(frozen=True, slots=True)
class HybridSearchRequest:
    """One query bound to an accepted Change and exact RAG scope."""

    project_id: str
    target_snapshot_id: str
    change_id: str
    embedding_profile_version_id: str
    profile_binding_key: str
    source_query_id: str
    query_text: str
    vector_top_k: int = 10
    keyword_top_k: int = 10
    final_top_k: int = 10
    rrf_k: int = 60

    def __post_init__(self) -> None:
        required = (
            self.project_id,
            self.target_snapshot_id,
            self.change_id,
            self.embedding_profile_version_id,
            self.profile_binding_key,
            self.source_query_id,
            self.query_text,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Hybrid Search request fields must not be blank")
        for name, value in (
            ("vector_top_k", self.vector_top_k),
            ("keyword_top_k", self.keyword_top_k),
            ("final_top_k", self.final_top_k),
        ):
            if not 1 <= value <= 1_000:
                raise ValueError(f"{name} must be between 1 and 1000")
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be greater than zero")


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """Content-free candidates produced by one current index build."""

    search_index_build_id: str
    ranking_policy_version: str
    candidates: tuple[SearchCandidate, ...]


class HybridSearchService:
    """Enforce review/Profile/Snapshot isolation and apply versioned RRF."""

    def __init__(
        self,
        *,
        profiles: ProfileCatalog,
        profile_repository: ProfileRepository,
        review_repository: StructuredChangeReviewRepository,
        index_repository: SearchIndexRepository,
    ) -> None:
        self._profiles = profiles
        self._profile_repository = profile_repository
        self._review_repository = review_repository
        self._index_repository = index_repository

    def run(
        self,
        request: HybridSearchRequest,
        *,
        provider: EmbeddingProvider,
    ) -> HybridSearchResult:
        """Return IDs only; any missing readiness gate blocks without fallback."""

        review = self._review_repository.get_state(
            project_id=request.project_id,
            change_id=request.change_id,
        )
        if review is None:
            raise HybridSearchBlockedError("StructuredChange does not exist in the project")
        if review.target_snapshot_id != request.target_snapshot_id:
            raise HybridSearchBlockedError("StructuredChange target Snapshot does not match query")
        if review.status is not ChangeReviewStatus.ACCEPTED:
            raise HybridSearchBlockedError("StructuredChange must be accepted before formal RAG")

        active = self._profile_repository.get_active(
            project_id=request.project_id,
            binding_key=request.profile_binding_key,
        )
        if active is None or active.profile_version_id != request.embedding_profile_version_id:
            raise HybridSearchBlockedError("EmbeddingProfile is not the active project binding")
        self._profiles.validate_profile(active.profile)
        if active.profile.get("profile_type") != "EmbeddingProfile":
            raise HybridSearchBlockedError("Active binding is not an EmbeddingProfile")
        build = self._index_repository.get_current_build(
            project_id=request.project_id,
            snapshot_id=request.target_snapshot_id,
            profile_version_id=request.embedding_profile_version_id,
        )
        if build is None:
            raise HybridSearchBlockedError("No current ready Search Index exists for the scope")
        self._validate_profile_build(active.profile, build.spec)

        query_batch = provider.embed((request.query_text,))
        if query_batch.model != build.spec.model or len(query_batch.vectors) != 1:
            raise HybridSearchBlockedError("Query embedding model/count does not match the index")
        query_vector = query_batch.vectors[0]
        if len(query_vector) != build.spec.dimensions:
            raise HybridSearchBlockedError("Query embedding dimensions do not match the index")
        vector_hits = self._index_repository.vector_search(
            state=build,
            query_vector=query_vector,
            top_k=request.vector_top_k,
        )
        keyword_hits = self._index_repository.keyword_search(
            state=build,
            query_text=request.query_text,
            top_k=request.keyword_top_k,
        )
        candidates = _reciprocal_rank_fusion(
            vector_hits=vector_hits,
            keyword_hits=keyword_hits,
            source_query_id=request.source_query_id,
            rrf_k=request.rrf_k,
            final_top_k=request.final_top_k,
        )
        return HybridSearchResult(
            search_index_build_id=build.spec.build_id,
            ranking_policy_version=build.spec.ranking_policy_version,
            candidates=candidates,
        )

    @staticmethod
    def _validate_profile_build(profile: dict[str, Any], spec: SearchIndexBuildSpec) -> None:
        expected = (
            int(profile["expected_dimensions"]),
            str(profile["preprocessing_version"]),
            str(profile["ranking_policy_version"]),
        )
        actual = (spec.dimensions, spec.preprocessing_version, spec.ranking_policy_version)
        if actual != expected:
            raise HybridSearchBlockedError("Active EmbeddingProfile drifted from the index build")
        if spec.ranking_policy_version != "hybrid-rrf-v1":
            raise HybridSearchBlockedError("Unsupported ranking policy version")


def _reciprocal_rank_fusion(
    *,
    vector_hits: tuple[RankedSearchHit, ...],
    keyword_hits: tuple[RankedSearchHit, ...],
    source_query_id: str,
    rrf_k: int,
    final_top_k: int,
) -> tuple[SearchCandidate, ...]:
    channel_hits = {
        SearchChannel.VECTOR: vector_hits,
        SearchChannel.KEYWORD: keyword_hits,
    }
    scores: dict[str, float] = {}
    channels: dict[str, set[SearchChannel]] = {}
    for channel, hits in channel_hits.items():
        for hit in hits:
            scores[hit.target_node_id] = scores.get(hit.target_node_id, 0.0) + 1.0 / (
                rrf_k + hit.rank
            )
            channels.setdefault(hit.target_node_id, set()).add(channel)
    if not scores:
        return ()
    maximum = max(scores.values())
    ordered_ids = sorted(scores, key=lambda target_id: (-scores[target_id], target_id))
    return tuple(
        SearchCandidate(
            target_type="slice",
            target_id=target_id,
            score=scores[target_id] / maximum,
            channels=tuple(
                channel
                for channel in (SearchChannel.VECTOR, SearchChannel.KEYWORD)
                if channel in channels[target_id]
            ),
            source_query_id=source_query_id,
        )
        for target_id in ordered_ids[:final_top_k]
    )
