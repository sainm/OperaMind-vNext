from operamind.commands.orchestration_tasks import build_parser, main
from operamind.commands.orchestration_worker import build_parser as build_worker_parser


def test_parser_supports_agent_neutral_task_operations() -> None:
    parser = build_parser()

    claim = parser.parse_args(
        [
            "claim",
            "--executor-kind",
            "subagent",
            "--executor-id",
            "worker-1",
            "--task-id",
            "task-2",
            "--capability",
            "document_review",
        ]
    )
    result = parser.parse_args(
        [
            "result",
            "--task-id",
            "task-1",
            "--executor-id",
            "worker-1",
            "--lease-token",
            "x" * 32,
            "--outcome",
            "completed",
            "--summary",
            "done",
            "--artifact-ref",
            "review-1",
            "--evidence-json",
            '{"reviewed":true}',
        ]
    )
    requeue = parser.parse_args(
        [
            "requeue",
            "--task-id",
            "task-1",
            "--actor",
            "operator-1",
            "--reason",
            "blocker resolved",
        ]
    )

    assert claim.executor_kind == "subagent"
    assert claim.task_id == "task-2"
    assert claim.capability == ["document_review"]
    assert result.outcome == "completed"
    assert result.artifact_ref == ["review-1"]
    assert requeue.reason == "blocker resolved"


def test_cli_requires_database_url(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    assert main(["list", "--run-id", "run-1"]) == 2
    assert capsys.readouterr().err == "error: OPERAMIND_DATABASE_URL is required\n"


def test_cli_rejects_invalid_parallelism_before_connecting(monkeypatch, capsys) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.setenv("OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN", "many")

    assert main(["list", "--run-id", "run-1"]) == 2
    assert "must be an integer" in capsys.readouterr().err


def test_worker_parser_supports_capability_polling_and_one_shot_mode() -> None:
    args = build_worker_parser().parse_args(
        [
            "--handler-config",
            "worker-handlers.json",
            "--executor-kind",
            "subagent",
            "--executor-id",
            "worker-2",
            "--capability",
            "change_planning",
            "--capability",
            "state_observation",
            "--project-id",
            "project-1",
            "--heartbeat-seconds",
            "5",
            "--poll-seconds",
            "1",
            "--max-concurrent-tasks",
            "2",
            "--once",
        ]
    )

    assert args.executor_kind == "subagent"
    assert args.capability == ["change_planning", "state_observation"]
    assert args.max_concurrent_tasks == 2
    assert args.once is True
