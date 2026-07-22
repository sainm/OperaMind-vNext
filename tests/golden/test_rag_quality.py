import pytest

from operamind.golden import RagQualityEvaluator

PURPOSES = ("business_behavior", "precise_anchor", "acceptance_criteria")


def expectation() -> dict[str, object]:
    return {
        "case_id": "case-1",
        "project_id": "project-1",
        "query_expectations": [
            {
                "query_purpose": "business_behavior",
                "required_candidate_refs": ["node-a", "node-b"],
                "irrelevant_candidate_refs": ["node-x"],
            },
            {
                "query_purpose": "precise_anchor",
                "required_candidate_refs": ["node-c"],
                "irrelevant_candidate_refs": ["node-y"],
            },
            {
                "query_purpose": "acceptance_criteria",
                "required_candidate_refs": ["node-d"],
                "irrelevant_candidate_refs": ["node-z"],
            },
        ],
        "quality_thresholds": {
            "min_recall_at_5": 0.6,
            "min_recall_at_10": 0.6,
            "min_mrr": 0.5,
            "max_irrelevant_rate": 0.2,
            "max_cross_project_leaks": 0,
        },
    }


def candidate(target_id: str, project_id: str = "project-1") -> dict[str, str]:
    return {"target_id": target_id, "project_id": project_id}


def test_rag_quality_reports_each_failed_gate_without_hiding_leaks() -> None:
    observed = {
        "case_id": "case-1",
        "project_id": "project-1",
        "query_results": [
            {
                "query_purpose": "business_behavior",
                "candidates": [
                    candidate("node-a"),
                    candidate("node-x"),
                    candidate("noise-1"),
                    candidate("noise-2"),
                    candidate("noise-3"),
                    candidate("node-b"),
                ],
            },
            {
                "query_purpose": "precise_anchor",
                "candidates": [candidate("node-y"), candidate("node-c")],
            },
            {
                "query_purpose": "acceptance_criteria",
                "candidates": [
                    candidate("node-z"),
                    candidate("foreign-node", "project-2"),
                ],
            },
        ],
    }

    result = RagQualityEvaluator().evaluate(expected=expectation(), observed=observed)

    assert result.metrics.recall_at_5 == pytest.approx(0.5)
    assert result.metrics.recall_at_10 == pytest.approx(2 / 3)
    assert result.metrics.mrr == pytest.approx(0.5)
    assert result.metrics.irrelevant_rate == pytest.approx(0.3)
    assert result.metrics.cross_project_leaks == 1
    assert not result.passed
    assert result.failures == (
        "recall_at_5",
        "irrelevant_rate",
        "cross_project_leaks",
    )


def test_rag_quality_passes_complete_rank_one_results() -> None:
    expected = expectation()
    expected["quality_thresholds"] = {
        "min_recall_at_5": 1.0,
        "min_recall_at_10": 1.0,
        "min_mrr": 1.0,
        "max_irrelevant_rate": 0.0,
        "max_cross_project_leaks": 0,
    }
    required = ("node-a", "node-c", "node-d")
    observed = {
        "case_id": "case-1",
        "project_id": "project-1",
        "query_results": [
            {
                "query_purpose": purpose,
                "candidates": [candidate(target_id)],
            }
            for purpose, target_id in zip(PURPOSES, required, strict=True)
        ],
    }
    cast_expectations = expected["query_expectations"]
    assert isinstance(cast_expectations, list)
    assert isinstance(cast_expectations[0], dict)
    cast_expectations[0]["required_candidate_refs"] = ["node-a"]

    result = RagQualityEvaluator().evaluate(expected=expected, observed=observed)

    assert result.passed
    assert result.failures == ()


def test_rag_quality_rejects_missing_query_purpose() -> None:
    observed = {
        "case_id": "case-1",
        "project_id": "project-1",
        "query_results": [],
    }

    with pytest.raises(ValueError, match="all three query purposes"):
        RagQualityEvaluator().evaluate(expected=expectation(), observed=observed)
