"""Execute evidence-bound UI Plans and publish Contract-valid closure results."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    UI_EVIDENCE_TYPES,
    ApprovalGrantRepository,
    ArtifactRepository,
    UiDeploymentWrite,
    UiExecutionEvidenceWrite,
    UiExecutionPlanRecord,
    UiExecutionPlanWrite,
    UiExecutionRunRecord,
    UiPreflightCheckWrite,
    UiScenarioResultWrite,
    UiVerificationCompletionWrite,
    UiVerificationRepository,
    UiVerificationResultRecord,
    VerificationScenarioWrite,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SCENARIO_STATUSES = frozenset({"passed", "failed", "blocked", "skipped"})
_FAILURE_CATEGORIES = frozenset(
    {
        "none",
        "business_assertion",
        "environment",
        "test_data",
        "locator",
        "authentication",
        "blocked",
    }
)


@dataclass(frozen=True, slots=True)
class UiVerificationServiceResult:
    artifact: dict[str, Any]
    record: UiVerificationResultRecord


@dataclass(frozen=True, slots=True)
class UiRunRecovery:
    recovery_id: str
    actor: str
    reason: str
    stale_before: datetime

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.recovery_id, self.actor, self.reason)):
            raise ValueError("UI Run Recovery identity, actor, and reason must not be blank")
        if self.stale_before.utcoffset() is None:
            raise ValueError("UI Run Recovery stale_before must include a timezone")

    def to_dict(self) -> dict[str, str]:
        return {
            "recovery_id": self.recovery_id,
            "cause": "interrupted_execution",
            "actor": self.actor,
            "reason": self.reason,
            "stale_before": self.stale_before.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }


class UiVerificationService:
    """Coordinate Scenario approval, execution gates, evidence, and Case closure."""

    def __init__(self, *, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)
        self._repository = UiVerificationRepository(connection)
        self._grants = ApprovalGrantRepository(connection, contracts)

    def register_scenario(self, write: VerificationScenarioWrite) -> bool:
        return self._repository.store_scenario(write)

    def build_plan(
        self,
        *,
        deployment: UiDeploymentWrite,
        plan: UiExecutionPlanWrite,
    ) -> UiExecutionPlanRecord:
        if (
            deployment.project_id != plan.project_id
            or deployment.environment_id != plan.environment_id
            or deployment.deployment_revision != plan.deployment_revision
        ):
            raise ValueError("UI Deployment does not match Execution Plan scope")
        with self._connection.transaction():
            self._repository.register_deployment(deployment)
            return self._repository.create_plan(plan)

    def record_preflight(
        self,
        *,
        project_id: str,
        plan_id: str,
        attempt_id: str,
        checks: tuple[UiPreflightCheckWrite, ...],
    ) -> UiExecutionPlanRecord:
        return self._repository.record_preflight(
            project_id=project_id,
            plan_id=plan_id,
            attempt_id=attempt_id,
            checks=checks,
        )

    def start_run(
        self,
        *,
        project_id: str,
        plan_id: str,
        run_id: str,
        approval_grant_id: str,
    ) -> UiExecutionRunRecord:
        with self._connection.transaction():
            existing = self._repository.find_run(
                project_id=project_id,
                plan_id=plan_id,
                run_id=run_id,
                approval_grant_id=approval_grant_id,
            )
            if existing is not None:
                return existing
            self._grants.authorize_ui_plan(
                grant_id=approval_grant_id,
                project_id=project_id,
                plan_id=plan_id,
                lock=True,
            )
            return self._repository.start_run(
                project_id=project_id,
                plan_id=plan_id,
                run_id=run_id,
                approval_grant_id=approval_grant_id,
            )

    def complete_run(
        self,
        *,
        verification_result_id: str,
        project_id: str,
        run_id: str,
        scenario_results: tuple[UiScenarioResultWrite, ...],
        evidence: tuple[UiExecutionEvidenceWrite, ...],
        out_of_scope_files: tuple[str, ...] = (),
        recovery: UiRunRecovery | None = None,
    ) -> UiVerificationServiceResult:
        if any(not value.strip() for value in (verification_result_id, project_id, run_id)):
            raise ValueError("UI Verification completion fields must not be blank")
        _validate_unique_non_blank(out_of_scope_files, "out-of-scope files")
        if recovery is not None and (
            evidence
            or out_of_scope_files
            or any(result.status != "blocked" for result in scenario_results)
        ):
            raise ValueError(
                "UI Run Recovery must close every Scenario as blocked without Evidence"
            )
        scope = self._repository.load_run_scope(project_id=project_id, run_id=run_id)
        is_replay = self._is_replay(verification_result_id)
        if scope.run_status != "running" and not is_replay:
            raise ValueError("UI Verification completion requires a running Run")
        ordered_results = _validate_results(
            scenario_refs=scope.scenario_refs,
            scenario_evidence_requirements=scope.scenario_evidence_requirements,
            impact_item_ids=scope.impact_item_ids,
            scenario_results=scenario_results,
            evidence=evidence,
        )
        covered_items = {
            item_id for result in ordered_results for item_id in result.impact_item_refs
        }
        unresolved = tuple(sorted(set(scope.impact_item_ids) - covered_items))
        normalized_out_of_scope = tuple(sorted(out_of_scope_files))
        status = _derive_status(
            scenario_results=ordered_results,
            unresolved_impact_item_ids=unresolved,
            out_of_scope_files=normalized_out_of_scope,
        )
        failure_reasons = _failure_reasons(
            scenario_results=ordered_results,
            unresolved_impact_item_ids=unresolved,
            out_of_scope_files=normalized_out_of_scope,
        )
        artifact: dict[str, Any] = {
            "artifact_type": "UiVerificationResult",
            "schema_version": "v1",
            "verification_result_id": verification_result_id,
            "analysis_case_id": scope.analysis_case_id,
            "edit_packet_id": scope.edit_packet_id,
            "repository_revision": scope.repository_revision,
            "deployment_revision": scope.deployment_revision,
            "environment_id": scope.environment_id,
            "status": status,
            "scenario_results": [_scenario_result_payload(result) for result in ordered_results],
            "unresolved_impact_item_ids": list(unresolved),
            "out_of_scope_files": list(normalized_out_of_scope),
            "failure_reasons": list(failure_reasons),
        }
        if recovery is not None:
            artifact["recovery"] = recovery.to_dict()
        self._contracts.validate_artifact(artifact)
        write = UiVerificationCompletionWrite(
            verification_result_id=verification_result_id,
            status=status,
            scenario_results=ordered_results,
            evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
            unresolved_impact_item_ids=unresolved,
            out_of_scope_files=normalized_out_of_scope,
            failure_reasons=failure_reasons,
        )
        with self._connection.transaction():
            authorization = None
            if not is_replay and status != "blocked":
                authorization = self._grants.authorize_ui(
                    grant_id=scope.approval_grant_id,
                    project_id=project_id,
                    edit_packet_id=scope.edit_packet_id,
                    scenario_refs=scope.scenario_refs,
                    lock=True,
                )
            else:
                self._grants.lock(
                    grant_id=scope.approval_grant_id,
                    project_id=project_id,
                )
            self._artifacts.store(
                artifact_id=verification_result_id,
                project_id=project_id,
                analysis_case_id=scope.analysis_case_id,
                artifact=artifact,
            )
            record = self._repository.complete_run(scope=scope, write=write)
            if (
                status != "blocked"
                and authorization is not None
                and authorization.state != "completed"
            ):
                self._grants.append_event(
                    event_id=f"approval-event:{verification_result_id}:completed",
                    grant_id=scope.approval_grant_id,
                    project_id=project_id,
                    event_type="completed",
                    actor="operamind",
                    reason="UI Verification closed the approved scenario scope",
                )
        return UiVerificationServiceResult(artifact=artifact, record=record)

    def recover_run(
        self,
        *,
        verification_result_id: str,
        project_id: str,
        run_id: str,
        recovery: UiRunRecovery,
    ) -> UiVerificationServiceResult:
        """Close one interrupted stale Run as blocked while preserving a retryable Plan."""

        if recovery.recovery_id != verification_result_id:
            raise ValueError("UI Run Recovery ID must equal its Verification Result ID")
        if not self._is_replay(verification_result_id):
            self._repository.assert_run_recoverable(
                project_id=project_id,
                run_id=run_id,
                stale_before=recovery.stale_before,
            )
        scope = self._repository.load_run_scope(project_id=project_id, run_id=run_id)
        return self.complete_run(
            verification_result_id=verification_result_id,
            project_id=project_id,
            run_id=run_id,
            scenario_results=tuple(
                UiScenarioResultWrite(
                    scenario_id=scenario_id,
                    status="blocked",
                    impact_item_refs=(),
                    evidence_refs=(),
                    failure_category="blocked",
                    summary=(f"Interrupted Run recovered by {recovery.actor}: {recovery.reason}"),
                )
                for scenario_id in scope.scenario_refs
            ),
            evidence=(),
            recovery=recovery,
        )

    def _is_replay(self, verification_result_id: str) -> bool:
        artifact = self._artifacts.get(verification_result_id)
        return artifact is not None and artifact.get("artifact_type") == "UiVerificationResult"


def _validate_results(
    *,
    scenario_refs: tuple[str, ...],
    scenario_evidence_requirements: tuple[tuple[str, tuple[str, ...]], ...],
    impact_item_ids: tuple[str, ...],
    scenario_results: tuple[UiScenarioResultWrite, ...],
    evidence: tuple[UiExecutionEvidenceWrite, ...],
) -> tuple[UiScenarioResultWrite, ...]:
    scenario_ids = [result.scenario_id for result in scenario_results]
    if len(scenario_ids) != len(set(scenario_ids)) or set(scenario_ids) != set(scenario_refs):
        raise ValueError("UI Run must contain exactly one Result for every planned Scenario")
    evidence_by_id: dict[str, UiExecutionEvidenceWrite] = {}
    evidence_refs: set[str] = set()
    for item in evidence:
        if any(
            not value.strip()
            for value in (
                item.evidence_id,
                item.scenario_id,
                item.evidence_type,
                item.evidence_ref,
                item.content_digest,
            )
        ):
            raise ValueError("UI Evidence fields must not be blank")
        if item.evidence_id in evidence_by_id or item.evidence_ref in evidence_refs:
            raise ValueError("UI Evidence IDs and refs must be unique")
        if item.scenario_id not in scenario_refs or item.evidence_type not in UI_EVIDENCE_TYPES:
            raise ValueError("UI Evidence is outside the Plan or has an invalid type")
        if not item.sanitized or _SHA256_PATTERN.fullmatch(item.content_digest) is None:
            raise ValueError("UI Evidence must be sanitized and use a lowercase SHA-256 digest")
        evidence_by_id[item.evidence_id] = item
        evidence_refs.add(item.evidence_ref)
    allowed_items = set(impact_item_ids)
    by_scenario = {result.scenario_id: result for result in scenario_results}
    requirements_by_scenario = dict(scenario_evidence_requirements)
    if set(requirements_by_scenario) != set(scenario_refs):
        raise RuntimeError("UI Plan Scenario Evidence requirements do not match the Plan")
    referenced_evidence_ids: set[str] = set()
    for result in scenario_results:
        _validate_unique_non_blank(result.impact_item_refs, "Scenario impact refs")
        _validate_unique_non_blank(result.evidence_refs, "Scenario evidence refs")
        if result.status not in _SCENARIO_STATUSES:
            raise ValueError(f"Invalid Scenario status: {result.status}")
        if result.failure_category not in _FAILURE_CATEGORIES:
            raise ValueError(f"Invalid Scenario failure category: {result.failure_category}")
        if (result.status == "passed") != (result.failure_category == "none"):
            raise ValueError("Scenario status and failure category are inconsistent")
        if not set(result.impact_item_refs).issubset(allowed_items):
            raise ValueError("Scenario Result references an Impact Item outside the Packet")
        referenced = [evidence_by_id.get(evidence_id) for evidence_id in result.evidence_refs]
        if any(item is None or item.scenario_id != result.scenario_id for item in referenced):
            raise ValueError("Scenario Result Evidence does not belong to that Scenario")
        referenced_evidence_ids.update(result.evidence_refs)
        if result.status == "passed":
            types = {item.evidence_type for item in referenced if item is not None}
            required_types = {
                "screenshot",
                "assertion",
                *requirements_by_scenario[result.scenario_id],
            }
            missing_types = sorted(required_types - types)
            if missing_types:
                raise ValueError(
                    f"Passed Scenario is missing required Evidence types: {missing_types}"
                )
    if referenced_evidence_ids != set(evidence_by_id):
        raise ValueError("Every UI Evidence item must be referenced by exactly one Scenario Result")
    return tuple(by_scenario[scenario_id] for scenario_id in scenario_refs)


def _derive_status(
    *,
    scenario_results: tuple[UiScenarioResultWrite, ...],
    unresolved_impact_item_ids: tuple[str, ...],
    out_of_scope_files: tuple[str, ...],
) -> str:
    if out_of_scope_files:
        return "reanalysis_required"
    if any(
        result.status == "failed" or result.failure_category == "business_assertion"
        for result in scenario_results
    ):
        return "failed"
    if unresolved_impact_item_ids or any(result.status != "passed" for result in scenario_results):
        return "blocked"
    return "passed"


def _failure_reasons(
    *,
    scenario_results: tuple[UiScenarioResultWrite, ...],
    unresolved_impact_item_ids: tuple[str, ...],
    out_of_scope_files: tuple[str, ...],
) -> tuple[str, ...]:
    values = {
        f"scenario:{result.scenario_id}:{result.failure_category}"
        for result in scenario_results
        if result.status != "passed"
    }
    values.update(f"unresolved_impact_item:{item_id}" for item_id in unresolved_impact_item_ids)
    values.update(f"out_of_scope_file:{path}" for path in out_of_scope_files)
    return tuple(sorted(values))


def _scenario_result_payload(result: UiScenarioResultWrite) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scenario_id": result.scenario_id,
        "status": result.status,
        "impact_item_refs": list(result.impact_item_refs),
        "evidence_refs": list(result.evidence_refs),
        "failure_category": result.failure_category,
    }
    if result.summary is not None:
        payload["summary"] = result.summary
    return payload


def _validate_unique_non_blank(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)) or any(not value.strip() for value in values):
        raise ValueError(f"{label} must be unique and non-blank")
