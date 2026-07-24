import pytest

from operamind.commands.unresolved_evidence import build_parser, main


def test_unresolved_evidence_cli_has_recompute_and_history_queries() -> None:
    recompute = build_parser().parse_args(["recompute", "--code-graph-snapshot-id", "graph-1"])
    show = build_parser().parse_args(["show", "--project-id", "project-1", "--history-limit", "25"])

    assert recompute.command == "recompute"
    assert recompute.code_graph_snapshot_id == "graph-1"
    assert show.command == "show"
    assert show.project_id == "project-1"
    assert show.history_limit == 25


def test_unresolved_evidence_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    assert main(["show", "--project-id", "project-1"]) == 2
