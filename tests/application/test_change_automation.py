from operamind.application.change_automation import decide_change_automation


def test_natural_language_run_waits_for_copilot_document_generation() -> None:
    decision = decide_change_automation(
        request=_request(case_id=None, review="pending"),
        diff={"total": 0},
        workspace=None,
        has_orchestration=False,
        execution=None,
    )

    assert decision.stage == "document_generation"
    assert decision.status == "waiting"
    assert decision.next_action == "prepare_document_with_copilot"


def test_confirmed_impact_advances_to_deterministic_planning() -> None:
    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=_workspace(report="confirmed", confirmation="confirmation-1"),
        has_orchestration=False,
        execution=None,
    )

    assert decision.stage == "planning"
    assert decision.status == "running"
    assert decision.next_action == "generate_orchestration"


def test_generated_plan_waits_at_external_code_change_boundary() -> None:
    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=_workspace(report="confirmed", confirmation="confirmation-1"),
        has_orchestration=True,
        execution={},
    )

    assert decision.stage == "code_change"
    assert decision.status == "waiting"
    assert decision.next_action == "apply_code_change_with_copilot"


def test_passed_closure_completes_the_run() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = {"id": "edit-1", "status": "succeeded"}
    workspace["approval_grant"] = {"id": "grant-1"}
    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=True,
        execution={
            "test_data_execution": {"status": "passed"},
            "change_closure": {"status": "passed"},
        },
    )

    assert decision.stage == "completed"
    assert decision.status == "completed"
    assert decision.next_action is None


def _request(*, case_id: str | None, review: str) -> dict[str, object]:
    return {
        "analysis_case_id": case_id,
        "artifact": {"ambiguity_status": "clear", "confirmation_required": False},
        "document_review": {"status": review},
    }


def _workspace(*, report: str, confirmation: str | None) -> dict[str, object]:
    return {
        "impact_report": {"id": "impact-1", "status": report},
        "confirmation": {"id": confirmation},
        "edit_result": {"id": None, "status": None},
        "approval_grant": {"id": None},
    }
