import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

import operamind.infrastructure.postgres.approval_grant_repository as grant_module
from operamind.infrastructure.postgres import (
    ApprovalGrantRepository,
    ConfirmedImpactItem,
    EditPacketRecord,
    EditPacketRepository,
    EditPacketSource,
    ImpactReportState,
    PersistenceConflictError,
)
from operamind.infrastructure.postgres.approval_grant_repository import (
    ApprovalGrantAuthorization,
    _issue_source_identity,
    _packet_source,
    _state,
    _timestamp,
    _validate_command_profile_binding,
    _validate_grant_semantics,
)


def _packet_artifact() -> dict[str, Any]:
    return {
        "artifact_type": "CopilotEditPacket",
        "schema_version": "v1",
        "edit_packet_id": "packet-001",
        "impact_report_id": "report-001",
        "confirmation_id": "confirmation-001",
        "project_id": "project-001",
        "repository_id": "repository-001",
        "base_repository_revision": "commit-001",
        "editable_files": ["src/ExpenseService.java"],
        "read_only_files": [],
        "test_files": ["test/ExpenseServiceTest.java"],
        "forbidden_globs": ["**/.env"],
        "allowed_items": [
            {
                "impact_item_id": "item-001",
                "target_path": "src/ExpenseService.java",
                "target_symbols": ["search(String)"],
                "allowed_actions": ["modify"],
                "business_summary": "Change the default expense status.",
                "implementation_constraints": [],
            }
        ],
        "required_ui_scenario_refs": ["scenario-001"],
        "out_of_scope_policy": "stop_and_reanalyze",
        "must_not_fetch_context_package": True,
    }


def _impact_state(*, status: str = "confirmed") -> ImpactReportState:
    return ImpactReportState(
        impact_report_id="report-001",
        project_id="project-001",
        analysis_case_id="case-001",
        repository_id="repository-001",
        repository_revision_id="revision-001",
        code_graph_snapshot_id="graph-001",
        status=status,
        item_count=1,
        blocking_unknowns=(),
        confirmed_at=None,
    )


def _impact_source(*, approved: bool = True) -> EditPacketSource:
    return EditPacketSource(
        project_id="project-001",
        analysis_case_id="case-001",
        impact_report_id="report-001",
        confirmation_id="confirmation-001",
        repository_id="repository-001",
        repository_revision_id="revision-001",
        commit_sha="commit-001",
        remote_url="https://example.invalid/repository.git",
        workspace_root="/workspace/repository",
        business_summary="Change the default expense status.",
        required_ui_scenario_refs=("scenario-001",),
        approved_item_ids=("item-001",) if approved else (),
        items=(
            ConfirmedImpactItem(
                impact_item_id="item-001",
                target_path="src/ExpenseService.java",
                target_symbols=("search(String)",),
                recommended_action="modify",
                test_file_refs=("test/ExpenseServiceTest.java",),
            ),
        ),
    )


def _packet_row() -> tuple[object, ...]:
    artifact = _packet_artifact()
    return (
        "packet-001",
        "project-001",
        "case-001",
        "report-001",
        "confirmation-001",
        "repository-001",
        "revision-001",
        "commit-001",
        "active",
        artifact["editable_files"],
        artifact["read_only_files"],
        artifact["test_files"],
        artifact["forbidden_globs"],
        artifact["allowed_items"],
        artifact["required_ui_scenario_refs"],
        "commit-001",
        "report-001",
    )


def _packet_repository(
    *, report_status: str = "confirmed", approved: bool = True
) -> EditPacketRepository:
    repository = EditPacketRepository(MagicMock(), MagicMock())
    repository._impacts = MagicMock()
    repository._impacts.get_state.return_value = _impact_state(status=report_status)
    repository._load_integrity_source = MagicMock(return_value=_impact_source(approved=approved))
    return repository


def _packet_record(
    *, status: str = "active", impact_report_status: str = "confirmed"
) -> EditPacketRecord:
    return EditPacketRecord(
        artifact=_packet_artifact(),
        project_id="project-001",
        analysis_case_id="case-001",
        repository_revision_id="revision-001",
        status=status,
        impact_report_status=impact_report_status,
    )


def _grant_artifact() -> dict[str, Any]:
    source = _packet_source(_packet_record())
    return {
        "artifact_type": "ApprovalGrant",
        "schema_version": "v1",
        "approval_grant_id": "grant-001",
        "change_session_id": source.analysis_case_id,
        "analysis_case_id": source.analysis_case_id,
        "edit_packet_id": source.edit_packet_id,
        "impact_report_id": source.impact_report_id,
        "confirmation_id": source.confirmation_id,
        "project_id": source.project_id,
        "repository_id": source.repository_id,
        "base_repository_revision": source.base_repository_revision,
        "editable_files": list(source.editable_files),
        "read_only_files": list(source.read_only_files),
        "test_files": list(source.test_files),
        "allowed_actions": [
            "read",
            "modify",
            "record_result",
            "add_test",
            "run_test",
            "execute_ui",
            "record_evidence",
        ],
        "command_profile_version_id": "command-profile-v1",
        "allowed_test_command_refs": ["test"],
        "allowed_ui_scenarios": list(source.required_ui_scenario_refs),
        "forbidden_globs": list(source.forbidden_globs),
        "approved_by": "owner",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "out_of_scope_policy": "collect_and_request_once",
    }


