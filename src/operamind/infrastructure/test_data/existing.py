"""Execute reviewed identity lookup Steps for existing-data registration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any, cast

from operamind.application.data_identity import DataIdentitySourceEvidence
from operamind.application.existing_test_data import (
    ExistingDataObservation,
    ExistingDataObservationResolver,
    ExistingTestDataRegistrationInput,
    ProjectDataIdentityProfile,
)
from operamind.application.test_data_execution import (
    TestDataChannelExecutor,
    TestDataExecutionRequest,
    _assert_postcondition,
    _extract,
    _resolve_variables,
)


class ReviewedExistingDataObservationResolver(ExistingDataObservationResolver):
    """Run only profile-reviewed SQL/API/UI observation Steps; no guessed fallback."""

    def __init__(
        self,
        *,
        executors: Mapping[str, TestDataChannelExecutor],
        base_url_by_project: Callable[[str], str | None],
    ) -> None:
        self._executors = dict(executors)
        self._base_url_by_project = base_url_by_project

    def resolve(
        self,
        *,
        registration: ExistingTestDataRegistrationInput,
        profile: ProjectDataIdentityProfile,
    ) -> ExistingDataObservation:
        variables: dict[str, object] = {
            "business_unique_value": registration.business_unique_value
        }
        observations: dict[str, object] = {}
        evidence: list[DataIdentitySourceEvidence] = []
        completed_steps: set[str] = set()
        registration_run_id = "existing-registration-" + hashlib.sha256(
            f"{registration.project_id}\0{registration.registration_id}".encode()
        ).hexdigest()[:24]
        request = TestDataExecutionRequest(
            execution_result_id=registration_run_id,
            run_id=registration_run_id,
            project_id=registration.project_id,
            base_url=self._base_url_by_project(registration.project_id),
            started_at=registration.requested_at,
        )
        for step in profile.lookup_steps:
            step_id = str(step.get("step_id", ""))
            dependencies = {
                str(value) for value in cast(list[object], step.get("depends_on", []))
            }
            if not step_id or not dependencies.issubset(completed_steps):
                raise ValueError("Reviewed existing-data lookup Step dependency is invalid")
            channel = str(step.get("channel", ""))
            executor = self._executors.get(channel)
            if executor is None:
                raise ValueError(f"No reviewed existing-data executor is configured: {channel}")
            resolved_inputs = cast(
                dict[str, object], _resolve_variables(step.get("inputs", {}), variables)
            )
            resolved_step = dict(step)
            if "target" in resolved_step:
                resolved_step["target"] = _resolve_variables(
                    resolved_step["target"], variables
                )
            if "playwright" in resolved_step:
                resolved_step["playwright"] = _resolve_variables(
                    resolved_step["playwright"], variables
                )
            resolved_step["_requires_unique_identity_match"] = True
            execution = executor.execute(
                request=request,
                flow_id=registration_run_id,
                step=resolved_step,
                resolved_inputs=resolved_inputs,
                variables=variables,
                phase="setup",
            )
            if execution.failure_reason is not None:
                raise ValueError(execution.failure_reason)
            observed = dict(execution.source_values)
            observations.update(observed)
            for output in cast(list[dict[str, Any]], step.get("output_bindings", [])):
                exists, result = _extract(
                    observed.get(str(output["source"])), str(output["path"])
                )
                if not exists and output.get("required") is True:
                    raise ValueError(
                        f"Reviewed existing-data output was not observed: {output['variable']}"
                    )
                if exists:
                    variable = str(output["variable"])
                    if variable in variables:
                        raise ValueError(
                            f"Reviewed existing-data variable is already defined: {variable}"
                        )
                    variables[variable] = result
            for assertion in cast(
                list[dict[str, Any]], step.get("postconditions", [])
            ):
                _assert_postcondition(assertion, observed, variables)
            evidence.extend(
                DataIdentitySourceEvidence(
                    evidence_type=value.evidence_type,
                    evidence_ref=value.evidence_ref,
                    sanitized=value.sanitized,
                )
                for value in execution.evidence
            )
            completed_steps.add(step_id)
        return ExistingDataObservation(
            observations=observations,
            source_evidence=tuple(evidence),
        )
