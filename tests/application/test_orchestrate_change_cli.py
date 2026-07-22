from operamind.commands.orchestrate_change import build_parser, main


def test_orchestration_cli_requires_canonical_identity() -> None:
    args = build_parser().parse_args(
        ["--change-request-id", "change-1", "--actor", "conversation:user"]
    )

    assert args.change_request_id == "change-1"
    assert args.actor == "conversation:user"


def test_orchestration_cli_requires_database_url(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    assert main(["--change-request-id", "change-1", "--actor", "user"]) == 2
    assert "OPERAMIND_DATABASE_URL is required" in capsys.readouterr().err
