"""Revision-bound orchestration for a Profile-driven Code Graph Snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import (
    CodeGraphScanner,
    CodeGraphScanResult,
    GitRevisionEvidence,
    GitWorkspaceInspector,
    GitWorktreeDiffInspector,
    IncrementalCodeGraphScanner,
    WorkspaceScanLimits,
    WorkspaceScanner,
)
from operamind.infrastructure.postgres import (
    CodeGraphPublishResult,
    CodeGraphSnapshotRepository,
    ProfileRepository,
)
from operamind.profiles import ProfileCatalog


class CodeGraphBuildBlockedError(ValueError):
    """Raised when Git, Workspace, Profile, or Repository evidence does not match."""


@dataclass(frozen=True, slots=True)
class CodeGraphBuildRequest:
    """Explicit scan scope and immutable persistence identities."""

    code_graph_snapshot_id: str
    project_id: str
    repository_id: str
    repository_revision_id: str
    workspace_root: Path
    scan_roots: tuple[str, ...]
    profile_version_id: str
    profile_binding_key: str
    profile_activation_event_id: str
    activated_by: str
    activation_reason: str
    limits: WorkspaceScanLimits = field(default_factory=WorkspaceScanLimits)
    incremental: bool = True

    def __post_init__(self) -> None:
        required = (
            self.code_graph_snapshot_id,
            self.project_id,
            self.repository_id,
            self.repository_revision_id,
            self.profile_version_id,
            self.profile_binding_key,
            self.profile_activation_event_id,
            self.activated_by,
            self.activation_reason,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Code Graph build request fields must not be blank")
        if not self.scan_roots or len(self.scan_roots) != len(set(self.scan_roots)):
            raise ValueError("Code Graph scan_roots must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class CodeGraphBuildResult:
    """Published graph evidence and non-persisted scan diagnostics."""

    publication: CodeGraphPublishResult
    scan: CodeGraphScanResult
    profile_digest: str


class CodeGraphBuildService:
    """Validate Git evidence, scan tracked files, and atomically publish the graph."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        profiles: ProfileCatalog,
    ) -> None:
        self._connection = connection
        self._contracts = contracts
        self._profiles = profiles
        self._profile_repository = ProfileRepository(connection, profiles)
        self._graph_repository = CodeGraphSnapshotRepository(connection, contracts)
        self._git = GitWorkspaceInspector()
        self._git_diff = GitWorktreeDiffInspector()
        self._workspace = WorkspaceScanner()
        self._scanner = CodeGraphScanner()
        self._incremental_scanner = IncrementalCodeGraphScanner()

    def run(
        self,
        request: CodeGraphBuildRequest,
        *,
        profile: dict[str, Any],
    ) -> CodeGraphBuildResult:
        """Build from exact HEAD and publish only inside registered repository scope."""

        self._profiles.validate_profile(profile)
        if profile.get("profile_type") != "CodeFrameworkProfile":
            raise CodeGraphBuildBlockedError("Code Graph requires a CodeFrameworkProfile")
        profile_roots = frozenset(
            str(value) for value in cast(list[object], profile["default_scan_roots"])
        )
        if not set(request.scan_roots).issubset(profile_roots):
            raise CodeGraphBuildBlockedError(
                "Explicit scan roots must be selected from the Code Framework Profile"
            )
        scope = self._graph_repository.get_repository_scope(
            project_id=request.project_id,
            repository_id=request.repository_id,
            repository_revision_id=request.repository_revision_id,
        )
        if scope is None:
            raise CodeGraphBuildBlockedError("Registered Repository Revision does not exist")
        if scope.workspace_root is None:
            raise CodeGraphBuildBlockedError("Repository workspace_root is not registered")
        try:
            requested_root = request.workspace_root.resolve(strict=True)
            registered_root = Path(scope.workspace_root).resolve(strict=True)
        except OSError as error:
            raise CodeGraphBuildBlockedError("Registered Workspace does not exist") from error
        if requested_root != registered_root:
            raise CodeGraphBuildBlockedError(
                "Workspace root does not match Repository registration"
            )
        try:
            git = self._git.inspect(requested_root)
        except ValueError as error:
            raise CodeGraphBuildBlockedError(str(error)) from error
        if git.head_sha != scope.commit_sha:
            raise CodeGraphBuildBlockedError("Git HEAD does not match Repository Revision")
        if git.remote_url != scope.remote_url:
            raise CodeGraphBuildBlockedError("Git origin does not match Repository remote URL")
        profile_ref = f"{profile['profile_id']}@{profile['profile_version']}"
        try:
            scan = self._scan_revision(
                request=request,
                profile=profile,
                profile_ref=profile_ref,
                scope_commit_sha=scope.commit_sha,
                git=git,
            )
            self._contracts.validate_artifact(scan.artifact)
        except (OSError, UnicodeError, ValueError) as error:
            raise CodeGraphBuildBlockedError(str(error)) from error
        except Exception as error:
            failure_reason = f"{type(error).__name__}: Code Graph scan failed"
            failure_artifact: dict[str, Any] = {
                "artifact_type": "CodeGraphSnapshot",
                "schema_version": "v1",
                "code_graph_snapshot_id": request.code_graph_snapshot_id,
                "project_id": request.project_id,
                "repository_id": request.repository_id,
                "repository_revision": scope.commit_sha,
                "framework_profile_refs": [profile_ref],
                "scan_roots": list(request.scan_roots),
                "scan_status": "failed",
                "framework_markers_found": [],
                "diagnostics": [f"scan_runtime_failure:{type(error).__name__}"],
                "files": [],
                "edges": [],
            }
            self._contracts.validate_artifact(failure_artifact)
            with self._connection.transaction():
                self._profile_repository.store_version(
                    profile_version_id=request.profile_version_id,
                    profile=profile,
                )
                self._graph_repository.publish(
                    artifact=failure_artifact,
                    repository_revision_id=request.repository_revision_id,
                    profile_version_ids={profile_ref: request.profile_version_id},
                    failure_reason=failure_reason,
                )
            raise CodeGraphBuildBlockedError(
                "Code Graph scan failed; an immutable failed Snapshot was recorded"
            ) from error

        existing = self._graph_repository.get(request.code_graph_snapshot_id)
        if existing is not None:
            profile_digest = self._profile_repository.store_version(
                profile_version_id=request.profile_version_id,
                profile=profile,
            )
            publication = self._graph_repository.publish(
                artifact=scan.artifact,
                repository_revision_id=request.repository_revision_id,
                profile_version_ids={profile_ref: request.profile_version_id},
            )
            return CodeGraphBuildResult(
                publication=publication,
                scan=scan,
                profile_digest=profile_digest,
            )

        with self._connection.transaction():
            profile_digest = self._profile_repository.store_version(
                profile_version_id=request.profile_version_id,
                profile=profile,
            )
            self._profile_repository.activate(
                activation_event_id=request.profile_activation_event_id,
                project_id=request.project_id,
                binding_key=request.profile_binding_key,
                profile_version_id=request.profile_version_id,
                activated_by=request.activated_by,
                reason=request.activation_reason,
            )
            active = self._profile_repository.get_active(
                project_id=request.project_id,
                binding_key=request.profile_binding_key,
            )
            if active is None or active.profile_version_id != request.profile_version_id:
                raise CodeGraphBuildBlockedError(
                    "Requested Code Framework Profile is not the active binding"
                )
            publication = self._graph_repository.publish(
                artifact=scan.artifact,
                repository_revision_id=request.repository_revision_id,
                profile_version_ids={profile_ref: request.profile_version_id},
            )
        return CodeGraphBuildResult(
            publication=publication,
            scan=scan,
            profile_digest=profile_digest,
        )

    def _scan_revision(
        self,
        *,
        request: CodeGraphBuildRequest,
        profile: dict[str, Any],
        profile_ref: str,
        scope_commit_sha: str,
        git: GitRevisionEvidence,
    ) -> CodeGraphScanResult:
        excluded_globs = tuple(
            str(value) for value in cast(list[object], profile["excluded_globs"])
        )
        languages = tuple(str(value) for value in cast(list[object], profile["languages"]))
        base = self._incremental_base(
            request=request,
            profile_ref=profile_ref,
            current_revision=scope_commit_sha,
        )
        if base is not None:
            incremental = self._scan_incrementally(
                request=request,
                profile=profile,
                scope_commit_sha=scope_commit_sha,
                git=git,
                base=base,
                excluded_globs=excluded_globs,
                languages=languages,
            )
            if incremental is not None:
                return incremental
        files = self._workspace.discover(
            workspace_root=git.workspace_root,
            scan_roots=request.scan_roots,
            excluded_globs=excluded_globs,
            languages=languages,
            limits=request.limits,
            allowed_paths=git.tracked_paths,
        )
        return self._scanner.scan(
            code_graph_snapshot_id=request.code_graph_snapshot_id,
            project_id=request.project_id,
            repository_id=request.repository_id,
            repository_revision=scope_commit_sha,
            scan_roots=request.scan_roots,
            profile=profile,
            files=files,
        )

    def _scan_incrementally(
        self,
        *,
        request: CodeGraphBuildRequest,
        profile: dict[str, Any],
        scope_commit_sha: str,
        git: GitRevisionEvidence,
        base: dict[str, Any],
        excluded_globs: tuple[str, ...],
        languages: tuple[str, ...],
    ) -> CodeGraphScanResult | None:
        base_revision = str(base["repository_revision"])
        try:
            changes = (
                ()
                if base_revision == scope_commit_sha
                else self._git_diff.inspect_committed(
                    git.workspace_root, base_sha=base_revision
                ).changes
            )
        except ValueError:
            # A non-ancestor or unavailable base cannot safely drive reuse. A bounded
            # full scan remains correct and records scan_mode=full in the new Snapshot.
            return None
        changed_paths = frozenset(
            path for change in changes for path in change.paths if path in git.tracked_paths
        )
        changed_files = self._workspace.discover(
            workspace_root=git.workspace_root,
            scan_roots=request.scan_roots,
            excluded_globs=excluded_globs,
            languages=languages,
            limits=request.limits,
            allowed_paths=changed_paths,
        )
        plan = self._incremental_scanner.plan(
            previous_artifact=base,
            changes=changes,
            changed_files=changed_files,
            profile=profile,
            current_tracked_paths=git.tracked_paths,
        )
        affected_files = self._workspace.discover(
            workspace_root=git.workspace_root,
            scan_roots=request.scan_roots,
            excluded_globs=excluded_globs,
            languages=languages,
            limits=request.limits,
            allowed_paths=frozenset(plan.affected_paths),
        )
        return self._incremental_scanner.scan(
            code_graph_snapshot_id=request.code_graph_snapshot_id,
            project_id=request.project_id,
            repository_id=request.repository_id,
            repository_revision=scope_commit_sha,
            scan_roots=request.scan_roots,
            profile=profile,
            previous_artifact=base,
            plan=plan,
            affected_files=affected_files,
            current_tracked_paths=git.tracked_paths,
        )

    def _incremental_base(
        self,
        *,
        request: CodeGraphBuildRequest,
        profile_ref: str,
        current_revision: str,
    ) -> dict[str, Any] | None:
        if not request.incremental:
            return None
        current = self._graph_repository.get_current(
            project_id=request.project_id,
            repository_id=request.repository_id,
        )
        if current is None:
            return None
        artifact = self._graph_repository.get(current.code_graph_snapshot_id)
        if artifact is None:
            return None
        if (
            artifact.get("scan_status") not in {"complete", "truncated"}
            or artifact.get("scan_roots") != list(request.scan_roots)
            or artifact.get("framework_profile_refs") != [profile_ref]
            or (
                artifact.get("repository_revision") == current_revision
                and artifact.get("code_graph_snapshot_id") == request.code_graph_snapshot_id
            )
        ):
            return None
        return artifact
