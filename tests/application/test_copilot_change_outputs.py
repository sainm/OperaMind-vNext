import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from operamind.application import copilot_coding_task as coding_task_module
from operamind.application.copilot_coding_task import (
    CopilotCodingTaskPublishRequest,
    CopilotCodingTaskService,
    _is_rejected_code_scope_revision,
    _public_document_discovery,
    _public_execution_scope,
    _public_task_artifact,
    _public_workspace,
    _validate_planning_alignment,
    _validate_planning_artifact_scope,
    build_bridge_task_view,
)
from operamind.application.test_data_flow import validate_test_data_plan_artifact
from operamind.application.test_data_ui_verification import _ui_scenario_evidence
from operamind.contracts import ContractCatalog


def _planning() -> tuple[dict[str, object], dict[str, object]]:
    test_plan: dict[str, object] = {
        "test_cases": [
            {
                "test_case_id": "expense-returned-ui",
                "level": "ui",
                "execution_mode": "browser",
                "steps": ["経費一覧を開く"],
                "step_ids": ["open-expense-search"],
                "test_data_refs": ["expense-returned"],
            }
        ]
    }
    test_data_plan: dict[str, object] = {
        "data_sets": [{"test_data_id": "expense-returned"}],
        "generation_flows": [
            {
                "flow_id": "expense-returned-flow",
                "test_case_refs": ["expense-returned-ui"],
                "steps": [
                    {
                        "step_id": "open-expense-search",
                        "channel": "ui",
                        "business_action": "経費一覧を開く",
                        "test_step_refs": ["open-expense-search"],
                        "playwright": {
                            "action": "goto",
                            "path": "/expense",
                            "mask_locators": [],
                            "observations": [],
                        },
                        "postconditions": [
                            {
                                "assertion_id": "returned-visible",
                                "observe_via": "ui",
                            }
                        ],
                    }
                ],
                "final_assertions": [],
            }
        ],
    }
    return test_plan, test_data_plan


def test_copilot_uses_the_rag_discovery_bound_to_the_active_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovery: dict[str, object] = {
        "status": "ready",
        "document_snapshot_id": "snapshot-confirmed",
        "candidates": [{"document_id": "document-confirmed"}],
    }
    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._tasks = SimpleNamespace(
        view=lambda _task_id: {
            "events": [
                {
                    "event_type": "document_discovery_bound",
                    "payload": {
                        "automation_run_id": "run-1",
                        "subject_digest": coding_task_module._payload_digest(discovery),
                        "discovery": discovery,
                    },
                }
            ]
        }
    )
    monkeypatch.setattr(
        coding_task_module,
        "ChangeAutomationRepository",
        lambda _connection: SimpleNamespace(
            latest_for_request=lambda _request_id: {"automation_run_id": "run-1"}
        ),
    )

    assert service._document_discovery_for_task("task-1", "change-1") == discovery


def test_rejected_code_scope_can_be_reopened_with_review_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_task = SimpleNamespace(
        change_request_id="change-1",
        current_stage="code_scope",
        approval_grant_id=None,
        workspace_root="/workspace",
    )
    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._tasks = SimpleNamespace(
        get=lambda _task_id: pending_task,
        view=lambda _task_id: {
            "task": {
                "coding_task_id": "task-1",
                "task_kind": "change",
                "change_request_id": "change-1",
                "project_id": "project-1",
                "workspace_root": "/workspace",
                "current_stage": "code_scope",
                "required_mcp_tools": [],
            }
        },
        begin_mcp=lambda **_kwargs: pending_task,
    )
    service._document_discovery_for_task = lambda *_args: {
        "status": "ready",
        "document_snapshot_id": "snapshot-1",
        "candidates": [{"document_id": "document-1"}],
    }
    automation_repository = SimpleNamespace(
        latest_for_request=lambda _request_id: {
            "automation_run_id": "run-1",
            "current_stage": "impact_confirmation",
            "status": "blocked",
            "next_action": "resolve_blocker",
        },
        latest_confirmation=lambda **_kwargs: {
            "checkpoint": "code_scope",
            "decision": "rejected",
            "note": "Do not create artificial production changes.",
            "created_at": "2026-08-02T14:55:34+08:00",
        },
    )
    monkeypatch.setattr(
        coding_task_module,
        "ChangeAutomationRepository",
        lambda _connection: automation_repository,
    )

    context = service.get_mcp_context(
        coding_task_id="task-1",
        workspace_root=Path("/workspace"),
    )

    assert context["current_stage"] == "code_scope"
    assert context["review_feedback"] == {
        "checkpoint": "code_scope",
        "decision": "rejected",
        "note": "Do not create artificial production changes.",
        "created_at": "2026-08-02T14:55:34+08:00",
    }


