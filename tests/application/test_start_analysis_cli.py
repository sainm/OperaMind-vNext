from pathlib import Path

import pytest

from operamind.application import AnalysisStartRequest
from operamind.commands.start_analysis import build_parser, main


def test_start_analysis_parser_requires_explicit_git_identity() -> None:
    args = build_parser().parse_args(
        [
            "--project-id",
            "visiondemo",
            "--project-name",
            "VisionDemo",
            "--repository-id",
            "visiondemo-repository",
            "--repository-revision-id",
            "visiondemo-revision",
            "--analysis-case-id",
            "visiondemo-case",
            "--workspace-root",
            "/tmp/repository",
            "--base-revision",
            "abc123",
        ]
    )

    assert args.workspace_root == Path("/tmp/repository")
    assert args.base_revision == "abc123"


def test_start_analysis_request_rejects_blank_identity() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        AnalysisStartRequest(
            project_id="",
            project_name="VisionDemo",
            repository_id="repository",
            repository_revision_id="revision",
            analysis_case_id="case",
            workspace_root=Path("/tmp/repository"),
            expected_base_revision="abc123",
        )


def test_start_analysis_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    assert (
        main(
            [
                "--project-id",
                "visiondemo",
                "--project-name",
                "VisionDemo",
                "--repository-id",
                "repository",
                "--repository-revision-id",
                "revision",
                "--analysis-case-id",
                "case",
                "--workspace-root",
                "/tmp/repository",
                "--base-revision",
                "abc123",
            ]
        )
        == 2
    )
    assert "OPERAMIND_DATABASE_URL is required" in capsys.readouterr().err
