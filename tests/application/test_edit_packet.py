from pathlib import Path
from unittest.mock import MagicMock

from operamind.application.edit_packet import EditPacketRequest, EditPacketService
from operamind.infrastructure.code_graph import GitRevisionEvidence
from operamind.infrastructure.postgres import ConfirmedImpactItem, EditPacketSource


def test_edit_packet_allows_a_graph_validated_new_test_file(tmp_path: Path) -> None:
    service = object.__new__(EditPacketService)
    service._contracts = MagicMock()
    service._repository = MagicMock()
    service._git = MagicMock()
    service._repository.load_source.return_value = EditPacketSource(
        project_id="project-001",
        analysis_case_id="case-001",
        impact_report_id="report-001",
        confirmation_id="confirmation-001",
        repository_id="repository-001",
        repository_revision_id="revision-001",
        commit_sha="a" * 40,
        remote_url="https://example.invalid/repository.git",
        workspace_root=str(tmp_path),
        business_summary="Change the expense status label.",
        required_ui_scenario_refs=(),
        approved_item_ids=("item-001",),
        items=(
            ConfirmedImpactItem(
                impact_item_id="item-001",
                target_path="src/main/resources/templates/expense-list.html",
                target_symbols=(),
                recommended_action="modify",
                test_file_refs=("src/test/java/example/ExpenseListTemplateTest.java",),
            ),
        ),
    )
    service._git.inspect.return_value = GitRevisionEvidence(
        workspace_root=tmp_path,
        head_sha="a" * 40,
        remote_url="https://example.invalid/repository.git",
        tracked_paths=frozenset(
            {"src/main/resources/templates/expense-list.html"}
        ),
    )
    service._repository.publish.return_value = MagicMock()

    result = service.run(
        EditPacketRequest(
            edit_packet_id="packet-001",
            project_id="project-001",
            analysis_case_id="case-001",
            impact_report_id="report-001",
            confirmation_id="confirmation-001",
            workspace_root=tmp_path,
            forbidden_globs=("**/.env",),
        )
    )

    assert result.artifact["editable_files"] == [
        "src/main/resources/templates/expense-list.html"
    ]
    assert result.artifact["test_files"] == [
        "src/test/java/example/ExpenseListTemplateTest.java"
    ]