def test_packet_integrity_accepts_exact_artifact_ledger_and_confirmation_scope() -> None:
    _packet_repository()._validate_packet_integrity(
        artifact=_packet_artifact(),
        row=_packet_row(),
    )


@pytest.mark.parametrize("index", [4, 9, 13, 15, 16])
def test_packet_integrity_rejects_normalized_scope_drift(index: int) -> None:
    row = list(_packet_row())
    row[index] = "drifted" if index not in {9, 13} else []

    with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
        _packet_repository()._validate_packet_integrity(
            artifact=_packet_artifact(),
            row=tuple(row),
        )


def test_packet_integrity_rejects_unapproved_derived_code_scope() -> None:
    with pytest.raises(PersistenceConflictError, match="derived scope differs"):
        _packet_repository(approved=False)._validate_packet_integrity(
            artifact=_packet_artifact(),
            row=_packet_row(),
        )


def test_active_packet_keeps_auditable_integrity_after_report_is_superseded() -> None:
    assert (
        _packet_repository(report_status="superseded")._validate_packet_integrity(
            artifact=_packet_artifact(), row=_packet_row()
        )
        == "superseded"
    )


def test_grant_source_requires_active_packet_and_editing_case() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = ("editing",)
    repository = ApprovalGrantRepository(connection, MagicMock())
    repository._packets.get = MagicMock(return_value=_packet_record())

    source = repository.load_source(
        project_id="project-001",
        analysis_case_id="case-001",
        edit_packet_id="packet-001",
    )

    assert source.edit_packet_id == "packet-001"
    assert source.editable_files == ("src/ExpenseService.java",)


def test_grant_issue_lock_identity_matches_query_column_order() -> None:
    source = _packet_source(_packet_record())

    assert _issue_source_identity(source) == (
        "active",
        "editing",
        "confirmed",
        True,
        "complete",
        "case-001",
        "report-001",
        "confirmation-001",
        "repository-001",
        "commit-001",
        ["src/ExpenseService.java"],
        [],
        ["test/ExpenseServiceTest.java"],
        ["**/.env"],
        ["scenario-001"],
        "repository-001",
        "commit-001",
        True,
    )


def test_grant_source_rejects_superseded_packet() -> None:
    repository = ApprovalGrantRepository(MagicMock(), MagicMock())
    repository._packets.get = MagicMock(return_value=_packet_record(status="superseded"))

    with pytest.raises(ValueError, match="active Edit Packet"):
        repository.load_source(
            project_id="project-001",
            analysis_case_id="case-001",
            edit_packet_id="packet-001",
        )


def test_grant_source_rejects_active_packet_with_superseded_report() -> None:
    repository = ApprovalGrantRepository(MagicMock(), MagicMock())
    repository._packets.get = MagicMock(
        return_value=_packet_record(impact_report_status="superseded")
    )

    with pytest.raises(ValueError, match="confirmed Impact Report"):
        repository.load_source(
            project_id="project-001",
            analysis_case_id="case-001",
            edit_packet_id="packet-001",
        )


def test_grant_replay_source_allows_integrity_checked_superseded_packet() -> None:
    repository = ApprovalGrantRepository(MagicMock(), MagicMock())
    repository._packets.get = MagicMock(return_value=_packet_record(status="superseded"))

    source = repository.load_replay_source(
        project_id="project-001",
        analysis_case_id="case-001",
        edit_packet_id="packet-001",
    )

    assert source.edit_packet_id == "packet-001"


def test_grant_source_rejects_case_state_drift() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = ("awaiting_confirmation",)
    repository = ApprovalGrantRepository(connection, MagicMock())
    repository._packets.get = MagicMock(return_value=_packet_record())

    with pytest.raises(ValueError, match="editing state"):
        repository.load_source(
            project_id="project-001",
            analysis_case_id="case-001",
            edit_packet_id="packet-001",
        )


def test_grant_source_propagates_packet_integrity_failure() -> None:
    repository = ApprovalGrantRepository(MagicMock(), MagicMock())
    repository._packets.get = MagicMock(
        side_effect=PersistenceConflictError("Edit Packet normalized identity differs")
    )

    with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
        repository.load_source(
            project_id="project-001",
            analysis_case_id="case-001",
            edit_packet_id="packet-001",
        )


