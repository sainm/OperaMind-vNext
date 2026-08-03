import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from operamind.application import command_execution
from operamind.application.command_execution import (
    ApprovedCommandRequest,
    ApprovedCommandService,
    _execute,
    _execute_serialized,
    _windows_gradle_batch,
    _workspace_command_lock,
)
from operamind.domain import SafeCommandTemplate
from operamind.infrastructure.postgres.command_execution_repository import (
    CommandExecutionRecord,
    CommandExecutionReservation,
    CommandExecutionScope,
)

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


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups are not used on Windows")
def test_runner_does_not_reclassify_success_when_reaped_group_probe_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied_probe(_process_group_id: int, _signal: int) -> None:
        raise PermissionError("process group id was reused")

    monkeypatch.setattr(command_execution.os, "killpg", denied_probe)

    result = _execute(
        template=_template(PYTHON_COMMAND, "-c", "print('completed')"),
        workspace_root=ROOT,
    )

    assert result.status == "passed"
    assert result.exit_code == 0
    assert result.stdout_bytes == len("completed\n")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups are not used on Windows")
def test_runner_allows_normally_exiting_descendants_a_short_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []

    def disappearing_group(_process_group_id: int, sent_signal: int) -> None:
        signals.append(sent_signal)
        if len(signals) == 3:
            raise ProcessLookupError("group completed during grace period")

    class CompletedProcess:
        pid = 12345

    monkeypatch.setattr(command_execution.os, "killpg", disappearing_group)
    monkeypatch.setattr(command_execution.time, "sleep", lambda _seconds: None)

    assert command_execution._terminate_process_tree(CompletedProcess()) is False  # type: ignore[arg-type]
    assert signal.SIGKILL not in signals


def test_windows_gradle_batch_wrapper_is_resolved_without_shell_fallback(tmp_path: Path) -> None:
    batch = tmp_path / "gradlew.bat"
    batch.write_text("@echo off\r\n", encoding="utf-8")

    assert _windows_gradle_batch(tmp_path, "./gradlew", platform_name="nt") == batch


def test_runner_serializes_commands_for_the_same_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = 0
    maximum = 0
    guard = threading.Lock()
    original = command_execution._execute

    def observed_execute(*, template: SafeCommandTemplate, workspace_root: Path):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.05)
            return original(template=template, workspace_root=workspace_root)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(command_execution, "_execute", observed_execute)
    template = _template(PYTHON_COMMAND, "-c", "pass")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda _value: _execute_serialized(
                    template=template,
                    workspace_root=tmp_path,
                ),
                range(2),
            )
        )

    assert maximum == 1
    assert {result.status for result in results} == {"passed"}


