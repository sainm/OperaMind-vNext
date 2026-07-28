"""Validate Copilot-proposed code scope and publish a deterministic Impact Report."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from psycopg import Connection

from operamind.application.code_graph_build import (
    CodeGraphBuildRequest,
    CodeGraphBuildService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import GitWorkspaceInspector
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    ImpactRepository,
    ProfileRepository,
    WebControlPlaneRepository,
)
from operamind.profiles import ProfileCatalog


class CopilotImpactService:
    """Turn a read-only Copilot proposal into Graph-validated Canonical impact evidence."""

    def __init__(self, *, connection: Connection[Any], repository_root: Path) -> None:
        root = repository_root.resolve()
        self._connection = connection
        self._contracts = ContractCatalog.load(root / "contracts")
        self._profiles = ProfileCatalog.load(root / "profiles")
        self._profile_repository = ProfileRepository(connection, self._profiles)
        self._artifacts = ArtifactRepository(connection, self._contracts)
        self._requests = WebControlPlaneRepository(connection, self._contracts)
        self._impacts = ImpactRepository(connection, self._contracts)

    def publish(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        change_request_id: str,
        coding_task_id: str,
        workspace_root: Path,
        source_document_snapshot_id: str,
        target_document_snapshot_id: str,
        search_index_build_id: str,
        document_change_refs: tuple[str, ...],
        code_scope: tuple[dict[str, Any], ...],
    ) -> dict[str, object]:
        if not code_scope:
            raise ValueError("Copilot code scope must not be empty")
        registration = self._requests.project_repository_registration(project_id)
        if registration is None:
            raise ValueError("Project has no unambiguous Repository registration")
        registered_root = Path(registration["workspace_root"]).resolve(strict=True)
        if workspace_root.resolve(strict=True) != registered_root:
            raise ValueError("Copilot code scope Workspace differs from Repository registration")
        git = GitWorkspaceInspector().inspect(registered_root)
        if git.remote_url != registration["remote_url"]:
            raise ValueError("Registered Repository remote differs from current Git Workspace")
        revision_id = self._requests.repository_revision_id(
            repository_id=registration["repository_id"],
            commit_sha=git.head_sha,
        )
        if revision_id is None:
            raise ValueError("Current Git revision is not registered")
        graph_id = _id(
            "copilot-code-graph",
            project_id,
            coding_task_id,
            git.head_sha,
        )
        graph, scan_roots, languages = self._build_graph(
            project_id=project_id,
            coding_task_id=coding_task_id,
            graph_id=graph_id,
            repository_id=registration["repository_id"],
            repository_revision_id=revision_id,
            workspace_root=registered_root,
        )
        normalized_scope = _validate_code_scope(
            code_scope,
            graph=graph,
            document_change_refs=document_change_refs,
            scan_roots=scan_roots,
            languages=languages,
        )
        context_id = _id("copilot-impact-context", project_id, coding_task_id)
        context = {
            "artifact_type": "CopilotImpactContext",
            "schema_version": "v1",
            "context_id": context_id,
            "coding_task_id": coding_task_id,
            "project_id": project_id,
            "analysis_case_id": analysis_case_id,
            "source_document_snapshot_id": source_document_snapshot_id,
            "target_document_snapshot_id": target_document_snapshot_id,
            "search_index_build_id": search_index_build_id,
            "document_change_refs": list(document_change_refs),
            "code_scope": normalized_scope,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        self._contracts.validate_artifact(context)
        self._artifacts.store(
            artifact_id=context_id,
            project_id=project_id,
            analysis_case_id=analysis_case_id,
            artifact=context,
        )
        impact_report_id = _id(
            "copilot-impact-report",
            project_id,
            analysis_case_id,
            coding_task_id,
        )
        items = [
            {
                "impact_item_id": _id(
                    "copilot-impact-item",
                    impact_report_id,
                    str(scope["target_path"]),
                ),
                "structured_change_refs": list(document_change_refs),
                "target_path": scope["target_path"],
                "target_symbols": scope["target_symbols"],
                "impact_level": "high",
                "impact_score": 1.0,
                "recommended_action": scope["recommended_action"],
                "rationale": scope["rationale"],
                "evidence_refs": [context_id, *document_change_refs],
                "graph_path_refs": [],
                "test_file_refs": scope["test_file_refs"],
                "requires_confirmation": False,
                "unknowns": [],
            }
            for scope in normalized_scope
        ]
        report = {
            "artifact_type": "ImpactReport",
            "schema_version": "v1",
            "impact_report_id": impact_report_id,
            "analysis_case_id": analysis_case_id,
            "project_id": project_id,
            "document_snapshot_id": target_document_snapshot_id,
            "context_package_id": context_id,
            "code_graph_snapshot_id": graph_id,
            "repository_revision": git.head_sha,
            "analysis_policy_version": "copilot-graph-validated-v1",
            "status": "awaiting_confirmation",
            "summary": f"VS Code GitHub Copilot impact scope for {change_request_id}",
            "items": items,
            "ui_impact_status": (
                "impacted"
                if any(bool(scope["ui_impact"]) for scope in normalized_scope)
                else "not_impacted"
            ),
            "required_ui_scenario_refs": [],
            "blocking_unknowns": [],
        }
        self._contracts.validate_artifact(report)
        publication = self._impacts.publish_report(
            artifact=report,
            repository_id=registration["repository_id"],
            repository_revision_id=revision_id,
        )
        return {
            "created": publication.created,
            "impact_report_id": impact_report_id,
            "context_id": context_id,
            "code_graph_snapshot_id": graph_id,
            "code_scope": normalized_scope,
        }

    def _build_graph(
        self,
        *,
        project_id: str,
        coding_task_id: str,
        graph_id: str,
        repository_id: str,
        repository_revision_id: str,
        workspace_root: Path,
    ) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
        bindings = self._profile_repository.list_active_by_type(
            project_id=project_id,
            profile_type="CodeFrameworkProfile",
        )
        if len(bindings) != 1:
            raise ValueError(
                "Copilot impact analysis requires exactly one active CodeFrameworkProfile "
                f"(found {len(bindings)})"
            )
        binding = bindings[0]
        scan_roots = tuple(
            str(value)
            for value in cast(list[object], binding.profile["default_scan_roots"])
        )
        languages = tuple(
            str(value) for value in cast(list[object], binding.profile["languages"])
        )
        result = CodeGraphBuildService(
            connection=self._connection,
            contracts=self._contracts,
            profiles=self._profiles,
        ).run(
            CodeGraphBuildRequest(
                code_graph_snapshot_id=graph_id,
                project_id=project_id,
                repository_id=repository_id,
                repository_revision_id=repository_revision_id,
                workspace_root=workspace_root,
                scan_roots=scan_roots,
                profile_version_id=binding.profile_version_id,
                profile_binding_key=binding.binding_key,
                profile_activation_event_id=_id(
                    "copilot-code-profile-activation",
                    project_id,
                    coding_task_id,
                ),
                activated_by="automation:operamind",
                activation_reason="Build Graph for Graph-validated Copilot impact scope",
            ),
            profile=binding.profile,
        )
        graph = result.scan.artifact
        if graph.get("scan_status") != "complete":
            raise ValueError("Code Graph is not complete")
        return graph, scan_roots, languages


def _validate_code_scope(
    values: tuple[dict[str, Any], ...],
    *,
    graph: dict[str, Any],
    document_change_refs: tuple[str, ...],
    scan_roots: tuple[str, ...] = ("src", "tests"),
    languages: tuple[str, ...] = (
        "java",
        "javascript",
        "typescript",
        "python",
        "xml",
        "sql",
        "properties",
    ),
) -> list[dict[str, object]]:
    if not document_change_refs:
        raise ValueError("Copilot impact scope has no document change evidence")
    files = {
        str(file["path"]): file
        for file in cast(list[dict[str, Any]], graph["files"])
    }
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in values:
        path = _safe_relative_path(str(value.get("target_path") or ""))
        if path in seen:
            raise ValueError(f"Copilot code scope contains a duplicate path: {path}")
        seen.add(path)
        file = files.get(path)
        action = str(value.get("recommended_action") or "")
        if action not in {"modify", "add", "delete", "review_only"}:
            raise ValueError("Copilot code scope action is not allowed")
        if action == "add":
            if file is not None:
                raise ValueError(f"Copilot add path already exists in the Code Graph: {path}")
            _validate_new_code_path(path, scan_roots=scan_roots, languages=languages)
        elif file is None:
            raise ValueError(f"Copilot code scope path is absent from the Code Graph: {path}")
        symbols = tuple(str(item) for item in cast(list[object], value.get("target_symbols", [])))
        if action == "add" and symbols:
            raise ValueError("Copilot add scope cannot claim symbols absent from the Code Graph")
        if file is not None:
            available_symbols = {
                str(symbol[field])
                for symbol in cast(list[dict[str, Any]], file.get("symbols", []))
                for field in ("symbol_id", "name", "signature")
                if symbol.get(field)
            }
            if symbols and not set(symbols).issubset(available_symbols):
                raise ValueError(f"Copilot target symbols are absent from Code Graph file: {path}")
        test_refs = tuple(
            _safe_relative_path(str(item))
            for item in cast(list[object], value.get("test_file_refs", []))
        )
        if not test_refs:
            raise ValueError(f"Copilot code scope has no test files: {path}")
        for test_ref in test_refs:
            graph_test = files.get(test_ref)
            if graph_test is not None and graph_test.get("role") != "test":
                raise ValueError(f"Copilot test reference is not a Graph test file: {test_ref}")
            if graph_test is None:
                if not _looks_like_test_path(test_ref):
                    raise ValueError(f"Copilot planned test path is unsafe: {test_ref}")
                _validate_new_code_path(
                    test_ref,
                    scan_roots=scan_roots,
                    languages=languages,
                )
        rationale = str(value.get("rationale") or "").strip()
        if not rationale:
            raise ValueError("Copilot code scope rationale must not be blank")
        ui_impact = value.get("ui_impact")
        if not isinstance(ui_impact, bool):
            raise ValueError("Copilot code scope ui_impact must be boolean")
        normalized.append(
            {
                "target_path": path,
                "target_symbols": list(symbols),
                "recommended_action": action,
                "test_file_refs": list(test_refs),
                "rationale": rationale,
                "ui_impact": ui_impact,
            }
        )
    return normalized


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value.strip()
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or path.as_posix() != value
    ):
        raise ValueError(f"Copilot code scope path is unsafe: {value}")
    return value


def _looks_like_test_path(value: str) -> bool:
    path = PurePosixPath(value)
    parts = {part.casefold() for part in path.parts}
    name = path.name.casefold()
    return bool(path.suffix) and (
        "test" in parts
        or "tests" in parts
        or "test" in name
        or "spec" in name
    )


_LANGUAGE_SUFFIXES = {
    "java": {".java"},
    "javascript": {".js", ".jsx", ".mjs", ".cjs"},
    "typescript": {".ts", ".tsx", ".mts", ".cts"},
    "python": {".py"},
    "kotlin": {".kt", ".kts"},
    "xml": {".xml", ".xhtml", ".jsp", ".jspx"},
    "sql": {".sql"},
    "properties": {".properties"},
}


def _validate_new_code_path(
    value: str,
    *,
    scan_roots: tuple[str, ...],
    languages: tuple[str, ...],
) -> None:
    path = PurePosixPath(value)
    roots = tuple(PurePosixPath(_safe_relative_path(root)) for root in scan_roots)
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError(f"Copilot planned path is outside configured scan roots: {value}")
    suffixes = {
        suffix
        for language in languages
        for suffix in _LANGUAGE_SUFFIXES.get(language.casefold(), set())
    }
    if path.suffix.casefold() not in suffixes:
        raise ValueError(f"Copilot planned path has an unsupported language suffix: {value}")


def _id(prefix: str, *values: str) -> str:
    material = "\0".join(values).encode()
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"
