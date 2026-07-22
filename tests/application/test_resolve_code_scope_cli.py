import pytest

from operamind.commands.resolve_code_scope import main


def test_resolve_code_scope_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    exit_code = main(
        [
            "--anchors",
            "anchors.json",
            "--project-id",
            "project-1",
            "--analysis-case-id",
            "case-1",
            "--context-package-id",
            "context-1",
            "--structured-change-id",
            "change-1",
            "--code-graph-snapshot-id",
            "graph-1",
            "--repository-revision-id",
            "revision-1",
            "--profile-binding-key",
            "code-framework:repository-1",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
