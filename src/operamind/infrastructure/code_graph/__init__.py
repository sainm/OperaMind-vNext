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
    "TextReplacement",
    "WorkspaceEditResult",
    "WorkspaceScanLimits",
    "WorkspaceScanner",
]
