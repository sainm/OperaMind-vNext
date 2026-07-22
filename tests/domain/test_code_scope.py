import pytest

from operamind.domain import (
    CodeAnchor,
    CodeAnchorKind,
    CodeAnchorMatch,
    CodeGraphTraversalPlanner,
    CodeRelationPolicy,
    CodeScopeEdge,
    relation_policy_for_domain,
)


def test_typed_anchor_normalization_is_namespace_specific() -> None:
    evidence = ("node-1",)

    assert CodeAnchor(
        "path", CodeAnchorKind.PATH, "src/main/App.java", evidence
    ).normalized_value == ("src/main/App.java")
    assert (
        CodeAnchor(
            "symbol", CodeAnchorKind.SYMBOL, " Search ( String status ) ", evidence
        ).normalized_value
        == "search(stringstatus)"
    )
    assert (
        CodeAnchor(
            "endpoint", CodeAnchorKind.ENDPOINT, "get //api//expenses", evidence
        ).normalized_value
        == "http:GET:/api/expenses"
    )
    assert (
        CodeAnchor(
            "endpoint-any", CodeAnchorKind.ENDPOINT, "/api/expenses", evidence
        ).normalized_value
        == "http:*:/api/expenses"
    )
    assert CodeAnchor("table", CodeAnchorKind.TABLE, '"EXPENSES"', evidence).normalized_value == (
        "expenses"
    )


def test_traversal_is_per_anchor_bounded_bidirectional_and_cycle_safe() -> None:
    anchors = (CodeAnchor("anchor-a", CodeAnchorKind.SYMBOL, "target", ("document-node-1",)),)
    matches = (CodeAnchorMatch("anchor-a", "symbol-target", ("edge-anchor",)),)
    edges = (
        CodeScopeEdge("edge-call", "calls", "symbol-caller", "symbol-target"),
        CodeScopeEdge("edge-test", "tests", "symbol-test", "symbol-caller"),
        CodeScopeEdge("edge-cycle", "calls", "symbol-target", "symbol-caller"),
    )
    policy = CodeRelationPolicy(
        change_domain="api",
        edge_types=("calls", "tests"),
        max_depth=2,
        include_reverse=True,
    )

    result = CodeGraphTraversalPlanner().traverse(
        anchors=anchors,
        matches=matches,
        edges=edges,
        policy=policy,
        max_states=10,
    )

    assert not result.truncated
    paths = {path.node_ref: path for path in result.paths}
    assert set(paths) == {"symbol-target", "symbol-caller", "symbol-test"}
    assert paths["symbol-target"].distance == 0
    assert paths["symbol-target"].edge_ids == ("edge-anchor",)
    assert paths["symbol-caller"].distance == 1
    assert paths["symbol-caller"].directions[-1] == "reverse"
    assert paths["symbol-test"].distance == 2
    assert paths["symbol-test"].edge_ids[-1] == "edge-test"


def test_traversal_reports_state_ceiling_instead_of_silent_truncation() -> None:
    anchor = CodeAnchor("anchor", CodeAnchorKind.SYMBOL, "start", ("node-1",))
    edges = tuple(
        CodeScopeEdge(f"edge-{index}", "calls", "start", f"target-{index}") for index in range(4)
    )

    result = CodeGraphTraversalPlanner().traverse(
        anchors=(anchor,),
        matches=(CodeAnchorMatch("anchor", "start"),),
        edges=edges,
        policy=CodeRelationPolicy("api", ("calls",), 1, False),
        max_states=3,
    )

    assert result.truncated
    assert len(result.paths) == 3


def test_relation_policy_requires_exact_change_domain() -> None:
    profile: dict[str, object] = {
        "relation_policies": [
            {
                "change_domain": "api",
                "edge_types": ["calls", "tests"],
                "max_depth": 2,
                "include_reverse": True,
            }
        ]
    }

    assert relation_policy_for_domain(profile, "api") == CodeRelationPolicy(
        "api", ("calls", "tests"), 2, True
    )
    assert relation_policy_for_domain(profile, "ui") is None


def test_path_anchor_rejects_workspace_escape() -> None:
    with pytest.raises(ValueError, match="workspace-relative"):
        CodeAnchor("anchor", CodeAnchorKind.PATH, "../secret.java", ("node-1",))
