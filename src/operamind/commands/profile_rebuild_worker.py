"""Long-running Worker for ordered Profile Drift replacement generation."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application.orchestration_worker import (
    OrchestrationTaskWorker,
    OrchestrationWorkerConfiguration,
    OrchestrationWorkerIteration,
)
from operamind.infrastructure.orchestration_worker import load_fixed_command_handlers
from operamind.infrastructure.postgres import (
    MigrationCatalog,
    MigrationRunner,
    OrchestrationTaskRepository,
    ProfileRebuildTaskQueue,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Claim and validate ordered Profile Drift rebuild requests"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--handler-config", type=Path, required=True)
    parser.add_argument("--executor-kind", choices=("agent", "subagent"), default="agent")
    parser.add_argument("--executor-id", required=True)
    parser.add_argument("--capability", action="append", required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--heartbeat-seconds", type=float, default=10.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--max-concurrent-tasks", type=int, default=1)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve(strict=True)
        handler_config = args.handler_config
        if not handler_config.is_absolute():
            handler_config = root / handler_config
        handlers = load_fixed_command_handlers(
            path=handler_config.resolve(strict=True), repository_root=root
        )
        capabilities = tuple(args.capability)
        if set(capabilities) != set(handlers):
            raise ValueError(
                "Profile Rebuild capabilities must exactly match configured handler actions"
            )
        if not 1 <= args.max_concurrent_tasks <= 100:
            raise ValueError("--max-concurrent-tasks must be between 1 and 100")
    except (json.JSONDecodeError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    stop_event = threading.Event()
    try:
        with psycopg.connect(database_url, autocommit=True) as connection:
            MigrationRunner(connection, MigrationCatalog.load(root / "migrations")).apply()
            worker_registry = OrchestrationTaskRepository(connection)
            queue = ProfileRebuildTaskQueue(connection)
            registration_lease_seconds = min(
                86400,
                max(30, int(max(args.heartbeat_seconds, args.poll_seconds) * 3)),
            )
            registration = worker_registry.register_worker(
                executor_kind=args.executor_kind,
                executor_id=args.executor_id,
                capabilities=capabilities,
                project_id=args.project_id,
                max_concurrent_tasks=args.max_concurrent_tasks,
                lease_seconds=registration_lease_seconds,
            )
            worker_token = str(registration["worker_token"])
            worker = OrchestrationTaskWorker(
                queue=queue,
                handlers=handlers,
                configuration=OrchestrationWorkerConfiguration(
                    executor_kind=args.executor_kind,
                    executor_id=args.executor_id,
                    capabilities=capabilities,
                    worker_token=worker_token,
                    project_id=args.project_id,
                    heartbeat_interval_seconds=args.heartbeat_seconds,
                    idle_poll_seconds=args.poll_seconds,
                ),
                stop_event=stop_event,
            )
            try:
                if args.once:
                    _print_iteration(worker.run_once())
                    return 0
                _install_stop_handlers(stop_event)
                while not stop_event.is_set():
                    worker_registry.heartbeat_worker(
                        executor_kind=args.executor_kind,
                        executor_id=args.executor_id,
                        worker_token=worker_token,
                        lease_seconds=registration_lease_seconds,
                    )
                    iteration = worker.run_once()
                    _print_iteration(iteration)
                    if iteration.status == "idle":
                        stop_event.wait(args.poll_seconds)
                return 0
            finally:
                worker_registry.unregister_worker(
                    executor_kind=args.executor_kind,
                    executor_id=args.executor_id,
                    worker_token=worker_token,
                )
    except KeyboardInterrupt:
        stop_event.set()
        return 0
    except (OSError, RuntimeError, ValueError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _install_stop_handlers(stop_event: threading.Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def _print_iteration(iteration: OrchestrationWorkerIteration) -> None:
    print(json.dumps(iteration.to_dict(), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
