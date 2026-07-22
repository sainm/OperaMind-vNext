import copy
import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from openpyxl import Workbook

from operamind.application import (
    ContextPackageBlockedError,
    ContextPackageBudgetError,
    ContextPackageRequest,
    ContextPackageService,
    DocumentDiffRequest,
    DocumentDiffService,
    DocumentRelationBuildRequest,
    DocumentRelationBuildService,
    HybridSearchBlockedError,
    HybridSearchRequest,
    HybridSearchService,
    PersistedDocumentDiffRequest,
    PersistedDocumentDiffService,
    RagReadinessBlockedError,
    RagReadinessRequest,
    RagReadinessService,
    SearchIndexBuildBlockedError,
    SearchIndexBuildRequest,
    SearchIndexBuildService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.documents import DocumentSignalExtractorRegistry
from operamind.infrastructure.embeddings import (
    EmbeddingBatch,
    EmbeddingProviderError,
    EmbeddingProviderProbe,
)
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    DocumentIngestionResultRepository,
    DocumentIngestionStatus,
    MigrationCatalog,
    MigrationRunner,
    PersistenceConflictError,
    ProfileRepository,
    SearchIndexBuildStatus,
    SearchIndexFailureKind,
    SearchIndexRepository,
    StructuredChangeReviewDecision,
    StructuredChangeReviewRepository,
    search_index_failure_event_id,
)
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


class DeterministicEmbeddingProvider:
    def __init__(self, *, model: str = "deterministic-test-model", dimensions: int = 3) -> None:
        self.model = model
        self.dimensions = dimensions
        self.probe_calls = 0
        self.embedded_texts: list[str] = []
        self.fail_embed = False

    def probe(self) -> EmbeddingProviderProbe:
        self.probe_calls += 1
        return EmbeddingProviderProbe(model=self.model, dimensions=self.dimensions)

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        if self.fail_embed:
            raise EmbeddingProviderError("deterministic provider failure")
        self.embedded_texts.extend(texts)
        vectors = tuple(self._vector(text) for text in texts)
        return EmbeddingBatch(model=self.model, vectors=vectors)

    def _vector(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode()).digest()
        return tuple((digest[index] + 1) / 256 for index in range(self.dimensions))


def load_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def embedding_profile() -> dict[str, Any]:
    profile = load_object(ROOT / "profiles/embedding-profile.example.json")
    profile["expected_dimensions"] = 3
    profile["batch_size"] = 1
    return profile


def write_screen_design(
    path: Path,
    default_value: str,
    *,
    document_title: str | None = None,
) -> None:
    workbook = Workbook()
    workbook.properties.title = document_title
    overview = workbook.active
    assert overview is not None
    overview.title = "画面概要"
    overview.append(["画面ID", "SCREEN_EXPENSE_LIST"])
    items = workbook.create_sheet("画面項目一覧")
    items.append(["項目名", "種別", "初期値", "備考"])
    items.append(
        [
            "expense-search-status",
            "セレクト",
            default_value,
            "ステータスフィルタ",
        ]
    )
    workbook.save(path)
    workbook.close()


def insert_project_case(
    connection: psycopg.Connection[Any],
    *,
    project_id: str,
    case_id: str,
    suffix: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, %s)",
            (project_id, "Search Index integration test"),
        )
        cursor.execute(
            """
            INSERT INTO repositories (repository_id, project_id, remote_url)
            VALUES (%s, %s, %s)
            """,
            (f"repository-{suffix}", project_id, f"https://example.invalid/{suffix}.git"),
        )
        cursor.execute(
            """
            INSERT INTO repository_revisions (
                repository_revision_id, repository_id, commit_sha
            ) VALUES (%s, %s, %s)
            """,
            (f"revision-{suffix}", f"repository-{suffix}", suffix),
        )
        cursor.execute(
            """
            INSERT INTO analysis_cases (
                analysis_case_id, project_id, repository_revision_id, status
            ) VALUES (%s, %s, %s, 'ingesting')
            """,
            (case_id, project_id, f"revision-{suffix}"),
        )


