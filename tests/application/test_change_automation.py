from operamind.application.change_automation import decide_change_automation


def test_natural_language_run_waits_for_copilot_document_generation() -> None:
    decision = decide_change_automation(
        request=_request(case_id=None, review="pending"),
        diff={"total": 0},
        workspace=None,
        has_orchestration=False,
        execution=None,
    )

    assert decision.stage == "requirement_confirmation"
    assert decision.status == "waiting"
    assert decision.next_action == "confirm_requirement"


def test_confirmed_requirement_waits_for_rag_document_confirmation() -> None:
    decision = decide_change_automation(
        request=_request(case_id="case-1", review="pending"),
        diff={"total": 0},
        workspace=None,
        has_orchestration=False,
        execution=None,
        confirmations={"requirement": "confirmed"},
        rag_discovery=_rag_discovery(),
    )

    assert decision.stage == "rag_document_confirmation"
    assert decision.next_action == "confirm_rag_documents"


def test_confirmed_impact_prepares_code_scope_before_planning() -> None:
    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=_workspace(report="confirmed", confirmation="confirmation-1"),
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.stage == "execution_approval"
    assert decision.status == "running"
    assert decision.next_action == "provision_execution_scope"


def test_high_confidence_document_diff_still_requires_human_confirmation() -> None:
    decision = decide_change_automation(
        request=_request(case_id="case-1", review="pending"),
        diff={
            "total": 1,
            "changes": [
                {
                    "confidence": "high",
                    "review_status": "accepted",
                    "unknowns": [],
                }
            ],
        },
        workspace=None,
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.status == "waiting"
    assert decision.next_action == "confirm_document_diff"


def test_low_confidence_document_diff_still_requires_user_confirmation() -> None:
    decision = decide_change_automation(
        request=_request(case_id="case-1", review="pending"),
        diff={
            "total": 1,
            "changes": [
                {
                    "confidence": "low",
                    "review_status": "needs_review",
                    "unknowns": ["状態の意味を確認する"],
                }
            ],
        },
        workspace=None,
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.status == "waiting"
    assert decision.next_action == "confirm_document_diff"


def test_deterministic_impact_still_requires_human_confirmation() -> None:
    workspace = _workspace(report="awaiting_confirmation", confirmation=None)
    workspace["impact_artifact"] = {
        "status": "awaiting_confirmation",
        "ui_impact_status": "impacted",
        "blocking_unknowns": [],
        "items": [
            {
                "impact_level": "high",
                "requires_confirmation": False,
                "unknowns": [],
            }
        ],
    }
    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.status == "waiting"
    assert decision.next_action == "confirm_code_scope"


def test_execution_scope_is_prepared_without_a_test_plan() -> None:
    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=_workspace(report="confirmed", confirmation="confirmation-1"),
        has_orchestration=False,
        execution={},
        **_confirmed_entry(),
    )

    assert decision.stage == "execution_approval"
    assert decision.status == "running"
    assert decision.next_action == "provision_execution_scope"


def test_committed_code_change_advances_to_copilot_test_planning() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = _committed_edit_result()
    workspace["approval_grant"] = {"id": "grant-1"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.stage == "planning"
    assert decision.status == "running"
    assert decision.next_action == "generate_orchestration"


def test_passed_closure_completes_the_run() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = _committed_edit_result()
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
        confirmations={
            "requirement": "confirmed",
            "rag_documents": "confirmed",
            "test_plan": "confirmed",
            "ui_test": "confirmed",
            "final_report": "confirmed",
        },
        rag_discovery=_rag_discovery(),
    )

    assert decision.stage == "completed"
    assert decision.status == "completed"
    assert decision.next_action is None


def test_generated_test_plan_waits_for_human_confirmation() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = _committed_edit_result()
    workspace["approval_grant"] = {"id": "grant-1"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=True,
        execution={"test_plan": {"status": "ready"}},
        **_confirmed_entry(),
    )

    assert decision.stage == "test_plan_confirmation"
    assert decision.next_action == "confirm_test_plan"


def test_passed_test_data_waits_for_ui_test_confirmation() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = _committed_edit_result()
    workspace["approval_grant"] = {"id": "grant-1"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=True,
        execution={"test_data_execution": {"status": "passed"}},
        confirmations={
            "requirement": "confirmed",
            "rag_documents": "confirmed",
            "test_plan": "confirmed",
        },
        rag_discovery=_rag_discovery(),
    )

    assert decision.stage == "ui_test_confirmation"
    assert decision.next_action == "confirm_ui_test"


def test_passed_closure_waits_for_final_report_confirmation() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = _committed_edit_result()
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
        confirmations={
            "requirement": "confirmed",
            "rag_documents": "confirmed",
            "test_plan": "confirmed",
            "ui_test": "confirmed",
        },
        rag_discovery=_rag_discovery(),
    )

    assert decision.stage == "final_report_confirmation"
    assert decision.next_action == "confirm_final_report"


def test_working_in_scope_diff_waits_for_copilot_compile_and_test() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = {
        "id": "edit-working",
        "validation_mode": "working",
        "status": "in_scope",
        "tests_passed": None,
        "command_evidence_status": "not_applicable",
    }
    workspace["approval_grant"] = {"id": "grant-1"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.stage == "code_change"
    assert decision.status == "waiting"
    assert decision.next_action == "apply_code_change_with_copilot"
    assert "TestPlan" in decision.message


def test_committed_result_without_verified_commands_is_blocked() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = {
        **_committed_edit_result(),
        "command_evidence_status": "failed",
    }
    workspace["approval_grant"] = {"id": "grant-1"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.stage == "code_change"
    assert decision.status == "blocked"


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


def _committed_edit_result() -> dict[str, object]:
    return {
        "id": "edit-1",
        "validation_mode": "committed",
        "status": "in_scope",
        "tests_passed": True,
        "command_evidence_status": "verified",
    }


def _rag_discovery() -> dict[str, object]:
    return {
        "status": "ready",
        "candidates": [{"document_id": "document-1", "document_ref": "file:///design.xlsx"}],
    }


def _confirmed_entry() -> dict[str, object]:
    return {
        "confirmations": {"requirement": "confirmed", "rag_documents": "confirmed"},
        "rag_discovery": _rag_discovery(),
    }
