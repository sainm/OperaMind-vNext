from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from psycopg import Cursor

from operamind.infrastructure.postgres import (
    ImpactReportState,
    ImpactRepository,
    PersistenceConflictError,
)
from operamind.infrastructure.postgres.impact_repository import _impact_item_rows

CONFIRMED_AT = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _report_artifact() -> dict[str, Any]:
    return {
        "artifact_type": "ImpactReport",
        "schema_version": "v1",
        "impact_report_id": "impact-report-001",
        "analysis_case_id": "case-001",
        "project_id": "project-001",
        "document_snapshot_id": "snapshot-001",
        "context_package_id": "context-001",
        "code_graph_snapshot_id": "graph-001",
        "repository_revision": "commit-001",
        "analysis_policy_version": "scope-impact-v1",
        "status": "awaiting_confirmation",
        "summary": "Status filter default changed.",
        "items": [
            {
                "impact_item_id": "item-001",
                "structured_change_refs": ["change-001"],
                "target_path": "src/ExpenseService.java",
                "target_symbols": ["search(String)"],
                "impact_level": "high",
                "impact_score": 0.95,
                "recommended_action": "modify",
                "rationale": "Direct typed-anchor match.",
                "evidence_refs": ["fact-001"],
                "graph_path_refs": ["edge-001"],
                "test_file_refs": ["test/ExpenseServiceTest.java"],
                "requires_confirmation": True,
                "unknowns": [],
            }
        ],
        "ui_impact_status": "impacted",
        "required_ui_scenario_refs": ["scenario-001"],
        "blocking_unknowns": [],
    }


def _state(*, confirmed: bool = False) -> ImpactReportState:
    return ImpactReportState(
        impact_report_id="impact-report-001",
        project_id="project-001",
        analysis_case_id="case-001",
        repository_id="repository-001",
        repository_revision_id="revision-001",
        code_graph_snapshot_id="graph-001",
        status="confirmed" if confirmed else "awaiting_confirmation",
        item_count=1,
        blocking_unknowns=(),
        confirmed_at=CONFIRMED_AT if confirmed else None,
    )


def _header(*, confirmed: bool = False) -> tuple[object, ...]:
    return (
        "impact-report-001",
        "project-001",
        "case-001",
        "snapshot-001",
        "context-001",
        "graph-001",
        "repository-001",
        "revision-001",
        "commit-001",
        "scope-impact-v1",
        "confirmed" if confirmed else "awaiting_confirmation",
        "Status filter default changed.",
        [],
        CONFIRMED_AT if confirmed else None,
    )


def _repository(
    *,
    confirmation_artifact: dict[str, Any] | None = None,
    copilot_context: bool = False,
) -> ImpactRepository:
    repository = ImpactRepository(MagicMock(), MagicMock())
    artifacts = MagicMock()
    context_artifact = {
        "artifact_type": (
            "CopilotImpactContext" if copilot_context else "ContextPackage"
        ),
        "project_id": "project-001",
        "analysis_case_id": "case-001",
        (
            "target_document_snapshot_id"
            if copilot_context
            else "document_snapshot_id"
        ): "snapshot-001",
    }
    artifacts.get.side_effect = lambda artifact_id: (
        context_artifact
        if artifact_id == "context-001"
        else confirmation_artifact
        if artifact_id == "confirmation-001"
        else None
    )
    repository._artifacts = artifacts
    graphs = MagicMock()
    graphs.get.return_value = {
        "project_id": "project-001",
        "code_graph_snapshot_id": "graph-001",
        "repository_revision": "commit-001",
    }
    repository._graphs = graphs
    return repository


def _cursor(
    *,
    confirmed: bool = False,
    item_rows: list[tuple[object, ...]] | None = None,
    confirmation_row: tuple[object, ...] | None = None,
) -> Cursor[Any]:
    rows = item_rows
    if rows is None:
        rows = [cast(tuple[object, ...], row) for row in _impact_item_rows(_report_artifact())]
    cursor = MagicMock()
    cursor.fetchone.side_effect = [_header(confirmed=confirmed), confirmation_row]
    cursor.fetchall.return_value = rows
    return cast(Cursor[Any], cursor)


def test_impact_report_read_validates_full_item_ledger() -> None:
    _repository()._validate_report_integrity(
        _cursor(),
        state=_state(),
        artifact=_report_artifact(),
    )


def test_impact_report_read_accepts_bounded_copilot_impact_context() -> None:
    _repository(copilot_context=True)._validate_report_integrity(
        _cursor(),
        state=_state(),
        artifact=_report_artifact(),
    )


def test_impact_report_read_rejects_item_content_drift() -> None:
    rows = [list(row) for row in _impact_item_rows(_report_artifact())]
    rows[0][8] = "drifted rationale"
    with pytest.raises(PersistenceConflictError, match="Item ledger differs"):
        _repository()._validate_report_integrity(
            _cursor(item_rows=[tuple(rows[0])]),
            state=_state(),
            artifact=_report_artifact(),
        )


def test_confirmed_report_read_validates_confirmation_artifact_and_decisions() -> None:
    confirmation_artifact: dict[str, Any] = {
        "artifact_type": "ImpactConfirmation",
        "schema_version": "v1",
        "confirmation_id": "confirmation-001",
        "impact_report_id": "impact-report-001",
        "confirmed_by": "reviewer@example.invalid",
        "approved_item_ids": ["item-001"],
        "rejected_item_ids": [],
        "user_note": "Approved.",
        "confirmed_at": "2026-07-17T12:00:00Z",
    }
    row: tuple[object, ...] = (
        "confirmation-001",
        "project-001",
        "case-001",
        "impact-report-001",
        "reviewer@example.invalid",
        ["item-001"],
        [],
        "Approved.",
        CONFIRMED_AT,
    )
    _repository(confirmation_artifact=confirmation_artifact)._validate_report_integrity(
        _cursor(confirmed=True, confirmation_row=row),
        state=_state(confirmed=True),
        artifact=_report_artifact(),
    )

    drifted: tuple[object, ...] = (*row[:4], "different-reviewer", *row[5:])
    with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
        _repository(confirmation_artifact=confirmation_artifact)._validate_report_integrity(
            _cursor(confirmed=True, confirmation_row=drifted),
            state=_state(confirmed=True),
            artifact=_report_artifact(),
        )
