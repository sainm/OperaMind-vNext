import pytest

from operamind.application.change_automation import (
    CHANGE_FLOW_STATE_MACHINE,
    ChangeAutomationDecision,
    decide_change_automation,
)


def test_state_machine_projects_one_shared_stage_and_confirmation_checkpoint() -> None:
    projection = CHANGE_FLOW_STATE_MACHINE.project(
        {
            "current_stage": "impact_confirmation",
            "status": "waiting",
            "next_action": "confirm_code_scope",
        }
    )

    assert projection.internal_stage == "impact_confirmation"
    assert projection.public_stage == "code_scope"
    assert projection.confirmation_checkpoint == "code_scope"
    assert CHANGE_FLOW_STATE_MACHINE.confirmation_checkpoint("confirm_code_scope") == (
        "code_scope"
    )


def test_state_machine_rejects_unknown_persisted_and_invalid_terminal_states() -> None:
    with pytest.raises(ValueError, match="Unknown persisted Change Flow stage"):
        CHANGE_FLOW_STATE_MACHINE.project(
            {"current_stage": "invented", "status": "waiting", "next_action": None}
        )
    with pytest.raises(ValueError, match="must be terminal"):
        CHANGE_FLOW_STATE_MACHINE.validate_decision(
            ChangeAutomationDecision(
                stage="completed",
                status="completed",
                next_action="refresh",
                blocking_reason=None,
                message="invalid",
            )
        )
    with pytest.raises(ValueError, match="stage and status"):
        CHANGE_FLOW_STATE_MACHINE.validate_decision(
            ChangeAutomationDecision(
                stage="code_change",
                status="completed",
                next_action=None,
                blocking_reason=None,
                message="invalid",
            )
        )
    with pytest.raises(ValueError, match="stage and status"):
        CHANGE_FLOW_STATE_MACHINE.project(
            {
                "current_stage": "code_change",
                "status": "completed",
                "next_action": None,
            }
        )
    with pytest.raises(ValueError, match="invalid for stage"):
        CHANGE_FLOW_STATE_MACHINE.project(
            {
                "current_stage": "impact_confirmation",
                "status": "waiting",
                "next_action": "start_test_data_execution",
            }
        )


@pytest.mark.parametrize(
    "decision",
    [
        ChangeAutomationDecision("invented", "waiting", None, None, "invalid"),
        ChangeAutomationDecision("code_change", "invented", None, None, "invalid"),
        ChangeAutomationDecision("code_change", "blocked", None, "blocked", "invalid"),
        ChangeAutomationDecision(
            "requirement_confirmation",
            "running",
            "confirm_requirement",
            None,
            "invalid",
        ),
    ],
)
def test_state_machine_rejects_invalid_decision_invariants(
    decision: ChangeAutomationDecision,
) -> None:
    with pytest.raises(ValueError):
        CHANGE_FLOW_STATE_MACHINE.validate_decision(decision)


def test_state_machine_normalizes_public_status_and_keeps_earlier_blocker_fail_closed() -> None:
    automation = {
        "current_stage": "test_plan_confirmation",
        "status": "waiting",
        "next_action": "confirm_test_plan",
    }

    assert CHANGE_FLOW_STATE_MACHINE.normalize_public_stage_statuses(
        automation=automation,
        evidence_statuses=(
            "completed",
            "completed",
            "completed",
            "running",
            "waiting",
            "waiting",
        ),
    ) == (
        "completed",
        "completed",
        "completed",
        "completed",
        "waiting",
        "waiting",
    )
    assert CHANGE_FLOW_STATE_MACHINE.normalize_public_stage_statuses(
        automation=automation,
        evidence_statuses=(
            "completed",
            "completed",
            "blocked",
            "completed",
            "waiting",
            "waiting",
        ),
    ) == (
        "completed",
        "completed",
        "blocked",
        "waiting",
        "waiting",
        "waiting",
    )


