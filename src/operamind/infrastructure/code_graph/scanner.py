"""Profile-driven CodeGraphSnapshot construction from bounded local files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, cast

from operamind.infrastructure.code_graph.framework import extract_framework_graph
from operamind.infrastructure.code_graph.java import (
    JavaDirectEdge,
    JavaFileExtraction,
    JavaLambdaExpression,
    JavaRelation,
    JavaSymbol,
    JavaTreeSitterExtractor,
    JavaType,
    code_edge_id,
    code_file_id,
)
from operamind.infrastructure.code_graph.workspace import DiscoveredCodeFile


@dataclass(frozen=True, slots=True)
class CodeGraphScanResult:
    """Contract-ready graph plus non-persisted scanner diagnostics."""

    artifact: dict[str, Any]
    diagnostics: tuple[str, ...]
    framework_markers_found: tuple[str, ...]


class CodeGraphScanner:
    """Extract deterministic graph structure and keep ambiguous relations unresolved."""

    def __init__(self) -> None:
        self._java = JavaTreeSitterExtractor()

    def scan(
        self,
        *,
        code_graph_snapshot_id: str,
        project_id: str,
        repository_id: str,
        repository_revision: str,
        scan_roots: tuple[str, ...],
        profile: dict[str, Any],
        files: tuple[DiscoveredCodeFile, ...],
    ) -> CodeGraphScanResult:
        """Build a v1 Artifact without reading paths beyond the supplied file set."""

        required = (
            code_graph_snapshot_id,
            project_id,
            repository_id,
            repository_revision,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Code Graph scan identity fields must not be blank")
        if profile.get("profile_type") != "CodeFrameworkProfile":
            raise ValueError("Code Graph scan requires a CodeFrameworkProfile")
        profile_ref = f"{profile['profile_id']}@{profile['profile_version']}"
        enabled_extractors = frozenset(
            str(value) for value in cast(list[object], profile["anchor_extractors"])
        )
        diagnostics = [
            f"unsupported_extractor:{value}"
            for value in sorted(enabled_extractors - _SUPPORTED_EXTRACTORS)
        ]
        discovered_languages = frozenset(file.language for file in files)
        diagnostics.extend(
            f"required_extractor_missing:{language}:{extractor}"
            for language, extractor in sorted(_REQUIRED_EXTRACTOR_BY_LANGUAGE.items())
            if language in discovered_languages and extractor not in enabled_extractors
        )
        diagnostics.extend(
            f"language_extractor_not_implemented:{language}"
            for language in sorted(
                {
                    file.language
                    for file in files
                    if file.role in {"production", "test"}
                    and file.language not in _SEMANTIC_CODE_LANGUAGES
                }
            )
        )
        all_text = "\n".join(file.content.decode("utf-8", errors="replace") for file in files)
        marker_values = tuple(
            str(value) for value in cast(list[object], profile["framework_markers"])
        )
        markers_found = tuple(sorted(marker for marker in marker_values if marker in all_text))
        if not markers_found:
            diagnostics.append("framework_marker_not_found")
        if not files:
            diagnostics.append("no_supported_files")

        java_extractions: list[JavaFileExtraction] = []
        symbols_by_path: dict[str, list[dict[str, object]]] = {file.path: [] for file in files}
        all_java_symbols: list[JavaSymbol] = []
        all_java_types: list[JavaType] = []
        all_java_lambdas: list[JavaLambdaExpression] = []
        relations: list[JavaRelation] = []
        direct_edges: list[JavaDirectEdge] = []
        for file in files:
            if file.language == "java":
                extraction = self._java.extract(
                    file=file,
                    profile_ref=profile_ref,
                    enabled_extractors=enabled_extractors,
                )
                java_extractions.append(extraction)
                all_java_symbols.extend(extraction.symbols)
                all_java_types.extend(extraction.types)
                all_java_lambdas.extend(extraction.lambda_expressions)
                relations.extend(extraction.relations)
                direct_edges.extend(extraction.direct_edges)
                diagnostics.extend(extraction.diagnostics)
                symbols_by_path[file.path].extend(
                    symbol.to_artifact() for symbol in extraction.symbols
                )
            if file.language == "properties" and "config_key" in enabled_extractors:
                lexical_symbols, lexical_edges = _extract_properties(file)
                symbols_by_path[file.path].extend(lexical_symbols)
                direct_edges.extend(lexical_edges)
            if file.language == "sql" and "sql_table" in enabled_extractors:
                lexical_symbols, lexical_edges = _extract_sql(file)
                symbols_by_path[file.path].extend(lexical_symbols)
                direct_edges.extend(lexical_edges)

        resolved_edges = list(direct_edges)
        resolved_edges.extend(
            _resolve_java_relations(
                relations=tuple(relations),
                symbols=tuple(all_java_symbols),
                types=tuple(all_java_types),
                role_by_file={code_file_id(file.path): file.role for file in files},
                junit_enabled="junit_test" in enabled_extractors,
            )
        )
        framework = extract_framework_graph(
            files=files,
            java_symbols=tuple(all_java_symbols),
            java_types=tuple(all_java_types),
            java_lambdas=tuple(all_java_lambdas),
            symbols_by_path=symbols_by_path,
            edges=tuple(resolved_edges),
            enabled_extractors=enabled_extractors,
        )
        resolved_edges = list(framework.edges)
        edge_artifacts: dict[str, dict[str, object]] = {}
        for edge in resolved_edges:
            edge_id = code_edge_id(edge, profile_ref=profile_ref)
            edge_artifacts[edge_id] = {
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
        file_artifacts: list[dict[str, Any]] = []
        for file in files:
            file_artifacts.append(
                {
                    "file_id": code_file_id(file.path),
                    "path": file.path,
                    "language": file.language,
                    "role": file.role,
                    "content_hash": file.content_hash,
                    "symbols": sorted(symbols_by_path[file.path], key=_symbol_artifact_id),
                }
            )
        scan_status = "complete" if not diagnostics else "truncated"
        artifact: dict[str, Any] = {
            "artifact_type": "CodeGraphSnapshot",
            "schema_version": "v1",
            "code_graph_snapshot_id": code_graph_snapshot_id,
            "project_id": project_id,
            "repository_id": repository_id,
            "repository_revision": repository_revision,
            "framework_profile_refs": [profile_ref],
            "scan_roots": list(scan_roots),
            "scan_status": scan_status,
            "scan_mode": "full",
            "changed_paths": [],
            "affected_paths": [file.path for file in files],
            "scanned_file_count": len(files),
            "reused_file_count": 0,
            "framework_markers_found": list(markers_found),
            "diagnostics": sorted(set(diagnostics)),
            "files": file_artifacts,
            "edges": [edge_artifacts[key] for key in sorted(edge_artifacts)],
        }
        return CodeGraphScanResult(
            artifact=artifact,
            diagnostics=tuple(sorted(set(diagnostics))),
            framework_markers_found=markers_found,
        )


_SUPPORTED_EXTRACTORS = frozenset(
    {
        "config_key",
        "java_field_access",
        "java_symbol",
        "junit_test",
        "spring_config_binding",
        "spring_data_access",
        "spring_endpoint",
        "sql_table",
        "web_ui_route",
    }
)
_REQUIRED_EXTRACTOR_BY_LANGUAGE = {
    "javascript": "web_ui_route",
    "java": "java_symbol",
    "properties": "config_key",
    "sql": "sql_table",
    "xml": "web_ui_route",
}
_SEMANTIC_CODE_LANGUAGES = frozenset({"java", "javascript", "properties", "sql", "xml"})
_SQL_TABLE = re.compile(
    r"\b(?P<operation>CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|INSERT\s+INTO|UPDATE|FROM|JOIN)\s+"
    r"(?P<table>[A-Za-z_][A-Za-z0-9_.$\"]*)",
    re.IGNORECASE,
)
_NEW_OBJECT = re.compile(r"\bnew\s+([A-Za-z_$][A-Za-z0-9_.$]*)")


def _resolve_java_relations(
    *,
    relations: tuple[JavaRelation, ...],
    symbols: tuple[JavaSymbol, ...],
    types: tuple[JavaType, ...],
    role_by_file: dict[str, str],
    junit_enabled: bool,
) -> tuple[JavaDirectEdge, ...]:
    types_by_fqn = {value.fqn: value for value in types}
    types_by_simple: dict[str, list[JavaType]] = {}
    for value in types:
        types_by_simple.setdefault(value.simple_name, []).append(value)
    methods_by_owner_name_arity: dict[tuple[str, str, int], list[JavaSymbol]] = {}
    fields_by_owner_name: dict[tuple[str, str], JavaSymbol] = {}
    symbol_by_id = {symbol.symbol_id: symbol for symbol in symbols}
    for symbol in symbols:
        if symbol.owner_type is None:
            continue
        if symbol.symbol_type in {"method", "constructor"} and symbol.arity is not None:
            methods_by_owner_name_arity.setdefault(
                (symbol.owner_type, symbol.name, symbol.arity), []
            ).append(symbol)
        if symbol.symbol_type == "field":
            fields_by_owner_name[(symbol.owner_type, symbol.name)] = symbol

    edges: list[JavaDirectEdge] = []
    for relation in relations:
        target_ref: str
        resolution_status: str
        confidence: str
        edge_type = relation.relation_kind
        if relation.relation_kind == "import":
            target_type = types_by_fqn.get(relation.target)
            target_ref = (
                target_type.symbol_id if target_type else f"external:type:{relation.target}"
            )
            resolution_status = "resolved" if target_type else "external"
            confidence = "high"
            edge_type = "imports"
        elif relation.relation_kind == "implements":
            owner = _owner_type(relation.owner_type, types_by_fqn)
            target_type = _resolve_type(
                relation.target,
                owner=owner,
                types_by_fqn=types_by_fqn,
                types_by_simple=types_by_simple,
            )
            target_ref = (
                target_type.symbol_id if target_type else f"external:type:{relation.target}"
            )
            resolution_status = "resolved" if target_type else "external"
            confidence = "high" if target_type else "medium"
            edge_type = "implements"
        elif relation.relation_kind == "call":
            target_method, external_call = _resolve_call(
                relation,
                types_by_fqn=types_by_fqn,
                types_by_simple=types_by_simple,
                methods_by_owner_name_arity=methods_by_owner_name_arity,
                fields_by_owner_name=fields_by_owner_name,
            )
            if target_method is not None:
                target_ref = target_method.symbol_id
                resolution_status = "resolved"
                confidence = "high"
            elif external_call:
                target_ref = f"external:call:{relation.target}"
                resolution_status = "external"
                confidence = "medium"
            else:
                target_ref = f"unresolved:call:{relation.target}"
                resolution_status = "unresolved"
                confidence = "low"
            edge_type = "calls"
        else:
            raise ValueError(f"Unsupported Java relation kind: {relation.relation_kind}")
        edge = JavaDirectEdge(
            edge_type=edge_type,
            from_ref=relation.from_ref,
            to_ref=target_ref,
            resolution_status=resolution_status,
            confidence=confidence,
            extractor=relation.extractor,
            source_path=relation.source_path,
            start_line=relation.start_line,
            end_line=relation.end_line,
        )
        edges.append(edge)
        if (
            junit_enabled
            and relation.relation_kind == "call"
            and relation.test_source
            and resolution_status == "resolved"
        ):
            source = symbol_by_id.get(relation.from_ref)
            target = symbol_by_id.get(target_ref)
            if (
                source is not None
                and target is not None
                and role_by_file.get(source.file_id) == "test"
                and role_by_file.get(target.file_id) != "test"
            ):
                edges.append(
                    JavaDirectEdge(
                        edge_type="tests",
                        from_ref=source.symbol_id,
                        to_ref=target.symbol_id,
                        resolution_status="resolved",
                        confidence="high",
                        extractor="junit_test",
                        source_path=relation.source_path,
                        start_line=relation.start_line,
                        end_line=relation.end_line,
                    )
                )
    return tuple(edges)


def _resolve_call(
    relation: JavaRelation,
    *,
    types_by_fqn: dict[str, JavaType],
    types_by_simple: dict[str, list[JavaType]],
    methods_by_owner_name_arity: dict[tuple[str, str, int], list[JavaSymbol]],
    fields_by_owner_name: dict[tuple[str, str], JavaSymbol],
) -> tuple[JavaSymbol | None, bool]:
    if relation.owner_type is None or relation.method_name is None or relation.arity is None:
        return None, False
    owner = types_by_fqn.get(relation.owner_type)
    if owner is None:
        return None, False
    target_owner, external_receiver = _resolve_receiver_owner(
        relation.object_name,
        declared_root_type=relation.receiver_type,
        owner=owner,
        types_by_fqn=types_by_fqn,
        types_by_simple=types_by_simple,
        methods_by_owner_name_arity=methods_by_owner_name_arity,
        fields_by_owner_name=fields_by_owner_name,
    )
    if target_owner is None:
        return None, external_receiver
    candidates = methods_by_owner_name_arity.get(
        (target_owner.fqn, relation.method_name, relation.arity), []
    )
    return (candidates[0], False) if len(candidates) == 1 else (None, False)


def _resolve_receiver_owner(
    expression: str | None,
    *,
    declared_root_type: str | None,
    owner: JavaType,
    types_by_fqn: dict[str, JavaType],
    types_by_simple: dict[str, list[JavaType]],
    methods_by_owner_name_arity: dict[tuple[str, str, int], list[JavaSymbol]],
    fields_by_owner_name: dict[tuple[str, str], JavaSymbol],
) -> tuple[JavaType | None, bool]:
    """Resolve an explicit receiver or deterministic local method-return chain."""

    if expression is None or expression in {"this", "super"}:
        return owner, False
    value = expression.strip()
    if _is_java_literal(value):
        return None, True
    new_match = _NEW_OBJECT.search(value) if value.startswith("new ") else None
    if new_match is not None:
        return _resolve_declared_owner(
            new_match.group(1),
            owner=owner,
            types_by_fqn=types_by_fqn,
            types_by_simple=types_by_simple,
        )
    invocation = _split_trailing_invocation(value)
    if invocation is not None:
        base, method_name, arity = invocation
        base_owner, base_external = _resolve_receiver_owner(
            base,
            declared_root_type=declared_root_type,
            owner=owner,
            types_by_fqn=types_by_fqn,
            types_by_simple=types_by_simple,
            methods_by_owner_name_arity=methods_by_owner_name_arity,
            fields_by_owner_name=fields_by_owner_name,
        )
        if base_external:
            return None, True
        if base_owner is None:
            return None, False
        candidates = methods_by_owner_name_arity.get((base_owner.fqn, method_name, arity), [])
        if len(candidates) != 1 or candidates[0].declared_type is None:
            return None, False
        return _resolve_declared_owner(
            candidates[0].declared_type,
            owner=base_owner,
            types_by_fqn=types_by_fqn,
            types_by_simple=types_by_simple,
        )
    if value.startswith("this.") and re.fullmatch(r"this\.[A-Za-z_$][A-Za-z0-9_$]*", value):
        field = fields_by_owner_name.get((owner.fqn, value.removeprefix("this.")))
        type_name = field.declared_type if field is not None else None
    elif re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", value):
        field = fields_by_owner_name.get((owner.fqn, value))
        type_name = (
            declared_root_type
            or (field.declared_type if field is not None else None)
            or (value if value[:1].isupper() else None)
        )
    else:
        type_name = value if value.rsplit(".", maxsplit=1)[-1][:1].isupper() else None
    return _resolve_declared_owner(
        type_name,
        owner=owner,
        types_by_fqn=types_by_fqn,
        types_by_simple=types_by_simple,
    )


def _resolve_declared_owner(
    type_name: str | None,
    *,
    owner: JavaType,
    types_by_fqn: dict[str, JavaType],
    types_by_simple: dict[str, list[JavaType]],
) -> tuple[JavaType | None, bool]:
    if type_name is None:
        return None, False
    target = _resolve_type(
        type_name,
        owner=owner,
        types_by_fqn=types_by_fqn,
        types_by_simple=types_by_simple,
    )
    if target is not None:
        return target, False
    return None, _is_explicit_external_type(
        type_name,
        owner=owner,
        types_by_fqn=types_by_fqn,
        types_by_simple=types_by_simple,
    )


def _split_trailing_invocation(value: str) -> tuple[str, str, int] | None:
    if not value.endswith(")"):
        return None
    depth = 0
    opening = -1
    quote: str | None = None
    escaped = False
    for index in range(len(value) - 1, -1, -1):
        character = value[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == ")":
            depth += 1
        elif character == "(":
            depth -= 1
            if depth == 0:
                opening = index
                break
    if opening < 0:
        return None
    prefix = value[:opening].strip()
    match = re.fullmatch(r"(?P<base>.+)\.(?P<method>[A-Za-z_$][A-Za-z0-9_$]*)", prefix)
    if match is None:
        return None
    return match.group("base"), match.group("method"), _expression_arity(value[opening + 1 : -1])


def _expression_arity(arguments: str) -> int:
    if not arguments.strip():
        return 0
    depth = 0
    quote: str | None = None
    escaped = False
    count = 1
    for character in arguments:
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}" and depth > 0:
            depth -= 1
        elif character == "," and depth == 0:
            count += 1
    return count


def _is_java_literal(value: str) -> bool:
    return bool(
        value.startswith(('"', "'"))
        or value in {"true", "false", "null"}
        or re.fullmatch(r"[-+]?\d+(?:\.\d+)?[dDfFlL]?", value)
    )


def _is_explicit_external_type(
    value: str | None,
    *,
    owner: JavaType,
    types_by_fqn: dict[str, JavaType],
    types_by_simple: dict[str, list[JavaType]],
) -> bool:
    """Classify only receivers whose declared/imported type is outside this graph."""

    if value is None:
        return False
    normalized = re.sub(r"<.*>", "", value).removesuffix("[]").strip()
    if (
        not normalized
        or _resolve_type(
            normalized,
            owner=owner,
            types_by_fqn=types_by_fqn,
            types_by_simple=types_by_simple,
        )
        is not None
    ):
        return False
    simple = normalized.rsplit(".", maxsplit=1)[-1]
    if simple in _JAVA_LANG_TYPES:
        return True
    if "." in normalized:
        return True
    return any(
        import_name.rsplit(".", maxsplit=1)[-1] == simple and import_name not in types_by_fqn
        for import_name in owner.imports
    )


_JAVA_LANG_TYPES = frozenset(
    {
        "Boolean",
        "Byte",
        "Character",
        "Class",
        "Double",
        "Enum",
        "Float",
        "Integer",
        "Long",
        "Math",
        "Number",
        "Object",
        "Short",
        "String",
        "StringBuffer",
        "StringBuilder",
        "System",
        "Throwable",
        "Void",
    }
)


def _resolve_type(
    value: str,
    *,
    owner: JavaType | None,
    types_by_fqn: dict[str, JavaType],
    types_by_simple: dict[str, list[JavaType]],
) -> JavaType | None:
    normalized = re.sub(r"<.*>", "", value).removesuffix("[]").strip()
    exact = types_by_fqn.get(normalized)
    if exact is not None:
        return exact
    simple = normalized.rsplit(".", maxsplit=1)[-1]
    if owner is not None:
        imported = next(
            (
                import_name
                for import_name in owner.imports
                if import_name.rsplit(".", maxsplit=1)[-1] == simple
            ),
            None,
        )
        if imported is not None and imported in types_by_fqn:
            return types_by_fqn[imported]
        same_package = f"{owner.package}.{simple}" if owner.package else simple
        if same_package in types_by_fqn:
            return types_by_fqn[same_package]
    candidates = types_by_simple.get(simple, [])
    return candidates[0] if len(candidates) == 1 else None


def _owner_type(value: str | None, types_by_fqn: dict[str, JavaType]) -> JavaType | None:
    return types_by_fqn.get(value) if value is not None else None


def _extract_properties(
    file: DiscoveredCodeFile,
) -> tuple[list[dict[str, object]], list[JavaDirectEdge]]:
    symbols: list[dict[str, object]] = []
    edges: list[JavaDirectEdge] = []
    file_id = code_file_id(file.path)
    for line_number, raw_line in enumerate(
        file.content.decode("utf-8", errors="replace").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        delimiter_positions = [
            position for value in ("=", ":") if (position := line.find(value)) >= 0
        ]
        if not delimiter_positions:
            continue
        key = line[: min(delimiter_positions)].strip()
        if not key:
            continue
        symbol_id = _generic_symbol_id(file.path, "config_key", key)
        symbols.append(
            {
                "symbol_id": symbol_id,
                "symbol_type": "config_key",
                "name": key,
                "signature": f"config:{key}",
                "start_line": line_number,
                "end_line": line_number,
            }
        )
        edges.append(
            JavaDirectEdge(
                edge_type="contains",
                from_ref=file_id,
                to_ref=symbol_id,
                resolution_status="resolved",
                confidence="high",
                extractor="config_key",
                source_path=file.path,
                start_line=line_number,
                end_line=line_number,
            )
        )
    return symbols, edges


def _extract_sql(
    file: DiscoveredCodeFile,
) -> tuple[list[dict[str, object]], list[JavaDirectEdge]]:
    content = file.content.decode("utf-8", errors="replace")
    file_id = code_file_id(file.path)
    first_by_table: dict[str, tuple[str, int, bool]] = {}
    operations: list[tuple[str, str, int]] = []
    for match in _SQL_TABLE.finditer(content):
        operation = " ".join(match.group("operation").upper().split())
        table = match.group("table").strip('"')
        normalized = table.casefold()
        line_number = content.count("\n", 0, match.start()) + 1
        is_definition = operation.startswith("CREATE TABLE")
        current = first_by_table.get(normalized)
        if current is None:
            first_by_table[normalized] = (table, line_number, is_definition)
        elif is_definition and not current[2]:
            first_by_table[normalized] = (table, line_number, True)
        operations.append((operation, normalized, line_number))
    symbols: list[dict[str, object]] = []
    symbol_by_table: dict[str, str] = {}
    edges: list[JavaDirectEdge] = []
    for normalized, (table, line_number, is_definition) in sorted(first_by_table.items()):
        symbol_id = _generic_symbol_id(file.path, "db_table", normalized)
        symbol_by_table[normalized] = symbol_id
        symbols.append(
            {
                "symbol_id": symbol_id,
                "symbol_type": "db_table",
                "name": table,
                "signature": f"table:{normalized}",
                "start_line": line_number,
                "end_line": line_number,
            }
        )
        edges.append(
            JavaDirectEdge(
                edge_type="contains",
                from_ref=file_id,
                to_ref=symbol_id,
                resolution_status="resolved",
                confidence="high",
                extractor=("sql_table_definition" if is_definition else "sql_table_reference"),
                source_path=file.path,
                start_line=line_number,
                end_line=line_number,
            )
        )
    for operation, table, line_number in operations:
        edge_type = "reads" if operation in {"FROM", "JOIN"} else "writes"
        edges.append(
            JavaDirectEdge(
                edge_type=edge_type,
                from_ref=file_id,
                to_ref=symbol_by_table[table],
                resolution_status="resolved",
                confidence="medium",
                extractor="sql_table",
                source_path=file.path,
                start_line=line_number,
                end_line=line_number,
            )
        )
    return symbols, edges


def _generic_symbol_id(path: str, symbol_type: str, signature: str) -> str:
    material = "\x00".join((path, symbol_type, signature))
    return f"symbol-{sha256(material.encode()).hexdigest()[:24]}"


def _symbol_artifact_id(value: dict[str, object]) -> str:
    return str(value["symbol_id"])
