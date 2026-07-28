"""PostgreSQL persistence adapters."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operamind.infrastructure.postgres.orchestration_task_repository import (
        OrchestrationTaskRepository,
    )
    from operamind.infrastructure.postgres.profile_rebuild_repository import (
        ProfileRebuildTaskQueue,
    )

from operamind.infrastructure.postgres.analysis_repository import (
    AnalysisRegistration,
    AnalysisRepository,
)
from operamind.infrastructure.postgres.approval_grant_repository import (
    ApprovalGrantAuthorization,
    ApprovalGrantRecord,
    ApprovalGrantRepository,
    ApprovalGrantSource,
)
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.canonical_repository import (
    CanonicalDocumentSlice,
    CanonicalRepository,
    DocumentSnapshotWrite,
    SnapshotStatus,
)
from operamind.infrastructure.postgres.change_automation_repository import (
    ChangeAutomationRepository,
    ChangeAutomationRunRecord,
)
from operamind.infrastructure.postgres.change_closure_repository import (
    ChangeClosureEvidence,
    ChangeClosureRecord,
    ChangeClosureRepository,
)
from operamind.infrastructure.postgres.change_orchestration_repository import (
    CanonicalOrchestrationEvidence,
    ChangeOrchestrationRecord,
    ChangeOrchestrationRepository,
)
from operamind.infrastructure.postgres.code_graph_query_repository import (
    CodeAnchorMatchLoad,
    CodeEdgeLoad,
    CodeGraphQueryRepository,
    CodeGraphQueryScope,
    CodeNodeLocation,
    CodeTestFileBinding,
    CodeUnresolvedEdgeLoad,
)
from operamind.infrastructure.postgres.code_graph_repository import (
    CodeGraphPublishResult,
    CodeGraphRepositoryScope,
    CodeGraphSnapshotRepository,
)
from operamind.infrastructure.postgres.command_execution_repository import (
    CommandExecutionRecord,
    CommandExecutionRepository,
    CommandExecutionRequestWrite,
    CommandExecutionReservation,
    CommandExecutionResultWrite,
    CommandExecutionScope,
)
from operamind.infrastructure.postgres.copilot_coding_task_repository import (
    CopilotCodingTaskRecord,
    CopilotCodingTaskRepository,
)
from operamind.infrastructure.postgres.document_node_repository import (
    DocumentExpansionReason,
    DocumentNodeExpansion,
    DocumentNodeRecord,
    DocumentNodeRepository,
)
from operamind.infrastructure.postgres.edit_packet_repository import (
    ConfirmedImpactItem,
    EditPacketPublishResult,
    EditPacketRecord,
    EditPacketRepository,
    EditPacketSource,
)
from operamind.infrastructure.postgres.edit_result_repository import (
    EditResultPacketScope,
    EditResultRecord,
    EditResultRepository,
    EditResultWrite,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.golden_binding_repository import (
    GOLDEN_SEMANTIC_BINDING_VERSION,
    GoldenSemanticBinding,
    GoldenSemanticBindingRepository,
)
from operamind.infrastructure.postgres.impact_repository import (
    ImpactConfirmationResult,
    ImpactReportPublishResult,
    ImpactReportState,
    ImpactRepository,
)
from operamind.infrastructure.postgres.ingestion_result_repository import (
    DocumentIngestionResultEvent,
    DocumentIngestionResultRepository,
    DocumentIngestionStatus,
    initial_ingestion_event_id,
)
from operamind.infrastructure.postgres.migrations import MigrationCatalog, MigrationRunner
from operamind.infrastructure.postgres.profile_drift_repository import (
    ProfileDriftDetectionResult,
    ProfileDriftRepository,
    ProfileRebuildScheduleResult,
)
from operamind.infrastructure.postgres.profile_rebuild_validation import (
    ProfileReplacementValidator,
)
from operamind.infrastructure.postgres.profile_repository import (
    ActiveProfileBinding,
    ProfileRepository,
)
from operamind.infrastructure.postgres.rag_quality_repository import (
    GoldenRagQualityGateBlockedError,
    GoldenRagQualityRepository,
    GoldenRagQualityState,
)
from operamind.infrastructure.postgres.readiness_repository import (
    ReadinessEvidenceInput,
    ReadinessEvidenceRepository,
)
from operamind.infrastructure.postgres.relation_repository import (
    DocumentRelationBuildResult,
    DocumentRelationBuildSpec,
    DocumentRelationBuildState,
    DocumentRelationBuildStatus,
    DocumentRelationRepository,
    document_relation_id,
    unresolved_relation_id,
)
from operamind.infrastructure.postgres.review_repository import (
    StructuredChangeReviewDecision,
    StructuredChangeReviewRepository,
    StructuredChangeReviewState,
)
from operamind.infrastructure.postgres.runtime_route_repository import (
    RuntimeRouteEvidencePublishResult,
    RuntimeRouteEvidenceRepository,
)
from operamind.infrastructure.postgres.search_index_repository import (
    RankedSearchHit,
    SearchIndexBuildSpec,
    SearchIndexBuildStartResult,
    SearchIndexBuildState,
    SearchIndexBuildStatus,
    SearchIndexEntryWrite,
    SearchIndexFailureKind,
    SearchIndexRepository,
    SearchIndexTarget,
    search_index_failure_event_id,
    vector_cache_id,
)
from operamind.infrastructure.postgres.test_case_execution_authorization_repository import (
    TestCaseExecutionAuthorizationRecord,
    TestCaseExecutionAuthorizationRepository,
)
from operamind.infrastructure.postgres.test_case_revision_repository import (
    TestCaseProposalRecord,
    TestCaseRevisionRecord,
    TestCaseRevisionRepository,
    TestCaseStaleScope,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionEventWrite,
    TestDataExecutionRecord,
    TestDataExecutionRecoveryWrite,
    TestDataExecutionRepository,
    TestDataExecutionReservation,
    TestDataExecutionRunWrite,
)
from operamind.infrastructure.postgres.unresolved_evidence_repository import (
    UnresolvedEvidencePublishResult,
    UnresolvedEvidenceRepository,
)
from operamind.infrastructure.postgres.web_command_repository import WebCommandRepository
from operamind.infrastructure.postgres.web_control_plane_repository import (
    ChangeRequestRecord,
    DocumentReviewRecord,
    WebControlPlaneRepository,
)


def __getattr__(name: str) -> object:
    """Load the orchestration adapter lazily to avoid application import cycles."""
    if name == "OrchestrationTaskRepository":
        from operamind.infrastructure.postgres.orchestration_task_repository import (
            OrchestrationTaskRepository,
        )

        return OrchestrationTaskRepository
    if name == "ProfileRebuildTaskQueue":
        from operamind.infrastructure.postgres.profile_rebuild_repository import (
            ProfileRebuildTaskQueue,
        )

        return ProfileRebuildTaskQueue
    raise AttributeError(name)


__all__ = [
    "GOLDEN_SEMANTIC_BINDING_VERSION",
    "ActiveProfileBinding",
    "AnalysisRegistration",
    "AnalysisRepository",
    "ApprovalGrantAuthorization",
    "ApprovalGrantRecord",
    "ApprovalGrantRepository",
    "ApprovalGrantSource",
    "ArtifactRepository",
    "CanonicalDocumentSlice",
    "CanonicalOrchestrationEvidence",
    "CanonicalRepository",
    "ChangeAutomationRepository",
    "ChangeAutomationRunRecord",
    "ChangeClosureEvidence",
    "ChangeClosureRecord",
    "ChangeClosureRepository",
    "ChangeOrchestrationRecord",
    "ChangeOrchestrationRepository",
    "ChangeRequestRecord",
    "CodeAnchorMatchLoad",
    "CodeEdgeLoad",
    "CodeGraphPublishResult",
    "CodeGraphQueryRepository",
    "CodeGraphQueryScope",
    "CodeGraphRepositoryScope",
    "CodeGraphSnapshotRepository",
    "CodeNodeLocation",
    "CodeTestFileBinding",
    "CodeUnresolvedEdgeLoad",
    "CommandExecutionRecord",
    "CommandExecutionRepository",
    "CommandExecutionRequestWrite",
    "CommandExecutionReservation",
    "CommandExecutionResultWrite",
    "CommandExecutionScope",
    "ConfirmedImpactItem",
    "CopilotCodingTaskRecord",
    "CopilotCodingTaskRepository",
    "DocumentExpansionReason",
    "DocumentIngestionResultEvent",
    "DocumentIngestionResultRepository",
    "DocumentIngestionStatus",
    "DocumentNodeExpansion",
    "DocumentNodeRecord",
    "DocumentNodeRepository",
    "DocumentRelationBuildResult",
    "DocumentRelationBuildSpec",
    "DocumentRelationBuildState",
    "DocumentRelationBuildStatus",
    "DocumentRelationRepository",
    "DocumentReviewRecord",
    "DocumentSnapshotWrite",
    "EditPacketPublishResult",
    "EditPacketRecord",
    "EditPacketRepository",
    "EditPacketSource",
    "EditResultPacketScope",
    "EditResultRecord",
    "EditResultRepository",
    "EditResultWrite",
    "GoldenRagQualityGateBlockedError",
    "GoldenRagQualityRepository",
    "GoldenRagQualityState",
    "GoldenSemanticBinding",
    "GoldenSemanticBindingRepository",
    "ImpactConfirmationResult",
    "ImpactReportPublishResult",
    "ImpactReportState",
    "ImpactRepository",
    "MigrationCatalog",
    "MigrationRunner",
    "OrchestrationTaskRepository",
    "PersistenceConflictError",
    "ProfileDriftDetectionResult",
    "ProfileDriftRepository",
    "ProfileRebuildScheduleResult",
    "ProfileRebuildTaskQueue",
    "ProfileReplacementValidator",
    "ProfileRepository",
    "RankedSearchHit",
    "ReadinessEvidenceInput",
    "ReadinessEvidenceRepository",
    "RuntimeRouteEvidencePublishResult",
    "RuntimeRouteEvidenceRepository",
    "SearchIndexBuildSpec",
    "SearchIndexBuildStartResult",
    "SearchIndexBuildState",
    "SearchIndexBuildStatus",
    "SearchIndexEntryWrite",
    "SearchIndexFailureKind",
    "SearchIndexRepository",
    "SearchIndexTarget",
    "SnapshotStatus",
    "StructuredChangeReviewDecision",
    "StructuredChangeReviewRepository",
    "StructuredChangeReviewState",
    "TestCaseExecutionAuthorizationRecord",
    "TestCaseExecutionAuthorizationRepository",
    "TestCaseProposalRecord",
    "TestCaseRevisionRecord",
    "TestCaseRevisionRepository",
    "TestCaseStaleScope",
    "TestDataExecutionEventWrite",
    "TestDataExecutionRecord",
    "TestDataExecutionRecoveryWrite",
    "TestDataExecutionRepository",
    "TestDataExecutionReservation",
    "TestDataExecutionRunWrite",
    "UnresolvedEvidencePublishResult",
    "UnresolvedEvidenceRepository",
    "WebCommandRepository",
    "WebControlPlaneRepository",
    "document_relation_id",
    "initial_ingestion_event_id",
    "search_index_failure_event_id",
    "unresolved_relation_id",
    "vector_cache_id",
]
