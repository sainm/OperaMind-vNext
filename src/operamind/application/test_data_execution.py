"""Ordered, fail-closed execution of TestDataPlan generation flows."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from operamind.contracts import ContractCatalog

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TestDataStepBlockedError(RuntimeError):
    """Raised when a required channel binding or environment is unavailable."""


@dataclass(frozen=True, slots=True)
class TestDataExecutionProgress:
    """One sanitized progress transition; variable values are never included."""

    event_type: str
    flow_id: str | None = None
    phase: str | None = None
    step_id: str | None = None
    status: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ValueError("Test data progress event type must not be blank")
        if self.phase is not None and self.phase not in {"setup", "cleanup"}:
            raise ValueError("Test data progress phase is invalid")
        if self.step_id is not None and (self.flow_id is None or self.phase is None):
            raise ValueError("Test data step progress requires flow and phase")


class _TestDataStepFailedError(RuntimeError):
    """Retain sanitized adapter evidence when validation fails after execution."""

    def __init__(
        self,
        message: str,
        evidence: tuple[TestDataExecutionEvidence, ...],
    ) -> None:
        super().__init__(message)
        self.evidence = evidence


@dataclass(frozen=True, slots=True)
class TestDataExecutionEvidence:
    evidence_id: str
    flow_id: str
    step_id: str
    phase: str
    evidence_type: str
    evidence_ref: str
    content_digest: str
    sanitized: bool = True

    def __post_init__(self) -> None:
        values = (
            self.evidence_id,
            self.flow_id,
            self.step_id,
            self.evidence_type,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Test data Evidence identity must not be blank")
        if self.phase not in {"setup", "cleanup"}:
            raise ValueError("Test data Evidence phase is invalid")
        if not self.evidence_ref.strip() or _SHA256.fullmatch(self.content_digest) is None:
            raise ValueError("Test data Evidence ref/digest is invalid")
        if not self.sanitized:
            raise ValueError("Test data Evidence must be sanitized")

    def to_artifact(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "flow_id": self.flow_id,
            "step_id": self.step_id,
            "phase": self.phase,
            "evidence_type": self.evidence_type,
            "evidence_ref": self.evidence_ref,
            "content_digest": self.content_digest,
            "sanitized": True,
        }


@dataclass(frozen=True, slots=True)
class TestDataStepExecution:
    source_values: Mapping[str, object]
    evidence: tuple[TestDataExecutionEvidence, ...]
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TestDataExecutionRequest:
    execution_result_id: str
    run_id: str
    project_id: str
    base_url: str | None = None
    started_at: datetime | None = None

    def __post_init__(self) -> None:
        if any(
            not value.strip() for value in (self.execution_result_id, self.run_id, self.project_id)
        ):
            raise ValueError("Test data execution identity must not be blank")
        if self.started_at is not None and self.started_at.utcoffset() is None:
            raise ValueError("Test data execution started_at must include a timezone")


class TestDataChannelExecutor(Protocol):
    def execute(
        self,
        *,
        request: TestDataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> TestDataStepExecution: ...


class TestDataExecutionEngine:
    """Execute reviewed generation steps without evaluating arbitrary code."""

    def __init__(
        self,
        *,
        contracts: ContractCatalog,
        executors: Mapping[str, TestDataChannelExecutor],
        clock: Callable[[], datetime] | None = None,
        progress_sink: Callable[[TestDataExecutionProgress], None] | None = None,
    ) -> None:
        self._contracts = contracts
        self._executors = dict(executors)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._progress_sink = progress_sink

    def execute(
        self,
        *,
        plan: dict[str, Any],
        request: TestDataExecutionRequest,
    ) -> dict[str, Any]:
        self._contracts.validate_artifact(plan)
        if plan.get("artifact_type") != "TestDataPlan":
            raise ValueError("Test data execution requires a TestDataPlan Artifact")
        if plan["project_id"] != request.project_id:
            raise ValueError("TestDataPlan Project does not match the execution request")
        started = self._iso(request.started_at or self._clock())
        flows = cast(list[dict[str, Any]], plan["generation_flows"])
        self._emit("run_started", status="running")
        if plan["status"] != "ready":
            blocked_artifact = self._blocked_plan_result(plan, request, started, flows)
            self._contracts.validate_artifact(blocked_artifact)
            self._emit("run_completed", status="blocked")
            return blocked_artifact

        flow_results: list[dict[str, Any]] = []
        all_evidence: list[TestDataExecutionEvidence] = []
        failure_reasons: list[str] = []
        executed_flows: list[tuple[dict[str, Any], dict[str, object]]] = []
        stopped = False
        for flow in flows:
            if stopped:
                flow_results.append(_not_run_flow(flow))
                continue
            flow_id = str(flow["flow_id"])
            self._emit("flow_started", flow_id=flow_id, status="running")
            variables: dict[str, object] = {}
            observations: dict[str, object] = {}
            step_results: list[dict[str, Any]] = []
            steps = cast(list[dict[str, Any]], flow["steps"])
            flow_status = "passed"
            for index, step in enumerate(steps):
                step_id = str(step["step_id"])
                self._emit(
                    "step_started",
                    flow_id=flow_id,
                    phase="setup",
                    step_id=step_id,
                    status="running",
                )
                try:
                    step_result, observed, evidence = self._execute_step(
                        request=request,
                        flow=flow,
                        step=step,
                        variables=variables,
                        phase="setup",
                    )
                    observations.update(observed)
                    step_results.append(step_result)
                    all_evidence.extend(evidence)
                    self._emit(
                        "step_completed",
                        flow_id=flow_id,
                        phase="setup",
                        step_id=step_id,
                        status="passed",
                    )
                except TestDataStepBlockedError as error:
                    reason = f"{flow['flow_id']}/{step['step_id']}: {error}"
                    step_results.append(_failed_step(step, "setup", "blocked", reason))
                    failure_reasons.append(reason)
                    flow_status = "blocked"
                    stopped = True
                    self._emit(
                        "step_completed",
                        flow_id=flow_id,
                        phase="setup",
                        step_id=step_id,
                        status="blocked",
                        message="Step execution was blocked.",
                    )
                except _TestDataStepFailedError as error:
                    reason = f"{flow['flow_id']}/{step['step_id']}: {error}"
                    all_evidence.extend(error.evidence)
                    step_results.append(
                        _failed_step(
                            step,
                            "setup",
                            "failed",
                            reason,
                            evidence_refs=tuple(value.evidence_ref for value in error.evidence),
                        )
                    )
                    failure_reasons.append(reason)
                    flow_status = "failed"
                    stopped = True
                    self._emit(
                        "step_completed",
                        flow_id=flow_id,
                        phase="setup",
                        step_id=step_id,
                        status="failed",
                        message="Step execution failed.",
                    )
                except (AssertionError, OSError, RuntimeError, ValueError) as error:
                    reason = f"{flow['flow_id']}/{step['step_id']}: {error}"
                    step_results.append(_failed_step(step, "setup", "failed", reason))
                    failure_reasons.append(reason)
                    flow_status = "failed"
                    stopped = True
                    self._emit(
                        "step_completed",
                        flow_id=flow_id,
                        phase="setup",
                        step_id=step_id,
                        status="failed",
                        message="Step execution failed.",
                    )
                if stopped:
                    step_results.extend(_not_run_step(item) for item in steps[index + 1 :])
                    break
            deferred = [
                str(assertion["assertion_id"])
                for assertion in cast(list[dict[str, Any]], flow["final_assertions"])
                if assertion["observe_via"] == "test"
            ]
            if not stopped:
                try:
                    for assertion in cast(list[dict[str, Any]], flow["final_assertions"]):
                        if assertion["observe_via"] != "test":
                            _assert_postcondition(assertion, observations, variables)
                except (AssertionError, ValueError) as error:
                    reason = f"{flow['flow_id']}: final assertion failed: {error}"
                    failure_reasons.append(reason)
                    flow_status = "failed"
                    stopped = True
            flow_results.append(
                {
                    "flow_id": flow["flow_id"],
                    "status": flow_status,
                    "step_results": step_results,
                    "cleanup_results": [],
                    "deferred_assertion_ids": deferred,
                }
            )
            self._emit("flow_completed", flow_id=flow_id, status=flow_status)
            executed_flows.append((flow, variables))

        cleanup_failed = False
        cleanup_required = stopped or any(
            flow["cleanup_policy"] == "delete_after_run" for flow, _ in executed_flows
        )
        if cleanup_required:
            results_by_flow = {str(value["flow_id"]): value for value in flow_results}
            for flow, variables in reversed(executed_flows):
                if not stopped and flow["cleanup_policy"] != "delete_after_run":
                    continue
                cleanup_results = cast(
                    list[dict[str, Any]], results_by_flow[str(flow["flow_id"])]["cleanup_results"]
                )
                for step in cast(list[dict[str, Any]], flow["cleanup_steps"]):
                    flow_id = str(flow["flow_id"])
                    step_id = str(step["step_id"])
                    self._emit(
                        "step_started",
                        flow_id=flow_id,
                        phase="cleanup",
                        step_id=step_id,
                        status="running",
                    )
                    try:
                        result, _observed, evidence = self._execute_step(
                            request=request,
                            flow=flow,
                            step=step,
                            variables=variables,
                            phase="cleanup",
                        )
                        cleanup_results.append(result)
                        all_evidence.extend(evidence)
                        self._emit(
                            "step_completed",
                            flow_id=flow_id,
                            phase="cleanup",
                            step_id=step_id,
                            status="passed",
                        )
                    except _TestDataStepFailedError as error:
                        reason = f"{flow['flow_id']}/{step['step_id']} cleanup: {error}"
                        all_evidence.extend(error.evidence)
                        cleanup_results.append(
                            _failed_step(
                                step,
                                "cleanup",
                                "failed",
                                reason,
                                evidence_refs=tuple(value.evidence_ref for value in error.evidence),
                            )
                        )
                        failure_reasons.append(reason)
                        cleanup_failed = True
                        self._emit(
                            "step_completed",
                            flow_id=flow_id,
                            phase="cleanup",
                            step_id=step_id,
                            status="failed",
                            message="Cleanup step failed.",
                        )
                    except (AssertionError, OSError, RuntimeError, ValueError) as error:
                        reason = f"{flow['flow_id']}/{step['step_id']} cleanup: {error}"
                        cleanup_results.append(_failed_step(step, "cleanup", "failed", reason))
                        failure_reasons.append(reason)
                        cleanup_failed = True
                        self._emit(
                            "step_completed",
                            flow_id=flow_id,
                            phase="cleanup",
                            step_id=step_id,
                            status="failed",
                            message="Cleanup step failed.",
                        )

        status = "passed"
        if failure_reasons:
            status = (
                "blocked"
                if any(value["status"] == "blocked" for value in flow_results)
                else "failed"
            )
        artifact: dict[str, Any] = {
            "artifact_type": "TestDataExecutionResult",
            "schema_version": "v1",
            "execution_result_id": request.execution_result_id,
            "run_id": request.run_id,
            "test_data_plan_id": plan["test_data_plan_id"],
            "project_id": request.project_id,
            "status": status,
            "started_at": started,
            "completed_at": self._iso(self._clock()),
            "flow_results": flow_results,
            "evidence": [value.to_artifact() for value in all_evidence],
            "failure_reasons": sorted(set(failure_reasons)),
            "cleanup_status": (
                "failed" if cleanup_failed else "passed" if cleanup_required else "not_required"
            ),
        }
        _validate_evidence_identity(all_evidence)
        self._contracts.validate_artifact(artifact)
        self._emit("run_completed", status=status)
        return artifact

    def interrupted_result(
        self,
        *,
        plan: dict[str, Any],
        request: TestDataExecutionRequest,
        reason: str,
    ) -> dict[str, Any]:
        """Create a fail-closed result for an explicitly recovered stale Run."""
        if not reason.strip():
            raise ValueError("Test data recovery reason must not be blank")
        self._contracts.validate_artifact(plan)
        artifact: dict[str, Any] = {
            "artifact_type": "TestDataExecutionResult",
            "schema_version": "v1",
            "execution_result_id": request.execution_result_id,
            "run_id": request.run_id,
            "test_data_plan_id": plan["test_data_plan_id"],
            "project_id": request.project_id,
            "status": "interrupted",
            "started_at": self._iso(request.started_at or self._clock()),
            "completed_at": self._iso(self._clock()),
            "flow_results": [
                _not_run_flow(flow) for flow in cast(list[dict[str, Any]], plan["generation_flows"])
            ],
            "evidence": [],
            "failure_reasons": [reason],
            "cleanup_status": "interrupted",
        }
        self._contracts.validate_artifact(artifact)
        return artifact

    def failed_result(
        self,
        *,
        plan: dict[str, Any],
        request: TestDataExecutionRequest,
        reason: str,
    ) -> dict[str, Any]:
        """Create a persisted failure when a worker crashes outside the engine loop."""
        if not reason.strip():
            raise ValueError("Test data failure reason must not be blank")
        self._contracts.validate_artifact(plan)
        artifact: dict[str, Any] = {
            "artifact_type": "TestDataExecutionResult",
            "schema_version": "v1",
            "execution_result_id": request.execution_result_id,
            "run_id": request.run_id,
            "test_data_plan_id": plan["test_data_plan_id"],
            "project_id": request.project_id,
            "status": "failed",
            "started_at": self._iso(request.started_at or self._clock()),
            "completed_at": self._iso(self._clock()),
            "flow_results": [
                _not_run_flow(flow) for flow in cast(list[dict[str, Any]], plan["generation_flows"])
            ],
            "evidence": [],
            "failure_reasons": [reason],
            "cleanup_status": "failed",
        }
        self._contracts.validate_artifact(artifact)
        return artifact

    def _emit(
        self,
        event_type: str,
        *,
        flow_id: str | None = None,
        phase: str | None = None,
        step_id: str | None = None,
        status: str | None = None,
        message: str | None = None,
    ) -> None:
        if self._progress_sink is not None:
            self._progress_sink(
                TestDataExecutionProgress(
                    event_type=event_type,
                    flow_id=flow_id,
                    phase=phase,
                    step_id=step_id,
                    status=status,
                    message=message,
                )
            )

    def _execute_step(
        self,
        *,
        request: TestDataExecutionRequest,
        flow: dict[str, Any],
        step: dict[str, Any],
        variables: dict[str, object],
        phase: str,
    ) -> tuple[
        dict[str, Any],
        dict[str, object],
        tuple[TestDataExecutionEvidence, ...],
    ]:
        channel = str(step["channel"])
        executor = self._executors.get(channel)
        if executor is None:
            raise TestDataStepBlockedError(f"No executor is configured for channel {channel}")
        resolved = cast(dict[str, object], _resolve_variables(step["inputs"], variables))
        resolved_step = dict(step)
        if "target" in resolved_step:
            resolved_step["target"] = _resolve_variables(resolved_step["target"], variables)
        execution = executor.execute(
            request=request,
            flow_id=str(flow["flow_id"]),
            step=resolved_step,
            resolved_inputs=resolved,
            variables=variables,
            phase=phase,
        )
        observations = dict(execution.source_values)
        output_names: list[str] = []
        try:
            if execution.failure_reason is not None:
                raise AssertionError(execution.failure_reason)
            for binding in cast(list[dict[str, Any]], step["output_bindings"]):
                source_name = str(binding["source"])
                source = observations.get(source_name)
                exists, output = _extract(source, str(binding["path"]))
                if not exists and binding["required"]:
                    raise AssertionError(
                        f"required output {binding['variable']} was not found at "
                        f"{source_name}.{binding['path']}"
                    )
                if exists:
                    variable = str(binding["variable"])
                    variables[variable] = output
                    output_names.append(variable)
            for assertion in cast(list[dict[str, Any]], step["postconditions"]):
                _assert_postcondition(assertion, observations, variables)
        except (AssertionError, ValueError) as error:
            raise _TestDataStepFailedError(str(error), execution.evidence) from error
        evidence_refs = [value.evidence_ref for value in execution.evidence]
        return (
            {
                "step_id": step["step_id"],
                "sequence": step["sequence"],
                "channel": channel,
                "phase": phase,
                "status": "passed",
                "output_variables": sorted(output_names),
                "evidence_refs": evidence_refs,
            },
            observations,
            execution.evidence,
        )

    def _blocked_plan_result(
        self,
        plan: dict[str, Any],
        request: TestDataExecutionRequest,
        started: str,
        flows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        reasons = [
            f"TestDataPlan is blocked: {value}"
            for value in cast(list[str], plan["blocking_reasons"])
        ]
        return {
            "artifact_type": "TestDataExecutionResult",
            "schema_version": "v1",
            "execution_result_id": request.execution_result_id,
            "run_id": request.run_id,
            "test_data_plan_id": plan["test_data_plan_id"],
            "project_id": request.project_id,
            "status": "blocked",
            "started_at": started,
            "completed_at": self._iso(self._clock()),
            "flow_results": [_not_run_flow(flow) for flow in flows],
            "evidence": [],
            "failure_reasons": reasons or ["TestDataPlan is blocked"],
            "cleanup_status": "not_required",
        }

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.utcoffset() is None:
            raise ValueError("Test data execution clock must return timezone-aware values")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _assert_postcondition(
    assertion: Mapping[str, object],
    observations: Mapping[str, object],
    variables: Mapping[str, object],
) -> None:
    source_name = str(assertion["observe_via"])
    if source_name == "test":
        return
    source = observations.get(source_name)
    exists, actual = _extract(source, str(assertion["subject"]))
    expected = _resolve_variables(assertion.get("expected"), variables)
    operator = str(assertion["operator"])
    if operator == "exists":
        if bool(expected) != exists:
            raise AssertionError(f"{assertion['assertion_id']} expected existence {expected}")
    elif not exists:
        raise AssertionError(f"{assertion['assertion_id']} subject was not observed")
    elif operator == "equals" and actual != expected:
        raise AssertionError(f"{assertion['assertion_id']} expected {expected!r}, got {actual!r}")
    elif operator == "contains":
        try:
            contains = expected in actual  # type: ignore[operator]
        except TypeError as error:
            raise AssertionError(
                f"{assertion['assertion_id']} observed value does not support contains"
            ) from error
        if not contains:
            raise AssertionError(f"{assertion['assertion_id']} did not contain {expected!r}")
    elif operator == "count_equals":
        try:
            actual_count = len(actual)  # type: ignore[arg-type]
        except TypeError as error:
            raise AssertionError(
                f"{assertion['assertion_id']} observed value does not have a count"
            ) from error
        if actual_count != expected:
            raise AssertionError(f"{assertion['assertion_id']} count did not equal {expected!r}")
    elif operator == "satisfies" and actual != expected:
        raise AssertionError(f"{assertion['assertion_id']} did not satisfy {expected!r}")


def _resolve_variables(value: object, variables: Mapping[str, object]) -> object:
    if isinstance(value, str):
        exact = _VARIABLE.fullmatch(value)
        if exact is not None:
            name = exact.group(1)
            if name not in variables:
                raise TestDataStepBlockedError(f"Variable {name} is not available")
            return variables[name]
        missing = [name for name in _VARIABLE.findall(value) if name not in variables]
        if missing:
            raise TestDataStepBlockedError(f"Variables are not available: {sorted(set(missing))}")
        return _VARIABLE.sub(lambda match: str(variables[match.group(1)]), value)
    if isinstance(value, list):
        return [_resolve_variables(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_variables(item, variables) for key, item in value.items()}
    return value


def _extract(source: object, path: str) -> tuple[bool, object | None]:
    if path in {"", "$"}:
        return source is not None, source
    current = source
    for component in path.split("."):
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif isinstance(current, list) and component.isdigit() and int(component) < len(current):
            current = current[int(component)]
        else:
            return False, None
    return True, current


def _failed_step(
    step: Mapping[str, object],
    phase: str,
    status: str,
    reason: str,
    *,
    evidence_refs: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "step_id": step["step_id"],
        "sequence": step["sequence"],
        "channel": step["channel"],
        "phase": phase,
        "status": status,
        "output_variables": [],
        "evidence_refs": list(evidence_refs),
        "failure_reason": reason,
    }


def _not_run_step(step: Mapping[str, object]) -> dict[str, object]:
    return {
        "step_id": step["step_id"],
        "sequence": step["sequence"],
        "channel": step["channel"],
        "phase": "setup",
        "status": "not_run",
        "output_variables": [],
        "evidence_refs": [],
    }


def _not_run_flow(flow: Mapping[str, object]) -> dict[str, object]:
    return {
        "flow_id": flow["flow_id"],
        "status": "not_run",
        "step_results": [
            _not_run_step(step) for step in cast(list[dict[str, object]], flow["steps"])
        ],
        "cleanup_results": [],
        "deferred_assertion_ids": [
            str(value["assertion_id"])
            for value in cast(list[dict[str, object]], flow["final_assertions"])
            if value["observe_via"] == "test"
        ],
    }


def _validate_evidence_identity(evidence: list[TestDataExecutionEvidence]) -> None:
    ids = [value.evidence_id for value in evidence]
    refs = [value.evidence_ref for value in evidence]
    if len(ids) != len(set(ids)) or len(refs) != len(set(refs)):
        raise ValueError("Test data Evidence IDs and refs must be unique")
