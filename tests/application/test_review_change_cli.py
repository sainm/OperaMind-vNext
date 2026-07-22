import pytest

from operamind.commands.review_change import main


def test_review_change_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)
    exit_code = main(
        [
            "--project-id",
            "project",
            "--change-id",
            "change",
            "--review-event-id",
            "review",
            "--decision",
            "accepted",
            "--reviewed-by",
            "reviewer",
            "--reason",
            "reviewed",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
