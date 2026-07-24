import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from operamind.application import (
    GoldenRagQualityRequest,
    GoldenRagQualityService,
    SearchIndexBuildRequest,
    SearchIndexBuildService,
)
from operamind.contracts import ContractCatalog
from operamind.domain import DocumentNode, DocumentNodeType
from operamind.golden import plan_golden_queries
from operamind.infrastructure.embeddings import (
    EmbeddingBatch,
    EmbeddingProviderError,
    EmbeddingProviderProbe,
)
from operamind.infrastructure.postgres import (
    DocumentNodeRepository,
    GoldenRagQualityGateBlockedError,
    GoldenRagQualityRepository,
    MigrationCatalog,
    MigrationRunner,
    ProfileRepository,
    SearchIndexRepository,
)
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration

SEMANTIC_REFS = (
    "visiondemo:screen-expense-list:status-filter",
    "visiondemo:expense-search:empty-status-contract",
    "visiondemo:expense-api:search-endpoint",
)
FROZEN_EXPECTED = json.loads(
    (
        ROOT
        / "golden-dataset/cases/visiondemo-expense-status-filter-golden/expected-rag-context.json"
    ).read_text(encoding="utf-8")
)
FROZEN_QUERY_PLAN = plan_golden_queries(
    json.loads(
        (
            ROOT
            / "golden-dataset/cases/visiondemo-expense-status-filter-golden/expected-changes.json"
        ).read_text(encoding="utf-8")
    ),
    FROZEN_EXPECTED,
)
QUERIES = tuple(query.text for query in FROZEN_QUERY_PLAN.queries)
VECTORS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class GoldenEmbeddingProvider:
    def __init__(self, *, wrong_queries: bool = False, fail_queries: bool = False) -> None:
        self.wrong_queries = wrong_queries
        self.fail_queries = fail_queries
        self.embed_calls = 0

    def probe(self) -> EmbeddingProviderProbe:
        return EmbeddingProviderProbe(model="golden-test-model", dimensions=3)

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        self.embed_calls += 1
        if self.fail_queries and all(text in QUERIES for text in texts):
            raise EmbeddingProviderError("Golden query provider unavailable")
        vectors = tuple(self._vector(text) for text in texts)
        return EmbeddingBatch(model="golden-test-model", vectors=vectors)

    def _vector(self, text: str) -> tuple[float, ...]:
        for index, query in enumerate(QUERIES):
            if text == query:
                return VECTORS[0] if self.wrong_queries else VECTORS[index]
        for index, target in enumerate(SEMANTIC_REFS):
            if target in text:
                return VECTORS[index]
        raise AssertionError(f"Unexpected embedding input: {text}")


