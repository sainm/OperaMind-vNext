"""Deterministic state model for the resumable one-click change workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChangeAutomationDecision:
    stage: str
    status: str
    next_action: str | None
    blocking_reason: str | None
    message: str


STAGE_LABELS = {
    "requirement_confirmation": "変更要件の確認",
    "document_generation": "設計書ドラフト生成",
    "document_revision": "設計書の再修正",
    "document_confirmation": "設計書差分の確認",
    "impact_analysis": "影響分析",
    "impact_confirmation": "影響範囲の確認",
    "planning": "コード・テスト編成",
    "code_change": "VS Code GitHub Copilot によるコード変更",
    "execution_approval": "実行範囲の承認",
    "test_data_execution": "テストデータ生成・検証",
    "ui_verification": "UI テスト・結果検証",
    "closure": "変更クローズ判定",
    "completed": "完了",
}


def decide_change_automation(
    *,
    request: dict[str, object],
    diff: dict[str, object],
    workspace: dict[str, object] | None,
    has_orchestration: bool,
    execution: dict[str, object] | None,
) -> ChangeAutomationDecision:
    """Return the next trusted checkpoint without inventing missing evidence."""
    artifact = _dict(request.get("artifact"))
    if artifact.get("ambiguity_status") != "clear" or artifact.get("confirmation_required") is True:
        return _waiting(
            "requirement_confirmation",
            "confirm_requirement",
            "変更要件に曖昧さがあります。内容を確認してください。",
        )
    total = diff.get("total")
    if request.get("analysis_case_id") is None or not isinstance(total, int) or total == 0:
        return _waiting(
            "document_generation",
            "prepare_document_with_copilot",
            "VS Code 上の GitHub Copilot で設計書ドラフトを生成し、"
            "Canonical Case に取り込んでください。",
        )
    review = _dict(request.get("document_review"))
    if review.get("status") == "revision_requested":
        return _waiting(
            "document_revision",
            "revise_document_with_copilot",
            "指摘内容に従って設計書を修正し、差分を再生成してください。",
        )
    if review.get("status") != "confirmed":
        return _waiting(
            "document_confirmation",
            "confirm_document_diff",
            "生成された設計書差分を確認してください。",
        )
    if workspace is None:
        return _waiting(
            "impact_analysis",
            "prepare_canonical_analysis",
            "Canonical RAG・Code Graph・Impact Report を生成してください。",
        )
    impact = _dict(workspace.get("impact_report"))
    if impact.get("status") == "blocked":
        return _blocked("impact_analysis", "Impact Report に未解決の阻断理由があります。")
    if impact.get("id") is None:
        return _waiting(
            "impact_analysis",
            "prepare_canonical_analysis",
            "Canonical RAG・Code Graph・Impact Report を生成してください。",
        )
    confirmation = _dict(workspace.get("confirmation"))
    if confirmation.get("id") is None or impact.get("status") != "confirmed":
        return _waiting(
            "impact_confirmation",
            "confirm_impact",
            "影響項目ごとに承認または却下を選択してください。",
        )
    if not has_orchestration:
        return ChangeAutomationDecision(
            stage="planning",
            status="running",
            next_action="generate_orchestration",
            blocking_reason=None,
            message=(
                "確認済み証跡からコード範囲、Case、データ、カバレッジ、UI シナリオを生成します。"
            ),
        )
    edit_result = _dict(workspace.get("edit_result"))
    if edit_result.get("id") is None:
        return _waiting(
            "code_change",
            "apply_code_change_with_copilot",
            "生成されたコード範囲を VS Code 上の GitHub Copilot で変更し、"
            "結果を取り込んでください。",
        )
    if edit_result.get("status") not in {"succeeded", "passed", "completed"}:
        return _blocked("code_change", "コード変更結果が成功状態ではありません。")
    grant = _dict(workspace.get("approval_grant"))
    if grant.get("id") is None:
        return _waiting(
            "execution_approval",
            "issue_approval_grant",
            "テストと UI 検証の対象範囲を確認し、Approval Grant を発行してください。",
        )
    management = execution or {}
    data_result = _dict(management.get("test_data_execution"))
    if not data_result:
        return _waiting(
            "test_data_execution",
            "start_test_data_execution",
            "承認範囲でテストデータ生成と検証を開始してください。",
        )
    if data_result.get("status") in {"running", "reserved"}:
        return ChangeAutomationDecision(
            "test_data_execution", "running", "refresh", None, "テストデータを実行中です。"
        )
    if data_result.get("status") != "passed":
        return _blocked("test_data_execution", "テストデータ生成または検証が合格していません。")
    closure = _dict(management.get("change_closure"))
    if not closure:
        return _waiting(
            "ui_verification",
            "run_ui_verification",
            "実ブラウザで UI シナリオを実行し、スクリーンショット証跡を保存してください。",
        )
    if closure.get("status") == "passed":
        return ChangeAutomationDecision(
            "completed", "completed", None, None, "文書、コード、テスト、UI 検証が完了しました。"
        )
    return _blocked("closure", "ChangeClosureResult に未解決項目があります。")


def _waiting(stage: str, action: str, message: str) -> ChangeAutomationDecision:
    return ChangeAutomationDecision(stage, "waiting", action, None, message)


def _blocked(stage: str, reason: str) -> ChangeAutomationDecision:
    return ChangeAutomationDecision(stage, "blocked", "resolve_blocker", reason, reason)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
