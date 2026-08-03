"""User-facing projection for the single OperaMind change delivery flow.

The projection deliberately hides approval grants, worker leases, queues, and
other control-plane mechanics.  Those remain implementation details; the Web
surface exposes only the six outcomes a local user needs to follow.
"""

from __future__ import annotations

from typing import Any

FLOW_STAGE_IDS = (
    "requirement",
    "document_change",
    "code_scope",
    "compile_test",
    "ui_validation",
    "final_report",
)

_ACTIVE_STATES = {"accepted", "claimed", "in_progress", "running", "submitted"}
_FAILED_STATES = {"blocked", "cancelled", "failed", "interrupted", "rejected"}
_SUCCESS_STATES = {
    "closed",
    "completed",
    "confirmed",
    "covered",
    "passed",
    "succeeded",
    "valid",
}


def build_main_change_flow(
    *,
    request: dict[str, object],
    document_diff: dict[str, object],
    workspace: dict[str, object] | None,
    automation: dict[str, object] | None,
    copilot_task: dict[str, object] | None,
    execution: dict[str, object] | None,
) -> dict[str, object]:
    """Build the only workflow shape exposed by the Web application."""

    stages = [
        _requirement_stage(request, automation),
        _document_stage(document_diff, automation, copilot_task),
        _code_scope_stage(workspace, automation),
        _compile_test_stage(workspace, copilot_task, execution, automation),
        _ui_stage(execution, automation, request),
        _report_stage(execution, automation),
    ]
    current_index = next(
        (
            index
            for index, stage in enumerate(stages)
            if stage["status"] not in {"completed", "not_required"}
        ),
        len(stages) - 1,
    )
    stages = [
        stage
        if index <= current_index
        else {
            **stage,
            "status": "waiting",
            "blocking_reasons": [],
        }
        for index, stage in enumerate(stages)
    ]
    blockers = [
        blocker for stage in stages for blocker in _string_list(stage.get("blocking_reasons"))
    ]
    completed = sum(stage["status"] in {"completed", "not_required"} for stage in stages)
    request_artifact = _dict(request.get("artifact"))
    request_id = request.get("change_request_id") or request_artifact.get("change_request_id")
    project_id = request.get("project_id") or request_artifact.get("project_id")
    if not isinstance(request_id, str) or not isinstance(project_id, str):
        raise ValueError("Main change flow requires Change Request identity")
    return {
        "change_request_id": request_id,
        "project_id": project_id,
        "status": (
            "blocked" if blockers else "completed" if completed == len(stages) else "in_progress"
        ),
        "current_stage": stages[current_index]["stage_id"],
        "progress_percent": round(completed * 100 / len(stages)),
        "stages": stages,
        "blocking_reasons": blockers,
    }


def _requirement_stage(
    request: dict[str, object], automation: dict[str, object] | None
) -> dict[str, object]:
    artifact = _dict(request.get("artifact"))
    rules = _dict_list(artifact.get("business_rules"))
    ambiguities = _string_list(artifact.get("ambiguities"))
    automation_stage = str((automation or {}).get("current_stage") or "")
    automation_status = str((automation or {}).get("status") or "")
    blocked = automation_stage == "requirement_confirmation" and automation_status == "blocked"
    waiting = automation_stage == "requirement_confirmation" and not blocked
    requirement = str(artifact.get("requirement_text") or "").strip()
    return _stage(
        "requirement",
        "変更要件",
        "blocked" if blocked else "waiting" if waiting else "completed",
        requirement or "登録済みの業務ルールを変更要件として使用します。",
        "user",
        blocking_reasons=(ambiguities or ["変更要件が差し戻されました。"] if blocked else []),
        details={
            "requirement_text": requirement or None,
            "business_rules": [rule.get("text") for rule in rules if rule.get("text")],
            "confirmation": _pending_confirmation(automation, {"requirement"}),
        },
    )