def test_code_scope_revision_requires_the_current_explicit_rejection() -> None:
    automation = {
        "current_stage": "impact_confirmation",
        "status": "blocked",
        "next_action": "resolve_blocker",
    }
    rejected = {"checkpoint": "code_scope", "decision": "rejected"}

    assert _is_rejected_code_scope_revision(automation, rejected)
    assert not _is_rejected_code_scope_revision({**automation, "status": "waiting"}, rejected)
    assert not _is_rejected_code_scope_revision(automation, {**rejected, "decision": "confirmed"})


def test_follow_up_execution_task_requires_confirmed_scope_basis() -> None:
    request = CopilotCodingTaskPublishRequest(
        coding_task_id="task-2",
        change_request_id="change-1",
        project_id="project-1",
        workspace_root=Path("."),
        task_summary="Re-run from the confirmed replacement impact scope",
        actor="automation:operamind",
        idempotency_key="execution-2",
        edit_packet_id="packet-2",
        approval_grant_id="grant-2",
        retry_of_coding_task_id="task-1",
        attempt_number=2,
        task_kind="change_execution",
        initial_stage="compile_test",
        execution_basis={
            "impact_report_id": "impact-2",
            "document_change_refs": ["document-change-1"],
        },
    )

    assert request.task_kind == "change_execution"
    with pytest.raises(ValueError, match="requires confirmed scope"):
        CopilotCodingTaskPublishRequest(
            coding_task_id="task-2",
            change_request_id="change-1",
            project_id="project-1",
            workspace_root=Path("."),
            task_summary="Missing immutable scope basis",
            actor="automation:operamind",
            idempotency_key="execution-invalid",
            edit_packet_id="packet-2",
            approval_grant_id="grant-2",
            task_kind="change_execution",
            initial_stage="compile_test",
        )


def test_follow_up_execution_reads_immutable_scope_basis() -> None:
    service = object.__new__(CopilotCodingTaskService)
    service._tasks = SimpleNamespace(
        view=lambda _task_id: {
            "task": {
                "task_kind": "change_execution",
                "execution_basis": {
                    "impact_report_id": "impact-2",
                    "document_change_refs": ["document-change-1"],
                },
            },
            "events": [],
        }
    )

    assert service._code_scope_output("task-2") == {
        "impact_report_id": "impact-2",
        "document_change_refs": ["document-change-1"],
    }


