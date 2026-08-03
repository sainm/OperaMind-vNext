"""Strict inputs for the six-stage Web and loopback VS Code Bridge."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(StrictModel):
    """Local paths required to initialize one project from the Web screen."""

    project_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=300)
    workspace_root: str = Field(min_length=1, max_length=4000)
    document_roots: list[str] = Field(min_length=1, max_length=20)
    test_base_url: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("document_roots")
    @classmethod
    def validate_document_roots(cls, value: list[str]) -> list[str]:
        if any(not root.strip() or len(root) > 4000 for root in value):
            raise ValueError("document_roots must contain non-blank paths up to 4000 characters")
        if len(value) != len(set(value)):
            raise ValueError("document_roots must be unique")
        return value

    @field_validator("test_base_url")
    @classmethod
    def validate_test_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("test_base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "test_base_url must not contain credentials, a query, or a fragment"
            )
        return value.rstrip("/")


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


class ChangeRequestCreate(StrictModel):
    """The only information a local user must enter to start the main flow."""

    change_request_id: str = Field(min_length=1, max_length=160)
    project_id: str = Field(min_length=1, max_length=160)
    requirement_text: str = Field(min_length=1, max_length=50_000)


class ChangeCheckpointDecisionInput(StrictModel):
    """One human decision accepted identically from Web or VS Code."""

    decision: Literal["confirmed", "rejected"]
    note: str | None = Field(default=None, min_length=1, max_length=2_000)


class BridgeChangeCheckpointDecision(ChangeCheckpointDecisionInput):
    actor: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=300)


class TestCaseRevisionProposalCreate(StrictModel):
    """A business-visible instruction for changing the generated Test Case."""

    instruction: str = Field(min_length=1, max_length=20_000)


class TestCaseRevisionConfirm(StrictModel):
    """Selections shown in one Test Case change proposal."""

    selections: dict[str, str] = Field(default_factory=dict)

    @field_validator("selections")
    @classmethod
    def validate_selections(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32:
            raise ValueError("selections must contain at most 32 choices")
        if any(
            not key.strip() or not selected.strip() or len(key) > 300 or len(selected) > 300
            for key, selected in value.items()
        ):
            raise ValueError("selection identifiers must be non-blank and at most 300 characters")
        return value


class BridgeTaskAccept(StrictModel):
    workspace_root: str = Field(min_length=1, max_length=4000)
    consumer_id: str = Field(min_length=1, max_length=200)
    accepted_by: str = Field(min_length=1, max_length=200)


class BridgeTaskCancel(StrictModel):
    workspace_root: str = Field(min_length=1, max_length=4000)
    consumer_id: str = Field(min_length=1, max_length=200)
    cancelled_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)