def _document_stage(
    document_diff: dict[str, object],
    automation: dict[str, object] | None,
    copilot_task: dict[str, object] | None,
) -> dict[str, object]:
    changes = _dict_list(document_diff.get("changes"))
    automation_stage = str((automation or {}).get("current_stage") or "")
    automation_status = str((automation or {}).get("status") or "")
    blockers = _automation_blockers(
        automation,
        {
            "rag_document_confirmation",
            "document_generation",
            "document_revision",
            "document_confirmation",
        },
    )
    if blockers:
        status = "blocked"
    elif automation_stage in {
        "rag_document_confirmation",
        "document_generation",
        "document_revision",
        "document_confirmation",
    }:
        status = "running" if automation_status == "running" else "waiting"
    elif changes:
        status = "completed"
    else:
        status = "waiting"
    ai_executor, ai_source = _ai_execution_source(copilot_task)
    return _stage(
        "document_change",
        "設計書差分",
        status,
        (
            f"{ai_source} が作成した設計書差分は {len(changes)} 件です。"
            if changes
            else f"RAG で対象設計書を特定し、{ai_source} が修正します。"
        ),
        ai_executor,
        blocking_reasons=blockers,
        details={
            "change_count": len(changes),
            "ai_source": ai_source,
            "changes": [_document_change_summary(change) for change in changes],
            "rag_documents": _rag_document_summaries(automation),
            "confirmation": _pending_confirmation(automation, {"rag_documents", "document_diff"}),
        },
    )


def _code_scope_stage(
    workspace: dict[str, object] | None,
    automation: dict[str, object] | None,
) -> dict[str, object]:
    impact = _dict((workspace or {}).get("impact_report"))
    impact_artifact = _dict((workspace or {}).get("impact_artifact"))
    confirmation = _dict((workspace or {}).get("confirmation"))
    impact_id = impact.get("id")
    impact_status = str(impact.get("status") or "pending")
    confirmation_id = confirmation.get("id")
    automation_stage = str((automation or {}).get("current_stage") or "")
    automation_status = str((automation or {}).get("status") or "")
    blockers = _automation_blockers(automation, {"impact_analysis", "impact_confirmation"})
    if impact_status in _FAILED_STATES or blockers:
        status = "blocked"
    elif impact_id is not None and impact_status == "confirmed" and confirmation_id is not None:
        status = "completed"
    elif automation_stage in {"impact_analysis", "impact_confirmation"}:
        status = "running" if automation_status == "running" else "waiting"
    else:
        status = "waiting"
    return _stage(
        "code_scope",
        "コード影響範囲",
        status,
        (
            "RAG と Code Graph から変更対象コードが確定しました。"
            if status == "completed"
            else "設計書差分から RAG と Code Graph で影響範囲を解析します。"
        ),
        "operamind",
        blocking_reasons=blockers
        or ([f"コード影響分析が {impact_status} で停止しました。"] if status == "blocked" else []),
        details={
            "base_revision": (workspace or {}).get("base_revision"),
            "impact_status": impact_status,
            "ui_impact_status": impact_artifact.get("ui_impact_status"),
            "items": [
                {
                    "target_path": item.get("target_path"),
                    "target_symbols": item.get("target_symbols", []),
                    "recommended_action": item.get("recommended_action"),
                    "test_file_refs": item.get("test_file_refs", []),
                    "rationale": item.get("rationale"),
                }
                for item in _dict_list(impact_artifact.get("items"))
            ],
            "impact_graph": _impact_graph(
                impact_artifact=impact_artifact,
                code_graph=_dict((workspace or {}).get("code_graph_artifact")),
            ),
            "confirmation": _pending_confirmation(automation, {"code_scope"}),
        },
    )


