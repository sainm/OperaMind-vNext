from datetime import UTC, datetime, timedelta

import pytest

from operamind.application import ApprovalGrantRequest
from operamind.commands.approval_grant import build_parser, main


def test_approval_grant_cli_exposes_issue_inspect_and_revoke() -> None:
    parser = build_parser()
    issue = parser.parse_args(
        [
            "issue",
            "--grant-id",
            "grant-001",
            "--project-id",
            "project-001",
            "--analysis-case-id",
            "case-001",
            "--edit-packet-id",
            "packet-001",
            "--approved-by",
            "reviewer@example.invalid",
            "--expires-at",
            "2026-07-31T12:00:00Z",
            "--command-profile-binding-key",
            "command-execution:repository-001",
            "--test-command-ref",
            "targeted-unit",
        ]
    )
    inspect = parser.parse_args(["inspect", "--grant-id", "grant-001"])
    revoke = parser.parse_args(
        [
            "revoke",
            "--event-id",
            "revocation-001",
            "--grant-id",
            "grant-001",
            "--project-id",
            "project-001",
            "--revoked-by",
            "reviewer@example.invalid",
            "--reason",
            "Scope changed",
        ]
    )

    assert issue.operation == "issue"
    assert issue.test_command_ref == ["targeted-unit"]
    assert inspect.operation == "inspect"
    assert revoke.operation == "revoke"


def test_approval_grant_request_rejects_expired_timestamp() -> None:
    with pytest.raises(ValueError, match="future"):
        ApprovalGrantRequest(
            grant_id="grant-001",
            project_id="project-001",
            analysis_case_id="case-001",
            edit_packet_id="packet-001",
            approved_by="reviewer@example.invalid",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            command_profile_binding_key="command-execution:repository-001",
        )


def test_approval_grant_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    exit_code = main(["inspect", "--grant-id", "grant-001"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