def _load_object(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _expected(project_id: str) -> dict[str, Any]:
    expected = json.loads(json.dumps(FROZEN_EXPECTED, ensure_ascii=False))
    assert isinstance(expected, dict)
    expected["project_id"] = project_id
    return expected


def _seed_index(
    connection: psycopg.Connection[Any], *, suffix: str
) -> tuple[str, str, str, tuple[str, ...]]:
    project_id = f"golden-project-{suffix}"
    snapshot_id = f"golden-snapshot-{suffix}"
    profile_version_id = f"golden-embedding-{suffix}"
    document_profile_version_id = f"golden-document-profile-{suffix}"
    profiles = ProfileCatalog.load(ROOT / "profiles")
    repository = ProfileRepository(connection, profiles)
    document_profile = _load_object(ROOT / "profiles/screen-design-convention-profile.example.json")
    document_profile["profile_id"] = f"golden-document-profile-{suffix}"
    repository.store_version(
        profile_version_id=document_profile_version_id,
        profile=document_profile,
    )
    documents = (
        (
            "02_画面設計書_経費精算申請一覧.xlsx",
            "画面項目一覧!B5",
            "screen_element",
        ),
        (
            "03_プログラム設計書_経費精算申請.xlsx",
            "メソッド一覧!B5",
            "method_contract",
        ),
        (
            "04_API詳細設計書_経費精算申請.xlsx",
            "API一覧!B6",
            "api_endpoint",
        ),
    )
    target_ids = tuple(
        f"golden-physical-node-{index}-{suffix}" for index in range(1, len(documents) + 1)
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, 'Golden RAG test')",
            (project_id,),
        )
        cursor.execute(
            """
            INSERT INTO document_snapshots (
                document_snapshot_id, project_id, status, committed_at
            ) VALUES (%s, %s, 'committed', now())
            """,
            (snapshot_id, project_id),
        )
        for index, (logical_name, _, _) in enumerate(documents, start=1):
            document_id = f"golden-document-{index}-{suffix}"
            document_version_id = f"golden-document-version-{index}-{suffix}"
            cursor.execute(
                """
                INSERT INTO documents (document_id, project_id, logical_name)
                VALUES (%s, %s, %s)
                """,
                (document_id, project_id, logical_name),
            )
            cursor.execute(
                """
                INSERT INTO document_versions (
                    document_version_id, project_id, document_id, source_ref,
                    content_digest, extractor_ref
                ) VALUES (%s, %s, %s, %s, %s, 'golden-test-extractor@1')
                """,
                (
                    document_version_id,
                    project_id,
                    document_id,
                    f"immutable://golden/{index}/{suffix}",
                    str(index) * 64,
                ),
            )
            cursor.execute(
                """
                INSERT INTO snapshot_memberships (
                    project_id, document_snapshot_id, document_version_id,
                    profile_version_id, selected_variant_id, selected_variant_ids
                ) VALUES (
                    %s, %s, %s, %s,
                    'screen-item-table-ja', '["screen-item-table-ja"]'::jsonb
                )
                """,
                (
                    project_id,
                    snapshot_id,
                    document_version_id,
                    document_profile_version_id,
                ),
            )
    for index, ((logical_name, location, fact_type), target_id, semantic_ref) in enumerate(
        zip(documents, target_ids, SEMANTIC_REFS, strict=True),
        start=1,
    ):
        document_version_id = f"golden-document-version-{index}-{suffix}"
        source_ref = f"{logical_name}#{location}"
        parent = DocumentNode(
            node_id=f"golden-section-{index}-{suffix}",
            snapshot_id=snapshot_id,
            document_version_id=document_version_id,
            parent_node_id=None,
            node_type=DocumentNodeType.SECTION,
            ordinal=0,
            heading_path=(logical_name, fact_type),
            business_keys=(),
            summary=f"Golden {fact_type} section",
            content=f"Golden section for {semantic_ref}",
            source_refs=(source_ref,),
            index_eligible=False,
        )
        node = DocumentNode(
            node_id=target_id,
            snapshot_id=snapshot_id,
            document_version_id=document_version_id,
            parent_node_id=parent.node_id,
            node_type=DocumentNodeType.SLICE,
            ordinal=0,
            heading_path=(logical_name, fact_type),
            business_keys=(semantic_ref,),
            summary=f"Frozen target {index}",
            content=f"Canonical evidence for {semantic_ref}",
            source_refs=(source_ref,),
            index_eligible=True,
        )
        DocumentNodeRepository(connection).store_nodes(
            project_id=project_id,
            snapshot_id=snapshot_id,
            nodes=(parent, node),
        )
    profile = _load_object(ROOT / "profiles/embedding-profile.example.json")
    profile["profile_id"] = f"golden-embedding-profile-{suffix}"
    profile["expected_dimensions"] = 3
    profile["batch_size"] = 32
    request = SearchIndexBuildRequest(
        build_id=f"golden-search-index-{suffix}",
        project_id=project_id,
        snapshot_id=snapshot_id,
        profile_version_id=profile_version_id,
        profile_binding_key="embedding:document_search",
        profile_activation_event_id=f"golden-embedding-activation-{suffix}",
        activated_by="golden-test",
        activation_reason="Golden retrieval integration test",
    )
    SearchIndexBuildService(connection=connection, profiles=profiles).run(
        request,
        profile=profile,
        provider=GoldenEmbeddingProvider(),
    )
    return project_id, snapshot_id, request.build_id, target_ids


