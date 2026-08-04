from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from operamind.application.approval_grant import (
    ApprovalGrantRequest,
    ApprovalGrantService,
)
from operamind.infrastructure.postgres.approval_grant_repository import (
    ApprovalGrantRecord,
    ApprovalGrantSource,
)

NOW = datetime.now(UTC)


class _Repository:
    def __init__(self, *, existing: dict[str, Any] | None = None) -> None:
        self.existing = existing
        self.issued: list[tuple[dict[str, Any], ApprovalGrantSource]] = []
        self.events: list[dict[str, object]] = []
        self.source = _source()

    def load_artifact(self, _grant_id: str) -> dict[str, Any] | None:
        return self.existing

    def load_source(self, **_values: object) -> ApprovalGrantSource:
        return self.source

    def load_replay_source(self, **_values: object) -> ApprovalGrantSource:
        return self.source

    def issue(
        self, *, artifact: dict[str, Any], source: ApprovalGrantSource
    ) -> ApprovalGrantRecord:
        self.issued.append((artifact, source))
        return ApprovalGrantRecord(True, str(artifact["approval_grant_id"]), "active_editing")

    def append_event(self, **values: object) -> bool:
        self.events.append(values)
        return True


def _source(**changes: object) -> ApprovalGrantSource:
    values: dict[str, object] = {
        "project_id": "project-1",
        "analysis_case_id": "case-1",
        "edit_packet_id": "packet-1",
        "impact_report_id": "impact-1",
        "confirmation_id": "confirmation-1",
        "repository_id": "repository-1",
        "base_repository_revision": "a" * 40,
        "editable_files": ("src/App.java",),
        "read_only_files": ("README.md",),
        "test_files": ("src/test/AppTest.java",),
        "forbidden_globs": ("**/.env",),
        "required_ui_scenario_refs": ("scenario-1",),
    }
    values.update(changes)
    return ApprovalGrantSource(**values)  # type: ignore[arg-type]


def _request(**changes: object) -> ApprovalGrantRequest:
    values: dict[str, object] = {
        "grant_id": "grant-1",
        "project_id": "project-1",
        "analysis_case_id": "case-1",
        "edit_packet_id": "packet-1",
        "approved_by": "owner",
        "expires_at": NOW + timedelta(hours=1),
        "command_profile_binding_key": "command-default",
        "allowed_test_command_refs": ("test",),
    }
    values.update(changes)
    return ApprovalGrantRequest(**values)  # type: ignore[arg-type]


def _service(
    *, repository: _Repository | None = None, binding: object = ...
) -> tuple[ApprovalGrantService, _Repository, list[dict[str, Any]]]:
    service = object.__new__(ApprovalGrantService)
    repo = repository or _Repository()
    validated: list[dict[str, Any]] = []
    service._repository = repo
    service._contracts = SimpleNamespace(validate_artifact=validated.append)
    active = SimpleNamespace(
        profile_version_id="command-profile-v1",
        profile={
            "profile_type": "CommandExecutionProfile",
            "templates": [{"command_ref": "test"}, {"command_ref": "build"}],
        },
    )
    resolved_binding = active if binding is ... else binding
    service._profiles = SimpleNamespace(get_active=lambda **_values: resolved_binding)
    return service, repo, validated


def test_approval_request_rejects_invalid_identity_expiry_and_command_refs() -> None:
    for changes, message in (
        ({"grant_id": " "}, "must not be blank"),
        ({"expires_at": NOW.replace(tzinfo=None)}, "timezone"),
        ({"expires_at": NOW - timedelta(seconds=1)}, "future"),
        ({"allowed_test_command_refs": ("test", "test")}, "unique"),
        ({"allowed_test_command_refs": (" ",)}, "unique"),
    ):
        with pytest.raises(ValueError, match=message):
            _request(**changes)


def test_issue_derives_bounded_actions_and_persists_validated_artifact() -> None:
    service, repository, validated = _service()

    result = service.issue(_request())

    assert result.record.state == "active_editing"
    assert result.artifact["allowed_actions"] == [
        "read",
        "modify",
        "record_result",
        "add_test",
        "run_test",
        "execute_ui",
        "record_evidence",
    ]
    assert validated == [result.artifact]
    assert repository.issued == [(result.artifact, repository.source)]


def test_issue_supports_verification_only_scope_without_modify_authority() -> None:
    service, repository, _validated = _service()
    repository.source = _source(
        editable_files=(), test_files=("src/test/AppTest.java",), required_ui_scenario_refs=()
    )

    artifact = service.issue(_request()).artifact

    assert artifact["allowed_actions"] == ["read", "record_result", "run_test"]


def test_issue_rejects_missing_invalid_or_incomplete_command_profile() -> None:
    for binding, message in (
        (None, "active Command Execution Profile"),
        (SimpleNamespace(profile={"profile_type": "CodeFrameworkProfile"}), "active Command"),
        (
            SimpleNamespace(
                profile_version_id="profile-1",
                profile={"profile_type": "CommandExecutionProfile", "templates": "invalid"},
            ),
            "lost its templates",
        ),
    ):
        service, _repository, _validated = _service(binding=binding)
        with pytest.raises((ValueError, RuntimeError), match=message):
            service.issue(_request())

    service, _repository, _validated = _service()
    with pytest.raises(ValueError, match="unknown command templates"):
        service.issue(_request(allowed_test_command_refs=("unknown",)))


def test_issue_replays_only_an_identical_immutable_artifact() -> None:
    original_service, _repository, _validated = _service()
    original = original_service.issue(_request()).artifact
    replay_repository = _Repository(existing=original)
    service, repository, validated = _service(repository=replay_repository, binding=None)

    replayed = service.issue(_request())

    assert replayed.artifact is original
    assert validated == []
    assert repository.issued[0][0] is original

    with pytest.raises(ValueError, match="differs from immutable Artifact"):
        service.issue(_request(approved_by="different-owner"))


def test_revoke_forwards_the_bounded_lifecycle_event() -> None:
    service, repository, _validated = _service()

    assert service.revoke(
        event_id="event-1",
        grant_id="grant-1",
        project_id="project-1",
        revoked_by="owner",
        reason="scope changed",
    )
    assert repository.events == [
        {
            "event_id": "event-1",
            "grant_id": "grant-1",
            "project_id": "project-1",
            "event_type": "revoked",
            "actor": "owner",
            "reason": "scope changed",
        }
    ]
