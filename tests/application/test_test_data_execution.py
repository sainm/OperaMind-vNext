from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from operamind.application.test_data_execution import (
    TestDataExecutionEngine as DataExecutionEngine,
)
from operamind.application.test_data_execution import (
    TestDataExecutionEvidence as DataExecutionEvidence,
)
from operamind.application.test_data_execution import (
    TestDataExecutionProgress as DataExecutionProgress,
)
from operamind.application.test_data_execution import (
    TestDataExecutionRequest as DataExecutionRequest,
)
from operamind.application.test_data_execution import (
    TestDataStepExecution as DataStepExecution,
)
from operamind.application.test_data_execution import _assert_postcondition, _extract
from operamind.contracts import ContractCatalog

ROOT = Path(__file__).parents[2]


def test_extract_supports_json_array_index_binding_paths() -> None:
    source = {"content": [{"id": 1}]}

    assert _extract(source, "content[0].id") == (True, 1)
    assert _extract(source, "$.content[0].id") == (True, 1)
    assert _extract(source, "content.0.id") == (True, 1)
    assert _extract(source, "content[1].id") == (False, None)


def test_satisfies_supports_bounded_numeric_predicates() -> None:
    assertion = {
        "assertion_id": "has-employees",
        "observe_via": "response",
        "subject": "totalElements",
        "operator": "satisfies",
        "expected": ">= 1",
    }

    _assert_postcondition(assertion, {"response": {"totalElements": 1}}, {})
    with pytest.raises(AssertionError, match="did not satisfy"):
        _assert_postcondition(assertion, {"response": {"totalElements": 0}}, {})


def test_count_equals_accepts_playwright_numeric_count_and_collections() -> None:
    assertion = {
        "assertion_id": "two-expense-rows",
        "observe_via": "ui",
        "subject": "expense_rows",
        "operator": "count_equals",
        "expected": 2,
    }

    _assert_postcondition(assertion, {"ui": {"expense_rows": 2}}, {})
    _assert_postcondition(assertion, {"ui": {"expense_rows": ["pending", "returned"]}}, {})
    with pytest.raises(AssertionError, match="count did not equal"):
        _assert_postcondition(assertion, {"ui": {"expense_rows": 1}}, {})