def test_test_planning_context_reads_the_successfully_closed_code_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    task = SimpleNamespace(
        coding_task_id="task-1",
        change_request_id="change-1",
        project_id="project-1",
        analysis_case_id="case-1",
        repository_id="repository-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
        base_repository_revision="a" * 40,
        workspace_root="/workspace",
        current_stage="test_planning",
        state="in_progress",
    )
    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._contracts = SimpleNamespace(root=Path("contracts").resolve())
    service._requests = SimpleNamespace(
        get_change_request=lambda _request_id: {
            "artifact": {
                "business_rules": [{"business_rule_id": "rule-1", "text": "差戻しを検索する"}]
            }
        }
    )
    service._recorded_output = lambda _task_id, _stage: {
        "impact_report_id": "impact-1",
        "document_change_refs": ["document-change-1"],
    }
    service._artifacts = SimpleNamespace(
        get=lambda _artifact_id: {
            "artifact_type": "ImpactReport",
            "required_ui_scenario_refs": ["ui-expense-status-search"],
        }
    )
    service._tasks = SimpleNamespace(
        get=lambda _task_id: task,
        begin_mcp=lambda **_kwargs: task,
        view=lambda _task_id: {
            "task": {
                "coding_task_id": "task-1",
                "change_request_id": "change-1",
                "project_id": "project-1",
                "execution_mode": "copilot_change_task",
                "task_summary": "Verify expense status search",
                "target_project": {},
                "required_mcp_tools": [],
            },
            "commands": [{"command_ref": "unit-test", "status": "passed", "exit_code": 0}],
        },
    )
    monkeypatch.setattr(
        coding_task_module,
        "ChangeAutomationRepository",
        lambda _connection: SimpleNamespace(latest_for_request=lambda _request_id: None),
    )

    class ContextService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def get(self, request: object) -> dict[str, object]:
            captured["request"] = request
            return {
                "edit_packet": {"editable_files": []},
                "approval": {
                    "allowed_test_command_refs": [],
                    "test_files": ["tests/ExpenseServiceTest.java"],
                },
                "workspace": {"root": "/workspace"},
            }

    monkeypatch.setattr(coding_task_module, "CopilotTaskContextService", ContextService)

    context = service.get_mcp_context(
        coding_task_id="task-1",
        workspace_root=Path("/workspace"),
    )

    request = captured["request"]
    assert request.require_active_grant is False
    assert context["current_stage"] == "test_planning"
    planning_contract = context["planning_contract"]
    assert planning_contract["test_plan_example"]["project_id"] == "project-1"
    assert planning_contract["test_plan_example"]["change_request_id"] == "change-1"
    assert "test_data_refs belongs on the generation flow" in planning_contract["instruction"]
    assert "setup_actions is a declarative summary" in planning_contract["instruction"]
    assert (
        "never invent an endpoint, field, status, or foreign-key ID"
        in planning_contract["instruction"]
    )
    assert "dependent HTTP read-back step" in planning_contract["instruction"]
    assert (
        "Do not assume that unnamed existing target-system rows" in planning_contract["instruction"]
    )
    assert planning_contract["required_ui_scenario_ids"] == ["ui-expense-status-search"]
    assert "bind every created ID" in planning_contract["instruction"]
    assert "Project test_base_url supplies the HTTP origin" in planning_contract["instruction"]
    assert planning_contract["http_setup_step_example"]["output_bindings"] == [
        {
            "variable": "created_expense_id",
            "source": "response",
            "path": "id",
            "required": True,
        }
    ]
    assert planning_contract["http_setup_step_example"]["inputs"]["json"]["expense"] == {
        "applyDate": "2026-08-02",
        "description": "UI 検証用",
        "totalAmount": 1000,
        "status": "申請中",
    }
    assert planning_contract["http_setup_step_example"]["data_effect"] == "creates"
    assert planning_contract["http_setup_step_example"]["depends_on"] == []
    assert len(planning_contract["http_setup_step_example"]["postconditions"]) == 3
    assert planning_contract["http_cleanup_step_example"]["target"] == (
        "DELETE /expense/api/{{created_expense_id}}"
    )
    assert planning_contract["http_cleanup_step_example"]["data_effect"] == "deletes"
    assert planning_contract["http_cleanup_step_example"]["depends_on"] == ["create-expense"]
    assert planning_contract["cleanup_step_example"]["postconditions"]
    example_plan = copy.deepcopy(planning_contract["test_data_plan_example"])
    example_plan["generation_flows"] = [
        {
            "flow_id": "http-example-flow",
            "title": "HTTP example",
            "test_data_refs": [example_plan["data_sets"][0]["test_data_id"]],
            "test_case_refs": example_plan["data_sets"][0]["test_case_refs"],
            "steps": [planning_contract["http_setup_step_example"]],
            "final_assertions": [
                {
                    "assertion_id": "http-example-result",
                    "observe_via": "test",
                    "subject": "scenario",
                    "operator": "equals",
                    "expected": True,
                }
            ],
            "cleanup_policy": "delete_after_run",
            "cleanup_steps": [planning_contract["http_cleanup_step_example"]],
        }
    ]
    ContractCatalog.load(Path(__file__).parents[2] / "contracts").validate_artifact(example_plan)
    assert validate_test_data_plan_artifact(example_plan) == []
    coverage_contract = planning_contract["business_coverage_contract"]
    assert coverage_contract["required_coverage_percent"] == 100
    assert coverage_contract["business_requirements"][0]["business_rule_id"] == "rule-1"
    assert coverage_contract["allowed_evidence"]["passed_command_refs"] == ["unit-test"]