def test_intermediate_completed_internal_stage_is_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="stage and status"):
        CHANGE_FLOW_STATE_MACHINE.normalize_public_stage_statuses(
            automation={
                "current_stage": "planning",
                "status": "completed",
                "next_action": "inspect_generated_plan",
            },
            evidence_statuses=(
                "completed",
                "completed",
                "completed",
                "completed",
                "waiting",
                "waiting",
            ),
        )


def test_state_machine_is_the_single_copilot_and_coordinator_gate() -> None:
    assert CHANGE_FLOW_STATE_MACHINE.allows_copilot_stage(
        task_stage="document_change",
        automation_stage="document_generation",
    )
    assert not CHANGE_FLOW_STATE_MACHINE.allows_copilot_stage(
        task_stage="document_change",
        automation_stage="document_confirmation",
    )
    assert CHANGE_FLOW_STATE_MACHINE.is_ready_for_action(
        {
            "run": {
                "current_stage": "test_data_execution",
                "status": "waiting",
                "next_action": "start_test_data_execution",
            }
        },
        action="start_test_data_execution",
    )


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


def test_replacement_packet_rebinds_a_follow_up_execution_task() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-2")
    workspace["edit_packet"] = {"id": "packet-2", "editable_files": []}
    workspace["approval_grant"] = {"id": "grant-2"}
    workspace["copilot_task"] = {
        "state": "completed",
        "current_stage": "ui_validation",
        "execution_scope": {
            "bound": True,
            "edit_packet_id": "packet-1",
            "approval_grant_id": "grant-1",
        },
    }

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.stage == "execution_approval"
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


