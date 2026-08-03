"""Formal vector + keyword retrieval with review and index readiness gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operamind.domain import ChangeReviewStatus, SearchCandidate, SearchChannel
from operamind.infrastructure.embeddings import EmbeddingProvider
from operamind.infrastructure.postgres import (
    DocumentNodeRepository,
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


@dataclass(frozen=True, slots=True)
class RequirementDocumentDiscoveryRequest:
    """Requirement-only lookup against one current Canonical document index."""

    project_id: str
    query_text: str
    vector_top_k: int = 10
    keyword_top_k: int = 10
    final_top_k: int = 10
    rrf_k: int = 60

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.query_text.strip():
            raise ValueError("Requirement document discovery scope must not be blank")
        for name, value in (
            ("vector_top_k", self.vector_top_k),
            ("keyword_top_k", self.keyword_top_k),
            ("final_top_k", self.final_top_k),
        ):
            if not 1 <= value <= 100:
                raise ValueError(f"{name} must be between 1 and 100")
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be greater than zero")


@dataclass(frozen=True, slots=True)
class RequirementDocumentCandidate:
    document_id: str
    section_id: str
    heading_path: tuple[str, ...]
    summary: str
    source_refs: tuple[str, ...]
    score: float
    channels: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "section_id": self.section_id,
            "heading_path": list(self.heading_path),
            "summary": self.summary,
            "source_refs": list(self.source_refs),
            "score": self.score,
            "channels": list(self.channels),
        }


@dataclass(frozen=True, slots=True)
class RequirementDocumentDiscoveryResult:
    search_index_build_id: str
    document_snapshot_id: str
    embedding_profile_binding_key: str
    candidates: tuple[RequirementDocumentCandidate, ...]


class RequirementDocumentDiscoveryService:
    """Find design-document candidates before a StructuredChange exists."""

    def __init__(
        self,
        *,
        profiles: ProfileCatalog,
        profile_repository: ProfileRepository,
        index_repository: SearchIndexRepository,
        node_repository: DocumentNodeRepository,
    ) -> None:
        self._profiles = profiles
        self._profile_repository = profile_repository
        self._index_repository = index_repository
        self._nodes = node_repository

    def run(
        self,
        request: RequirementDocumentDiscoveryRequest,
        *,
        provider: EmbeddingProvider,
    ) -> RequirementDocumentDiscoveryResult:
        bindings = self._profile_repository.list_active_by_type(
            project_id=request.project_id,
            profile_type="EmbeddingProfile",
        )
        if len(bindings) != 1:
            raise HybridSearchBlockedError(
                "Requirement discovery requires exactly one active EmbeddingProfile "
                f"(found {len(bindings)})"
            )
        binding = bindings[0]
        self._profiles.validate_profile(binding.profile)
        builds = self._index_repository.find_current_builds(
            project_id=request.project_id,
            profile_version_id=binding.profile_version_id,
        )
        if len(builds) != 1:
            raise HybridSearchBlockedError(
                "Requirement discovery requires exactly one current ready Search Index "
                f"(found {len(builds)})"
            )
        build = builds[0]
        HybridSearchService._validate_profile_build(binding.profile, build.spec)
        query_batch = provider.embed((request.query_text,))
        if query_batch.model != build.spec.model or len(query_batch.vectors) != 1:
            raise HybridSearchBlockedError("Requirement embedding does not match Search Index")
        query_vector = query_batch.vectors[0]
        if len(query_vector) != build.spec.dimensions:
            raise HybridSearchBlockedError(
                "Requirement embedding dimensions do not match Search Index"
            )
        candidate_pool_size = min(
            100,
            max(
                request.vector_top_k,
                request.keyword_top_k,
                request.final_top_k * 10,
            ),
        )
        candidates = _reciprocal_rank_fusion(
            vector_hits=self._index_repository.vector_search(
                state=build,
                query_vector=query_vector,
                top_k=candidate_pool_size,
            ),
            keyword_hits=self._index_repository.keyword_search(
                state=build,
                query_text=request.query_text,
                top_k=candidate_pool_size,
            ),
            source_query_id="requirement-document-discovery",
            rrf_k=request.rrf_k,
            final_top_k=candidate_pool_size,
        )
        records = self._nodes.get_nodes_with_documents(
            project_id=request.project_id,
            snapshot_id=build.spec.snapshot_id,
            node_ids=tuple(candidate.target_id for candidate in candidates),
        )
        by_node_id = {record.node.node_id: record for record in records}
        if set(by_node_id) != {candidate.target_id for candidate in candidates}:
            raise HybridSearchBlockedError(
                "Requirement discovery candidate cannot be rehydrated in Canonical Snapshot"
            )
        ranked = sorted(
            (
                (
                    candidate,
                    by_node_id[candidate.target_id],
                    _requirement_document_relevance(
                        query_text=request.query_text,
                        heading_path=by_node_id[candidate.target_id].node.heading_path,
                        summary=(
                            by_node_id[candidate.target_id].node.summary
                            + "\n"
                            + by_node_id[candidate.target_id].node.content
                        ),
                        retrieval_score=candidate.score,
                    ),
                )
                for candidate in candidates
            ),
            key=lambda item: item[2],
            reverse=True,
        )
        selected = _select_requirement_documents(
            ranked,
            final_top_k=request.final_top_k,
        )
        resolved = tuple(
            RequirementDocumentCandidate(
                document_id=record.document_id,
                section_id=candidate.target_id,
                heading_path=record.node.heading_path,
                summary=record.node.summary,
                source_refs=record.node.source_refs,
                score=candidate.score,
                channels=tuple(channel.value for channel in candidate.channels),
            )
            for candidate, record in selected
        )
        if not resolved:
            raise HybridSearchBlockedError(
                "Requirement discovery returned no Canonical document candidates"
            )
        return RequirementDocumentDiscoveryResult(
            search_index_build_id=build.spec.build_id,
            document_snapshot_id=build.spec.snapshot_id,
            embedding_profile_binding_key=binding.binding_key,
            candidates=resolved,
        )


def _requirement_document_relevance(
    *,
    query_text: str,
    heading_path: tuple[str, ...],
    retrieval_score: float,
    summary: str = "",
) -> tuple[int, float, int, float, int, int, float]:
    """Prefer one best fragment per document whose business name matches the request."""

    query = _search_text(query_text)
    subject = _document_subject(heading_path)
    contained = int(bool(subject) and subject in query)
    subject_bigrams = _bigrams(subject)
    overlap = (
        len(subject_bigrams & _bigrams(query)) / len(subject_bigrams)
        if subject_bigrams
        else 0.0
    )
    fragment = _search_text("".join((*heading_path[1:], summary)))
    fragment_bigrams = _bigrams(fragment)
    matching_fragment_bigrams = fragment_bigrams & _bigrams(query)
    operation_matches = len(
        _operation_signals(query_text) & _operation_signals(fragment)
    )
    fragment_overlap = (
        len(matching_fragment_bigrams) / len(fragment_bigrams)
        if fragment_bigrams
        else 0.0
    )
    return (
        contained,
        overlap,
        operation_matches,
        fragment_overlap,
        len(matching_fragment_bigrams),
        len(subject),
        retrieval_score,
    )


def _select_requirement_documents(
    ranked: list[
        tuple[
            SearchCandidate,
            Any,
            tuple[int, float, int, float, int, int, float],
        ]
    ],
    *,
    final_top_k: int,
) -> list[tuple[SearchCandidate, Any]]:
    """Keep the best fragment per document without discarding cross-document evidence."""

    selected: list[tuple[SearchCandidate, Any]] = []
    selected_document_ids: set[str] = set()
    for candidate, record, _relevance in ranked:
        if record.document_id in selected_document_ids:
            continue
        selected.append((candidate, record))
        selected_document_ids.add(record.document_id)
        if len(selected) >= final_top_k:
            break
    return selected


_OPERATION_ALIASES = {
    "search": ("検索", "search", "filter", "絞り込"),
    "create": ("新規作成", "create", "insert", "登録"),
    "update": ("編集", "edit", "update", "更新"),
    "delete": ("削除", "delete", "remove"),
    "submit": ("申請する", "提出", "submit"),
    "approve": ("承認する", "approve"),
    "reject": ("差戻す", "却下", "reject", "return"),
}


def _operation_signals(value: str) -> set[str]:
    normalized = _search_text(value)
    return {
        operation
        for operation, aliases in _OPERATION_ALIASES.items()
        if any(_search_text(alias) in normalized for alias in aliases)
    }


def _document_subject(heading_path: tuple[str, ...]) -> str:
    if not heading_path:
        return ""
    stem = heading_path[0].rsplit(".", 1)[0]
    parts = [part for part in stem.split("_") if part]
    if parts and parts[0].isdigit():
        parts.pop(0)
    if parts and parts[0] in {
        "画面設計書",
        "プログラム設計書",
        "API詳細設計書",
        "DB設計書",
    }:
        parts.pop(0)
    return _search_text("".join(parts))


def _search_text(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


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