def test_rejected_code_scope_records_a_revision_specific_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, object] = {}
    task = SimpleNamespace(
        project_id="project-1",
        change_request_id="change-1",
        approval_grant_id=None,
        state="in_progress",
    )
    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._root = Path("/repository")
    service._tasks = SimpleNamespace(
        get=lambda _task_id: task,
        record_change_outputs=lambda **values: recorded.update(values),
    )
    service._requests = SimpleNamespace(
        impact_report=lambda **_values: {"impact_report_id": "impact-old"}
    )
    service._recorded_output = lambda *_args: {
        "document_change_refs": ["change-ref-1"],
        "source_document_snapshot_id": "snapshot-before",
        "target_document_snapshot_id": "snapshot-after",
        "search_index_build_id": "index-1",
    }
    service._bound_change_request_case = lambda _request_id: "case-1"
    automation_repository = SimpleNamespace(
        latest_for_request=lambda _request_id: {
            "automation_run_id": "run-1",
            "current_stage": "impact_confirmation",
            "status": "blocked",
            "next_action": "resolve_blocker",
        },
        latest_confirmation=lambda **_kwargs: {
            "checkpoint": "code_scope",
            "decision": "rejected",
        },
    )
    monkeypatch.setattr(
        coding_task_module,
        "ChangeAutomationRepository",
        lambda _connection: automation_repository,
    )
    monkeypatch.setattr(
        coding_task_module,
        "CopilotImpactService",
        lambda **_kwargs: SimpleNamespace(
            publish=lambda **_values: {
                "created": True,
                "impact_report_id": "impact-revision-2",
                "code_scope": [{"target_path": "src/ExpenseService.java"}],
            }
        ),
    )

    service._record_code_scope_output(
        coding_task_id="task-1",
        workspace_root=Path("/workspace"),
        code_scope=({"target_path": "src/ExpenseService.java"},),
    )

    assert recorded["revision_identity"] == "impact-revision-2"
    assert recorded["output_stage"] == "code_scope"


@pytest.mark.parametrize("existing_context_task_id", ["previous-task", "revision-task"])
def test_document_revision_replaces_impact_without_current_code_scope_output(
    monkeypatch: pytest.MonkeyPatch,
    existing_context_task_id: str,
) -> None:
    recorded: dict[str, object] = {}
    published: list[dict[str, object]] = []
    task = SimpleNamespace(
        project_id="project-1",
        change_request_id="change-1",
        approval_grant_id=None,
        state="in_progress",
    )
    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._root = Path("/repository")
    service._tasks = SimpleNamespace(
        get=lambda _task_id: task,
        record_change_outputs=lambda **values: recorded.update(values),
    )
    service._requests = SimpleNamespace(
        impact_report=lambda **_values: {"impact_report_id": "impact-old"}
    )
    service._artifacts = SimpleNamespace(
        get=lambda artifact_id: {
            "impact-old": {
                "artifact_type": "ImpactReport",
                "context_package_id": "context-old",
                "items": [],
            },
            "context-old": {
                "artifact_type": "CopilotImpactContext",
                "coding_task_id": existing_context_task_id,
            },
        }.get(artifact_id)
    )

    def recorded_output(_task_id: str, output_stage: str) -> dict[str, object]:
        if output_stage != "document_change":
            raise ValueError(f"no recorded {output_stage} output")
        return {
            "output_stage": "document_change",
            "document_change_refs": ["revised-change-ref"],
            "source_document_snapshot_id": "snapshot-before",
            "target_document_snapshot_id": "snapshot-revised",
            "search_index_build_id": "index-1",
        }

    service._recorded_output = recorded_output
    service._bound_change_request_case = lambda _request_id: "case-1"
    automation_repository = SimpleNamespace(
        latest_for_request=lambda _request_id: {
            "automation_run_id": "run-1",
            "current_stage": "impact_analysis",
            "status": "waiting",
            "next_action": "analyze_code_scope_with_copilot",
        },
        latest_confirmation=lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        coding_task_module,
        "ChangeAutomationRepository",
        lambda _connection: automation_repository,
    )

    def publish(**values: object) -> dict[str, object]:
        published.append(values)
        return {
            "created": True,
            "impact_report_id": "impact-revised",
            "code_scope": [{"target_path": "src/ExpenseService.java"}],
        }

    monkeypatch.setattr(
        coding_task_module,
        "CopilotImpactService",
        lambda **_kwargs: SimpleNamespace(publish=publish),
    )

    result = service._record_code_scope_output(
        coding_task_id="revision-task",
        workspace_root=Path("/workspace"),
        code_scope=({"target_path": "src/ExpenseService.java"},),
    )

    assert len(published) == 1
    assert published[0]["coding_task_id"] == "revision-task"
    assert published[0]["document_change_refs"] == ("revised-change-ref",)
    output_refs = recorded["output_refs"]
    assert isinstance(output_refs, dict)
    assert output_refs["impact_report_id"] == "impact-revised"
    assert result["impact_report_id"] == "impact-revised"


