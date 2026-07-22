"""CLI for the agent-neutral orchestration task protocol."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application.orchestration_task import (
    ORCHESTRATION_MAX_ACTIVE_TASKS_ENV,
    parse_orchestration_scheduling_policy,
)
from operamind.infrastructure.postgres import (
    MigrationCatalog,
    MigrationRunner,
    OrchestrationTaskRepository,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claim and report agent-neutral workflow tasks")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser("list")
    listing.add_argument("--run-id", required=True)

    ready = subparsers.add_parser("ready")
    _executor_arguments(ready, include_id=False)
    ready.add_argument("--project-id")

    claim = subparsers.add_parser("claim")
    _executor_arguments(claim, include_id=True)
    claim.add_argument("--task-id")
    claim.add_argument("--project-id")

    heartbeat = subparsers.add_parser("heartbeat")
    _lease_arguments(heartbeat)

    release = subparsers.add_parser("release")
    _lease_arguments(release)
    release.add_argument("--reason", required=True)

    retry = subparsers.add_parser("requeue")
    retry.add_argument("--task-id", required=True)
    retry.add_argument("--actor", required=True)
    retry.add_argument("--reason", required=True)

    result = subparsers.add_parser("result")
    _lease_arguments(result)
    result.add_argument("--outcome", choices=("completed", "failed", "blocked"), required=True)
    result.add_argument("--summary", required=True)
    result.add_argument("--artifact-ref", action="append", default=[])
    result.add_argument("--evidence-json", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        scheduling_policy = parse_orchestration_scheduling_policy(
            os.getenv(ORCHESTRATION_MAX_ACTIVE_TASKS_ENV)
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url, autocommit=True) as connection:
            MigrationRunner(
                connection, MigrationCatalog.load(args.root.resolve() / "migrations")
            ).apply()
            tasks = OrchestrationTaskRepository(connection, scheduling_policy)
            if args.command == "list":
                payload: object = {"tasks": tasks.list_for_run(args.run_id)}
            elif args.command == "ready":
                payload = {
                    "tasks": tasks.list_ready(
                        executor_kind=args.executor_kind,
                        capabilities=tuple(args.capability),
                        project_id=args.project_id,
                    )
                }
            elif args.command == "claim":
                worker_token = os.getenv("OPERAMIND_WORKER_TOKEN")
                if args.executor_kind != "human" and not worker_token:
                    raise ValueError(
                        "OPERAMIND_WORKER_TOKEN is required for agent or subagent claims"
                    )
                claimed: dict[str, object] | None
                if args.task_id:
                    claimed = tasks.claim(
                        task_id=args.task_id,
                        executor_kind=args.executor_kind,
                        executor_id=args.executor_id,
                        capabilities=tuple(args.capability),
                        project_id=args.project_id,
                        worker_token=worker_token,
                    )
                else:
                    claimed = tasks.claim_next(
                        executor_kind=args.executor_kind,
                        executor_id=args.executor_id,
                        capabilities=tuple(args.capability),
                        project_id=args.project_id,
                        worker_token=worker_token,
                    )
                payload = {"task": claimed}
            elif args.command == "heartbeat":
                payload = {
                    "task": tasks.heartbeat(
                        task_id=args.task_id,
                        executor_id=args.executor_id,
                        lease_token=args.lease_token,
                    )
                }
            elif args.command == "release":
                payload = {
                    "task": tasks.release(
                        task_id=args.task_id,
                        executor_id=args.executor_id,
                        lease_token=args.lease_token,
                        reason=args.reason,
                    )
                }
            elif args.command == "requeue":
                payload = {
                    "task": tasks.requeue(
                        task_id=args.task_id,
                        actor=args.actor,
                        reason=args.reason,
                    )
                }
            else:
                evidence = json.loads(args.evidence_json)
                if not isinstance(evidence, dict):
                    raise ValueError("evidence-json must contain a JSON object")
                payload = {
                    "task": tasks.record_result(
                        task_id=args.task_id,
                        executor_id=args.executor_id,
                        lease_token=args.lease_token,
                        outcome=args.outcome,
                        summary=args.summary,
                        artifact_refs=tuple(args.artifact_ref),
                        evidence=evidence,
                    )
                }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _executor_arguments(parser: argparse.ArgumentParser, *, include_id: bool) -> None:
    parser.add_argument("--executor-kind", choices=("agent", "subagent", "human"), required=True)
    if include_id:
        parser.add_argument("--executor-id", required=True)
    parser.add_argument("--capability", action="append", required=True)


def _lease_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--executor-id", required=True)
    parser.add_argument("--lease-token", required=True)


if __name__ == "__main__":
    raise SystemExit(main())
