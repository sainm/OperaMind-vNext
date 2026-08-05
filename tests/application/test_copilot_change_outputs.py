import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from operamind.application import copilot_coding_task as coding_task_module
from operamind.application.copilot_coding_task import (
    CopilotCodingTaskPublishRequest,
    CopilotCodingTaskService,
    _bound_task_scope,
    _confirmed_existing_test_data_context,
    _is_rejected_code_scope_revision,
    _looks_like_natural_language,
    _public_canonical_fact,
    _public_data_identity_providers,
    _public_document_discovery,
    _public_execution_scope,
    _public_task_artifact,
    _public_workspace,
    _stage_contract,
    _validate_planning_alignment,
    _validate_planning_artifact_scope,
    build_bridge_task_view,
    validate_confirmed_existing_test_data_usage,
)
from operamind.application.test_data_ui_verification import (
    _ui_scenario_binding_refs,
    _ui_scenario_evidence,
)
from operamind.run_context_values import canonical_digest


def _planning() -> tuple[dict[str, object], dict[str, object]]:
    test_plan: dict[str, object] = {
        "test_cases": [
            {
                "test_case_id": "expense-returned-ui",
                "level": "ui",
                "execution_mode": "browser",
                "acceptance_criteria_refs": ["criterion-returned"],
                "steps": ["経費一覧を開く"],
                "step_ids": ["open-expense-search"],
                "test_data_refs": ["expense-returned"],
            }
        ]
    }
    test_data_plan: dict[str, object] = {
        "data_sets": [
            {
                "test_data_id": "expense-returned",
                "coverage_conditions": [
                    {
                        "condition_id": "returned-status-condition",
                        "criterion_ref": "criterion-returned",
                        "test_case_ref": "expense-returned-ui",
                        "test_data_id": "expense-returned",
                    }
                ],
            }
        ],
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


def test_confirmed_existing_data_is_exposed_and_must_be_consumed_by_its_case() -> None:
    identity_binding = {
        "provider": {"type": "database", "provider_ref": "database.v1"},
        "binding_mode": "adopted",
    }
    reviewed_data_set = {
        "test_data_id": "adopted-expense-1",
        "test_case_refs": ["case-1"],
        "setup_actions": [],
        "cleanup_policy": "retain",
        "identity_binding": identity_binding,
        "runtime_variable_writes": [],
    }
    reviewed_flow = {
        "flow_id": "adopt-expense-1",
        "steps": [{"step_id": "lookup-existing", "channel": "sql"}],
        "cleanup_steps": [],
        "cleanup_policy": "retain",
        "test_data_refs": ["adopted-expense-1"],
        "test_case_refs": ["case-1"],
    }
    registration = SimpleNamespace(
        status="confirmed",
        data_name="差戻し済み経費",
        business_unique_value="EXP-041",
        test_case_ref="case-1",
        retain_after_test=True,
        provider_type="database",
        business_summary={"expense_number": "EXP-041"},
        plan_data_definition={
            "data_set": reviewed_data_set,
            "generation_flow": reviewed_flow,
        },
    )
    repository = SimpleNamespace(
        list_for_project=lambda _project_id, **_kwargs: (registration,)
    )
    test_plan = {"test_cases": [{"test_case_id": "case-1"}]}

    context = _confirmed_existing_test_data_context(  # type: ignore[arg-type]
        repository,
        "project-1",
        change_request_id="change-1",
    )
    assert context[0]["reviewed_plan_fragment"] == registration.plan_data_definition
    with pytest.raises(ValueError, match="missing from TestDataPlan"):
        validate_confirmed_existing_test_data_usage(
            repository=repository,  # type: ignore[arg-type]
            project_id="project-1",
            change_request_id="change-1",
            test_plan=test_plan,
            test_data_plan={"data_sets": [], "generation_flows": []},
        )

    with pytest.raises(ValueError, match="missing Test Case"):
        validate_confirmed_existing_test_data_usage(
            repository=repository,  # type: ignore[arg-type]
            project_id="project-1",
            change_request_id="change-1",
            test_plan={"test_cases": [{"test_case_id": "different-case"}]},
            test_data_plan={"data_sets": [], "generation_flows": []},
        )

    actual_data_set = {**reviewed_data_set, "coverage_conditions": [{"condition_id": "c1"}]}
    validate_confirmed_existing_test_data_usage(
        repository=repository,  # type: ignore[arg-type]
        project_id="project-1",
        change_request_id="change-1",
        test_plan=test_plan,
        test_data_plan={
            "data_sets": [actual_data_set],
            "generation_flows": [reviewed_flow],
        },
    )


def test_confirmed_existing_data_is_isolated_by_change_request() -> None:
    calls: list[str | None] = []
    current = SimpleNamespace(
        status="confirmed",
        data_name="current",
        business_unique_value="CURRENT-1",
        test_case_ref="case-current",
        retain_after_test=True,
        provider_type="api",
        business_summary={"business_no": "CURRENT-1"},
        plan_data_definition={
            "data_set": {"test_data_id": "data-current"},
            "generation_flow": {"flow_id": "flow-current"},
        },
    )
    unrelated = SimpleNamespace(
        **{
            **vars(current),
            "data_name": "unrelated",
            "test_case_ref": "case-unrelated",
        }
    )

    def list_for_project(
        _project_id: str, *, change_request_id: str | None = None
    ) -> tuple[object, ...]:
        calls.append(change_request_id)
        return (current,) if change_request_id == "change-current" else (unrelated,)

    repository = SimpleNamespace(list_for_project=list_for_project)

    context = _confirmed_existing_test_data_context(  # type: ignore[arg-type]
        repository,
        "project-1",
        change_request_id="change-current",
    )

    assert [value["data_name"] for value in context] == ["current"]
    assert calls == ["change-current"]


def _publish_request(root: Path, **changes: object) -> CopilotCodingTaskPublishRequest:
    values: dict[str, object] = {
        "coding_task_id": "task-publish-1",
        "change_request_id": "change-1",
        "project_id": "project-1",
        "workspace_root": root,
        "task_summary": "更新対象を限定して変更する",
        "actor": "owner",
        "idempotency_key": "publish-1",
    }
    values.update(changes)
    return CopilotCodingTaskPublishRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(("editable_files", "expected"), [([], True), (["src/app.py"], False)])
def test_verification_only_comes_from_the_confirmed_approval_scope(
    editable_files: list[str], expected: bool
) -> None:
    service = object.__new__(CopilotCodingTaskService)
    service._tasks = SimpleNamespace(
        get=lambda _task_id: SimpleNamespace(approval_grant_id="grant-1")
    )
    service._artifacts = SimpleNamespace(
        get=lambda _artifact_id: {
            "artifact_type": "ApprovalGrant",
            "editable_files": editable_files,
        }
    )

    assert service.is_verification_only("task-1") is expected


def test_verification_only_fails_closed_without_approval_artifact() -> None:
    service = object.__new__(CopilotCodingTaskService)
    service._tasks = SimpleNamespace(
        get=lambda _task_id: SimpleNamespace(approval_grant_id="grant-1")
    )
    service._artifacts = SimpleNamespace(get=lambda _artifact_id: None)

    with pytest.raises(RuntimeError, match="Approval Grant Artifact is missing"):
        service.is_verification_only("task-1")


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
        state="in_progress",
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
    service._recorded_output = lambda *_args: {"document_change_refs": ["document-change-1"]}
    service._artifacts = SimpleNamespace(
        get=lambda _artifact_id: {
            "artifact_type": "StructuredChange",
            "change_id": "document-change-1",
            "stable_key": "screen:status",
            "change_type": "modified",
            "summary": "差戻し状態を追加する",
        }
    )
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

    assert context["stage_status"]["task_stage"] == "code_scope"
    assert context["stage_contract"]["id"] == "code_scope"
    assert context["inputs"]["review_feedback"] == {
        "checkpoint": "code_scope",
        "decision": "rejected",
        "note": "Do not create artificial production changes.",
        "created_at": "2026-08-02T14:55:34+08:00",
    }
    assert context["inputs"]["design_changes"]["changes"][0]["stable_key"] == ("screen:status")
    assert "change_plan" not in context


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


def test_stage_context_fails_when_confirmed_design_change_is_missing() -> None:
    service = object.__new__(CopilotCodingTaskService)
    service._recorded_output = lambda *_args: {"document_change_refs": ["document-change-missing"]}
    service._artifacts = SimpleNamespace(get=lambda _artifact_id: None)

    with pytest.raises(RuntimeError, match="StructuredChange input is missing"):
        service._public_document_changes("task-1")


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
    service._existing_test_data = SimpleNamespace(
        list_for_project=lambda _project_id, **_kwargs: (),
        profiles=lambda _project_id: (),
    )
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
        get=lambda artifact_id: (
            {
                "artifact_type": "ImpactReport",
                "required_ui_scenario_refs": ["ui-expense-status-search"],
            }
            if artifact_id == "impact-1"
            else {
                "artifact_type": "StructuredChange",
                "change_id": "document-change-1",
                "stable_key": "screen:status",
                "change_type": "modified",
                "summary": "差戻し状態を追加する",
            }
        )
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
    monkeypatch.setattr(
        coding_task_module,
        "TargetDataProfileRepository",
        lambda _connection: SimpleNamespace(get=lambda _project_id: None),
    )

    context = service.get_mcp_context(
        coding_task_id="task-1",
        workspace_root=Path("/workspace"),
    )

    request = captured["request"]
    assert request.require_active_grant is False
    assert context["stage_status"]["task_stage"] == "test_planning"
    assert context["stage_contract"]["id"] == "test_planning"
    planning = context["inputs"]["planning"]
    assert planning["schema_source"] == "copilot_record_change_outputs.inputSchema"
    assert planning["required_ui_scenario_ids"] == ["ui-expense-status-search"]
    assert "test_plan_example" not in planning
    assert "test_data_plan_example" not in planning
    coverage_contract = planning["business_coverage"]
    assert coverage_contract["required_coverage_percent"] == 100
    assert coverage_contract["business_requirements"][0]["business_rule_id"] == "rule-1"
    assert coverage_contract["allowed_evidence"]["passed_command_refs"] == ["unit-test"]
    assert any("Coverage 100%" in rule for rule in planning["rules"])
    assert any("dom_observation" in rule for rule in planning["rules"])
    assert planning["data_identity_providers"] == []
    assert any("fake" in rule for rule in planning["rules"])
    assert "secret" not in json.dumps(planning["data_identity_providers"]).lower()
    assert "change_plan" not in context
    assert "planning_contract" not in context


def test_public_data_identity_provider_registry_contains_no_runtime_or_secret_data() -> None:
    profile = SimpleNamespace(
        provider_ref="database.expense.v1",
        provider_type="database",
        revision=2,
        content_digest="a" * 64,
        identity_definition={"business_unique_keys": [{"name": "expense_number"}]},
        lookup_steps=({"step_id": "lookup", "target": "expense.lookup.v1"},),
        cleanup_steps=({"step_id": "cleanup", "target": "expense.cleanup.v1"},),
        business_summary_fields=("expense_number", "status"),
    )
    providers = _public_data_identity_providers(
        SimpleNamespace(profiles=lambda _project_id: (profile,)),  # type: ignore[arg-type]
        "project-1",
    )

    assert providers[0]["provider_ref"] == "database.expense.v1"
    assert providers[0]["type"] == "database"
    assert providers[0]["revision"] == 2
    assert providers[0]["business_summary_fields"] == ["expense_number", "status"]
    assert "secret" not in json.dumps(providers).lower()


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
    test_data_plan["schema_version"] = "v3"
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
    service._connection = None
    service._existing_test_data = SimpleNamespace(
        list_for_project=lambda _project_id, **_kwargs: (),
        profiles=lambda _project_id: (),
    )
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
    monkeypatch.setattr(
        coding_task_module,
        "_project_target_data_blockers",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        coding_task_module,
        "validate_test_data_plan_artifact",
        lambda _plan, **_kwargs: [],
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


def test_test_planning_rejects_structurally_incomplete_ui_plans() -> None:
    _test_plan, test_data_plan = _planning()
    invalid_plans: list[
        tuple[dict[str, object], dict[str, object], str]
    ] = []

    invalid_plans.append(({"test_cases": []}, test_data_plan, "non-empty and unique"))

    non_ui_plan, non_ui_data = _planning()
    non_ui_plan["test_cases"][0]["level"] = "unit"  # type: ignore[index]
    invalid_plans.append((non_ui_plan, non_ui_data, "only browser UI test cases"))

    unpaired_plan, unpaired_data = _planning()
    unpaired_plan["test_cases"][0]["step_ids"] = []  # type: ignore[index]
    invalid_plans.append((unpaired_plan, unpaired_data, "parallel step_id"))

    duplicate_step_plan, duplicate_step_data = _planning()
    duplicate_step_plan["test_cases"][0]["steps"].append("検索結果を確認する")  # type: ignore[index,union-attr]
    duplicate_step_plan["test_cases"][0]["step_ids"].append("open-expense-search")  # type: ignore[index,union-attr]
    invalid_plans.append((duplicate_step_plan, duplicate_step_data, "globally unique"))

    missing_data_plan, missing_data = _planning()
    missing_data["data_sets"] = []
    invalid_plans.append((missing_data_plan, missing_data, "test_data_id values"))

    unknown_data_plan, unknown_data = _planning()
    unknown_data_plan["test_cases"][0]["test_data_refs"] = ["missing-data"]  # type: ignore[index]
    invalid_plans.append((unknown_data_plan, unknown_data, "missing TestDataPlan data refs"))

    uncovered_plan, uncovered_data = _planning()
    uncovered_data["generation_flows"] = []
    invalid_plans.append((uncovered_plan, uncovered_data, "cover exactly every"))

    opaque_action_plan, opaque_action_data = _planning()
    opaque_action_data["generation_flows"][0]["steps"][0]["business_action"] = "x"  # type: ignore[index]
    invalid_plans.append((opaque_action_plan, opaque_action_data, "business_action"))

    missing_playwright_plan, missing_playwright_data = _planning()
    missing_playwright_data["generation_flows"][0]["steps"][0].pop("playwright")  # type: ignore[index,union-attr]
    invalid_plans.append(
        (missing_playwright_plan, missing_playwright_data, "no Playwright action")
    )

    non_ui_ref_plan, non_ui_ref_data = _planning()
    non_ui_ref_data["generation_flows"][0]["steps"][0]["channel"] = "sql"  # type: ignore[index]
    invalid_plans.append((non_ui_ref_plan, non_ui_ref_data, "Only executable Playwright"))

    cleanup_plan, cleanup_data = _planning()
    cleanup_data["generation_flows"][0]["cleanup_steps"] = [  # type: ignore[index]
        {"step_id": "cleanup-1", "business_action": "x"}
    ]
    invalid_plans.append((cleanup_plan, cleanup_data, "Cleanup business_action"))

    for invalid_test_plan, invalid_test_data_plan, expected in invalid_plans:
        with pytest.raises(ValueError, match=expected):
            _validate_planning_alignment(
                test_plan=invalid_test_plan,
                test_data_plan=invalid_test_data_plan,
                ui_impacted=True,
            )


def test_natural_language_guard_rejects_non_text_and_control_characters() -> None:
    assert not _looks_like_natural_language(None)
    assert not _looks_like_natural_language("検索\n実行")


def test_project_target_data_gate_requires_local_secret_only_for_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(connection_alias="expense_test_db", dialect="postgresql")
    repository = SimpleNamespace(
        validate_plan=lambda **_values: ["binding-policy"],
        get=lambda _project_id: profile,
    )
    monkeypatch.setattr(
        coding_task_module,
        "TargetDataProfileRepository",
        lambda _connection: repository,
    )
    monkeypatch.setattr(
        coding_task_module,
        "TargetDataSecretStore",
        lambda: SimpleNamespace(configured=lambda **_values: False),
    )

    monkeypatch.setattr(coding_task_module, "test_data_plan_channels", lambda _plan: {"sql"})
    assert coding_task_module._project_target_data_blockers(
        connection=object(), project_id="project-1", plan={}
    ) == [
        "Project Target Data connection Secret is not configured for SQL execution",
        "binding-policy",
    ]

    monkeypatch.setattr(coding_task_module, "test_data_plan_channels", lambda _plan: {"ui"})
    assert coding_task_module._project_target_data_blockers(
        connection=object(), project_id="project-1", plan={}
    ) == ["binding-policy"]


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


def test_document_discovery_reuses_the_bound_canonical_context_package() -> None:
    service = object.__new__(CopilotCodingTaskService)
    service._requests = SimpleNamespace(
        get_change_request=lambda _request_id: {
            "project_id": "project-1",
            "analysis_case_id": "case-1",
            "artifact": {
                "source_document_ref": "design/source.xlsx",
                "target_document_ref": "design/target.xlsx",
            },
        },
        impact_report=lambda **_values: {"context_package_id": "context-1"},
    )
    service._artifacts = SimpleNamespace(
        get=lambda _artifact_id: {
            "artifact_type": "ContextPackage",
            "document_snapshot_id": "snapshot-1",
            "search_index_build_id": "index-1",
            "context_items": [
                {
                    "document_id": "document-1",
                    "section_id": "section-1",
                    "heading_path": ["経費検索"],
                    "compressed_summary": "状態検索の仕様",
                    "relevance_reason": "要件と一致",
                    "evidence_refs": ["document:document-1"],
                }
            ],
        }
    )
    service._bind_real_documents = lambda **values: values["candidates"]

    result = service._document_discovery("change-1")

    assert result["status"] == "ready"
    assert result["mode"] == "canonical_hybrid_rag"
    assert result["context_package_id"] == "context-1"
    assert result["explicit_document_refs"] == [
        "design/source.xlsx",
        "design/target.xlsx",
    ]


def test_document_discovery_fails_closed_without_one_embedding_profile() -> None:
    service = object.__new__(CopilotCodingTaskService)
    service._requests = SimpleNamespace(
        get_change_request=lambda _request_id: {
            "project_id": "project-1",
            "analysis_case_id": None,
            "artifact": {
                "requirement_text": " ",
                "business_rules": [{"text": "差戻し状態を検索できる"}],
            },
        }
    )
    service._profile_repository = SimpleNamespace(
        list_active_by_type=lambda **_values: ()
    )

    result = service._document_discovery("change-1")

    assert result["status"] == "blocked"
    assert result["candidates"] == []
    assert "exactly one active EmbeddingProfile" in str(result["blocking_reason"])


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


def test_v3_ui_verification_links_scenario_step_and_screenshot_to_frozen_binding() -> None:
    test_plan, test_data_plan = _planning()
    payload = {
        "binding_id": "binding-1",
        "project_id": "project-1",
        "run_id": "run-1",
        "test_data_id": "expense-returned",
        "business_unique_keys": [{"name": "expense_no", "value": "EXP-1"}],
        "screen_identity_values": [{"name": "expense_no", "value": "EXP-1"}],
        "identity_digest": canonical_digest(
            {
                "business_unique_keys": [{"name": "expense_no", "value": "EXP-1"}],
                "screen_identity_values": [{"name": "expense_no", "value": "EXP-1"}],
            }
        ),
    }
    binding = {
        **payload,
        "content_digest": canonical_digest(payload),
        "evidence_ref": "artifact://execution/bindings/binding-1",
    }
    execution = {
        "project_id": "project-1",
        "run_id": "run-1",
        "data_bindings": [binding],
        "flow_results": [
            {
                "flow_id": "expense-returned-flow",
                "status": "passed",
                "step_results": [
                    {
                        "step_id": "open-expense-search",
                        "channel": "ui",
                        "status": "passed",
                        "test_data_binding_refs": ["binding-1"],
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
                "test_data_binding_ref": "binding-1",
            }
        ],
    }

    assert _ui_scenario_binding_refs(
        ui_cases=test_plan["test_cases"],  # type: ignore[arg-type]
        test_data_plan=test_data_plan,
        execution_result=execution,
    ) == {"expense-returned-ui": ["binding-1"]}

    execution["evidence"][0]["test_data_binding_ref"] = "foreign-binding"  # type: ignore[index]
    with pytest.raises(ValueError, match="does not resolve"):
        _ui_scenario_binding_refs(
            ui_cases=test_plan["test_cases"],  # type: ignore[arg-type]
            test_data_plan=test_data_plan,
            execution_result=execution,
        )


def test_publish_request_rejects_invalid_task_shapes(tmp_path: Path) -> None:
    revision_context = {
        "proposal_id": "proposal-1",
        "source_orchestration_id": "orchestration-1",
        "source_test_plan_id": "plan-1",
        "instruction": "検索手順を追加する",
        "confirmed_operations_json": "[]",
        "selections_json": "[]",
    }
    execution_basis = {
        "impact_report_id": "impact-1",
        "document_change_refs": ["change-1"],
    }
    invalid = (
        ({"task_summary": " "}, "must not be blank"),
        ({"task_summary": "x" * 10_001}, "exceeds"),
        ({"edit_packet_id": "packet-1"}, "supplied together"),
        ({"edit_packet_id": " ", "approval_grant_id": "grant-1"}, "must not be blank"),
        ({"edit_packet_id": "packet-1", "approval_grant_id": " "}, "must not be blank"),
        ({"retry_of_coding_task_id": " "}, "must not be blank"),
        ({"attempt_number": 0}, "positive"),
        ({"task_kind": "unknown"}, "Unsupported"),
        (
            {"task_kind": "ui_test_plan_revision", "initial_stage": "ui_test_revision"},
            "requires revision context",
        ),
        (
            {
                "task_kind": "ui_test_plan_revision",
                "initial_stage": "ui_test_revision",
                "plan_revision_context": {**revision_context, "instruction": " "},
            },
            "context is incomplete",
        ),
        (
            {
                "task_kind": "ui_test_plan_revision",
                "initial_stage": "ui_test_revision",
                "plan_revision_context": revision_context,
                "edit_packet_id": "packet-1",
                "approval_grant_id": "grant-1",
            },
            "must not receive code edit scope",
        ),
        (
            {
                "task_kind": "ui_test_plan_revision",
                "initial_stage": "ui_test_revision",
                "plan_revision_context": revision_context,
                "execution_basis": execution_basis,
            },
            "must not receive execution basis",
        ),
        (
            {
                "task_kind": "change_execution",
                "initial_stage": "compile_test",
                "edit_packet_id": "packet-1",
                "approval_grant_id": "grant-1",
            },
            "requires confirmed scope",
        ),
        (
            {
                "task_kind": "change_execution",
                "initial_stage": "compile_test",
                "edit_packet_id": "packet-1",
                "approval_grant_id": "grant-1",
                "execution_basis": {"impact_report_id": " ", "document_change_refs": []},
            },
            "incomplete execution basis",
        ),
        (
            {
                "task_kind": "change_execution",
                "initial_stage": "compile_test",
                "edit_packet_id": "packet-1",
                "approval_grant_id": "grant-1",
                "execution_basis": execution_basis,
                "plan_revision_context": revision_context,
            },
            "revision-only fields",
        ),
        ({"initial_stage": "compile_test"}, "invalid revision-only fields"),
    )
    for changes, message in invalid:
        with pytest.raises(ValueError, match=message):
            _publish_request(tmp_path, **changes)


def test_publish_creates_a_compact_unbound_change_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    published: list[dict[str, object]] = []

    class Tasks:
        def get(self, _task_id: str) -> object:
            raise ValueError("missing")

        def publish(self, **values: object) -> object:
            published.append(values)
            return SimpleNamespace(created=True, coding_task_id="task-publish-1")

        def view(self, _task_id: str) -> dict[str, object]:
            return {"task": published[0]["artifact"], "state": "submitted"}

    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._contracts = object()
    service._tasks = Tasks()
    service._provider = coding_task_module.LocalBridgeCopilotProvider()
    service._requests = SimpleNamespace(
        get_change_request=lambda _request_id: {
            "project_id": "project-1",
            "analysis_case_id": None,
            "artifact": {
                "requirement_text": "経費検索条件を変更する",
                "source_document_ref": None,
                "target_document_ref": None,
                "business_rules": [],
                "ambiguity_status": "clear",
            },
        }
    )
    monkeypatch.setattr(
        coding_task_module,
        "detect_project_stack",
        lambda _root: SimpleNamespace(copilot_context=lambda: {"stack": "python"}),
    )

    result = service.publish(_publish_request(tmp_path))

    assert result["created"] is True
    artifact = published[0]["artifact"]
    assert artifact["task_kind"] == "change_delivery"
    assert artifact["provider_contract"]["provider_id"] == "vscode_github_copilot"
    assert artifact["change_context"]["requirement_text"] == "経費検索条件を変更する"
    assert published[0]["workspace_root"] == tmp_path


def test_publish_replay_and_bridge_lifecycle_delegation(tmp_path: Path) -> None:
    request = _publish_request(tmp_path)
    task_artifact = {
        "task_summary": request.task_summary,
        "created_by": request.actor,
        "task_kind": request.task_kind,
        "initial_stage": request.initial_stage,
    }
    record = SimpleNamespace(
        change_request_id=request.change_request_id,
        project_id=request.project_id,
        edit_packet_id=None,
        approval_grant_id=None,
        workspace_root=str(tmp_path.resolve()),
        retry_of_coding_task_id=None,
        attempt_number=1,
        state="cancelled",
    )
    calls: list[tuple[str, dict[str, object]]] = []

    class Tasks:
        def get(self, _task_id: str) -> object:
            return record

        def view(self, _task_id: str) -> dict[str, object]:
            return {"task": task_artifact, "state": record.state}

        def claim_next(self, **values: object) -> dict[str, object]:
            calls.append(("claim", values))
            return {"task": task_artifact}

        def accept(self, **values: object) -> dict[str, object]:
            calls.append(("accept", values))
            return {"state": "in_progress"}

        def resume(self, **values: object) -> dict[str, object]:
            calls.append(("resume", values))
            return {"state": "in_progress"}

        def cancel(self, **values: object) -> dict[str, object]:
            calls.append(("cancel", values))
            return {"state": "cancelled"}

        def latest_for_request(self, *args: object, **kwargs: object) -> dict[str, object]:
            calls.append(("latest", {"args": args, **kwargs}))
            return {"task": task_artifact}

    service = object.__new__(CopilotCodingTaskService)
    service._tasks = Tasks()

    replay = service.publish(request)
    assert replay["created"] is False
    assert service.claim_next(workspace_root=tmp_path, consumer_id="vscode") is not None
    assert (
        service.accept(
            coding_task_id="task-publish-1",
            workspace_root=tmp_path,
            consumer_id="vscode",
            actor="owner",
        )["state"]
        == "in_progress"
    )
    assert (
        service.resume(
            coding_task_id="task-publish-1",
            workspace_root=tmp_path,
            consumer_id="vscode",
        )["state"]
        == "in_progress"
    )
    assert (
        service.cancel(
            coding_task_id="task-publish-1",
            change_request_id="change-1",
            actor="owner",
            reason=" stop ",
            idempotency_key="cancel-1",
        )["state"]
        == "cancelled"
    )
    assert service.view("task-publish-1")["state"] == "cancelled"
    assert service.latest_for_request("change-1", task_kind="change_delivery") is not None

    with pytest.raises(ValueError, match="consumer_id"):
        service.claim_next(workspace_root=tmp_path, consumer_id=" ")
    with pytest.raises(ValueError, match="consumer_id"):
        service.resume(coding_task_id="task-publish-1", workspace_root=tmp_path, consumer_id=" ")
    with pytest.raises(ValueError, match="outside requested"):
        service.cancel(
            coding_task_id="task-publish-1",
            change_request_id="other",
            actor="owner",
            reason="stop",
            idempotency_key="cancel-2",
        )
    with pytest.raises(ValueError, match="reason must not be blank"):
        service.cancel(
            coding_task_id="task-publish-1",
            change_request_id="change-1",
            actor="owner",
            reason=" ",
            idempotency_key="cancel-3",
        )

    cancel_call = next(values for name, values in calls if name == "cancel")
    assert cancel_call["reason"] == "stop"


def test_retry_republishes_the_failed_task_with_a_new_bounded_scope(
    tmp_path: Path,
) -> None:
    previous = SimpleNamespace(
        change_request_id="change-1",
        project_id="project-1",
        state="failed",
        attempt_number=2,
    )
    service = object.__new__(CopilotCodingTaskService)
    service._tasks = SimpleNamespace(
        get=lambda _task_id: previous,
        view=lambda _task_id: {"task": {"task_summary": "再実行する"}},
    )
    captured: dict[str, object] = {}

    def publish(request: CopilotCodingTaskPublishRequest) -> dict[str, object]:
        captured["request"] = request
        return {"created": True}

    service.publish = publish  # type: ignore[method-assign]

    assert service.retry(
        coding_task_id="task-1",
        retry_coding_task_id="task-2",
        change_request_id="change-1",
        actor="developer",
        idempotency_key="retry-2",
        edit_packet_id="packet-2",
        approval_grant_id="grant-2",
        workspace_root=tmp_path,
    ) == {"created": True}
    request = captured["request"]
    assert request.retry_of_coding_task_id == "task-1"  # type: ignore[union-attr]
    assert request.attempt_number == 3  # type: ignore[union-attr]

    previous.state = "completed"
    with pytest.raises(ValueError, match="cancelled or failed"):
        service.retry(
            coding_task_id="task-1",
            retry_coding_task_id="task-3",
            change_request_id="change-1",
            actor="developer",
            idempotency_key="retry-3",
            edit_packet_id="packet-3",
            approval_grant_id="grant-3",
            workspace_root=tmp_path,
        )


def test_rollback_and_bind_document_evidence_and_execution_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    task = SimpleNamespace(
        project_id="project-1",
        change_request_id="change-1",
        state="in_progress",
    )

    class Tasks:
        def get(self, _task_id: str) -> object:
            return task

        def bind_document_discovery(self, **values: object) -> None:
            calls["discovery"] = values

        def bind_execution_scope(self, **values: object) -> None:
            calls["scope"] = values

        def view(self, _task_id: str) -> dict[str, object]:
            return {"state": "in_progress"}

    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._contracts = object()
    service._root = tmp_path
    service._tasks = Tasks()
    service._recorded_output = lambda *_args: {
        "document_ids": ["document-1"],
        "source_document_snapshot_id": "snapshot-before",
        "target_document_snapshot_id": "snapshot-after",
    }

    class DocumentChangeService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def rollback_materialized(self, **values: object) -> tuple[Path, ...]:
            calls["rollback"] = values
            return (tmp_path / "design.xlsx",)

    class ContextService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def get(self, _request: object) -> dict[str, object]:
            return {
                "edit_packet": {
                    "repository_id": "repository-1",
                    "base_repository_revision": "a" * 40,
                }
            }

    automation = SimpleNamespace(change_request_id="change-1", project_id="project-1")
    monkeypatch.setattr(coding_task_module, "CopilotDocumentChangeService", DocumentChangeService)
    monkeypatch.setattr(coding_task_module, "CopilotTaskContextService", ContextService)
    monkeypatch.setattr(
        coding_task_module,
        "ChangeAutomationRepository",
        lambda _connection: SimpleNamespace(get=lambda _run_id: automation),
    )

    rollback = service.rollback_document_change("task-1")
    discovery = {"status": "ready", "candidates": []}
    service.bind_document_discovery(
        coding_task_id="task-1",
        automation_run_id="run-1",
        subject_digest=coding_task_module._payload_digest(discovery),
        discovery=discovery,
        actor="github-copilot",
    )
    scope_view = service.bind_execution_scope(
        coding_task_id="task-1",
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
        workspace_root=tmp_path,
        actor="github-copilot",
    )

    assert rollback["restored_paths"] == [str(tmp_path / "design.xlsx")]
    assert calls["rollback"]["document_ids"] == ("document-1",)  # type: ignore[index]
    assert calls["discovery"]["automation_run_id"] == "run-1"  # type: ignore[index]
    assert calls["scope"]["repository_id"] == "repository-1"  # type: ignore[index]
    assert scope_view == {"state": "in_progress"}


def test_document_discovery_binding_rejects_cross_scope_and_tampered_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SimpleNamespace(change_request_id="change-1", project_id="project-1")
    automation = SimpleNamespace(change_request_id="other-change", project_id="project-1")
    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._tasks = SimpleNamespace(get=lambda _task_id: task)
    monkeypatch.setattr(
        coding_task_module,
        "ChangeAutomationRepository",
        lambda _connection: SimpleNamespace(get=lambda _run_id: automation),
    )
    discovery = {"status": "ready", "candidates": []}

    with pytest.raises(ValueError, match="outside Coding Task scope"):
        service.bind_document_discovery(
            coding_task_id="task-1",
            automation_run_id="run-1",
            subject_digest=coding_task_module._payload_digest(discovery),
            discovery=discovery,
            actor="github-copilot",
        )

    automation.change_request_id = "change-1"
    with pytest.raises(ValueError, match="digest differs"):
        service.bind_document_discovery(
            coding_task_id="task-1",
            automation_run_id="run-1",
            subject_digest="tampered",
            discovery=discovery,
            actor="github-copilot",
        )


def test_coding_task_scope_helpers_fail_closed_without_canonical_bindings() -> None:
    service = object.__new__(CopilotCodingTaskService)
    service._requests = SimpleNamespace(
        get_change_request=lambda _request_id: {"analysis_case_id": None}
    )

    with pytest.raises(ValueError, match="bound Analysis Case"):
        service._bound_change_request_case("change-1")
    with pytest.raises(ValueError, match="execution scope is not bound"):
        _bound_task_scope(
            SimpleNamespace(
                analysis_case_id="case-1",
                edit_packet_id=None,
                approval_grant_id="grant-1",
            )
        )
    with pytest.raises(ValueError, match="Unsupported Copilot Change Task stage"):
        _stage_contract("invented")


def test_record_result_requires_exact_command_and_coverage_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    task = SimpleNamespace(
        project_id="project-1",
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
        base_repository_revision="a" * 40,
        state="in_progress",
    )
    command_event = {
        "event_type": "command_recorded",
        "payload": {
            "command_execution_id": "command-1",
            "tested_content_digest": "content-digest",
            "coverage_report": {
                "path": "coverage.xml",
                "format": "cobertura",
                "digest": "coverage-digest",
            },
        },
    }

    class Tasks:
        def get(self, _task_id: str) -> object:
            return task

        def view(self, _task_id: str) -> dict[str, object]:
            return {"events": [command_event]}

        def bind_edit_result(self, **values: object) -> None:
            calls["bound_result"] = values

    class Inspector:
        def inspect_committed(self, *_args: object, **values: object) -> object:
            calls["inspect"] = values
            return SimpleNamespace(content_digest="content-digest")

    class Result:
        def to_dict(self) -> dict[str, object]:
            return {"status": "in_scope", "tests_passed": True}

    class ResultService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(self, request: object) -> Result:
            calls["request"] = request
            return Result()

    coverage = coding_task_module.ChangedLineCoverageEvidence(
        evidence_refs=("command-1",),
        executable_lines=(("src/a.py", (1,)),),
        covered_lines=(("src/a.py", (1,)),),
    )
    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._contracts = object()
    service._tasks = Tasks()
    service._artifacts = SimpleNamespace(
        get=lambda _artifact_id: {
            "artifact_type": "ApprovalGrant",
            "editable_files": ["src/a.py"],
        }
    )
    monkeypatch.setattr(coding_task_module, "GitWorktreeDiffInspector", Inspector)
    monkeypatch.setattr(coding_task_module, "EditResultService", ResultService)
    monkeypatch.setattr(
        coding_task_module,
        "load_coverage_report",
        lambda **values: calls.setdefault("coverage", values) and coverage,
    )

    result = service.record_result(
        coding_task_id="task-1",
        edit_result_id="result-1",
        workspace_root=tmp_path,
        test_result_refs=("command-1",),
        tests_passed=True,
        coverage_report_command_execution_id="command-1",
    )

    assert result == {
        "status": "in_scope",
        "tests_passed": True,
        "coding_task_state": "in_progress",
    }
    assert calls["coverage"]["expected_digest"] == "coverage-digest"  # type: ignore[index]
    assert calls["bound_result"]["committed"] is True  # type: ignore[index]
    assert calls["request"].changed_line_coverage is coverage  # type: ignore[union-attr]

    command_event["payload"]["tested_content_digest"] = "stale"
    with pytest.raises(ValueError, match="exact tested diff"):
        service.record_result(
            coding_task_id="task-1",
            edit_result_id="result-2",
            workspace_root=tmp_path,
            test_result_refs=("command-1", "missing-command"),
            tests_passed=True,
        )


def test_run_command_binds_the_approved_result_to_the_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    task = SimpleNamespace(
        state="in_progress",
        project_id="project-1",
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
    )

    class CommandResult:
        def to_dict(self) -> dict[str, object]:
            return {"status": "passed", "exit_code": 0}

    class CommandService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(self, request: object) -> CommandResult:
            calls["request"] = request
            return CommandResult()

    class Tasks:
        def get(self, _task_id: str) -> object:
            return task

        def bind_command(self, **values: object) -> None:
            calls["binding"] = values

    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._contracts = object()
    service._profiles = object()
    service._tasks = Tasks()
    monkeypatch.setattr(coding_task_module, "ApprovedCommandService", CommandService)

    result = service.run_command(
        coding_task_id="task-1",
        command_execution_id="command-1",
        command_ref="unit-test",
        workspace_root=tmp_path,
    )

    assert result == {
        "status": "passed",
        "exit_code": 0,
        "coding_task_state": "in_progress",
    }
    assert calls["request"].approval_grant_id == "grant-1"  # type: ignore[union-attr]
    assert calls["binding"]["command_execution_id"] == "command-1"  # type: ignore[index]


@pytest.mark.parametrize(
    ("stage", "arguments", "method_name"),
    [
        (
            "document_change",
            {"document_ids": ("document-1",)},
            "_record_document_outputs",
        ),
        (
            "code_scope",
            {"code_scope": ({"target_path": "src/a.py"},)},
            "_record_code_scope_output",
        ),
        (
            "test_planning",
            {"test_plan": {"id": "plan"}, "test_data_plan": {"id": "data"}},
            "_record_test_planning_outputs",
        ),
        (
            "ui_test_revision",
            {"test_plan": {"id": "plan"}, "test_data_plan": {"id": "data"}},
            "_record_ui_test_revision_outputs",
        ),
    ],
)
def test_record_change_outputs_dispatches_each_supported_stage(
    tmp_path: Path,
    stage: str,
    arguments: dict[str, object],
    method_name: str,
) -> None:
    task = SimpleNamespace(state="in_progress", workspace_root=str(tmp_path.resolve()))
    service = object.__new__(CopilotCodingTaskService)
    service._tasks = SimpleNamespace(get=lambda _task_id: task)
    calls: dict[str, object] = {}

    def record(**values: object) -> dict[str, object]:
        calls.update(values)
        return {"recorded_stage": stage}

    setattr(service, method_name, record)

    result = service.record_change_outputs(
        coding_task_id="task-1",
        workspace_root=tmp_path,
        output_stage=stage,
        **arguments,  # type: ignore[arg-type]
    )

    assert result == {"recorded_stage": stage}
    assert calls["coding_task_id"] == "task-1"


def test_record_change_outputs_rejects_unloaded_wrong_workspace_and_mixed_stages(
    tmp_path: Path,
) -> None:
    other_root = tmp_path / "other"
    other_root.mkdir()
    task = SimpleNamespace(state="submitted", workspace_root=str(tmp_path.resolve()))
    service = object.__new__(CopilotCodingTaskService)
    service._tasks = SimpleNamespace(get=lambda _task_id: task)

    with pytest.raises(ValueError, match="actor must not be blank"):
        service.record_change_outputs(
            coding_task_id="task-1",
            workspace_root=tmp_path,
            output_stage="document_change",
            actor=" ",
        )
    with pytest.raises(ValueError, match="must be loaded"):
        service.record_change_outputs(
            coding_task_id="task-1",
            workspace_root=tmp_path,
            output_stage="document_change",
        )

    task.state = "in_progress"
    with pytest.raises(ValueError, match="Workspace does not match"):
        service.record_change_outputs(
            coding_task_id="task-1",
            workspace_root=other_root,
            output_stage="document_change",
        )

    invalid_stage_payloads = (
        ("document_change", {"code_scope": ({"target_path": "src/a.py"},)}),
        ("code_scope", {"document_ids": ("document-1",)}),
        ("test_planning", {}),
        ("ui_test_revision", {}),
    )
    for output_stage, values in invalid_stage_payloads:
        with pytest.raises(ValueError):
            service.record_change_outputs(
                coding_task_id="task-1",
                workspace_root=tmp_path,
                output_stage=output_stage,
                **values,  # type: ignore[arg-type]
            )

    with pytest.raises(ValueError, match=r"Unsupported.*output stage"):
        service.record_change_outputs(
            coding_task_id="task-1",
            workspace_root=tmp_path,
            output_stage="invented",
        )


def test_record_change_outputs_materializes_only_documents_from_ready_rag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    task = SimpleNamespace(
        state="in_progress",
        workspace_root=str(tmp_path.resolve()),
        change_request_id="change-1",
        project_id="project-1",
    )

    class Tasks:
        def get(self, _task_id: str) -> object:
            return task

        def record_change_outputs(self, **values: object) -> None:
            calls["recorded"] = values

    class DocumentChangeService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def materialize(self, **values: object) -> object:
            calls["materialized"] = values
            return SimpleNamespace(
                change_refs=("structured-change-1",),
                document_ids=("document-1",),
                source_snapshot_id="snapshot-before",
                target_snapshot_id="snapshot-after",
            )

    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._root = tmp_path
    service._tasks = Tasks()
    service._requests = SimpleNamespace(
        get_change_request=lambda _request_id: {"analysis_case_id": "case-1"}
    )
    service._document_discovery_for_task = lambda *_args: {
        "status": "ready",
        "document_snapshot_id": "snapshot-before",
        "search_index_build_id": "index-1",
        "candidates": [{"document_id": "document-1"}],
    }
    monkeypatch.setattr(coding_task_module, "CopilotDocumentChangeService", DocumentChangeService)

    result = service.record_change_outputs(
        coding_task_id="task-1",
        workspace_root=tmp_path,
        output_stage="document_change",
        document_ids=("document-1",),
    )

    assert result["document_change_refs"] == ["structured-change-1"]
    assert result["next_stage"] == "code_scope"
    assert calls["materialized"]["source_snapshot_id"] == "snapshot-before"  # type: ignore[index]
    assert calls["recorded"]["output_stage"] == "document_change"  # type: ignore[index]


def test_test_planning_persists_only_a_fully_covered_executable_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {"stored": []}
    task = SimpleNamespace(
        project_id="project-1",
        change_request_id="change-1",
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
        base_repository_revision="a" * 40,
        workspace_root=str(tmp_path),
        state="in_progress",
    )
    view = {
        "edit_results": [
            {
                "validation_mode": "committed",
                "status": "in_scope",
                "changed_paths": ["src/a.py"],
                "tests_passed": True,
                "command_evidence_status": "verified",
                "changed_line_coverage_status": "passed",
                "result_repository_revision": "b" * 40,
            }
        ],
        "commands": [{"command_ref": "unit-test", "status": "passed", "exit_code": 0}],
    }

    class Tasks:
        def get(self, _task_id: str) -> object:
            return task

        def view(self, _task_id: str) -> dict[str, object]:
            return view

        def record_change_outputs(self, **values: object) -> None:
            calls["recorded"] = values

    class Artifacts:
        def get(self, artifact_id: str) -> dict[str, object] | None:
            return {
                "grant-1": {
                    "artifact_type": "ApprovalGrant",
                    "editable_files": ["src/a.py"],
                    "test_files": ["tests/test_a.py"],
                    "allowed_test_command_refs": ["unit-test"],
                },
                "impact-1": {
                    "artifact_type": "ImpactReport",
                    "ui_impact_status": "impacted",
                    "required_ui_scenario_refs": ["ui-case-1"],
                },
            }.get(artifact_id)

        def store(self, **values: object) -> None:
            calls["stored"].append(values)  # type: ignore[union-attr]

    class Inspector:
        def inspect_committed(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(result_sha="b" * 40)

    service = object.__new__(CopilotCodingTaskService)
    service._existing_test_data = SimpleNamespace(
        list_for_project=lambda _project_id, **_kwargs: (),
        profiles=lambda _project_id: (),
    )
    service._tasks = Tasks()
    service._artifacts = Artifacts()
    service._requests = SimpleNamespace(
        project_test_base_url=lambda _project_id: "http://tested.local",
        get_change_request=lambda _request_id: {
            "artifact": {"business_rules": [{"business_rule_id": "rule-1"}]}
        },
    )
    service._code_scope_output = lambda _task_id: {
        "output_stage": "code_scope",
        "impact_report_id": "impact-1",
        "document_change_refs": ["change-1"],
    }
    monkeypatch.setattr(coding_task_module, "GitWorktreeDiffInspector", Inspector)
    monkeypatch.setattr(coding_task_module, "_validate_planning_alignment", lambda **_v: None)
    monkeypatch.setattr(
        coding_task_module, "_validate_required_ui_scenario_scope", lambda **_v: None
    )
    monkeypatch.setattr(
        coding_task_module,
        "validate_test_data_plan_artifact",
        lambda _plan, **_kwargs: [],
    )
    monkeypatch.setattr(coding_task_module, "test_data_plan_channels", lambda _plan: {"ui"})
    monkeypatch.setattr(
        coding_task_module,
        "canonical_artifact_refs_from_output",
        lambda _output: frozenset({"change-1"}),
    )
    monkeypatch.setattr(
        coding_task_module,
        "assess_planned_business_coverage",
        lambda **_values: {"status": "passed", "coverage_percent": 100},
    )
    test_plan = {
        "artifact_type": "TestPlan",
        "schema_version": "v2",
        "plan_kind": "ui",
        "project_id": "project-1",
        "change_request_id": "change-1",
        "test_plan_id": "plan-1",
        "status": "ready",
    }
    test_data_plan = {
        "artifact_type": "TestDataPlan",
        "schema_version": "v3",
        "project_id": "project-1",
        "test_plan_id": "plan-1",
        "test_data_plan_id": "data-plan-1",
        "status": "ready",
    }

    result = service._record_test_planning_outputs(
        coding_task_id="task-1",
        test_plan=test_plan,
        test_data_plan=test_data_plan,
    )

    assert [item["artifact_id"] for item in calls["stored"]] == [  # type: ignore[union-attr]
        "plan-1",
        "data-plan-1",
    ]
    assert calls["recorded"]["complete"] is True  # type: ignore[index]
    assert result["recorded_stage"] == "test_planning"
    assert result["coding_task_state"] == "in_progress"


def test_ui_plan_revision_context_is_read_only_and_contains_confirmed_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = SimpleNamespace(
        change_request_id="change-1",
        current_stage="ui_test_revision",
        state="in_progress",
        workspace_root=str(tmp_path),
    )
    immutable = {
        "coding_task_id": "revision-task-1",
        "change_request_id": "change-1",
        "project_id": "project-1",
        "task_summary": "UI テスト計画を再作成する",
        "attempt_number": 1,
        "task_kind": "ui_test_plan_revision",
        "plan_revision_context": {
            "source_orchestration_id": "orchestration-1",
            "instruction": "検索手順を追加する",
            "confirmed_operations_json": '[{"operation":"append"}]',
            "locator_failure_evidence_json": (
                '{"failure_stage":"formal_ui_run_pre_action_validation",'
                '"failures":[{"step_id":"search-expense",'
                '"failure_reason":"record scope matched 0 records"}]}'
            ),
        },
    }
    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._existing_test_data = SimpleNamespace(
        list_for_project=lambda _project_id, **_kwargs: (),
        profiles=lambda _project_id: (),
    )
    service._contracts = object()
    service._tasks = SimpleNamespace(
        get=lambda _task_id: task,
        view=lambda _task_id: {"task": immutable},
        begin_mcp=lambda **_values: task,
    )
    monkeypatch.setattr(
        coding_task_module,
        "ChangeOrchestrationRepository",
        lambda *_args: SimpleNamespace(
            bundle=lambda _orchestration_id: {
                "test_plan": {"test_plan_id": "plan-1"},
                "test_data_plan": {"test_data_plan_id": "data-plan-1"},
            }
        ),
    )
    monkeypatch.setattr(
        coding_task_module,
        "TargetDataProfileRepository",
        lambda _connection: SimpleNamespace(get=lambda _project_id: None),
    )

    result = service.get_mcp_context(
        coding_task_id="revision-task-1", workspace_root=tmp_path
    )

    assert result["stage_contract"]["id"] == "ui_test_revision"  # type: ignore[index]
    assert result["inputs"]["revision_instruction"] == "検索手順を追加する"  # type: ignore[index]
    assert result["inputs"]["confirmed_change_summary"] == [  # type: ignore[index]
        {"operation": "append"}
    ]
    assert result["inputs"]["locator_failure_evidence"] == {  # type: ignore[index]
        "failure_stage": "formal_ui_run_pre_action_validation",
        "failures": [
            {
                "step_id": "search-expense",
                "failure_reason": "record scope matched 0 records",
            }
        ],
    }
    assert result["constraints"] == {
        "execution_scope": {"bound": False, "read_only": True}
    }


def test_copilot_context_rejects_blank_actor_before_repository_access(tmp_path: Path) -> None:
    service = object.__new__(CopilotCodingTaskService)

    with pytest.raises(ValueError, match="context actor must not be blank"):
        service.get_mcp_context(
            coding_task_id="revision-task-1",
            workspace_root=tmp_path,
            actor=" ",
        )


def test_ui_plan_revision_records_only_a_validated_regenerated_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    task = SimpleNamespace(
        project_id="project-1",
        change_request_id="change-1",
        state="in_progress",
    )
    context = {
        "proposal_id": "proposal-1",
        "source_orchestration_id": "orchestration-1",
        "source_test_plan_id": "source-plan-1",
        "instruction": "検索手順を追加する",
        "confirmed_operations_json": '[{"operation":"append"}]',
        "selections_json": '{"case":"ui-case-1"}',
    }

    class Tasks:
        def get(self, _task_id: str) -> object:
            return task

        def view(self, _task_id: str) -> dict[str, object]:
            return {
                "task": {
                    "task_kind": "ui_test_plan_revision",
                    "plan_revision_context": context,
                }
            }

        def record_change_outputs(self, **values: object) -> None:
            calls["recorded"] = values

    class RevisionService:
        def __init__(self, **_values: object) -> None:
            pass

        def apply_ai_regeneration(self, **values: object) -> dict[str, object]:
            calls["applied"] = values
            return {
                "revision": {
                    "revision_id": "revision-1",
                    "target_orchestration_id": "orchestration-2",
                    "target_test_plan_id": "plan-2",
                },
                "bundle": {"test_data_plan": {"test_data_plan_id": "data-plan-2"}},
            }

    service = object.__new__(CopilotCodingTaskService)
    service._connection = object()
    service._existing_test_data = SimpleNamespace(
        list_for_project=lambda _project_id, **_kwargs: (),
        profiles=lambda _project_id: (),
    )
    service._contracts = object()
    service._root = tmp_path
    service._tasks = Tasks()
    service._requests = SimpleNamespace(
        project_test_base_url=lambda _project_id: "http://tested.local"
    )
    monkeypatch.setattr(
        coding_task_module,
        "ChangeOrchestrationRepository",
        lambda *_args: SimpleNamespace(
            bundle=lambda _orchestration_id: {
                "test_plan": {"test_plan_id": "source-plan-1"},
            }
        ),
    )
    monkeypatch.setattr(
        "operamind.application.test_case_revision_service.TestCaseRevisionService",
        RevisionService,
    )
    monkeypatch.setattr(coding_task_module, "_validate_planning_alignment", lambda **_v: None)
    monkeypatch.setattr(
        coding_task_module,
        "validate_test_data_plan_artifact",
        lambda _p, **_kwargs: [],
    )
    monkeypatch.setattr(coding_task_module, "test_data_plan_channels", lambda _p: {"ui"})
    test_plan = {
        "artifact_type": "TestPlan",
        "schema_version": "v2",
        "plan_kind": "ui",
        "project_id": "project-1",
        "change_request_id": "change-1",
        "test_plan_id": "plan-2",
        "status": "ready",
    }
    test_data_plan = {
        "artifact_type": "TestDataPlan",
        "schema_version": "v3",
        "project_id": "project-1",
        "test_plan_id": "plan-2",
        "test_data_plan_id": "data-plan-2",
        "status": "ready",
    }

    result = service._record_ui_test_revision_outputs(
        coding_task_id="revision-task-1",
        test_plan=test_plan,
        test_data_plan=test_data_plan,
    )

    assert result["revision_id"] == "revision-1"
    assert result["coding_task_state"] == "in_progress"
    assert calls["recorded"]["complete"] is True  # type: ignore[index]
    assert calls["applied"]["operations"] == [{"operation": "append"}]  # type: ignore[index]


def test_ui_plan_revision_output_rejects_a_non_revision_task() -> None:
    service = object.__new__(CopilotCodingTaskService)
    service._tasks = SimpleNamespace(
        get=lambda _task_id: SimpleNamespace(),
        view=lambda _task_id: {"task": {"task_kind": "change_delivery"}},
    )

    with pytest.raises(ValueError, match="Only a UI TestPlan revision Task"):
        service._record_ui_test_revision_outputs(
            coding_task_id="change-task-1",
            test_plan={},
            test_data_plan={},
        )


def test_service_initialization_and_verification_contract_use_repository_catalogs(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]

    service = CopilotCodingTaskService(connection=SimpleNamespace(), repository_root=root)
    contract = _stage_contract("compile_test", verification_only=True)
    fact = SimpleNamespace(
        stable_key="screen:status",
        fact_type="screen_field",
        values={"label": "状態"},
        source_refs=("design.xlsx#Sheet1!A1",),
        field_evidence=(
            SimpleNamespace(
                canonical_field="label",
                source_aliases=("項目名",),
                source_refs=("design.xlsx#Sheet1!A1",),
            ),
        ),
    )

    assert service._root == root
    assert service._provider.contract["route"] == "local_bridge"
    assert "ファイルを変更せず" in contract["goal"]
    assert _public_canonical_fact(fact)["field_evidence"] == [
        {
            "canonical_field": "label",
            "source_aliases": ["項目名"],
            "source_refs": ["design.xlsx#Sheet1!A1"],
        }
    ]
