"""Run one Profile-defined command inside an active Approval Grant boundary."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, cast

from psycopg import Connection

from operamind.application.coverage_report import coverage_report_digest
from operamind.contracts import ContractCatalog
from operamind.domain import SafeCommandTemplate
from operamind.infrastructure.code_graph import GitWorktreeDiffInspector
from operamind.infrastructure.postgres import (
    CommandExecutionRecord,
    CommandExecutionRepository,
    CommandExecutionRequestWrite,
    CommandExecutionResultWrite,
    CommandExecutionScope,
    ProfileRepository,
)
from operamind.platform_runtime import (
    approved_process_environment,
    subprocess_creation_flags,
    terminate_windows_process_tree,
)
from operamind.profiles import ProfileCatalog

_WORKSPACE_COMMAND_LOCKS_GUARD = threading.Lock()
_WORKSPACE_COMMAND_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_GROUP_GRACE_SECONDS = 0.5
_PROCESS_GROUP_POLL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class ApprovedCommandRequest:
    command_execution_id: str
    approval_grant_id: str
    project_id: str
    analysis_case_id: str
    edit_packet_id: str
    workspace_root: Path
    command_ref: str

    def __post_init__(self) -> None:
        values = (
            self.command_execution_id,
            self.approval_grant_id,
            self.project_id,
            self.analysis_case_id,
            self.edit_packet_id,
            self.command_ref,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Approved command request fields must not be blank")


@dataclass(frozen=True, slots=True)
class ApprovedCommandResult:
    record: CommandExecutionRecord
    command_profile_version_id: str
    command_ref: str
    template_digest: str
    tested_content_digest: str
    coverage_report: dict[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "command_execution_id": self.record.command_execution_id,
            "created": self.record.created,
            "status": self.record.status,
            "exit_code": self.record.exit_code,
            "command_profile_version_id": self.command_profile_version_id,
            "command_ref": self.command_ref,
            "template_digest": self.template_digest,
            "tested_content_digest": self.tested_content_digest,
            "executable_path": self.record.executable_path,
            "working_directory": self.record.working_directory,
            "stdout_digest": self.record.stdout_digest,
            "stderr_digest": self.record.stderr_digest,
            "stdout_bytes": self.record.stdout_bytes,
            "stderr_bytes": self.record.stderr_bytes,
            "output_truncated": self.record.output_truncated,
            "started_at": self.record.started_at.isoformat(),
            "completed_at": self.record.completed_at.isoformat(),
        }
        if self.record.recovery_id is not None:
            payload["recovery"] = {
                "recovery_id": self.record.recovery_id,
                "actor": self.record.recovery_actor,
                "reason": self.record.recovery_reason,
                "stale_before": (
                    self.record.recovery_stale_before.isoformat()
                    if self.record.recovery_stale_before is not None
                    else None
                ),
            }
        if self.coverage_report is not None:
            payload["coverage_report"] = dict(self.coverage_report)
        return payload


@dataclass(frozen=True, slots=True)
class CommandExecutionRecoveryRequest:
    recovery_id: str
    command_execution_id: str
    project_id: str
    actor: str
    reason: str
    stale_before: datetime

    def __post_init__(self) -> None:
        required = (
            self.recovery_id,
            self.command_execution_id,
            self.project_id,
            self.actor,
            self.reason,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Command execution recovery fields must not be blank")
        if self.stale_before.utcoffset() is None:
            raise ValueError("Command recovery stale_before must include a timezone")


class CommandExecutionRecoveryService:
    """Close an interrupted reservation without re-running its approved command."""

    def __init__(self, *, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._repository = CommandExecutionRepository(connection, contracts)

    def run(self, request: CommandExecutionRecoveryRequest) -> CommandExecutionRecord:
        return self._repository.recover_incomplete(
            recovery_id=request.recovery_id,
            command_execution_id=request.command_execution_id,
            project_id=request.project_id,
            actor=request.actor,
            reason=request.reason,
            stale_before=request.stale_before,
        )


class ApprovedCommandService:
    """Authorize, reserve, run without a shell, then append a digest-only result."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        profiles: ProfileCatalog,
    ) -> None:
        self._repository = CommandExecutionRepository(connection, contracts)
        self._profiles = ProfileRepository(connection, profiles)
        self._git = GitWorktreeDiffInspector()

    def run(self, request: ApprovedCommandRequest) -> ApprovedCommandResult:
        scope = self._repository.load_scope(
            approval_grant_id=request.approval_grant_id,
            project_id=request.project_id,
            analysis_case_id=request.analysis_case_id,
            edit_packet_id=request.edit_packet_id,
            command_ref=request.command_ref,
        )
        registered_root = Path(scope.workspace_root).resolve(strict=True)
        requested_root = request.workspace_root.resolve(strict=True)
        if self._git.common_repository_dir(registered_root) != self._git.common_repository_dir(
            requested_root
        ):
            raise ValueError(
                "Approved command Workspace is not linked to the registered Repository"
            )
        profile = self._profiles.get_version(scope.command_profile_version_id)
        if profile is None:
            raise RuntimeError("Approval Grant Command Profile Version no longer exists")
        template = SafeCommandTemplate.from_profile(profile, command_ref=scope.command_ref)
        with _workspace_command_lock(requested_root):
            evidence = self._git.inspect_worktree(
                requested_root,
                base_sha=scope.base_repository_revision,
            )
            if evidence.remote_url != scope.remote_url:
                raise ValueError(
                    "Approved command Workspace origin does not match Repository registration"
                )
            request_digest = _request_digest(
                command_execution_id=request.command_execution_id,
                scope=scope,
                template_digest=template.digest,
                tested_content_digest=evidence.content_digest,
            )
            reservation = self._repository.reserve(
                CommandExecutionRequestWrite(
                    command_execution_id=request.command_execution_id,
                    scope=scope,
                    template_digest=template.digest,
                    request_digest=request_digest,
                )
            )
            if reservation.result is not None:
                return ApprovedCommandResult(
                    reservation.result,
                    scope.command_profile_version_id,
                    scope.command_ref,
                    template.digest,
                    evidence.content_digest,
                    _recorded_coverage_report(reservation.result),
                )
            if reservation.incomplete:
                raise RuntimeError(
                    "Command execution was previously reserved without a result; "
                    "operator review is required before using a new execution ID"
                )

            write = _execute(template=template, workspace_root=requested_root)
            current = self._git.inspect_worktree(
                requested_root,
                base_sha=scope.base_repository_revision,
            )
            if (
                current.remote_url != evidence.remote_url
                or current.content_digest != evidence.content_digest
            ):
                write = replace(write, status="failed")
            try:
                coverage_report = _coverage_report_result(
                    template=template,
                    workspace_root=requested_root,
                    command_passed=write.status == "passed",
                )
            except (OSError, RuntimeError, ValueError):
                # The command reservation must always receive a terminal result.
                # A missing or unsafe report is a failed quality gate, not an
                # incomplete execution that requires manual recovery.
                coverage_report = None
                write = replace(write, status="failed")
            if coverage_report is not None:
                write = replace(
                    write,
                    coverage_report_format=coverage_report["format"],
                    coverage_report_path=coverage_report["path"],
                    coverage_report_digest=coverage_report["digest"],
                )
            record = self._repository.record(
                command_execution_id=request.command_execution_id,
                project_id=request.project_id,
                write=write,
            )
            return ApprovedCommandResult(
                record,
                scope.command_profile_version_id,
                scope.command_ref,
                template.digest,
                evidence.content_digest,
                _recorded_coverage_report(record),
            )