def persist_snapshots(
    connection: psycopg.Connection[Any],
    *,
    suffix: str,
    project_id: str,
    case_id: str,
    before_path: Path,
    after_path: Path,
) -> PersistedDocumentDiffRequest:
    contracts = ContractCatalog.load(ROOT / "contracts")
    profiles = ProfileCatalog.load(ROOT / "profiles")
    request = PersistedDocumentDiffRequest(
        diff=DocumentDiffRequest(
            project_id=project_id,
            domain="ui",
            fact_type="screen_element",
            source_snapshot_id=f"snapshot-before-{suffix}",
            target_snapshot_id=f"snapshot-after-{suffix}",
            before_path=before_path,
            after_path=after_path,
        ),
        ingestion_batch_id=f"ingestion-{suffix}",
        analysis_case_id=case_id,
        document_id=f"document-{suffix}",
        logical_name="02_画面設計書_経費精算申請一覧.xlsx",
        source_document_version_id=f"version-before-{suffix}",
        target_document_version_id=f"version-after-{suffix}",
        source_ref=f"immutable://design/{suffix}/before.xlsx",
        target_ref=f"immutable://design/{suffix}/after.xlsx",
        profile_version_id=f"document-profile-{suffix}",
        profile_binding_key="document:screen_design",
        profile_activation_event_id=f"document-profile-activation-{suffix}",
        activated_by="reviewer@example.invalid",
        activation_reason="Reviewed Document Convention",
    )
    PersistedDocumentDiffService(
        connection=connection,
        document_diff=DocumentDiffService(
            extractors=DocumentSignalExtractorRegistry.default(),
            contracts=contracts,
        ),
        contracts=contracts,
        profiles=profiles,
    ).run(
        request,
        load_object(ROOT / "profiles/screen-design-convention-profile.example.json"),
    )
    return request


