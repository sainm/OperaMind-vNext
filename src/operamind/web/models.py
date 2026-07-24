"""Strict public request models for the Web control plane."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OrchestrationTaskClaim(StrictModel):
    executor_kind: Literal["agent", "subagent", "human"]
    capabilities: list[str] = Field(min_length=1, max_length=100)
    project_id: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("capabilities")
    @classmethod
    def require_unique_capabilities(cls, value: list[str]) -> list[str]:
        if any(not capability.strip() for capability in value):
            raise ValueError("capabilities must not contain blank values")
        if len(value) != len(set(value)):
            raise ValueError("capabilities must be unique")
        return value


class OrchestrationTaskLease(StrictModel):
    lease_token: str = Field(min_length=32, max_length=500)


class OrchestrationTaskRelease(OrchestrationTaskLease):
    reason: str = Field(min_length=1, max_length=2_000)


class OrchestrationTaskRequeue(StrictModel):
    reason: str = Field(min_length=1, max_length=2_000)


class OrchestrationTaskPriorityUpdate(StrictModel):
    priority: int = Field(ge=1, le=1000)


class OrchestrationTaskResult(OrchestrationTaskLease):
    outcome: Literal["completed", "failed", "blocked"]
    summary: str = Field(min_length=1, max_length=10_000)
    artifact_refs: list[str] = Field(default_factory=list, max_length=500)
    evidence: dict[str, str | bool | int | float | None] = Field(
        default_factory=dict, min_length=1, max_length=100
    )


class OrchestrationWorkerConfigurationUpdate(StrictModel):
    capabilities: list[str] = Field(min_length=1, max_length=100)
    max_concurrent_tasks: int = Field(ge=1, le=100)

    @field_validator("capabilities")
    @classmethod
    def require_unique_worker_capabilities(cls, value: list[str]) -> list[str]:
        if any(not capability.strip() or len(capability) > 160 for capability in value):
            raise ValueError("capabilities must be non-blank and bounded")
        if len(value) != len(set(value)):
            raise ValueError("capabilities must be unique")
        return value


class LocalEnvironmentExtensionDiagnostic(StrictModel):
    """Secret-free VS Code observation accepted only by the loopback Bridge."""

    consumer_id: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    workspace_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    vsix_version: str = Field(min_length=1, max_length=40)
    bridge_url_loopback: bool
    bridge_token_configured: bool
    workspace_trusted: bool
    linked_worktree: bool
    mcp_tool_names: list[str] = Field(default_factory=list, max_length=32)
    copilot_extension_installed: bool
    copilot_extension_active: bool
    copilot_extension_version: str | None = Field(default=None, max_length=80)
    copilot_model_api_available: bool
    copilot_model_count: int = Field(ge=0, le=100)

    @field_validator("observed_at")
    @classmethod
    def require_diagnostic_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value

    @field_validator("mcp_tool_names")
    @classmethod
    def validate_mcp_tool_names(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("mcp_tool_names must be unique")
        safe_characters = "abcdefghijklmnopqrstuvwxyz0123456789_-"
        if any(
            not name or len(name) > 160 or any(char not in safe_characters for char in name)
            for name in value
        ):
            raise ValueError("mcp_tool_names must contain only lowercase safe identifiers")
        return value


class BusinessRuleModel(StrictModel):
    business_rule_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=10_000)
    source_refs: list[str] = Field(default_factory=list, max_length=100)


class ChangeRequestCreate(StrictModel):
    change_request_id: str = Field(min_length=1, max_length=160)
    project_id: str = Field(min_length=1, max_length=160)
    analysis_case_id: str | None = Field(default=None, max_length=160)
    input_mode: Literal["documents", "natural_language", "hybrid"]
    requirement_text: str | None = Field(default=None, max_length=50_000)
    source_document_ref: str | None = Field(default=None, max_length=2_000)
    target_document_ref: str | None = Field(default=None, max_length=2_000)
    business_rules: list[BusinessRuleModel] = Field(min_length=1, max_length=100)
    ambiguity_status: Literal["clear", "needs_confirmation"] = "clear"
    ambiguities: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_input(self) -> ChangeRequestCreate:
        if self.input_mode in {"natural_language", "hybrid"} and not self.requirement_text:
            raise ValueError("requirement_text is required for this input mode")
        if self.input_mode in {"documents", "hybrid"} and not self.source_document_ref:
            raise ValueError("source_document_ref is required for this input mode")
        if self.input_mode in {"documents", "hybrid"} and not self.target_document_ref:
            raise ValueError("target_document_ref is required for this input mode")
        if self.ambiguity_status == "clear" and self.ambiguities:
            raise ValueError("clear requests cannot contain ambiguities")
        if self.ambiguity_status == "needs_confirmation" and not self.ambiguities:
            raise ValueError("needs_confirmation requests require ambiguities")
        rule_ids = [rule.business_rule_id for rule in self.business_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("business_rule_id values must be unique")
        return self


class DocumentReviewCreate(StrictModel):
    project_id: str = Field(min_length=1, max_length=160)
    decision: Literal["confirmed", "revision_requested"]
    note: str | None = Field(default=None, max_length=10_000)


class ChangeRequestCaseBindingCreate(StrictModel):
    project_id: str = Field(min_length=1, max_length=160)
    analysis_case_id: str = Field(min_length=1, max_length=160)


class CopilotCodingTaskCreate(StrictModel):
    project_id: str = Field(min_length=1, max_length=160)
    edit_packet_id: str = Field(min_length=1, max_length=160)
    approval_grant_id: str = Field(min_length=1, max_length=160)
    workspace_root: str = Field(min_length=1, max_length=4000)
    task_summary: str = Field(min_length=1, max_length=10_000)


class BridgeTaskAccept(StrictModel):
    workspace_root: str = Field(min_length=1, max_length=4000)
    consumer_id: str = Field(min_length=1, max_length=200)
    accepted_by: str = Field(min_length=1, max_length=200)


class CopilotCodingTaskCancel(StrictModel):
    reason: str = Field(min_length=1, max_length=2_000)


class CopilotCodingTaskRetry(StrictModel):
    edit_packet_id: str = Field(min_length=1, max_length=160)
    approval_grant_id: str = Field(min_length=1, max_length=160)
    workspace_root: str = Field(min_length=1, max_length=4000)


class BridgeTaskCancel(StrictModel):
    workspace_root: str = Field(min_length=1, max_length=4000)
    consumer_id: str = Field(min_length=1, max_length=200)
    cancelled_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)


class TestDataRecoveryCreate(StrictModel):
    reason: str = Field(min_length=1, max_length=2_000)
    stale_before: datetime

    @field_validator("stale_before")
    @classmethod
    def require_recovery_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("stale_before must include a timezone")
        return value


class TestCaseModificationCreate(StrictModel):
    instruction: str = Field(min_length=1, max_length=20_000)


class TestCaseModificationConfirm(StrictModel):
    selections: dict[str, str] = Field(default_factory=dict, max_length=100)

    @field_validator("selections")
    @classmethod
    def require_non_blank_selections(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not selected.strip() for key, selected in value.items()):
            raise ValueError("ambiguity and option identities must not be blank")
        return value


class TestCaseExecutionScopeConfirm(StrictModel):
    approval_grant_id: str = Field(min_length=1, max_length=160)
    target_scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class UiKnowledgeReviewCreate(StrictModel):
    result_snapshot_version: str = Field(min_length=1, max_length=160)
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=10_000)
    activate: bool = False

    @model_validator(mode="after")
    def validate_activation(self) -> UiKnowledgeReviewCreate:
        if self.activate and self.decision != "approved":
            raise ValueError("only approved UI Knowledge can be activated")
        return self


class ProfileActivationCreate(StrictModel):
    binding_key: str = Field(min_length=1, max_length=500)
    profile_version_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=10_000)


class ProfileRebuildCreate(StrictModel):
    drift_event_id: str = Field(min_length=1, max_length=160)
    artifact_type: str = Field(min_length=1, max_length=160)
    artifact_id: str = Field(min_length=1, max_length=500)


class ProfileRebuildRequeue(StrictModel):
    reason: str = Field(min_length=1, max_length=10_000)


class ImpactConfirmationCreate(StrictModel):
    change_request_id: str = Field(min_length=1, max_length=160)
    report_id: str = Field(min_length=1, max_length=160)
    approved_item_ids: list[str] = Field(default_factory=list, max_length=500)
    rejected_item_ids: list[str] = Field(default_factory=list, max_length=500)
    note: str = Field(default="", max_length=10_000)

    @model_validator(mode="after")
    def validate_decisions(self) -> ImpactConfirmationCreate:
        approved = set(self.approved_item_ids)
        rejected = set(self.rejected_item_ids)
        if len(approved) != len(self.approved_item_ids):
            raise ValueError("approved_item_ids must be unique")
        if len(rejected) != len(self.rejected_item_ids):
            raise ValueError("rejected_item_ids must be unique")
        if approved & rejected:
            raise ValueError("an impact item cannot be approved and rejected")
        return self


class ApprovalGrantCreate(StrictModel):
    change_request_id: str = Field(min_length=1, max_length=160)
    edit_packet_id: str = Field(min_length=1, max_length=160)
    expires_at: datetime
    command_profile_binding_key: str = Field(min_length=1, max_length=500)
    test_command_refs: list[str] = Field(min_length=1, max_length=100)

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value