def _coverage_report_result(
    *, template: SafeCommandTemplate, workspace_root: Path, command_passed: bool
) -> dict[str, str] | None:
    if template.purpose != "coverage":
        return None
    if not command_passed:
        return None
    if template.coverage_report_format is None or template.coverage_report_path is None:
        raise RuntimeError("Coverage command has no validated report binding")
    return {
        "format": template.coverage_report_format,
        "path": template.coverage_report_path,
        "digest": coverage_report_digest(workspace_root, template.coverage_report_path),
    }


def _recorded_coverage_report(record: CommandExecutionRecord) -> dict[str, str] | None:
    values = (
        record.coverage_report_format,
        record.coverage_report_path,
        record.coverage_report_digest,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise RuntimeError("Stored Coverage report binding is incomplete")
    return {
        "format": str(record.coverage_report_format),
        "path": str(record.coverage_report_path),
        "digest": str(record.coverage_report_digest),
    }


def _execute(*, template: SafeCommandTemplate, workspace_root: Path) -> CommandExecutionResultWrite:
    started_at = datetime.now(UTC)
    exit_code: int | None = None
    executable_path = template.argv[0]
    working_directory = str(workspace_root)
    status = "launch_failed"
    empty_digest = hashlib.sha256(b"").hexdigest()
    stdout_digest, stdout_bytes = empty_digest, 0
    stderr_digest, stderr_bytes = empty_digest, 0
    try:
        cwd = _resolve_workspace_path(
            workspace_root,
            template.working_directory,
            kind="working directory",
        )
        if not cwd.is_dir():
            raise OSError(f"Command working directory is not a directory: {cwd}")
        working_directory = str(cwd)
        environment = approved_process_environment(template.environment_keys)
        invocation, executable = _command_invocation(
            workspace_root=workspace_root,
            argv=template.argv,
            environment=environment,
        )
        executable_path = str(executable)
        process = subprocess.Popen(
            invocation,
            cwd=cwd,
            env=environment,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            creationflags=subprocess_creation_flags(),
        )
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Approved command pipes were not created")
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="operamind-command") as pool:
            stdout_future = pool.submit(_digest_stream, process.stdout)
            stderr_future = pool.submit(_digest_stream, process.stderr)
            timed_out = False
            try:
                process.wait(timeout=template.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                remaining_processes = _terminate_process_tree(process)
                if process.poll() is None:
                    process.wait()
            if timed_out:
                status = "timed_out"
            else:
                exit_code = process.returncode
                status = "passed" if exit_code in template.expected_exit_codes else "failed"
                if remaining_processes:
                    status = "failed"
            stdout_digest, stdout_bytes = stdout_future.result()
            stderr_digest, stderr_bytes = stderr_future.result()
    except (OSError, ValueError):
        status = "launch_failed"

    completed_at = datetime.now(UTC)
    return CommandExecutionResultWrite(
        status=status,
        exit_code=exit_code,
        executable_path=executable_path,
        working_directory=working_directory,
        stdout_digest=stdout_digest,
        stderr_digest=stderr_digest,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        output_truncated=(stdout_bytes + stderr_bytes) > template.output_limit_bytes,
        started_at=started_at,
        completed_at=completed_at,
    )


def _execute_serialized(
    *, template: SafeCommandTemplate, workspace_root: Path
) -> CommandExecutionResultWrite:
    """Prevent fixed commands from competing inside one target Workspace."""

    with _workspace_command_lock(workspace_root):
        return _execute(template=template, workspace_root=workspace_root)


@contextmanager
def _workspace_command_lock(workspace_root: Path) -> Iterator[None]:
    """Serialize commands for one Workspace across threads and local processes."""

    key = os.path.normcase(str(workspace_root.resolve(strict=True)))
    with _WORKSPACE_COMMAND_LOCKS_GUARD:
        thread_lock = _WORKSPACE_COMMAND_LOCKS.setdefault(key, threading.Lock())
    lock_root = Path(tempfile.gettempdir()) / "operamind-command-locks"
    lock_path = lock_root / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.lock"
    with thread_lock:
        lock_root.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as stream:
            _acquire_process_file_lock(stream)
            try:
                yield
            finally:
                _release_process_file_lock(stream)


def _acquire_process_file_lock(stream: IO[bytes]) -> None:
    if os.name != "nt":
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        return

    msvcrt = cast(Any, importlib.import_module("msvcrt"))

    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
    while True:
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            time.sleep(_PROCESS_GROUP_POLL_SECONDS)


def _release_process_file_lock(stream: IO[bytes]) -> None:
    if os.name != "nt":
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return

    msvcrt = cast(Any, importlib.import_module("msvcrt"))

    stream.seek(0)
    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _resolve_workspace_path(workspace_root: Path, value: str, *, kind: str) -> Path:
    candidate = (workspace_root / value).resolve(strict=True)
    if not candidate.is_relative_to(workspace_root):
        raise ValueError(f"Command {kind} escapes the Workspace")
    return candidate


def _resolve_executable(
    *, workspace_root: Path, executable: str, environment: dict[str, str]
) -> Path:
    if "/" in executable:
        candidate = _resolve_workspace_path(workspace_root, executable, kind="executable")
        if not candidate.is_file():
            raise OSError(f"Command executable is not a file: {candidate}")
        return candidate
    resolved = shutil.which(executable, path=environment.get("PATH", ""))
    if resolved is None:
        raise OSError(f"Command executable is not available in the allowed PATH: {executable}")
    return Path(resolved).resolve(strict=True)


def _command_invocation(
    *, workspace_root: Path, argv: tuple[str, ...], environment: dict[str, str]
) -> tuple[tuple[str, ...], Path]:
    """Resolve an approved argv, including Windows' non-executable .bat wrapper."""

    batch = _windows_gradle_batch(workspace_root, argv[0])
    if batch is not None:
        comspec = environment.get("COMSPEC") or shutil.which(
            "cmd.exe", path=environment.get("PATH", "")
        )
        if comspec is None:
            raise OSError("Windows command interpreter is not available")
        command_line = subprocess.list2cmdline((str(batch), *argv[1:]))
        # CALL is required for cmd.exe to return the batch file's exit code.
        return (comspec, "/d", "/s", "/c", f"call {command_line}"), batch
    executable = _resolve_executable(
        workspace_root=workspace_root,
        executable=argv[0],
        environment=environment,
    )
    return (str(executable), *argv[1:]), executable


def _windows_gradle_batch(
    workspace_root: Path, executable: str, *, platform_name: str | None = None
) -> Path | None:
    """Map the profile's POSIX Gradle wrapper token to gradlew.bat on Windows."""

    platform = os.name if platform_name is None else platform_name
    if platform != "nt":
        return None
    normalized = executable.replace("\\", "/")
    if normalized not in {"./gradlew", "gradlew", "./gradlew.bat", "gradlew.bat"}:
        return None
    candidate = (workspace_root / "gradlew.bat").resolve(strict=True)
    if not candidate.is_file():
        raise OSError(f"Command executable is not a file: {candidate}")
    return candidate


def _digest_stream(stream: IO[bytes]) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    try:
        while chunk := stream.read(65536):
            digest.update(chunk)
            byte_count += len(chunk)
    finally:
        stream.close()
    return digest.hexdigest(), byte_count


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> bool:
    """Terminate descendants in the command's isolated process group, if any remain."""

    if os.name != "posix":
        return terminate_windows_process_tree(process)
    attempts = max(1, int(_PROCESS_GROUP_GRACE_SECONDS / _PROCESS_GROUP_POLL_SECONDS))
    for attempt in range(attempts):
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # The leader has already been waited and reaped.  On macOS its now-free
            # process-group id can be reused before this probe; EPERM then refers to
            # an unrelated group, not to a command launch failure.  Never signal it.
            return False
        if attempt + 1 < attempts:
            time.sleep(_PROCESS_GROUP_POLL_SECONDS)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The group disappeared or was reused between the probe and kill.
        return False
    return True


def _request_digest(
    *,
    command_execution_id: str,
    scope: CommandExecutionScope,
    template_digest: str,
    tested_content_digest: str,
) -> str:
    payload = {
        "command_execution_id": command_execution_id,
        "scope": asdict(scope),
        "template_digest": template_digest,
        "tested_content_digest": tested_content_digest,
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
