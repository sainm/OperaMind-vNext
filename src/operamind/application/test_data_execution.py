"""Ordered, fail-closed execution of TestDataPlan generation flows."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from operamind.application.data_identity import (
    DataIdentityProvider,
    DataIdentityResolveRequest,
    DataIdentitySourceEvidence,
)
from operamind.application.run_context import (
    RunContext,
    flow_dependencies_from_plan,
)
from operamind.application.test_data_coverage import (
    conditions_for_step,
    evaluate_condition,
    summarize_data_coverage,
)
from operamind.application.test_data_flow import validate_test_data_plan_artifact
from operamind.contracts import ContractCatalog
from operamind.run_context_values import canonical_digest

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_JSON_ARRAY_INDEX = re.compile(r"\[(\d+)\]")
_NUMERIC_PREDICATE = re.compile(r"^\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")


class TestDataStepBlockedError(RuntimeError):
    """Raised when a required channel binding or environment is unavailable."""

    def __init__(
        self,
        message: str,
        evidence: tuple[TestDataExecutionEvidence, ...] = (),
        trace: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence = evidence
        self.trace = dict(trace or {})


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


class _TestDataBindingBlockedError(TestDataStepBlockedError):
    """Preserve source-step Evidence when deterministic identity binding is blocked."""

    def __init__(
        self,
        message: str,
        evidence: tuple[TestDataExecutionEvidence, ...],
    ) -> None:
        super().__init__(message)
        self.evidence = evidence


class _TestDataCoverageBlockedError(TestDataStepBlockedError):
    """Preserve real readback and computed coverage Evidence on a failed condition."""

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
    test_data_binding_ref: str | None = None

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
        if self.test_data_binding_ref is not None and not self.test_data_binding_ref.strip():
            raise ValueError("Test data Evidence Binding ref must not be blank")

    def to_artifact(self) -> dict[str, object]:
        artifact: dict[str, object] = {
            "evidence_id": self.evidence_id,
            "flow_id": self.flow_id,
            "step_id": self.step_id,
            "phase": self.phase,
            "evidence_type": self.evidence_type,
            "evidence_ref": self.evidence_ref,
            "content_digest": self.content_digest,
            "sanitized": True,
        }
        if self.test_data_binding_ref is not None:
            artifact["test_data_binding_ref"] = self.test_data_binding_ref
        return artifact


@dataclass(frozen=True, slots=True)
class TestDataStepExecution:
    source_values: Mapping[str, object]
    evidence: tuple[TestDataExecutionEvidence, ...]
    failure_reason: str | None = None
    trace: Mapping[str, object] = field(default_factory=dict)


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
        identity_providers: Mapping[str, DataIdentityProvider] | None = None,
        clock: Callable[[], datetime] | None = None,
        progress_sink: Callable[[TestDataExecutionProgress], None] | None = None,
    ) -> None:
        self._contracts = contracts
        self._executors = dict(executors)
        self._identity_providers = dict(identity_providers or {})
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
        started_at = request.started_at or self._clock()
        started = self._iso(started_at)
        flows = cast(list[dict[str, Any]], plan["generation_flows"])
        run_context = (
            RunContext(
                project_id=request.project_id,
                run_id=request.run_id,
                execution_started_at=started_at,
                flow_dependencies=flow_dependencies_from_plan(flows),
            )
            if plan.get("schema_version") == "v3"
            else None
        )
        if run_context is not None:
            flows_by_id = {str(flow["flow_id"]): flow for flow in flows}
            flows = [flows_by_id[flow_id] for flow_id in run_context.execution_order]
        self._emit("run_started", status="running")
        semantic_blockers = (
            validate_test_data_plan_artifact(
                plan,
                identity_provider_types={
                    provider_ref: provider.provider_type
                    for provider_ref, provider in self._identity_providers.items()
                },
            )
            if plan.get("schema_version") in {"v2", "v3"}
            else []
        )
        if semantic_blockers:
            blocked_artifact = self._blocked_plan_result(
                plan,
                request,
                started,
                flows,
                reasons=semantic_blockers,
                run_context=run_context,
            )
            self._contracts.validate_artifact(blocked_artifact)
            self._emit("run_completed", status="blocked")
            return blocked_artifact
        if plan["status"] != "ready":
            blocked_artifact = self._blocked_plan_result(
                plan, request, started, flows, run_context=run_context
            )
            self._contracts.validate_artifact(blocked_artifact)
            self._emit("run_completed", status="blocked")
            return blocked_artifact

        flow_results: list[dict[str, Any]] = []
        all_evidence: list[TestDataExecutionEvidence] = []
        frozen_bindings: dict[str, dict[str, Any]] = {}
        coverage_proofs: dict[str, dict[str, Any]] = {}
        failure_reasons: list[str] = []
        executed_flows: list[tuple[dict[str, Any], dict[str, object], set[str], set[str]]] = []
        stopped = False
        for flow in flows:
            if stopped:
                flow_results.append(_not_run_flow(flow))
                continue
            flow_id = str(flow["flow_id"])
            self._emit("flow_started", flow_id=flow_id, status="running")
            variables = (
                run_context.local_variables_for_flow(flow_id)
                if run_context is not None
                else {}
            )
            observations: dict[str, object] = {}
            cleanup_dependency_ids: set[str] = set()
            attempted_channels: set[str] = set()
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
                    attempted_channels.add(str(step["channel"]))
                    if (
                        plan.get("schema_version") in {"v2", "v3"}
                        and step.get("channel") == "ui"
                        and cast(list[object], step.get("test_step_refs", []))
                        and summarize_data_coverage(
                            plan=plan,
                            proofs=list(coverage_proofs.values()),
                        )["status"]
                        != "passed"
                    ):
                        raise TestDataStepBlockedError(
                            "Test Data Coverage must be 100% before a TestPlan UI step"
                        )
                    step_result, observed, evidence = self._execute_step(
                        request=request,
                        plan=plan,
                        flow=flow,
                        step=step,
                        variables=variables,
                        frozen_bindings=frozen_bindings,
                        phase="setup",
                        run_context=run_context,
                    )
                    bindings, binding_evidence = _freeze_step_bindings(
                        plan=plan,
                        request=request,
                        flow_id=flow_id,
                        step_id=step_id,
                        observations={**observations, **observed},
                        variables=variables,
                        frozen_bindings=frozen_bindings,
                        clock=self._clock,
                        source_evidence=evidence,
                        identity_source_evidence=(
                            *(
                                value
                                for value in all_evidence
                                if value.flow_id == flow_id and value.phase == "setup"
                            ),
                            *evidence,
                        ),
                        identity_providers=self._identity_providers,
                    )
                    for binding in bindings:
                        if run_context is not None:
                            run_context.freeze_binding(binding)
                        frozen_bindings[str(binding["test_data_id"])] = binding
                    if run_context is not None:
                        step_result["test_data_binding_refs"] = sorted(
                            {
                                *cast(list[str], step_result["test_data_binding_refs"]),
                                *(str(binding["binding_id"]) for binding in bindings),
                            }
                        )
                    coverage, coverage_evidence = _evaluate_step_data_coverage(
                        plan=plan,
                        request=request,
                        flow_id=flow_id,
                        step_id=step_id,
                        observations=observed,
                    )
                    duplicate_conditions = set(coverage_proofs).intersection(
                        str(value["condition_id"]) for value in coverage
                    )
                    if duplicate_conditions:
                        raise ValueError(
                            "Test Data Coverage condition was already evaluated: "
                            f"{sorted(duplicate_conditions)}"
                        )
                    coverage_proofs.update(
                        (str(value["condition_id"]), value) for value in coverage
                    )
                    if binding_evidence or coverage_evidence:
                        step_result["evidence_refs"] = sorted(
                            {
                                *cast(list[str], step_result["evidence_refs"]),
                                *(value.evidence_ref for value in binding_evidence),
                                *(value.evidence_ref for value in coverage_evidence),
                            }
                        )
                    if any(value["status"] != "passed" for value in coverage):
                        raise _TestDataCoverageBlockedError(
                            "Executable Test Data Coverage condition failed",
                            (*evidence, *binding_evidence, *coverage_evidence),
                        )
                    observations.update(observed)
                    cleanup_dependency_ids.add(step_id)
                    step_results.append(step_result)
                    all_evidence.extend((*evidence, *binding_evidence, *coverage_evidence))
                    self._emit(
                        "step_completed",
                        flow_id=flow_id,
                        phase="setup",
                        step_id=step_id,
                        status="passed",
                    )
                except _TestDataBindingBlockedError as error:
                    reason = f"{flow['flow_id']}/{step['step_id']}: {error}"
                    cleanup_dependency_ids.add(step_id)
                    all_evidence.extend(error.evidence)
                    step_results.append(
                        _failed_step(
                            step,
                            "setup",
                            "blocked",
                            reason,
                            evidence_refs=tuple(value.evidence_ref for value in error.evidence),
                            trace=error.trace,
                        )
                    )
                    failure_reasons.append(reason)
                    flow_status = "blocked"
                    stopped = True
                    self._emit(
                        "step_completed",
                        flow_id=flow_id,
                        phase="setup",
                        step_id=step_id,
                        status="blocked",
                        message="Deterministic data binding was blocked.",
                    )
                except _TestDataCoverageBlockedError as error:
                    reason = f"{flow['flow_id']}/{step['step_id']}: {error}"
                    cleanup_dependency_ids.add(step_id)
                    all_evidence.extend(error.evidence)
                    step_results.append(
                        _failed_step(
                            step,
                            "setup",
                            "blocked",
                            reason,
                            evidence_refs=tuple(value.evidence_ref for value in error.evidence),
                            trace=error.trace,
                        )
                    )
                    failure_reasons.append(reason)
                    flow_status = "blocked"
                    stopped = True
                    self._emit(
                        "step_completed",
                        flow_id=flow_id,
                        phase="setup",
                        step_id=step_id,
                        status="blocked",
                        message="Executable Test Data Coverage was blocked.",
                    )
                except TestDataStepBlockedError as error:
                    reason = f"{flow['flow_id']}/{step['step_id']}: {error}"
                    all_evidence.extend(error.evidence)
                    step_results.append(
                        _failed_step(
                            step,
                            "setup",
                            "blocked",
                            reason,
                            evidence_refs=tuple(
                                value.evidence_ref for value in error.evidence
                            ),
                            trace=error.trace,
                        )
                    )
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
                    # The adapter returned Evidence before an output binding or
                    # assertion failed, so its external side effect may exist.
                    # Make the step eligible for its reviewed cleanup sequence.
                    cleanup_dependency_ids.add(step_id)
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
                except Exception as error:
                    reason = f"{flow['flow_id']}/{step['step_id']}: {type(error).__name__}: {error}"
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
            executed_flows.append((flow, variables, cleanup_dependency_ids, attempted_channels))

        cleanup_failed = False
        cleanup_required = stopped or any(
            flow["cleanup_policy"] == "delete_after_run" for flow, _, _, _ in executed_flows
        )
        if cleanup_required:
            results_by_flow = {str(value["flow_id"]): value for value in flow_results}
            for flow, variables, cleanup_dependency_ids, attempted_channels in reversed(
                executed_flows
            ):
                if not stopped and flow["cleanup_policy"] != "delete_after_run":
                    continue
                cleanup_results = cast(
                    list[dict[str, Any]], results_by_flow[str(flow["flow_id"])]["cleanup_results"]
                )
                for step in cast(list[dict[str, Any]], flow["cleanup_steps"]):
                    flow_id = str(flow["flow_id"])
                    step_id = str(step["step_id"])
                    dependencies = {
                        str(value) for value in cast(list[object], step.get("depends_on", []))
                    }
                    missing_dependencies = dependencies - cleanup_dependency_ids
                    if missing_dependencies:
                        cleanup_results.append(
                            _not_run_step(
                                step,
                                phase="cleanup",
                            )
                        )
                        self._emit(
                            "step_completed",
                            flow_id=flow_id,
                            phase="cleanup",
                            step_id=step_id,
                            status="not_run",
                            message="Cleanup step was not required.",
                        )
                        continue
                    if step["channel"] == "ui" and "ui" not in attempted_channels:
                        cleanup_results.append(
                            _not_run_step(
                                step,
                                phase="cleanup",
                            )
                        )
                        self._emit(
                            "step_completed",
                            flow_id=flow_id,
                            phase="cleanup",
                            step_id=step_id,
                            status="not_run",
                            message="Cleanup step was not required.",
                        )
                        continue
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
                            plan=plan,
                            flow=flow,
                            step=step,
                            variables=variables,
                            frozen_bindings=frozen_bindings,
                            phase="cleanup",
                            run_context=run_context,
                        )
                        cleanup_results.append(result)
                        cleanup_dependency_ids.add(step_id)
                        all_evidence.extend(evidence)
                        self._emit(
                            "step_completed",
                            flow_id=flow_id,
                            phase="cleanup",
                            step_id=step_id,
                            status="passed",
                        )
                    except TestDataStepBlockedError as error:
                        if "Variable" in str(error) and "not available" in str(error):
                            cleanup_results.append(
                                _not_run_step(
                                    step,
                                    phase="cleanup",
                                )
                            )
                            self._emit(
                                "step_completed",
                                flow_id=flow_id,
                                phase="cleanup",
                                step_id=step_id,
                                status="not_run",
                                message="Cleanup step was not required.",
                            )
                            continue
                        reason = f"{flow['flow_id']}/{step['step_id']} cleanup: {error}"
                        all_evidence.extend(error.evidence)
                        cleanup_results.append(
                            _failed_step(
                                step,
                                "cleanup",
                                "blocked",
                                reason,
                                evidence_refs=tuple(
                                    value.evidence_ref for value in error.evidence
                                ),
                                trace=error.trace,
                            )
                        )
                        failure_reasons.append(reason)
                        cleanup_failed = True
                        self._emit(
                            "step_completed",
                            flow_id=flow_id,
                            phase="cleanup",
                            step_id=step_id,
                            status="blocked",
                            message="Cleanup step was blocked.",
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
                    except Exception as error:
                        reason = (
                            f"{flow['flow_id']}/{step['step_id']} cleanup: "
                            f"{type(error).__name__}: {error}"
                        )
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
        data_coverage = summarize_data_coverage(
            plan=plan,
            proofs=list(coverage_proofs.values()),
        )
        expected_binding_ids = {
            str(value["test_data_id"]) for value in cast(list[dict[str, Any]], plan["data_sets"])
        }
        if (
            plan.get("schema_version") in {"v2", "v3"}
            and not stopped
            and (set(frozen_bindings) != expected_binding_ids)
        ):
            missing = sorted(expected_binding_ids - set(frozen_bindings))
            failure_reasons.append(f"Test data identity bindings were not frozen: {missing}")
            status = "blocked"
        if plan.get("schema_version") in {"v2", "v3"} and data_coverage["status"] != "passed":
            failure_reasons.append(
                f"Executable Test Data Coverage is below 100%: {data_coverage['coverage_percent']}"
            )
            status = "blocked"
        if failure_reasons and status != "blocked":
            status = (
                "blocked"
                if any(value["status"] == "blocked" for value in flow_results)
                else "failed"
            )
        if run_context is not None:
            run_context.add_evidence_refs(
                tuple(value.evidence_ref for value in all_evidence)
            )
            _add_v3_binding_refs(flow_results)
        artifact: dict[str, Any] = {
            "artifact_type": "TestDataExecutionResult",
            "schema_version": _execution_result_schema_version(plan),
            "execution_result_id": request.execution_result_id,
            "run_id": request.run_id,
            "test_data_plan_id": plan["test_data_plan_id"],
            "project_id": request.project_id,
            "status": status,
            "started_at": started,
            "completed_at": self._iso(self._clock()),
            "flow_results": flow_results,
            "data_bindings": [frozen_bindings[key] for key in sorted(frozen_bindings)],
            "data_coverage": data_coverage,
            "evidence": [value.to_artifact() for value in all_evidence],
            "failure_reasons": sorted(set(failure_reasons)),
            "cleanup_status": (
                "failed" if cleanup_failed else "passed" if cleanup_required else "not_required"
            ),
        }
        if run_context is not None:
            artifact["run_context"] = run_context.to_artifact()
            _validate_v3_binding_references(artifact)
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
        started_at = request.started_at or self._clock()
        flows = cast(list[dict[str, Any]], plan["generation_flows"])
        flow_results = [_not_run_flow(flow) for flow in flows]
        artifact: dict[str, Any] = {
            "artifact_type": "TestDataExecutionResult",
            "schema_version": _execution_result_schema_version(plan),
            "execution_result_id": request.execution_result_id,
            "run_id": request.run_id,
            "test_data_plan_id": plan["test_data_plan_id"],
            "project_id": request.project_id,
            "status": "interrupted",
            "started_at": self._iso(started_at),
            "completed_at": self._iso(self._clock()),
            "flow_results": flow_results,
            "data_bindings": [],
            "data_coverage": summarize_data_coverage(plan=plan, proofs=[]),
            "evidence": [],
            "failure_reasons": [reason],
            "cleanup_status": "interrupted",
        }
        _attach_empty_v3_run_context(
            artifact=artifact,
            plan=plan,
            request=request,
            started_at=started_at,
            flows=flows,
        )
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
        started_at = request.started_at or self._clock()
        flows = cast(list[dict[str, Any]], plan["generation_flows"])
        flow_results = [_not_run_flow(flow) for flow in flows]
        artifact: dict[str, Any] = {
            "artifact_type": "TestDataExecutionResult",
            "schema_version": _execution_result_schema_version(plan),
            "execution_result_id": request.execution_result_id,
            "run_id": request.run_id,
            "test_data_plan_id": plan["test_data_plan_id"],
            "project_id": request.project_id,
            "status": "failed",
            "started_at": self._iso(started_at),
            "completed_at": self._iso(self._clock()),
            "flow_results": flow_results,
            "data_bindings": [],
            "data_coverage": summarize_data_coverage(plan=plan, proofs=[]),
            "evidence": [],
            "failure_reasons": [reason],
            "cleanup_status": "failed",
        }
        _attach_empty_v3_run_context(
            artifact=artifact,
            plan=plan,
            request=request,
            started_at=started_at,
            flows=flows,
        )
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
        plan: Mapping[str, object],
        flow: dict[str, Any],
        step: dict[str, Any],
        variables: dict[str, object],
        frozen_bindings: Mapping[str, dict[str, Any]],
        phase: str,
        run_context: RunContext | None = None,
    ) -> tuple[
        dict[str, Any],
        dict[str, object],
        tuple[TestDataExecutionEvidence, ...],
    ]:
        channel = str(step["channel"])
        executor = self._executors.get(channel)
        if executor is None:
            raise TestDataStepBlockedError(f"No executor is configured for channel {channel}")
        visible_variables = (
            run_context.variables_for_flow(str(flow["flow_id"]))
            if run_context is not None
            else variables
        )
        resolved = cast(
            dict[str, object], _resolve_variables(step["inputs"], visible_variables)
        )
        resolved_step = dict(step)
        if "target" in resolved_step:
            resolved_step["target"] = _resolve_variables(
                resolved_step["target"], visible_variables
            )
        if "playwright" in resolved_step:
            resolved_step["playwright"] = _resolve_variables(
                resolved_step["playwright"], visible_variables
            )
        binding_ref = str(step.get("data_binding_ref", ""))
        binding: dict[str, Any] | None = None
        if binding_ref:
            binding = (
                dict(run_context.resolve_binding(binding_ref))
                if run_context is not None
                else frozen_bindings.get(binding_ref)
            )
            if binding is None:
                raise TestDataStepBlockedError(
                    f"Data binding is missing for operation: {binding_ref}"
                )
            resolved_step["_frozen_data_binding"] = binding
        elif plan.get("schema_version") == "v3" and channel == "ui":
            # v3 screenshots are data Evidence. Navigation or other screen-level
            # preparation may run before a record is in scope, but it must not
            # publish an unbound screenshot that could be mistaken for record proof.
            resolved_step["_suppress_unbound_screenshot"] = True
        if _is_identity_source_step(
            plan=plan,
            flow_id=str(flow["flow_id"]),
            step_id=str(step["step_id"]),
        ):
            resolved_step["_requires_unique_identity_match"] = True
        execution = executor.execute(
            request=request,
            flow_id=str(flow["flow_id"]),
            step=resolved_step,
            resolved_inputs=resolved,
            variables=visible_variables,
            phase=phase,
        )
        observations = dict(execution.source_values)
        if binding_ref and channel == "ui":
            ui = observations.get("ui")
            if not isinstance(ui, Mapping):
                raise _TestDataBindingBlockedError(
                    f"Bound UI operation returned no binding verification: {binding_ref}",
                    execution.evidence,
                )
            if binding is None:
                raise TestDataStepBlockedError(
                    f"Data binding is missing for UI operation: {binding_ref}"
                )
            if ui.get("binding_match_count") != 1:
                raise _TestDataBindingBlockedError(
                    f"Frozen Binding の record scope が 1 件ではありません: {binding_ref}",
                    execution.evidence,
                )
            if ui.get("observed_identity_digest") != binding["identity_digest"]:
                raise _TestDataBindingBlockedError(
                    f"Frozen Binding と DOM の身元が一致しません: {binding_ref}",
                    execution.evidence,
                )
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
                    if run_context is not None:
                        run_context.set_local_variable(
                            flow_id=str(flow["flow_id"]),
                            name=variable,
                            value=output,
                        )
                    else:
                        variables[variable] = output
                    output_names.append(variable)
            for assertion in cast(list[dict[str, Any]], step["postconditions"]):
                _assert_postcondition(
                    assertion,
                    observations,
                    (
                        run_context.variables_for_flow(str(flow["flow_id"]))
                        if run_context is not None
                        else visible_variables
                    ),
                )
        except (AssertionError, ValueError) as error:
            raise _TestDataStepFailedError(str(error), execution.evidence) from error
        evidence_refs = [value.evidence_ref for value in execution.evidence]
        step_result: dict[str, Any] = {
                "step_id": step["step_id"],
                "sequence": step["sequence"],
                "channel": channel,
                "phase": phase,
                "status": "passed",
                "output_variables": sorted(output_names),
                "evidence_refs": evidence_refs,
            }
        if channel == "ui":
            step_result.update(_ui_step_trace(execution.trace))
        if run_context is not None:
            step_result["test_data_binding_refs"] = (
                [str(binding["binding_id"])] if binding is not None else []
            )
        return (
            step_result,
            observations,
            execution.evidence,
        )

    def _blocked_plan_result(
        self,
        plan: dict[str, Any],
        request: TestDataExecutionRequest,
        started: str,
        flows: list[dict[str, Any]],
        reasons: list[str] | None = None,
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        blocking_reasons = reasons or [
            f"TestDataPlan is blocked: {value}"
            for value in cast(list[str], plan["blocking_reasons"])
        ]
        flow_results = [_not_run_flow(flow) for flow in flows]
        artifact: dict[str, Any] = {
            "artifact_type": "TestDataExecutionResult",
            "schema_version": _execution_result_schema_version(plan),
            "execution_result_id": request.execution_result_id,
            "run_id": request.run_id,
            "test_data_plan_id": plan["test_data_plan_id"],
            "project_id": request.project_id,
            "status": "blocked",
            "started_at": started,
            "completed_at": self._iso(self._clock()),
            "flow_results": flow_results,
            "data_bindings": [],
            "data_coverage": summarize_data_coverage(plan=plan, proofs=[]),
            "evidence": [],
            "failure_reasons": blocking_reasons or ["TestDataPlan is blocked"],
            "cleanup_status": "not_required",
        }
        if run_context is not None:
            _add_v3_binding_refs(flow_results)
            artifact["run_context"] = run_context.to_artifact()
            _validate_v3_binding_references(artifact)
        return artifact

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
        if isinstance(actual, int) and not isinstance(actual, bool):
            # Playwright's bounded `count` observation is already the numeric
            # element count.  Other channels may expose the collection itself.
            actual_count = actual
        else:
            try:
                actual_count = len(actual)  # type: ignore[arg-type]
            except TypeError as error:
                raise AssertionError(
                    f"{assertion['assertion_id']} observed value does not have a count"
                ) from error
        if actual_count != expected:
            raise AssertionError(f"{assertion['assertion_id']} count did not equal {expected!r}")
    elif operator == "satisfies" and not _satisfies(actual, expected):
        raise AssertionError(f"{assertion['assertion_id']} did not satisfy {expected!r}")


def _freeze_step_bindings(
    *,
    plan: Mapping[str, object],
    request: TestDataExecutionRequest,
    flow_id: str,
    step_id: str,
    observations: Mapping[str, object],
    variables: Mapping[str, object],
    frozen_bindings: Mapping[str, dict[str, Any]],
    clock: Callable[[], datetime],
    source_evidence: tuple[TestDataExecutionEvidence, ...],
    identity_source_evidence: tuple[TestDataExecutionEvidence, ...] | None = None,
    identity_providers: Mapping[str, DataIdentityProvider] | None = None,
) -> tuple[list[dict[str, Any]], tuple[TestDataExecutionEvidence, ...]]:
    candidates = [
        data_set
        for data_set in cast(list[dict[str, Any]], plan.get("data_sets", []))
        if isinstance(data_set.get("identity_binding"), dict)
        and cast(dict[str, Any], data_set["identity_binding"]).get("source_flow_id") == flow_id
        and cast(dict[str, Any], data_set["identity_binding"]).get("source_step_id") == step_id
    ]
    if not candidates:
        return [], ()
    try:
        frozen_at = clock()
        if frozen_at.utcoffset() is None:
            raise ValueError("Data binding clock must be timezone-aware")
        bindings = [
            _freeze_binding(
                data_set=data_set,
                request=request,
                observations=observations,
                variables=variables,
                frozen_at=frozen_at,
                source_evidence=identity_source_evidence or source_evidence,
                identity_providers=identity_providers or {},
                include_project_scope=plan.get("schema_version") == "v3",
            )
            for data_set in candidates
        ]
        duplicate = {str(value["test_data_id"]) for value in bindings}.intersection(frozen_bindings)
        if duplicate:
            raise ValueError(f"Data binding is already frozen: {sorted(duplicate)}")
    except (KeyError, TypeError, ValueError) as error:
        raise _TestDataBindingBlockedError(
            f"Deterministic data binding failed: {error}", source_evidence
        ) from error
    evidence = tuple(_binding_evidence(value) for value in bindings)
    return bindings, evidence


def _is_identity_source_step(*, plan: Mapping[str, object], flow_id: str, step_id: str) -> bool:
    return any(
        isinstance(data_set.get("identity_binding"), Mapping)
        and cast(Mapping[str, object], data_set["identity_binding"]).get("source_flow_id")
        == flow_id
        and cast(Mapping[str, object], data_set["identity_binding"]).get("source_step_id")
        == step_id
        for data_set in cast(list[dict[str, Any]], plan.get("data_sets", []))
    )


def _freeze_binding(
    *,
    data_set: Mapping[str, object],
    request: TestDataExecutionRequest,
    observations: Mapping[str, object],
    variables: Mapping[str, object],
    frozen_at: datetime,
    source_evidence: tuple[TestDataExecutionEvidence, ...],
    identity_providers: Mapping[str, DataIdentityProvider],
    include_project_scope: bool = False,
) -> dict[str, Any]:
    del variables
    test_data_id = str(data_set["test_data_id"])
    identity = cast(dict[str, Any], data_set["identity_binding"])
    provider_definition = identity.get("provider")
    if not isinstance(provider_definition, Mapping):
        raise ValueError(f"{test_data_id} has no configured DataIdentityProvider")
    provider_ref = str(provider_definition.get("provider_ref", ""))
    provider_type = str(provider_definition.get("type", ""))
    provider = identity_providers.get(provider_ref)
    if provider is None:
        raise ValueError(
            f"{test_data_id} DataIdentityProvider is not configured: {provider_ref or '<blank>'}"
        )
    if provider.provider_type != provider_type:
        raise ValueError(
            f"{test_data_id} DataIdentityProvider type differs: "
            f"expected={provider_type!r} actual={provider.provider_type!r}"
        )
    binding_id = _binding_id(request.run_id, test_data_id)
    provider_result = provider.resolve(
        DataIdentityResolveRequest(
            project_id=request.project_id,
            run_id=request.run_id,
            test_data_id=test_data_id,
            provider_ref=provider_ref,
            identity_definition=identity,
            observations=observations,
            source_evidence=tuple(
                DataIdentitySourceEvidence(
                    evidence_type=value.evidence_type,
                    evidence_ref=value.evidence_ref,
                    sanitized=value.sanitized,
                )
                for value in source_evidence
            ),
            evidence_ref=(
                f"artifact://{request.execution_result_id}/data-bindings/{binding_id}"
            ),
        )
    )
    resolved = provider_result.to_mapping()
    primary = cast(dict[str, object], resolved["primary_key"])
    business = cast(list[dict[str, object]], resolved["business_unique_keys"])
    screen_values = cast(list[dict[str, object]], resolved["screen_identity_values"])
    record_scope_locator = cast(dict[str, object], resolved["record_scope_locator"])
    configured_screen_specs = identity.get("screen_identity_values")
    screen_specs = (
        cast(list[dict[str, Any]], configured_screen_specs)
        if isinstance(configured_screen_specs, list) and configured_screen_specs
        else [cast(dict[str, Any], identity["screen_key"])]
    )
    screen = screen_values[0]
    identity_observations = {
        "business_unique_keys": [
            _frozen_dom_identity_observation(value)
            for value in cast(list[dict[str, Any]], identity["business_unique_keys"])
        ],
        "screen_key": _frozen_dom_identity_observation(screen_specs[0]),
        "screen_identity_values": [
            _frozen_dom_identity_observation(value) for value in screen_specs
        ],
    }
    identity_digest = _canonical_digest(
        {
            "business_unique_keys": business,
            "screen_identity_values": screen_values,
        }
    )
    payload: dict[str, Any] = {
        "binding_id": binding_id,
        "run_id": request.run_id,
        "test_data_id": test_data_id,
        "binding_mode": identity["binding_mode"],
        "source_flow_id": identity["source_flow_id"],
        "source_step_id": identity["source_step_id"],
        "identity_provider_type": provider_type,
        "identity_provider_ref": provider_ref,
        "primary_key": primary,
        "business_unique_keys": business,
        "screen_identity_values": screen_values,
        "record_scope_locator": record_scope_locator,
        "screen_key": screen,
        "screen_locator": record_scope_locator,
        "identity_observations": identity_observations,
        "identity_digest": identity_digest,
        "match_count": provider_result.match_count,
        "frozen_at": frozen_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    if include_project_scope:
        payload["project_id"] = request.project_id
    content_digest = _canonical_digest(payload)
    return {
        **payload,
        "content_digest": content_digest,
        "evidence_ref": provider_result.evidence_ref,
    }


def _frozen_dom_identity_observation(spec: Mapping[str, object]) -> dict[str, object]:
    raw = spec.get("dom_observation")
    if not isinstance(raw, Mapping):
        raise ValueError(f"{spec.get('name', '<unknown>')} の DOM 身元観測定義がありません")
    observation = {"name": str(spec["name"]), **dict(raw)}
    kind = str(observation.get("kind", ""))
    if kind not in {"text", "input_value", "attribute"}:
        raise ValueError(f"{spec['name']} の DOM 身元観測種別が不正です")
    if kind == "attribute" and not str(observation.get("attribute_name", "")).strip():
        raise ValueError(f"{spec['name']} の DOM 属性名がありません")
    locator = observation.get("locator")
    if locator is not None and (
        not isinstance(locator, Mapping)
        or locator.get("exact") is not True
        or locator.get("frame") is not None
    ):
        raise ValueError(
            f"{spec['name']} の DOM 身元 Locator は bound container 内の exact 指定が必須です"
        )
    return observation


def _binding_evidence(binding: Mapping[str, object]) -> TestDataExecutionEvidence:
    return TestDataExecutionEvidence(
        evidence_id=f"{binding['binding_id']}-evidence",
        flow_id=str(binding["source_flow_id"]),
        step_id=str(binding["source_step_id"]),
        phase="setup",
        evidence_type="data_binding",
        evidence_ref=str(binding["evidence_ref"]),
        content_digest=str(binding["content_digest"]),
        sanitized=True,
    )


def _evaluate_step_data_coverage(
    *,
    plan: Mapping[str, object],
    request: TestDataExecutionRequest,
    flow_id: str,
    step_id: str,
    observations: Mapping[str, object],
) -> tuple[list[dict[str, Any]], tuple[TestDataExecutionEvidence, ...]]:
    conditions = conditions_for_step(plan, flow_id=flow_id, step_id=step_id)
    if not conditions:
        return [], ()
    database = observations.get("database")
    proofs: list[dict[str, Any]] = []
    evidence: list[TestDataExecutionEvidence] = []
    for condition in conditions:
        evaluated = evaluate_condition(condition, database=database)
        proof_id = _coverage_proof_id(request.run_id, str(condition["condition_id"]))
        payload = {
            "proof_id": proof_id,
            "run_id": request.run_id,
            **evaluated,
        }
        if payload.get("failure_reason") is None:
            payload.pop("failure_reason", None)
        content_digest = _canonical_digest(payload)
        proof = {
            **payload,
            "content_digest": content_digest,
            "evidence_ref": (f"artifact://{request.execution_result_id}/data-coverage/{proof_id}"),
        }
        proofs.append(proof)
        evidence.append(
            TestDataExecutionEvidence(
                evidence_id=f"{proof_id}-evidence",
                flow_id=flow_id,
                step_id=step_id,
                phase="setup",
                evidence_type="data_coverage",
                evidence_ref=str(proof["evidence_ref"]),
                content_digest=content_digest,
                sanitized=True,
            )
        )
    return proofs, tuple(evidence)


def _coverage_proof_id(run_id: str, condition_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{condition_id}\0data-coverage".encode()).hexdigest()
    return f"test-data-coverage-{digest[:32]}"


def _binding_id(run_id: str, test_data_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{test_data_id}\0identity-binding".encode()).hexdigest()
    return f"test-data-binding-{digest[:32]}"


def _execution_result_schema_version(plan: Mapping[str, object]) -> str:
    version = str(plan.get("schema_version", "v1"))
    return version if version in {"v2", "v3"} else "v1"


def _canonical_digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _add_v3_binding_refs(flow_results: list[dict[str, Any]]) -> None:
    for flow in flow_results:
        refs: set[str] = set()
        for step in (
            *cast(list[dict[str, Any]], flow["step_results"]),
            *cast(list[dict[str, Any]], flow["cleanup_results"]),
        ):
            step_refs = cast(list[str], step.setdefault("test_data_binding_refs", []))
            refs.update(step_refs)
        flow["test_data_binding_refs"] = sorted(refs)


def _validate_v3_binding_references(artifact: Mapping[str, object]) -> None:
    bindings = cast(list[dict[str, Any]], artifact["data_bindings"])
    by_id = {str(binding["binding_id"]): binding for binding in bindings}
    if len(by_id) != len(bindings):
        raise ValueError("TestDataBinding IDs must be unique within one Run")
    run_id = str(artifact["run_id"])
    project_id = str(artifact["project_id"])
    for binding in bindings:
        if binding.get("run_id") != run_id or binding.get("project_id") != project_id:
            raise ValueError("TestDataBinding scope differs from its execution Result")
        payload = {
            key: value
            for key, value in binding.items()
            if key not in {"content_digest", "evidence_ref"}
        }
        if binding.get("content_digest") != canonical_digest(payload):
            raise ValueError("TestDataBinding digest differs from its frozen content")
    allowed_unbound_screenshot_refs: set[str] = set()
    for flow in cast(list[dict[str, Any]], artifact["flow_results"]):
        flow_refs = set(cast(list[str], flow["test_data_binding_refs"]))
        step_refs: set[str] = set()
        for step in (
            *cast(list[dict[str, Any]], flow["step_results"]),
            *cast(list[dict[str, Any]], flow["cleanup_results"]),
        ):
            refs = set(cast(list[str], step["test_data_binding_refs"]))
            if not refs.issubset(by_id):
                raise ValueError("StepResult references a foreign TestDataBinding")
            step_refs.update(refs)
            if (
                step.get("channel") == "ui"
                and step.get("status") == "blocked"
                and not refs
                and isinstance(step.get("failure_stage"), str)
                and str(step["failure_stage"]).strip()
            ):
                allowed_unbound_screenshot_refs.update(
                    str(value)
                    for value in cast(list[object], step.get("evidence_refs", []))
                    if isinstance(value, str)
                )
        if flow_refs != step_refs:
            raise ValueError("FlowResult TestDataBinding refs differ from its Steps")
    for evidence in cast(list[dict[str, Any]], artifact["evidence"]):
        binding_ref = evidence.get("test_data_binding_ref")
        if binding_ref is not None and str(binding_ref) not in by_id:
            raise ValueError("Evidence references a foreign TestDataBinding")
        if (
            evidence.get("evidence_type") == "screenshot"
            and binding_ref is None
            and evidence.get("evidence_ref") not in allowed_unbound_screenshot_refs
        ):
            raise ValueError("Screenshot Evidence has no current TestDataBinding")
    run_context = cast(Mapping[str, object], artifact["run_context"])
    runtime_variables = cast(
        Mapping[str, object], run_context.get("runtime_variables", {})
    )
    if runtime_variables.get("operamind_run_id") != run_id:
        raise ValueError("RunContext Run identity differs")
    context_bindings = cast(list[dict[str, Any]], run_context["frozen_data_bindings"])
    if context_bindings != [by_id[key] for key in sorted(by_id)]:
        raise ValueError("RunContext frozen bindings differ from execution Result")


def _attach_empty_v3_run_context(
    *,
    artifact: dict[str, Any],
    plan: Mapping[str, object],
    request: TestDataExecutionRequest,
    started_at: datetime,
    flows: list[dict[str, Any]],
) -> None:
    if plan.get("schema_version") != "v3":
        return
    context = RunContext(
        project_id=request.project_id,
        run_id=request.run_id,
        execution_started_at=started_at,
        flow_dependencies=flow_dependencies_from_plan(flows),
    )
    _add_v3_binding_refs(cast(list[dict[str, Any]], artifact["flow_results"]))
    artifact["run_context"] = context.to_artifact()
    _validate_v3_binding_references(artifact)


def _satisfies(actual: object, expected: object) -> bool:
    if (
        isinstance(expected, str)
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and (match := _NUMERIC_PREDICATE.fullmatch(expected)) is not None
    ):
        boundary = float(match.group(2))
        value = float(actual)
        return {
            ">=": value >= boundary,
            "<=": value <= boundary,
            ">": value > boundary,
            "<": value < boundary,
        }[match.group(1)]
    return actual == expected


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
    normalized = path[2:] if path.startswith("$.") else path
    normalized = _JSON_ARRAY_INDEX.sub(r".\1", normalized)
    if "[" in normalized or "]" in normalized:
        return False, None
    current = source
    for component in normalized.split("."):
        if not component:
            return False, None
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
    trace: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "step_id": step["step_id"],
        "sequence": step["sequence"],
        "channel": step["channel"],
        "phase": phase,
        "status": status,
        "output_variables": [],
        "evidence_refs": list(evidence_refs),
        "failure_reason": reason,
    }
    if step.get("channel") == "ui" and trace:
        result.update(_ui_step_trace(trace))
    return result


def _ui_step_trace(value: Mapping[str, object]) -> dict[str, object]:
    """Project the public UI execution trace onto the StepResult."""

    result: dict[str, object] = {}
    for key in (
        "failure_stage",
        "driver",
        "locator_type",
        "record_scope_match_count",
        "action_locator_match_count",
        "observed_screen_identity_values",
        "observed_identity_digest",
    ):
        if key in value and value[key] is not None:
            result[key] = value[key]
    return result


def _not_run_step(
    step: Mapping[str, object],
    *,
    phase: str = "setup",
) -> dict[str, object]:
    return {
        "step_id": step["step_id"],
        "sequence": step["sequence"],
        "channel": step["channel"],
        "phase": phase,
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