def _request(
    *, suffix: str, report_id: str, project_id: str, snapshot_id: str, build_id: str
) -> GoldenRagQualityRequest:
    return GoldenRagQualityRequest(
        report_id=report_id,
        case_id="visiondemo-expense-status-filter-golden",
        dataset_id="operamind-vnext-golden",
        dataset_version="1.0.0",
        project_id=project_id,
        document_snapshot_id=snapshot_id,
        embedding_profile_version_id=f"golden-embedding-{suffix}",
        embedding_profile_binding_key="embedding:document_search",
        search_index_build_id=build_id,
        expected=_expected(project_id),
        query_plan_version=FROZEN_QUERY_PLAN.planner_version,
        query_texts=(QUERIES[0], QUERIES[1], QUERIES[2]),
        created_by="golden-test",
    )


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_real_golden_retrieval_persists_metrics_and_fail_closed_gate() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    with psycopg.connect(DATABASE_URL) as connection:
        contracts = ContractCatalog.load(ROOT / "contracts")
        profiles = ProfileCatalog.load(ROOT / "profiles")
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        project_id, snapshot_id, build_id, target_ids = _seed_index(
            connection,
            suffix=suffix,
        )
        discovered = SearchIndexRepository(connection).find_current_builds_containing_targets(
            project_id=project_id,
            profile_version_id=f"golden-embedding-{suffix}",
            target_node_ids=target_ids,
        )
        assert tuple(build.spec.build_id for build in discovered) == (build_id,)
        service = GoldenRagQualityService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        )
        passed_provider = GoldenEmbeddingProvider()
        passed = service.run(
            _request(
                suffix=suffix,
                report_id=f"golden-rag-passed-{suffix}",
                project_id=project_id,
                snapshot_id=snapshot_id,
                build_id=build_id,
            ),
            provider=passed_provider,
        )
        assert passed.state.status == "passed"
        assert passed.state.recall_at_5 == 1.0
        assert passed.state.recall_at_10 == 1.0
        assert passed.state.mrr == 1.0
        first_candidates = [
            result["candidates"][0]["target_id"] for result in passed.artifact["query_results"]
        ]
        assert first_candidates == list(target_ids)
        assert [
            binding["semantic_ref"] for binding in passed.artifact["semantic_bindings"]
        ] == sorted(SEMANTIC_REFS)
        assert {
            binding["resolution_method"] for binding in passed.artifact["semantic_bindings"]
        } == {"reviewed_source_location"}
        GoldenRagQualityRepository(connection, contracts).require_passed_gate(
            project_id=project_id,
            document_snapshot_id=snapshot_id,
            embedding_profile_version_id=f"golden-embedding-{suffix}",
            search_index_build_id=build_id,
        )

        failed = service.run(
            _request(
                suffix=suffix,
                report_id=f"golden-rag-failed-{suffix}",
                project_id=project_id,
                snapshot_id=snapshot_id,
                build_id=build_id,
            ),
            provider=GoldenEmbeddingProvider(wrong_queries=True),
        )
        assert failed.state.status == "failed"
        assert "mrr" in failed.state.threshold_failures
        with pytest.raises(GoldenRagQualityGateBlockedError, match="quality_threshold_failed:mrr"):
            GoldenRagQualityRepository(connection, contracts).require_passed_gate(
                project_id=project_id,
                document_snapshot_id=snapshot_id,
                embedding_profile_version_id=f"golden-embedding-{suffix}",
                search_index_build_id=build_id,
            )

        blocked = service.run(
            _request(
                suffix=suffix,
                report_id=f"golden-rag-blocked-{suffix}",
                project_id=project_id,
                snapshot_id=snapshot_id,
                build_id=build_id,
            ),
            provider=GoldenEmbeddingProvider(fail_queries=True),
        )
        assert blocked.state.status == "blocked"
        assert blocked.state.recall_at_5 is None
        assert "Golden query provider unavailable" in blocked.state.failure_reasons[0]
        connection.rollback()
