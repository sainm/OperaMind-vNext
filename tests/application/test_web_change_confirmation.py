from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from operamind.application import web_control_plane
from operamind.infrastructure.postgres.change_automation_repository import (
    ChangeAutomationRunRecord,
)


class _AutomationRepository:
    def __init__(self) -> None:
        self.persisted: dict[str, object] | None = None
        self.record_count = 0

    def rag_discovery(self, run_id: str) -> dict[str, object] | None:
        assert run_id == "run-1"
        return self.persisted

    def record_rag_discovery(
        self, *, run_id: str, discovery: dict[str, object]
    ) -> dict[str, object]:
        assert run_id == "run-1"
        self.record_count += 1
        self.persisted = discovery
        return {"discovery": discovery, "created": True}


class _DiscoveryService:
    call_count = 0

    def __init__(self, **_: object) -> None:
        pass

    def canonical_document_discovery_for_request(self, request_id: str) -> dict[str, object]:
        assert request_id == "change-1"
        type(self).call_count += 1
        return {
            "status": "ready",
            "mode": "requirement_hybrid_rag",
            "document_snapshot_id": "snapshot-1",
            "search_index_build_id": "search-index-1",
            "candidates": [{"document_id": "document-1", "summary": "対象設計書"}],
            "blocking_reason": None,
        }


def test_rag_confirmation_reuses_persisted_discovery_instead_of_embedding_again(
    monkeypatch: MonkeyPatch,
) -> None:
    repository = _AutomationRepository()
    service = object.__new__(web_control_plane.WebControlPlaneService)
    service._automation_runs = repository
    service._connection = object()
    service._root = Path("/repository")
    _DiscoveryService.call_count = 0
    monkeypatch.setattr(
        web_control_plane,
        "CopilotCodingTaskService",
        _DiscoveryService,
    )
    record = ChangeAutomationRunRecord(
        automation_run_id="run-1",
        change_request_id="change-1",
        project_id="project-1",
        status="waiting",
        current_stage="rag_document_confirmation",
        next_action="confirm_rag_documents",
        blocking_reason=None,
        created=False,
    )

    first = service._rag_discovery_for_run(record)
    second = service._rag_discovery_for_run(record)

    assert first == second
    assert first["document_snapshot_id"] == "snapshot-1"
    assert first["search_index_build_id"] == "search-index-1"
    assert _DiscoveryService.call_count == 1
    assert repository.record_count == 1


def test_execution_scope_does_not_reuse_superseded_edit_packet() -> None:
    assert (
        web_control_plane._reusable_edit_packet_id({"id": "packet-old", "status": "superseded"})
        is None
    )
    assert (
        web_control_plane._reusable_edit_packet_id({"id": "packet-current", "status": "active"})
        == "packet-current"
    )
