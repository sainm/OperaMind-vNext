import pytest

from operamind.commands.ui_verification import build_parser, main


def test_ui_cli_exposes_browser_manifest_and_execution_subcommands() -> None:
    parser = build_parser()

    registration = parser.parse_args(
        ["register-browser-manifest", "--manifest", "browser-manifest.json"]
    )
    execution = parser.parse_args(
        [
            "execute-browser",
            "--project-id",
            "project-001",
            "--plan-id",
            "plan-001",
            "--manifest-id",
            "manifest-001",
            "--run-id",
            "run-001",
            "--verification-result-id",
            "result-001",
            "--approval-grant-id",
            "grant-001",
            "--evidence-root",
            "evidence",
        ]
    )
    recovery = parser.parse_args(
        [
            "recover-run",
            "--verification-result-id",
            "result-recovery-001",
            "--project-id",
            "project-001",
            "--run-id",
            "run-001",
            "--recovery-id",
            "result-recovery-001",
            "--actor",
            "operator@example.invalid",
            "--reason",
            "worker process was interrupted",
            "--stale-before",
            "2026-07-16T12:00:00Z",
        ]
    )
    knowledge = parser.parse_args(["register-ui-knowledge", "--snapshot", "ui-knowledge.json"])
    proposal = parser.parse_args(
        [
            "propose-ui-knowledge",
            "--project-id",
            "project-001",
            "--document-snapshot-id",
            "document-snapshot-001",
            "--environment-id",
            "staging",
            "--deployment-revision",
            "deployment-001",
            "--snapshot-id",
            "ui-knowledge-001",
            "--snapshot-version",
            "1.0.0",
        ]
    )
    preflight = parser.parse_args(
        [
            "preflight-browser",
            "--project-id",
            "project-001",
            "--plan-id",
            "plan-001",
            "--manifest-id",
            "manifest-001",
            "--attempt-id",
            "attempt-001",
        ]
    )
    observation = parser.parse_args(
        [
            "observe-ui-knowledge",
            "--project-id",
            "project-001",
            "--source-snapshot-id",
            "ui-knowledge-source",
            "--observation-run-id",
            "observation-run-001",
            "--result-snapshot-id",
            "ui-knowledge-observed",
            "--result-snapshot-version",
            "1.1.0-draft",
        ]
    )
    review = parser.parse_args(
        [
            "review-ui-knowledge",
            "--project-id",
            "project-001",
            "--source-snapshot-id",
            "ui-knowledge-observed",
            "--review-event-id",
            "ui-knowledge-review-001",
            "--result-snapshot-id",
            "ui-knowledge-approved",
            "--result-snapshot-version",
            "1.1.0",
            "--decision",
            "approved",
            "--reviewed-by",
            "qa@example.invalid",
            "--activate",
        ]
    )

    assert registration.operation == "register-browser-manifest"
    assert execution.operation == "execute-browser"
    assert execution.approval_grant_id == "grant-001"
    assert execution.timeout_ms == 10_000
    assert execution.navigation_timeout_ms == 20_000
    assert recovery.operation == "recover-run"
    assert recovery.stale_before.isoformat() == "2026-07-16T12:00:00+00:00"
    assert knowledge.operation == "register-ui-knowledge"
    assert proposal.operation == "propose-ui-knowledge"
    assert preflight.operation == "preflight-browser"
    assert preflight.timeout_ms == 5_000
    assert observation.operation == "observe-ui-knowledge"
    assert observation.browser_name == "chromium"
    assert observation.browser_channel == "msedge"
    assert review.operation == "review-ui-knowledge"
    assert review.decision == "approved"
    assert review.activate


def test_ui_browser_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    exit_code = main(["register-browser-manifest", "--manifest", "manifest.json"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
