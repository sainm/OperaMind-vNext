"""Transactional persistence for one validated before/after document Diff."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.application.document_diff import (
    DocumentDiffRequest,
    DocumentDiffResult,
    DocumentDiffService,
)
from operamind.contracts import ContractCatalog
from operamind.domain import CanonicalDocumentNodeBuilder, DocumentNode
from operamind.domain.document_conventions import DocumentConvention
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    CanonicalRepository,
    DocumentIngestionResultRepository,
    DocumentIngestionStatus,
    DocumentNodeRepository,
    DocumentSnapshotWrite,
    ProfileRepository,
    SnapshotStatus,
    initial_ingestion_event_id,
)
from operamind.profiles import ProfileCatalog


@dataclass(frozen=True, slots=True)
class PersistedDocumentDiffRequest:
    """Persistence identities and provenance for one logical document update."""

    diff: DocumentDiffRequest
    ingestion_batch_id: str
    analysis_case_id: str
    document_id: str
    logical_name: str
    source_document_version_id: str
    target_document_version_id: str
    source_ref: str
    target_ref: str
    profile_version_id: str
    profile_binding_key: str
    profile_activation_event_id: str
    activated_by: str
    activation_reason: str
    embedding_profile_ref: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.ingestion_batch_id,
            self.analysis_case_id,
            self.document_id,
            self.logical_name,
            self.source_document_version_id,
            self.target_document_version_id,
            self.source_ref,
            self.target_ref,
            self.profile_version_id,
            self.profile_binding_key,
            self.profile_activation_event_id,
            self.activated_by,
            self.activation_reason,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Persisted document Diff fields must not be blank")
        if self.source_document_version_id == self.target_document_version_id:
            raise ValueError("Source and target document version IDs must differ")
        if self.embedding_profile_ref is not None and not self.embedding_profile_ref.strip():
            raise ValueError("embedding_profile_ref must not be blank when supplied")


@dataclass(frozen=True, slots=True)
class PersistedDocumentDiffResult:
    """Persisted Diff result and its Contract-validated ingestion Artifact."""

    diff: DocumentDiffResult
    source_nodes: tuple[DocumentNode, ...]
    target_nodes: tuple[DocumentNode, ...]
    ingestion_artifact: dict[str, Any]
    profile_digest: str
    artifact_digests: tuple[tuple[str, str], ...]
    initial_ingestion_event_id: str


class PersistedDocumentDiffService:
    """Persist a Profile, snapshots, changes, and Artifacts as one transaction."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        document_diff: DocumentDiffService,
        contracts: ContractCatalog,
        profiles: ProfileCatalog,
    ) -> None:
        self._connection = connection
        self._document_diff = document_diff
        self._contracts = contracts
        self._profiles = profiles
        self._profile_repository = ProfileRepository(connection, profiles)
        self._canonical_repository = CanonicalRepository(connection, contracts)
        self._artifact_repository = ArtifactRepository(connection, contracts)
        self._ingestion_result_repository = DocumentIngestionResultRepository(connection, contracts)
        self._node_repository = DocumentNodeRepository(connection)
        self._node_builder = CanonicalDocumentNodeBuilder()

    def run(
        self,
        request: PersistedDocumentDiffRequest,
        profile: dict[str, Any],
    ) -> PersistedDocumentDiffResult:
        """Execute the Diff first, then atomically store every resulting record."""

        self._validate_scope(request)
        self._profiles.validate_profile(profile)
        convention = DocumentConvention.from_validated_profile(profile)
        diff = self._document_diff.run(request.diff, convention)
        source_nodes = self._node_builder.build(
            snapshot=diff.source_snapshot,
            document_version_id=request.source_document_version_id,
            logical_name=request.logical_name,
            document_type=convention.document_type,
        )
        target_nodes = self._node_builder.build(
            snapshot=diff.target_snapshot,
            document_version_id=request.target_document_version_id,
            logical_name=request.logical_name,
            document_type=convention.document_type,
        )
        ingestion_event_id = initial_ingestion_event_id(
            project_id=request.diff.project_id,
            ingestion_batch_id=request.ingestion_batch_id,
        )
        ingestion_artifact = self._build_ingestion_artifact(
            request,
            profile,
            diff,
            ingestion_result_event_id=ingestion_event_id,
            eligible_index_target_count=sum(node.index_eligible for node in target_nodes),
        )
        self._contracts.validate_artifact(ingestion_artifact)

        artifact_digests: list[tuple[str, str]] = []
        with self._connection.transaction():
            profile_digest = self._profile_repository.store_version(
                profile_version_id=request.profile_version_id,
                profile=profile,
            )
            self._profile_repository.activate(
                activation_event_id=request.profile_activation_event_id,
                project_id=request.diff.project_id,
                binding_key=request.profile_binding_key,
                profile_version_id=request.profile_version_id,
                activated_by=request.activated_by,
                reason=request.activation_reason,
            )
            active = self._profile_repository.get_active(
                project_id=request.diff.project_id,
                binding_key=request.profile_binding_key,
            )
            if active is None or active.profile_version_id != request.profile_version_id:
                raise ValueError("Requested Profile version is not the active project binding")

            self._canonical_repository.store_snapshot(
                DocumentSnapshotWrite(
                    project_id=request.diff.project_id,
                    document_id=request.document_id,
                    document_version_id=request.source_document_version_id,
                    logical_name=request.logical_name,
                    source_ref=request.source_ref,
                    content_digest=diff.source_content_digest,
                    extractor_ref=diff.source_extractor_ref,
                    profile_version_id=request.profile_version_id,
                    selected_variant_id=diff.source_variant_id,
                    status=SnapshotStatus.COMMITTED,
                    snapshot=diff.source_snapshot,
                    selected_variant_ids=diff.source_snapshot_variant_ids,
                    fact_variant_ids=diff.source_fact_variant_ids,
                )
            )
            self._canonical_repository.store_snapshot(
                DocumentSnapshotWrite(
                    project_id=request.diff.project_id,
                    document_id=request.document_id,
                    document_version_id=request.target_document_version_id,
                    logical_name=request.logical_name,
                    source_ref=request.target_ref,
                    content_digest=diff.target_content_digest,
                    extractor_ref=diff.target_extractor_ref,
                    profile_version_id=request.profile_version_id,
                    selected_variant_id=diff.target_variant_id,
                    status=SnapshotStatus.COMMITTED,
                    snapshot=diff.target_snapshot,
                    selected_variant_ids=diff.target_snapshot_variant_ids,
                    fact_variant_ids=diff.target_fact_variant_ids,
                )
            )
            self._node_repository.store_nodes(
                project_id=request.diff.project_id,
                snapshot_id=request.diff.source_snapshot_id,
                nodes=source_nodes,
            )
            self._node_repository.store_nodes(
                project_id=request.diff.project_id,
                snapshot_id=request.diff.target_snapshot_id,
                nodes=target_nodes,
            )
            self._canonical_repository.store_changes(diff.changes)
            for change in diff.changes:
                digest = self._artifact_repository.store(
                    artifact_id=change.change_id,
                    project_id=request.diff.project_id,
                    analysis_case_id=request.analysis_case_id,
                    artifact=change.to_artifact(),
                )
                artifact_digests.append((change.change_id, digest))
            ingestion_digest = self._artifact_repository.store(
                artifact_id=request.ingestion_batch_id,
                project_id=request.diff.project_id,
                analysis_case_id=request.analysis_case_id,
                artifact=ingestion_artifact,
            )
            artifact_digests.append((request.ingestion_batch_id, ingestion_digest))
            self._ingestion_result_repository.append(
                event_id=ingestion_event_id,
                project_id=request.diff.project_id,
                ingestion_batch_id=request.ingestion_batch_id,
                analysis_case_id=request.analysis_case_id,
                expected_previous_event_id=None,
                artifact_id=request.ingestion_batch_id,
                search_index_build_id=None,
                status=DocumentIngestionStatus(str(ingestion_artifact["status"])),
            )

        return PersistedDocumentDiffResult(
            diff=diff,
            source_nodes=source_nodes,
            target_nodes=target_nodes,
            ingestion_artifact=ingestion_artifact,
            profile_digest=profile_digest,
            artifact_digests=tuple(artifact_digests),
            initial_ingestion_event_id=ingestion_event_id,
        )

    @staticmethod
    def _validate_scope(request: PersistedDocumentDiffRequest) -> None:
        if not request.diff.before_path.is_file():
            raise ValueError(f"Source document does not exist: {request.diff.before_path}")
        if not request.diff.after_path.is_file():
            raise ValueError(f"Target document does not exist: {request.diff.after_path}")

    @staticmethod
    def _build_ingestion_artifact(
        request: PersistedDocumentDiffRequest,
        profile: dict[str, Any],
        diff: DocumentDiffResult,
        *,
        ingestion_result_event_id: str,
        eligible_index_target_count: int,
    ) -> dict[str, Any]:
        profile_ref = f"{profile['profile_id']}@{profile['profile_version']}"
        blocking_reasons = ["embedding_index_not_started"]
        if diff.changes:
            blocking_reasons.insert(0, "structured_changes_require_review")
        artifact: dict[str, Any] = {
            "artifact_type": "DocumentIngestionResult",
            "schema_version": "v1",
            "ingestion_result_event_id": ingestion_result_event_id,
            "ingestion_batch_id": request.ingestion_batch_id,
            "project_id": request.diff.project_id,
            "source_snapshot_id": request.diff.source_snapshot_id,
            "target_snapshot_id": request.diff.target_snapshot_id,
            "analysis_case_id": request.analysis_case_id,
            "document_profile_refs": [profile_ref],
            "document_profiles": [
                {
                    "profile_version_id": request.profile_version_id,
                    "binding_key": request.profile_binding_key,
                    "activation_event_id": request.profile_activation_event_id,
                    "profile_ref": profile_ref,
                }
            ],
            "source_content_digest": diff.source_content_digest,
            "target_content_digest": diff.target_content_digest,
            "source_extractor_ref": diff.source_extractor_ref,
            "target_extractor_ref": diff.target_extractor_ref,
            "source_variant_ids": list(diff.source_snapshot_variant_ids),
            "target_variant_ids": list(diff.target_snapshot_variant_ids),
            "source_fact_variant_ids": dict(diff.source_fact_variant_ids),
            "target_fact_variant_ids": dict(diff.target_fact_variant_ids),
            "source_ignored_sections": list(diff.source_ignored_sections),
            "target_ignored_sections": list(diff.target_ignored_sections),
            # A before/after pair represents one logical document update.
            "uploaded_document_count": 1,
            "changed_document_count": 1 if diff.changes else 0,
            "structured_change_count": len(diff.changes),
            "eligible_index_target_count": eligible_index_target_count,
            "indexed_target_count": 0,
            "embedding_index_status": "not_started",
            "status": "needs_review",
            "blocking_reasons": blocking_reasons,
        }
        if request.embedding_profile_ref is not None:
            artifact["embedding_profile_ref"] = request.embedding_profile_ref
        return artifact
