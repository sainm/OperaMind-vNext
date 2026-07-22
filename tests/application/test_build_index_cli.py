import pytest

from operamind.commands.build_index import main


def test_build_index_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)
    exit_code = main(
        [
            "--profile",
            "embedding-profile.json",
            "--build-id",
            "build-1",
            "--project-id",
            "project-1",
            "--snapshot-id",
            "snapshot-1",
            "--profile-version-id",
            "profile-1",
            "--profile-activation-event-id",
            "activation-1",
            "--activated-by",
            "indexer",
            "--activation-reason",
            "build index",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
