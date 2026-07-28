"""Use cases that coordinate domain rules and infrastructure adapters."""

from operamind.application.approval_grant import (
    ApprovalGrantRequest,
    ApprovalGrantResult,
    ApprovalGrantService,
)
from operamind.application.change_closure import (
    ChangeClosureEvaluator,
    ChangeClosureInput,
)
from operamind.application.change_closure_service import (
    ChangeClosureService,
    ChangeClosureServiceResult,
)
from operamind.application.change_coverage import ChangedLineCoverageEvidence
from operamind.application.change_orchestration import (
    ChangeOrchestrationBlockedError,
    ChangeOrchestrationInput,
    ChangeOrchestrationPlanner,
    ChangeOrchestrationResult,
)
from operamind.application.change_orchestration_service import (
    ChangeOrchestrationService,
    ChangeOrchestrationServiceResult,
)
from operamind.application.code_graph_build import (
    CodeGraphBuildBlockedError,
    CodeGraphBuildRequest,
    CodeGraphBuildResult,
    CodeGraphBuildService,
)
from operamind.application.code_scope import (
    CodeScopeBlockedError,
    CodeScopeCandidate,
    CodeScopeLimits,
    CodeScopeRequest,
    CodeScopeResolutionResult,
    CodeScopeResolverService,
)
from operamind.application.command_execution import (
    ApprovedCommandRequest,
    ApprovedCommandResult,
    ApprovedCommandService,
    CommandExecutionRecoveryRequest,
    CommandExecutionRecoveryService,
)
from operamind.application.context_package import (
    ContextPackageBlockedError,
    ContextPackageBudgetError,
    ContextPackageRequest,
    ContextPackageResult,
    ContextPackageService,
)
from operamind.application.copilot_coding_task import (
    CodingTaskDeliveryProvider,
    CopilotCodingTaskPublishRequest,
    CopilotCodingTaskService,
    LocalBridgeCopilotProvider,
)
from operamind.application.copilot_task_context import (
    CopilotTaskContextRequest,
    CopilotTaskContextService,
)
from operamind.application.document_diff import (
    DocumentDiffBlockedError,
    DocumentDiffRequest,
    DocumentDiffResult,
    DocumentDiffService,
    DocumentSnapshotBuildResult,
)
from operamind.application.edit_packet import (
    EditPacketRequest,
    EditPacketResult,
    EditPacketService,
)
from operamind.application.edit_result import (
    EditResultRequest,
    EditResultService,
    EditResultServiceResult,
    EditValidationMode,
)
from operamind.application.golden_rag_quality import (
    GoldenRagQualityBlockedError,
    GoldenRagQualityRequest,
    GoldenRagQualityResult,
    GoldenRagQualityService,
)
from operamind.application.hybrid_search import (
    HybridSearchBlockedError,
    HybridSearchRequest,
    HybridSearchResult,
    HybridSearchService,
    RequirementDocumentCandidate,
    RequirementDocumentDiscoveryRequest,
    RequirementDocumentDiscoveryResult,
    RequirementDocumentDiscoveryService,
)
from operamind.application.impact_report import (
    ImpactReportRequest,
    ImpactReportResult,
    ImpactReportService,
    UiImpactStatus,
)
from operamind.application.persisted_document_diff import (
    PersistedDocumentDiffRequest,
    PersistedDocumentDiffResult,
    PersistedDocumentDiffService,
)
from operamind.application.rag_readiness import (
    RagReadinessBlockedError,
    RagReadinessRequest,
    RagReadinessResult,
    RagReadinessService,
)
from operamind.application.relation_build import (
    DocumentRelationBuildRequest,
    DocumentRelationBuildService,
    DocumentRelationBuildServiceResult,
)
from operamind.application.runtime_routes import (
    RuntimeRouteReconciler,
    RuntimeRouteReconcileRequest,
    RuntimeRouteReconcileResult,
)
from operamind.application.search_index_build import (
    SearchIndexBuildBlockedError,
    SearchIndexBuildRequest,
    SearchIndexBuildResult,
    SearchIndexBuildService,
)
from operamind.application.search_index_recovery import (
    SearchIndexRecoveryRequest,
    SearchIndexRecoveryService,
)
from operamind.application.test_data_execution_service import (
    TestDataExecutionService,
    TestDataExecutionServiceRequest,
    TestDataExecutionServiceResult,
)
from operamind.application.unresolved_evidence import (
    UnresolvedEvidenceBuildResult,
    UnresolvedEvidenceReportBuilder,
    unresolved_evidence_report_id,
)

