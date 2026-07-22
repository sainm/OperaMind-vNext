"""Bind approved browser manifests to UI Runs and final verification closure."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

from operamind.application.ui_verification import (
    UiVerificationService,
    UiVerificationServiceResult,
)
from operamind.contracts import ContractCatalog
from operamind.domain import BrowserExecutionManifest
from operamind.infrastructure.browser import (
    BrowserExecutionOutput,
    BrowserExecutor,
    BrowserScenarioOutcome,
)
from operamind.infrastructure.postgres import (
    UI_EVIDENCE_TYPES,
    BrowserManifestRecord,
    UiBrowserManifestRepository,
    UiExecutionEvidenceWrite,
    UiExecutionRunRecord,
    UiScenarioResultWrite,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RESULT_STATUSES = frozenset({"passed", "failed", "blocked", "skipped"})
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


class BrowserExecutionRuntimeError(RuntimeError):
    """Raised after an unexpected Executor failure has been safely recorded as blocked."""


@dataclass(frozen=True, slots=True)
class BrowserExecutionRequest:
    project_id: str
    plan_id: str
    manifest_id: str
    run_id: str
    verification_result_id: str
    approval_grant_id: str
    storage_state: Path | None = None

    def __post_init__(self) -> None:
        required = (
            self.project_id,
            self.plan_id,
            self.manifest_id,
            self.run_id,
            self.verification_result_id,
            self.approval_grant_id,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Browser Execution request fields must not be blank")


@dataclass(frozen=True, slots=True)
class BrowserExecutionServiceResult:
    run: UiExecutionRunRecord
    verification: UiVerificationServiceResult


class BrowserExecutionService:
    """Start one ready Plan, execute its approved DSL, and atomically close the Run."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        executor: BrowserExecutor | None = None,
    ) -> None:
        self._manifests = UiBrowserManifestRepository(connection)
        self._verification = UiVerificationService(connection=connection, contracts=contracts)
        self._executor = executor

    def register_manifest(self, manifest: BrowserExecutionManifest) -> BrowserManifestRecord:
        return self._manifests.store(manifest)

    def execute(self, request: BrowserExecutionRequest) -> BrowserExecutionServiceResult:
        if self._executor is None:
            raise RuntimeError("Browser Execution Service has no Executor")
        approved = self._manifests.load_approved(
            project_id=request.project_id,
            plan_id=request.plan_id,
        )
        if approved.manifest.manifest_id != request.manifest_id:
            raise ValueError("Requested Browser Manifest is not the approved Plan Manifest")
        storage_state = _validate_storage_state(request.storage_state)
        run = self._verification.start_run(
            project_id=request.project_id,
            plan_id=request.plan_id,
            run_id=request.run_id,
            approval_grant_id=request.approval_grant_id,
        )
        if not run.created:
            raise ValueError("Browser Executor requires a new UI Run ID")
        execution_error: Exception | None = None
        try:
            output = self._executor.execute(
                manifest=approved.manifest,
                base_url=approved.base_url,
                run_id=request.run_id,
                storage_state=storage_state,
            )
            if not _output_matches_manifest(
                approved.manifest,
                output,
                approved.scenario_evidence_requirements,
            ):
                output = _blocked_output(approved.manifest)
        except Exception as error:
            execution_error = error
            output = _blocked_output(approved.manifest)
        verification = self._verification.complete_run(
            verification_result_id=request.verification_result_id,
            project_id=request.project_id,
            run_id=request.run_id,
            scenario_results=tuple(
                UiScenarioResultWrite(
                    scenario_id=result.scenario_id,
                    status=result.status,
                    impact_item_refs=result.impact_item_refs,
                    evidence_refs=result.evidence_refs,
                    failure_category=result.failure_category,
                    summary=result.summary,
                )
                for result in output.scenario_results
            ),
            evidence=tuple(
                UiExecutionEvidenceWrite(
                    evidence_id=item.evidence_id,
                    scenario_id=item.scenario_id,
                    evidence_type=item.evidence_type,
                    evidence_ref=item.evidence_ref,
                    content_digest=item.content_digest,
                    sanitized=item.sanitized,
                )
                for item in output.evidence
            ),
        )
        result = BrowserExecutionServiceResult(run=run, verification=verification)
        if execution_error is not None:
            raise BrowserExecutionRuntimeError(
                "Browser Executor failed; the UI Run was recorded as blocked"
            ) from execution_error
        return result


def _validate_storage_state(path: Path | None) -> Path | None:
    if path is None:
        return None
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Browser storage_state must be a file")
    return resolved


def _output_matches_manifest(
    manifest: BrowserExecutionManifest,
    output: BrowserExecutionOutput,
    scenario_evidence_requirements: tuple[tuple[str, tuple[str, ...]], ...],
) -> bool:
    expected = {scenario.scenario_id: scenario for scenario in manifest.scenarios}
    requirement_ids = [value[0] for value in scenario_evidence_requirements]
    requirements_by_scenario = dict(scenario_evidence_requirements)
    if (
        len(requirement_ids) != len(set(requirement_ids))
        or set(requirement_ids) != set(expected)
        or any(
            not requirements
            or len(requirements) != len(set(requirements))
            or not set(requirements).issubset(UI_EVIDENCE_TYPES)
            for requirements in requirements_by_scenario.values()
        )
    ):
        return False
    actual_ids = [result.scenario_id for result in output.scenario_results]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected):
        return False
    evidence_by_id = {item.evidence_id: item for item in output.evidence}
    evidence_refs = {item.evidence_ref for item in output.evidence}
    if len(evidence_by_id) != len(output.evidence) or any(
        not item.sanitized
        or not item.evidence_id.strip()
        or not item.evidence_ref.strip()
        or item.evidence_type not in UI_EVIDENCE_TYPES
        or _SHA256.fullmatch(item.content_digest) is None
        for item in output.evidence
    ):
        return False
    if len(evidence_refs) != len(output.evidence):
        return False
    referenced: set[str] = set()
    for result in output.scenario_results:
        expected_items = set(expected[result.scenario_id].impact_item_refs)
        if (
            len(result.impact_item_refs) != len(set(result.impact_item_refs))
            or set(result.impact_item_refs) != expected_items
            or len(result.evidence_refs) != len(set(result.evidence_refs))
            or result.status not in _RESULT_STATUSES
            or result.failure_category not in _FAILURE_CATEGORIES
            or (result.status == "passed") != (result.failure_category == "none")
        ):
            return False
        result_evidence_types: set[str] = set()
        for evidence_id in result.evidence_refs:
            item = evidence_by_id.get(evidence_id)
            if item is None or item.scenario_id != result.scenario_id:
                return False
            referenced.add(evidence_id)
            result_evidence_types.add(item.evidence_type)
        if result.status == "passed":
            required_types = {
                "screenshot",
                "assertion",
                *requirements_by_scenario[result.scenario_id],
            }
            if not required_types.issubset(result_evidence_types):
                return False
    return referenced == set(evidence_by_id)


def _blocked_output(manifest: BrowserExecutionManifest) -> BrowserExecutionOutput:
    return BrowserExecutionOutput(
        scenario_results=tuple(
            BrowserScenarioOutcome(
                scenario_id=scenario.scenario_id,
                status="blocked",
                impact_item_refs=scenario.impact_item_refs,
                evidence_refs=(),
                failure_category="blocked",
                summary="Browser Executor failed to produce trusted complete Evidence.",
            )
            for scenario in manifest.scenarios
        ),
        evidence=(),
    )
