from operamind.commands.change_automation import build_parser, main


def test_change_automation_parser_supports_all_run_operations() -> None:
    start = build_parser().parse_args(
        [
            "start",
            "--change-request-id",
            "change-1",
            "--idempotency-key",
            "start-1",
            "--actor",
            "owner",
        ]
    )
    status = build_parser().parse_args(["status", "--change-request-id", "change-1"])
    binding = build_parser().parse_args(
        [
            "bind-case",
            "--change-request-id",
            "change-1",
            "--project-id",
            "demo",
            "--case-id",
            "case-1",
            "--idempotency-key",
            "binding-1",
            "--actor",
            "owner",
        ]
    )
    resume = build_parser().parse_args(
        [
            "resume",
            "--change-request-id",
            "change-1",
            "--run-id",
            "run-1",
            "--actor",
            "owner",
        ]
    )

    assert (start.command, status.command, resume.command) == ("start", "status", "resume")
    assert binding.command == "bind-case"


def test_change_automation_cli_requires_database_url(monkeypatch) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    assert main(["status", "--change-request-id", "change-1"]) == 2


def test_change_automation_cli_rejects_invalid_parallelism(monkeypatch, capsys) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.setenv("OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN", "0")

    assert main(["status", "--change-request-id", "change-1"]) == 2
    assert "between 1 and 100" in capsys.readouterr().err