def test_test_planning_rejects_a_head_newer_than_the_tested_edit_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SimpleNamespace(
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
        base_repository_revision="base-sha",
        workspace_root="/workspace",
    )
    service = object.__new__(CopilotCodingTaskService)
    service._tasks = SimpleNamespace(
        get=lambda _task_id: task,
        view=lambda _task_id: {
            "edit_results": [
                {
                    "validation_mode": "committed",
                    "status": "in_scope",
                    "changed_paths": ["src/App.java"],
                    "tests_passed": True,
                    "command_evidence_status": "verified",
                    "changed_line_coverage_status": "passed",
                    "result_repository_revision": "tested-sha",
                }
            ]
        },
    )
    service._artifacts = SimpleNamespace(
        get=lambda _artifact_id: {
            "artifact_type": "ApprovalGrant",
            "editable_files": ["src/App.java"],
        }
    )
    monkeypatch.setattr(
        coding_task_module,
        "GitWorktreeDiffInspector",
        lambda: SimpleNamespace(
            inspect_committed=lambda *_args, **_kwargs: SimpleNamespace(
                result_sha="newer-untested-sha"
            )
        ),
    )

    with pytest.raises(ValueError, match="current clean HEAD"):
        service._record_test_planning_outputs(
            coding_task_id="task-1",
            test_plan={},
            test_data_plan={},
        )


def test_test_planning_returns_uncovered_requirements_without_completing_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).parents[2]
    test_plan = json.loads((root / "contracts/examples/test-plan.v2.example.json").read_text())
    test_data_plan = json.loads(
        (root / "contracts/examples/test-data-plan.v2.example.json").read_text()
    )
    task = SimpleNamespace(
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
        base_repository_revision="base-sha",
        workspace_root="/workspace",
        project_id="visiondemo",
        change_request_id="change-expense-status",
    )
    recorded: list[object] = []
    service = object.__new__(CopilotCodingTaskService)
    service._tasks = SimpleNamespace(
        get=lambda _task_id: task,
        view=lambda _task_id: {
            "edit_results": [
                {
                    "validation_mode": "committed",
                    "status": "in_scope",
                    "changed_paths": ["src/App.java"],
                    "tests_passed": True,
                    "command_evidence_status": "verified",
                    "changed_line_coverage_status": "passed",
                    "result_repository_revision": "tested-sha",
                }
            ],
            "commands": [{"command_ref": "unit-test", "status": "passed", "exit_code": 0}],
        },
        record_change_outputs=lambda **kwargs: recorded.append(kwargs),
    )
    artifacts = {
        "grant-1": {
            "artifact_type": "ApprovalGrant",
            "editable_files": ["src/App.java"],
            "test_files": ["tests/AppTest.java"],
            "allowed_test_command_refs": ["unit-test"],
        },
        "impact-1": {
            "artifact_type": "ImpactReport",
            "ui_impact_status": "impacted",
            "required_ui_scenario_refs": ["ui-expense-status-search"],
        },
    }
    service._artifacts = SimpleNamespace(
        get=lambda artifact_id: artifacts.get(artifact_id),
        store=lambda **kwargs: recorded.append(kwargs),
    )
    service._recorded_output = lambda _task_id, _stage: {
        "impact_report_id": "impact-1",
        "document_change_refs": ["document-change-1"],
    }
    service._requests = SimpleNamespace(
        project_test_base_url=lambda _project_id: "http://127.0.0.1:8080",
        get_change_request=lambda _request_id: {
            "artifact": {
                "business_rules": [
                    {
                        "business_rule_id": "expense-status-rule",
                        "text": "差戻しを検索できる",
                    },
                    {
                        "business_rule_id": "expense-status-options-rule",
                        "text": "既存の状態選択肢を維持する",
                    },
                ]
            }
        },
    )
    monkeypatch.setattr(
        coding_task_module,
        "GitWorktreeDiffInspector",
        lambda: SimpleNamespace(
            inspect_committed=lambda *_args, **_kwargs: SimpleNamespace(result_sha="tested-sha")
        ),
    )

    with pytest.raises(ValueError) as raised:
        service._record_test_planning_outputs(
            coding_task_id="task-1",
            test_plan=test_plan,
            test_data_plan=test_data_plan,
        )

    assert "coverage_percent=50.0" in str(raised.value)
    assert "expense-status-options-rule" in str(raised.value)
    assert recorded == []


def test_test_planning_requires_ui_flow_for_ui_impact() -> None:
    test_plan, test_data_plan = _planning()

    _validate_planning_alignment(
        test_plan=test_plan,
        test_data_plan=test_data_plan,
        ui_impacted=True,
    )


def test_test_planning_keeps_end_to_end_ui_validation_for_backend_code_impact() -> None:
    test_plan, test_data_plan = _planning()

    _validate_planning_alignment(
        test_plan=test_plan,
        test_data_plan=test_data_plan,
        ui_impacted=False,
    )