def test_grant_issue_persists_an_identity_checked_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _grant_artifact()
    source = _packet_source(_packet_record())
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = _issue_source_identity(source)
    contracts = MagicMock()
    repository = ApprovalGrantRepository(connection, contracts)
    repository._packets = MagicMock()
    repository._packets.get.return_value = _packet_record()
    repository._artifacts = MagicMock()
    repository._artifacts.get.return_value = None
    monkeypatch.setattr(
        grant_module,
        "_validate_command_profile_binding",
        lambda *_args, **_kwargs: None,
    )

    record = repository.issue(artifact=artifact, source=source)

    assert record.created is True
    assert record.grant_id == "grant-001"
    assert record.state == "active_editing"
    contracts.validate_artifact.assert_called_once_with(artifact)
    repository._artifacts.store.assert_called_once()
    assert "INSERT INTO approval_grants" in cursor.execute.call_args_list[-1].args[0]


def test_grant_issue_rejects_scope_drift_and_conflicting_replay() -> None:
    artifact = _grant_artifact()
    source = _packet_source(_packet_record())
    repository = ApprovalGrantRepository(MagicMock(), MagicMock())

    with pytest.raises(ValueError, match="outside the active Edit Packet scope"):
        repository.issue(artifact={**artifact, "repository_id": "other"}, source=source)

    repository._packets = MagicMock()
    repository._packets.get.return_value = _packet_record()
    repository._artifacts = MagicMock()
    repository._artifacts.get.return_value = {**artifact, "approved_by": "other"}
    with pytest.raises(PersistenceConflictError, match="different content"):
        repository.issue(artifact=artifact, source=source)


