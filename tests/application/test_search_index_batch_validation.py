import pytest

from operamind.application.search_index_build import (
    SearchIndexBuildBlockedError,
    SearchIndexBuildService,
)
from operamind.infrastructure.embeddings import EmbeddingBatch
from operamind.infrastructure.postgres import SearchIndexBuildSpec


def _spec() -> SearchIndexBuildSpec:
    return SearchIndexBuildSpec(
        build_id="build-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        profile_version_id="embedding-profile@1",
        model="embedding-model-1",
        dimensions=3,
        preprocessing_version="canonical-section-v1",
        ranking_policy_version="hybrid-rrf-v1",
        relation_build_id=None,
    )


def test_search_index_batch_rejects_all_zero_vector() -> None:
    with pytest.raises(SearchIndexBuildBlockedError, match="all-zero"):
        SearchIndexBuildService._validate_batch(
            EmbeddingBatch(
                model="embedding-model-1",
                vectors=((1.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            ),
            spec=_spec(),
            expected_count=2,
        )
