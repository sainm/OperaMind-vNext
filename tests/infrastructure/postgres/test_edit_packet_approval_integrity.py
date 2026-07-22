from typing import Any
from unittest.mock import MagicMock

import pytest

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
    _issue_source_identity,
    _packet_source,
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