def build_request(
    *,
    suffix: str,
    project_id: str,
    snapshot_id: str,
    label: str,
) -> SearchIndexBuildRequest:
    return SearchIndexBuildRequest(
        build_id=f"search-build-{label}-{suffix}",
        project_id=project_id,
        snapshot_id=snapshot_id,
        profile_version_id=f"embedding-profile-{suffix}",
        profile_binding_key="embedding:document_search",
        profile_activation_event_id=f"embedding-activation-{label}-{suffix}",
        activated_by="indexer@example.invalid",
        activation_reason=f"Build {label} Snapshot index",
    )


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_search_index_build_reuses_vectors_across_snapshots(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    case_id = f"case-{suffix}"
    before_path = tmp_path / "02_画面設計書_before.xlsx"
    after_path = tmp_path / "02_画面設計書_after.xlsx"
    write_screen_design(before_path, "申請中")
    write_screen_design(after_path, "申請中", document_title="after revision")

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        insert_project_case(
            connection,
            project_id=project_id,
            case_id=case_id,
            suffix=suffix,
        )
        persisted = persist_snapshots(
            connection,
            suffix=suffix,
            project_id=project_id,
            case_id=case_id,
            before_path=before_path,
            after_path=after_path,
        )
        service = SearchIndexBuildService(
            connection=connection,
            profiles=ProfileCatalog.load(ROOT / "profiles"),
        )
        provider = DeterministicEmbeddingProvider()
        profile = embedding_profile()
        source_request = build_request(
            suffix=suffix,
            project_id=project_id,
            snapshot_id=persisted.diff.source_snapshot_id,
            label="source",
        )
        target_request = build_request(
            suffix=suffix,
            project_id=project_id,
            snapshot_id=persisted.diff.target_snapshot_id,
            label="target",
        )

        source = service.run(source_request, profile=profile, provider=provider)
        target = service.run(target_request, profile=profile, provider=provider)
        replay = service.run(source_request, profile=profile, provider=provider)

        assert source.state.status is SearchIndexBuildStatus.READY
        assert source.state.eligible_target_count == source.state.indexed_target_count == 1
        assert source.generated_vector_count == 1
        assert source.state.reused_vector_count == 0
        assert target.state.status is SearchIndexBuildStatus.READY
        assert target.generated_vector_count == 0
        assert target.state.reused_vector_count == 1
        assert replay.state == source.state
        assert replay.generated_vector_count == 0
        assert len(provider.embedded_texts) == 1
        assert provider.probe_calls == 3
        with pytest.raises(ValueError, match="cannot fail from ready"):
            SearchIndexRepository(connection).fail_build(
                failure_event_id="forbidden-ready-failure",
                build_id=source.state.spec.build_id,
                kind=SearchIndexFailureKind.EMBEDDING_GENERATION,
                actor="test-indexer",
                reason="a ready build must remain immutable",
            )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT count(DISTINCT vector_cache_id)
                     FROM search_index_entries WHERE project_id = %s),
                    (SELECT count(*) FROM search_index_entries WHERE project_id = %s),
                    (SELECT count(*) FROM search_index_builds
                     WHERE project_id = %s AND is_current)
                """,
                (project_id, project_id, project_id),
            )
            assert cursor.fetchone() == (1, 2, 2)

        repository = SearchIndexRepository(connection)
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT search_index_keyword_drift_probe")
            cursor.execute(
                """
                UPDATE search_index_entries
                SET keyword_text = keyword_text || ' drifted'
                WHERE search_index_build_id = %s
                """,
                (target.state.spec.build_id,),
            )
        with pytest.raises(PersistenceConflictError, match="entry ledger digest differs"):
            repository.get_build(target.state.spec.build_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT search_index_keyword_drift_probe")
            cursor.execute("RELEASE SAVEPOINT search_index_keyword_drift_probe")

            cursor.execute("SAVEPOINT search_index_vector_drift_probe")
            cursor.execute(
                """
                UPDATE document_search_vectors
                SET embedding = '[0.125,0.25,0.5]'::public.vector
                WHERE vector_cache_id IN (
                    SELECT vector_cache_id
                    FROM search_index_entries
                    WHERE search_index_build_id = %s
                )
                """,
                (target.state.spec.build_id,),
            )
        with pytest.raises(PersistenceConflictError, match="entry ledger digest differs"):
            repository.get_build(target.state.spec.build_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT search_index_vector_drift_probe")
            cursor.execute("RELEASE SAVEPOINT search_index_vector_drift_probe")

            cursor.execute("SAVEPOINT search_index_entry_delete_probe")
            cursor.execute(
                "DELETE FROM search_index_entries WHERE search_index_build_id = %s",
                (source.state.spec.build_id,),
            )
        with pytest.raises(PersistenceConflictError, match="ledger count differs"):
            repository.get_build(source.state.spec.build_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT search_index_entry_delete_probe")
            cursor.execute("RELEASE SAVEPOINT search_index_entry_delete_probe")
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_relation_build_invalidates_index_and_rebuild_binds_current_relation(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    case_id = f"case-{suffix}"
    before_path = tmp_path / "02_画面設計書_before.xlsx"
    after_path = tmp_path / "02_画面設計書_after.xlsx"
    write_screen_design(before_path, "申請中")
    write_screen_design(after_path, "すべて")

    with psycopg.connect(DATABASE_URL) as connection:
        profiles = ProfileCatalog.load(ROOT / "profiles")
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        insert_project_case(
            connection,
            project_id=project_id,
            case_id=case_id,
            suffix=suffix,
        )
        persisted = persist_snapshots(
            connection,
            suffix=suffix,
            project_id=project_id,
            case_id=case_id,
            before_path=before_path,
            after_path=after_path,
        )
        index_service = SearchIndexBuildService(connection=connection, profiles=profiles)
        provider = DeterministicEmbeddingProvider()
        initial_request = build_request(
            suffix=suffix,
            project_id=project_id,
            snapshot_id=persisted.diff.target_snapshot_id,
            label="before-relations",
        )
        initial = index_service.run(
            initial_request,
            profile=embedding_profile(),
            provider=provider,
        )
        assert initial.state.spec.relation_build_id is None
        assert initial.state.status is SearchIndexBuildStatus.READY

        relation_build_id = f"document-relation-build-{suffix}"
        relation_profile: dict[str, Any] = {
            "profile_type": "DocumentRelationProfile",
            "profile_id": "index-invalidation-relations",
            "profile_version": "1.0.0",
            "rules": [
                {
                    "rule_id": "same-default-value",
                    "relation_label": "shares_default_value",
                    "source_document_types": ["screen_design"],
                    "source_fact_types": ["screen_element"],
                    "source_fields": ["default_value"],
                    "target_document_types": ["screen_design"],
                    "target_fact_types": ["screen_element"],
                    "target_fields": ["default_value"],
                    "value_normalizers": ["nfkc_casefold"],
                    "ambiguity_policy": "require_unique_target",
                }
            ],
            "unresolved_policy": "record_and_continue",
        }
        DocumentRelationBuildService(connection=connection, profiles=profiles).run(
            DocumentRelationBuildRequest(
                build_id=relation_build_id,
                project_id=project_id,
                snapshot_id=persisted.diff.target_snapshot_id,
                profile_version_id=f"relation-profile-{suffix}",
                profile_binding_key="relation:document_graph",
                profile_activation_event_id=f"relation-activation-{suffix}",
                activated_by="reviewer@example.invalid",
                activation_reason="Reviewed relation rules",
            ),
            profile=relation_profile,
        )

        repository = SearchIndexRepository(connection)
        stale = repository.get_build(initial_request.build_id)
        assert stale is not None
        assert stale.status is SearchIndexBuildStatus.STALE
        assert not stale.is_current

        current_request = build_request(
            suffix=suffix,
            project_id=project_id,
            snapshot_id=persisted.diff.target_snapshot_id,
            label="current-relations",
        )
        current = index_service.run(
            current_request,
            profile=embedding_profile(),
            provider=provider,
        )
        assert current.state.spec.relation_build_id == relation_build_id
        assert current.state.status is SearchIndexBuildStatus.READY
        assert current.generated_vector_count == 0
        assert current.state.reused_vector_count == current.state.eligible_target_count
        assert len(provider.embedded_texts) == initial.state.eligible_target_count
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_search_index_build_records_provider_failure(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    case_id = f"case-{suffix}"
    before_path = tmp_path / "02_画面設計書_before.xlsx"
    after_path = tmp_path / "02_画面設計書_after.xlsx"
    write_screen_design(before_path, "申請中")
    write_screen_design(after_path, "申請中", document_title="after revision")

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        insert_project_case(
            connection,
            project_id=project_id,
            case_id=case_id,
            suffix=suffix,
        )
        persisted = persist_snapshots(
            connection,
            suffix=suffix,
            project_id=project_id,
            case_id=case_id,
            before_path=before_path,
            after_path=after_path,
        )
        request = build_request(
            suffix=suffix,
            project_id=project_id,
            snapshot_id=persisted.diff.target_snapshot_id,
            label="failed",
        )
        provider = DeterministicEmbeddingProvider()
        provider.fail_embed = True
        service = SearchIndexBuildService(
            connection=connection,
            profiles=ProfileCatalog.load(ROOT / "profiles"),
        )

        with pytest.raises(EmbeddingProviderError, match="deterministic provider failure"):
            service.run(request, profile=embedding_profile(), provider=provider)

        repository = SearchIndexRepository(connection)
        state = repository.get_build(request.build_id)
        assert state is not None
        assert state.status is SearchIndexBuildStatus.FAILED
        assert not state.is_current
        assert state.failure_event_id == search_index_failure_event_id(request.build_id)
        assert state.failure_kind is SearchIndexFailureKind.EMBEDDING_GENERATION
        assert state.failure_actor == "operamind-build-index@1"
        assert state.failure_reason == "EmbeddingProviderError: embedding generation failed"
        assert state.failure_stale_before is None
        assert (
            repository.fail_build(
                failure_event_id=state.failure_event_id,
                build_id=request.build_id,
                kind=SearchIndexFailureKind.EMBEDDING_GENERATION,
                actor="operamind-build-index@1",
                reason=state.failure_reason,
            )
            == state
        )
        with pytest.raises(PersistenceConflictError, match="different failure event"):
            repository.fail_build(
                failure_event_id=state.failure_event_id,
                build_id=request.build_id,
                kind=SearchIndexFailureKind.EMBEDDING_GENERATION,
                actor="operamind-build-index@1",
                reason="different failure reason",
            )
        with pytest.raises(SearchIndexBuildBlockedError, match="use a new build ID"):
            provider.fail_embed = False
            service.run(request, profile=embedding_profile(), provider=provider)

        interrupted_spec = replace(
            state.spec,
            build_id=f"search-build-interrupted-{suffix}",
        )
        interrupted_start = repository.start_build(
            spec=interrupted_spec,
            eligible_target_count=state.eligible_target_count,
        )
        assert interrupted_start.created
        assert interrupted_start.state.status is SearchIndexBuildStatus.BUILDING
        with pytest.raises(SearchIndexBuildBlockedError, match="already building"):
            service.run(
                replace(
                    request,
                    build_id=interrupted_spec.build_id,
                    profile_activation_event_id=f"interrupted-activation-{suffix}",
                ),
                profile=embedding_profile(),
                provider=provider,
            )
        with pytest.raises(ValueError, match="newer than the recovery boundary"):
            repository.recover_stale_build(
                recovery_id=f"future-recovery-{suffix}",
                build_id=interrupted_spec.build_id,
                actor="operator@example.invalid",
                reason="must not recover against a future boundary",
                stale_before=datetime.now(UTC) + timedelta(minutes=1),
            )
        stale_before = datetime.now(UTC)
        recovered = repository.recover_stale_build(
            recovery_id=f"recovery-{suffix}",
            build_id=interrupted_spec.build_id,
            actor="operator@example.invalid",
            reason="index worker process was interrupted",
            stale_before=stale_before,
        )
        recovered_replay = repository.recover_stale_build(
            recovery_id=f"recovery-{suffix}",
            build_id=interrupted_spec.build_id,
            actor="operator@example.invalid",
            reason="index worker process was interrupted",
            stale_before=stale_before,
        )
        assert recovered_replay == recovered
        assert recovered.failure_kind is SearchIndexFailureKind.STALE_RECOVERY
        assert recovered.failure_actor == "operator@example.invalid"
        assert recovered.failure_stale_before == stale_before
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_hybrid_search_requires_accepted_change_and_returns_ids_only(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    case_id = f"case-{suffix}"
    before_path = tmp_path / "02_画面設計書_before.xlsx"
    after_path = tmp_path / "02_画面設計書_after.xlsx"
    write_screen_design(before_path, "申請中")
    write_screen_design(after_path, "すべて")

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        insert_project_case(
            connection,
            project_id=project_id,
            case_id=case_id,
            suffix=suffix,
        )
        persisted = persist_snapshots(
            connection,
            suffix=suffix,
            project_id=project_id,
            case_id=case_id,
            before_path=before_path,
            after_path=after_path,
        )
        profile = embedding_profile()
        provider = DeterministicEmbeddingProvider()
        build_request_value = build_request(
            suffix=suffix,
            project_id=project_id,
            snapshot_id=persisted.diff.target_snapshot_id,
            label="retrieval",
        )
        SearchIndexBuildService(
            connection=connection,
            profiles=ProfileCatalog.load(ROOT / "profiles"),
        ).run(build_request_value, profile=profile, provider=provider)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT structured_change_id
                FROM structured_changes
                WHERE project_id = %s
                  AND target_snapshot_id = %s
                """,
                (project_id, persisted.diff.target_snapshot_id),
            )
            change_row = cursor.fetchone()
        assert change_row is not None
        change_id = str(change_row[0])
        profiles = ProfileCatalog.load(ROOT / "profiles")
        profile_repository = ProfileRepository(connection, profiles)
        review_repository = StructuredChangeReviewRepository(connection)
        index_repository = SearchIndexRepository(connection)
        search = HybridSearchService(
            profiles=profiles,
            profile_repository=profile_repository,
            review_repository=review_repository,
            index_repository=index_repository,
        )
        request = HybridSearchRequest(
            project_id=project_id,
            target_snapshot_id=persisted.diff.target_snapshot_id,
            change_id=change_id,
            embedding_profile_version_id=build_request_value.profile_version_id,
            profile_binding_key=build_request_value.profile_binding_key,
            source_query_id=f"query-{suffix}",
            query_text="status",
        )

        with pytest.raises(HybridSearchBlockedError, match="must be accepted"):
            search.run(request, provider=provider)
        review_repository.review(
            review_event_id=f"review-{suffix}",
            project_id=project_id,
            change_id=change_id,
            decision=StructuredChangeReviewDecision.ACCEPTED,
            reviewed_by="reviewer@example.invalid",
            reason="Verified against source design",
            expected_previous_review_event_id=None,
        )
        result = search.run(request, provider=provider)

        targets = index_repository.load_targets(
            project_id=project_id,
            snapshot_id=persisted.diff.target_snapshot_id,
        )
        assert result.search_index_build_id == build_request_value.build_id
        assert len(result.candidates) == 1
        assert result.candidates[0].target_id == targets[0].node.node_id
        assert result.candidates[0].target_type == "slice"
        assert result.candidates[0].source_query_id == f"query-{suffix}"
        assert {channel.value for channel in result.candidates[0].channels} == {
            "vector",
            "keyword",
        }
        assert not hasattr(result.candidates[0], "content")
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_rag_readiness_appends_evidence_and_invalidates_review_reversal(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    case_id = f"case-{suffix}"
    before_path = tmp_path / "02_画面設計書_before.xlsx"
    after_path = tmp_path / "02_画面設計書_after.xlsx"
    write_screen_design(before_path, "申請中")
    write_screen_design(after_path, "すべて")

    with psycopg.connect(DATABASE_URL) as connection:
        contracts = ContractCatalog.load(ROOT / "contracts")
        profiles = ProfileCatalog.load(ROOT / "profiles")
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        insert_project_case(
            connection,
            project_id=project_id,
            case_id=case_id,
            suffix=suffix,
        )
        persisted = persist_snapshots(
            connection,
            suffix=suffix,
            project_id=project_id,
            case_id=case_id,
            before_path=before_path,
            after_path=after_path,
        )
        build_request_value = build_request(
            suffix=suffix,
            project_id=project_id,
            snapshot_id=persisted.diff.target_snapshot_id,
            label="readiness",
        )
        SearchIndexBuildService(connection=connection, profiles=profiles).run(
            build_request_value,
            profile=embedding_profile(),
            provider=DeterministicEmbeddingProvider(),
        )
        ingestion_repository = DocumentIngestionResultRepository(connection, contracts)
        initial = ingestion_repository.get_latest(
            project_id=project_id,
            ingestion_batch_id=persisted.ingestion_batch_id,
        )
        assert initial is not None
        assert initial.status is DocumentIngestionStatus.NEEDS_REVIEW
        initial_artifact = ArtifactRepository(connection, contracts).get(
            persisted.ingestion_batch_id
        )
        assert initial_artifact is not None
        readiness = RagReadinessService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        )
        pending_request = RagReadinessRequest(
            event_id=f"ingestion-ready-pending-{suffix}",
            project_id=project_id,
            ingestion_batch_id=persisted.ingestion_batch_id,
            analysis_case_id=case_id,
            expected_previous_event_id=initial.event_id,
            search_index_build_id=build_request_value.build_id,
            embedding_profile_binding_key=build_request_value.profile_binding_key,
        )

        pending = readiness.run(pending_request)
        replay = readiness.run(pending_request)

        assert pending.created
        assert pending.event.status is DocumentIngestionStatus.NEEDS_REVIEW
        assert pending.event.previous_event_id == initial.event_id
        assert pending.event.artifact["embedding_index_status"] == "ready"
        assert pending.event.artifact["blocking_reasons"] == ["structured_changes_require_review"]
        assert pending.analysis_case_status == "indexing_rag"
        assert not replay.created
        assert replay.event == pending.event
        assert replay.analysis_case_status == "indexing_rag"
        with pytest.raises(
            RagReadinessBlockedError,
            match="different persisted content",
        ):
            readiness.run(
                replace(
                    pending_request,
                    embedding_profile_binding_key="embedding:different-binding",
                )
            )
        with pytest.raises(RagReadinessBlockedError, match="Stale RAG readiness request"):
            readiness.run(
                RagReadinessRequest(
                    event_id=f"ingestion-stale-{suffix}",
                    project_id=project_id,
                    ingestion_batch_id=persisted.ingestion_batch_id,
                    analysis_case_id=case_id,
                    expected_previous_event_id=initial.event_id,
                    search_index_build_id=build_request_value.build_id,
                    embedding_profile_binding_key=build_request_value.profile_binding_key,
                )
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT structured_change_id
                FROM structured_changes
                WHERE project_id = %s AND target_snapshot_id = %s
                """,
                (project_id, persisted.diff.target_snapshot_id),
            )
            change_row = cursor.fetchone()
        assert change_row is not None
        change_id = str(change_row[0])
        reviews = StructuredChangeReviewRepository(connection)
        accepted_review_id = f"review-accepted-{suffix}"
        reviews.review(
            review_event_id=accepted_review_id,
            project_id=project_id,
            change_id=change_id,
            decision=StructuredChangeReviewDecision.ACCEPTED,
            reviewed_by="reviewer@example.invalid",
            reason="Verified against the source design",
            expected_previous_review_event_id=None,
        )
        accepted_request = RagReadinessRequest(
            event_id=f"ingestion-ready-accepted-{suffix}",
            project_id=project_id,
            ingestion_batch_id=persisted.ingestion_batch_id,
            analysis_case_id=case_id,
            expected_previous_event_id=pending.event.event_id,
            search_index_build_id=build_request_value.build_id,
            embedding_profile_binding_key=build_request_value.profile_binding_key,
        )

        accepted = readiness.run(accepted_request)

        assert accepted.event.status is DocumentIngestionStatus.READY_FOR_IMPACT
        assert accepted.event.previous_status is DocumentIngestionStatus.NEEDS_REVIEW
        assert accepted.event.artifact["blocking_reasons"] == []
        assert accepted.event.artifact["ingestion_result_event_id"] == accepted_request.event_id
        assert accepted.event.artifact["search_index_build_id"] == build_request_value.build_id
        assert (
            accepted.event.artifact["embedding_profile_version_id"]
            == build_request_value.profile_version_id
        )
        assert (
            accepted.event.artifact["embedding_profile_binding_key"]
            == build_request_value.profile_binding_key
        )
        assert accepted.analysis_case_status == "ready_for_impact"
        accepted_artifact = copy.deepcopy(accepted.event.artifact)
        accepted_artifact["embedding_profile_version_id"] = f"drifted-profile-{suffix}"
        accepted_payload = json.dumps(
            accepted_artifact,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT ingestion_build_binding_drift_probe")
            cursor.execute(
                """
                UPDATE artifact_records
                SET payload = %s::jsonb,
                    payload_digest = %s
                WHERE artifact_id = %s
                """,
                (
                    accepted_payload,
                    hashlib.sha256(accepted_payload.encode()).hexdigest(),
                    accepted_request.event_id,
                ),
            )
        with pytest.raises(PersistenceConflictError, match="drifted from Search Index Build"):
            ingestion_repository.get_event(accepted_request.event_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT ingestion_build_binding_drift_probe")
            cursor.execute("RELEASE SAVEPOINT ingestion_build_binding_drift_probe")

        reviews.review(
            review_event_id=f"review-rejected-{suffix}",
            project_id=project_id,
            change_id=change_id,
            decision=StructuredChangeReviewDecision.REJECTED,
            reviewed_by="reviewer@example.invalid",
            reason="Later evidence invalidated the accepted interpretation",
            expected_previous_review_event_id=accepted_review_id,
        )
        rejected = readiness.run(
            RagReadinessRequest(
                event_id=f"ingestion-ready-rejected-{suffix}",
                project_id=project_id,
                ingestion_batch_id=persisted.ingestion_batch_id,
                analysis_case_id=case_id,
                expected_previous_event_id=accepted.event.event_id,
                search_index_build_id=build_request_value.build_id,
                embedding_profile_binding_key=build_request_value.profile_binding_key,
            )
        )

        assert rejected.event.status is DocumentIngestionStatus.BLOCKED
        assert rejected.event.previous_status is DocumentIngestionStatus.READY_FOR_IMPACT
        assert rejected.event.artifact["blocking_reasons"] == ["structured_changes_rejected"]
        assert rejected.analysis_case_status == "reanalysis_required"
        assert ArtifactRepository(connection, contracts).get(persisted.ingestion_batch_id) == (
            initial_artifact
        )
        assert initial_artifact["embedding_index_status"] == "not_started"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*), count(DISTINCT artifact_id)
                FROM document_ingestion_result_events
                WHERE project_id = %s AND ingestion_batch_id = %s
                """,
                (project_id, persisted.ingestion_batch_id),
            )
            assert cursor.fetchone() == (4, 4)
            cursor.execute(
                "SELECT count(*) FROM artifact_records WHERE artifact_id = %s",
                (f"ingestion-stale-{suffix}",),
            )
            assert cursor.fetchone() == (0,)
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_context_package_plans_rehydrates_persists_and_enforces_budget(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    case_id = f"case-{suffix}"
    before_path = tmp_path / "02_画面設計書_before.xlsx"
    after_path = tmp_path / "02_画面設計書_after.xlsx"
    write_screen_design(before_path, "申請中")
    write_screen_design(after_path, "すべて")

    with psycopg.connect(DATABASE_URL) as connection:
        contracts = ContractCatalog.load(ROOT / "contracts")
        profiles = ProfileCatalog.load(ROOT / "profiles")
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        insert_project_case(
            connection,
            project_id=project_id,
            case_id=case_id,
            suffix=suffix,
        )
        persisted = persist_snapshots(
            connection,
            suffix=suffix,
            project_id=project_id,
            case_id=case_id,
            before_path=before_path,
            after_path=after_path,
        )
        build_request_value = build_request(
            suffix=suffix,
            project_id=project_id,
            snapshot_id=persisted.diff.target_snapshot_id,
            label="context",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT structured_change_id
                FROM structured_changes
                WHERE project_id = %s AND target_snapshot_id = %s
                """,
                (project_id, persisted.diff.target_snapshot_id),
            )
            change_row = cursor.fetchone()
        assert change_row is not None
        change_id = str(change_row[0])
        StructuredChangeReviewRepository(connection).review(
            review_event_id=f"review-context-{suffix}",
            project_id=project_id,
            change_id=change_id,
            decision=StructuredChangeReviewDecision.ACCEPTED,
            reviewed_by="reviewer@example.invalid",
            reason="Approved for Context Package generation",
            expected_previous_review_event_id=None,
        )
        ingestion_repository = DocumentIngestionResultRepository(connection, contracts)
        initial = ingestion_repository.get_latest(
            project_id=project_id,
            ingestion_batch_id=persisted.ingestion_batch_id,
        )
        assert initial is not None
        readiness_request = RagReadinessRequest(
            event_id=f"ingestion-context-ready-{suffix}",
            project_id=project_id,
            ingestion_batch_id=persisted.ingestion_batch_id,
            analysis_case_id=case_id,
            expected_previous_event_id=initial.event_id,
            search_index_build_id=build_request_value.build_id,
            embedding_profile_binding_key=build_request_value.profile_binding_key,
        )
        relation_build_id = f"document-relation-build-{suffix}"
        relation_profile: dict[str, Any] = {
            "profile_type": "DocumentRelationProfile",
            "profile_id": "context-relation-profile",
            "profile_version": "1.0.0",
            "rules": [
                {
                    "rule_id": "same-default-value",
                    "relation_label": "shares_default_value",
                    "source_document_types": ["screen_design"],
                    "source_fact_types": ["screen_element"],
                    "source_fields": ["default_value"],
                    "target_document_types": ["screen_design"],
                    "target_fact_types": ["screen_element"],
                    "target_fields": ["default_value"],
                    "value_normalizers": ["nfkc_casefold"],
                    "ambiguity_policy": "require_unique_target",
                }
            ],
            "unresolved_policy": "record_and_continue",
        }
        relation_build = DocumentRelationBuildService(
            connection=connection,
            profiles=profiles,
        ).run(
            DocumentRelationBuildRequest(
                build_id=relation_build_id,
                project_id=project_id,
                snapshot_id=persisted.diff.target_snapshot_id,
                profile_version_id=f"relation-profile-{suffix}",
                profile_binding_key="relation:document_graph",
                profile_activation_event_id=f"relation-activation-{suffix}",
                activated_by="reviewer@example.invalid",
                activation_reason="Reviewed Context relation rules",
            ),
            profile=relation_profile,
        )
        assert relation_build.publication.state.unresolved_count == 1
        SearchIndexBuildService(connection=connection, profiles=profiles).run(
            build_request_value,
            profile=embedding_profile(),
            provider=DeterministicEmbeddingProvider(),
        )
        ready = RagReadinessService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        ).run(readiness_request)
        service = ContextPackageService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        )
        request = ContextPackageRequest(
            context_package_id=f"context-package-{suffix}",
            project_id=project_id,
            analysis_case_id=case_id,
            ingestion_batch_id=persisted.ingestion_batch_id,
            ingestion_result_event_id=ready.event.event_id,
            target_snapshot_id=persisted.diff.target_snapshot_id,
            change_id=change_id,
            embedding_profile_version_id=build_request_value.profile_version_id,
            embedding_profile_binding_key=build_request_value.profile_binding_key,
            token_budget=10_000,
        )
        query_provider = DeterministicEmbeddingProvider()

        result = service.run(request, provider=query_provider)
        query_count = len(query_provider.embedded_texts)
        query_provider.fail_embed = True
        replay = service.run(request, provider=query_provider)

        artifact = result.artifact
        contracts.validate_artifact(artifact)
        assert result.created
        assert not replay.created
        assert replay.artifact == artifact
        assert len(result.query_plan.queries) == 3
        assert query_count == 3
        assert len(query_provider.embedded_texts) == query_count
        for changed_request in (
            replace(request, ingestion_batch_id=f"different-batch-{suffix}"),
            replace(
                request,
                embedding_profile_binding_key="embedding:different-binding",
            ),
            replace(
                request,
                embedding_profile_version_id=f"different-profile-{suffix}",
            ),
            replace(request, vector_top_k=request.vector_top_k + 1),
            replace(request, keyword_top_k=request.keyword_top_k + 1),
            replace(request, final_top_k=request.final_top_k + 1),
            replace(request, adjacent_distance=request.adjacent_distance + 1),
        ):
            with pytest.raises(
                ContextPackageBlockedError,
                match="different persisted request scope",
            ):
                service.run(changed_request, provider=query_provider)
        assert artifact["document_ingestion_result_event_id"] == ready.event.event_id
        assert artifact["search_index_build_id"] == build_request_value.build_id
        assert artifact["document_relation_build_id"] == relation_build_id
        assert artifact["query_plan_version"] == "structured-change-query-v1"
        assert [trace["query_purpose"] for trace in artifact["retrieval_trace"]] == [
            "business_behavior",
            "precise_anchor",
            "acceptance_criteria",
        ]
        assert len(artifact["context_items"]) == 1
        context_item = artifact["context_items"][0]
        assert context_item["section_id"] != context_item["evidence_refs"][0]
        assert context_item["relevance_reason"] == "direct_change"
        assert "default_value: すべて" in context_item["compressed_summary"]
        assert artifact["unknowns"] == ["unresolved_document_relations:1"]
        assert 0 < artifact["estimated_tokens"] <= artifact["token_budget"]
        assert ArtifactRepository(connection, contracts).get(request.context_package_id) == artifact

        query_provider.fail_embed = False
        too_small_id = f"context-package-too-small-{suffix}"
        with pytest.raises(ContextPackageBudgetError, match="exceeds token budget"):
            service.run(
                ContextPackageRequest(
                    context_package_id=too_small_id,
                    project_id=project_id,
                    analysis_case_id=case_id,
                    ingestion_batch_id=persisted.ingestion_batch_id,
                    ingestion_result_event_id=ready.event.event_id,
                    target_snapshot_id=persisted.diff.target_snapshot_id,
                    change_id=change_id,
                    embedding_profile_version_id=build_request_value.profile_version_id,
                    embedding_profile_binding_key=build_request_value.profile_binding_key,
                    token_budget=1,
                ),
                provider=query_provider,
            )
        assert ArtifactRepository(connection, contracts).get(too_small_id) is None
        connection.rollback()
