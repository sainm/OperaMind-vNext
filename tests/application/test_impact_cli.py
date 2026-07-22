from collections.abc import Callable, Sequence

import pytest

from operamind.commands.build_edit_packet import main as build_edit_packet
from operamind.commands.build_impact import main as build_impact
from operamind.commands.confirm_impact import main as confirm_impact
from operamind.commands.record_edit_result import main as record_edit_result


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        (
            record_edit_result,
            [
                "--edit-result-id",
                "result-1",
                "--edit-packet-id",
                "packet-1",
                "--approval-grant-id",
                "grant-1",
                "--project-id",
                "project-1",
                "--analysis-case-id",
                "case-1",
                "--workspace-root",
                ".",
                "--mode",
                "working",
            ],
        ),
        (
            build_edit_packet,
            [
                "--edit-packet-id",
                "packet-1",
                "--project-id",
                "project-1",
                "--analysis-case-id",
                "case-1",
                "--impact-report-id",
                "report-1",
                "--confirmation-id",
                "confirmation-1",
                "--workspace-root",
                ".",
                "--forbidden-glob",
                "**/.env",
            ],
        ),
        (
            build_impact,
            [
                "--anchors",
                "anchors.json",
                "--impact-report-id",
                "report-1",
                "--project-id",
                "project-1",
                "--analysis-case-id",
                "case-1",
                "--context-package-id",
                "context-1",
                "--structured-change-id",
                "change-1",
                "--code-graph-snapshot-id",
                "graph-1",
                "--repository-revision-id",
                "revision-1",
                "--profile-binding-key",
                "code-framework:repository-1",
                "--ui-impact-status",
                "unknown",
            ],
        ),
        (
            confirm_impact,
            [
                "--confirmation-id",
                "confirmation-1",
                "--impact-report-id",
                "report-1",
                "--project-id",
                "project-1",
                "--analysis-case-id",
                "case-1",
                "--confirmed-by",
                "developer@example.invalid",
            ],
        ),
    ],
)
def test_impact_cli_requires_database_url(
    command: Callable[[Sequence[str] | None], int],
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    exit_code = command(arguments)

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"
