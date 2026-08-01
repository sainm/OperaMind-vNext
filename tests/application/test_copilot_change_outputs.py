from types import SimpleNamespace

import pytest

from operamind.application.copilot_coding_task import (
    CopilotCodingTaskService,
    _public_document_discovery,
    _public_execution_scope,
    _public_task_artifact,
    _public_workspace,
    _validate_planning_alignment,
    _validate_planning_artifact_scope,
    build_bridge_task_view,
)
from operamind.application.test_data_ui_verification import _ui_scenario_evidence


def _planning() -> tuple[dict[str, object], dict[str, object]]:
    test_plan: dict[str, object] = {
        "test_cases": [
            {
                "test_case_id": "expense-returned-ui",
                "level": "ui",
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


def test_test_planning_requires_ui_flow_for_ui_impact() -> None:
    test_plan, test_data_plan = _planning()

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
        "operamind.application.copilot_coding_task."
        "OpenAICompatibleEmbeddingProvider.from_profile",
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
        "operamind.application.copilot_coding_task."
        "RequirementDocumentDiscoveryService",
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

    with pytest.raises(ValueError, match="bounded UI step and UI assertion"):
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
