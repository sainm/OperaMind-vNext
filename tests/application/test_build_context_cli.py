import pytest

from operamind.commands.build_context import main


def test_build_context_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)
    exit_code = main(
        [
            "--context-package-id",
            "context-package",
            "--project-id",
            "project",
            "--analysis-case-id",
            "case",
            "--ingestion-batch-id",
            "ingestion",
            "--ingestion-result-event-id",
            "readiness-event",
            "--target-snapshot-id",
            "snapshot",
            "--change-id",
            "change",
            "--embedding-profile-version-id",
            "embedding-profile",
            "--token-budget",
            "4000",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
