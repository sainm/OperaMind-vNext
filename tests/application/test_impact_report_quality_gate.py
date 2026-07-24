from unittest.mock import MagicMock

import pytest

from operamind.application import (
    CodeScopeRequest,
    ImpactReportRequest,
    ImpactReportService,
    UiImpactStatus,
)
from operamind.domain import CodeAnchor, CodeAnchorKind
from operamind.infrastructure.postgres import GoldenRagQualityGateBlockedError


def test_impact_report_blocks_before_scope_resolution_when_golden_quality_fails() -> None:
    service = ImpactReportService.__new__(ImpactReportService)
    service._artifacts = MagicMock()
    service._artifacts.get.return_value = {
        "artifact_type": "ContextPackage",
        "project_id": "visiondemo",
        "document_snapshot_id": "snapshot-001",
        "search_index_build_id": "index-001",
        "retrieval_policy": {
            "embedding_profile_version_id": "embedding-001",
        },
    }
    service._rag_quality = MagicMock()
    service._rag_quality.require_passed_gate.side_effect = GoldenRagQualityGateBlockedError(
        "quality_threshold_failed:mrr"
    )
    service._scope = MagicMock()
    request = ImpactReportRequest(
        impact_report_id="impact-001",
        scope=CodeScopeRequest(
            project_id="visiondemo",
            analysis_case_id="case-001",
            context_package_id="context-001",
            structured_change_id="change-001",
            code_graph_snapshot_id="graph-001",
            repository_revision_id="revision-001",
            profile_binding_key="code-framework:visiondemo",
            anchors=(
                CodeAnchor(
                    anchor_id="anchor-001",
                    kind=CodeAnchorKind.ENDPOINT,
                    value="GET /expense/api/search",
                    evidence_refs=("document-node-001",),
                ),
            ),
        ),
        ui_impact_status=UiImpactStatus.IMPACTED,
        required_ui_scenario_refs=("expense-filter-default-all",),
    )

    with pytest.raises(
        GoldenRagQualityGateBlockedError,
        match="quality_threshold_failed:mrr",
    ):
        service.run(request)

    service._rag_quality.require_passed_gate.assert_called_once_with(
        project_id="visiondemo",
        document_snapshot_id="snapshot-001",
        embedding_profile_version_id="embedding-001",
        search_index_build_id="index-001",
    )
    service._scope.resolve.assert_not_called()
