"""Built-in fixed-command handler for the deterministic change-planning task."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg

from operamind.application.change_orchestration import ChangeOrchestrationBlockedError
from operamind.application.change_orchestration_service import ChangeOrchestrationService

MAX_HANDLER_INPUT_BYTES = 2_000_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Handle one generate_orchestration Task received on standard input"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        task = _load_task(sys.stdin.read(MAX_HANDLER_INPUT_BYTES + 1))
        change_request_id = _required_text(task, "change_request_id")
        actor = _active_executor(task)
        with psycopg.connect(database_url) as connection:
            result = ChangeOrchestrationService(
                connection=connection, repository_root=args.root.resolve(strict=True)
            ).orchestrate(change_request_id=change_request_id, actor=actor)
        orchestration = result.orchestration
        refs = [str(orchestration["orchestration_id"])]
        artifact_values = orchestration.get("artifact_refs")
        if isinstance(artifact_values, dict):
            refs.extend(str(value) for value in artifact_values.values())
        _write_result(
            outcome="completed",
            summary="Canonical change orchestration was generated.",
            artifact_refs=refs,
            evidence={
                "handler": "canonical_change_orchestration",
                "created": result.created,
                "artifact_count": len(refs),
            },
        )
        return 0
    except ChangeOrchestrationBlockedError as error:
        _write_result(
            outcome="blocked",
            summary="Canonical change orchestration is blocked.",
            artifact_refs=[],
            evidence={"blocking_reason": str(error)[:2_000]},
        )
        return 0
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError, psycopg.Error) as error:
        print(f"error: {type(error).__name__}", file=sys.stderr)
        return 1


def _load_task(raw: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > MAX_HANDLER_INPUT_BYTES:
        raise ValueError("worker handler input is too large")
    value: object = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"protocol_version", "task"}:
        raise ValueError("worker handler input does not match the protocol")
    if value["protocol_version"] != "orchestration_worker_handler_v1":
        raise ValueError("worker handler protocol version is unsupported")
    task = value["task"]
    if not isinstance(task, dict) or task.get("action") != "generate_orchestration":
        raise ValueError("handler only accepts generate_orchestration tasks")
    if "lease_token" in task:
        raise ValueError("worker handler input must not contain a lease token")
    return task


def _active_executor(task: dict[str, Any]) -> str:
    claims = task.get("claims")
    if not isinstance(claims, list):
        raise ValueError("worker task does not contain claim history")
    active = [
        value for value in claims if isinstance(value, dict) and value.get("status") == "active"
    ]
    if len(active) != 1:
        raise ValueError("worker task requires exactly one active claim")
    return _required_text(active[0], "executor_id")


def _required_text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"worker task is missing {key}")
    return item


def _write_result(
    *, outcome: str, summary: str, artifact_refs: list[str], evidence: dict[str, object]
) -> None:
    print(
        json.dumps(
            {
                "outcome": outcome,
                "summary": summary,
                "artifact_refs": artifact_refs,
                "evidence": evidence,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
