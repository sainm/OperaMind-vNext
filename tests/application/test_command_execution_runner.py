import os
import time
from pathlib import Path

import pytest

from operamind.application.command_execution import _execute, _windows_gradle_batch
from operamind.domain import SafeCommandTemplate

ROOT = Path(__file__).parents[2]
PYTHON_COMMAND = "python.exe" if os.name == "nt" else "python3"


def _template(*argv: str, timeout: int = 5, output_limit: int = 1024) -> SafeCommandTemplate:
    return SafeCommandTemplate(
        command_ref="test-command",
        argv=argv,
        working_directory=".",
        timeout_seconds=timeout,
        expected_exit_codes=(0,),
        environment_keys=("PATH", "LANG"),
        output_limit_bytes=output_limit,
        failure_policy="record_and_block",
    )


def test_runner_records_nonzero_exit_without_persisting_output_text() -> None:
    result = _execute(
        template=_template("git", "rev-parse", "--verify", "definitely-missing-ref"),
        workspace_root=ROOT,
    )

    assert result.status == "failed"
    assert result.exit_code != 0
    assert result.stderr_bytes > 0
    assert len(result.stderr_digest) == 64


def test_runner_kills_timed_out_process() -> None:
    result = _execute(
        template=_template(
            PYTHON_COMMAND,
            "-c",
            "import time; time.sleep(2)",
            timeout=1,
        ),
        workspace_root=ROOT,
    )

    assert result.status == "timed_out"
    assert result.exit_code is None


def test_runner_records_missing_executable_as_launch_failure() -> None:
    result = _execute(
        template=_template("operamind-command-that-does-not-exist"),
        workspace_root=ROOT,
    )

    assert result.status == "launch_failed"
    assert result.exit_code is None


def test_runner_marks_digest_only_output_over_the_profile_limit() -> None:
    result = _execute(
        template=_template(
            PYTHON_COMMAND,
            "-c",
            "print('x' * 2048)",
            output_limit=1024,
        ),
        workspace_root=ROOT,
    )

    assert result.status == "passed"
    assert result.stdout_bytes > 1024
    assert result.output_truncated


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows taskkill can bound live command trees but cannot recover an exited parent PID",
)
def test_runner_kills_descendants_left_after_parent_exit() -> None:
    started_at = time.monotonic()
    result = _execute(
        template=_template(
            PYTHON_COMMAND,
            "-c",
            (
                "import subprocess, sys; "
                "subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(30)']); "
                "print('spawned')"
            ),
        ),
        workspace_root=ROOT,
    )

    assert time.monotonic() - started_at < 5
    assert result.status == "failed"
    assert result.exit_code == 0
    assert result.stdout_bytes == len("spawned\n")


def test_windows_gradle_batch_wrapper_is_resolved_without_shell_fallback(tmp_path: Path) -> None:
    batch = tmp_path / "gradlew.bat"
    batch.write_text("@echo off\r\n", encoding="utf-8")

    assert _windows_gradle_batch(tmp_path, "./gradlew", platform_name="nt") == batch
