"""Build a request-scoped traceability graph and fail-closed omission ledger."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def build_change_traceability(
    *,
    request: dict[str, Any],
    document_diff: dict[str, Any] | None,
    case: dict[str, Any] | None,
    bundle: dict[str, Any] | None,
    management: dict[str, Any] | None,
    modification: dict[str, Any] | None = None,
    copilot_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic graph whose gaps describe missing downstream proof."""

    nodes: dict[str, dict[str, Any]] = {}
    edges: set[tuple[str, str, str]] = set()
    gaps: list[dict[str, Any]] = []
    artifact = _dict(request.get("artifact"))
    request_id = str(artifact.get("change_request_id") or request.get("change_request_id") or "")

    review = _dict(request.get("document_review"))
    _add(
        nodes,
        "request",
        "変更要件",
        request_id or "変更要件",
        str(review.get("status") or "pending"),
    )
    changes = _list(document_diff and document_diff.get("changes"))
    for change in changes:
        change_id = str(change.get("change_id") or change.get("structured_change_id") or "")
        if not change_id:
            continue
        node_id = f"design:{change_id}"
        _add(
            nodes,
            node_id,
            "設計変更",
            str(change.get("summary") or change.get("stable_key") or change_id),
            str(change.get("review_status") or "pending"),
            refs=[change_id],
        )
        _edge(edges, "request", node_id, "contains")
    if not changes:
        _gap(gaps, "design_change", "設計変更がありません。文書差分を取り込んでください。")

    impact = _dict(case and case.get("impact_report"))
    impact_items = _list(impact.get("items"))
    for item in impact_items:
        item_id = str(item.get("impact_item_id") or "")
        if not item_id:
            continue
        node_id = f"impact:{item_id}"
        _add(
            nodes,
            node_id,
            "影響項目",
            str(item.get("target_path") or item_id),
            str(item.get("impact_level") or "unknown"),
            refs=[item_id],
        )
        for change_id in _strings(item.get("structured_change_refs")):
            _edge(edges, "design:" + change_id, node_id, "影響")
        if not _has_incoming(edges, node_id, "影響", nodes):
            _gap(
                gaps, "impact_link", f"影響項目 {item_id} が設計変更に関連付いていません。", node_id
            )
    if not impact:
        _gap(gaps, "impact_report", "Impact Report がありません。", "request:" + request_id)
    elif not impact_items:
        _gap(
            gaps,
            "impact_item",
            "Impact Report に影響項目がありません。",
            f"impact:{impact.get('impact_report_id', 'unknown')}",
        )

    if bundle:
        orchestration = _dict(bundle.get("orchestration"))
        for scope in _list(orchestration.get("code_scope")):
            item_id = str(scope.get("impact_item_id") or "")
            path = str(scope.get("target_path") or "")
            node_id = f"code:{item_id}:{path}"
            _add(
                nodes,
                node_id,
                "影響コード",
                path or item_id,
                str(scope.get("recommended_action") or "pending"),
                refs=[path] if path else [],
            )
            if item_id:
                _edge(edges, f"impact:{item_id}", node_id, "コード範囲")
                if f"impact:{item_id}" not in nodes:
                    _gap(
                        gaps,
                        "code_scope",
                        f"コード範囲 {path or item_id} の Impact 参照先がありません。",
                        node_id,
                    )
            if not item_id or not path:
                _gap(
                    gaps,
                    "code_scope",
                    "影響コードに影響項目またはファイルパスがありません。",
                    node_id,
                )
        if not _list(orchestration.get("code_scope")):
            _gap(
                gaps,
                "code_scope",
                "コード影響範囲がまだ生成されていません。",
                "request:" + request_id,
            )

        acceptance = _dict(bundle.get("acceptance_criteria"))
        for criterion in _list(acceptance.get("criteria")):
            criterion_id = str(criterion.get("criterion_id") or "")
            if not criterion_id:
                continue
            node_id = f"criterion:{criterion_id}"
            _add(
                nodes,
                node_id,
                "検証基準",
                str(criterion.get("subject") or criterion_id),
                "defined",
                refs=[criterion_id],
            )
            for test_id in _strings(criterion.get("test_case_refs")):
                _edge(edges, node_id, f"case:{test_id}", "検証")
            if not _strings(criterion.get("test_case_refs")):
                _gap(
                    gaps,
                    "verification_standard",
                    f"検証基準 {criterion_id} に Test Case の関連がありません。",
                    node_id,
                )
        if not acceptance:
            _gap(
                gaps,
                "verification_standard",
                "Acceptance Criteria と検証基準がありません。",
                "request:" + request_id,
            )
        elif not _list(acceptance.get("criteria")):
            _gap(
                gaps,
                "verification_standard",
                "Acceptance Criteria の検証基準が空です。",
                "request:" + request_id,
            )

        test_plan = _dict(bundle.get("test_plan"))
        test_cases = _list(test_plan.get("test_cases"))
        for test_case in test_cases:
            test_id = str(test_case.get("test_case_id") or "")
            if not test_id:
                continue
            node_id = f"case:{test_id}"
            _add(
                nodes,
                node_id,
                "Test Case",
                str(test_case.get("title") or test_id),
                str(test_case.get("level") or "pending"),
                refs=[test_id],
            )
            for rule_id in _strings(test_case.get("business_rule_refs")):
                _edge(edges, f"rule:{rule_id}", node_id, "対象")
            for criterion_id in _strings(test_case.get("acceptance_criteria_refs")):
                _edge(edges, f"criterion:{criterion_id}", node_id, "検証")
            if not _strings(test_case.get("business_rule_refs")):
                _gap(
                    gaps,
                    "test_case_link",
                    f"Test Case {test_id} に業務ルールの関連がありません。",
                    node_id,
                )
            if not _strings(test_case.get("acceptance_criteria_refs")):
                _gap(
                    gaps,
                    "test_case_link",
                    f"Test Case {test_id} に検証基準の関連がありません。",
                    node_id,
                )
        if not test_cases:
            _gap(gaps, "test_case", "Test Case が生成されていません。", "request:" + request_id)

        data_plan = _dict(bundle.get("test_data_plan"))
        data_execution = _dict(management and management.get("test_data_execution"))
        data_result = _dict(data_execution.get("result")) or data_execution
        for flow in _list(data_plan.get("generation_flows")):
            flow_id = str(flow.get("flow_id") or "")
            node_id = f"data:{flow_id}"
            flow_result = next(
                (
                    item
                    for item in _list(data_result.get("flow_results"))
                    if str(item.get("flow_id")) == flow_id
                ),
                None,
            )
            _add(
                nodes,
                node_id,
                "テストデータ",
                str(flow.get("title") or flow_id),
                str((flow_result or {}).get("status") or "未実行"),
                refs=[flow_id],
            )
            for test_id in _strings(flow.get("test_case_refs")):
                _edge(edges, f"case:{test_id}", node_id, "データ")
            if flow_result is None:
                _gap(
                    gaps,
                    "test_data_result",
                    f"テストデータフロー {flow_id} の実行結果がありません。",
                    node_id,
                )
            elif flow_result.get("status") != "passed":
                _gap(
                    gaps,
                    "test_data_result",
                    f"テストデータフロー {flow_id} が成功していません。",
                    node_id,
                )
        if not data_plan:
            _gap(gaps, "test_data_plan", "TestDataPlan がありません。", "request:" + request_id)
        elif not _list(data_plan.get("generation_flows")):
            _gap(
                gaps,
                "test_data_plan",
                "TestDataPlan に生成フローがありません。",
                "request:" + request_id,
            )

        coverage = _dict(bundle.get("coverage_report"))
        for item in _list(coverage.get("items")):
            rule_id = str(item.get("business_rule_id") or "")
            node_id = f"coverage:{rule_id}"
            _add(
                nodes,
                node_id,
                "業務カバレッジ",
                rule_id,
                str(item.get("status") or "uncovered"),
                refs=[rule_id],
            )
            _edge(edges, f"rule:{rule_id}", node_id, "カバレッジ")
            for test_id in _strings(item.get("test_case_refs")):
                _edge(edges, node_id, f"case:{test_id}", "根拠")
            if item.get("status") != "covered":
                _gap(gaps, "business_coverage", f"業務ルール {rule_id} が未カバーです。", node_id)
        if not coverage:
            _gap(
                gaps,
                "business_coverage",
                "Business Coverage Report がありません。",
                "request:" + request_id,
            )
        elif not _list(coverage.get("items")):
            _gap(
                gaps,
                "business_coverage",
                "Business Coverage Report に業務ルール項目がありません。",
                "request:" + request_id,
            )

        for scenario in _list(orchestration.get("ui_scenarios")):
            scenario_id = str(scenario.get("scenario_id") or "")
            node_id = f"ui:{scenario_id}"
            _add(
                nodes,
                node_id,
                "UI Scenario",
                str(scenario.get("title") or scenario_id),
                "planned",
                refs=[scenario_id],
            )
            scenario_test_refs = _strings(scenario.get("test_case_refs"))
            if not scenario_test_refs and f"case:{scenario_id}" in nodes:
                scenario_test_refs = [scenario_id]
            for test_id in scenario_test_refs:
                _edge(edges, f"case:{test_id}", node_id, "UI")
            if not scenario_test_refs:
                _gap(
                    gaps,
                    "ui_link",
                    f"UI Scenario {scenario_id} に Test Case の関連がありません。",
                    node_id,
                )
            ui_result = _dict(management and management.get("change_closure"))
            result = next(
                (
                    item
                    for item in _list(ui_result.get("test_results"))
                    if str(item.get("test_case_id")) == scenario_id
                ),
                None,
            )
            if result is None:
                _gap(
                    gaps,
                    "ui_result",
                    f"UI Scenario {scenario_id} に検証結果がありません。",
                    node_id,
                )
            else:
                _add(
                    nodes,
                    f"ui-result:{scenario_id}",
                    "UI 検証結果",
                    scenario_id,
                    str(result.get("status") or "blocked"),
                    refs=_strings(result.get("evidence_refs")),
                )
                _edge(edges, node_id, f"ui-result:{scenario_id}", "検証")
                if result.get("status") != "passed":
                    _gap(
                        gaps,
                        "ui_result",
                        f"UI Scenario {scenario_id} の検証が成功していません。",
                        f"ui-result:{scenario_id}",
                    )

    progress = _dict(case and case.get("progress"))
    edit_result = _dict(progress.get("edit_result"))
    if not edit_result.get("id"):
        _gap(gaps, "edit_result", "Committed Edit Result がありません。", "request:" + request_id)
    else:
        edit_node = "edit:" + str(edit_result["id"])
        _add(
            nodes,
            edit_node,
            "コード変更結果",
            str(edit_result["id"]),
            str(edit_result.get("status") or "pending"),
            refs=[str(edit_result["id"])],
        )
        for code_node in [
            node_id for node_id, node in nodes.items() if node["kind"] == "影響コード"
        ]:
            _edge(edges, code_node, edit_node, "実変更")
        if edit_result.get("status") != "in_scope":
            _gap(
                gaps,
                "edit_result",
                "Committed Edit Result が承認範囲内ではありません。",
                edit_node,
            )

    closure = _dict(management and management.get("change_closure"))
    if not closure:
        _gap(gaps, "closure", "ChangeClosureResult がありません。", "request:" + request_id)
    else:
        closure_id = str(closure.get("closure_result_id") or "")
        closure_node = "closure:" + closure_id
        if not closure_id:
            _gap(
                gaps,
                "closure",
                "ChangeClosureResult の識別子がありません。",
                closure_node,
            )
        _add(
            nodes,
            closure_node,
            "Closure Result",
            closure_id or "ChangeClosureResult",
            str(closure.get("status") or "blocked"),
            refs=[closure_id] if closure_id else [],
        )
        for kind in ("コード変更結果", "テストデータ", "UI 検証結果", "業務カバレッジ"):
            for node_id, node in nodes.items():
                if node["kind"] == kind:
                    _edge(edges, node_id, closure_node, "クローズ根拠")
        if closure.get("status") != "passed":
            for reason in _strings(closure.get("unresolved_items")):
                _gap(gaps, "closure_blocker", reason, closure_node)

    _add_business_rules(nodes, edges, artifact)
    # A business rule edge can be added after Test Case/Criteria nodes exist.
    for node_id, node in list(nodes.items()):
        if node["kind"] == "Test Case":
            test_case_source = _find_source_by_ref(test_cases if bundle else (), node["refs"])
            for rule_id in _strings(test_case_source.get("business_rule_refs")):
                _edge(edges, f"rule:{rule_id}", node_id, "対象")

    if modification and _dict(modification.get("latest")):
        latest = _dict(modification["latest"])
        _add(
            nodes,
            "case-revision:" + str(latest.get("proposal", {}).get("proposal_id", "latest")),
            "Case 修正",
            str(latest.get("state") or "pending"),
            str(latest.get("state") or "pending"),
        )
    if copilot_task and copilot_task.get("task"):
        task = _dict(copilot_task["task"])
        _add(
            nodes,
            "copilot:" + str(task.get("coding_task_id") or "latest"),
            "Copilot 変更タスク",
            str(task.get("coding_task_id") or "latest"),
            str(task.get("state") or copilot_task.get("state") or "pending"),
        )

    for source, target, relation in sorted(edges):
        if source not in nodes or target not in nodes:
            _gap(
                gaps,
                "broken_reference",
                f"関係 {relation} の参照先がありません。",
                source if source not in nodes else target,
            )

    edge_values = [
        {"from": source, "to": target, "relation": relation}
        for source, target, relation in sorted(edges)
        if source in nodes and target in nodes
    ]
    stage_order = (
        "変更要件",
        "設計変更",
        "影響項目",
        "影響コード",
        "業務ルール",
        "検証基準",
        "Test Case",
        "Case 修正",
        "テストデータ",
        "UI Scenario",
        "UI 検証結果",
        "業務カバレッジ",
        "Copilot 変更タスク",
        "コード変更結果",
        "Closure Result",
    )
    return {
        "change_request_id": request_id,
        "project_id": str(request.get("project_id") or artifact.get("project_id") or ""),
        "analysis_case_id": str(request.get("analysis_case_id") or ""),
        "nodes": list(nodes.values()),
        "edges": edge_values,
        "gaps": sorted(gaps, key=lambda item: (item["severity"], item["code"], item["message"])),
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edge_values),
            "gap_count": len(gaps),
            "critical_gap_count": sum(item["severity"] == "critical" for item in gaps),
            "stage_order": list(stage_order),
        },
    }


