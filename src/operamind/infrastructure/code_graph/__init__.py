"""Safe local workspace discovery and Tree-sitter Code Graph extraction."""

from operamind.infrastructure.code_graph.git import (
    GitDiffEvidence,
    GitPathChange,
    GitRevisionEvidence,
    GitWorkspaceInspector,
    GitWorktreeDiffInspector,
)
from operamind.infrastructure.code_graph.incremental import (
    IncrementalCodeGraphScanner,
    IncrementalScanPlan,
)
from operamind.infrastructure.code_graph.scanner import (
    CodeGraphScanner,
    CodeGraphScanResult,
)
from operamind.infrastructure.code_graph.semantic import (
    SemanticAdapterRegistry,
    SemanticFileExtraction,
    SemanticRelation,
    SemanticSymbol,
)
from operamind.infrastructure.code_graph.struts1 import (
    STRUTS1_EXTRACTOR,
    Struts1GraphResult,
    extract_struts1_graph,
)
from operamind.infrastructure.code_graph.workspace import (
    DiscoveredCodeFile,
    WorkspaceScanLimits,
    WorkspaceScanner,
)
from operamind.infrastructure.code_graph.workspace_edit import (
    PreEditedWorkspaceVerifier,
    SafeWorkspaceEditor,
    TextReplacement,
    WorkspaceEditResult,
)

__all__ = [
    "STRUTS1_EXTRACTOR",
    "CodeGraphScanResult",
    "CodeGraphScanner",
    "DiscoveredCodeFile",
    "GitDiffEvidence",
    "GitPathChange",
    "GitRevisionEvidence",
    "GitWorkspaceInspector",
    "GitWorktreeDiffInspector",
    "IncrementalCodeGraphScanner",
    "IncrementalScanPlan",
    "PreEditedWorkspaceVerifier",
    "SafeWorkspaceEditor",
    "SemanticAdapterRegistry",
    "SemanticFileExtraction",
    "SemanticRelation",
    "SemanticSymbol",
    "Struts1GraphResult",
    "TextReplacement",
    "WorkspaceEditResult",
    "WorkspaceScanLimits",
    "WorkspaceScanner",
    "extract_struts1_graph",
]
