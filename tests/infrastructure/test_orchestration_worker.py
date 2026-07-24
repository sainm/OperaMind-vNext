from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from operamind.application.orchestration_worker import (
    OrchestrationTaskExecutionCancelled,
    OrchestrationTaskExecutionContext,
    OrchestrationTaskExecutionError,
)
from operamind.infrastructure.orchestration_worker import (
    FixedCommandHandlerConfiguration,
    FixedCommandOrchestrationTaskHandler,
    load_fixed_command_handlers,
)


def test_fixed_command_handler_uses_json_protocol_without_lease_token(tmp_path: Path) -> None:
    code = (
        "import json,sys; value=json.load(sys.stdin); task=value['task']; "
        "assert 'lease_token' not in task; "
        "json.dump({'outcome':'completed','summary':'done',"
        "'artifact_refs':['artifact-1'],'evidence':{'task_id':task['orchestration_task_id']}},"
        "sys.stdout)"
    )
    handler = FixedCommandOrchestrationTaskHandler(
        FixedCommandHandlerConfiguration(
            action="generate_plan",
            argv=(sys.executable, "-c", code),
            working_directory=tmp_path,
            timeout_seconds=5,
        )
    )

    result = handler.execute(
        task={"orchestration_task_id": "task-1", "action": "generate_plan"},
        context=OrchestrationTaskExecutionContext(threading.Event(), threading.Event()),
    )

    assert result.outcome == "completed"
    assert result.artifact_refs == ("artifact-1",)
    assert result.evidence == {"task_id": "task-1"}


def test_handler_config_is_fixed_argv_and_stays_inside_repository(tmp_path: Path) -> None:
    config = tmp_path / "handlers.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "orchestration_worker_handlers_v1",
                "handlers": [
                    {
                        "action": "generate_plan",
                        "command": [sys.executable, "handler.py"],
                        "working_directory": ".",
                        "timeout_seconds": 60,
                        "environment_keys": ["OPERAMIND_HANDLER_MODE"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    handlers = load_fixed_command_handlers(path=config, repository_root=tmp_path)

    assert set(handlers) == {"generate_plan"}

    config.write_text(
        json.dumps(
            {
                "schema_version": "orchestration_worker_handlers_v1",
                "handlers": [
                    {
                        "action": "generate_plan",
                        "command": [sys.executable],
                        "working_directory": "../",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="leaves repository root"):
        load_fixed_command_handlers(path=config, repository_root=tmp_path)


def test_fixed_command_handler_terminates_after_lease_loss(tmp_path: Path) -> None:
    lease_lost = threading.Event()
    handler = FixedCommandOrchestrationTaskHandler(
        FixedCommandHandlerConfiguration(
            action="generate_plan",
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            working_directory=tmp_path,
            timeout_seconds=60,
        )
    )
    timer = threading.Timer(0.05, lease_lost.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(OrchestrationTaskExecutionCancelled):
            handler.execute(
                task={"orchestration_task_id": "task-lease-loss"},
                context=OrchestrationTaskExecutionContext(lease_lost, threading.Event()),
            )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 3


def test_fixed_command_handler_terminates_when_output_limit_is_crossed(tmp_path: Path) -> None:
    code = (
        "import sys,time; sys.stdout.buffer.write(b'x' * 2048); sys.stdout.flush(); time.sleep(30)"
    )
    handler = FixedCommandOrchestrationTaskHandler(
        FixedCommandHandlerConfiguration(
            action="generate_plan",
            argv=(sys.executable, "-c", code),
            working_directory=tmp_path,
            timeout_seconds=60,
            max_output_bytes=1024,
        )
    )

    started = time.monotonic()
    with pytest.raises(OrchestrationTaskExecutionError) as raised:
        handler.execute(
            task={"orchestration_task_id": "task-output-limit"},
            context=OrchestrationTaskExecutionContext(threading.Event(), threading.Event()),
        )

    assert raised.value.error_kind == "handler_output_too_large"
    assert time.monotonic() - started < 3