def test_test_planning_rejects_natural_step_without_playwright_mapping() -> None:
    test_plan, test_data_plan = _planning()
    test_plan["test_cases"][0]["steps"].append("差戻し状態を選択する")  # type: ignore[index,union-attr]
    test_plan["test_cases"][0]["step_ids"].append("select-returned")  # type: ignore[index,union-attr]

    with pytest.raises(ValueError, match="missing refs: select-returned"):
        _validate_planning_alignment(
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            ui_impacted=True,
        )


def test_test_planning_rejects_unknown_or_cross_case_step_reference() -> None:
    test_plan, test_data_plan = _planning()
    test_data_plan["generation_flows"][0]["steps"][0]["test_step_refs"] = [  # type: ignore[index]
        "unknown-step"
    ]

    with pytest.raises(ValueError, match="outside its flow"):
        _validate_planning_alignment(
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            ui_impacted=True,
        )


def test_test_planning_allows_extra_executable_ui_data_setup_step() -> None:
    test_plan, test_data_plan = _planning()
    test_data_plan["generation_flows"][0]["steps"].insert(  # type: ignore[index]
        0,
        {
            "step_id": "create-expense-prerequisite",
            "channel": "ui",
            "business_action": "前提となる経費申請を作成する",
            "test_step_refs": [],
            "playwright": {
                "action": "goto",
                "path": "/expense/new",
                "mask_locators": [],
                "observations": [],
            },
            "postconditions": [],
        },
    )

    _validate_planning_alignment(
        test_plan=test_plan,
        test_data_plan=test_data_plan,
        ui_impacted=True,
    )


def test_test_planning_rejects_opaque_step_text() -> None:
    test_plan, test_data_plan = _planning()
    test_plan["test_cases"][0]["steps"] = ["step-1"]  # type: ignore[index]

    with pytest.raises(ValueError, match="natural-language actions"):
        _validate_planning_alignment(
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            ui_impacted=True,
        )


def test_test_planning_scope_error_names_every_incorrect_binding() -> None:
    with pytest.raises(ValueError) as raised:
        _validate_planning_artifact_scope(
            artifact_name="TestPlan",
            artifact={"cases": []},
            expected={
                "artifact_type": "TestPlan",
                "project_id": "project-1",
                "status": "ready",
            },
        )

    message = str(raised.value)
    assert "artifact_type must be 'TestPlan' (received None)" in message
    assert "project_id must be 'project-1' (received None)" in message
    assert "status must be 'ready' (received None)" in message


def test_copilot_context_exposes_constraints_without_internal_authorization_records() -> None:
    task = _public_task_artifact(
        {
            "coding_task_id": "task-1",
            "change_request_id": "change-1",
            "project_id": "project-1",
            "task_summary": "差戻し検索を追加する",
            "attempt_number": 1,
            "approval_grant_id": "grant-internal",
            "edit_packet_id": "packet-internal",
            "analysis_case_id": "case-internal",
        }
    )
    scope = _public_execution_scope(
        {
            "base_repository_revision": "abc123",
            "editable_files": ["src/ExpenseService.java"],
            "read_only_files": ["docs/expense.md"],
            "test_files": ["test/ExpenseServiceTest.java"],
            "forbidden_globs": [".env*"],
            "allowed_items": [
                {
                    "impact_item_id": "impact-internal",
                    "target_path": "src/ExpenseService.java",
                    "target_symbols": ["search"],
                    "allowed_actions": ["modify"],
                    "business_summary": "差戻しを検索対象にする",
                    "implementation_constraints": ["Framework を更新しない"],
                }
            ],
            "out_of_scope_policy": "stop_and_reanalyze",
        },
        {
            "approval_grant_id": "grant-internal",
            "allowed_test_command_refs": ["springboot15-test"],
            "expires_at": "2026-07-29T00:00:00Z",
        },
    )
    workspace = _public_workspace(
        {
            "root": "/workspace/change-1",
            "registered_root": "/workspace/repository",
            "isolated_worktree": True,
            "remote_url": "ssh://internal/repository",
            "head_revision": "abc123",
            "changed_paths": [],
            "result_committed": False,
        }
    )

    assert task == {
        "coding_task_id": "task-1",
        "change_request_id": "change-1",
        "project_id": "project-1",
        "task_summary": "差戻し検索を追加する",
        "attempt_number": 1,
    }
    assert scope == {
        "bound": True,
        "base_repository_revision": "abc123",
        "editable_files": ["src/ExpenseService.java"],
        "read_only_files": ["docs/expense.md"],
        "test_files": ["test/ExpenseServiceTest.java"],
        "forbidden_globs": [".env*"],
        "allowed_items": [
            {
                "target_path": "src/ExpenseService.java",
                "target_symbols": ["search"],
                "allowed_actions": ["modify"],
                "business_summary": "差戻しを検索対象にする",
                "implementation_constraints": ["Framework を更新しない"],
            }
        ],
        "required_command_refs": ["springboot15-test"],
        "out_of_scope_policy": "stop_and_reanalyze",
    }
    assert workspace == {
        "root": "/workspace/change-1",
        "isolated_worktree": True,
        "head_revision": "abc123",
        "changed_paths": [],
        "result_committed": False,
    }
    assert "grant-internal" not in repr((task, scope, workspace))
    assert "packet-internal" not in repr((task, scope, workspace))
    assert "impact-internal" not in repr((task, scope, workspace))


