from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from operamind.application.web_control_plane import WebControlPlaneService


class RequestRepository:
    def get_change_request(self, request_id: str) -> dict[str, object]:
        return {
            "change_request_id": request_id,
            "project_id": "visiondemo",
            "analysis_case_id": "case-001",
        }

    def evidence(self, *, project_id: str, case_id: str) -> dict[str, object]:
        assert (project_id, case_id) == ("visiondemo", "case-001")
        return {
            "command_results": [],
            "ui_evidence": [
                {
                    "evidence_id": "ui-screen",
                    "scenario_id": "expense-list",
                    "evidence_type": "screenshot",
                    "evidence_ref": "evidence://external/ui-screen",
                    "sha256": "b" * 64,
                }
            ],
        }

    def case_workspace(self, *, project_id: str, case_id: str) -> dict[str, object]:
        assert (project_id, case_id) == ("visiondemo", "case-001")
        return {"validation": {"id": "ui-result-001", "status": "blocked"}}


class ArtifactRepository:
    def get(self, artifact_id: str) -> dict[str, object] | None:
        assert artifact_id == "ui-result-001"
        return {
            "artifact_type": "UiVerificationResult",
            "status": "blocked",
            "scenario_results": [],
            "failure_reasons": ["UI verification result is missing"],
        }


class OrchestrationRepository:
    def latest_bundle(self, request_id: str) -> dict[str, Any]:
        assert request_id == "change-001"
        return {
            "orchestration": {
                "orchestration_id": "orchestration-001",
                "project_id": "visiondemo",
                "analysis_case_id": "case-001",
            },
            "test_data_plan": {
                "artifact_type": "TestDataPlan",
                "status": "ready",
                "generation_flows": [],
            },
            "coverage_report": {
                "artifact_type": "BusinessCoverageReport",
                "status": "passed",
                "coverage_percent": 100,
                "items": [],
            },
        }


class TestDataRepository:
    def latest_active_scope(self, **values: object) -> dict[str, str | None]:
        assert values["orchestration_id"] == "orchestration-001"
        return {"approval_grant_id": "grant-001", "base_url": None}

    def latest_for_orchestration(self, orchestration_id: str) -> dict[str, Any]:
        assert orchestration_id == "orchestration-001"
        return {
            "run_id": "run-001",
            "status": "passed",
            "result": {
                "artifact_type": "TestDataExecutionResult",
                "evidence": [
                    {
                        "evidence_id": "data-screen",
                        "flow_id": "flow-001",
                        "step_id": "verify-list",
                        "phase": "setup",
                        "evidence_type": "screenshot",
                        "evidence_ref": "evidence/data-screen.png",
                        "content_digest": "a" * 64,
                    },
                    {
                        "evidence_id": "unsafe-screen",
                        "flow_id": "flow-001",
                        "step_id": "unsafe",
                        "phase": "setup",
                        "evidence_type": "screenshot",
                        "evidence_ref": "../outside.png",
                        "content_digest": "c" * 64,
                    },
                ],
            },
        }


class ClosureRepository:
    def latest(self, request_id: str) -> dict[str, Any]:
        assert request_id == "change-001"
        return {
            "artifact_type": "ChangeClosureResult",
            "status": "blocked",
            "artifact_refs": ["ui-result-001"],
            "ui_status": "blocked",
            "business_coverage_percent": 100,
            "unresolved_items": ["UI verification result is missing"],
        }

    def latest_for_orchestration(self, orchestration_id: str) -> dict[str, Any]:
        assert orchestration_id == "orchestration-001"
        return self.latest("change-001")


class TestCaseRevisionService:
    def state(self, request_id: str) -> dict[str, object]:
        assert request_id == "change-001"
        return {
            "latest": {"revision": {"stale_evidence_refs": ["evidence://external/ui-screen"]}},
            "history": [],
        }


class ExecutionAuthorizationRepository:
    def state(self, **values: object) -> dict[str, object]:
        assert values["target_orchestration_id"] == "orchestration-001"
        return {
            "authorized": True,
            "status": "original",
            "approval_grant_id": "grant-001",
            "authorization_id": None,
            "confirmed_by": None,
            "blocking_reason": None,
        }


def test_management_combines_plan_run_coverage_closure_and_safe_screenshots(
    tmp_path: Path,
) -> None:
    screenshot = tmp_path / "evidence" / "data-screen.png"
    screenshot.parent.mkdir()
    screenshot.write_bytes(b"image")
    service = _service(tmp_path)

    result = service.execution_management("change-001")

    assert result["orchestration_id"] == "orchestration-001"
    assert result["test_data_execution"]["status"] == "passed"  # type: ignore[index]
    assert result["business_coverage"]["coverage_percent"] == 100  # type: ignore[index]
    assert result["change_closure"]["status"] == "blocked"  # type: ignore[index]
    failure_management = result["failure_management"]
    assert isinstance(failure_management, dict)
    assert {value["category"] for value in failure_management["failures"]} == {
        "ui",
        "closure",
    }
    assert failure_management["actions"]["can_rerun"] is True
    assert failure_management["actions"]["can_recover"] is False
    screenshots = result["screenshots"]
    assert isinstance(screenshots, list)
    assert [(item["evidence_id"], item["available"]) for item in screenshots] == [
        ("data-screen", True),
        ("unsafe-screen", False),
    ]
    assert screenshots[0]["content_url"].endswith("/screenshots/test_data/data-screen")
    assert (
        service.screenshot_path(
            request_id="change-001", origin="test_data", evidence_id="data-screen"
        )
        == screenshot
    )
    with pytest.raises(ValueError, match="does not exist"):
        service.screenshot_path(
            request_id="change-001",
            origin="test_data",
            evidence_id="unsafe-screen",
        )


def _service(root: Path) -> WebControlPlaneService:
    service = object.__new__(WebControlPlaneService)
    service._root = root  # type: ignore[attr-defined]
    service._repository = RequestRepository()  # type: ignore[attr-defined]
    service._artifacts = ArtifactRepository()  # type: ignore[attr-defined]
    service._orchestrations = OrchestrationRepository()  # type: ignore[attr-defined]
    service._test_data_runs = TestDataRepository()  # type: ignore[attr-defined]
    service._closures = ClosureRepository()  # type: ignore[attr-defined]
    service._test_case_revisions = TestCaseRevisionService()  # type: ignore[attr-defined]
    service._case_execution_authorizations = (  # type: ignore[attr-defined]
        ExecutionAuthorizationRepository()
    )
    return service
