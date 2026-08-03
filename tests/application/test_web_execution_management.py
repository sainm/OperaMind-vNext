from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
from pytest import MonkeyPatch

from operamind.application.web_control_plane import (
    WebControlPlaneService,
    _public_test_case_proposal,
)


class RequestRepository:
    def get_change_request(self, request_id: str) -> dict[str, object]:
        return {
            "change_request_id": request_id,
            "project_id": "visiondemo",
            "analysis_case_id": "case-001",
        }

    def project_workspace_root(self, project_id: str) -> str:
        assert project_id == "visiondemo"
        return "."


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
            "test_plan": {
                "artifact_type": "TestPlan",
                "status": "ready",
                "test_cases": [{"title": "差戻し状態で検索する"}],
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
        return {"approval_grant_id": "grant-001"}

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
    def __init__(self, *, stale: bool = False) -> None:
        self.stale = stale

    def latest(self, request_id: str) -> dict[str, Any]:
        assert request_id == "change-001"
        return {
            "artifact_type": "ChangeClosureResult",
            "status": "blocked",
            "artifact_refs": ["ui-result-001", "edit-result-001"],
            "ui_status": "blocked",
            "business_coverage_percent": 100,
            "unresolved_items": ["UI verification result is missing"],
        }

    def latest_for_orchestration(self, orchestration_id: str) -> dict[str, Any]:
        assert orchestration_id == "orchestration-001"
        return self.latest("change-001")

    def latest_changed_line_coverage(self, **values: object) -> dict[str, Any]:
        assert values == {
            "project_id": "visiondemo",
            "analysis_case_id": "case-001",
            "orchestration_id": "orchestration-001",
        }
        return {
            "edit_result_id": "edit-result-002" if self.stale else "edit-result-001",
            "status": "missing",
            "coverage_percent": 0,
            "blocking_reasons": ["Changed-line coverage evidence is missing"],
        }


class TestCaseRevisionService:
    def state(self, request_id: str) -> dict[str, object]:
        assert request_id == "change-001"
        return {
            "latest": {"revision": {"stale_evidence_refs": ["evidence://external/ui-screen"]}},
            "history": [],
        }


class RevisingTestCaseService:
    def propose(self, **values: object) -> dict[str, object]:
        return {
            "state": "ready_for_confirmation",
            "proposal": _revision_proposal(str(values["instruction"])),
        }

    def prepare_ai_regeneration(self, **values: object) -> dict[str, object]:
        assert values["proposal_id"] == "proposal-001"
        assert values["selections"] == {}
        return {
            "proposal": _revision_proposal("期待結果を変更"),
            "operations": _revision_proposal("期待結果を変更")["operations"],
            "selections": {},
        }


class CopilotRevisionTaskService:
    published: ClassVar[list[object]] = []

    def __init__(self, **values: object) -> None:
        del values

    def publish(self, request: object) -> dict[str, object]:
        type(self).published.append(request)
        return {
            "task": {"coding_task_id": "copilot-revision-001"},
            "state": "pending_confirmation",
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
    assert result["test_plan"]["test_cases"][0]["title"] == "差戻し状態で検索する"  # type: ignore[index]
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
    assert screenshots[0]["content_url"].endswith("/screenshots/data-screen")
    assert service.screenshot_path(request_id="change-001", evidence_id="data-screen") == screenshot
    with pytest.raises(ValueError, match="does not exist"):
        service.screenshot_path(
            request_id="change-001",
            evidence_id="unsafe-screen",
        )


def test_management_does_not_expose_closure_from_an_older_edit_result(
    tmp_path: Path,
) -> None:
    result = _service(tmp_path, stale_closure=True).execution_management("change-001")

    assert result["change_closure"] is None
    assert result["changed_line_coverage"]["edit_result_id"] == "edit-result-002"  # type: ignore[index]
    assert result["controls"]["blocking_reason"] == (  # type: ignore[index]
        "Change Closure is stale for current Edit Result"
    )


def test_incomplete_business_coverage_disables_execution_and_direct_start(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    complete_bundle = OrchestrationRepository().latest_bundle("change-001")
    complete_bundle["coverage_report"] = {
        "artifact_type": "BusinessCoverageReport",
        "status": "failed",
        "coverage_percent": 50,
        "items": [],
    }
    service._orchestrations = type(  # type: ignore[attr-defined]
        "IncompleteCoverageRepository",
        (),
        {"latest_bundle": lambda _self, _request_id: complete_bundle},
    )()

    management = service.execution_management("change-001")

    assert management["controls"]["can_start"] is False  # type: ignore[index]
    assert management["controls"]["can_rerun"] is False  # type: ignore[index]
    assert management["controls"]["blocking_reason"] == (  # type: ignore[index]
        "Business coverage must be 100 before TestDataPlan or UI execution"
    )
    with pytest.raises(ValueError, match="Business coverage must be 100"):
        service.start_test_data_run(
            request_id="change-001",
            idempotency_key="blocked-run",
            actor="local-user",
        )


def test_test_case_revision_preview_hides_internal_ids_and_confirmation_restarts_downstream(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    service._connection = object()  # type: ignore[attr-defined]
    service._test_case_revisions = RevisingTestCaseService()  # type: ignore[attr-defined]
    CopilotRevisionTaskService.published = []
    monkeypatch.setattr(
        "operamind.application.web_control_plane.CopilotCodingTaskService",
        CopilotRevisionTaskService,
    )
    service.main_change_flow = lambda request_id: {"change_request_id": request_id}  # type: ignore[method-assign]

    preview = service.propose_test_case_revision(
        request_id="change-001",
        instruction="期待結果を変更",
        actor="local-user",
    )
    applied = service.confirm_test_case_revision(
        request_id="change-001",
        proposal_id="proposal-001",
        selections={},
        actor="local-user",
    )

    assert preview["proposal"] == _public_test_case_proposal(_revision_proposal("期待結果を変更"))
    assert "test_case_id" not in repr(preview)
    assert len(CopilotRevisionTaskService.published) == 1
    assert applied == {
        "state": "awaiting_copilot",
        "copilot_task": {
            "task": {"coding_task_id": "copilot-revision-001"},
            "state": "pending_confirmation",
            "attempt_number": None,
            "current_stage": None,
        },
        "flow": {"change_request_id": "change-001"},
    }


def _service(root: Path, *, stale_closure: bool = False) -> WebControlPlaneService:
    service = object.__new__(WebControlPlaneService)
    service._root = root  # type: ignore[attr-defined]
    service._repository = RequestRepository()  # type: ignore[attr-defined]
    service._artifacts = ArtifactRepository()  # type: ignore[attr-defined]
    service._orchestrations = OrchestrationRepository()  # type: ignore[attr-defined]
    service._test_data_runs = TestDataRepository()  # type: ignore[attr-defined]
    service._closures = ClosureRepository(stale=stale_closure)  # type: ignore[attr-defined]
    service._test_case_revisions = TestCaseRevisionService()  # type: ignore[attr-defined]
    service._case_execution_authorizations = (  # type: ignore[attr-defined]
        ExecutionAuthorizationRepository()
    )
    return service


def _revision_proposal(instruction: str) -> dict[str, Any]:
    operation = {
        "operation_id": "operation-001",
        "test_case_id": "case-001",
        "case_title": "差戻し状態で検索する",
        "field": "expected_results",
        "action": "replace",
        "summary_before": "期待結果: 1 件",
        "summary_after": "期待結果: 2 件",
    }
    return {
        "proposal_id": "proposal-001",
        "project_id": "visiondemo",
        "source_orchestration_id": "orchestration-001",
        "source_test_plan_id": "test-plan-001",
        "instruction": instruction,
        "analysis_status": "needs_confirmation",
        "operations": [operation],
        "ambiguities": [
            {
                "ambiguity_id": "ambiguity-001",
                "question": "どの対象に適用しますか?",
                "options": [
                    {
                        "option_id": "option-001",
                        "label": "差戻し一覧",
                        "operations": [operation],
                    }
                ],
            }
        ],
        "blocking_reasons": [],
    }