def test_copilot_document_discovery_hides_search_index_implementation_ids() -> None:
    discovery = _public_document_discovery(
        {
            "status": "ready",
            "mode": "canonical_hybrid_rag",
            "context_package_id": "context-internal",
            "document_snapshot_id": "snapshot-internal",
            "search_index_build_id": "index-internal",
            "explicit_document_refs": [],
            "candidates": [
                {
                    "document_id": "expense-design",
                    "section_id": "status-filter",
                    "heading_path": ["経費検索", "状態"],
                    "summary": "差戻し状態の検索条件",
                    "logical_name": "02_画面設計書_経費一覧.xlsx",
                    "document_ref": "file:///design/expense.xlsx",
                    "canonical_document": {
                        "document_id": "expense-design",
                        "logical_name": "02_画面設計書_経費一覧.xlsx",
                        "document_ref": "file:///design/expense.xlsx",
                        "facts": [
                            {
                                "stable_key": "screen_element:expense/status",
                                "fact_type": "screen_element",
                                "values": {"default_value": "申請中"},
                            }
                        ],
                    },
                    "relevance_reason": "変更要件と一致",
                    "evidence_refs": ["document:expense-design"],
                    "embedding_distance": 0.01,
                }
            ],
            "blocking_reason": None,
        }
    )

    assert discovery == {
        "status": "ready",
        "mode": "canonical_hybrid_rag",
        "explicit_document_refs": [],
        "candidates": [
            {
                "document_id": "expense-design",
                "section_id": "status-filter",
                "heading_path": ["経費検索", "状態"],
                "summary": "差戻し状態の検索条件",
                "logical_name": "02_画面設計書_経費一覧.xlsx",
                "document_ref": "file:///design/expense.xlsx",
                "canonical_document": {
                    "document_id": "expense-design",
                    "logical_name": "02_画面設計書_経費一覧.xlsx",
                    "document_ref": "file:///design/expense.xlsx",
                    "facts": [
                        {
                            "stable_key": "screen_element:expense/status",
                            "fact_type": "screen_element",
                            "values": {"default_value": "申請中"},
                        }
                    ],
                },
                "relevance_reason": "変更要件と一致",
                "evidence_refs": ["document:expense-design"],
            }
        ],
        "blocking_reason": None,
    }
    assert "internal" not in repr(discovery)


