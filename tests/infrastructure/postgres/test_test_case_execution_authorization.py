from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from operamind.domain.test_case_execution_scope import (
    TestCaseExecutionScopeComparison as ScopeComparison,
)
from operamind.infrastructure.postgres.test_case_execution_authorization_repository import (
    TestCaseExecutionAuthorizationRepository as AuthorizationRepository,
)


def test_deterministic_scope_selects_the_newest_equivalent_grant() -> None:
    repository = object.__new__(AuthorizationRepository)
    repository._orchestrations = SimpleNamespace(  # type: ignore[attr-defined]
        bundle=lambda _orchestration_id: {
            "orchestration": {
                "project_id": "visiondemo",
                "analysis_case_id": "case-1",
                "ui_scenarios": [],
            }
        }
    )
    repository._revision = lambda _orchestration_id: {  # type: ignore[method-assign]
        "revision_id": "revision-1"
    }
    repository._eligible_grants = lambda **_values: [  # type: ignore[method-assign]
        "grant-new",
        "grant-old",
    ]
    comparison = ScopeComparison(
        source_scope_digest="source",
        target_scope_digest="target",
        changed_dimensions=("test_data",),
        dimensions=(),
    )
    repository._comparison = lambda *_args: comparison  # type: ignore[method-assign]
    repository._authorization_for_grant = (  # type: ignore[method-assign]
        lambda **_values: None
    )
    captured: dict[str, Any] = {}

    def persist(**values: Any) -> SimpleNamespace:
        captured.update(values)
        return SimpleNamespace(approval_grant_id=values["grant_id"])

    repository._persist = persist  # type: ignore[method-assign]

    result = repository.confirm_deterministic_scope(
        target_orchestration_id="orchestration-2",
        actor="automation:operamind",
        at=datetime.now(UTC),
    )

    assert result is not None
    assert result.approval_grant_id == "grant-new"
    assert captured["grant_id"] == "grant-new"
    assert captured["decision"] == "reconfirmed"
