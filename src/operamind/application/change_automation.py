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
    "rag_document_confirmation": "RAG 対象設計書の確認",
    "document_generation": "設計書ドラフト生成",
    "document_revision": "設計書の再修正",
    "document_confirmation": "設計書差分の確認",
    "impact_analysis": "影響分析",
    "impact_confirmation": "コード影響範囲の確認",
    "execution_approval": "実行範囲の自動準備",
    "code_change": "VS Code GitHub Copilot によるコード変更",
    "planning": "コード・テスト編成",
    "test_plan_confirmation": "UI テスト計画と実行範囲の確認",
    "ui_test_confirmation": "旧 UI テスト実行確認",
    "test_data_execution": "テストデータ生成・検証",
    "ui_verification": "UI テスト・結果検証",
    "closure": "変更クローズ判定",
    "final_report_confirmation": "最終レポートの確認",
    "completed": "完了",
}

PUBLIC_STAGE_IDS = (
    "requirement",
    "document_change",
    "code_scope",
    "compile_test",
    "ui_validation",
    "final_report",
)

INTERNAL_TO_PUBLIC_STAGE = {
    "requirement_confirmation": "requirement",
    "rag_document_confirmation": "document_change",
    "document_generation": "document_change",
    "document_revision": "document_change",
    "document_confirmation": "document_change",
    "impact_analysis": "code_scope",
    "impact_confirmation": "code_scope",
    "execution_approval": "compile_test",
    "code_change": "compile_test",
    "planning": "ui_validation",
    "test_plan_confirmation": "ui_validation",
    "ui_test_confirmation": "ui_validation",
    "test_data_execution": "ui_validation",
    "ui_verification": "ui_validation",
    "closure": "final_report",
    "final_report_confirmation": "final_report",
    "completed": "final_report",
}

CONFIRMATION_ACTION_CHECKPOINTS = {
    "confirm_requirement": "requirement",
    "confirm_rag_documents": "rag_documents",
    "confirm_document_diff": "document_diff",
    "confirm_code_scope": "code_scope",
    "confirm_test_plan": "test_plan",
    "confirm_ui_test": "ui_test",
    "confirm_final_report": "final_report",
}

COPILOT_TASK_AUTOMATION_STAGES = {
    "document_change": frozenset({"document_generation", "document_revision"}),
    "code_scope": frozenset({"impact_analysis"}),
}

_ACTION_STAGES = {
    "confirm_requirement": frozenset({"requirement_confirmation"}),
    "confirm_rag_documents": frozenset({"rag_document_confirmation"}),
    "prepare_document_with_copilot": frozenset({"document_generation"}),
    "revise_document_with_copilot": frozenset({"document_revision"}),
    "confirm_document_diff": frozenset({"document_confirmation"}),
    "prepare_canonical_analysis": frozenset({"impact_analysis"}),
    "analyze_code_scope_with_copilot": frozenset({"impact_analysis"}),
    "confirm_code_scope": frozenset({"impact_confirmation"}),
    "provision_execution_scope": frozenset({"execution_approval"}),
    "apply_code_change_with_copilot": frozenset({"code_change"}),
    "generate_ui_test_plan": frozenset({"code_change"}),
    "generate_orchestration": frozenset({"planning"}),
    "inspect_generated_plan": frozenset({"planning"}),
    "confirm_test_plan": frozenset({"test_plan_confirmation"}),
    "confirm_ui_test": frozenset({"ui_test_confirmation"}),
    "start_test_data_execution": frozenset({"test_data_execution"}),
    "refresh": frozenset({"test_data_execution"}),
    "run_ui_verification": frozenset({"ui_verification"}),
    "confirm_final_report": frozenset({"final_report_confirmation"}),
}

_AUTOMATION_STATUSES = frozenset({"waiting", "running", "blocked", "completed"})


@dataclass(frozen=True, slots=True)
class ChangeFlowProjection:
    """One normalized state shared by Web, MCP, Copilot, and the coordinator."""

    internal_stage: str
    public_stage: str
    status: str
    next_action: str | None
    confirmation_checkpoint: str | None
    blocking_reasons: tuple[str, ...]


