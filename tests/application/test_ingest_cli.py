import pytest

from operamind.commands.ingest import main


def test_ingest_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)
    exit_code = main(
        [
            "--profile",
            "profile.json",
            "--before",
            "before.xlsx",
            "--after",
            "after.xlsx",
            "--project-id",
            "project",
            "--analysis-case-id",
            "case",
            "--domain",
            "ui",
            "--fact-type",
            "screen_element",
            "--source-snapshot-id",
            "before-snapshot",
            "--target-snapshot-id",
            "after-snapshot",
            "--ingestion-batch-id",
            "ingestion",
            "--document-id",
            "document",
            "--logical-name",
            "screen.xlsx",
            "--source-document-version-id",
            "before-version",
            "--target-document-version-id",
            "after-version",
            "--source-ref",
            "immutable://before",
            "--target-ref",
            "immutable://after",
            "--profile-version-id",
            "profile-version",
            "--profile-binding-key",
            "document:screen_design",
            "--profile-activation-event-id",
            "activation",
            "--activated-by",
            "reviewer",
            "--activation-reason",
            "reviewed",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
