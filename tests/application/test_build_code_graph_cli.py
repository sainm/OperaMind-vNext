import pytest

from operamind.commands.build_code_graph import build_parser, main


def test_build_code_graph_cli_uses_incremental_by_default_and_accepts_full_scan() -> None:
    parser = build_parser()
    common = [
        "--profile",
        "code-framework-profile.json",
        "--code-graph-snapshot-id",
        "graph-1",
        "--project-id",
        "project-1",
        "--repository-id",
        "repository-1",
        "--repository-revision-id",
        "revision-1",
        "--workspace-root",
        "/workspace",
        "--scan-root",
        "src/main",
        "--profile-version-id",
        "profile-1",
        "--profile-activation-event-id",
        "activation-1",
        "--activated-by",
        "scanner",
        "--activation-reason",
        "scan code",
    ]

    assert parser.parse_args(common).full_scan is False
    assert parser.parse_args([*common, "--full-scan"]).full_scan is True


def test_build_code_graph_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    exit_code = main(
        [
            "--profile",
            "code-framework-profile.json",
            "--code-graph-snapshot-id",
            "graph-1",
            "--project-id",
            "project-1",
            "--repository-id",
            "repository-1",
            "--repository-revision-id",
            "revision-1",
            "--workspace-root",
            "/workspace",
            "--scan-root",
            "src/main",
            "--profile-version-id",
            "profile-1",
            "--profile-activation-event-id",
            "activation-1",
            "--activated-by",
            "scanner",
            "--activation-reason",
            "scan code",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