def test_explicit_document_ref_still_requires_canonical_rag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = object.__new__(CopilotCodingTaskService)
    service._requests = SimpleNamespace(  # type: ignore[attr-defined]
        get_change_request=lambda _request_id: {
            "project_id": "visiondemo",
            "analysis_case_id": None,
            "artifact": {
                "requirement_text": "差戻し状態を検索できる",
                "source_document_ref": "/design/expense.xlsx",
                "target_document_ref": None,
                "business_rules": [],
            },
        }
    )
    binding = SimpleNamespace(profile={"profile_type": "EmbeddingProfile"})
    service._profile_repository = SimpleNamespace(  # type: ignore[attr-defined]
        list_active_by_type=lambda **_values: [binding]
    )
    service._profiles = object()  # type: ignore[attr-defined]
    service._index_repository = object()  # type: ignore[attr-defined]
    service._document_nodes = object()  # type: ignore[attr-defined]
    service._canonical = SimpleNamespace(  # type: ignore[attr-defined]
        get_document_slice=lambda **_values: SimpleNamespace(
            document_id="expense-design",
            logical_name="02_画面設計書_経費一覧.xlsx",
            source_ref="file:///design/expense.xlsx",
            snapshot=SimpleNamespace(
                facts=(
                    SimpleNamespace(
                        fact=SimpleNamespace(
                            stable_key="screen_element:expense/status",
                            fact_type="screen_element",
                            values={"default_value": "申請中"},
                        )
                    ),
                )
            ),
        )
    )

    monkeypatch.setattr(
        "operamind.application.copilot_coding_task.OpenAICompatibleEmbeddingProvider.from_profile",
        lambda _profile: object(),
    )

    class _Discovery:
        def __init__(self, **_values: object) -> None:
            pass

        def run(self, _request: object, *, provider: object) -> object:
            assert provider is not None
            return SimpleNamespace(
                document_snapshot_id="snapshot-1",
                search_index_build_id="index-1",
                embedding_profile_binding_key="embedding:visiondemo",
                candidates=(
                    SimpleNamespace(
                        to_dict=lambda: {
                            "document_id": "expense-design",
                            "section_id": "status-filter",
                        }
                    ),
                ),
            )

    monkeypatch.setattr(
        "operamind.application.copilot_coding_task.RequirementDocumentDiscoveryService",
        _Discovery,
    )

    discovery = service._document_discovery("change-1")

    assert discovery["status"] == "ready"
    assert discovery["mode"] == "requirement_hybrid_rag_with_explicit_refs"
    assert discovery["document_snapshot_id"] == "snapshot-1"
    assert discovery["explicit_document_refs"] == ["/design/expense.xlsx"]
    assert discovery["candidates"] == [
        {
            "document_id": "expense-design",
            "section_id": "status-filter",
            "logical_name": "02_画面設計書_経費一覧.xlsx",
            "document_ref": "file:///design/expense.xlsx",
            "canonical_document": {
                "document_id": "expense-design",
                "logical_name": "02_画面設計書_経費一覧.xlsx",
                "document_ref": "file:///design/expense.xlsx",
                "facts": [
                    {
                        "stable_key": "screen_element:expense/status",
                        "fact_type": "screen_element",
                        "values": {"default_value": "申請中"},
                    }
                ],
            },
        }
    ]


def test_bridge_task_view_hides_claim_and_execution_authorization_state() -> None:
    view = build_bridge_task_view(
        {
            "task": {
                "coding_task_id": "task-1",
                "change_request_id": "change-1",
                "project_id": "project-1",
                "execution_mode": "copilot_change_task",
                "task_summary": "差戻し検索を追加する",
                "required_mcp_tools": ["copilot_get_coding_task"],
                "approval_grant_id": "grant-internal",
                "edit_packet_id": "packet-internal",
            },
            "state": "accepted",
            "attempt_number": 1,
            "current_stage": "document_change",
            "claimed_by": "consumer-internal",
            "claim_expires_at": "2026-07-28T00:00:00Z",
            "accepted_by": "actor-internal",
            "execution_scope": {"approval_grant_id": "grant-internal"},
        }
    )

    assert view == {
        "task": {
            "coding_task_id": "task-1",
            "change_request_id": "change-1",
            "project_id": "project-1",
            "execution_mode": "copilot_change_task",
            "task_summary": "差戻し検索を追加する",
            "required_mcp_tools": ["copilot_get_coding_task"],
        },
        "state": "accepted",
        "attempt_number": 1,
        "current_stage": "document_change",
    }
    assert "internal" not in repr(view)


def test_test_planning_rejects_ui_case_without_bounded_ui_assertion() -> None:
    test_plan, test_data_plan = _planning()
    test_data_plan["generation_flows"][0]["steps"][0]["postconditions"] = []  # type: ignore[index]

    with pytest.raises(ValueError, match="executable Playwright UI step"):
        _validate_planning_alignment(
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            ui_impacted=True,
        )


def test_ui_verification_uses_only_passed_ui_screenshot_evidence() -> None:
    test_plan, test_data_plan = _planning()
    result = _ui_scenario_evidence(
        ui_cases=test_plan["test_cases"],  # type: ignore[arg-type]
        test_data_plan=test_data_plan,
        execution_result={
            "flow_results": [
                {
                    "flow_id": "expense-returned-flow",
                    "status": "passed",
                    "step_results": [
                        {
                            "step_id": "open-expense-search",
                            "channel": "ui",
                            "status": "passed",
                        }
                    ],
                }
            ],
            "evidence": [
                {
                    "flow_id": "expense-returned-flow",
                    "step_id": "open-expense-search",
                    "phase": "setup",
                    "evidence_type": "screenshot",
                    "evidence_ref": "evidence/ui/returned.png",
                    "sanitized": True,
                }
            ],
        },
    )

    assert result == {"expense-returned-ui": ["evidence/ui/returned.png"]}
