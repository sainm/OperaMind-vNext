from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from operamind.application.test_data_execution import (
    TestDataExecutionRequest as DataExecutionRequest,
)
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.test_data import (
    BoundFixtureTestDataExecutor,
    BoundSqlTestDataExecutor,
    BoundUiTestDataExecutor,
    HttpResponse,
    SafeHttpTestDataExecutor,
    UiDataActionResult,
)


@dataclass
class FakeTransport:
    response: HttpResponse
    call: tuple[str, str, bytes | None] | None = None

    def send(
        self,
        *,
        method: str,
        url: str,
        body: bytes | None,
        headers: object,
        timeout_seconds: float,
    ) -> HttpResponse:
        del headers, timeout_seconds
        self.call = (method, url, body)
        return self.response


def test_http_executor_uses_bound_origin_and_records_sanitized_evidence(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(
        HttpResponse(
            201,
            {"Content-Type": "application/json"},
            b'{"id":91,"expenseNo":"EXP-001"}',
        )
    )
    executor = SafeHttpTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path), transport=transport
    )

    result = executor.execute(
        request=_request(),
        flow_id="expense-flow",
        step={"step_id": "create", "target": "POST /expense/api/save"},
        resolved_inputs={
            "method": "POST",
            "path": "/expense/api/save",
            "query": {"trace": "test"},
            "json": {"expenseNo": "EXP-001", "token": "top-secret"},
        },
        variables={},
        phase="setup",
    )

    assert transport.call == (
        "POST",
        "http://127.0.0.1:8080/expense/api/save?trace=test",
        b'{"expenseNo":"EXP-001","token":"top-secret"}',
    )
    assert result.source_values["response"] == {"id": 91, "expenseNo": "EXP-001"}
    assert result.failure_reason is None
    assert [value.evidence_type for value in result.evidence] == ["request", "response"]
    request_file = next(tmp_path.rglob("td-request-*.json"))
    assert "top-secret" not in request_file.read_text(encoding="utf-8")


def test_http_executor_rejects_target_drift_and_retains_non_success_observation(
    tmp_path: Path,
) -> None:
    executor = SafeHttpTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path),
        transport=FakeTransport(HttpResponse(409, {}, b'{"error":"duplicate"}')),
    )
    with pytest.raises(ValueError, match="differ from the reviewed target"):
        executor.execute(
            request=_request(),
            flow_id="expense-flow",
            step={"step_id": "create", "target": "POST /expense/api/save"},
            resolved_inputs={"method": "POST", "path": "/admin/delete"},
            variables={},
            phase="setup",
        )

    result = executor.execute(
        request=_request(),
        flow_id="expense-flow",
        step={"step_id": "create", "target": "POST /expense/api/save"},
        resolved_inputs={"method": "POST", "path": "/expense/api/save"},
        variables={},
        phase="setup",
    )
    assert result.failure_reason == "HTTP Test data request returned status 409"
    assert len(result.evidence) == 2


def test_bound_fixture_sql_and_ui_executors_use_only_registered_bindings(
    tmp_path: Path,
) -> None:
    store = LocalEvidenceStore(tmp_path)
    fixture = BoundFixtureTestDataExecutor(
        evidence_store=store,
        bindings={"default-seed": lambda inputs: {"count": inputs["expected_count"]}},
    )
    sql = BoundSqlTestDataExecutor(
        evidence_store=store,
        bindings={"expense-by-id": lambda inputs: {"expense": {"id": inputs["id"]}}},
    )
    ui = BoundUiTestDataExecutor(
        evidence_store=store,
        bindings={
            ("expense-list", "search-expense"): lambda request, inputs, variables: (
                UiDataActionResult(
                    observations={"visible_id": inputs["id"]},
                    screenshot=b"\x89PNG\r\n\x1a\nfixture",
                )
            )
        },
    )

    fixture_result = fixture.execute(
        request=_request(),
        flow_id="flow-fixture",
        step={"step_id": "load", "target": "default-seed"},
        resolved_inputs={"expected_count": 4},
        variables={},
        phase="setup",
    )
    sql_result = sql.execute(
        request=_request(),
        flow_id="flow-sql",
        step={"step_id": "query", "target": "expense-by-id"},
        resolved_inputs={"id": 91},
        variables={},
        phase="setup",
    )
    ui_result = ui.execute(
        request=_request(),
        flow_id="flow-ui",
        step={
            "step_id": "search",
            "screen_ref": "expense-list",
            "ui_action_ref": "search-expense",
        },
        resolved_inputs={"id": 91},
        variables={"expense_id": 91},
        phase="setup",
    )

    assert fixture_result.source_values == {"fixture": {"count": 4}}
    assert sql_result.source_values == {"database": {"expense": {"id": 91}}}
    assert ui_result.source_values == {"ui": {"visible_id": 91}}
    assert [value.evidence_type for value in ui_result.evidence] == [
        "step_log",
        "screenshot",
    ]
    with pytest.raises(ValueError, match="no approved query binding"):
        sql.execute(
            request=_request(),
            flow_id="flow-sql",
            step={"step_id": "raw", "target": "DELETE FROM expenses"},
            resolved_inputs={},
            variables={},
            phase="setup",
        )


def _request() -> DataExecutionRequest:
    return DataExecutionRequest(
        execution_result_id="result-001",
        run_id="run-001",
        project_id="visiondemo",
        base_url="http://127.0.0.1:8080",
    )
