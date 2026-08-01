from dataclasses import replace

import pytest

from operamind.application.change_automation import ChangeAutomationDecision
from operamind.application.orchestration_task import (
    ACTION_POLICIES,
    EXECUTOR_KINDS,
    ORCHESTRATION_TASK_PROTOCOL_VERSION,
    OrchestrationSchedulingPolicy,
    build_orchestration_task,
    parse_orchestration_scheduling_policy,
    validate_orchestration_result_evidence,
)

EXPECTED_CHANGE_AUTOMATION_ACTIONS = {
    "confirm_requirement",
    "confirm_rag_documents",
    "prepare_document_with_copilot",
    "revise_document_with_copilot",
    "confirm_document_diff",
    "prepare_canonical_analysis",
    "confirm_impact",
    "confirm_code_scope",
    "generate_orchestration",
    "confirm_test_plan",
    "provision_execution_scope",
    "apply_code_change_with_copilot",
    "issue_approval_grant",
    "start_test_data_execution",
    "refresh",
    "run_ui_verification",
    "confirm_ui_test",
    "confirm_final_report",
    "resolve_blocker",
}


def test_task_definition_is_agent_neutral_and_deterministic() -> None:
    decision = ChangeAutomationDecision(
        stage="document_confirmation",
        status="waiting",
        next_action="confirm_document_diff",
        blocking_reason=None,
        message="生成された設計書差分を確認してください。",
    )

    first = build_orchestration_task(
        automation_run_id="run-1",
        change_request_id="request-1",
        project_id="project-1",
        decision=decision,
    )
    replay = build_orchestration_task(
        automation_run_id="run-1",
        change_request_id="request-1",
        project_id="project-1",
        decision=decision,
    )

    assert first == replay
    assert first is not None
    assert first.task_kind == "judgment"
    assert first.protocol_version == ORCHESTRATION_TASK_PROTOCOL_VERSION
    assert first.eligible_executor_kinds == EXECUTOR_KINDS
    assert first.required_capabilities == ("document_review",)
    assert first.expected_output_types == ("DocumentReview",)
    assert "人工" in first.acceptance_criteria[0]


def test_task_identity_changes_when_immutable_instruction_changes() -> None:
    first = build_orchestration_task(
        automation_run_id="run-1",
        change_request_id="request-1",
        project_id="project-1",
        decision=ChangeAutomationDecision(
            "impact_analysis", "blocked", "resolve_blocker", "原因 A", "原因 A"
        ),
    )
    second = build_orchestration_task(
        automation_run_id="run-1",
        change_request_id="request-1",
        project_id="project-1",
        decision=ChangeAutomationDecision(
            "impact_analysis", "blocked", "resolve_blocker", "原因 B", "原因 B"
        ),
    )

    assert first is not None
    assert second is not None
    assert first.orchestration_task_id != second.orchestration_task_id
    assert first.definition_digest != second.definition_digest


def test_completed_workflow_does_not_create_a_task() -> None:
    task = build_orchestration_task(
        automation_run_id="run-1",
        change_request_id="request-1",
        project_id="project-1",
        decision=ChangeAutomationDecision("completed", "completed", None, None, "完了"),
    )

    assert task is None


def test_every_change_automation_action_uses_the_same_executor_neutral_contract() -> None:
    assert set(ACTION_POLICIES) == EXPECTED_CHANGE_AUTOMATION_ACTIONS

    for action in EXPECTED_CHANGE_AUTOMATION_ACTIONS:
        task = build_orchestration_task(
            automation_run_id=f"run-{action}",
            change_request_id="request-1",
            project_id="project-1",
            decision=ChangeAutomationDecision(
                "impact_analysis", "waiting", action, None, f"Execute {action}"
            ),
        )

        assert task is not None
        assert task.eligible_executor_kinds == ("agent", "subagent", "human")
        assert task.required_capabilities
        assert task.expected_output_types
        assert task.acceptance_criteria


def test_provider_specific_business_actions_are_exposed_as_neutral_tasks() -> None:
    expected = {
        "prepare_document_with_copilot": "prepare_document",
        "revise_document_with_copilot": "revise_document",
        "apply_code_change_with_copilot": "apply_code_change",
    }

    for business_action, neutral_action in expected.items():
        task = build_orchestration_task(
            automation_run_id=f"run-{neutral_action}",
            change_request_id="request-1",
            project_id="project-1",
            decision=ChangeAutomationDecision(
                "code_change",
                "waiting",
                business_action,
                None,
                "VS Code GitHub Copilot specific legacy instruction",
            ),
        )

        assert task is not None
        assert task.action == neutral_action
        assert "Copilot" not in task.title
        assert "VS Code" not in task.title
        assert "Copilot" not in task.instruction
        assert "VS Code" not in task.instruction


def test_single_agent_limit_is_a_replaceable_scheduling_policy() -> None:
    assert OrchestrationSchedulingPolicy().max_active_tasks_per_run == 1
    assert OrchestrationSchedulingPolicy(max_active_tasks_per_run=8).max_active_tasks_per_run == 8


def test_parallelism_is_deployment_configuration_with_single_agent_default() -> None:
    assert parse_orchestration_scheduling_policy(None).max_active_tasks_per_run == 1
    assert parse_orchestration_scheduling_policy(" 8 ").max_active_tasks_per_run == 8

    for invalid in ("", "workers", "0", "101"):
        with pytest.raises(ValueError):
            parse_orchestration_scheduling_policy(invalid)


def test_task_definition_rejects_executor_specific_contracts() -> None:
    task = build_orchestration_task(
        automation_run_id="run-contract",
        change_request_id="request-contract",
        project_id="project-contract",
        decision=ChangeAutomationDecision(
            "planning", "running", "generate_orchestration", None, "生成编排计划"
        ),
    )
    assert task is not None
    assert task.expected_output_types == (
        "AcceptanceCriteria",
        "TestPlan",
        "TestDataPlan",
        "BusinessCoverageReport",
        "ChangeOrchestrationPlan",
    )
    with pytest.raises(ValueError, match="agent, subagent, and human"):
        replace(task, eligible_executor_kinds=("agent",))

    with pytest.raises(ValueError, match="outputs and acceptance criteria"):
        replace(task, expected_output_types=())


def test_result_evidence_rejects_bodies_secrets_and_nested_values() -> None:
    validate_orchestration_result_evidence(
        {"artifact_digest": "a" * 64, "human_confirmation": True}
    )

    for evidence in (
        {},
        {"source_code": "class Secret {}"},
        {"bridge_token": "not-allowed"},
        {"nested": {"full": "body"}},
    ):
        with pytest.raises(ValueError):
            validate_orchestration_result_evidence(evidence)