def _add_business_rules(
    nodes: dict[str, dict[str, Any]],
    edges: set[tuple[str, str, str]],
    artifact: dict[str, Any],
) -> None:
    for rule in _list(artifact.get("business_rules")):
        rule_id = str(rule.get("business_rule_id") or "")
        if rule_id:
            _add(
                nodes,
                f"rule:{rule_id}",
                "業務ルール",
                str(rule.get("text") or rule_id),
                "defined",
                refs=[rule_id],
            )
            _edge(edges, "request", f"rule:{rule_id}", "業務ルール")


def _find_source_by_ref(values: Iterable[object], refs: list[str]) -> dict[str, Any]:
    wanted = set(refs)
    for value in values:
        item = _dict(value)
        if str(item.get("test_case_id")) in wanted:
            return item
    return {}


def _add(
    nodes: dict[str, dict[str, Any]],
    node_id: str,
    kind: str,
    title: str,
    status: str,
    refs: list[str] | None = None,
) -> None:
    if not node_id:
        return
    nodes.setdefault(
        node_id,
        {
            "id": node_id,
            "kind": kind,
            "title": title,
            "status": status,
            "refs": sorted(set(refs or [])),
        },
    )


def _edge(edges: set[tuple[str, str, str]], source: str, target: str, relation: str) -> None:
    if source and target:
        edges.add((source, target, relation))


def _has_incoming(
    edges: set[tuple[str, str, str]],
    target: str,
    relation: str,
    nodes: dict[str, dict[str, Any]],
) -> bool:
    return any(
        source in nodes and edge_target == target and edge_relation == relation
        for source, edge_target, edge_relation in edges
    )


def _gap(gaps: list[dict[str, Any]], code: str, message: str, node_id: str | None = None) -> None:
    gaps.append(
        {
            "code": code,
            "severity": "critical"
            if code
            in {
                "impact_report",
                "impact_link",
                "code_scope",
                "test_case",
                "test_case_link",
                "test_data_plan",
                "test_data_result",
                "verification_standard",
                "business_coverage",
                "ui_link",
                "ui_result",
                "broken_reference",
                "design_change",
                "edit_result",
                "closure",
            }
            else "warning",
            "message": message,
            "node_id": node_id,
        }
    )


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []
