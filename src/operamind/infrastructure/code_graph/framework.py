"""Deterministic framework relations layered on the language-level Code Graph."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from operamind.infrastructure.code_graph.java import (
    JavaDirectEdge,
    JavaLambdaExpression,
    JavaSymbol,
    JavaType,
)
from operamind.infrastructure.code_graph.struts1 import extract_struts1_graph
from operamind.infrastructure.code_graph.workspace import DiscoveredCodeFile

SPECIALIZED_FRAMEWORK_EXTRACTORS = frozenset(
    {"spring_config_binding", "spring_data_access", "struts1_mvc", "web_ui_route"}
)


@dataclass(frozen=True, slots=True)
class FrameworkGraphResult:
    """Symbols and edges added or refined by explicit framework conventions."""

    symbol_additions: dict[str, tuple[dict[str, object], ...]]
    edges: tuple[JavaDirectEdge, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _EntityTable:
    type_name: str
    type_ref: str
    table_name: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class _Repository:
    type_name: str
    type_ref: str
    entity_type: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class _ConfigRead:
    from_ref: str
    key: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class _UiRoute:
    symbol: dict[str, object]
    method: str
    route: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class _RouteSink:
    function_name: str
    arity: int
    route_parameter_index: int
    route_parameter_name: str
    method: str
    path: str
    body_start: int
    body_end: int


@dataclass(frozen=True, slots=True)
class _Target:
    ref: str
    resolution_status: str
    confidence: str


_TABLE_ANNOTATION = re.compile(r"@Table\s*\((?P<body>[^)]*)\)", re.DOTALL)
_ANNOTATION_NAME = re.compile(r"\bname\s*=\s*\"(?P<value>[^\"]+)\"")
_TYPE_AFTER_ANNOTATION = re.compile(
    r"\b(?:public\s+)?(?:abstract\s+)?(?:class|record)\s+(?P<name>[A-Za-z_$][\w$]*)"
)
_SPRING_REPOSITORY = re.compile(
    r"\binterface\s+(?P<name>[A-Za-z_$][\w$]*)\s+extends\s+"
    r"(?:[\w$.]+\.)?(?:JpaRepository|CrudRepository|PagingAndSortingRepository)\s*"
    r"<\s*(?P<entity>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*,",
    re.DOTALL,
)
_VALUE_FIELD = re.compile(
    r"@Value\s*\(\s*\"\$\{(?P<key>[^}:]+)(?::[^}]*)?\}\"\s*\)"
    r"(?P<tail>.{0,400}?)\b(?P<name>[A-Za-z_$][\w$]*)\s*(?:=|;)",
    re.DOTALL,
)
_ENVIRONMENT_FIELD = re.compile(r"\bEnvironment\s+(?P<name>[A-Za-z_$][\w$]*)\b")
_HTML_ROUTE_TAG = re.compile(
    r"<(?P<tag>a|form)\b(?P<attributes>[^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_ROUTE_ATTRIBUTE = re.compile(
    r"\b(?P<attribute>(?:th:)?(?:href|action))\s*=\s*"
    r"(?P<quote>['\"])(?P<route>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_HTML_METHOD_ATTRIBUTE = re.compile(
    r"\b(?:th:)?method\s*=\s*(?P<quote>['\"])(?P<method>GET|POST)(?P=quote)",
    re.IGNORECASE,
)
_JS_ROUTE = re.compile(r"\b(?:url|href)\s*:\s*(?P<expression>[^,\n}]+)", re.IGNORECASE)
_JS_LOCATION_ROUTE = re.compile(
    r"(?:window\.)?location(?:\.href)?\s*=\s*(?P<expression>[^;\n]+)", re.IGNORECASE
)
_JS_VARIABLE_ASSIGNMENT = re.compile(
    r"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<expression>[^;\n]+)"
)
_JS_FUNCTION_DECLARATION = re.compile(
    r"\bfunction\s+(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<parameters>[^)]*)\)\s*\{"
)
_STRING_TOKEN = re.compile(r"(?P<quote>['\"])(?P<value>(?:\\.|(?!\1).)*?)(?P=quote)")
_HTTP_METHOD = re.compile(
    r"\b(?:type|method)\s*:\s*['\"](?P<method>GET|POST|PUT|PATCH|DELETE)['\"]",
    re.IGNORECASE,
)
_SPRING_PATH_VARIABLE = re.compile(r"\{[^/{}]+\}")
_UNRESOLVED_SIMPLE_CALL = re.compile(
    r"^unresolved:call:(?P<receiver>[A-Za-z_$][\w$]*)\."
    r"(?P<method>[A-Za-z_$][\w$]*)/\d+$"
)
_UNRESOLVED_REPOSITORY_OPTIONAL_CALL = re.compile(
    r"^unresolved:call:(?P<receiver>[A-Za-z_$][\w$]*)\."
    r"(?P<origin>findById)\(.*\)\."
    r"(?P<terminal>filter|flatMap|ifPresent|ifPresentOrElse|map|orElse|orElseGet|orElseThrow)/"
    r"(?P<arity>\d+)$"
)
_READ_PREFIXES = (
    "count",
    "exists",
    "find",
    "get",
    "query",
    "read",
    "search",
    "stream",
)
_WRITE_PREFIXES = ("create", "delete", "insert", "remove", "save", "update", "write")


def extract_framework_graph(
    *,
    files: tuple[DiscoveredCodeFile, ...],
    java_symbols: tuple[JavaSymbol, ...],
    java_types: tuple[JavaType, ...],
    java_lambdas: tuple[JavaLambdaExpression, ...],
    symbols_by_path: dict[str, list[dict[str, object]]],
    edges: tuple[JavaDirectEdge, ...],
    enabled_extractors: frozenset[str],
) -> FrameworkGraphResult:
    """Resolve only explicit Spring/JPA/UI relations over the supplied bounded files."""

    if not enabled_extractors.intersection(SPECIALIZED_FRAMEWORK_EXTRACTORS):
        return FrameworkGraphResult(symbol_additions={}, edges=edges)
    additions: dict[str, list[dict[str, object]]] = {}
    diagnostics: list[str] = []
    route_facts: list[_UiRoute] = []
    working_edges = list(edges)
    if "web_ui_route" in enabled_extractors:
        route_sinks = _extract_route_sinks(files)
        for file in files:
            if file.language not in {"javascript", "xml"}:
                continue
            symbols, route_edges, routes = _extract_ui_routes(file, route_sinks=route_sinks)
            additions[file.path] = symbols
            symbols_by_path[file.path].extend(symbols)
            working_edges.extend(route_edges)
            route_facts.extend(routes)

    symbol_records = [
        (path, symbol) for path, symbols in symbols_by_path.items() for symbol in symbols
    ]
    table_targets = _named_targets(
        symbol_records=symbol_records,
        edges=tuple(working_edges),
        symbol_type="db_table",
        signature_prefix="table:",
        definition_extractor="sql_table_definition",
        unresolved_prefix="table",
        casefold_names=True,
    )
    config_targets = _named_targets(
        symbol_records=symbol_records,
        edges=tuple(working_edges),
        symbol_type="config_key",
        signature_prefix="config:",
        definition_extractor=None,
        unresolved_prefix="config_key",
        casefold_names=False,
    )
    entity_facts: list[_EntityTable] = []
    repository_facts: list[_Repository] = []
    config_reads: list[_ConfigRead] = []
    if enabled_extractors.intersection({"spring_config_binding", "spring_data_access"}):
        for file in files:
            if file.language != "java":
                continue
            entities, repositories, reads = _extract_java_framework_facts(
                file=file,
                java_symbols=java_symbols,
                java_types=java_types,
                config_enabled="spring_config_binding" in enabled_extractors,
                data_enabled="spring_data_access" in enabled_extractors,
            )
            entity_facts.extend(entities)
            repository_facts.extend(repositories)
            config_reads.extend(reads)

    repository_targets: dict[str, _Target] = {}
    repository_entities: dict[str, _EntityTable] = {}
    if "spring_data_access" in enabled_extractors:
        entity_targets: dict[str, _Target] = {}
        entities_by_simple: dict[str, list[_EntityTable]] = {}
        for fact in entity_facts:
            target = table_targets.get(
                fact.table_name.casefold(),
                _Target(
                    ref=f"unresolved:table:{fact.table_name.casefold()}",
                    resolution_status="unresolved",
                    confidence="low",
                ),
            )
            entity_targets[fact.type_name] = target
            entities_by_simple.setdefault(fact.type_name.rsplit(".", 1)[-1], []).append(fact)
            working_edges.append(
                _edge(
                    edge_type="maps_to",
                    from_ref=fact.type_ref,
                    target=target,
                    extractor="spring_data_access",
                    path=fact.path,
                    line=fact.line,
                )
            )
        for repository in repository_facts:
            entity = _resolve_entity(repository.entity_type, entities_by_simple, entity_facts)
            target = (
                entity_targets[entity.type_name]
                if entity is not None
                else _Target(
                    ref=f"unresolved:entity:{repository.entity_type}",
                    resolution_status="unresolved",
                    confidence="low",
                )
            )
            repository_targets[repository.type_name] = target
            if entity is not None:
                repository_entities[repository.type_name] = entity
            working_edges.append(
                _edge(
                    edge_type="maps_to",
                    from_ref=repository.type_ref,
                    target=target,
                    extractor="spring_data_access",
                    path=repository.path,
                    line=repository.line,
                )
            )
        working_edges.extend(
            _repository_method_edges(
                java_symbols=java_symbols,
                repository_targets=repository_targets,
            )
        )

    if "spring_config_binding" in enabled_extractors:
        for read in config_reads:
            target = config_targets.get(
                read.key,
                _Target(
                    ref=f"unresolved:config_key:{read.key}",
                    resolution_status="unresolved",
                    confidence="low",
                ),
            )
            working_edges.append(
                _edge(
                    edge_type="reads",
                    from_ref=read.from_ref,
                    target=target,
                    extractor="spring_config_binding",
                    path=read.path,
                    line=read.line,
                )
            )

    if repository_targets:
        working_edges = _replace_repository_calls(
            edges=working_edges,
            java_symbols=java_symbols,
            java_types=java_types,
            repository_targets=repository_targets,
        )
        working_edges = _replace_repository_optional_chains(
            edges=working_edges,
            java_symbols=java_symbols,
            java_types=java_types,
            java_lambdas=java_lambdas,
            repository_entities=repository_entities,
        )
    if "struts1_mvc" in enabled_extractors:
        struts = extract_struts1_graph(
            files=files,
            java_symbols=java_symbols,
            java_types=java_types,
            edges=tuple(working_edges),
        )
        for path, struts_symbols in struts.symbol_additions.items():
            additions.setdefault(path, []).extend(struts_symbols)
            symbols_by_path[path].extend(struts_symbols)
        working_edges = list(struts.edges)
        diagnostics.extend(struts.diagnostics)
    if route_facts:
        working_edges.extend(_route_endpoint_edges(route_facts, tuple(working_edges)))
    return FrameworkGraphResult(
        symbol_additions={path: tuple(values) for path, values in additions.items()},
        edges=tuple(working_edges),
        diagnostics=tuple(sorted(set(diagnostics))),
    )


def _extract_java_framework_facts(
    *,
    file: DiscoveredCodeFile,
    java_symbols: tuple[JavaSymbol, ...],
    java_types: tuple[JavaType, ...],
    config_enabled: bool,
    data_enabled: bool,
) -> tuple[list[_EntityTable], list[_Repository], list[_ConfigRead]]:
    content = file.content.decode("utf-8", errors="replace")
    file_id = _file_id(java_symbols, file.path)
    file_types = [value for value in java_types if value.file_id == file_id]
    type_by_simple = {value.simple_name: value for value in file_types}
    file_symbols = [value for value in java_symbols if value.path == file.path]
    entities: list[_EntityTable] = []
    repositories: list[_Repository] = []
    reads: list[_ConfigRead] = []
    if data_enabled:
        for match in _TABLE_ANNOTATION.finditer(content):
            name_match = _ANNOTATION_NAME.search(match.group("body"))
            type_match = _TYPE_AFTER_ANNOTATION.search(content, match.end(), match.end() + 800)
            if name_match is None or type_match is None:
                continue
            java_type = type_by_simple.get(type_match.group("name"))
            if java_type is None:
                continue
            entities.append(
                _EntityTable(
                    type_name=java_type.fqn,
                    type_ref=java_type.symbol_id,
                    table_name=name_match.group("value").strip('"'),
                    path=file.path,
                    line=_line(content, match.start()),
                )
            )
        for match in _SPRING_REPOSITORY.finditer(content):
            java_type = type_by_simple.get(match.group("name"))
            if java_type is None:
                continue
            repositories.append(
                _Repository(
                    type_name=java_type.fqn,
                    type_ref=java_type.symbol_id,
                    entity_type=match.group("entity"),
                    path=file.path,
                    line=_line(content, match.start()),
                )
            )
    if config_enabled:
        fields_by_name = {
            value.name: value for value in file_symbols if value.symbol_type == "field"
        }
        for match in _VALUE_FIELD.finditer(content):
            field = fields_by_name.get(match.group("name"))
            if field is None:
                continue
            reads.append(
                _ConfigRead(
                    from_ref=field.symbol_id,
                    key=match.group("key"),
                    path=file.path,
                    line=_line(content, match.start()),
                )
            )
        environment_names = tuple(
            dict.fromkeys(match.group("name") for match in _ENVIRONMENT_FIELD.finditer(content))
        )
        for name in environment_names:
            getter = re.compile(
                rf"\b{re.escape(name)}\.(?:getProperty|getRequiredProperty)\s*\(\s*"
                r"\"(?P<key>[^\"]+)\""
            )
            for match in getter.finditer(content):
                line = _line(content, match.start())
                owner = _enclosing_callable(file_symbols, line)
                if owner is not None:
                    reads.append(
                        _ConfigRead(
                            from_ref=owner.symbol_id,
                            key=match.group("key"),
                            path=file.path,
                            line=line,
                        )
                    )
    return entities, repositories, reads


def _extract_ui_routes(
    file: DiscoveredCodeFile,
    *,
    route_sinks: tuple[_RouteSink, ...],
) -> tuple[list[dict[str, object]], list[JavaDirectEdge], list[_UiRoute]]:
    content = file.content.decode("utf-8", errors="replace")
    route_aliases = _static_route_aliases(content)
    candidates: list[tuple[str, str, int]] = []
    if file.language == "xml":
        candidates.extend(_html_route_candidates(content))
        expression_patterns = (_JS_ROUTE, _JS_LOCATION_ROUTE)
    else:
        expression_patterns = (_JS_ROUTE, _JS_LOCATION_ROUTE)
    for pattern in expression_patterns:
        for match in pattern.finditer(content):
            expression = match.group("expression")
            if _is_route_sink_parameter(
                file.path,
                offset=match.start(),
                expression=expression,
                route_sinks=route_sinks,
            ):
                continue
            route = _route_expression(expression) or route_aliases.get(expression.strip())
            if route is None:
                route = _dynamic_route(expression)
            method = _method_near_expression(content, match.start(), match.end())
            candidates.append((method, route, match.start()))
    candidates.extend(
        _route_sink_call_candidates(
            file=file,
            content=content,
            route_aliases=route_aliases,
            route_sinks=route_sinks,
        )
    )
    unique: dict[tuple[str, str], int] = {}
    for method, route, offset in candidates:
        unique.setdefault((method, route), offset)
    symbols: list[dict[str, object]] = []
    edges: list[JavaDirectEdge] = []
    routes: list[_UiRoute] = []
    file_ref = _code_file_id(file.path)
    for (method, route), offset in sorted(unique.items()):
        line = _line(content, offset)
        signature = f"route:{method}:{route}"
        symbol_id = _generic_symbol_id(file.path, "ui_route", signature)
        symbol = {
            "symbol_id": symbol_id,
            "symbol_type": "ui_route",
            "name": route,
            "signature": signature,
            "start_line": line,
            "end_line": line,
        }
        symbols.append(symbol)
        edges.append(
            JavaDirectEdge(
                edge_type="contains",
                from_ref=file_ref,
                to_ref=symbol_id,
                resolution_status="resolved",
                confidence="high",
                extractor="web_ui_route",
                source_path=file.path,
                start_line=line,
                end_line=line,
            )
        )
        edges.append(
            JavaDirectEdge(
                edge_type="navigates_to",
                from_ref=symbol_id,
                to_ref=f"route:{route}",
                resolution_status="external",
                confidence="high",
                extractor="web_ui_route",
                source_path=file.path,
                start_line=line,
                end_line=line,
            )
        )
        routes.append(_UiRoute(symbol, method, route, file.path, line))
    return symbols, edges, routes


def _html_route_candidates(content: str) -> list[tuple[str, str, int]]:
    candidates: list[tuple[str, str, int]] = []
    for tag_match in _HTML_ROUTE_TAG.finditer(content):
        tag = tag_match.group("tag").casefold()
        attributes = tag_match.group("attributes")
        route_match = _HTML_ROUTE_ATTRIBUTE.search(attributes)
        if route_match is None:
            continue
        raw_attribute = route_match.group("attribute").casefold()
        attribute = raw_attribute.removeprefix("th:")
        if (tag, attribute) not in {("a", "href"), ("form", "action")}:
            continue
        raw_route = route_match.group("route").strip()
        route = _thymeleaf_route(raw_route)
        if route is None:
            if raw_route.startswith("/"):
                route = _normalize_route(raw_route)
            elif raw_attribute.startswith("th:"):
                route = _dynamic_route(raw_route)
            else:
                continue
        method = "GET"
        if tag == "form":
            method_match = _HTML_METHOD_ATTRIBUTE.search(attributes)
            if method_match is not None:
                method = method_match.group("method").upper()
        candidates.append((method, route, tag_match.start()))
    return candidates


def _thymeleaf_route(value: str) -> str | None:
    if not value.startswith("@{") or not value.endswith("}"):
        return None
    expression = value[2:-1].strip()
    if not expression.startswith("/"):
        return _dynamic_route(expression)
    parameter_start = expression.find("(")
    route = expression[:parameter_start].strip() if parameter_start >= 0 else expression
    if not route or any(marker in route for marker in ("${", "*{", "#{")):
        return _dynamic_route(expression)
    return _normalize_route(route)


def _route_endpoint_edges(
    routes: list[_UiRoute], edges: tuple[JavaDirectEdge, ...]
) -> list[JavaDirectEdge]:
    endpoints: dict[tuple[str, str], list[str]] = {}
    for edge in edges:
        if edge.edge_type != "exposes" or not edge.to_ref.startswith("http:"):
            continue
        _, method, path = edge.to_ref.split(":", maxsplit=2)
        endpoints.setdefault((method, _endpoint_path(path)), []).append(edge.from_ref)
    values: list[JavaDirectEdge] = []
    for route in routes:
        if route.route.startswith("dynamic:"):
            values.append(
                _edge(
                    edge_type="calls",
                    from_ref=str(route.symbol["symbol_id"]),
                    target=_Target(
                        f"unresolved:endpoint:{route.method}:{route.route}",
                        "unresolved",
                        "low",
                    ),
                    extractor="web_ui_route",
                    path=route.path,
                    line=route.line,
                )
            )
            continue
        route_path = _endpoint_path(route.route)
        candidates = endpoints.get((route.method, route_path), [])
        if not candidates:
            candidates = endpoints.get(("ANY", route_path), [])
        if len(set(candidates)) == 1:
            target = _Target(next(iter(dict.fromkeys(candidates))), "resolved", "high")
        else:
            target = _Target(
                f"unresolved:endpoint:http:{route.method}:{route_path}",
                "unresolved",
                "low",
            )
        values.append(
            _edge(
                edge_type="calls",
                from_ref=str(route.symbol["symbol_id"]),
                target=target,
                extractor="web_ui_route",
                path=route.path,
                line=route.line,
            )
        )
    return values


def _named_targets(
    *,
    symbol_records: list[tuple[str, dict[str, object]]],
    edges: tuple[JavaDirectEdge, ...],
    symbol_type: str,
    signature_prefix: str,
    definition_extractor: str | None,
    unresolved_prefix: str,
    casefold_names: bool,
) -> dict[str, _Target]:
    candidates: dict[str, list[str]] = {}
    for _, symbol in symbol_records:
        if symbol.get("symbol_type") != symbol_type:
            continue
        signature = str(symbol["signature"])
        if not signature.startswith(signature_prefix):
            continue
        name = signature.removeprefix(signature_prefix)
        candidates.setdefault(name.casefold() if casefold_names else name, []).append(
            str(symbol["symbol_id"])
        )
    definitions = {
        edge.to_ref
        for edge in edges
        if definition_extractor is not None
        and edge.edge_type == "contains"
        and edge.extractor == definition_extractor
    }
    values: dict[str, _Target] = {}
    for name, refs in candidates.items():
        preferred = [value for value in refs if value in definitions] or refs
        unique = tuple(dict.fromkeys(preferred))
        values[name] = (
            _Target(unique[0], "resolved", "high")
            if len(unique) == 1
            else _Target(f"unresolved:{unresolved_prefix}:{name}", "unresolved", "low")
        )
    return values


def _repository_method_edges(
    *,
    java_symbols: tuple[JavaSymbol, ...],
    repository_targets: dict[str, _Target],
) -> list[JavaDirectEdge]:
    values: list[JavaDirectEdge] = []
    for symbol in java_symbols:
        if symbol.symbol_type != "method" or symbol.owner_type not in repository_targets:
            continue
        operation = _db_operation(symbol.name)
        if operation is None:
            continue
        values.append(
            _edge(
                edge_type=operation,
                from_ref=symbol.symbol_id,
                target=repository_targets[symbol.owner_type],
                extractor="spring_data_access",
                path=symbol.path,
                line=symbol.start_line,
            )
        )
    return values


def _replace_repository_calls(
    *,
    edges: list[JavaDirectEdge],
    java_symbols: tuple[JavaSymbol, ...],
    java_types: tuple[JavaType, ...],
    repository_targets: dict[str, _Target],
) -> list[JavaDirectEdge]:
    symbol_by_id = {symbol.symbol_id: symbol for symbol in java_symbols}
    fields_by_owner_name = {
        (symbol.owner_type, symbol.name): symbol
        for symbol in java_symbols
        if symbol.symbol_type == "field" and symbol.owner_type is not None
    }
    types_by_fqn = {value.fqn: value for value in java_types}
    types_by_simple: dict[str, list[JavaType]] = {}
    for value in java_types:
        types_by_simple.setdefault(value.simple_name, []).append(value)
    values: list[JavaDirectEdge] = []
    for edge in edges:
        match = (
            _UNRESOLVED_SIMPLE_CALL.fullmatch(edge.to_ref)
            if edge.edge_type == "calls" and edge.resolution_status == "unresolved"
            else None
        )
        if match is None:
            values.append(edge)
            continue
        source = symbol_by_id.get(edge.from_ref)
        field = (
            fields_by_owner_name.get((source.owner_type, match.group("receiver")))
            if source is not None and source.owner_type is not None
            else None
        )
        repository_type = (
            _resolve_java_type(
                field.signature.rsplit(":", maxsplit=1)[-1],
                owner_fqn=source.owner_type if source is not None else None,
                types_by_fqn=types_by_fqn,
                types_by_simple=types_by_simple,
            )
            if field is not None
            else None
        )
        operation = _db_operation(match.group("method"))
        target = (
            repository_targets.get(repository_type.fqn) if repository_type is not None else None
        )
        if operation is None or target is None or target.resolution_status != "resolved":
            values.append(edge)
            continue
        values.append(
            JavaDirectEdge(
                edge_type=operation,
                from_ref=edge.from_ref,
                to_ref=target.ref,
                resolution_status="resolved",
                confidence="high",
                extractor="spring_data_access",
                source_path=edge.source_path,
                start_line=edge.start_line,
                end_line=edge.end_line,
            )
        )
    return values


def _replace_repository_optional_chains(
    *,
    edges: list[JavaDirectEdge],
    java_symbols: tuple[JavaSymbol, ...],
    java_types: tuple[JavaType, ...],
    java_lambdas: tuple[JavaLambdaExpression, ...],
    repository_entities: dict[str, _EntityTable],
) -> list[JavaDirectEdge]:
    """Apply only the declared Spring Data findById -> Optional<T> contract."""

    fields_by_owner_name = {
        (symbol.owner_type, symbol.name): symbol
        for symbol in java_symbols
        if symbol.symbol_type == "field" and symbol.owner_type is not None
    }
    types_by_fqn = {value.fqn: value for value in java_types}
    types_by_simple: dict[str, list[JavaType]] = {}
    for value in java_types:
        types_by_simple.setdefault(value.simple_name, []).append(value)
    methods_by_owner_name_arity: dict[tuple[str, str, int], list[JavaSymbol]] = {}
    for symbol in java_symbols:
        if (
            symbol.symbol_type == "method"
            and symbol.owner_type is not None
            and symbol.arity is not None
        ):
            methods_by_owner_name_arity.setdefault(
                (symbol.owner_type, symbol.name, symbol.arity), []
            ).append(symbol)

    repository_by_source_receiver: dict[tuple[str, str], str] = {}
    for source in java_symbols:
        if source.owner_type is None:
            continue
        for (owner_type, field_name), field in fields_by_owner_name.items():
            if owner_type != source.owner_type or field.declared_type is None:
                continue
            repository_type = _resolve_java_type(
                field.declared_type,
                owner_fqn=source.owner_type,
                types_by_fqn=types_by_fqn,
                types_by_simple=types_by_simple,
            )
            if repository_type is not None and repository_type.fqn in repository_entities:
                repository_by_source_receiver[(source.symbol_id, field_name)] = repository_type.fqn

    lambda_entity_by_scope: list[tuple[JavaLambdaExpression, str]] = []
    for expression in java_lambdas:
        if (
            expression.origin_method_name != "findById"
            or expression.terminal_method_name
            not in {"filter", "flatMap", "ifPresent", "ifPresentOrElse", "map"}
            or len(expression.parameter_names) != 1
        ):
            continue
        source_candidates = [
            symbol
            for symbol in java_symbols
            if symbol.path == expression.source_path
            and symbol.owner_type == expression.owner_type
            and symbol.symbol_type in {"method", "constructor"}
            and symbol.start_line <= expression.start_line <= symbol.end_line
        ]
        if len(source_candidates) != 1:
            continue
        repository_fqn = repository_by_source_receiver.get(
            (source_candidates[0].symbol_id, expression.origin_object_name)
        )
        entity = repository_entities.get(repository_fqn or "")
        if entity is not None:
            lambda_entity_by_scope.append((expression, entity.type_name))

    values: list[JavaDirectEdge] = []
    for edge in edges:
        if edge.edge_type != "calls" or edge.resolution_status != "unresolved":
            values.append(edge)
            continue
        optional_match = _UNRESOLVED_REPOSITORY_OPTIONAL_CALL.fullmatch(edge.to_ref)
        if (
            optional_match is not None
            and (
                edge.from_ref,
                optional_match.group("receiver"),
            )
            in repository_by_source_receiver
        ):
            values.append(
                JavaDirectEdge(
                    edge_type="calls",
                    from_ref=edge.from_ref,
                    to_ref=(
                        "external:call:java.util.Optional."
                        f"{optional_match.group('terminal')}/{optional_match.group('arity')}"
                    ),
                    resolution_status="external",
                    confidence="high",
                    extractor="spring_data_access",
                    source_path=edge.source_path,
                    start_line=edge.start_line,
                    end_line=edge.end_line,
                )
            )
            continue
        simple_match = _UNRESOLVED_SIMPLE_CALL.fullmatch(edge.to_ref)
        if simple_match is None:
            values.append(edge)
            continue
        entity_types = {
            entity_type
            for expression, entity_type in lambda_entity_by_scope
            if expression.source_path == edge.source_path
            and expression.start_line <= edge.start_line <= expression.end_line
            and expression.parameter_names[0] == simple_match.group("receiver")
        }
        if len(entity_types) != 1:
            values.append(edge)
            continue
        candidates = methods_by_owner_name_arity.get(
            (
                next(iter(entity_types)),
                simple_match.group("method"),
                int(edge.to_ref.rsplit("/", maxsplit=1)[-1]),
            ),
            [],
        )
        if len(candidates) != 1:
            values.append(edge)
            continue
        values.append(
            JavaDirectEdge(
                edge_type="calls",
                from_ref=edge.from_ref,
                to_ref=candidates[0].symbol_id,
                resolution_status="resolved",
                confidence="high",
                extractor="spring_data_access",
                source_path=edge.source_path,
                start_line=edge.start_line,
                end_line=edge.end_line,
            )
        )
    return values


def _resolve_java_type(
    value: str,
    *,
    owner_fqn: str | None,
    types_by_fqn: dict[str, JavaType],
    types_by_simple: dict[str, list[JavaType]],
) -> JavaType | None:
    normalized = re.sub(r"<.*>", "", value).removesuffix("[]").strip()
    if normalized in types_by_fqn:
        return types_by_fqn[normalized]
    simple = normalized.rsplit(".", maxsplit=1)[-1]
    owner = types_by_fqn.get(owner_fqn or "")
    if owner is not None:
        imported = next(
            (
                name
                for name in owner.imports
                if name.rsplit(".", maxsplit=1)[-1] == simple and name in types_by_fqn
            ),
            None,
        )
        if imported is not None:
            return types_by_fqn[imported]
        same_package = f"{owner.package}.{simple}" if owner.package else simple
        if same_package in types_by_fqn:
            return types_by_fqn[same_package]
    candidates = types_by_simple.get(simple, [])
    return candidates[0] if len(candidates) == 1 else None


def _resolve_entity(
    value: str,
    by_simple: dict[str, list[_EntityTable]],
    all_entities: list[_EntityTable],
) -> _EntityTable | None:
    exact = [item for item in all_entities if item.type_name == value]
    if len(exact) == 1:
        return exact[0]
    candidates = by_simple.get(value.rsplit(".", maxsplit=1)[-1], [])
    return candidates[0] if len(candidates) == 1 else None


def _db_operation(method_name: str) -> str | None:
    folded = method_name.casefold()
    if folded.startswith(_WRITE_PREFIXES):
        return "writes"
    if folded.startswith(_READ_PREFIXES):
        return "reads"
    return None


def _edge(
    *,
    edge_type: str,
    from_ref: str,
    target: _Target,
    extractor: str,
    path: str,
    line: int,
) -> JavaDirectEdge:
    return JavaDirectEdge(
        edge_type=edge_type,
        from_ref=from_ref,
        to_ref=target.ref,
        resolution_status=target.resolution_status,
        confidence=target.confidence,
        extractor=extractor,
        source_path=path,
        start_line=line,
        end_line=line,
    )


def _route_expression(expression: str) -> str | None:
    tokens = list(_STRING_TOKEN.finditer(expression))
    if not tokens:
        return None
    first = tokens[0].group("value")
    if not first.startswith("/"):
        return None
    parts = [part.strip() for part in expression.split("+")]
    route_parts: list[str] = []
    for part in parts:
        token = _STRING_TOKEN.fullmatch(part)
        route_parts.append(token.group("value") if token is not None else "{*}")
    return _normalize_route("".join(route_parts))


def _static_route_aliases(content: str) -> dict[str, str]:
    candidates: dict[str, list[str]] = {}
    for match in _JS_VARIABLE_ASSIGNMENT.finditer(content):
        route = _route_expression(match.group("expression"))
        if route is not None:
            candidates.setdefault(match.group("name"), []).append(route)
    return {
        name: unique[0]
        for name, values in candidates.items()
        if len(unique := tuple(dict.fromkeys(values))) == 1 and len(values) == 1
    }


def _extract_route_sinks(files: tuple[DiscoveredCodeFile, ...]) -> tuple[_RouteSink, ...]:
    candidates: list[_RouteSink] = []
    for file in files:
        if file.language not in {"javascript", "xml"}:
            continue
        content = file.content.decode("utf-8", errors="replace")
        for function in _JS_FUNCTION_DECLARATION.finditer(content):
            body_start = function.end() - 1
            body_end = _matching_delimiter(content, body_start, "{", "}")
            if body_end is None:
                continue
            parameters = tuple(
                value.strip() for value in function.group("parameters").split(",") if value.strip()
            )
            body = content[body_start + 1 : body_end]
            for route_match in _JS_ROUTE.finditer(body):
                expression = route_match.group("expression").strip()
                if expression not in parameters:
                    continue
                absolute_start = body_start + 1 + route_match.start()
                candidates.append(
                    _RouteSink(
                        function_name=function.group("name"),
                        arity=len(parameters),
                        route_parameter_index=parameters.index(expression),
                        route_parameter_name=expression,
                        method=_method_near_expression(
                            content,
                            absolute_start,
                            body_start + 1 + route_match.end(),
                        ),
                        path=file.path,
                        body_start=body_start,
                        body_end=body_end,
                    )
                )
    grouped: dict[tuple[str, int], list[_RouteSink]] = {}
    for candidate in candidates:
        grouped.setdefault((candidate.function_name, candidate.arity), []).append(candidate)
    return tuple(values[0] for _, values in sorted(grouped.items()) if len(values) == 1)


def _is_route_sink_parameter(
    path: str,
    *,
    offset: int,
    expression: str,
    route_sinks: tuple[_RouteSink, ...],
) -> bool:
    return any(
        sink.path == path
        and sink.body_start <= offset <= sink.body_end
        and expression.strip() == sink.route_parameter_name
        for sink in route_sinks
    )


def _route_sink_call_candidates(
    *,
    file: DiscoveredCodeFile,
    content: str,
    route_aliases: dict[str, str],
    route_sinks: tuple[_RouteSink, ...],
) -> list[tuple[str, str, int]]:
    values: list[tuple[str, str, int]] = []
    for sink in route_sinks:
        call_pattern = re.compile(rf"\b{re.escape(sink.function_name)}\s*\(")
        for match in call_pattern.finditer(content):
            if re.search(r"\bfunction\s*$", content[max(0, match.start() - 20) : match.start()]):
                continue
            opening = match.end() - 1
            closing = _matching_delimiter(content, opening, "(", ")")
            if closing is None:
                continue
            arguments = _split_top_level(content[opening + 1 : closing])
            if len(arguments) != sink.arity or sink.route_parameter_index >= len(arguments):
                continue
            expression = arguments[sink.route_parameter_index].strip()
            route = _route_expression(expression) or route_aliases.get(expression)
            if route is None:
                route = _dynamic_route(expression)
            values.append((sink.method, route, match.start()))
    return values


def _matching_delimiter(content: str, opening_index: int, opening: str, closing: str) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    index = opening_index
    while index < len(content):
        character = content[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {'"', "'", "`"}:
            quote = character
        elif content.startswith("//", index):
            newline = content.find("\n", index + 2)
            index = len(content) if newline < 0 else newline
            continue
        elif content.startswith("/*", index):
            comment_end = content.find("*/", index + 2)
            index = len(content) if comment_end < 0 else comment_end + 2
            continue
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _split_top_level(value: str) -> tuple[str, ...]:
    if not value.strip():
        return ()
    parts: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'", "`"}:
            quote = character
        elif character in depths:
            depths[character] += 1
        elif character in pairs and depths[pairs[character]] > 0:
            depths[pairs[character]] -= 1
        elif character == "," and not any(depths.values()):
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return tuple(parts)


def _dynamic_route(expression: str) -> str:
    value = re.sub(r"\s+", "", expression.strip())
    value = re.sub(r"[^A-Za-z0-9_$.()\[\]-]", "?", value)
    return f"dynamic:{value[:120] or 'unknown'}"


def _normalize_route(value: str) -> str:
    route = value.strip().replace(r"\/", "/")
    if not route.startswith("/"):
        route = f"/{route}"
    path, separator, query = route.partition("?")
    path = re.sub(r"/+", "/", path)
    if "{*}" in path:
        path = re.sub(r"(?:\{\*\})+", "{*}", path)
    return f"{path}?{query}" if separator else path


def _endpoint_path(value: str) -> str:
    path = urlsplit(value).path
    return _SPRING_PATH_VARIABLE.sub("{*}", re.sub(r"/+", "/", path))


def _method_near_expression(content: str, start: int, end: int) -> str:
    object_start = content.rfind("{", max(0, start - 800), start)
    object_end = content.find("}", end, min(len(content), end + 800))
    context = content[object_start if object_start >= 0 else start : object_end + 1]
    match = _HTTP_METHOD.search(context)
    return match.group("method").upper() if match is not None else "GET"


def _enclosing_callable(symbols: list[JavaSymbol], line: int) -> JavaSymbol | None:
    candidates = [
        symbol
        for symbol in symbols
        if symbol.symbol_type in {"method", "constructor"}
        and symbol.start_line <= line <= symbol.end_line
    ]
    return min(candidates, key=lambda value: value.end_line - value.start_line, default=None)


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _file_id(symbols: tuple[JavaSymbol, ...], path: str) -> str:
    return next((symbol.file_id for symbol in symbols if symbol.path == path), _code_file_id(path))


def _code_file_id(path: str) -> str:
    from hashlib import sha256

    return f"file-{sha256(path.encode()).hexdigest()[:24]}"


def _generic_symbol_id(path: str, symbol_type: str, signature: str) -> str:
    from hashlib import sha256

    material = "\x00".join((path, symbol_type, signature))
    return f"symbol-{sha256(material.encode()).hexdigest()[:24]}"
