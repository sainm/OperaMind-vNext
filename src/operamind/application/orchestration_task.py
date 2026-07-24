"""Agent-neutral task definitions derived from business workflow decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from operamind.application.change_automation import STAGE_LABELS, ChangeAutomationDecision

EXECUTOR_KINDS = ("agent", "subagent", "human")
ORCHESTRATION_TASK_PROTOCOL_VERSION = "orchestration_task_v1"
ORCHESTRATION_MAX_ACTIVE_TASKS_ENV = "OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN"


@dataclass(frozen=True, slots=True)
class OrchestrationTaskDefinition:
    protocol_version: str
    orchestration_task_id: str
    automation_run_id: str
    change_request_id: str
    project_id: str
    step_key: str
    action: str
    title: str
    instruction: str
    task_kind: str
    required_capabilities: tuple[str, ...]
    eligible_executor_kinds: tuple[str, ...]
    input_artifact_refs: tuple[str, ...]
    expected_output_types: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    lease_seconds: int
    max_attempts: int
    definition_digest: str
    priority: int = 100

    def __post_init__(self) -> None:
        """Keep every persisted definition inside the neutral executor contract."""
        required_text = {
            "protocol_version": self.protocol_version,
            "orchestration_task_id": self.orchestration_task_id,
            "automation_run_id": self.automation_run_id,
            "change_request_id": self.change_request_id,
            "project_id": self.project_id,
            "step_key": self.step_key,
            "action": self.action,
            "title": self.title,
            "instruction": self.instruction,
            "task_kind": self.task_kind,
            "definition_digest": self.definition_digest,
        }
        if any(not isinstance(value, str) or not value.strip() for value in required_text.values()):
            raise ValueError("Orchestration Task definition text must not be blank")
        if self.eligible_executor_kinds != EXECUTOR_KINDS:
            raise ValueError("Orchestration Task must be claimable by agent, subagent, and human")
        if self.task_kind not in {
            "deterministic_action",
            "judgment",
            "external_execution",
            "verification",
            "recovery",
        }:
            raise ValueError("Orchestration Task kind is invalid")
        if not self.required_capabilities:
            raise ValueError("Orchestration Task requires at least one capability")
        if not self.expected_output_types or not self.acceptance_criteria:
            raise ValueError("Orchestration Task requires outputs and acceptance criteria")
        if not 30 <= self.lease_seconds <= 86400:
            raise ValueError("Orchestration Task lease_seconds is out of bounds")
        if not 1 <= self.max_attempts <= 100:
            raise ValueError("Orchestration Task max_attempts is out of bounds")
        if not 1 <= self.priority <= 1000:
            raise ValueError("Orchestration Task priority is out of bounds")
        if len(self.definition_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.definition_digest
        ):
            raise ValueError("Orchestration Task definition_digest must be SHA-256")


@dataclass(frozen=True, slots=True)
class OrchestrationSchedulingPolicy:
    """Deployment policy; changing it must not alter business task definitions."""

    max_active_tasks_per_run: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.max_active_tasks_per_run <= 100:
            raise ValueError("max_active_tasks_per_run must be between 1 and 100")


def parse_orchestration_scheduling_policy(
    value: str | None,
) -> OrchestrationSchedulingPolicy:
    """Parse deployment parallelism without coupling it to business decisions."""
    if value is None:
        return OrchestrationSchedulingPolicy()
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{ORCHESTRATION_MAX_ACTIVE_TASKS_ENV} must not be blank")
    try:
        maximum = int(normalized)
    except ValueError as error:
        raise ValueError(f"{ORCHESTRATION_MAX_ACTIVE_TASKS_ENV} must be an integer") from error
    return OrchestrationSchedulingPolicy(max_active_tasks_per_run=maximum)


@dataclass(frozen=True, slots=True)
class _ActionPolicy:
    task_kind: str
    capabilities: tuple[str, ...]
    outputs: tuple[str, ...]
    criteria: tuple[str, ...]
    lease_seconds: int = 300
    max_attempts: int = 3


_DEFAULT_POLICY = _ActionPolicy(
    "recovery",
    ("workflow_recovery",),
    ("EvidenceRef",),
    ("阻断原因已被消除, 且 Canonical 状态可重新判定。",),
)

ACTION_POLICIES: dict[str, _ActionPolicy] = {
    "confirm_requirement": _ActionPolicy(
        "judgment",
        ("requirement_review",),
        ("RequirementConfirmation",),
        ("必须引用可审计的人工确认记录; 领取者身份本身不构成批准。",),
        1800,
    ),
    "prepare_document_with_copilot": _ActionPolicy(
        "external_execution",
        ("document_generation",),
        ("DocumentSnapshot", "StructuredChange"),
        ("设计书已写入 Canonical Case, 并可生成非空差分。",),
        900,
    ),
    "revise_document_with_copilot": _ActionPolicy(
        "external_execution",
        ("document_revision",),
        ("DocumentSnapshot", "StructuredChange"),
        ("修订意见已落实, 并生成新的可审查差分。",),
        900,
    ),
    "confirm_document_diff": _ActionPolicy(
        "judgment",
        ("document_review",),
        ("DocumentReview",),
        ("必须引用逐项确认或退回的人工审查记录。",),
        1800,
    ),
    "prepare_canonical_analysis": _ActionPolicy(
        "deterministic_action",
        ("rag_analysis", "code_graph_analysis", "impact_analysis"),
        ("ImpactReport",),
        ("RAG、Code Graph 与 Impact Report 均来自 Canonical 证据。",),
        900,
    ),
    "confirm_impact": _ActionPolicy(
        "judgment",
        ("impact_review",),
        ("ImpactConfirmation",),
        ("每个影响项都有可审计的人工批准或拒绝结果。",),
        1800,
    ),
    "generate_orchestration": _ActionPolicy(
        "deterministic_action",
        ("change_planning",),
        ("ChangeOrchestration", "TestDataPlan", "UiExecutionPlan"),
        ("生成物可追溯到已确认的需求、设计差分和影响项。",),
    ),
    "apply_code_change_with_copilot": _ActionPolicy(
        "external_execution",
        ("source_code_edit",),
        ("EditResult",),
        ("修改范围不超出 EditPacket, 且结果包含命令与差分证据。",),
        900,
    ),
    "issue_approval_grant": _ActionPolicy(
        "judgment",
        ("execution_scope_review",),
        ("ApprovalGrant",),
        ("必须引用人工确认的执行范围, 且授权尚未过期。",),
        1800,
    ),
    "start_test_data_execution": _ActionPolicy(
        "external_execution",
        ("test_data_execution",),
        ("TestDataExecutionResult",),
        ("准备、断言、清理均完成, 并保留跨画面变量证据。",),
        900,
    ),
    "refresh": _ActionPolicy(
        "verification",
        ("state_observation",),
        ("ExecutionState",),
        ("重新读取 Canonical 执行状态, 不以本地推测替代。",),
    ),
    "run_ui_verification": _ActionPolicy(
        "verification",
        ("ui_test_execution",),
        ("UiVerificationResult",),
        ("UI 场景、截图、断言和业务覆盖率均已记录。",),
        900,
    ),
    "resolve_blocker": _DEFAULT_POLICY,
}

AGENT_NEUTRAL_ACTIONS = {
    "prepare_document_with_copilot": "prepare_document",
    "revise_document_with_copilot": "revise_document",
    "apply_code_change_with_copilot": "apply_code_change",
}

AGENT_NEUTRAL_INSTRUCTIONS = {
    "prepare_document_with_copilot": (
        "根据已确认的变更需求生成设计书草案, 并写入 Canonical Case。"
    ),
    "revise_document_with_copilot": (
        "根据审查意见修订设计书, 写入新的 Document Snapshot 并重新生成差分。"
    ),
    "apply_code_change_with_copilot": (
        "依据已批准的 EditPacket 修改允许范围内的源代码, 并记录命令、差分和结果证据。"
    ),
}

AGENT_NEUTRAL_STAGE_LABELS = {
    "code_change": "コード変更",
}


def build_orchestration_task(
    *,
    automation_run_id: str,
    change_request_id: str,
    project_id: str,
    decision: ChangeAutomationDecision,
    input_artifact_refs: tuple[str, ...] = (),
) -> OrchestrationTaskDefinition | None:
    """Create a stable task contract without selecting a concrete executor."""
    if decision.next_action is None or decision.stage == "completed":
        return None
    policy = ACTION_POLICIES.get(decision.next_action, _DEFAULT_POLICY)
    action = AGENT_NEUTRAL_ACTIONS.get(decision.next_action, decision.next_action)
    instruction = AGENT_NEUTRAL_INSTRUCTIONS.get(decision.next_action, decision.message)
    payload = {
        "protocol_version": ORCHESTRATION_TASK_PROTOCOL_VERSION,
        "automation_run_id": automation_run_id,
        "change_request_id": change_request_id,
        "project_id": project_id,
        "step_key": decision.stage,
        "action": action,
        "instruction": instruction,
        "task_kind": policy.task_kind,
        "required_capabilities": list(policy.capabilities),
        "eligible_executor_kinds": list(EXECUTOR_KINDS),
        "input_artifact_refs": list(input_artifact_refs),
        "expected_output_types": list(policy.outputs),
        "acceptance_criteria": list(policy.criteria),
        "lease_seconds": policy.lease_seconds,
        "max_attempts": policy.max_attempts,
        "priority": 100,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    identity = f"{automation_run_id}:{decision.stage}:{action}:{digest}"
    task_id = f"orchestration-task-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
    return OrchestrationTaskDefinition(
        protocol_version=ORCHESTRATION_TASK_PROTOCOL_VERSION,
        orchestration_task_id=task_id,
        automation_run_id=automation_run_id,
        change_request_id=change_request_id,
        project_id=project_id,
        step_key=decision.stage,
        action=action,
        title=AGENT_NEUTRAL_STAGE_LABELS.get(
            decision.stage, STAGE_LABELS.get(decision.stage, decision.stage)
        ),
        instruction=instruction,
        task_kind=policy.task_kind,
        required_capabilities=policy.capabilities,
        eligible_executor_kinds=EXECUTOR_KINDS,
        input_artifact_refs=input_artifact_refs,
        expected_output_types=policy.outputs,
        acceptance_criteria=policy.criteria,
        lease_seconds=policy.lease_seconds,
        max_attempts=policy.max_attempts,
        definition_digest=digest,
        priority=100,
    )


def validate_orchestration_result_evidence(evidence: dict[str, object]) -> None:
    """Reject unbounded bodies and secrets from the task result ledger."""
    if not evidence or len(evidence) > 100:
        raise ValueError("result evidence must contain between 1 and 100 fields")
    forbidden = ("source_code", "diff_body", "document_body", "secret", "token", "password")
    for key, value in evidence.items():
        normalized = key.strip().lower()
        if not normalized or len(key) > 160:
            raise ValueError("result evidence keys must be non-blank and bounded")
        if any(fragment in normalized for fragment in forbidden):
            raise ValueError("result evidence must not contain source, Diff, or secret bodies")
        if not isinstance(value, str | bool | int | float) and value is not None:
            raise ValueError("result evidence values must be scalar")
        if isinstance(value, str) and len(value) > 2_000:
            raise ValueError("result evidence string values must be bounded")