def _compile_test_stage(
    workspace: dict[str, object] | None,
    copilot_task: dict[str, object] | None,
    execution: dict[str, object] | None,
    automation: dict[str, object] | None,
) -> dict[str, object]:
    edit = _dict((workspace or {}).get("edit_result"))
    edit_status = str(edit.get("status") or "pending")
    edit_validation_mode = str(edit.get("validation_mode") or "pending")
    command_evidence_status = str(edit.get("command_evidence_status") or "pending")
    verification_only = not bool(
        _dict((workspace or {}).get("edit_packet")).get("editable_files", ["pending"])
    )
    task_state = str((copilot_task or {}).get("state") or "pending")
    commands = _dict_list((copilot_task or {}).get("commands"))
    ai_executor, ai_source = _ai_execution_source(copilot_task)
    if (
        edit.get("id") is not None
        and (edit_status == "in_scope" or (verification_only and edit_status == "no_changes"))
        and edit_validation_mode == "committed"
        and edit.get("tests_passed") is True
        and command_evidence_status == "verified"
        and task_state == "completed"
    ):
        status = "completed"
    elif (
        edit_status in _FAILED_STATES | {"out_of_scope"}
        or (edit_status == "no_changes" and not verification_only)
        or task_state in _FAILED_STATES
        or (
            edit_validation_mode == "committed"
            and edit.get("id") is not None
            and (
                edit_status
                not in ({"in_scope", "no_changes"} if verification_only else {"in_scope"})
                or edit.get("tests_passed") is not True
                or command_evidence_status != "verified"
            )
        )
    ):
        status = "blocked"
    elif task_state in _ACTIVE_STATES:
        status = "running"
    else:
        status = "waiting"
    blockers: list[str] = []
    if status == "blocked" and not blockers:
        blockers = [f"{ai_source} の変更タスクが {task_state} で停止しました。"]
    return _stage(
        "compile_test",
        "コード変更・コンパイル・テスト",
        status,
        (
            "コード変更、コンパイル、設定されたテストが完了しました。"
            if status == "completed"
            else (
                f"{ai_source} がコードを更新し、"
                "コンパイル、コードテスト、カバレッジ確認を実行します。"
            )
        ),
        ai_executor,
        blocking_reasons=blockers,
        details={
            "ai_source": ai_source,
            "copilot_task_state": task_state,
            "edit_status": edit_status,
            "edit_validation_mode": edit_validation_mode,
            "result_revision": edit.get("result_revision"),
            "command_evidence_status": command_evidence_status,
            "commands": [
                {
                    "command_ref": command.get("command_ref"),
                    "status": command.get("status"),
                    "exit_code": command.get("exit_code"),
                }
                for command in commands
            ],
        },
    )


def _ai_execution_source(
    copilot_task: dict[str, object] | None,
) -> tuple[str, str]:
    """Project the accepted AI executor without relabelling fallback Evidence."""

    task = copilot_task or {}
    accepted_by = str(task.get("accepted_by") or "").strip().lower()
    claimed_by = str(task.get("claimed_by") or "").strip().lower()
    accepted_event_actors = [
        str(event.get("actor") or "").strip().lower()
        for event in _dict_list(task.get("events"))
        if event.get("event_type") == "accepted"
    ]
    authoritative_actor = accepted_by or next(
        (actor for actor in reversed(accepted_event_actors) if actor),
        claimed_by,
    )
    if authoritative_actor.startswith("codex"):
        return "codex_fallback", "Codex fallback"
    return "vscode_github_copilot", "VS Code GitHub Copilot"


