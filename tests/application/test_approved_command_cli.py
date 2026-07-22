from pathlib import Path

import pytest

from operamind.application import ApprovedCommandRequest
from operamind.commands.run_approved_command import build_parser, main


def test_approved_command_cli_requires_complete_scope() -> None:
    args = build_parser().parse_args(
        [
            "--command-execution-id",
            "execution-001",
            "--approval-grant-id",
            "grant-001",
            "--project-id",
            "project-001",
            "--analysis-case-id",
            "case-001",
            "--edit-packet-id",
            "packet-001",
            "--workspace-root",
            "/workspace/project",
            "--command-ref",
            "targeted-unit",
        ]
    )

    assert args.command_execution_id == "execution-001"
    assert args.command_ref == "targeted-unit"


def test_approved_command_request_rejects_blank_identity() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        ApprovedCommandRequest(
            command_execution_id="",
            approval_grant_id="grant-001",
            project_id="project-001",
            analysis_case_id="case-001",
            edit_packet_id="packet-001",
            workspace_root=Path("/workspace/project"),
            command_ref="targeted-unit",
        )


def test_approved_command_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    exit_code = main(
        [
            "--command-execution-id",
            "execution-001",
            "--approval-grant-id",
            "grant-001",
            "--project-id",
            "project-001",
            "--analysis-case-id",
            "case-001",
            "--edit-packet-id",
            "packet-001",
            "--workspace-root",
            "/workspace/project",
            "--command-ref",
            "targeted-unit",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
