import hashlib

from operamind.application.browser_execution import _output_matches_manifest
from operamind.domain import BrowserExecutionManifest
from operamind.infrastructure.browser import (
    BrowserExecutionOutput,
    BrowserScenarioOutcome,
    StoredBrowserEvidence,
)


def _manifest() -> BrowserExecutionManifest:
    return BrowserExecutionManifest.from_dict(
        {
            "manifest_id": "manifest-1",
            "plan_id": "plan-1",
            "project_id": "project-1",
            "browser": {
                "name": "chromium",
                "channel": "chrome",
                "headless": True,
                "viewport": {"width": 1280, "height": 720},
            },
            "review_status": "approved",
            "reviewed_by": "qa@example.invalid",
            "scenarios": [
                {
                    "scenario_id": "scenario-1",
                    "trigger_path": "/expenses",
                    "impact_item_refs": ["impact-1"],
                    "actions": [],
                    "assertions": [
                        {
                            "assertion_id": "expense-list-visible",
                            "kind": "visible",
                            "locator": {
                                "strategy": "test_id",
                                "value": "expense-list",
                                "exact": True,
                            },
                            "failure_category": "business_assertion",
                        }
                    ],
                    "redaction_locators": [],
                    "preflight_assertions": [],
                }
            ],
        }
    )


def _evidence(evidence_type: str) -> StoredBrowserEvidence:
    evidence_id = f"evidence-{evidence_type}"
    return StoredBrowserEvidence(
        evidence_id=evidence_id,
        scenario_id="scenario-1",
        evidence_type=evidence_type,
        evidence_ref=f"evidence://run-1/{evidence_id}",
        content_digest=hashlib.sha256(evidence_id.encode()).hexdigest(),
    )


def _passed_output(*evidence_types: str) -> BrowserExecutionOutput:
    evidence = tuple(_evidence(value) for value in evidence_types)
    return BrowserExecutionOutput(
        scenario_results=(
            BrowserScenarioOutcome(
                scenario_id="scenario-1",
                status="passed",
                impact_item_refs=("impact-1",),
                evidence_refs=tuple(item.evidence_id for item in evidence),
                failure_category="none",
                summary="The expense list is visible.",
            ),
        ),
        evidence=evidence,
    )


def test_browser_output_requires_plan_fixed_evidence_types() -> None:
    requirements = (("scenario-1", ("screenshot", "assertion", "step_log")),)

    assert not _output_matches_manifest(
        _manifest(),
        _passed_output("screenshot", "assertion"),
        requirements,
    )
    assert _output_matches_manifest(
        _manifest(),
        _passed_output("screenshot", "assertion", "step_log"),
        requirements,
    )


def test_browser_output_rejects_misaligned_requirement_scope() -> None:
    assert not _output_matches_manifest(
        _manifest(),
        _passed_output("screenshot", "assertion"),
        (("different-scenario", ("screenshot", "assertion")),),
    )
