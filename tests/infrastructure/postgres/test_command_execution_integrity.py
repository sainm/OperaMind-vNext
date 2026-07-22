from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from operamind.infrastructure.postgres import CommandExecutionResultWrite
from operamind.infrastructure.postgres.command_execution_repository import _result_digest


def _write(*, offset: timezone) -> CommandExecutionResultWrite:
    return CommandExecutionResultWrite(
        status="passed",
        exit_code=0,
        executable_path="/usr/bin/git",
        working_directory="/workspace",
        stdout_digest="a" * 64,
        stderr_digest="b" * 64,
        stdout_bytes=1,
        stderr_bytes=2,
        output_truncated=False,
        started_at=datetime(2026, 7, 17, 12, 0, tzinfo=UTC).astimezone(offset),
        completed_at=datetime(2026, 7, 17, 12, 1, tzinfo=UTC).astimezone(offset),
    )


def test_command_result_digest_is_independent_of_database_session_timezone() -> None:
    assert _result_digest(_write(offset=UTC)) == _result_digest(
        _write(offset=timezone(timedelta(hours=8)))
    )


def test_command_result_digest_rejects_naive_timestamps() -> None:
    write = _write(offset=UTC)
    naive = replace(write, started_at=write.started_at.replace(tzinfo=None))

    with pytest.raises(ValueError, match="must include a timezone"):
        _result_digest(naive)
