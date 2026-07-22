import pytest

from operamind.commands.finalize_rag import main


def test_finalize_rag_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)
    exit_code = main(
        [
            "--event-id",
            "readiness-event",
            "--project-id",
            "project",
            "--ingestion-batch-id",
            "ingestion",
            "--analysis-case-id",
            "case",
            "--expected-previous-event-id",
            "previous-event",
            "--search-index-build-id",
            "search-build",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
