"""Framework-independent business rules for OperaMind."""

from operamind.domain.canonical_facts import (
    CanonicalFact,
    CanonicalFactMapper,
    CanonicalFieldEvidence,
    CanonicalMappingReason,
    CanonicalMappingResult,
    CanonicalMappingStatus,
    ObservedField,
    ObservedRecord,
)
from operamind.domain.command_execution import SafeCommandTemplate
from operamind.domain.document_nodes import (
    CanonicalDocumentNodeBuilder,
    DocumentNode,
    DocumentNodeType,
)
from operamind.domain.document_relations import (
    DocumentRelationFact,
    DocumentRelationPlan,
    DocumentRelationPlanner,
    DocumentRelationRule,
    PlannedDocumentRelation,
    RelationUnresolvedReason,
    RelationValueNormalizer,
    UnresolvedDocumentRelation,
)
from operamind.domain.rag import (
    DocumentEmbeddingInput,
    DocumentEmbeddingInputBuilder,
    SearchCandidate,
    SearchChannel,
)
from operamind.domain.rag_query_plan import (
    RagPlannedQuery,
    RagQueryPlan,
    RagQueryPurpose,
    StructuredChangeQueryPlanner,
)
from operamind.domain.structured_changes import (
    CanonicalSnapshot,
    ChangeConfidence,
    ChangeReviewStatus,
    ChangeType,
    FactState,
    SnapshotFact,
    StructuredChange,
    StructuredChangeBuilder,
)

__all__ = [
    "CanonicalDocumentNodeBuilder",
    "CanonicalFact",
    "CanonicalFactMapper",
    "CanonicalFieldEvidence",
    "CanonicalMappingReason",
    "CanonicalMappingResult",
    "CanonicalMappingStatus",
    "CanonicalSnapshot",
    "ChangeConfidence",
    "ChangeReviewStatus",
    "ChangeType",
    "DocumentEmbeddingInput",
    "DocumentEmbeddingInputBuilder",
    "DocumentNode",
    "DocumentNodeType",
    "DocumentRelationFact",
    "DocumentRelationPlan",
    "DocumentRelationPlanner",
    "DocumentRelationRule",
    "FactState",
    "ObservedField",
    "ObservedRecord",
    "PlannedDocumentRelation",
    "RagPlannedQuery",
    "RagQueryPlan",
    "RagQueryPurpose",
    "RelationUnresolvedReason",
    "RelationValueNormalizer",
    "SafeCommandTemplate",
    "SearchCandidate",
    "SearchChannel",
    "SnapshotFact",
    "StructuredChange",
    "StructuredChangeBuilder",
    "StructuredChangeQueryPlanner",
    "UnresolvedDocumentRelation",
]
