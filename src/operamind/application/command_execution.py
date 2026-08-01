"""Run one Profile-defined command inside an active Approval Grant boundary."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from psycopg import Connection

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

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "command_execution_id": self.record.command_execution_id,
            "created": self.record.created,
            "status": self.record.status,
            "exit_code": self.record.exit_code,
            "command_profile_version_id": self.command_profile_version_id,
            "command_ref": self.command_ref,
            "template_digest": self.template_digest,
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
        evidence = self._git.inspect_worktree(
            requested_root,
            base_sha=scope.base_repository_revision,
        )
        if evidence.remote_url != scope.remote_url:
            raise ValueError(
                "Approved command Workspace origin does not match Repository registration"
            )

        profile = self._profiles.get_version(scope.command_profile_version_id)
        if profile is None:
            raise RuntimeError("Approval Grant Command Profile Version no longer exists")
        template = SafeCommandTemplate.from_profile(profile, command_ref=scope.command_ref)
        request_digest = _request_digest(
            command_execution_id=request.command_execution_id,
            scope=scope,
            template_digest=template.digest,
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
            )
        if reservation.incomplete:
            raise RuntimeError(
                "Command execution was previously reserved without a result; "
                "operator review is required before using a new execution ID"
            )

        write = _execute(template=template, workspace_root=requested_root)
        return ApprovedCommandResult(
            self._repository.record(
                command_execution_id=request.command_execution_id,
                project_id=request.project_id,
                write=write,
            ),
            scope.command_profile_version_id,
            scope.command_ref,
            template.digest,
        )


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
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    return True


def _request_digest(
    *, command_execution_id: str, scope: CommandExecutionScope, template_digest: str
) -> str:
    payload = {
        "command_execution_id": command_execution_id,
        "scope": asdict(scope),
        "template_digest": template_digest,
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
