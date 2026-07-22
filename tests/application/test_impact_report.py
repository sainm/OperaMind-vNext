from pathlib import PurePosixPath

import pytest

from operamind.application import ImpactReportRequest, UiImpactStatus
from operamind.application.code_scope import CodeScopeRequest
from operamind.domain import CodeAnchor, CodeAnchorKind


def _scope() -> CodeScopeRequest:
    return CodeScopeRequest(
        project_id="project-1",
        analysis_case_id="case-1",
        context_package_id="context-1",
        structured_change_id="change-1",
        code_graph_snapshot_id="graph-1",
        repository_revision_id="revision-1",
        profile_binding_key="code-framework:repository-1",
        anchors=(
            CodeAnchor(
                anchor_id="repository-search",
                kind=CodeAnchorKind.SYMBOL,
                value="example.ExpenseRepository#search(String,Pageable)",
                evidence_refs=("document-node-1",),
            ),
        ),
    )


def test_planned_test_file_requires_a_safe_test_path() -> None:
    request = ImpactReportRequest(
        impact_report_id="impact-1",
        scope=_scope(),
        ui_impact_status=UiImpactStatus.IMPACTED,
        required_ui_scenario_refs=("expense-status-filter",),
        planned_test_files=("VisionDemo/src/test/scripts/expense-status-search.sh",),
    )

    assert request.planned_test_files == (
        PurePosixPath("VisionDemo/src/test/scripts/expense-status-search.sh").as_posix(),
    )


@pytest.mark.parametrize(
    "path",
    (
        "VisionDemo/src/main/java/Unapproved.java",
        "../test/escape.sh",
        "/tmp/test/absolute.sh",
        "VisionDemo/src/test/scripts/no-extension",
    ),
)
def test_planned_test_file_rejects_non_test_or_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="safe test path"):
        ImpactReportRequest(
            impact_report_id="impact-1",
            scope=_scope(),
            ui_impact_status=UiImpactStatus.NOT_IMPACTED,
            planned_test_files=(path,),
        )
