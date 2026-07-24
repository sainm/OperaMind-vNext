import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operamind.commands import run_golden_rag


def _arguments() -> list[str]:
    return [
        "--case-id",
        "visiondemo-expense-status-filter-golden",
        "--report-id",
        "golden-report-001",
        "--project-id",
        "visiondemo",
        "--document-snapshot-id",
        "snapshot-001",
        "--embedding-profile-version-id",
        "embedding-001",
        "--search-index-build-id",
        "index-001",
        "--created-by",
        "quality-operator",
    ]


def _auto_scope_arguments() -> list[str]:
    return [
        "--case-id",
        "visiondemo-expense-status-filter-golden",
        "--report-id",
        "golden-report-001",
        "--project-id",
        "visiondemo",
        "--created-by",
        "quality-operator",
    ]


def test_live_golden_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    assert run_golden_rag.main(_arguments()) == 2

    assert "OPERAMIND_DATABASE_URL is required" in capsys.readouterr().err


@pytest.mark.parametrize(("status", "expected_code"), [("passed", 0), ("failed", 1)])
def test_live_golden_cli_replays_report_and_returns_gate_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    expected_code: int,
) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql://example.invalid/test")
    connection = MagicMock()
    connect_context = MagicMock()
    connect_context.__enter__.return_value = connection
    monkeypatch.setattr(run_golden_rag.psycopg, "connect", MagicMock(return_value=connect_context))
    artifact_repository = MagicMock()
    artifact_repository.get.return_value = {"artifact_type": "GoldenRagQualityReport"}
    monkeypatch.setattr(
        run_golden_rag,
        "ArtifactRepository",
        MagicMock(return_value=artifact_repository),
    )
    service = MagicMock()
    service.run.return_value = SimpleNamespace(
        created=False,
        state=SimpleNamespace(status=status),
        artifact={"artifact_type": "GoldenRagQualityReport", "status": status},
    )
    monkeypatch.setattr(
        run_golden_rag,
        "GoldenRagQualityService",
        MagicMock(return_value=service),
    )

    assert run_golden_rag.main(_arguments()) == expected_code

    output = json.loads(capsys.readouterr().out)
    assert output["created"] is False
    assert output["status"] == status
    request = service.run.call_args.args[0]
    assert request.dataset_id == "operamind-vnext-golden"
    assert request.dataset_version == "1.0.0"
    assert request.project_id == "visiondemo"
    assert isinstance(service.run.call_args.kwargs["provider"], run_golden_rag._ReplayOnlyProvider)


def test_live_golden_cli_replay_recovers_scope_from_immutable_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql://example.invalid/test")
    connection = MagicMock()
    connect_context = MagicMock()
    connect_context.__enter__.return_value = connection
    monkeypatch.setattr(run_golden_rag.psycopg, "connect", MagicMock(return_value=connect_context))
    artifact_repository = MagicMock()
    artifact_repository.get.return_value = {
        "artifact_type": "GoldenRagQualityReport",
        "document_snapshot_id": "snapshot-from-report",
        "embedding_profile_version_id": "profile-from-report",
        "search_index_build_id": "build-from-report",
    }
    monkeypatch.setattr(
        run_golden_rag,
        "ArtifactRepository",
        MagicMock(return_value=artifact_repository),
    )
    service = MagicMock()
    service.run.return_value = SimpleNamespace(
        created=False,
        state=SimpleNamespace(status="passed"),
        artifact={"artifact_type": "GoldenRagQualityReport", "status": "passed"},
    )
    monkeypatch.setattr(
        run_golden_rag,
        "GoldenRagQualityService",
        MagicMock(return_value=service),
    )

    assert run_golden_rag.main(_auto_scope_arguments()) == 0

    request = service.run.call_args.args[0]
    assert (
        request.document_snapshot_id,
        request.embedding_profile_version_id,
        request.search_index_build_id,
    ) == ("snapshot-from-report", "profile-from-report", "build-from-report")


def test_live_golden_cli_writes_persisted_report_inside_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql://example.invalid/test")
    connection = MagicMock()
    connect_context = MagicMock()
    connect_context.__enter__.return_value = connection
    monkeypatch.setattr(run_golden_rag.psycopg, "connect", MagicMock(return_value=connect_context))
    artifact_repository = MagicMock()
    artifact_repository.get.return_value = {"artifact_type": "GoldenRagQualityReport"}
    monkeypatch.setattr(
        run_golden_rag,
        "ArtifactRepository",
        MagicMock(return_value=artifact_repository),
    )
    service = MagicMock()
    service.run.return_value = SimpleNamespace(
        created=False,
        state=SimpleNamespace(status="failed"),
        artifact={"artifact_type": "GoldenRagQualityReport", "status": "failed"},
    )
    monkeypatch.setattr(
        run_golden_rag,
        "GoldenRagQualityService",
        MagicMock(return_value=service),
    )
    output = tmp_path / "golden-report.json"

    assert run_golden_rag.main([*_arguments(), "--output", str(output)]) == 1

    assert capsys.readouterr().out == ""
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["report"]["artifact_type"] == "GoldenRagQualityReport"