__all__ = [
    "ApprovalGrantRequest",
    "ApprovalGrantResult",
    "ApprovalGrantService",
    "ApprovedCommandRequest",
    "ApprovedCommandResult",
    "ApprovedCommandService",
    "ChangeClosureEvaluator",
    "ChangeClosureInput",
    "ChangeClosureService",
    "ChangeClosureServiceResult",
    "ChangeOrchestrationBlockedError",
    "ChangeOrchestrationInput",
    "ChangeOrchestrationPlanner",
    "ChangeOrchestrationResult",
    "ChangeOrchestrationService",
    "ChangeOrchestrationServiceResult",
    "ChangedLineCoverageEvidence",
    "CodeGraphBuildBlockedError",
    "CodeGraphBuildRequest",
    "CodeGraphBuildResult",
    "CodeGraphBuildService",
    "CodeScopeBlockedError",
    "CodeScopeCandidate",
    "CodeScopeLimits",
    "CodeScopeRequest",
    "CodeScopeResolutionResult",
    "CodeScopeResolverService",
    "CodingTaskDeliveryProvider",
    "CommandExecutionRecoveryRequest",
    "CommandExecutionRecoveryService",
    "ContextPackageBlockedError",
    "ContextPackageBudgetError",
    "ContextPackageRequest",
    "ContextPackageResult",
    "ContextPackageService",
    "CopilotCodingTaskPublishRequest",
    "CopilotCodingTaskService",
    "CopilotTaskContextRequest",
    "CopilotTaskContextService",
    "DocumentDiffBlockedError",
    "DocumentDiffRequest",
    "DocumentDiffResult",
    "DocumentDiffService",
    "DocumentRelationBuildRequest",
    "DocumentRelationBuildService",
    "DocumentRelationBuildServiceResult",
    "DocumentSnapshotBuildResult",
    "EditPacketRequest",
    "EditPacketResult",
    "EditPacketService",
    "EditResultRequest",
    "EditResultService",
    "EditResultServiceResult",
    "EditValidationMode",
    "GoldenRagQualityBlockedError",
    "GoldenRagQualityRequest",
    "GoldenRagQualityResult",
    "GoldenRagQualityService",
    "HybridSearchBlockedError",
    "HybridSearchRequest",
    "HybridSearchResult",
    "HybridSearchService",
    "ImpactReportRequest",
    "ImpactReportResult",
    "ImpactReportService",
    "LocalBridgeCopilotProvider",
    "PersistedDocumentDiffRequest",
    "PersistedDocumentDiffResult",
    "PersistedDocumentDiffService",
    "RagReadinessBlockedError",
    "RagReadinessRequest",
    "RagReadinessResult",
    "RagReadinessService",
    "RequirementDocumentCandidate",
    "RequirementDocumentDiscoveryRequest",
    "RequirementDocumentDiscoveryResult",
    "RequirementDocumentDiscoveryService",
    "RuntimeRouteReconcileRequest",
    "RuntimeRouteReconcileResult",
    "RuntimeRouteReconciler",
    "SearchIndexBuildBlockedError",
    "SearchIndexBuildRequest",
    "SearchIndexBuildResult",
    "SearchIndexBuildService",
    "SearchIndexRecoveryRequest",
    "SearchIndexRecoveryService",
    "TestDataExecutionService",
    "TestDataExecutionServiceRequest",
    "TestDataExecutionServiceResult",
    "UiImpactStatus",
    "UnresolvedEvidenceBuildResult",
    "UnresolvedEvidenceReportBuilder",
    "unresolved_evidence_report_id",
]
