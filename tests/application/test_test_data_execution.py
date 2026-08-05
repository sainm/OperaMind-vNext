from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
    TestDataStepBlockedError as DataStepBlockedError,
)
from operamind.application.test_data_execution import (
    TestDataStepExecution as DataStepExecution,
)
from operamind.application.test_data_execution import (
    _add_v3_binding_refs,
    _assert_postcondition,
    _extract,
    _freeze_step_bindings,
    _is_identity_source_step,
    _resolve_variables,
    _TestDataBindingBlockedError,
    _validate_evidence_identity,
    _validate_v3_binding_references,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.test_data import default_data_identity_providers
from operamind.run_context_values import canonical_digest

ROOT = Path(__file__).parents[2]


def test_v3_binding_reference_validation_fails_closed_for_every_scope_edge() -> None:
    payload = {
        "binding_id": "binding-1",
        "run_id": "run-1",
        "project_id": "project-1",
        "test_data_id": "expense-1",
    }
    binding = {**payload, "content_digest": canonical_digest(payload), "evidence_ref": "e-1"}
    artifact: dict[str, Any] = {
        "run_id": "run-1",
        "project_id": "project-1",
        "data_bindings": [binding],
        "flow_results": [
            {
                "test_data_binding_refs": ["binding-1"],
                "step_results": [{"test_data_binding_refs": ["binding-1"]}],
                "cleanup_results": [],
            }
        ],
        "evidence": [
            {
                "evidence_type": "screenshot",
                "test_data_binding_ref": "binding-1",
            }
        ],
        "run_context": {
            "runtime_variables": {"operamind_run_id": "run-1"},
            "frozen_data_bindings": [binding],
        },
    }

    _add_v3_binding_refs(cast(list[dict[str, Any]], artifact["flow_results"]))
    _validate_v3_binding_references(artifact)

    invalid = deepcopy(artifact)
    invalid["data_bindings"] = [binding, binding]
    with pytest.raises(ValueError, match="IDs must be unique"):
        _validate_v3_binding_references(invalid)
    invalid = deepcopy(artifact)
    invalid["data_bindings"][0]["project_id"] = "foreign-project"
    with pytest.raises(ValueError, match="scope differs"):
        _validate_v3_binding_references(invalid)
    invalid = deepcopy(artifact)
    invalid["data_bindings"][0]["content_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest differs"):
        _validate_v3_binding_references(invalid)
    invalid = deepcopy(artifact)
    invalid["flow_results"][0]["step_results"][0]["test_data_binding_refs"] = ["foreign-binding"]
    with pytest.raises(ValueError, match="StepResult references"):
        _validate_v3_binding_references(invalid)
    invalid = deepcopy(artifact)
    invalid["flow_results"][0]["test_data_binding_refs"] = []
    with pytest.raises(ValueError, match="FlowResult"):
        _validate_v3_binding_references(invalid)
    invalid = deepcopy(artifact)
    invalid["evidence"][0]["test_data_binding_ref"] = "foreign-binding"
    with pytest.raises(ValueError, match="Evidence references"):
        _validate_v3_binding_references(invalid)
    invalid = deepcopy(artifact)
    invalid["evidence"][0].pop("test_data_binding_ref")
    with pytest.raises(ValueError, match="Screenshot Evidence"):
        _validate_v3_binding_references(invalid)
    blocked_screen_failure = deepcopy(artifact)
    blocked_screen_failure["data_bindings"] = []
    blocked_screen_failure["flow_results"] = [
        {
            "test_data_binding_refs": [],
            "step_results": [
                {
                    "channel": "ui",
                    "status": "blocked",
                    "failure_stage": "pre_action_locator_validation",
                    "evidence_refs": ["e-screen"],
                    "test_data_binding_refs": [],
                }
            ],
            "cleanup_results": [],
        }
    ]
    blocked_screen_failure["evidence"] = [
        {"evidence_type": "screenshot", "evidence_ref": "e-screen"}
    ]
    blocked_screen_failure["run_context"]["frozen_data_bindings"] = []
    _validate_v3_binding_references(blocked_screen_failure)
    blocked_screen_failure["flow_results"][0]["step_results"][0]["status"] = "passed"
    with pytest.raises(ValueError, match="Screenshot Evidence"):
        _validate_v3_binding_references(blocked_screen_failure)
    invalid = deepcopy(artifact)
    invalid["run_context"]["runtime_variables"]["operamind_run_id"] = "foreign-run"
    with pytest.raises(ValueError, match="RunContext Run identity"):
        _validate_v3_binding_references(invalid)
    invalid = deepcopy(artifact)
    invalid["run_context"]["frozen_data_bindings"] = []
    with pytest.raises(ValueError, match="RunContext frozen bindings"):
        _validate_v3_binding_references(invalid)


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


def test_postcondition_edge_cases_fail_closed() -> None:
    _assert_postcondition(
        {
            "assertion_id": "deferred",
            "observe_via": "test",
            "subject": "case-1",
            "operator": "satisfies",
            "expected": "passed",
        },
        {},
        {},
    )
    with pytest.raises(AssertionError, match="expected existence"):
        _assert_postcondition(
            {
                "assertion_id": "required",
                "observe_via": "database",
                "subject": "row",
                "operator": "exists",
                "expected": True,
            },
            {"database": {}},
            {},
        )
    contains = {
        "assertion_id": "contains",
        "observe_via": "database",
        "subject": "value",
        "operator": "contains",
        "expected": "expected",
    }
    with pytest.raises(AssertionError, match="does not support contains"):
        _assert_postcondition(contains, {"database": {"value": 41}}, {})
    with pytest.raises(AssertionError, match="did not contain"):
        _assert_postcondition(contains, {"database": {"value": "other"}}, {})
    with pytest.raises(AssertionError, match="does not have a count"):
        _assert_postcondition(
            {
                "assertion_id": "count",
                "observe_via": "database",
                "subject": "value",
                "operator": "count_equals",
                "expected": 1,
            },
            {"database": {"value": None}},
            {},
        )
    _assert_postcondition(
        {
            "assertion_id": "literal-satisfies",
            "observe_via": "database",
            "subject": "state",
            "operator": "satisfies",
            "expected": "ready",
        },
        {"database": {"state": "ready"}},
        {},
    )
    with pytest.raises(ValueError, match="clock must return timezone-aware"):
        DataExecutionEngine._iso(datetime(2026, 8, 4))


def test_identity_binding_freezes_real_readback_into_run_scoped_evidence() -> None:
    plan = {"data_sets": [_identity_data_set()]}

    bindings, evidence = _freeze_step_bindings(
        plan=plan,
        request=_request(),
        flow_id="identity-flow",
        step_id="read-expense",
        observations={
            "database": {
                "row_count": 1,
                "rows": [{"id": 41, "expense_number": "EXP-041"}],
            }
        },
        variables={},
        frozen_bindings={},
        clock=lambda: datetime(2026, 8, 4, tzinfo=UTC),
        source_evidence=_sql_identity_evidence(),
        identity_providers=default_data_identity_providers(),
    )

    binding = bindings[0]
    assert binding["run_id"] == "run-cross-screen"
    assert binding["primary_key"] == {"name": "id", "value": 41}
    assert binding["business_unique_keys"] == [{"name": "expense_number", "value": "EXP-041"}]
    assert binding["identity_provider_type"] == "database"
    assert binding["identity_provider_ref"] == "database.v1"
    assert binding["screen_identity_values"] == [{"name": "expense_number", "value": "EXP-041"}]
    assert binding["record_scope_locator"] == {
        "by": "css",
        "value": "[data-expense-number='EXP-041']",
        "exact": True,
    }
    assert binding["screen_locator"] == {
        "by": "css",
        "value": "[data-expense-number='EXP-041']",
        "exact": True,
    }
    assert binding["identity_observations"] == {
        "business_unique_keys": [
            {
                "name": "expense_number",
                "kind": "attribute",
                "attribute_name": "data-observed-expense-number",
            }
        ],
        "screen_key": {
            "name": "expense_number",
            "kind": "attribute",
            "attribute_name": "data-observed-expense-number",
        },
        "screen_identity_values": [
            {
                "name": "expense_number",
                "kind": "attribute",
                "attribute_name": "data-observed-expense-number",
            }
        ],
    }
    expected_identity = {
        "business_unique_keys": [{"name": "expense_number", "value": "EXP-041"}],
        "screen_identity_values": [{"name": "expense_number", "value": "EXP-041"}],
    }
    assert (
        binding["identity_digest"]
        == hashlib.sha256(
            json.dumps(
                expected_identity,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    assert binding["identity_digest"] != binding["content_digest"]
    assert evidence[0].evidence_type == "data_binding"
    assert evidence[0].content_digest == binding["content_digest"]
    assert _is_identity_source_step(
        plan=plan,
        flow_id="identity-flow",
        step_id="read-expense",
    )


@pytest.mark.parametrize(
    ("database", "message"),
    [
        ({"row_count": 0, "rows": []}, "exactly one record"),
        (
            {"row_count": 1, "rows": [{"id": 41}]},
            "identity source was not observed",
        ),
        (
            {
                "row_count": 1,
                "rows": [{"id": [41], "expense_number": "EXP-041"}],
            },
            "identity value must be scalar",
        ),
        (
            {"row_count": 1, "rows": [{"id": 41, "expense_number": " "}]},
            "identity value must not be blank",
        ),
    ],
)
def test_identity_binding_blocks_incomplete_or_non_unique_readback(
    database: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(_TestDataBindingBlockedError, match=message):
        _freeze_step_bindings(
            plan={"data_sets": [_identity_data_set()]},
            request=_request(),
            flow_id="identity-flow",
            step_id="read-expense",
            observations={"database": database},
            variables={},
            frozen_bindings={},
            clock=lambda: datetime(2026, 8, 4, tzinfo=UTC),
            source_evidence=_sql_identity_evidence(),
            identity_providers=default_data_identity_providers(),
        )


def test_identity_binding_blocks_when_provider_ref_is_not_registered() -> None:
    with pytest.raises(_TestDataBindingBlockedError, match="is not configured"):
        _freeze_step_bindings(
            plan={"data_sets": [_identity_data_set()]},
            request=_request(),
            flow_id="identity-flow",
            step_id="read-expense",
            observations={
                "database": {
                    "row_count": 1,
                    "rows": [{"id": 41, "expense_number": "EXP-041"}],
                }
            },
            variables={},
            frozen_bindings={},
            clock=lambda: datetime(2026, 8, 4, tzinfo=UTC),
            source_evidence=_sql_identity_evidence(),
            identity_providers={},
        )


def test_v2_engine_blocks_before_steps_when_no_real_provider_registry_is_configured() -> None:
    plan = json.loads(
        (ROOT / "contracts/examples/test-data-plan.v2.example.json").read_text(encoding="utf-8")
    )
    result = DataExecutionEngine(
        contracts=ContractCatalog.load(ROOT / "contracts"),
        executors={},
        identity_providers={},
        clock=lambda: datetime(2026, 8, 4, tzinfo=UTC),
    ).execute(
        plan=plan,
        request=DataExecutionRequest(
            execution_result_id="result-no-provider",
            run_id="run-no-provider",
            project_id=str(plan["project_id"]),
            started_at=datetime(2026, 8, 4, tzinfo=UTC),
        ),
    )

    assert result["status"] == "blocked"
    assert any(
        "DataIdentityProvider is not configured" in reason for reason in result["failure_reasons"]
    )
    assert all(flow["status"] == "not_run" for flow in result["flow_results"])


def test_identity_binding_rejects_clock_replay_and_unsafe_locator_values() -> None:
    plan = {"data_sets": [_identity_data_set()]}
    observations = {
        "database": {
            "row_count": 1,
            "rows": [{"id": 41, "expense_number": "EXP-041"}],
        }
    }
    request = _request()

    with pytest.raises(_TestDataBindingBlockedError, match="clock must be timezone-aware"):
        _freeze_step_bindings(
            plan=plan,
            request=request,
            flow_id="identity-flow",
            step_id="read-expense",
            observations=observations,
            variables={},
            frozen_bindings={},
            clock=lambda: datetime(2026, 8, 4),
            source_evidence=_sql_identity_evidence(),
            identity_providers=default_data_identity_providers(),
        )
    bindings, _ = _freeze_step_bindings(
        plan=plan,
        request=request,
        flow_id="identity-flow",
        step_id="read-expense",
        observations=observations,
        variables={},
        frozen_bindings={},
        clock=lambda: datetime(2026, 8, 4, tzinfo=UTC),
        source_evidence=_sql_identity_evidence(),
        identity_providers=default_data_identity_providers(),
    )
    with pytest.raises(_TestDataBindingBlockedError, match="already frozen"):
        _freeze_step_bindings(
            plan=plan,
            request=request,
            flow_id="identity-flow",
            step_id="read-expense",
            observations=observations,
            variables={},
            frozen_bindings={"expense-bound": bindings[0]},
            clock=lambda: datetime(2026, 8, 4, tzinfo=UTC),
            source_evidence=_sql_identity_evidence(),
            identity_providers=default_data_identity_providers(),
        )


def test_execution_boundary_values_fail_closed_without_adapter_calls() -> None:
    with pytest.raises(ValueError, match="event type"):
        DataExecutionProgress(" ")
    with pytest.raises(ValueError, match="phase is invalid"):
        DataExecutionProgress("step", phase="verify")
    with pytest.raises(ValueError, match="requires flow and phase"):
        DataExecutionProgress("step", step_id="step-1")
    with pytest.raises(ValueError, match="identity must not be blank"):
        DataExecutionEvidence(" ", "flow", "step", "setup", "sql", "ref", "a" * 64)
    with pytest.raises(ValueError, match="phase is invalid"):
        DataExecutionEvidence("id", "flow", "step", "verify", "sql", "ref", "a" * 64)
    with pytest.raises(ValueError, match="ref/digest"):
        DataExecutionEvidence("id", "flow", "step", "setup", "sql", "", "invalid")
    with pytest.raises(ValueError, match="must be sanitized"):
        DataExecutionEvidence(
            "id", "flow", "step", "setup", "sql", "ref", "a" * 64, sanitized=False
        )
    with pytest.raises(ValueError, match="identity must not be blank"):
        DataExecutionRequest("result", " ", "project")
    with pytest.raises(ValueError, match="include a timezone"):
        DataExecutionRequest("result", "run", "project", started_at=datetime(2026, 8, 4))


def test_variable_and_evidence_identity_helpers_reject_ambiguous_runtime_state() -> None:
    assert _resolve_variables(
        {"ids": ["{{expense_id}}"], "label": "expense-{{expense_id}}"},
        {"expense_id": 41},
    ) == {"ids": [41], "label": "expense-41"}
    with pytest.raises(DataStepBlockedError, match="Variable missing is not available"):
        _resolve_variables("{{missing}}", {})
    with pytest.raises(DataStepBlockedError, match="Variables are not available"):
        _resolve_variables("expense-{{missing}}", {})
    assert _extract({"value": 1}, "$") == (True, {"value": 1})
    assert _extract(["first", "second"], "1") == (True, "second")
    assert _extract(["first", "second"], "[1]") == (False, None)
    assert _extract({}, "broken[abc]") == (False, None)
    assert _extract({}, "a..b") == (False, None)

    evidence = DataExecutionEvidence(
        "duplicate",
        "flow",
        "step",
        "setup",
        "sql",
        "evidence://duplicate",
        "a" * 64,
    )
    with pytest.raises(ValueError, match="must be unique"):
        _validate_evidence_identity([evidence, evidence])


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
    assert [step["status"] for step in result["flow_results"][0]["cleanup_results"]] == ["passed"]


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


def test_engine_preserves_structured_pre_action_failure_trace_on_ui_step() -> None:
    class BlockingUiExecutor(FakeExecutor):
        def execute(self, **values: object) -> DataStepExecution:
            raise DataStepBlockedError(
                "Playwright pre-action observation did not match",
                trace={
                    "failure_stage": "pre_action_state_validation",
                    "driver": "playwright",
                    "locator_type": "role+name",
                    "record_scope_match_count": 1,
                    "action_locator_match_count": 0,
                },
            )

    calls: list[tuple[str, str, dict[str, object]]] = []
    executors: dict[str, Any] = {
        channel: FakeExecutor(channel, calls) for channel in ("fixture", "http", "sql")
    }
    executors["ui"] = BlockingUiExecutor("ui", calls)

    result = _engine(executors).execute(plan=_plan(), request=_request())

    step = result["flow_results"][0]["step_results"][2]
    assert step["status"] == "blocked"
    assert step["failure_stage"] == "pre_action_state_validation"
    assert step["driver"] == "playwright"
    assert step["locator_type"] == "role+name"
    assert step["record_scope_match_count"] == 1
    assert step["action_locator_match_count"] == 0


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


def _identity_data_set() -> dict[str, object]:
    return {
        "test_data_id": "expense-bound",
        "identity_binding": {
            "provider": {"type": "database", "provider_ref": "database.v1"},
            "binding_mode": "generated",
            "source_flow_id": "identity-flow",
            "source_step_id": "read-expense",
            "primary_key": {
                "name": "id",
                "source": "database",
                "path": "rows[0].id",
            },
            "business_unique_keys": [
                {
                    "name": "expense_number",
                    "source": "database",
                    "path": "rows[0].expense_number",
                    "dom_observation": {
                        "kind": "attribute",
                        "attribute_name": "data-observed-expense-number",
                    },
                }
            ],
            "screen_key": {
                "name": "expense_number",
                "source": "database",
                "path": "rows[0].expense_number",
                "dom_observation": {
                    "kind": "attribute",
                    "attribute_name": "data-observed-expense-number",
                },
                "locator_template": {
                    "by": "css",
                    "value": "[data-expense-number='{{value}}']",
                    "exact": True,
                },
            },
            "match_count": {"source": "database", "path": "row_count"},
        },
    }


def _request() -> DataExecutionRequest:
    return DataExecutionRequest(
        execution_result_id="result-cross-screen",
        run_id="run-cross-screen",
        project_id="visiondemo",
        base_url="http://127.0.0.1:8080",
    )


def _sql_identity_evidence() -> tuple[DataExecutionEvidence, ...]:
    return (
        DataExecutionEvidence(
            evidence_id="identity-sql-evidence",
            flow_id="identity-flow",
            step_id="read-expense",
            phase="setup",
            evidence_type="sql",
            evidence_ref="artifact://result-cross-screen/sql/read-expense",
            content_digest="a" * 64,
        ),
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