def test_grant_inspection_rehydrates_artifact_events_and_profile() -> None:
    artifact = _grant_artifact()
    source = _packet_source(_packet_record())
    canonical = json.dumps(
        artifact, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    payload_digest = hashlib.sha256(canonical.encode()).hexdigest()
    expires_at = _timestamp(artifact["expires_at"])
    normalized = (
        artifact["project_id"],
        artifact["analysis_case_id"],
        artifact["edit_packet_id"],
        artifact["impact_report_id"],
        artifact["confirmation_id"],
        artifact["repository_id"],
        artifact["base_repository_revision"],
        artifact["editable_files"],
        artifact["read_only_files"],
        artifact["test_files"],
        artifact["allowed_actions"],
        artifact["command_profile_version_id"],
        artifact["allowed_test_command_refs"],
        artifact["allowed_ui_scenarios"],
        artifact["forbidden_globs"],
        artifact["approved_by"],
        expires_at,
        artifact["out_of_scope_policy"],
        payload_digest,
    )
    event_payload = {
        "event_id": "event-1",
        "grant_id": "grant-001",
        "project_id": "project-001",
        "event_type": "edit_completed",
        "actor": "worker",
        "reason": "code recorded",
    }
    event_digest = hashlib.sha256(
        json.dumps(
            event_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    profile = {
        "profile_type": "CommandExecutionProfile",
        "profile_id": "command-default",
        "profile_version": "1.0.0",
        "templates": [{"command_ref": "test"}],
    }
    profile_digest = hashlib.sha256(
        json.dumps(profile, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [
        normalized,
        (
            "CommandExecutionProfile",
            "command-default",
            "1.0.0",
            profile,
            profile_digest,
        ),
    ]
    cursor.fetchall.return_value = [
        (
            "event-1",
            "grant-001",
            "project-001",
            "edit_completed",
            "worker",
            "code recorded",
            event_digest,
        )
    ]
    repository = ApprovalGrantRepository(connection, MagicMock())
    repository._artifacts = MagicMock()
    repository._artifacts.get.return_value = artifact
    repository._packets = MagicMock()
    repository._packets.get.return_value = _packet_record()

    inspected = repository.inspect("grant-001")

    assert inspected == ApprovalGrantAuthorization(
        grant_id="grant-001",
        project_id=source.project_id,
        analysis_case_id=source.analysis_case_id,
        edit_packet_id=source.edit_packet_id,
        impact_report_id=source.impact_report_id,
        confirmation_id=source.confirmation_id,
        repository_id=source.repository_id,
        base_repository_revision=source.base_repository_revision,
        allowed_actions=tuple(artifact["allowed_actions"]),
        command_profile_version_id="command-profile-v1",
        allowed_test_command_refs=("test",),
        allowed_ui_scenarios=("scenario-001",),
        expires_at=expires_at,
        state="ui_pending",
    )


def test_authorization_lock_and_lifecycle_events_fail_closed() -> None:
    grant = ApprovalGrantAuthorization(
        grant_id="grant-001",
        project_id="project-001",
        analysis_case_id="case-001",
        edit_packet_id="packet-001",
        impact_report_id="report-001",
        confirmation_id="confirmation-001",
        repository_id="repository-001",
        base_repository_revision="commit-001",
        allowed_actions=("read", "record_result"),
        command_profile_version_id="command-v1",
        allowed_test_command_refs=(),
        allowed_ui_scenarios=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        state="active_editing",
    )
    repository = ApprovalGrantRepository(MagicMock(), MagicMock())
    repository.inspect = MagicMock(return_value=grant)
    repository.lock = MagicMock()
    repository._assert_edit_source_current = MagicMock()

    assert (
        repository.authorize_edit(
            grant_id="grant-001",
            project_id="project-001",
            analysis_case_id="case-001",
            edit_packet_id="packet-001",
            required_action="record_result",
            lock=True,
        )
        is grant
    )
    repository.lock.assert_called_once()
    repository._assert_edit_source_current.assert_called_once_with(grant, lock=True)

    with pytest.raises(ValueError, match="does not match"):
        repository.authorize_edit(
            grant_id="grant-001",
            project_id="other",
            analysis_case_id="case-001",
            edit_packet_id="packet-001",
        )

    for events, expected in (
        (("revoked",), "revoked"),
        (("completed",), "completed"),
        ((), "expired"),
        (("edit_completed",), "ui_pending"),
    ):
        instant = datetime.now(UTC)
        expiry = instant - timedelta(seconds=1) if not events else instant + timedelta(hours=1)
        assert _state(events=events, expires_at=expiry, at=instant) == expected


def test_grant_helper_validation_rejects_derived_and_profile_drift() -> None:
    artifact = _grant_artifact()
    source = _packet_source(_packet_record())
    with pytest.raises(ValueError, match="change session differs"):
        _validate_grant_semantics(
            artifact={**artifact, "change_session_id": "other"}, source=source
        )
    with pytest.raises(ValueError, match="actions are not derived"):
        _validate_grant_semantics(
            artifact={**artifact, "allowed_actions": ["read"]}, source=source
        )
    with pytest.raises(ValueError, match="without approved test files"):
        _validate_grant_semantics(
            artifact={
                **artifact,
                "allowed_actions": [
                    "read",
                    "modify",
                    "record_result",
                    "execute_ui",
                    "record_evidence",
                ],
            },
            source=replace(source, test_files=()),
        )

    cursor = MagicMock()
    cursor.fetchone.return_value = None
    with pytest.raises(ValueError, match="does not exist"):
        _validate_command_profile_binding(cursor, artifact=artifact)


def test_grant_repository_lifecycle_and_source_lock_are_consistent() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    repository = ApprovalGrantRepository(connection, MagicMock())
    repository._artifacts = MagicMock()
    artifact = _grant_artifact()
    repository._artifacts.get.return_value = artifact
    assert repository.load_artifact("grant-001") is artifact
    repository._artifacts.get.return_value = {"artifact_type": "TestPlan"}
    with pytest.raises(PersistenceConflictError, match="not an Approval Grant"):
        repository.load_artifact("grant-001")

    grant = ApprovalGrantAuthorization(
        grant_id="grant-001",
        project_id="project-001",
        analysis_case_id="case-001",
        edit_packet_id="packet-001",
        impact_report_id="report-001",
        confirmation_id="confirmation-001",
        repository_id="repository-001",
        base_repository_revision="commit-001",
        allowed_actions=("read", "record_result"),
        command_profile_version_id="command-v1",
        allowed_test_command_refs=(),
        allowed_ui_scenarios=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        state="active_editing",
    )
    cursor.fetchone.return_value = (
        "active",
        "confirmed",
        True,
        "complete",
        "editing",
        "commit-001",
        "commit-001",
        "report-001",
        "confirmation-001",
        "repository-001",
    )
    repository._assert_edit_source_current(grant, lock=False)

    cursor.fetchone.return_value = ("project-001",)
    repository.lock(grant_id="grant-001", project_id="project-001")
    with pytest.raises(ValueError, match="project does not match"):
        repository.lock(grant_id="grant-001", project_id="other-project")

    repository.lock = MagicMock()
    repository.inspect = MagicMock(return_value=grant)
    cursor.fetchone.return_value = None
    assert repository.append_event(
        event_id="event-001",
        grant_id="grant-001",
        project_id="project-001",
        event_type="edit_completed",
        actor="worker",
        reason="code result recorded",
    )
    assert "INSERT INTO approval_grant_events" in cursor.execute.call_args_list[-1].args[0]

    with pytest.raises(ValueError, match="event type is invalid"):
        repository.append_event(
            event_id="event-002",
            grant_id="grant-001",
            project_id="project-001",
            event_type="invalid",
            actor="worker",
            reason="invalid",
        )