def _ui_stage(
    execution: dict[str, object] | None,
    automation: dict[str, object] | None,
    request: dict[str, object],
) -> dict[str, object]:
    execution = execution or {}
    data_run = _dict(execution.get("test_data_execution"))
    data_plan = _dict(execution.get("test_data_plan"))
    test_plan = _dict(execution.get("test_plan"))
    test_plan_status = str(test_plan.get("status") or "pending")
    data_plan_status = str(data_plan.get("status") or "pending")
    data_status = str(data_run.get("status") or "pending")
    closure = _dict(execution.get("change_closure"))
    closure_ui_status = str(closure.get("ui_status") or "")
    coverage = _dict(execution.get("business_coverage"))
    coverage_gate_failed = bool(coverage) and (
        coverage.get("coverage_percent") != 100
        or ("status" in coverage and coverage.get("status") != "passed")
    )
    passed = data_plan_status == "ready" and (closure_ui_status in {"passed", "not_impacted"})
    failed = coverage_gate_failed or data_plan_status == "blocked" or data_status in _FAILED_STATES
    running = data_status in _ACTIVE_STATES
    confirmation_waiting = str((automation or {}).get("current_stage") or "") in {
        "test_plan_confirmation",
        "ui_test_confirmation",
    }
    confirmation_blockers = _automation_blockers(
        automation, {"test_plan_confirmation", "ui_test_confirmation"}
    )
    status = (
        "blocked"
        if failed or confirmation_blockers
        else "waiting"
        if confirmation_waiting
        else "completed"
        if passed
        else "running"
        if running
        else "waiting"
    )
    blockers = []
    if status == "blocked":
        if coverage_gate_failed:
            blockers.append(
                "業務要件カバレッジが 100% ではないため、TestPlan を Copilot に返却します。"
            )
        blockers.extend(confirmation_blockers)
        blockers.extend(_string_list(data_plan.get("blocking_reasons")))
        blockers.extend(_string_list(closure.get("blocking_reasons")))
    if status == "blocked" and not blockers:
        blockers = ["テストデータ生成または UI 検証が合格していません。"]
    screenshots = _dict_list(execution.get("screenshots"))
    execution_result = _dict(data_run.get("result"))
    failure_management = _dict(execution.get("failure_management"))
    execution_actions = _dict(failure_management.get("actions"))
    return _stage(
        "ui_validation",
        "テストデータ・UI 検証",
        status,
        (
            "実ブラウザによる UI 検証が完了しました。"
            if status == "completed"
            else "生成した一連の業務データを使い、実ブラウザで UI を検証します。"
        ),
        "operamind",
        blocking_reasons=blockers,
        details={
            "ui_test_plan_status": test_plan_status,
            "ui_test_cases": _test_case_summaries(test_plan),
            "business_coverage_status": coverage.get("status"),
            "business_coverage_percent": coverage.get("coverage_percent"),
            "business_coverage_items": _business_coverage_summaries(
                coverage=coverage,
                request=request,
            ),
            "test_data_plan_status": data_plan_status,
            "test_data_status": data_status,
            "ui_status": closure_ui_status or "pending",
            "cleanup_status": execution_result.get("cleanup_status"),
            "execution_actions": {
                "can_rerun": execution_actions.get("can_rerun") is True,
                "rerun_run_id": execution_actions.get("rerun_run_id"),
            },
            "generation_flows": _generation_flow_summaries(
                data_plan=data_plan,
                execution_result=execution_result,
            ),
            "screenshots": [
                {
                    "content_url": item.get("content_url"),
                    "available": item.get("available"),
                }
                for item in screenshots
            ],
            "confirmation": (
                None
                if coverage_gate_failed
                else _pending_confirmation(automation, {"test_plan", "ui_test"})
            ),
        },
    )


def _business_coverage_summaries(
    *,
    coverage: dict[str, object],
    request: dict[str, object],
) -> list[dict[str, object]]:
    request_artifact = _dict(request.get("artifact"))
    rule_texts = {
        str(rule["business_rule_id"]): str(rule.get("text") or "業務ルール")
        for rule in _dict_list(request_artifact.get("business_rules"))
        if rule.get("business_rule_id") is not None
    }
    return [
        {
            "text": rule_texts.get(str(item.get("business_rule_id")), "業務ルール"),
            "status": item.get("status"),
            "test_case_count": len(_string_list(item.get("test_case_refs"))),
            "criterion_count": len(_string_list(item.get("criterion_refs"))),
        }
        for item in _dict_list(coverage.get("items"))
    ]


def _report_stage(
    execution: dict[str, object] | None,
    automation: dict[str, object] | None,
) -> dict[str, object]:
    execution = execution or {}
    closure = _dict(execution.get("change_closure"))
    status_value = str(closure.get("status") or "pending")
    blockers = _string_list(closure.get("blocking_reasons"))
    confirmation_waiting = (
        str((automation or {}).get("current_stage") or "") == "final_report_confirmation"
    )
    confirmation_blockers = _automation_blockers(automation, {"final_report_confirmation"})
    if confirmation_blockers:
        status = "blocked"
    elif confirmation_waiting:
        status = "waiting"
    elif status_value in _SUCCESS_STATES:
        status = "completed"
    elif status_value in _FAILED_STATES or blockers:
        status = "blocked"
    else:
        status = "waiting"
    coverage = _dict(execution.get("business_coverage"))
    line_coverage = _dict(execution.get("changed_line_coverage"))
    test_plan = _dict(execution.get("test_plan"))
    return _stage(
        "final_report",
        "最終レポート",
        status,
        (
            "要件、設計書、コード、テスト、UI 証跡を結合した最終レポートです。"
            if status == "completed"
            else "すべての検証完了後に最終レポートを生成します。"
        ),
        "operamind",
        blocking_reasons=confirmation_blockers or blockers,
        details={
            "closure_status": status_value,
            "ui_status": closure.get("ui_status"),
            "business_coverage_percent": coverage.get("coverage_percent"),
            "changed_line_coverage_percent": line_coverage.get("coverage_percent"),
            "modified_paths": _string_list(closure.get("modified_paths")),
            "test_results": _test_result_summaries(
                closure=closure,
                test_plan=test_plan,
            ),
            "unresolved_items": _string_list(closure.get("unresolved_items")),
            "confirmation": _pending_confirmation(automation, {"final_report"}),
        },
    )