@dataclass
class FakeExecutor:
    channel: str
    calls: list[tuple[str, str, dict[str, object]]]
    fail_step: str | None = None

    def execute(
        self,
        *,
        request: DataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> DataStepExecution:
        del variables
        step_id = str(step["step_id"])
        self.calls.append((phase, step_id, dict(resolved_inputs)))
        source: object
        if self.fail_step == step_id == "create-expense":
            source = {"id": 91, "expenseNo": "WRONG"}
        elif self.fail_step == step_id:
            source = {"status": "wrong"}
        elif step_id == "load-fixture":
            source = {"loaded": True}
        elif step_id == "create-expense":
            source = {"id": 91, "expenseNo": "EXP-CROSS-001"}
        elif step_id == "update-expense":
            source = {"status": "申請中"}
        elif step_id == "verify-database":
            source = {"expense": {"id": 91, "status": "申請中"}}
        else:
            source = {"deleted": True}
        source_name = {
            "fixture": "fixture",
            "http": "response",
            "sql": "database",
            "ui": "ui",
        }[self.channel]
        evidence_type = {
            "fixture": "fixture",
            "http": "response",
            "sql": "sql",
            "ui": "screenshot",
        }[self.channel]
        evidence_id = f"{phase}-{step_id}"
        return DataStepExecution(
            source_values={source_name: source},
            evidence=(
                DataExecutionEvidence(
                    evidence_id=evidence_id,
                    flow_id=flow_id,
                    step_id=step_id,
                    phase=phase,
                    evidence_type=evidence_type,
                    evidence_ref=f"evidence://{request.project_id}/{request.run_id}/{evidence_id}",
                    content_digest=("a" * 63 + str(len(self.calls) % 10)),
                ),
            ),
        )


def test_engine_executes_all_channels_in_order_and_passes_variables() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    executors = {
        channel: FakeExecutor(channel, calls) for channel in ("fixture", "http", "sql", "ui")
    }
    result = _engine(executors).execute(plan=_plan(), request=_request())

    assert result["status"] == "passed"
    assert result["cleanup_status"] == "passed"
    assert calls == [
        ("setup", "load-fixture", {}),
        ("setup", "create-expense", {"expenseNo": "EXP-CROSS-001"}),
        ("setup", "update-expense", {"expense_id": 91}),
        ("setup", "verify-database", {"expense_id": 91}),
        ("cleanup", "delete-expense", {"expense_id": 91}),
    ]
    flow = result["flow_results"][0]
    assert flow["step_results"][1]["output_variables"] == ["expense_id"]
    assert flow["deferred_assertion_ids"] == ["scenario-result"]
    assert len(result["evidence"]) == 5


def test_engine_stops_after_failure_and_still_cleans_created_data() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    executors = {
        channel: FakeExecutor(
            channel,
            calls,
            fail_step="update-expense" if channel == "ui" else None,
        )
        for channel in ("fixture", "http", "sql", "ui")
    }
    result = _engine(executors).execute(plan=_plan(), request=_request())

    assert result["status"] == "failed"
    assert result["cleanup_status"] == "passed"
    steps = result["flow_results"][0]["step_results"]
    assert [step["status"] for step in steps] == ["passed", "passed", "failed", "not_run"]
    assert calls[-1] == ("cleanup", "delete-expense", {"expense_id": 91})
    assert any("update-status" in reason for reason in result["failure_reasons"])
    failed = result["flow_results"][0]["step_results"][2]
    assert failed["evidence_refs"] == [
        "evidence://visiondemo/run-cross-screen/setup-update-expense"
    ]
    assert any(evidence["step_id"] == "update-expense" for evidence in result["evidence"])


def test_engine_cleans_side_effect_when_create_postcondition_fails() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    executors = {
        channel: FakeExecutor(
            channel,
            calls,
            fail_step="create-expense" if channel == "http" else None,
        )
        for channel in ("fixture", "http", "sql", "ui")
    }

    result = _engine(executors).execute(plan=_plan(), request=_request())

    assert result["status"] == "failed"
    assert result["cleanup_status"] == "passed"
    assert calls[-1] == ("cleanup", "delete-expense", {"expense_id": 91})
    assert [
        step["status"] for step in result["flow_results"][0]["cleanup_results"]
    ] == ["passed"]


def test_engine_skips_cleanup_for_resources_and_ui_that_were_never_created() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    executors = {
        channel: FakeExecutor(
            channel,
            calls,
            fail_step="load-fixture" if channel == "fixture" else None,
        )
        for channel in ("fixture", "http", "sql", "ui")
    }
    plan = _plan()
    flow = plan["generation_flows"][0]
    flow["cleanup_steps"].insert(
        0,
        _step(
            "reset-ui",
            1,
            "ui",
            {},
            [],
            [],
            [_assertion("ui-reset", "ui", "status", "equals", "")],
            screen_ref="expense-list",
            ui_action_ref="reset-status",
        ),
    )
    flow["cleanup_steps"][1]["sequence"] = 2

    result = _engine(executors).execute(plan=plan, request=_request())

    assert result["status"] == "failed"
    assert result["cleanup_status"] == "passed"
    assert calls == [("setup", "load-fixture", {})]
    cleanup = result["flow_results"][0]["cleanup_results"]
    assert [step["status"] for step in cleanup] == ["not_run", "not_run"]
    assert all("failure_reason" not in step for step in cleanup)


def test_engine_cleans_created_data_when_an_adapter_raises_an_unexpected_error() -> None:
    class AdapterTimeout(Exception):
        pass

    class TimeoutExecutor(FakeExecutor):
        def execute(self, **values: object) -> DataStepExecution:
            step = values["step"]
            assert isinstance(step, Mapping)
            if step["step_id"] == "update-expense":
                self.calls.append(("setup", "update-expense", {}))
                raise AdapterTimeout("locator wait expired")
            return super().execute(**values)  # type: ignore[arg-type]

    calls: list[tuple[str, str, dict[str, object]]] = []
    executors: dict[str, Any] = {
        channel: FakeExecutor(channel, calls) for channel in ("fixture", "http", "sql")
    }
    executors["ui"] = TimeoutExecutor("ui", calls)

    result = _engine(executors).execute(plan=_plan(), request=_request())

    assert result["status"] == "failed"
    assert result["cleanup_status"] == "passed"
    assert calls[-1] == ("cleanup", "delete-expense", {"expense_id": 91})
    assert "AdapterTimeout: locator wait expired" in result["failure_reasons"][0]


def test_engine_blocks_when_a_required_channel_executor_is_missing() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    executors = {channel: FakeExecutor(channel, calls) for channel in ("fixture", "http", "sql")}
    result = _engine(executors).execute(plan=_plan(), request=_request())

    assert result["status"] == "blocked"
    assert "No executor is configured for channel ui" in result["failure_reasons"][0]
    assert result["flow_results"][0]["step_results"][3]["status"] == "not_run"


def test_engine_emits_sanitized_live_progress_including_cleanup() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    events: list[DataExecutionProgress] = []
    executors = {
        channel: FakeExecutor(channel, calls) for channel in ("fixture", "http", "sql", "ui")
    }
    values = iter(
        (
            datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
            datetime(2026, 7, 18, 12, 1, tzinfo=UTC),
        )
    )
    engine = DataExecutionEngine(
        contracts=ContractCatalog.load(ROOT / "contracts"),
        executors=executors,
        clock=lambda: next(values),
        progress_sink=events.append,
    )

    result = engine.execute(plan=_plan(), request=_request())

    assert result["status"] == "passed"
    assert [event.event_type for event in events] == [
        "run_started",
        "flow_started",
        "step_started",
        "step_completed",
        "step_started",
        "step_completed",
        "step_started",
        "step_completed",
        "step_started",
        "step_completed",
        "flow_completed",
        "step_started",
        "step_completed",
        "run_completed",
    ]
    cleanup = [event for event in events if event.phase == "cleanup"]
    assert [event.status for event in cleanup] == ["running", "passed"]
    assert all(event.message is None for event in events)


def test_interrupted_result_is_fail_closed_and_marks_cleanup_unconfirmed() -> None:
    engine = DataExecutionEngine(
        contracts=ContractCatalog.load(ROOT / "contracts"),
        executors={},
        clock=lambda: datetime(2026, 7, 18, 12, 5, tzinfo=UTC),
    )
    request = DataExecutionRequest(
        execution_result_id="result-interrupted",
        run_id="run-interrupted",
        project_id="visiondemo",
        started_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )

    result = engine.interrupted_result(
        plan=_plan(), request=request, reason="Worker heartbeat stopped."
    )

    assert result["status"] == "interrupted"
    assert result["cleanup_status"] == "interrupted"
    assert result["failure_reasons"] == ["Worker heartbeat stopped."]
    assert all(flow["status"] == "not_run" for flow in result["flow_results"])


def _engine(executors: dict[str, FakeExecutor]) -> DataExecutionEngine:
    values = iter(
        (
            datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
            datetime(2026, 7, 18, 12, 1, tzinfo=UTC),
        )
    )
    return DataExecutionEngine(
        contracts=ContractCatalog.load(ROOT / "contracts"),
        executors=executors,
        clock=lambda: next(values),
    )


def _request() -> DataExecutionRequest:
    return DataExecutionRequest(
        execution_result_id="result-cross-screen",
        run_id="run-cross-screen",
        project_id="visiondemo",
        base_url="http://127.0.0.1:8080",
    )


def _plan() -> dict[str, Any]:
    steps = [
        _step(
            "load-fixture",
            1,
            "fixture",
            {},
            [],
            [],
            [_assertion("fixture-loaded", "fixture", "loaded", "equals", True)],
            target="VisionDemo/src/main/resources/data.sql",
        ),
        _step(
            "create-expense",
            2,
            "http",
            {"expenseNo": "EXP-CROSS-001"},
            ["load-fixture"],
            [
                {
                    "variable": "expense_id",
                    "source": "response",
                    "path": "id",
                    "required": True,
                }
            ],
            [_assertion("expense-created", "response", "expenseNo", "equals", "EXP-CROSS-001")],
            target="POST /expense/api/save",
        ),
        _step(
            "update-expense",
            3,
            "ui",
            {"expense_id": "{{expense_id}}"},
            ["create-expense"],
            [],
            [_assertion("update-status", "ui", "status", "equals", "申請中")],
            screen_ref="expense-update",
            ui_action_ref="submit-expense",
        ),
        _step(
            "verify-database",
            4,
            "sql",
            {"expense_id": "{{expense_id}}"},
            ["update-expense"],
            [],
            [
                _assertion(
                    "database-status",
                    "database",
                    "expense.status",
                    "equals",
                    "申請中",
                )
            ],
            target="SELECT expense",
        ),
    ]
    cleanup = _step(
        "delete-expense",
        1,
        "http",
        {"expense_id": "{{expense_id}}"},
        ["create-expense"],
        [],
        [_assertion("expense-deleted", "response", "deleted", "equals", True)],
        target="DELETE /expense/api/{{expense_id}}",
    )
    return {
        "artifact_type": "TestDataPlan",
        "schema_version": "v1",
        "test_data_plan_id": "test-data-cross-screen",
        "test_plan_id": "test-plan-cross-screen",
        "project_id": "visiondemo",
        "status": "ready",
        "data_sets": [
            {
                "test_data_id": "cross-screen-expense",
                "test_case_refs": ["expense-flow"],
                "setup_actions": [],
                "cleanup_policy": "delete_after_run",
            }
        ],
        "generation_flows": [
            {
                "flow_id": "expense-cross-screen",
                "title": "Cross-screen expense flow",
                "test_data_refs": ["cross-screen-expense"],
                "test_case_refs": ["expense-flow"],
                "steps": steps,
                "final_assertions": [
                    _assertion("scenario-result", "test", "expense-flow", "satisfies", "passed")
                ],
                "cleanup_policy": "delete_after_run",
                "cleanup_steps": [cleanup],
            }
        ],
        "blocking_reasons": [],
    }


def _step(
    step_id: str,
    sequence: int,
    channel: str,
    inputs: dict[str, object],
    depends_on: list[str],
    output_bindings: list[dict[str, object]],
    postconditions: list[dict[str, object]],
    *,
    target: str | None = None,
    screen_ref: str | None = None,
    ui_action_ref: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "step_id": step_id,
        "sequence": sequence,
        "channel": channel,
        "business_action": step_id,
        "inputs": inputs,
        "depends_on": depends_on,
        "output_bindings": output_bindings,
        "postconditions": postconditions,
    }
    if target is not None:
        result["target"] = target
    if screen_ref is not None:
        result["screen_ref"] = screen_ref
    if ui_action_ref is not None:
        result["ui_action_ref"] = ui_action_ref
    return result


def _assertion(
    assertion_id: str,
    observe_via: str,
    subject: str,
    operator: str,
    expected: object,
) -> dict[str, object]:
    return {
        "assertion_id": assertion_id,
        "observe_via": observe_via,
        "subject": subject,
        "operator": operator,
        "expected": expected,
    }
