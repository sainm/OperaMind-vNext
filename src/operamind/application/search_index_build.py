"""Transactional orchestration for a complete pgvector Search Index build."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.domain import DocumentEmbeddingInput, DocumentEmbeddingInputBuilder
from operamind.infrastructure.embeddings import (
    EmbeddingBatch,
    EmbeddingProvider,
)
from operamind.infrastructure.postgres import (
    DocumentRelationRepository,
    ProfileRepository,
    SearchIndexBuildSpec,
    SearchIndexBuildState,
    SearchIndexBuildStatus,
    SearchIndexEntryWrite,
    SearchIndexFailureKind,
    SearchIndexRepository,
    search_index_failure_event_id,
    vector_cache_id,
)
from operamind.profiles import ProfileCatalog


class SearchIndexBuildBlockedError(ValueError):
    """Raised when Snapshot, Profile, Provider, or coverage gates block readiness."""


@dataclass(frozen=True, slots=True)
class SearchIndexBuildRequest:
    """Audited identity and Profile activation inputs for one build attempt."""

    build_id: str
    project_id: str
    snapshot_id: str
    profile_version_id: str
    profile_binding_key: str
    profile_activation_event_id: str
    activated_by: str
    activation_reason: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.build_id,
                self.project_id,
                self.snapshot_id,
                self.profile_version_id,
                self.profile_binding_key,
                self.profile_activation_event_id,
                self.activated_by,
                self.activation_reason,
            )
        ):
            raise ValueError("Search Index build request fields must not be blank")


@dataclass(frozen=True, slots=True)
class SearchIndexBuildResult:
    """Ready build evidence including Provider work and cache reuse."""

    state: SearchIndexBuildState
    generated_vector_count: int
    profile_digest: str


class SearchIndexBuildService:
    """Probe, activate, incrementally embed, and atomically publish one index."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        profiles: ProfileCatalog,
    ) -> None:
        self._connection = connection
        self._profiles = profiles
        self._profile_repository = ProfileRepository(connection, profiles)
        self._relation_repository = DocumentRelationRepository(connection)
        self._index_repository = SearchIndexRepository(connection)
        self._input_builder = DocumentEmbeddingInputBuilder()

    def run(
        self,
        request: SearchIndexBuildRequest,
        *,
        profile: dict[str, Any],
        provider: EmbeddingProvider,
    ) -> SearchIndexBuildResult:
        """Build exact coverage and publish only after every target is indexed."""

        self._profiles.validate_profile(profile)
        if profile.get("profile_type") != "EmbeddingProfile":
            raise SearchIndexBuildBlockedError("Search Index requires an EmbeddingProfile")
        probe = provider.probe()
        expected_dimensions = int(profile["expected_dimensions"])
        if probe.dimensions != expected_dimensions:
            raise SearchIndexBuildBlockedError(
                "Embedding Provider dimensions do not match the active Profile"
            )
        if not probe.model.strip():
            raise SearchIndexBuildBlockedError("Embedding Provider model must not be blank")
        relation_build = self._relation_repository.get_current_build(
            project_id=request.project_id,
            snapshot_id=request.snapshot_id,
        )
        spec = SearchIndexBuildSpec(
            build_id=request.build_id,
            project_id=request.project_id,
            snapshot_id=request.snapshot_id,
            profile_version_id=request.profile_version_id,
            model=probe.model,
            dimensions=probe.dimensions,
            preprocessing_version=str(profile["preprocessing_version"]),
            ranking_policy_version=str(profile["ranking_policy_version"]),
            relation_build_id=(
                relation_build.spec.build_id if relation_build is not None else None
            ),
        )
        targets = self._index_repository.load_targets(
            project_id=request.project_id,
            snapshot_id=request.snapshot_id,
        )
        inputs = tuple(
            self._input_builder.build(
                node=target.node,
                document_type=target.document_type,
                relation_labels=target.relation_labels,
                preprocessing_version=spec.preprocessing_version,
            )
            for target in targets
        )
        if not inputs:
            raise SearchIndexBuildBlockedError("Snapshot has no eligible Search Index targets")

        with self._connection.transaction():
            profile_digest = self._profile_repository.store_version(
                profile_version_id=request.profile_version_id,
                profile=profile,
            )
            self._profile_repository.activate(
                activation_event_id=request.profile_activation_event_id,
                project_id=request.project_id,
                binding_key=request.profile_binding_key,
                profile_version_id=request.profile_version_id,
                activated_by=request.activated_by,
                reason=request.activation_reason,
            )
            active = self._profile_repository.get_active(
                project_id=request.project_id,
                binding_key=request.profile_binding_key,
            )
            if active is None or active.profile_version_id != request.profile_version_id:
                raise SearchIndexBuildBlockedError(
                    "Requested EmbeddingProfile is not the active project binding"
                )
            start = self._index_repository.start_build(
                spec=spec,
                eligible_target_count=len(inputs),
            )
            state = start.state
            if state.status not in {
                SearchIndexBuildStatus.BUILDING,
                SearchIndexBuildStatus.READY,
            }:
                raise SearchIndexBuildBlockedError(
                    f"Search Index build ID is already {state.status.value}; use a new build ID"
                )
            if state.status is SearchIndexBuildStatus.BUILDING and not start.created:
                raise SearchIndexBuildBlockedError(
                    "Search Index build ID is already building; recover an interrupted build "
                    "or use a new build ID"
                )
        if state.status is SearchIndexBuildStatus.READY:
            return SearchIndexBuildResult(
                state=state,
                generated_vector_count=0,
                profile_digest=profile_digest,
            )
        if state.status is not SearchIndexBuildStatus.BUILDING:
            raise AssertionError("Validated Search Index start returned an unexpected state")

        generated_count = 0
        try:
            cache_by_digest = self._index_repository.find_cached_vectors(
                input_digests=tuple(item.input_digest for item in inputs),
                model=spec.model,
                dimensions=spec.dimensions,
                preprocessing_version=spec.preprocessing_version,
            )
            missing_by_digest: dict[str, DocumentEmbeddingInput] = {}
            for embedding_input in inputs:
                if embedding_input.input_digest not in cache_by_digest:
                    missing_by_digest.setdefault(embedding_input.input_digest, embedding_input)
            batch_size = int(profile["batch_size"])
            missing = tuple(missing_by_digest.values())
            for offset in range(0, len(missing), batch_size):
                input_batch = missing[offset : offset + batch_size]
                provider_batch = provider.embed(tuple(item.text for item in input_batch))
                self._validate_batch(provider_batch, spec=spec, expected_count=len(input_batch))
                for embedding_input, vector in zip(
                    input_batch,
                    provider_batch.vectors,
                    strict=True,
                ):
                    cache_id = vector_cache_id(
                        input_digest=embedding_input.input_digest,
                        model=spec.model,
                        dimensions=spec.dimensions,
                        preprocessing_version=spec.preprocessing_version,
                    )
                    self._index_repository.store_vector(
                        vector_cache_id=cache_id,
                        input_digest=embedding_input.input_digest,
                        model=spec.model,
                        dimensions=spec.dimensions,
                        preprocessing_version=spec.preprocessing_version,
                        vector=vector,
                    )
                    cache_by_digest[embedding_input.input_digest] = cache_id
                    generated_count += 1
            entries = tuple(
                SearchIndexEntryWrite(
                    embedding_input=embedding_input,
                    vector_cache_id=cache_by_digest[embedding_input.input_digest],
                )
                for embedding_input in inputs
            )
        except Exception as error:
            self._index_repository.fail_build(
                failure_event_id=search_index_failure_event_id(spec.build_id),
                build_id=spec.build_id,
                kind=SearchIndexFailureKind.EMBEDDING_GENERATION,
                actor="operamind-build-index@1",
                reason=f"{type(error).__name__}: embedding generation failed",
            )
            raise
        try:
            final = self._index_repository.finalize_build(
                spec=spec,
                entries=entries,
                reused_vector_count=len(inputs) - generated_count,
            )
        except ValueError as error:
            self._index_repository.fail_build(
                failure_event_id=search_index_failure_event_id(spec.build_id),
                build_id=spec.build_id,
                kind=SearchIndexFailureKind.PUBLISH_VALIDATION,
                actor="operamind-build-index@1",
                reason="ValueError: Search Index publish validation failed",
            )
            raise SearchIndexBuildBlockedError(
                "Search Index could not publish because its scope or coverage changed"
            ) from error
        except Exception as error:
            self._index_repository.fail_build(
                failure_event_id=search_index_failure_event_id(spec.build_id),
                build_id=spec.build_id,
                kind=SearchIndexFailureKind.PUBLISH_EXECUTION,
                actor="operamind-build-index@1",
                reason=f"{type(error).__name__}: Search Index publish execution failed",
            )
            raise
        return SearchIndexBuildResult(
            state=final,
            generated_vector_count=generated_count,
            profile_digest=profile_digest,
        )

    @staticmethod
    def _validate_batch(
        batch: EmbeddingBatch,
        *,
        spec: SearchIndexBuildSpec,
        expected_count: int,
    ) -> None:
        if batch.model != spec.model:
            raise SearchIndexBuildBlockedError("Embedding model drifted during the build")
        if len(batch.vectors) != expected_count:
            raise SearchIndexBuildBlockedError("Embedding batch count does not match input")
        if any(len(vector) != spec.dimensions for vector in batch.vectors):
            raise SearchIndexBuildBlockedError("Embedding dimensions drifted during the build")
        if any(not math.isfinite(value) for vector in batch.vectors for value in vector):
            raise SearchIndexBuildBlockedError("Embedding contains non-finite values")
        if any(not any(value != 0.0 for value in vector) for vector in batch.vectors):
            raise SearchIndexBuildBlockedError("Embedding contains an all-zero vector")
