import hashlib
import json
from pathlib import Path

from operamind.golden import plan_golden_queries

ROOT = Path(__file__).parents[2]


def test_reviewed_golden_change_derives_three_frozen_query_digests() -> None:
    expected_changes = json.loads(
        (
            ROOT
            / "golden-dataset/cases/visiondemo-expense-status-filter-golden/expected-changes.json"
        ).read_text(encoding="utf-8")
    )
    expected_context = json.loads(
        (
            ROOT
            / "golden-dataset/cases/visiondemo-expense-status-filter-golden"
            / "expected-rag-context.json"
        ).read_text(encoding="utf-8")
    )

    plan = plan_golden_queries(expected_changes, expected_context)

    assert plan.planner_version == "golden-cross-document-query-v2"
    assert [query.purpose.value for query in plan.queries] == [
        "business_behavior",
        "precise_anchor",
        "acceptance_criteria",
    ]
    assert [hashlib.sha256(query.text.encode()).hexdigest() for query in plan.queries] == [
        "b65e8e890fce4c230f916978793e23c1a2432f396fa310c4951a66988e2b29fd",
        "2ee7bd4228d015cc43fe28b5df173cc6d541be58ea44450e0c76b28e3b407f20",
        "b6129afc56c33ea38f939708516b9d6983164ea46af0a773a741c8bcf849a421",
    ]
