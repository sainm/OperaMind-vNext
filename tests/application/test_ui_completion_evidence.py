import hashlib

import pytest

from operamind.application.ui_verification import _validate_results
from operamind.infrastructure.postgres import (
    UiExecutionEvidenceWrite,
    UiScenarioResultWrite,
    VerificationScenarioWrite,
)
from operamind.infrastructure.postgres.ui_verification_repository import _validate_scenario


def _evidence(evidence_id: str, evidence_type: str) -> UiExecutionEvidenceWrite:
    return UiExecutionEvidenceWrite(
        evidence_id=evidence_id,
        scenario_id="scenario-1",
        evidence_type=evidence_type,
        evidence_ref=f"evidence://run-1/{evidence_id}",
        content_digest=hashlib.sha256(evidence_id.encode()).hexdigest(),
        sanitized=True,
    )


def test_passed_scenario_requires_every_fixed_evidence_type() -> None:
    evidence = (_evidence("screenshot-1", "screenshot"), _evidence("assertion-1", "assertion"))
    result = UiScenarioResultWrite(
        scenario_id="scenario-1",
        status="passed",
        impact_item_refs=("impact-1",),
        evidence_refs=("screenshot-1", "assertion-1"),
        failure_category="none",
    )

    with pytest.raises(ValueError, match="step_log"):
        _validate_results(
            scenario_refs=("scenario-1",),
            scenario_evidence_requirements=(("scenario-1", ("step_log",)),),
            impact_item_ids=("impact-1",),
            scenario_results=(result,),
            evidence=evidence,
        )


def test_completion_rejects_unreferenced_extra_evidence() -> None:
    evidence = (
        _evidence("screenshot-1", "screenshot"),
        _evidence("assertion-1", "assertion"),
        _evidence("step-log-1", "step_log"),
    )
    result = UiScenarioResultWrite(
        scenario_id="scenario-1",
        status="passed",
        impact_item_refs=("impact-1",),
        evidence_refs=("screenshot-1", "assertion-1"),
        failure_category="none",
    )

    with pytest.raises(ValueError, match="Every UI Evidence item"):
        _validate_results(
            scenario_refs=("scenario-1",),
            scenario_evidence_requirements=(("scenario-1", ("screenshot", "assertion")),),
            impact_item_ids=("impact-1",),
            scenario_results=(result,),
            evidence=evidence,
        )


def test_approved_scenario_rejects_unsupported_evidence_requirement() -> None:
    scenario = VerificationScenarioWrite(
        scenario_version_id="scenario-version-1",
        project_id="project-1",
        scenario_id="scenario-1",
        scenario_version="1.0.0",
        title="Expense status filter",
        preconditions=(),
        steps=("Open the expense page",),
        expected_visible_results=("Every expected row is visible",),
        evidence_requirements=("video",),
        trigger_path="/expenses",
        data_recipe_ref=None,
        review_status="approved",
        activate=True,
    )

    with pytest.raises(ValueError, match="unsupported"):
        _validate_scenario(scenario)
