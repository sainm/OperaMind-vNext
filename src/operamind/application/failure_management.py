"""Unified, fail-closed failure read model for the Japanese Web control plane."""

from __future__ import annotations

import hashlib
from typing import Any, cast


def build_failure_management(
    *,
    test_data_plan: dict[str, Any] | None,
    test_data_execution: dict[str, Any] | None,
    ui_result: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
    closure: dict[str, Any] | None,
    controls: dict[str, Any],
) -> dict[str, object]:
    """Aggregate canonical failure sources without turning missing evidence into success."""

    failures: list[dict[str, object]] = []
    run_id = (
        str(test_data_execution["run_id"])
        if test_data_execution is not None and test_data_execution.get("run_id")
        else None
    )
    if test_data_plan is not None:
        for reason in cast(list[str], test_data_plan.get("blocking_reasons", [])):
            _append_failure(
                failures,
                category="test_data",
                status="blocked",
                stage="plan",
                summary_ja="テストデータ計画を実行できません",
                reason=reason,
                run_id=run_id,
            )
    elif controls.get("blocking_reason"):
        _append_failure(
            failures,
            category="test_data",
            status="blocked",
            stage="plan",
            summary_ja="テストデータ計画を利用できません",
            reason=str(controls["blocking_reason"]),
            run_id=run_id,
        )

    result = (
        cast(dict[str, Any], test_data_execution.get("result"))
        if test_data_execution is not None and isinstance(test_data_execution.get("result"), dict)
        else None
    )
    if controls.get("can_recover") is True and result is None:
        _append_failure(
            failures,
            category="test_data",
            status="blocked",
            stage="execution",
            summary_ja="実行中の Run が更新されていません",
            reason="The running TestData execution is stale and requires explicit recovery",
            run_id=run_id,
        )
    elif (
        test_data_execution is not None
        and test_data_execution.get("status") in {"failed", "blocked", "interrupted"}
        and result is None
    ):
        _append_failure(
            failures,
            category="test_data",
            status=str(test_data_execution["status"]),
            stage="execution",
            summary_ja="テストデータ実行結果を確認できません",
            reason="Terminal TestData Run has no canonical execution result",
            run_id=run_id,
        )
    step_failure_keys: set[tuple[str, str]] = set()
    if result is not None:
        for flow in cast(list[dict[str, Any]], result.get("flow_results", [])):
            for phase, category, summary in (
                ("step_results", "test_data", "テストデータ生成に失敗しました"),
                ("cleanup_results", "cleanup", "テストデータのクリーンアップに失敗しました"),
            ):
                for step in cast(list[dict[str, Any]], flow.get(phase, [])):
                    if step.get("status") not in {"failed", "blocked"}:
                        continue
                    reason = str(step.get("failure_reason") or "失敗理由が記録されていません")
                    step_failure_keys.add((category, reason))
                    _append_failure(
                        failures,
                        category=category,
                        status=str(step["status"]),
                        stage=str(step.get("step_id") or flow.get("flow_id") or phase),
                        summary_ja=summary,
                        reason=reason,
                        run_id=run_id,
                    )
        for reason_value in cast(list[object], result.get("failure_reasons", [])):
            reason = str(reason_value)
            category = "cleanup" if "cleanup" in reason.lower() else "test_data"
            if (category, reason) in step_failure_keys:
                continue
            _append_failure(
                failures,
                category=category,
                status=str(result.get("status", "failed")),
                stage="execution",
                summary_ja=(
                    "テストデータのクリーンアップに失敗しました"
                    if category == "cleanup"
                    else "テストデータ実行に失敗しました"
                ),
                reason=reason,
                run_id=run_id,
            )
        cleanup_status = str(result.get("cleanup_status", "not_required"))
        if cleanup_status in {"failed", "interrupted"} and not any(
            value["category"] == "cleanup" for value in failures
        ):
            _append_failure(
                failures,
                category="cleanup",
                status=cleanup_status,
                stage="cleanup",
                summary_ja="クリーンアップが正常に完了していません",
                reason=f"Cleanup status: {cleanup_status}",
                run_id=run_id,
            )

    if ui_result is not None and ui_result.get("status") != "passed":
        ui_reasons = [
            str(value) for value in cast(list[object], ui_result.get("failure_reasons", []))
        ]
        for scenario in cast(list[dict[str, Any]], ui_result.get("scenario_results", [])):
            if scenario.get("status") in {"failed", "blocked", "skipped"}:
                reason = str(
                    scenario.get("summary")
                    or f"Failure category: {scenario.get('failure_category', 'blocked')}"
                )
                _append_failure(
                    failures,
                    category="ui",
                    status=str(scenario["status"]),
                    stage=str(scenario.get("scenario_id", "ui-scenario")),
                    summary_ja="UI シナリオの検証に失敗しました",
                    reason=reason,
                    run_id=run_id,
                )
        for reason in ui_reasons:
            _append_failure(
                failures,
                category="ui",
                status=str(ui_result["status"]),
                stage="ui-verification",
                summary_ja="UI 検証を完了できません",
                reason=reason,
                run_id=run_id,
            )

    if coverage is not None and (
        coverage.get("status") != "passed" or float(coverage.get("coverage_percent", 0)) < 100
    ):
        uncovered = [
            value
            for value in cast(list[dict[str, Any]], coverage.get("items", []))
            if value.get("status") != "covered"
        ]
        if uncovered:
            for item in uncovered:
                rule_id = str(item.get("business_rule_id", "unknown-rule"))
                _append_failure(
                    failures,
                    category="coverage",
                    status="failed",
                    stage=rule_id,
                    summary_ja="業務ルールがテストでカバーされていません",
                    reason=f"Uncovered business rule: {rule_id}",
                    run_id=run_id,
                )
        else:
            _append_failure(
                failures,
                category="coverage",
                status="failed",
                stage="business-coverage",
                summary_ja="業務カバレッジが 100% に達していません",
                reason=f"Coverage: {coverage.get('coverage_percent', 0)}%",
                run_id=run_id,
            )

    if closure is not None and closure.get("status") != "passed":
        unresolved = [
            str(value) for value in cast(list[object], closure.get("unresolved_items", []))
        ]
        for reason in unresolved or ["Closure is not passed, but no unresolved item was recorded"]:
            _append_failure(
                failures,
                category="closure",
                status=str(closure["status"]),
                stage="change-closure",
                summary_ja="変更をクローズできません",
                reason=reason,
                run_id=run_id,
            )

    status = (
        "clear"
        if not failures
        else "recovery_required"
        if controls.get("can_recover")
        else "attention_required"
    )
    return {
        "status": status,
        "failure_count": len(failures),
        "failures": failures,
        "actions": {
            "can_recover": controls.get("can_recover") is True,
            "recover_run_id": run_id if controls.get("can_recover") is True else None,
            "recovery_requires_reason": True,
            "can_rerun": controls.get("can_rerun") is True,
            "rerun_run_id": run_id if controls.get("can_rerun") is True else None,
        },
    }


def _append_failure(
    failures: list[dict[str, object]],
    *,
    category: str,
    status: str,
    stage: str,
    summary_ja: str,
    reason: str,
    run_id: str | None,
) -> None:
    identity = hashlib.sha256(
        "\0".join((category, status, stage, reason, run_id or "")).encode()
    ).hexdigest()[:20]
    if any(value["failure_id"] == identity for value in failures):
        return
    failures.append(
        {
            "failure_id": identity,
            "category": category,
            "status": status,
            "stage": stage,
            "summary_ja": summary_ja,
            "reason": reason,
            "run_id": run_id,
        }
    )
