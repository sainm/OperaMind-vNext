from operamind.domain import RagQueryPurpose, StructuredChangeQueryPlanner


def modified_change() -> dict[str, object]:
    return {
        "change_id": "change-1",
        "stable_key": "screen_element:screen-a/status",
        "fact_type": "screen_element",
        "domain": "ui",
        "change_type": "modified",
        "summary": "Status default changed",
        "before": {
            "values": {
                "description": "Status selector",
                "default_value": "申請中",
                "element_id": "status",
            }
        },
        "after": {
            "values": {
                "description": "All status selector",
                "default_value": "すべて",
                "element_id": "status",
            }
        },
    }


def test_query_plan_has_three_deterministic_content_only_purposes() -> None:
    planner = StructuredChangeQueryPlanner()

    first = planner.plan(modified_change())
    repeated = planner.plan(modified_change())

    assert first == repeated
    assert first.planner_version == "structured-change-query-v1"
    assert tuple(query.purpose for query in first.queries) == tuple(RagQueryPurpose)
    assert len({query.query_id for query in first.queries}) == 3
    combined = "\n".join(query.text for query in first.queries)
    assert "default_value: 申請中 -> すべて" in combined
    assert "description: Status selector -> All status selector" in combined
    assert "element_id" not in combined
    assert "source_ref" not in combined
    assert "screen_element:screen-a/status" in combined


def test_deleted_change_acceptance_requires_absence() -> None:
    change = modified_change()
    change["change_type"] = "deleted"
    change["after"] = None

    plan = StructuredChangeQueryPlanner().plan(change)

    acceptance = plan.queries[2]
    assert acceptance.purpose is RagQueryPurpose.ACCEPTANCE_CRITERIA
    assert "screen_element:screen-a/status must be absent" in acceptance.text
    assert "default_value was 申請中" in acceptance.text
