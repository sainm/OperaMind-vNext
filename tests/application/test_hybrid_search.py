from types import SimpleNamespace

from operamind.application.hybrid_search import (
    _reciprocal_rank_fusion,
    _requirement_document_relevance,
    _select_requirement_documents,
)
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


def test_requirement_document_relevance_prefers_matching_business_document() -> None:
    query = "経費精算申請一覧でステータス「すべて」を選択する"

    screen = _requirement_document_relevance(
        query_text=query,
        heading_path=("02_画面設計書_経費精算申請一覧.xlsx", "screen_element"),
        retrieval_score=0.4,
    )
    program = _requirement_document_relevance(
        query_text=query,
        heading_path=("03_プログラム設計書_経費精算申請.xlsx", "program_method"),
        retrieval_score=0.3,
    )
    unrelated = _requirement_document_relevance(
        query_text=query,
        heading_path=("02_画面設計書_帳票出力.xlsx", "screen_element"),
        retrieval_score=1.0,
    )

    assert screen > program > unrelated


def test_requirement_document_relevance_prefers_matching_fragment_in_same_document() -> None:
    query = "経費精算申請一覧でステータス「すべて」は全件、個別状態は完全一致で検索"
    heading = ("02_画面設計書_経費精算申請一覧.xlsx", "screen_element")

    status_filter = _requirement_document_relevance(
        query_text=query,
        heading_path=heading,
        summary="ステータスフィルタ(下書き/申請中/承認済/差戻し)、初期値すべて",
        retrieval_score=0.4,
    )
    edit_button = _requirement_document_relevance(
        query_text=query,
        heading_path=heading,
        summary="編集ボタンをクリックして申請詳細を開く",
        retrieval_score=1.0,
    )

    assert status_filter > edit_button


def test_requirement_document_relevance_prefers_search_over_status_transition() -> None:
    query = (
        "経費精算申請一覧のステータス検索で、すべては全件、"
        "下書き、申請中、承認済、差戻しは完全一致にする"
    )
    heading = ("03_プログラム設計書_経費精算申請.xlsx", "program_method")

    search = _requirement_document_relevance(
        query_text=query,
        heading_path=heading,
        summary="search ステータスで経費精算を検索。null/空文字は全件検索",
        retrieval_score=0.4,
    )
    submit = _requirement_document_relevance(
        query_text=query,
        heading_path=heading,
        summary="submit 経費精算のステータスを申請中に変更。下書きから呼出",
        retrieval_score=1.0,
    )

    assert search > submit


def test_exact_document_subject_does_not_remove_related_documents() -> None:
    candidates = _reciprocal_rank_fusion(
        vector_hits=(
            RankedSearchHit(target_node_id="screen-node", rank=1, score=0.9),
            RankedSearchHit(target_node_id="api-node", rank=2, score=0.8),
        ),
        keyword_hits=(),
        source_query_id="requirement-document-discovery",
        rrf_k=60,
        final_top_k=2,
    )
    ranked = [
        (
            candidates[0],
            SimpleNamespace(document_id="screen-design"),
            (1, 1.0, 1, 1.0, 4, 8, candidates[0].score),
        ),
        (
            candidates[1],
            SimpleNamespace(document_id="api-design"),
            (0, 0.2, 1, 0.5, 2, 6, candidates[1].score),
        ),
    ]

    selected = _select_requirement_documents(ranked, final_top_k=2)

    assert [record.document_id for _candidate, record in selected] == [
        "screen-design",
        "api-design",
    ]
