from operamind.domain import (
    DocumentRelationFact,
    DocumentRelationPlanner,
    RelationUnresolvedReason,
)


def profile() -> dict[str, object]:
    return {
        "profile_type": "DocumentRelationProfile",
        "rules": [
            {
                "rule_id": "screen-api-path",
                "relation_label": "calls_api",
                "source_document_types": ["screen_design"],
                "source_fact_types": ["screen_event"],
                "source_fields": ["api_path"],
                "target_document_types": ["api_design"],
                "target_fact_types": ["api_endpoint"],
                "target_fields": ["path"],
                "value_normalizers": ["url_path"],
            }
        ],
    }


def fact(
    node_id: str,
    *,
    document_type: str,
    fact_type: str,
    values: dict[str, str],
) -> DocumentRelationFact:
    return DocumentRelationFact(
        node_id=node_id,
        document_id=f"document-{node_id}",
        document_type=document_type,
        fact_type=fact_type,
        values=values,
    )


def test_relation_planner_resolves_unique_exact_target_and_records_every_failure() -> None:
    planner = DocumentRelationPlanner.from_validated_profile(profile())
    facts = (
        fact(
            "source-ok",
            document_type="screen_design",
            fact_type="screen_event",
            values={"api_path": "https://example.invalid/expense/api/search?status=all"},
        ),
        fact(
            "source-missing-value",
            document_type="screen_design",
            fact_type="screen_event",
            values={},
        ),
        fact(
            "source-no-target",
            document_type="screen_design",
            fact_type="screen_event",
            values={"api_path": "/missing"},
        ),
        fact(
            "source-ambiguous",
            document_type="screen_design",
            fact_type="screen_event",
            values={"api_path": "/duplicate"},
        ),
        fact(
            "target-ok",
            document_type="api_design",
            fact_type="api_endpoint",
            values={"path": "/expense/api/search/"},
        ),
        fact(
            "target-duplicate-1",
            document_type="api_design",
            fact_type="api_endpoint",
            values={"path": "/duplicate"},
        ),
        fact(
            "target-duplicate-2",
            document_type="api_design",
            fact_type="api_endpoint",
            values={"path": "/duplicate"},
        ),
    )

    plan = planner.plan(facts)

    assert len(plan.relations) == 1
    relation = plan.relations[0]
    assert relation.rule_id == "screen-api-path"
    assert relation.relation_label == "calls_api"
    assert relation.source_node_id == "source-ok"
    assert relation.target_node_id == "target-ok"
    assert len(relation.match_key_digest) == 64
    unresolved = [
        (item.source_node_id, item.reason, item.candidate_target_count) for item in plan.unresolved
    ]
    assert unresolved == [
        ("source-ambiguous", RelationUnresolvedReason.AMBIGUOUS_TARGET, 2),
        ("source-missing-value", RelationUnresolvedReason.MISSING_SOURCE_VALUE, 0),
        ("source-no-target", RelationUnresolvedReason.NO_TARGET, 0),
    ]
    assert plan.unresolved[0].match_key_digest is not None
    assert plan.unresolved[1].match_key_digest is None