def test_workspace_command_lock_serializes_separate_processes(tmp_path: Path) -> None:
    child_script = (
        "import sys,time; "
        "sys.path.insert(0, sys.argv[2]); "
        "from pathlib import Path; "
        "from operamind.application.command_execution import _workspace_command_lock; "
        "root=Path(sys.argv[1]); "
        "lock=_workspace_command_lock(root); "
        "lock.__enter__(); "
        "print('locked', flush=True); "
        "time.sleep(0.5); "
        "lock.__exit__(None, None, None)"
    )
    process = subprocess.Popen(
        (sys.executable, "-c", child_script, str(tmp_path), str(ROOT / "src")),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        started = time.monotonic()
        with _workspace_command_lock(tmp_path):
            pass
        elapsed = time.monotonic() - started
        _stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    assert process.returncode == 0, stderr
    assert elapsed >= 0.25


def test_approved_command_fails_when_the_command_changes_tested_source(tmp_path: Path) -> None:
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    scope = CommandExecutionScope(
        approval_grant_id="grant-1",
        project_id="project-1",
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        repository_id="repository-1",
        command_profile_version_id="profile-1",
        command_ref="mutating-test",
        base_repository_revision="base-1",
        remote_url="operamind-local://repository-1",
        workspace_root=str(tmp_path),
    )

    class Repository:
        recorded_status: str | None = None

        def load_scope(self, **_values: object) -> CommandExecutionScope:
            return scope

        def reserve(self, _write: object) -> CommandExecutionReservation:
            return CommandExecutionReservation(True, False, None)

        def record(self, *, command_execution_id: str, project_id: str, write: object):
            del project_id
            self.recorded_status = write.status  # type: ignore[attr-defined]
            return CommandExecutionRecord(
                created=True,
                command_execution_id=command_execution_id,
                status=write.status,  # type: ignore[attr-defined]
                exit_code=write.exit_code,  # type: ignore[attr-defined]
                executable_path=write.executable_path,  # type: ignore[attr-defined]
                working_directory=write.working_directory,  # type: ignore[attr-defined]
                stdout_digest=write.stdout_digest,  # type: ignore[attr-defined]
                stderr_digest=write.stderr_digest,  # type: ignore[attr-defined]
                stdout_bytes=write.stdout_bytes,  # type: ignore[attr-defined]
                stderr_bytes=write.stderr_bytes,  # type: ignore[attr-defined]
                output_truncated=write.output_truncated,  # type: ignore[attr-defined]
                started_at=write.started_at,  # type: ignore[attr-defined]
                completed_at=write.completed_at,  # type: ignore[attr-defined]
            )

    class Profiles:
        def get_version(self, _profile_version_id: str) -> dict[str, object]:
            return {
                "profile_type": "CommandExecutionProfile",
                "templates": [
                    {
                        "command_ref": "mutating-test",
                        "purpose": "test",
                        "argv": [
                            PYTHON_COMMAND,
                            "-c",
                            "from pathlib import Path; Path('tracked.txt').write_text('after\\n')",
                        ],
                        "working_directory": ".",
                        "timeout_seconds": 5,
                        "expected_exit_codes": [0],
                        "environment_keys": ["PATH", "LANG"],
                        "output_limit_bytes": 1024,
                        "failure_policy": "record_and_block",
                    }
                ],
            }

    class Git:
        def common_repository_dir(self, _root: Path) -> Path:
            return tmp_path

        def inspect_worktree(self, _root: Path, *, base_sha: str):
            del base_sha
            return type(
                "Evidence",
                (),
                {
                    "remote_url": "operamind-local://repository-1",
                    "content_digest": hashlib.sha256(tracked.read_bytes()).hexdigest(),
                },
            )()

    repository = Repository()
    service = ApprovedCommandService.__new__(ApprovedCommandService)
    service._repository = repository
    service._profiles = Profiles()
    service._git = Git()

    result = service.run(
        ApprovedCommandRequest(
            command_execution_id="command-1",
            approval_grant_id="grant-1",
            project_id="project-1",
            analysis_case_id="case-1",
            edit_packet_id="packet-1",
            workspace_root=tmp_path,
            command_ref="mutating-test",
        )
    )

    assert result.record.status == "failed"
    assert repository.recorded_status == "failed"


def test_missing_coverage_report_records_a_terminal_failed_result(tmp_path: Path) -> None:
    scope = CommandExecutionScope(
        approval_grant_id="grant-1",
        project_id="project-1",
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        repository_id="repository-1",
        command_profile_version_id="profile-1",
        command_ref="coverage",
        base_repository_revision="base-1",
        remote_url="operamind-local://repository-1",
        workspace_root=str(tmp_path),
    )

    class Repository:
        recorded: object | None = None

        def load_scope(self, **_values: object) -> CommandExecutionScope:
            return scope

        def reserve(self, _write: object) -> CommandExecutionReservation:
            return CommandExecutionReservation(True, False, None)

        def record(self, **values: object) -> object:
            write = values["write"]
            self.recorded = write
            return SimpleNamespace(
                status=write.status,  # type: ignore[attr-defined]
                coverage_report_format=write.coverage_report_format,  # type: ignore[attr-defined]
                coverage_report_path=write.coverage_report_path,  # type: ignore[attr-defined]
                coverage_report_digest=write.coverage_report_digest,  # type: ignore[attr-defined]
            )

    class Profiles:
        def get_version(self, _profile_version_id: str) -> dict[str, object]:
            return {
                "profile_type": "CommandExecutionProfile",
                "templates": [
                    {
                        "command_ref": "coverage",
                        "purpose": "coverage",
                        "argv": [PYTHON_COMMAND, "-c", "pass"],
                        "working_directory": ".",
                        "timeout_seconds": 5,
                        "expected_exit_codes": [0],
                        "environment_keys": ["PATH", "LANG"],
                        "output_limit_bytes": 1024,
                        "failure_policy": "record_and_block",
                        "coverage_report": {
                            "format": "coverage_py_json",
                            "path": "missing-coverage.json",
                        },
                    }
                ],
            }

    class Git:
        def common_repository_dir(self, _root: Path) -> Path:
            return tmp_path

        def inspect_worktree(self, _root: Path, *, base_sha: str) -> object:
            del base_sha
            return SimpleNamespace(
                remote_url="operamind-local://repository-1",
                content_digest="a" * 64,
            )

    repository = Repository()
    service = ApprovedCommandService.__new__(ApprovedCommandService)
    service._repository = repository
    service._profiles = Profiles()
    service._git = Git()

    result = service.run(
        ApprovedCommandRequest(
            command_execution_id="coverage-command-1",
            approval_grant_id="grant-1",
            project_id="project-1",
            analysis_case_id="case-1",
            edit_packet_id="packet-1",
            workspace_root=tmp_path,
            command_ref="coverage",
        )
    )

    assert result.record.status == "failed"
    assert repository.recorded is not None
