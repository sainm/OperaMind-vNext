import pytest

from operamind.commands.build_relations import main


def test_build_relations_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)
    exit_code = main(
        [
            "--profile",
            "relation-profile.json",
            "--build-id",
            "relation-build",
            "--project-id",
            "project",
            "--snapshot-id",
            "snapshot",
            "--profile-version-id",
            "relation-profile-version",
            "--profile-activation-event-id",
            "activation",
            "--activated-by",
            "reviewer",
            "--activation-reason",
            "reviewed relation rules",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