class ChangeFlowStateMachine:
    """Own workflow decisions and all cross-surface state projections."""

    def decide(
        self,
        *,
        request: dict[str, object],
        diff: dict[str, object],
        workspace: dict[str, object] | None,
        has_orchestration: bool,
        execution: dict[str, object] | None,
        confirmations: dict[str, str] | None = None,
        rag_discovery: dict[str, object] | None = None,
    ) -> ChangeAutomationDecision:
        decision = _decide_change_automation(
            request=request,
            diff=diff,
            workspace=workspace,
            has_orchestration=has_orchestration,
            execution=execution,
            confirmations=confirmations,
            rag_discovery=rag_discovery,
        )
        self.validate_decision(decision)
        return decision

    def validate_decision(self, decision: ChangeAutomationDecision) -> None:
        if decision.stage not in INTERNAL_TO_PUBLIC_STAGE:
            raise ValueError(f"Unknown Change Flow stage: {decision.stage}")
        if decision.status not in _AUTOMATION_STATUSES:
            raise ValueError(f"Unknown Change Flow status: {decision.status}")
        if decision.status == "blocked" and decision.next_action != "resolve_blocker":
            raise ValueError("Blocked Change Flow must require resolve_blocker")
        if (decision.stage == "completed") != (decision.status == "completed"):
            raise ValueError("Completed Change Flow stage and status must be terminal together")
        if decision.status == "completed" and decision.next_action is not None:
            raise ValueError("Completed Change Flow must be terminal without a next action")
        if decision.next_action in CONFIRMATION_ACTION_CHECKPOINTS and decision.status != "waiting":
            raise ValueError("Human confirmation must be exposed as a waiting state")
        self._validate_stage_action(decision.stage, decision.next_action)

    def project(self, automation: dict[str, object]) -> ChangeFlowProjection:
        value = automation
        nested = value.get("run")
        if isinstance(nested, dict):
            value = nested
        internal_stage = str(value.get("current_stage") or "")
        status = str(value.get("status") or "")
        if internal_stage not in INTERNAL_TO_PUBLIC_STAGE:
            raise ValueError(f"Unknown persisted Change Flow stage: {internal_stage}")
        if status not in _AUTOMATION_STATUSES:
            raise ValueError(f"Unknown persisted Change Flow status: {status}")
        next_action_value = value.get("next_action")
        next_action = str(next_action_value) if next_action_value is not None else None
        reason = value.get("blocking_reason") or value.get("message")
        self.validate_decision(
            ChangeAutomationDecision(
                stage=internal_stage,
                status=status,
                next_action=next_action,
                blocking_reason=str(reason) if reason else None,
                message=str(value.get("message") or "Persisted Change Flow state"),
            )
        )
        blockers = (str(reason),) if status == "blocked" and reason else ()
        return ChangeFlowProjection(
            internal_stage=internal_stage,
            public_stage=INTERNAL_TO_PUBLIC_STAGE[internal_stage],
            status=status,
            next_action=next_action,
            confirmation_checkpoint=CONFIRMATION_ACTION_CHECKPOINTS.get(next_action or ""),
            blocking_reasons=blockers,
        )

    def confirmation_checkpoint(self, next_action: str | None) -> str | None:
        return CONFIRMATION_ACTION_CHECKPOINTS.get(next_action or "")

    @staticmethod
    def _validate_stage_action(stage: str, next_action: str | None) -> None:
        if next_action is None or next_action == "resolve_blocker":
            return
        allowed_stages = _ACTION_STAGES.get(next_action)
        if allowed_stages is None:
            raise ValueError(f"Unknown Change Flow action: {next_action}")
        if stage not in allowed_stages:
            raise ValueError(
                f"Change Flow action {next_action} is invalid for stage {stage}"
            )

    def allows_copilot_stage(
        self,
        *,
        task_stage: str,
        automation_stage: object,
        has_review_feedback: bool = False,
    ) -> bool:
        if has_review_feedback:
            return True
        return automation_stage in COPILOT_TASK_AUTOMATION_STAGES.get(task_stage, frozenset())

    def is_ready_for_action(
        self, automation: dict[str, object] | None, *, action: str
    ) -> bool:
        if automation is None:
            return False
        projection = self.project(automation)
        return projection.status == "waiting" and projection.next_action == action

    def is_running_action(
        self, automation: dict[str, object] | None, *, action: str
    ) -> bool:
        if automation is None:
            return False
        projection = self.project(automation)
        return projection.status == "running" and projection.next_action == action

    def normalize_public_stage_statuses(
        self,
        *,
        automation: dict[str, object],
        evidence_statuses: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Make the persisted decision authoritative while retaining fail-closed blockers."""

        if len(evidence_statuses) != len(PUBLIC_STAGE_IDS):
            raise ValueError("Public Change Flow evidence status count is invalid")
        projection = self.project(automation)
        if projection.internal_stage == "completed":
            blocked_index = next(
                (index for index, status in enumerate(evidence_statuses) if status == "blocked"),
                None,
            )
            if blocked_index is None:
                return tuple("completed" for _ in PUBLIC_STAGE_IDS)
            current_index = blocked_index
        else:
            current_index = PUBLIC_STAGE_IDS.index(projection.public_stage)
            earlier_blocker = next(
                (
                    index
                    for index, status in enumerate(evidence_statuses[: current_index + 1])
                    if status == "blocked"
                ),
                None,
            )
            if earlier_blocker is not None:
                current_index = earlier_blocker
        statuses = ["completed" if index < current_index else "waiting" for index in range(6)]
        evidence_status = evidence_statuses[current_index]
        if evidence_status == "blocked" or projection.status == "blocked":
            statuses[current_index] = "blocked"
        elif evidence_status == "running" or projection.status == "running":
            statuses[current_index] = "running"
        else:
            statuses[current_index] = "waiting"
        return tuple(statuses)

    @staticmethod
    def flow_requires_confirmation(flow: dict[str, object] | None) -> bool:
        if flow is None:
            return False
        stages = flow.get("stages")
        if not isinstance(stages, list):
            return False
        return any(
            isinstance(stage, dict)
            and isinstance(stage.get("details"), dict)
            and isinstance(stage["details"].get("confirmation"), dict)
            for stage in stages
        )


CHANGE_FLOW_STATE_MACHINE = ChangeFlowStateMachine()


@dataclass(frozen=True, slots=True)
class _ChangeFlowInputs:
    request: dict[str, object]
    diff: dict[str, object]
    workspace: dict[str, object] | None
    has_orchestration: bool
    execution: dict[str, object] | None
    confirmations: dict[str, str]
    rag_discovery: dict[str, object]


def _decide_change_automation(
    *,
    request: dict[str, object],
    diff: dict[str, object],
    workspace: dict[str, object] | None,
    has_orchestration: bool,
    execution: dict[str, object] | None,
    confirmations: dict[str, str] | None = None,
    rag_discovery: dict[str, object] | None = None,
) -> ChangeAutomationDecision:
    """Return the next trusted checkpoint without inventing missing evidence."""
    inputs = _ChangeFlowInputs(
        request=request,
        diff=diff,
        workspace=workspace,
        has_orchestration=has_orchestration,
        execution=execution,
        confirmations=confirmations or {},
        rag_discovery=rag_discovery or {},
    )
    for resolver in (
        _decide_document_stage,
        _decide_code_scope_stage,
        _decide_compile_test_stage,
    ):
        decision = resolver(inputs)
        if decision is not None:
            return decision
    return _decide_ui_and_closure_stage(inputs)


def _decide_document_stage(inputs: _ChangeFlowInputs) -> ChangeAutomationDecision | None:
    requirement = _confirmation_decision(
        inputs.confirmations,
        checkpoint="requirement",
        stage="requirement_confirmation",
        action="confirm_requirement",
        message="変更要件を確認してください。",
    )
    if requirement is not None:
        return requirement
    discovery = inputs.rag_discovery
    if discovery.get("status") != "ready":
        reason = str(
            discovery.get("blocking_reason") or "Canonical RAG から対象設計書を取得できません。"
        )
        return _blocked("rag_document_confirmation", reason)
    rag_documents = _confirmation_decision(
        inputs.confirmations,
        checkpoint="rag_documents",
        stage="rag_document_confirmation",
        action="confirm_rag_documents",
        message="RAG が選定した対象設計書と根拠箇所を確認してください。",
    )
    if rag_documents is not None:
        return rag_documents
    if inputs.request.get("analysis_case_id") is None:
        return _waiting(
            "document_generation",
            "prepare_document_with_copilot",
            "VS Code 上の GitHub Copilot で設計書ドラフトを生成してください。",
        )
    total = inputs.diff.get("total")
    if not isinstance(total, int) or total == 0:
        return _waiting(
            "document_generation",
            "prepare_document_with_copilot",
            "VS Code 上の GitHub Copilot で設計書ドラフトを生成し、"
            "Canonical Case に取り込んでください。",
        )
    review = _dict(inputs.request.get("document_review"))
    if review.get("status") == "revision_requested":
        revision_task = _dict((inputs.workspace or {}).get("copilot_task"))
        if revision_task.get("current_stage") == "document_change":
            return _waiting(
                "document_revision",
                "revise_document_with_copilot",
                "指摘内容に従って設計書を修正し、差分を再生成してください。",
            )
    if review.get("status") != "confirmed":
        document_diff = _confirmation_decision(
            inputs.confirmations,
            checkpoint="document_diff",
            stage="document_confirmation",
            action="confirm_document_diff",
            message="生成された設計書差分を確認してください。",
        )
        if document_diff is not None:
            return document_diff
    return None


def _decide_code_scope_stage(inputs: _ChangeFlowInputs) -> ChangeAutomationDecision | None:
    workspace = inputs.workspace
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
    copilot_task = _dict(workspace.get("copilot_task"))
    if _has_identified_copilot_task(copilot_task) and not _task_recorded_impact(
        copilot_task,
        impact_report_id=str(impact["id"]),
    ):
        return _waiting(
            "impact_analysis",
            "analyze_code_scope_with_copilot",
            "現在の設計書差分から VS Code GitHub Copilot がコード影響範囲を"
            "再解析してください。以前の Task の Impact Report は再利用しません。",
        )
    confirmation = _dict(workspace.get("confirmation"))
    if confirmation.get("id") is None or impact.get("status") != "confirmed":
        code_scope = _confirmation_decision(
            inputs.confirmations,
            checkpoint="code_scope",
            stage="impact_confirmation",
            action="confirm_code_scope",
            message="変更対象コードとテスト影響範囲を確認してください。",
        )
        if code_scope is not None:
            return code_scope
    grant = _dict(workspace.get("approval_grant"))
    if grant.get("id") is None:
        return ChangeAutomationDecision(
            stage="execution_approval",
            status="running",
            next_action="provision_execution_scope",
            blocking_reason=None,
            message="確認済み影響範囲からコード変更とテストの実行範囲を自動準備します。",
        )
    execution_scope = _dict(copilot_task.get("execution_scope"))
    edit_packet = _dict(workspace.get("edit_packet"))
    current_grant = _dict(workspace.get("approval_grant"))
    scoped_packet_id = execution_scope.get("edit_packet_id")
    scoped_grant_id = execution_scope.get("approval_grant_id")
    if (
        execution_scope.get("bound") is not True
        or (scoped_packet_id is not None and scoped_packet_id != edit_packet.get("id"))
        or (scoped_grant_id is not None and scoped_grant_id != current_grant.get("id"))
    ):
        return ChangeAutomationDecision(
            stage="execution_approval",
            status="running",
            next_action="provision_execution_scope",
            blocking_reason=None,
            message="現在の VS Code Change Task に確認済み実行範囲を再バインドします。",
        )
    return None


def _decide_compile_test_stage(inputs: _ChangeFlowInputs) -> ChangeAutomationDecision | None:
    workspace = inputs.workspace
    if workspace is None:
        raise RuntimeError("Change Flow reached compile/test without a Workspace")
    copilot_task = _dict(workspace.get("copilot_task"))
    edit_packet = _dict(workspace.get("edit_packet"))
    edit_result = _dict(workspace.get("edit_result"))
    if edit_result.get("id") is None:
        return _waiting(
            "code_change",
            "apply_code_change_with_copilot",
            "生成されたコード範囲を VS Code 上の GitHub Copilot で変更し、"
            "結果を取り込んでください。",
        )
    verification_only = not bool(edit_packet.get("editable_files", ["pending"]))
    edit_status = edit_result.get("status")
    accepted_edit_status = edit_status == "in_scope" or (
        verification_only and edit_status == "no_changes"
    )
    validation_mode = edit_result.get("validation_mode")
    if validation_mode == "working":
        if accepted_edit_status:
            return _waiting(
                "code_change",
                "apply_code_change_with_copilot",
                "コード差分は実行範囲内です。VS Code 上の GitHub Copilot で "
                "TestPlan と TestDataPlan を生成し、コンパイルとテストを完了してください。",
            )
        return _blocked("code_change", "作業中のコード差分が実行範囲外です。")
    if (
        validation_mode != "committed"
        or not accepted_edit_status
        or edit_result.get("tests_passed") is not True
        or edit_result.get("command_evidence_status") != "verified"
        or edit_result.get("changed_line_coverage_status") not in {"passed", "not_required"}
    ):
        return _blocked(
            "code_change",
            "コード変更、テスト、または変更行カバレッジが成功状態ではありません。",
        )
    if (
        copilot_task.get("state") != "completed"
        or copilot_task.get("current_stage") != "ui_validation"
    ):
        return _waiting(
            "code_change",
            "generate_ui_test_plan",
            "コード結果は確定しました。VS Code 上の GitHub Copilot で、"
            "確定済み設計とコードから UI TestPlan / TestDataPlan を生成してください。",
        )
    if not inputs.has_orchestration:
        return ChangeAutomationDecision(
            stage="planning",
            status="running",
            next_action="generate_orchestration",
            blocking_reason=None,
            message=(
                "変更済みコードと Copilot TestPlan からテストデータ、"
                "カバレッジ、UI シナリオを生成します。"
            ),
        )
    return None


def _decide_ui_and_closure_stage(inputs: _ChangeFlowInputs) -> ChangeAutomationDecision:
    management = inputs.execution or {}
    business_coverage = _dict(management.get("business_coverage"))
    if (
        business_coverage.get("status") != "passed"
        or business_coverage.get("coverage_percent") != 100
    ):
        return _blocked(
            "planning",
            "業務要件カバレッジが 100% ではありません。未カバー要件を VS Code "
            "GitHub Copilot に自動返却し、UI TestPlan と TestDataPlan を再生成してください。",
        )
    test_plan = _confirmation_decision(
        inputs.confirmations,
        checkpoint="test_plan",
        stage="test_plan_confirmation",
        action="confirm_test_plan",
        message=(
            "生成された UI TestPlan、TestDataPlan、Playwright 実行手順、"
            "業務カバレッジを確認し、実ブラウザ実行を許可してください。"
        ),
    )
    if test_plan is not None:
        return test_plan
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
    execution_artifact = _dict(data_result.get("result"))
    data_coverage = _dict(execution_artifact.get("data_coverage"))
    if (
        data_coverage.get("status") != "passed"
        or data_coverage.get("coverage_percent") != 100
    ):
        return _blocked(
            "test_data_execution",
            "実 DB の Test Data Coverage が 100% ではないため UI 検証へ進めません。",
        )
    closure = _dict(management.get("change_closure"))
    if not closure:
        return _waiting(
            "ui_verification",
            "run_ui_verification",
            "実ブラウザで UI シナリオを実行し、スクリーンショット証跡を保存してください。",
        )
    if closure.get("status") == "passed":
        final_report = _confirmation_decision(
            inputs.confirmations,
            checkpoint="final_report",
            stage="final_report_confirmation",
            action="confirm_final_report",
            message="最終レポートと証跡を確認してください。",
        )
        if final_report is not None:
            return final_report
        return ChangeAutomationDecision(
            "completed", "completed", None, None, "文書、コード、テスト、UI 検証が完了しました。"
        )
    return _blocked("closure", "ChangeClosureResult に未解決項目があります。")


def decide_change_automation(
    *,
    request: dict[str, object],
    diff: dict[str, object],
    workspace: dict[str, object] | None,
    has_orchestration: bool,
    execution: dict[str, object] | None,
    confirmations: dict[str, str] | None = None,
    rag_discovery: dict[str, object] | None = None,
) -> ChangeAutomationDecision:
    """Compatibility entry point backed by the single Change Flow state machine."""

    return CHANGE_FLOW_STATE_MACHINE.decide(
        request=request,
        diff=diff,
        workspace=workspace,
        has_orchestration=has_orchestration,
        execution=execution,
        confirmations=confirmations,
        rag_discovery=rag_discovery,
    )


def _waiting(stage: str, action: str, message: str) -> ChangeAutomationDecision:
    return ChangeAutomationDecision(stage, "waiting", action, None, message)


def _blocked(stage: str, reason: str) -> ChangeAutomationDecision:
    return ChangeAutomationDecision(stage, "blocked", "resolve_blocker", reason, reason)


def _confirmation_decision(
    confirmations: dict[str, str],
    *,
    checkpoint: str,
    stage: str,
    action: str,
    message: str,
) -> ChangeAutomationDecision | None:
    decision = confirmations.get(checkpoint)
    if decision == "confirmed":
        return None
    if decision == "rejected":
        return _blocked(stage, f"{message} ユーザーにより差し戻されました。")
    return _waiting(stage, action, message)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _has_identified_copilot_task(task_view: dict[str, Any]) -> bool:
    task = _dict(task_view.get("task"))
    task_id = task.get("coding_task_id")
    return isinstance(task_id, str) and bool(task_id.strip())


def _task_recorded_impact(task_view: dict[str, Any], *, impact_report_id: str) -> bool:
    events = task_view.get("events")
    if not isinstance(events, list):
        return False
    for event_value in events:
        event = _dict(event_value)
        payload = _dict(event.get("payload"))
        if (
            event.get("event_type") == "outputs_recorded"
            and payload.get("output_stage") == "code_scope"
            and payload.get("impact_report_id") == impact_report_id
        ):
            return True
    return False
