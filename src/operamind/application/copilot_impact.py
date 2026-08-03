"""Validate Copilot-proposed code scope and publish a deterministic Impact Report."""

from __future__ import annotations

import hashlib
import json
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
        actor: str = "mcp:github-copilot",
        provider_id: str = "vscode_github_copilot",
    ) -> dict[str, object]:
        if not code_scope:
            raise ValueError("Copilot code scope must not be empty")
        if not actor.strip() or not provider_id.strip():
            raise ValueError("Impact scope provenance must not be blank")
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
        request_record = self._requests.get_change_request(change_request_id)
        request_artifact = cast(dict[str, Any], request_record["artifact"])
        ui_impacted = any(bool(scope["ui_impact"]) for scope in normalized_scope)
        required_ui_scenario_refs = _required_ui_scenario_refs(
            change_request_id=change_request_id,
            business_rules=cast(list[object], request_artifact.get("business_rules", [])),
        )
        scope_material = json.dumps(
            normalized_scope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        context_id = _id(
            "copilot-impact-context",
            project_id,
            coding_task_id,
            git.head_sha,
            provider_id,
            scope_material,
        )
        context = {
            "artifact_type": "CopilotImpactContext",
            "schema_version": "v2",
            "context_id": context_id,
            "coding_task_id": coding_task_id,
            "project_id": project_id,
            "analysis_case_id": analysis_case_id,
            "source_document_snapshot_id": source_document_snapshot_id,
            "target_document_snapshot_id": target_document_snapshot_id,
            "search_index_build_id": search_index_build_id,
            "document_change_refs": list(document_change_refs),
            "code_scope": normalized_scope,
            "generated_by": actor,
            "generator_provider": provider_id,
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
            git.head_sha,
            provider_id,
            scope_material,
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
                "impact_level": (
                    "high" if scope["related_code_paths"] else "medium"
                ),
                "impact_score": 1.0 if scope["related_code_paths"] else 0.7,
                "recommended_action": scope["recommended_action"],
                "rationale": scope["rationale"],
                "evidence_refs": [context_id, *document_change_refs],
                "graph_path_refs": scope["graph_path_refs"],
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
            "summary": f"AI impact scope ({provider_id}) for {change_request_id}",
            "items": items,
            "ui_impact_status": "impacted" if ui_impacted else "not_impacted",
            "required_ui_scenario_refs": required_ui_scenario_refs,
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
            "generated_by": actor,
            "generator_provider": provider_id,
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
            if symbols and (
                not available_symbols or _uses_file_level_scope(path)
            ):
                # Some supported source files (for example HTML templates and
                # MyBatis XML) either do not expose symbol nodes or expose
                # parser-internal symbols that Copilot cannot address
                # reliably.  A file-level authorization is still bounded by
                # target_path, so discard pseudo-symbols for these formats.
                symbols = ()
            elif symbols and not set(symbols).issubset(available_symbols):
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
                "related_code_paths": [],
                "graph_path_refs": [],
            }
        )
    _validate_impact_closure(normalized, graph=graph)
    return normalized


_IMPACT_EDGE_TYPES = {
    "calls",
    "implements",
    "maps_to",
    "reads",
    "writes",
    "tests",
    "navigates_to",
}


