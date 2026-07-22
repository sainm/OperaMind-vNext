from datetime import UTC, datetime

import pytest

from operamind.application import SearchIndexRecoveryRequest
from operamind.commands.recover_index import build_parser, main
from operamind.infrastructure.postgres import search_index_failure_event_id


def test_recover_index_cli_parses_fixed_timezone_boundary() -> None:
    args = build_parser().parse_args(
        [
            "--recovery-id",
            "recovery-1",
            "--build-id",
            "build-1",
            "--actor",
            "operator@example.invalid",
            "--reason",
            "worker interrupted",
            "--stale-before",
            "2026-07-16T12:00:00Z",
        ]
    )

    assert args.stale_before.tzinfo is UTC
    assert args.stale_before.isoformat() == "2026-07-16T12:00:00+00:00"


def test_search_index_failure_identity_is_stable_and_recovery_requires_timezone() -> None:
    assert search_index_failure_event_id("build-1") == search_index_failure_event_id("build-1")
    assert search_index_failure_event_id("build-1") != search_index_failure_event_id("build-2")
    with pytest.raises(ValueError, match="timezone"):
        SearchIndexRecoveryRequest(
            recovery_id="recovery-1",
            build_id="build-1",
            actor="operator@example.invalid",
            reason="worker interrupted",
            stale_before=datetime(2026, 7, 16, 12, 0),
        )


def test_recover_index_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    exit_code = main(
        [
            "--recovery-id",
            "recovery-1",
            "--build-id",
            "build-1",
            "--actor",
            "operator@example.invalid",
            "--reason",
            "worker interrupted",
            "--stale-before",
            "2026-07-16T12:00:00Z",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