def _stage(
    stage_id: str,
    label: str,
    status: str,
    summary: str,
    executor: str,
    *,
    blocking_reasons: list[str],
    details: dict[str, object],
) -> dict[str, object]:
    if stage_id not in FLOW_STAGE_IDS:
        raise ValueError(f"Unknown main change flow stage: {stage_id}")
    return {
        "stage_id": stage_id,
        "label": label,
        "status": status,
        "summary": summary,
        "executor": executor,
        "blocking_reasons": blocking_reasons,
        "details": details,
    }


def _document_change_summary(change: dict[str, object]) -> dict[str, object]:
    before = _dict(change.get("before")).get("values")
    after = _dict(change.get("after")).get("values")
    return {
        "domain": change.get("domain"),
        "fact_type": change.get("fact_type"),
        "change_type": change.get("change_type"),
        "summary": change.get("summary"),
        "field_deltas": _field_deltas(before, after),
        "source_refs": change.get("source_refs", []),
    }


def _field_deltas(before: object, after: object) -> list[dict[str, object]]:
    before_values = _dict(before)
    after_values = _dict(after)
    fields = sorted(set(before_values) | set(after_values))
    return [
        {
            "field": field,
            "before": before_values.get(field),
            "after": after_values.get(field),
        }
        for field in fields
        if before_values.get(field) != after_values.get(field)
    ]


def _test_case_summaries(test_plan: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "title": case.get("title"),
            "level": case.get("level"),
            "execution_mode": case.get("execution_mode"),
            "preconditions": _string_list(case.get("preconditions")),
            "steps": _string_list(case.get("steps")),
            "expected_results": _string_list(case.get("expected_results")),
        }
        for case in _dict_list(test_plan.get("test_cases"))
    ]


def _generation_flow_summaries(
    *,
    data_plan: dict[str, object],
    execution_result: dict[str, object],
) -> list[dict[str, object]]:
    result_by_flow = {
        str(result.get("flow_id")): result
        for result in _dict_list(execution_result.get("flow_results"))
        if result.get("flow_id") is not None
    }
    values: list[dict[str, object]] = []
    for flow in _dict_list(data_plan.get("generation_flows")):
        flow_result = result_by_flow.get(str(flow.get("flow_id")), {})
        step_results = {
            str(result.get("step_id")): result
            for result in (
                _dict_list(flow_result.get("step_results"))
                + _dict_list(flow_result.get("cleanup_results"))
            )
            if result.get("step_id") is not None
        }
        values.append(
            {
                "title": flow.get("title"),
                "status": flow_result.get("status"),
                "steps": [
                    _generation_step_summary(step, step_results)
                    for step in _dict_list(flow.get("steps"))
                ],
                "final_assertions": [
                    _assertion_summary(assertion)
                    for assertion in _dict_list(flow.get("final_assertions"))
                ],
                "cleanup_policy": flow.get("cleanup_policy"),
                "cleanup_steps": [
                    _generation_step_summary(step, step_results)
                    for step in _dict_list(flow.get("cleanup_steps"))
                ],
            }
        )
    return values