def _validate_impact_closure(
    scopes: list[dict[str, object]], *, graph: dict[str, Any]
) -> None:
    """Complete the bounded scope with a resolved two-hop review-only closure."""

    files = cast(list[dict[str, Any]], graph.get("files", []))
    path_by_ref: dict[str, str] = {}
    role_by_path: dict[str, str] = {}
    for file in files:
        path = str(file["path"])
        role_by_path[path] = str(file.get("role") or "unknown")
        if file.get("file_id"):
            path_by_ref[str(file["file_id"])] = path
        for symbol in cast(list[dict[str, Any]], file.get("symbols", [])):
            path_by_ref[str(symbol["symbol_id"])] = path

    adjacency: dict[str, list[tuple[str, str]]] = {}
    edges = cast(list[dict[str, Any]], graph.get("edges", []))
    for edge in edges:
        if (
            edge.get("resolution_status") != "resolved"
            or edge.get("confidence") == "low"
            or edge.get("edge_type") not in _IMPACT_EDGE_TYPES
        ):
            continue
        source = path_by_ref.get(str(edge.get("from_ref") or ""))
        target = path_by_ref.get(str(edge.get("to_ref") or ""))
        if source is None or target is None or source == target:
            continue
        edge_id = str(edge.get("edge_id") or "")
        if not edge_id:
            continue
        adjacency.setdefault(source, []).append((target, edge_id))
        adjacency.setdefault(target, []).append((source, edge_id))

    submitted_paths = {str(scope["target_path"]) for scope in scopes}
    submitted_tests = {
        str(path)
        for scope in scopes
        for path in cast(list[object], scope["test_file_refs"])
    }
    missing_code: set[str] = set()
    graph_refs_by_path: dict[str, set[str]] = {}
    missing_tests: set[str] = set()
    actionable_scopes = [
        scope
        for scope in scopes
        if scope["recommended_action"] in {"modify", "add", "delete"}
    ]
    for scope in actionable_scopes:
        seed = str(scope["target_path"])
        if seed not in role_by_path:
            continue
        visited = {seed}
        frontier = {seed}
        graph_refs: set[str] = set()
        for _depth in range(2):
            next_frontier: set[str] = set()
            for current in sorted(frontier):
                for related, edge_id in adjacency.get(current, []):
                    graph_refs.add(edge_id)
                    if related not in visited:
                        visited.add(related)
                        next_frontier.add(related)
            frontier = next_frontier
        related_code = sorted(
            path
            for path in visited - {seed}
            if role_by_path.get(path) in {"production", "config", "migration", "contract"}
        )
        related_tests = sorted(
            path for path in visited if role_by_path.get(path) == "test"
        )
        scope["related_code_paths"] = related_code
        scope["graph_path_refs"] = sorted(graph_refs)
        for related_path in related_code:
            if related_path not in submitted_paths:
                missing_code.add(related_path)
                graph_refs_by_path.setdefault(related_path, set()).update(graph_refs)
        missing_tests.update(set(related_tests) - submitted_tests)
    if missing_tests:
        raise ValueError(
            "Copilot code scope does not cover the resolved Code Graph impact closure: "
            f"missing test paths={sorted(missing_tests)}"
        )
    shared_test_refs = sorted(submitted_tests)
    for path in sorted(missing_code):
        scopes.append(
            {
                "target_path": path,
                "target_symbols": [],
                "recommended_action": "review_only",
                "test_file_refs": shared_test_refs,
                "rationale": (
                    "OperaMind added this file as a review-only member of the "
                    "resolved two-hop Code Graph impact closure."
                ),
                "ui_impact": _is_ui_path(path),
                "related_code_paths": [],
                "graph_path_refs": sorted(graph_refs_by_path.get(path, set())),
            }
        )


def _is_ui_path(value: str) -> bool:
    return PurePosixPath(value).suffix.casefold() in {
        ".css",
        ".htm",
        ".html",
        ".js",
        ".jsp",
        ".jspx",
        ".ts",
        ".tsx",
        ".xhtml",
    }


def _uses_file_level_scope(value: str) -> bool:
    return PurePosixPath(value).suffix.casefold() in {
        ".css",
        ".gradle",
        ".htm",
        ".html",
        ".jsp",
        ".jspx",
        ".properties",
        ".sql",
        ".xhtml",
        ".xml",
    }


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
    "css": {".css"},
    "gradle": {".gradle"},
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


def _required_ui_scenario_refs(
    *, change_request_id: str, business_rules: list[object]
) -> list[str]:
    """Create stable UI scenario identities before execution authority is issued."""

    refs: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(business_rules, start=1):
        if isinstance(value, dict):
            rule_id = str(value.get("business_rule_id") or value.get("text") or "").strip()
        else:
            rule_id = str(value).strip()
        if not rule_id or rule_id in seen:
            continue
        seen.add(rule_id)
        refs.append(_id("ui-scenario", change_request_id, str(index), rule_id))
    return refs or [_id("ui-scenario", change_request_id, "end-to-end")]
