from operamind.infrastructure.postgres.golden_binding_repository import (
    _IndexedNode,
    _resolve_context,
)


def test_reviewed_primary_location_selects_one_node_from_supplemental_locations() -> None:
    document = "02_画面設計書_経費精算申請一覧.xlsx"
    primary = _IndexedNode(
        node_id="node-status-filter",
        document=document,
        business_keys=("expense-search-status",),
        source_refs=(f"{document}#画面項目一覧!B5",),
    )
    supplemental = _IndexedNode(
        node_id="node-search-event",
        document=document,
        business_keys=("検索",),
        source_refs=(f"{document}#イベント一覧!B5",),
    )

    binding = _resolve_context(
        semantic_ref="visiondemo:screen-expense-list:status-filter",
        document=document,
        locations=("画面項目一覧!A5:I5", "イベント一覧!A5:G6"),
        nodes=(primary, supplemental),
    )

    assert binding.canonical_node_id == "node-status-filter"
    assert binding.matched_locations == ("画面項目一覧!A5:I5",)
    assert binding.unmatched_locations == ("イベント一覧!A5:G6",)
    assert binding.resolution_method == "reviewed_source_location"


def test_reviewed_primary_location_remains_fail_closed_when_ambiguous() -> None:
    document = "02_画面設計書_経費精算申請一覧.xlsx"
    nodes = tuple(
        _IndexedNode(
            node_id=f"node-{index}",
            document=document,
            business_keys=(f"key-{index}",),
            source_refs=(f"{document}#画面項目一覧!{column}5",),
        )
        for index, column in enumerate(("B", "C"), start=1)
    )

    try:
        _resolve_context(
            semantic_ref="visiondemo:screen-expense-list:status-filter",
            document=document,
            locations=("画面項目一覧!A5:I5",),
            nodes=nodes,
        )
    except ValueError as error:
        assert "resolved=2" in str(error)
    else:
        raise AssertionError("ambiguous primary location must remain blocked")