def _generation_step_summary(
    step: dict[str, object],
    results: dict[str, dict[str, Any]],
) -> dict[str, object]:
    result = results.get(str(step.get("step_id")), {})
    fallback = _dict(step.get("computer_use_fallback"))
    return {
        "sequence": step.get("sequence"),
        "channel": step.get("channel"),
        "business_action": step.get("business_action"),
        "mapped_test_step_count": len(_string_list(step.get("test_step_refs"))),
        "computer_use_fallback": (
            {
                "reason": fallback.get("reason"),
                "objective": fallback.get("objective"),
                "max_actions": fallback.get("max_actions"),
            }
            if fallback
            else None
        ),
        "status": result.get("status"),
        "input_variables": sorted(_dict(step.get("inputs"))),
        "output_variables": [
            binding.get("variable")
            for binding in _dict_list(step.get("output_bindings"))
            if binding.get("variable")
        ],
        "assertions": [
            _assertion_summary(assertion) for assertion in _dict_list(step.get("postconditions"))
        ],
        "failure_reason": result.get("failure_reason"),
    }


def _assertion_summary(assertion: dict[str, object]) -> dict[str, object]:
    return {
        "observe_via": assertion.get("observe_via"),
        "subject": assertion.get("subject"),
        "operator": assertion.get("operator"),
        "expected": assertion.get("expected"),
    }


def _test_result_summaries(
    *,
    closure: dict[str, object],
    test_plan: dict[str, object],
) -> list[dict[str, object]]:
    title_by_id = {
        str(case.get("test_case_id")): str(case.get("title") or "テストケース")
        for case in _dict_list(test_plan.get("test_cases"))
        if case.get("test_case_id") is not None
    }
    return [
        {
            "title": title_by_id.get(str(result.get("test_case_id")), "テストケース"),
            "status": result.get("status"),
            "summary": result.get("summary"),
        }
        for result in _dict_list(closure.get("test_results"))
    ]


def _automation_blockers(automation: dict[str, object] | None, stages: set[str]) -> list[str]:
    if not automation or automation.get("current_stage") not in stages:
        return []
    if automation.get("status") != "blocked":
        return []
    reason = automation.get("blocking_reason") or automation.get("message")
    return [str(reason)] if reason else ["工程が停止しました。"]


