from datetime import UTC, datetime

import pytest

from operamind.application import CommandExecutionRecoveryRequest
from operamind.commands.recover_command import build_parser, main
from operamind.infrastructure.postgres import CommandExecutionResultWrite
from operamind.infrastructure.postgres.command_execution_repository import _result_digest


def test_recover_command_cli_parses_audited_scope_and_boundary() -> None:
    args = build_parser().parse_args(
        [
            "--recovery-id",
            "command-recovery-1",
            "--command-execution-id",
            "command-1",
            "--project-id",
            "project-1",
            "--actor",
            "operator@example.invalid",
            "--reason",
            "worker interrupted",
            "--stale-before",
            "2026-07-16T12:00:00Z",
        ]
    )

    assert args.command_execution_id == "command-1"
    assert args.stale_before.tzinfo is UTC


def test_command_recovery_request_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone"):
        CommandExecutionRecoveryRequest(
            recovery_id="command-recovery-1",
            command_execution_id="command-1",
            project_id="project-1",
            actor="operator@example.invalid",
            reason="worker interrupted",
            stale_before=datetime(2026, 7, 16, 12, 0),
        )


def test_interrupted_command_result_has_digestable_complete_recovery_audit() -> None:
    boundary = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    write = CommandExecutionResultWrite(
        status="interrupted",
        exit_code=None,
        executable_path="<interrupted-before-result>",
        working_directory="/workspace/project",
        stdout_digest="0" * 64,
        stderr_digest="0" * 64,
        stdout_bytes=0,
        stderr_bytes=0,
        output_truncated=False,
        started_at=boundary,
        completed_at=boundary,
        recovery_id="command-recovery-1",
        recovery_actor="operator@example.invalid",
        recovery_reason="worker interrupted",
        recovery_stale_before=boundary,
    )

    assert len(_result_digest(write)) == 64
    with pytest.raises(ValueError, match="complete recovery audit"):
        CommandExecutionResultWrite(
            status="interrupted",
            exit_code=None,
            executable_path="<interrupted-before-result>",
            working_directory="/workspace/project",
            stdout_digest="0" * 64,
            stderr_digest="0" * 64,
            stdout_bytes=0,
            stderr_bytes=0,
            output_truncated=False,
            started_at=boundary,
            completed_at=boundary,
        )


def test_recover_command_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    exit_code = main(
        [
            "--recovery-id",
            "command-recovery-1",
            "--command-execution-id",
            "command-1",
            "--project-id",
            "project-1",
            "--actor",
            "operator@example.invalid",
            "--reason",
            "worker interrupted",
            "--stale-before",
            "2026-07-16T12:00:00Z",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
