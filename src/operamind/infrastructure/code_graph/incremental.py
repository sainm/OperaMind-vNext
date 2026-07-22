"""Revision incremental Code Graph planning and snapshot reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from operamind.infrastructure.code_graph.framework import (
    SPECIALIZED_FRAMEWORK_EXTRACTORS,
    extract_framework_graph,
)
from operamind.infrastructure.code_graph.git import GitPathChange
from operamind.infrastructure.code_graph.java import (
    JavaDirectEdge,
    JavaFileExtraction,
    JavaLambdaExpression,
    JavaSymbol,
    JavaTreeSitterExtractor,
    JavaType,
    code_edge_id,
    code_file_id,
)
from operamind.infrastructure.code_graph.scanner import (
    _REQUIRED_EXTRACTOR_BY_LANGUAGE,
    _SEMANTIC_CODE_LANGUAGES,
    _SUPPORTED_EXTRACTORS,
    CodeGraphScanResult,
    _extract_properties,
    _extract_sql,
    _resolve_java_relations,
    _symbol_artifact_id,
)
from operamind.infrastructure.code_graph.workspace import DiscoveredCodeFile


@dataclass(frozen=True, slots=True)
class IncrementalScanPlan:
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    affected_paths: tuple[str, ...]
    declaration_changed: bool


class IncrementalCodeGraphScanner:
    """Reparse affected files and reuse immutable facts from the previous Snapshot."""

    def __init__(self) -> None:
        self._java = JavaTreeSitterExtractor()

    def plan(
        self,
        *,
        previous_artifact: dict[str, Any],
        changes: tuple[GitPathChange, ...],
        changed_files: tuple[DiscoveredCodeFile, ...],
        profile: dict[str, Any],
        current_tracked_paths: frozenset[str],
    ) -> IncrementalScanPlan:
        current_changed, removed = _changed_path_sets(changes)
        previous_files = _files_by_path(previous_artifact)
        old_touched = set(removed) | (set(current_changed) & set(previous_files))
        enabled = _enabled_extractors(profile)
        changed_extractions = {
            file.path: self._java.extract(
                file=file,
                profile_ref=_profile_ref(profile),
                enabled_extractors=enabled,
            )
            for file in changed_files
            if file.language == "java"
        }
        old_declarations = {
            (str(symbol["symbol_type"]), str(symbol["signature"]))
            for path in old_touched
            for symbol in cast(list[dict[str, Any]], previous_files[path]["symbols"])
        }
        new_declarations = {
            (symbol.symbol_type, symbol.signature)
            for extraction in changed_extractions.values()
            for symbol in extraction.symbols
        }
        path_identity_changed = any(change.status.startswith("R") for change in changes)
        declaration_changed = old_declarations != new_declarations or path_identity_changed
        affected = {file.path for file in changed_files} | (
            set(current_changed) & set(previous_files)
        )
        if declaration_changed:
            old_symbol_ids = {
                str(symbol["symbol_id"])
                for path in old_touched
                for symbol in cast(list[dict[str, Any]], previous_files[path]["symbols"])
            }
            new_type_names = {
                value.fqn
                for extraction in changed_extractions.values()
                for value in extraction.types
            }
            for edge in cast(list[dict[str, Any]], previous_artifact["edges"]):
                source_path = str(cast(dict[str, Any], edge["source_location"])["path"])
                target = str(edge["to_ref"])
                if (
                    target in old_symbol_ids
                    or edge["resolution_status"] == "unresolved"
                    or (
                        edge["edge_type"] == "imports"
                        and target.removeprefix("external:type:") in new_type_names
                    )
                ):
                    affected.add(source_path)
        if changes and enabled.intersection(SPECIALIZED_FRAMEWORK_EXTRACTORS):
            # Framework relations join facts across config, UI, controller, entity,
            # repository and SQL files. Until a persisted framework-fact ledger is
            # available, re-scan the approved tracked set instead of reusing stale
            # cross-file edges.
            affected = set(previous_files).intersection(current_tracked_paths) | {
                file.path for file in changed_files
            }
            declaration_changed = True
        affected &= set(current_tracked_paths)
        return IncrementalScanPlan(
            changed_paths=tuple(sorted(set(current_changed) | set(removed))),
            deleted_paths=tuple(sorted(removed)),
            affected_paths=tuple(sorted(affected)),
            declaration_changed=declaration_changed,
        )

    def scan(
        self,
        *,
        code_graph_snapshot_id: str,
        project_id: str,
        repository_id: str,
        repository_revision: str,
        scan_roots: tuple[str, ...],
        profile: dict[str, Any],
        previous_artifact: dict[str, Any],
        plan: IncrementalScanPlan,
        affected_files: tuple[DiscoveredCodeFile, ...],
        current_tracked_paths: frozenset[str],
    ) -> CodeGraphScanResult:
        enabled = _enabled_extractors(profile)
        profile_ref = _profile_ref(profile)
        previous_files = _files_by_path(previous_artifact)
        affected_by_path = {file.path: file for file in affected_files}
        reused_files = {
            path: value
            for path, value in previous_files.items()
            if path in current_tracked_paths and path not in set(plan.affected_paths)
        }
        diagnostics = _reused_path_diagnostics(previous_artifact, plan)
        symbols_by_path: dict[str, list[dict[str, object]]] = {
            path: [] for path in affected_by_path
        }
        java_extractions: list[JavaFileExtraction] = []
        direct_edges: list[JavaDirectEdge] = []
        for file in affected_files:
            if file.language == "java":
                extraction = self._java.extract(
                    file=file,
                    profile_ref=profile_ref,
                    enabled_extractors=enabled,
                )
                java_extractions.append(extraction)
                diagnostics.extend(extraction.diagnostics)
                symbols_by_path[file.path].extend(
                    symbol.to_artifact() for symbol in extraction.symbols
                )
                direct_edges.extend(extraction.direct_edges)
            elif file.language == "properties" and "config_key" in enabled:
                symbols, edges = _extract_properties(file)
                symbols_by_path[file.path].extend(symbols)
                direct_edges.extend(edges)
            elif file.language == "sql" and "sql_table" in enabled:
                symbols, edges = _extract_sql(file)
                symbols_by_path[file.path].extend(symbols)
                direct_edges.extend(edges)

        reused_symbols, reused_types = _reconstruct_java_semantics(
            reused_files=reused_files,
            previous_edges=cast(list[dict[str, Any]], previous_artifact["edges"]),
        )
        current_symbols = [
            symbol for extraction in java_extractions for symbol in extraction.symbols
        ]
        current_types = [value for extraction in java_extractions for value in extraction.types]
        current_lambdas: list[JavaLambdaExpression] = [
            value for extraction in java_extractions for value in extraction.lambda_expressions
        ]
        relations = [
            relation for extraction in java_extractions for relation in extraction.relations
        ]
        direct_edges.extend(
            _resolve_java_relations(
                relations=tuple(relations),
                symbols=tuple((*reused_symbols, *current_symbols)),
                types=tuple((*reused_types, *current_types)),
                role_by_file={
                    str(value["file_id"]): str(value["role"]) for value in reused_files.values()
                }
                | {code_file_id(file.path): file.role for file in affected_files},
                junit_enabled="junit_test" in enabled,
            )
        )
        framework = extract_framework_graph(
            files=affected_files,
            java_symbols=tuple(current_symbols),
            java_types=tuple(current_types),
            java_lambdas=tuple(current_lambdas),
            symbols_by_path=symbols_by_path,
            edges=tuple(direct_edges),
            enabled_extractors=enabled,
        )
        direct_edges = list(framework.edges)
        reused_edges = [
            _static_edge_artifact(edge)
            for edge in cast(list[dict[str, Any]], previous_artifact["edges"])
            if str(cast(dict[str, Any], edge["source_location"])["path"]) in reused_files
        ]
        new_edges = [_edge_artifact(edge, profile_ref) for edge in direct_edges]
        edge_by_id = {str(edge["edge_id"]): edge for edge in (*reused_edges, *new_edges)}
        file_artifacts = [dict(value) for value in reused_files.values()]
        file_artifacts.extend(
            {
                "file_id": code_file_id(file.path),
                "path": file.path,
                "language": file.language,
                "role": file.role,
                "content_hash": file.content_hash,
                "symbols": sorted(symbols_by_path[file.path], key=_symbol_artifact_id),
            }
            for file in affected_files
        )
        discovered_languages = frozenset(str(file["language"]) for file in file_artifacts)
        diagnostics.extend(
            f"unsupported_extractor:{value}" for value in sorted(enabled - _SUPPORTED_EXTRACTORS)
        )
        diagnostics.extend(
            f"required_extractor_missing:{language}:{extractor}"
            for language, extractor in sorted(_REQUIRED_EXTRACTOR_BY_LANGUAGE.items())
            if language in discovered_languages and extractor not in enabled
        )
        diagnostics.extend(
            f"language_extractor_not_implemented:{file['language']}"
            for file in file_artifacts
            if file["role"] in {"production", "test"}
            and file["language"] not in _SEMANTIC_CODE_LANGUAGES
        )
        markers = _framework_markers(
            profile=profile,
            affected_files=affected_files,
            reused_files=reused_files,
            reused_edges=reused_edges,
        )
        if not markers:
            diagnostics.append("framework_marker_not_found")
        if not file_artifacts:
            diagnostics.append("no_supported_files")
        diagnostics = sorted(set(diagnostics))
        artifact: dict[str, Any] = {
            "artifact_type": "CodeGraphSnapshot",
            "schema_version": "v1",
            "code_graph_snapshot_id": code_graph_snapshot_id,
            "project_id": project_id,
            "repository_id": repository_id,
            "repository_revision": repository_revision,
            "framework_profile_refs": [profile_ref],
            "scan_roots": list(scan_roots),
            "scan_status": "complete" if not diagnostics else "truncated",
            "scan_mode": "incremental",
            "base_code_graph_snapshot_id": previous_artifact["code_graph_snapshot_id"],
            "changed_paths": list(plan.changed_paths),
            "affected_paths": list(plan.affected_paths),
            "scanned_file_count": len(affected_files),
            "reused_file_count": len(reused_files),
            "framework_markers_found": list(markers),
            "diagnostics": diagnostics,
            "files": sorted(file_artifacts, key=lambda value: str(value["path"])),
            "edges": [edge_by_id[key] for key in sorted(edge_by_id)],
        }
        return CodeGraphScanResult(
            artifact=artifact,
            diagnostics=tuple(diagnostics),
            framework_markers_found=markers,
        )


def _changed_path_sets(
    changes: tuple[GitPathChange, ...],
) -> tuple[frozenset[str], frozenset[str]]:
    current: set[str] = set()
    removed: set[str] = set()
    for change in changes:
        kind = change.status[0]
        if kind == "D":
            removed.add(change.paths[0])
        elif kind == "R":
            removed.add(change.paths[0])
            current.add(change.paths[1])
        elif kind == "C":
            current.add(change.paths[1])
        else:
            current.add(change.paths[-1])
    return frozenset(current), frozenset(removed)


def _files_by_path(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(value["path"]): value for value in cast(list[dict[str, Any]], artifact["files"])}


def _enabled_extractors(profile: dict[str, Any]) -> frozenset[str]:
    return frozenset(str(value) for value in cast(list[object], profile["anchor_extractors"]))


def _profile_ref(profile: dict[str, Any]) -> str:
    return f"{profile['profile_id']}@{profile['profile_version']}"


def _reconstruct_java_semantics(
    *,
    reused_files: dict[str, dict[str, Any]],
    previous_edges: list[dict[str, Any]],
) -> tuple[list[JavaSymbol], list[JavaType]]:
    imports_by_path: dict[str, list[str]] = {}
    symbol_signature_by_id = {
        str(symbol["symbol_id"]): str(symbol["signature"])
        for file in reused_files.values()
        for symbol in cast(list[dict[str, Any]], file["symbols"])
    }
    for edge in previous_edges:
        if edge["edge_type"] != "imports":
            continue
        path = str(cast(dict[str, Any], edge["source_location"])["path"])
        if path not in reused_files:
            continue
        target = str(edge["to_ref"])
        imports_by_path.setdefault(path, []).append(
            target.removeprefix("external:type:")
            if target.startswith("external:type:")
            else symbol_signature_by_id.get(target, target)
        )
    symbols: list[JavaSymbol] = []
    types: list[JavaType] = []
    for path, file in reused_files.items():
        if file["language"] != "java":
            continue
        file_symbols = cast(list[dict[str, Any]], file["symbols"])
        type_signatures = [
            str(value["signature"])
            for value in file_symbols
            if value["symbol_type"] in {"annotation", "class", "enum", "interface", "record"}
        ]
        shallowest_type = min(type_signatures, key=lambda value: value.count("."), default="")
        package = shallowest_type.rsplit(".", maxsplit=1)[0] if "." in shallowest_type else ""
        for value in file_symbols:
            signature = str(value["signature"])
            symbol_type = str(value["symbol_type"])
            owner = signature.split("#", maxsplit=1)[0] if "#" in signature else None
            arity = (
                _signature_arity(signature) if symbol_type in {"method", "constructor"} else None
            )
            symbol = JavaSymbol(
                symbol_id=str(value["symbol_id"]),
                file_id=str(file["file_id"]),
                path=path,
                symbol_type=symbol_type,
                name=str(value["name"]),
                signature=signature,
                start_line=int(value["start_line"]),
                end_line=int(value["end_line"]),
                owner_type=owner,
                arity=arity,
                declared_type=(
                    str(value["declared_type"]) if value.get("declared_type") is not None else None
                ),
            )
            symbols.append(symbol)
            if symbol_type in {"annotation", "class", "enum", "interface", "record"}:
                fqn = signature
                types.append(
                    JavaType(
                        symbol_id=symbol.symbol_id,
                        file_id=symbol.file_id,
                        fqn=fqn,
                        simple_name=symbol.name,
                        package=package,
                        imports=tuple(sorted(set(imports_by_path.get(path, [])))),
                    )
                )
    return symbols, types


def _signature_arity(signature: str) -> int | None:
    if "(" not in signature or not signature.endswith(")"):
        return None
    parameters = signature.rsplit("(", maxsplit=1)[1][:-1]
    if not parameters:
        return 0
    depth = 0
    arity = 1
    for character in parameters:
        if character in "<[(":
            depth += 1
        elif character in ">])" and depth > 0:
            depth -= 1
        elif character == "," and depth == 0:
            arity += 1
    return arity


def _edge_artifact(edge: JavaDirectEdge, profile_ref: str) -> dict[str, object]:
    edge_id = code_edge_id(edge, profile_ref=profile_ref)
    return {
        "edge_id": edge_id,
        "edge_type": edge.edge_type,
        "from_ref": edge.from_ref,
        "to_ref": edge.to_ref,
        "resolution_status": edge.resolution_status,
        "confidence": edge.confidence,
        "extractor": edge.extractor,
        "profile_version": profile_ref,
        "provenance": "static",
        "evidence_refs": [],
        "source_location": {
            "path": edge.source_path,
            "start_line": edge.start_line,
            "end_line": edge.end_line,
        },
    }


def _static_edge_artifact(edge: dict[str, Any]) -> dict[str, Any]:
    value = dict(edge)
    value.setdefault("provenance", "static")
    value.setdefault("evidence_refs", [])
    return value


def _reused_path_diagnostics(
    previous_artifact: dict[str, Any], plan: IncrementalScanPlan
) -> list[str]:
    invalidated = set(plan.affected_paths) | set(plan.deleted_paths)
    return [
        str(value)
        for value in cast(list[object], previous_artifact["diagnostics"])
        if any(path in str(value) for path in _files_by_path(previous_artifact))
        and not any(path in str(value) for path in invalidated)
    ]


def _framework_markers(
    *,
    profile: dict[str, Any],
    affected_files: tuple[DiscoveredCodeFile, ...],
    reused_files: dict[str, dict[str, Any]],
    reused_edges: list[dict[str, Any]],
) -> tuple[str, ...]:
    semantic_text = "\n".join(
        [file.content.decode("utf-8", errors="replace") for file in affected_files]
        + [
            str(symbol["signature"])
            for file in reused_files.values()
            for symbol in cast(list[dict[str, Any]], file["symbols"])
        ]
        + [f"{edge['from_ref']} {edge['to_ref']}" for edge in reused_edges]
    )
    return tuple(
        sorted(
            str(marker)
            for marker in cast(list[object], profile["framework_markers"])
            if str(marker) in semantic_text
        )
    )