def _impact_graph(
    *,
    impact_artifact: dict[str, Any],
    code_graph: dict[str, Any],
) -> dict[str, object] | None:
    """Project one bounded, file-level view from Canonical Code Graph evidence."""

    impact_items = _dict_list(impact_artifact.get("items"))
    graph_files = _dict_list(code_graph.get("files"))
    if not impact_items or not graph_files:
        return None

    files_by_path = {
        str(item["path"]): item
        for item in graph_files
        if isinstance(item.get("path"), str) and item["path"]
    }
    ref_to_path: dict[str, str] = {}
    for path, file in files_by_path.items():
        file_id = file.get("file_id")
        if isinstance(file_id, str):
            ref_to_path[file_id] = path
        ref_to_path[path] = path
        for symbol in _dict_list(file.get("symbols")):
            symbol_id = symbol.get("symbol_id")
            if isinstance(symbol_id, str):
                ref_to_path[symbol_id] = path

    item_by_path = {
        str(item["target_path"]): item
        for item in impact_items
        if isinstance(item.get("target_path"), str) and item["target_path"]
    }
    direct_paths = set(item_by_path)
    test_paths = {
        value for item in impact_items for value in _string_list(item.get("test_file_refs"))
    }
    seed_paths = direct_paths | test_paths
    edge_candidates: list[dict[str, object]] = []
    connected_paths: set[str] = set(seed_paths)
    for edge in _dict_list(code_graph.get("edges")):
        if edge.get("resolution_status") != "resolved":
            continue
        from_path = ref_to_path.get(str(edge.get("from_ref") or ""))
        to_path = ref_to_path.get(str(edge.get("to_ref") or ""))
        if from_path is None or to_path is None or from_path == to_path:
            continue
        if from_path not in seed_paths and to_path not in seed_paths:
            continue
        connected_paths.update((from_path, to_path))
        location = _dict(edge.get("source_location"))
        edge_candidates.append(
            {
                "from_path": from_path,
                "to_path": to_path,
                "relation": edge.get("edge_type"),
                "evidence_source": "code_graph",
                "source_path": location.get("path"),
                "start_line": location.get("start_line"),
            }
        )

    ordered_paths = sorted(
        connected_paths,
        key=lambda path: (
            0 if path in direct_paths else 1 if path in test_paths else 2,
            path,
        ),
    )
    visible_paths = set(ordered_paths[:40])
    edges_by_pair: dict[tuple[str, str], dict[str, object]] = {}
    for candidate in edge_candidates:
        from_path = str(candidate["from_path"])
        to_path = str(candidate["to_path"])
        if from_path not in visible_paths or to_path not in visible_paths:
            continue
        key = (from_path, to_path)
        relation = str(candidate.get("relation") or "depends_on")
        current = edges_by_pair.get(key)
        if current is None:
            edges_by_pair[key] = {**candidate, "relations": [relation]}
        else:
            relations = current["relations"]
            if isinstance(relations, list) and relation not in relations:
                relations.append(relation)
    edges = list(edges_by_pair.values())
    existing_pairs = {frozenset((str(edge["from_path"]), str(edge["to_path"]))) for edge in edges}
    for target_path, item in item_by_path.items():
        if target_path not in visible_paths:
            continue
        for test_path in _string_list(item.get("test_file_refs")):
            if test_path not in visible_paths:
                continue
            pair = frozenset((target_path, test_path))
            if pair in existing_pairs:
                continue
            edges.append(
                {
                    "from_path": target_path,
                    "to_path": test_path,
                    "relation": "related_test",
                    "relations": ["related_test"],
                    "evidence_source": "impact_report",
                    "source_path": None,
                    "start_line": None,
                }
            )
            existing_pairs.add(pair)

    nodes: list[dict[str, object]] = []
    for path in ordered_paths[:40]:
        file = files_by_path.get(path, {})
        impact_item = item_by_path.get(path, {})
        graph_symbols = [
            str(symbol.get("name") or symbol.get("signature"))
            for symbol in _dict_list(file.get("symbols"))
            if symbol.get("name") or symbol.get("signature")
        ]
        target_symbols = _string_list(impact_item.get("target_symbols"))
        related_tests = _string_list(impact_item.get("test_file_refs"))
        nodes.append(
            {
                "path": path,
                "role": file.get("role") or ("test" if path in test_paths else "unknown"),
                "language": file.get("language"),
                "directly_impacted": path in direct_paths,
                "recommended_action": impact_item.get("recommended_action"),
                "rationale": impact_item.get("rationale")
                or _dependency_reason(path=path, edges=edge_candidates, direct_paths=direct_paths),
                "symbols": (target_symbols or graph_symbols)[:12],
                "symbol_count": len(target_symbols or graph_symbols),
                "related_tests": related_tests,
            }
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "total_file_count": len(ordered_paths),
        "visible_file_count": len(nodes),
        "relation_count": len(edges),
        "truncated": len(ordered_paths) > len(nodes),
    }


def _dependency_reason(*, path: str, edges: list[dict[str, object]], direct_paths: set[str]) -> str:
    relation = next(
        (
            str(edge.get("relation") or "depends_on")
            for edge in edges
            if path in {edge.get("from_path"), edge.get("to_path")}
            and ({edge.get("from_path"), edge.get("to_path")} & direct_paths)
        ),
        "depends_on",
    )
    return f"Code Graph の {relation} 関係で変更対象に接続しています。"


def _pending_confirmation(
    automation: dict[str, object] | None, checkpoints: set[str]
) -> dict[str, object] | None:
    value = _dict((automation or {}).get("pending_confirmation"))
    return value if value.get("checkpoint") in checkpoints else None


def _rag_document_summaries(
    automation: dict[str, object] | None,
) -> list[dict[str, object]]:
    confirmation = _pending_confirmation(automation, {"rag_documents"})
    details = _dict((confirmation or {}).get("details"))
    return [
        {
            "logical_name": candidate.get("logical_name"),
            "document_ref": candidate.get("document_ref"),
            "heading_path": candidate.get("heading_path"),
            "summary": candidate.get("summary"),
            "relevance_reason": candidate.get("relevance_reason"),
        }
        for candidate in _dict_list(details.get("candidates"))
    ]


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
