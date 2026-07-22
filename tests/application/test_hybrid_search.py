from operamind.application.hybrid_search import _reciprocal_rank_fusion
from operamind.domain import SearchChannel
from operamind.infrastructure.postgres import RankedSearchHit


def test_rrf_combines_channels_and_normalizes_top_score() -> None:
    candidates = _reciprocal_rank_fusion(
        vector_hits=(
            RankedSearchHit(target_node_id="node-a", rank=1, score=0.9),
            RankedSearchHit(target_node_id="node-b", rank=2, score=0.8),
        ),
        keyword_hits=(
            RankedSearchHit(target_node_id="node-b", rank=1, score=0.7),
            RankedSearchHit(target_node_id="node-c", rank=2, score=0.6),
        ),
        source_query_id="query-1",
        rrf_k=60,
        final_top_k=3,
    )

    assert [candidate.target_id for candidate in candidates] == ["node-b", "node-a", "node-c"]
    assert candidates[0].score == 1.0
    assert candidates[0].channels == (SearchChannel.VECTOR, SearchChannel.KEYWORD)
    assert candidates[0].source_query_id == "query-1"