def test_rejected_document_diff_waits_for_copilot_revision_task() -> None:
    workspace = _workspace(report="awaiting_confirmation", confirmation=None)
    workspace["copilot_task"] = {"state": "in_progress", "current_stage": "document_change"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="revision_requested"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.stage == "document_revision"
    assert decision.next_action == "revise_document_with_copilot"


def test_revised_document_diff_returns_to_human_confirmation() -> None:
    workspace = _workspace(report="awaiting_confirmation", confirmation=None)
    workspace["copilot_task"] = {"state": "in_progress", "current_stage": "code_scope"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="revision_requested"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=False,
        execution=None,
        **_confirmed_entry(),
    )

    assert decision.stage == "document_confirmation"
    assert decision.next_action == "confirm_document_diff"


def test_revised_document_waits_for_current_task_code_scope_instead_of_reusing_old_impact() -> None:
    workspace = _workspace(report="awaiting_confirmation", confirmation=None)
    workspace["copilot_task"] = {
        "task": {"coding_task_id": "revision-task-2"},
        "state": "in_progress",
        "current_stage": "code_scope",
        "events": [
            {
                "event_type": "outputs_recorded",
                "payload": {
                    "output_stage": "document_change",
                    "document_change_refs": ["revised-document-change"],
                },
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

    assert decision.stage == "impact_analysis"
    assert decision.next_action == "analyze_code_scope_with_copilot"
    assert "以前の Task" in decision.message


def test_current_task_code_scope_can_reach_impact_confirmation() -> None:
    workspace = _workspace(report="awaiting_confirmation", confirmation=None)
    workspace["copilot_task"] = {
        "task": {"coding_task_id": "revision-task-2"},
        "state": "in_progress",
        "current_stage": "code_scope",
        "events": [
            {
                "event_type": "outputs_recorded",
                "payload": {
                    "output_stage": "code_scope",
                    "impact_report_id": "impact-1",
                },
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

    assert decision.stage == "impact_confirmation"
    assert decision.next_action == "confirm_code_scope"


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


def test_existing_grant_is_rebound_to_the_current_copilot_task() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["approval_grant"] = {"id": "grant-1"}
    workspace["copilot_task"] = {
        "state": "in_progress",
        "current_stage": "code_scope",
        "execution_scope": {"bound": False},
    }

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=False,
        execution=None,
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


def test_committed_verification_only_result_advances_to_orchestration() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_packet"] = {"editable_files": []}
    workspace["edit_result"] = {
        **_committed_edit_result(),
        "status": "no_changes",
        "changed_line_coverage_status": "not_required",
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

    assert decision.stage == "planning"
    assert decision.status == "running"
    assert decision.next_action == "generate_orchestration"


def test_committed_no_change_result_is_blocked_when_files_were_editable() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = {
        **_committed_edit_result(),
        "status": "no_changes",
        "changed_line_coverage_status": "not_required",
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
            "business_coverage": _passed_business_coverage(),
            "test_data_execution": _passed_data_execution(),
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
        execution={
            "test_plan": {"status": "ready"},
            "business_coverage": _passed_business_coverage(),
        },
        **_confirmed_entry(),
    )

    assert decision.stage == "test_plan_confirmation"
    assert decision.next_action == "confirm_test_plan"


def test_confirmed_ui_test_plan_does_not_require_a_duplicate_execution_confirmation() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = _committed_edit_result()
    workspace["approval_grant"] = {"id": "grant-1"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=True,
        execution={
            "business_coverage": _passed_business_coverage(),
            "test_data_execution": _passed_data_execution(),
        },
        confirmations={
            "requirement": "confirmed",
            "rag_documents": "confirmed",
            "test_plan": "confirmed",
        },
        rag_discovery=_rag_discovery(),
    )

    assert decision.stage == "ui_verification"
    assert decision.next_action == "run_ui_verification"


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
            "business_coverage": _passed_business_coverage(),
            "test_data_execution": _passed_data_execution(),
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


def test_incomplete_business_coverage_never_reaches_human_test_plan_confirmation() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = _committed_edit_result()
    workspace["approval_grant"] = {"id": "grant-1"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=True,
        execution={
            "business_coverage": {
                "status": "failed",
                "coverage_percent": 75,
            }
        },
        **_confirmed_entry(),
    )

    assert decision.stage == "planning"
    assert decision.status == "blocked"
    assert decision.next_action == "resolve_blocker"
    assert "100%" in decision.message


def test_incomplete_executed_data_coverage_never_reaches_ui_verification() -> None:
    workspace = _workspace(report="confirmed", confirmation="confirmation-1")
    workspace["edit_result"] = _committed_edit_result()
    workspace["approval_grant"] = {"id": "grant-1"}

    decision = decide_change_automation(
        request=_request(case_id="case-1", review="confirmed"),
        diff={"total": 1},
        workspace=workspace,
        has_orchestration=True,
        execution={
            "business_coverage": _passed_business_coverage(),
            "test_data_execution": {
                "status": "passed",
                "result": {
                    "data_coverage": {
                        "status": "failed",
                        "coverage_percent": 99,
                    }
                },
            },
        },
        confirmations={
            "requirement": "confirmed",
            "rag_documents": "confirmed",
            "test_plan": "confirmed",
        },
        rag_discovery=_rag_discovery(),
    )

    assert decision.stage == "test_data_execution"
    assert decision.status == "blocked"
    assert decision.next_action == "resolve_blocker"
    assert "100%" in decision.message


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
        "copilot_task": {
            "state": "completed",
            "current_stage": "ui_validation",
            "execution_scope": {"bound": True},
        },
    }


def _committed_edit_result() -> dict[str, object]:
    return {
        "id": "edit-1",
        "validation_mode": "committed",
        "status": "in_scope",
        "tests_passed": True,
        "command_evidence_status": "verified",
        "changed_line_coverage_status": "passed",
    }


def _passed_business_coverage() -> dict[str, object]:
    return {"status": "passed", "coverage_percent": 100}


def _passed_data_execution() -> dict[str, object]:
    return {
        "status": "passed",
        "result": {
            "data_coverage": {"status": "passed", "coverage_percent": 100}
        },
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
