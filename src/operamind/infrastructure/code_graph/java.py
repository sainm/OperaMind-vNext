"""Tree-sitter Java extraction with unresolved relations kept explicit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

import tree_sitter_java
from tree_sitter import Language, Node, Parser

from operamind.infrastructure.code_graph.workspace import DiscoveredCodeFile


@dataclass(frozen=True, slots=True)
class JavaSymbol:
    symbol_id: str
    file_id: str
    path: str
    symbol_type: str
    name: str
    signature: str
    start_line: int
    end_line: int
    owner_type: str | None
    arity: int | None
    declared_type: str | None

    def to_artifact(self) -> dict[str, object]:
        artifact: dict[str, object] = {
            "symbol_id": self.symbol_id,
            "symbol_type": self.symbol_type,
            "name": self.name,
            "signature": self.signature,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
        if self.declared_type is not None:
            artifact["declared_type"] = self.declared_type
        return artifact


@dataclass(frozen=True, slots=True)
class JavaType:
    symbol_id: str
    file_id: str
    fqn: str
    simple_name: str
    package: str
    imports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JavaRelation:
    relation_kind: str
    from_ref: str
    target: str
    source_path: str
    start_line: int
    end_line: int
    extractor: str
    method_name: str | None = None
    arity: int | None = None
    object_name: str | None = None
    receiver_type: str | None = None
    owner_type: str | None = None
    test_source: bool = False


@dataclass(frozen=True, slots=True)
class JavaDirectEdge:
    edge_type: str
    from_ref: str
    to_ref: str
    resolution_status: str
    confidence: str
    extractor: str
    source_path: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class JavaLambdaExpression:
    parameter_names: tuple[str, ...]
    origin_object_name: str
    origin_method_name: str
    terminal_method_name: str
    owner_type: str
    source_path: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class JavaFileExtraction:
    symbols: tuple[JavaSymbol, ...]
    types: tuple[JavaType, ...]
    relations: tuple[JavaRelation, ...]
    direct_edges: tuple[JavaDirectEdge, ...]
    lambda_expressions: tuple[JavaLambdaExpression, ...]
    diagnostics: tuple[str, ...]


class JavaTreeSitterExtractor:
    """Extract Java declarations and relation candidates from one bounded file."""

    def __init__(self) -> None:
        self._parser = Parser(Language(tree_sitter_java.language()))

    def extract(
        self,
        *,
        file: DiscoveredCodeFile,
        profile_ref: str,
        enabled_extractors: frozenset[str],
    ) -> JavaFileExtraction:
        """Parse one file; syntax errors become diagnostics, never guessed edges."""

        if file.language != "java":
            raise ValueError("JavaTreeSitterExtractor only accepts Java files")
        file_id = code_file_id(file.path)
        tree = self._parser.parse(file.content)
        root = tree.root_node
        diagnostics: list[str] = []
        if root.has_error:
            diagnostics.append(f"tree_sitter_parse_error:{file.path}")
        package = _package_name(root, file.content)
        imports = _imports(root, file.content)
        declaration_nodes = tuple(node for node in _walk(root) if node.type in _TYPE_DECLARATIONS)
        type_info_by_key: dict[tuple[int, int, str], JavaType] = {}
        callable_symbol_by_key: dict[tuple[int, int, str], JavaSymbol] = {}
        symbols: list[JavaSymbol] = []
        types: list[JavaType] = []
        relations: list[JavaRelation] = []
        direct_edges: list[JavaDirectEdge] = []
        lambda_expressions: list[JavaLambdaExpression] = []

        if "java_symbol" in enabled_extractors:
            for node in declaration_nodes:
                name = _field_text(node, "name", file.content)
                if name is None:
                    diagnostics.append(f"java_type_without_name:{file.path}:{_line(node)}")
                    continue
                enclosing = _enclosing_type_names(node, file.content)
                qualified_parts = tuple(part for part in (package, *enclosing, name) if part)
                fqn = ".".join(qualified_parts)
                symbol = _symbol(
                    file_id=file_id,
                    path=file.path,
                    symbol_type=_TYPE_DECLARATIONS[node.type],
                    name=name,
                    signature=fqn,
                    node=node,
                    owner_type=".".join(qualified_parts[:-1]) or None,
                    arity=None,
                    declared_type=None,
                )
                java_type = JavaType(
                    symbol_id=symbol.symbol_id,
                    file_id=file_id,
                    fqn=fqn,
                    simple_name=name,
                    package=package,
                    imports=imports,
                )
                symbols.append(symbol)
                types.append(java_type)
                type_info_by_key[_node_key(node)] = java_type
                direct_edges.append(
                    _direct_edge(
                        edge_type="contains",
                        from_ref=file_id,
                        to_ref=symbol.symbol_id,
                        resolution_status="resolved",
                        confidence="high",
                        extractor="java_symbol",
                        path=file.path,
                        node=node,
                    )
                )
                interfaces = node.child_by_field_name("interfaces")
                if interfaces is not None:
                    for target in _type_list_values(interfaces, file.content):
                        relations.append(
                            JavaRelation(
                                relation_kind="implements",
                                from_ref=symbol.symbol_id,
                                target=target,
                                source_path=file.path,
                                start_line=_line(interfaces),
                                end_line=_end_line(interfaces),
                                extractor="java_symbol",
                                owner_type=fqn,
                            )
                        )

            for node in _walk(root):
                if node.type not in {"method_declaration", "constructor_declaration"}:
                    continue
                owner_node = _nearest_type(node)
                owner = type_info_by_key.get(_node_key(owner_node)) if owner_node else None
                if owner is None:
                    diagnostics.append(f"java_callable_without_owner:{file.path}:{_line(node)}")
                    continue
                name = _field_text(node, "name", file.content)
                if name is None:
                    diagnostics.append(f"java_callable_without_name:{file.path}:{_line(node)}")
                    continue
                parameter_types = _parameter_types(node, file.content)
                return_type = (
                    _normalize_type(value)
                    if node.type == "method_declaration"
                    and (value := _field_text(node, "type", file.content)) is not None
                    else None
                )
                signature = f"{owner.fqn}#{name}({','.join(parameter_types)})"
                symbol = _symbol(
                    file_id=file_id,
                    path=file.path,
                    symbol_type=(
                        "constructor" if node.type == "constructor_declaration" else "method"
                    ),
                    name=name,
                    signature=signature,
                    node=node,
                    owner_type=owner.fqn,
                    arity=len(parameter_types),
                    declared_type=return_type,
                )
                symbols.append(symbol)
                callable_symbol_by_key[_node_key(node)] = symbol
                direct_edges.append(
                    _direct_edge(
                        edge_type="contains",
                        from_ref=file_id,
                        to_ref=symbol.symbol_id,
                        resolution_status="resolved",
                        confidence="high",
                        extractor="java_symbol",
                        path=file.path,
                        node=node,
                    )
                )
                annotations = _annotations(node, file.content)
                is_test = any(
                    _simple_annotation_name(value[0]) in _TEST_ANNOTATIONS for value in annotations
                )
                variable_types = _declared_variable_types(node, file.content)
                for lambda_node in _descendants_owned_by(node, "lambda_expression"):
                    lambda_expression = _lambda_expression(
                        lambda_node,
                        owner_type=owner.fqn,
                        source=file.content,
                        path=file.path,
                    )
                    if lambda_expression is not None:
                        lambda_expressions.append(lambda_expression)
                for invocation in _descendants_owned_by(node, "method_invocation"):
                    invocation_name = _field_text(invocation, "name", file.content)
                    if invocation_name is None:
                        continue
                    arguments = invocation.child_by_field_name("arguments")
                    arity = len(arguments.named_children) if arguments is not None else 0
                    object_name = _field_text(invocation, "object", file.content)
                    target = (
                        f"{object_name}.{invocation_name}/{arity}"
                        if object_name
                        else f"{invocation_name}/{arity}"
                    )
                    relations.append(
                        JavaRelation(
                            relation_kind="call",
                            from_ref=symbol.symbol_id,
                            target=target,
                            source_path=file.path,
                            start_line=_line(invocation),
                            end_line=_end_line(invocation),
                            extractor="java_symbol",
                            method_name=invocation_name,
                            arity=arity,
                            object_name=object_name,
                            receiver_type=_receiver_declared_type(
                                object_name, variable_types=variable_types
                            ),
                            owner_type=owner.fqn,
                            test_source=is_test,
                        )
                    )
                if "spring_endpoint" in enabled_extractors:
                    class_paths = _mapping_paths(_annotations(owner_node, file.content))
                    for http_method, method_path in _mapping_endpoints(annotations):
                        paths = class_paths or ("/",)
                        for class_path in paths:
                            endpoint_path = _join_url_paths(class_path, method_path)
                            endpoint = f"http:{http_method}:{endpoint_path}"
                            direct_edges.append(
                                _direct_edge(
                                    edge_type="exposes",
                                    from_ref=symbol.symbol_id,
                                    to_ref=endpoint,
                                    resolution_status="external",
                                    confidence="high",
                                    extractor="spring_endpoint",
                                    path=file.path,
                                    node=node,
                                )
                            )

            for node in _walk(root):
                if node.type != "field_declaration":
                    continue
                owner_node = _nearest_type(node)
                owner = type_info_by_key.get(_node_key(owner_node)) if owner_node else None
                field_type = _field_text(node, "type", file.content)
                if owner is None or field_type is None:
                    continue
                for declarator in (
                    child for child in node.named_children if child.type == "variable_declarator"
                ):
                    name = _field_text(declarator, "name", file.content)
                    if name is None:
                        continue
                    symbol = _symbol(
                        file_id=file_id,
                        path=file.path,
                        symbol_type="field",
                        name=name,
                        signature=f"{owner.fqn}#{name}:{_normalize_type(field_type)}",
                        node=declarator,
                        owner_type=owner.fqn,
                        arity=None,
                        declared_type=_normalize_type(field_type),
                    )
                    symbols.append(symbol)
                    direct_edges.append(
                        _direct_edge(
                            edge_type="contains",
                            from_ref=file_id,
                            to_ref=symbol.symbol_id,
                            resolution_status="resolved",
                            confidence="high",
                            extractor="java_symbol",
                            path=file.path,
                            node=declarator,
                        )
                    )

            if "java_field_access" in enabled_extractors:
                fields_by_owner_name = {
                    (symbol.owner_type, symbol.name): symbol
                    for symbol in symbols
                    if symbol.symbol_type == "field" and symbol.owner_type is not None
                }
                for node in _walk(root):
                    if node.type not in {"method_declaration", "constructor_declaration"}:
                        continue
                    callable_symbol = callable_symbol_by_key.get(_node_key(node))
                    if callable_symbol is None or callable_symbol.owner_type is None:
                        continue
                    direct_edges.extend(
                        _field_access_edges(
                            node=node,
                            callable_symbol=callable_symbol,
                            fields_by_owner_name=fields_by_owner_name,
                            source=file.content,
                            path=file.path,
                        )
                    )

            for import_name, import_node in _import_nodes(root, file.content):
                relations.append(
                    JavaRelation(
                        relation_kind="import",
                        from_ref=file_id,
                        target=import_name,
                        source_path=file.path,
                        start_line=_line(import_node),
                        end_line=_end_line(import_node),
                        extractor="java_symbol",
                    )
                )

        return JavaFileExtraction(
            symbols=tuple(sorted(symbols, key=lambda item: item.symbol_id)),
            types=tuple(sorted(types, key=lambda item: item.fqn)),
            relations=tuple(
                sorted(
                    relations,
                    key=lambda item: (
                        item.source_path,
                        item.start_line,
                        item.relation_kind,
                        item.from_ref,
                        item.target,
                    ),
                )
            ),
            direct_edges=tuple(
                sorted(
                    direct_edges,
                    key=lambda item: (
                        item.source_path,
                        item.start_line,
                        item.edge_type,
                        item.from_ref,
                        item.to_ref,
                    ),
                )
            ),
            lambda_expressions=tuple(
                sorted(
                    lambda_expressions,
                    key=lambda item: (
                        item.source_path,
                        item.start_line,
                        item.origin_object_name,
                        item.origin_method_name,
                    ),
                )
            ),
            diagnostics=tuple(sorted(set(diagnostics))),
        )


_TYPE_DECLARATIONS = {
    "annotation_type_declaration": "annotation",
    "class_declaration": "class",
    "enum_declaration": "enum",
    "interface_declaration": "interface",
    "record_declaration": "record",
}
_TEST_ANNOTATIONS = frozenset(
    {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate"}
)
_MAPPING_METHODS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": "ANY",
}
_WHITESPACE = re.compile(r"\s+")
_STRING_LITERAL = re.compile(r'"((?:\\.|[^"\\])*)"')
_REQUEST_METHOD = re.compile(r"RequestMethod\.([A-Z]+)")


def _declared_variable_types(node: Node, source: bytes) -> dict[str, str]:
    """Return explicit parameter/local receiver types within one callable."""

    declared: dict[str, str] = {}
    for candidate in _walk(node):
        if candidate.type not in {
            "formal_parameter",
            "spread_parameter",
            "local_variable_declaration",
            "enhanced_for_statement",
        }:
            continue
        type_name = _field_text(candidate, "type", source)
        if type_name is None:
            continue
        if candidate.type == "local_variable_declaration":
            declarators = (
                child for child in candidate.named_children if child.type == "variable_declarator"
            )
            for declarator in declarators:
                name = _field_text(declarator, "name", source)
                if name is not None:
                    declared[name] = type_name
            continue
        name = _field_text(candidate, "name", source)
        if name is not None:
            declared[name] = type_name
    return declared


def _receiver_declared_type(
    object_name: str | None, *, variable_types: dict[str, str]
) -> str | None:
    if object_name is None:
        return None
    root = re.match(r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)", object_name)
    return variable_types.get(root.group("name")) if root is not None else None


def _lambda_expression(
    node: Node, *, owner_type: str, source: bytes, path: str
) -> JavaLambdaExpression | None:
    arguments = node.parent
    terminal = (
        arguments.parent if arguments is not None and arguments.type == "argument_list" else None
    )
    if terminal is None or terminal.type != "method_invocation":
        return None
    origin = terminal.child_by_field_name("object")
    if origin is None or origin.type != "method_invocation":
        return None
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return None
    parameter_names: tuple[str, ...]
    if parameters.type == "identifier":
        parameter_names = (_text(parameters, source),)
    else:
        parameter_names = tuple(
            name
            for child in parameters.named_children
            if (name := _field_text(child, "name", source)) is not None
        )
    origin_object = _field_text(origin, "object", source)
    origin_method = _field_text(origin, "name", source)
    terminal_method = _field_text(terminal, "name", source)
    if (
        not parameter_names
        or origin_object is None
        or origin_method is None
        or terminal_method is None
    ):
        return None
    return JavaLambdaExpression(
        parameter_names=parameter_names,
        origin_object_name=origin_object,
        origin_method_name=origin_method,
        terminal_method_name=terminal_method,
        owner_type=owner_type,
        source_path=path,
        start_line=_line(node),
        end_line=_end_line(node),
    )


def _field_access_edges(
    *,
    node: Node,
    callable_symbol: JavaSymbol,
    fields_by_owner_name: dict[tuple[str, str], JavaSymbol],
    source: bytes,
    path: str,
) -> list[JavaDirectEdge]:
    """Extract proven accesses to fields owned by the current Java type."""

    owner = callable_symbol.owner_type
    if owner is None:
        return []
    local_names = set(_declared_variable_types(node, source))
    seen_nodes: set[tuple[int, int, str]] = set()
    values: list[JavaDirectEdge] = []
    for candidate in _descendants_owned_by(node, "field_access"):
        if _field_text(candidate, "object", source) != "this":
            continue
        name = _field_text(candidate, "field", source)
        field = fields_by_owner_name.get((owner, name or ""))
        if field is None:
            continue
        seen_nodes.update(_node_key(value) for value in _walk(candidate))
        values.extend(
            _access_edges_for_node(
                candidate=candidate,
                callable_symbol=callable_symbol,
                field=field,
                path=path,
            )
        )
    for candidate in _descendants_owned_by(node, "identifier"):
        if _node_key(candidate) in seen_nodes:
            continue
        name = _text(candidate, source)
        if name in local_names:
            continue
        field = fields_by_owner_name.get((owner, name))
        if field is None or _identifier_is_declaration_name(candidate):
            continue
        values.extend(
            _access_edges_for_node(
                candidate=candidate,
                callable_symbol=callable_symbol,
                field=field,
                path=path,
            )
        )
    return values


def _access_edges_for_node(
    *,
    candidate: Node,
    callable_symbol: JavaSymbol,
    field: JavaSymbol,
    path: str,
) -> list[JavaDirectEdge]:
    access_types = _field_access_types(candidate)
    return [
        JavaDirectEdge(
            edge_type=edge_type,
            from_ref=callable_symbol.symbol_id,
            to_ref=field.symbol_id,
            resolution_status="resolved",
            confidence="high",
            extractor="java_field_access",
            source_path=path,
            start_line=_line(candidate),
            end_line=_end_line(candidate),
        )
        for edge_type in access_types
    ]


def _field_access_types(candidate: Node) -> tuple[str, ...]:
    parent = candidate.parent
    while parent is not None and parent.type in {"parenthesized_expression", "field_access"}:
        candidate = parent
        parent = candidate.parent
    if parent is None:
        return ("reads",)
    if parent.type == "update_expression":
        return ("reads", "writes")
    if parent.type == "assignment_expression":
        left = parent.child_by_field_name("left")
        if left is not None and (
            left == candidate
            or (left.start_byte <= candidate.start_byte and candidate.end_byte <= left.end_byte)
        ):
            operator = next(
                (child.type for child in parent.children if child.type.endswith("=")), "="
            )
            return ("writes",) if operator == "=" else ("reads", "writes")
    return ("reads",)


def _identifier_is_declaration_name(node: Node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    return parent.child_by_field_name("name") == node and parent.type in {
        "formal_parameter",
        "spread_parameter",
        "variable_declarator",
    }


def code_file_id(path: str) -> str:
    return f"file-{sha256(path.encode()).hexdigest()[:24]}"


def code_edge_id(edge: JavaDirectEdge, *, profile_ref: str) -> str:
    material = "\x00".join(
        (
            edge.edge_type,
            edge.from_ref,
            edge.to_ref,
            edge.extractor,
            profile_ref,
            edge.source_path,
            str(edge.start_line),
            str(edge.end_line),
        )
    )
    return f"edge-{sha256(material.encode()).hexdigest()[:24]}"


def _symbol(
    *,
    file_id: str,
    path: str,
    symbol_type: str,
    name: str,
    signature: str,
    node: Node,
    owner_type: str | None,
    arity: int | None,
    declared_type: str | None,
) -> JavaSymbol:
    material = "\x00".join((path, symbol_type, signature))
    return JavaSymbol(
        symbol_id=f"symbol-{sha256(material.encode()).hexdigest()[:24]}",
        file_id=file_id,
        path=path,
        symbol_type=symbol_type,
        name=name,
        signature=signature,
        start_line=_line(node),
        end_line=_end_line(node),
        owner_type=owner_type,
        arity=arity,
        declared_type=declared_type,
    )


def _direct_edge(
    *,
    edge_type: str,
    from_ref: str,
    to_ref: str,
    resolution_status: str,
    confidence: str,
    extractor: str,
    path: str,
    node: Node,
) -> JavaDirectEdge:
    return JavaDirectEdge(
        edge_type=edge_type,
        from_ref=from_ref,
        to_ref=to_ref,
        resolution_status=resolution_status,
        confidence=confidence,
        extractor=extractor,
        source_path=path,
        start_line=_line(node),
        end_line=_end_line(node),
    )


def _walk(node: Node) -> tuple[Node, ...]:
    values: list[Node] = [node]
    for child in node.named_children:
        values.extend(_walk(child))
    return tuple(values)


def _descendants_owned_by(node: Node, node_type: str) -> tuple[Node, ...]:
    return tuple(
        candidate
        for candidate in _walk(node)
        if candidate.type == node_type and _nearest_callable(candidate) == node
    )


def _nearest_callable(node: Node) -> Node | None:
    current = node.parent
    while current is not None:
        if current.type in {"method_declaration", "constructor_declaration"}:
            return current
        current = current.parent
    return None


def _nearest_type(node: Node) -> Node | None:
    current = node.parent
    while current is not None:
        if current.type in _TYPE_DECLARATIONS:
            return current
        current = current.parent
    return None


def _enclosing_type_names(node: Node, content: bytes) -> tuple[str, ...]:
    values: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in _TYPE_DECLARATIONS:
            name = _field_text(current, "name", content)
            if name is not None:
                values.append(name)
        current = current.parent
    return tuple(reversed(values))


def _package_name(root: Node, content: bytes) -> str:
    for child in root.named_children:
        if child.type == "package_declaration":
            value = _text(child, content).removeprefix("package").removesuffix(";")
            return _WHITESPACE.sub("", value)
    return ""


def _imports(root: Node, content: bytes) -> tuple[str, ...]:
    return tuple(value for value, _ in _import_nodes(root, content))


def _import_nodes(root: Node, content: bytes) -> tuple[tuple[str, Node], ...]:
    values: list[tuple[str, Node]] = []
    for child in root.named_children:
        if child.type != "import_declaration":
            continue
        value = _text(child, content).removeprefix("import").removesuffix(";").strip()
        value = value.removeprefix("static ").strip()
        values.append((_WHITESPACE.sub("", value), child))
    return tuple(values)


def _parameter_types(node: Node, content: bytes) -> tuple[str, ...]:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return ()
    values: list[str] = []
    for parameter in parameters.named_children:
        type_node = parameter.child_by_field_name("type")
        if type_node is None:
            continue
        value = _normalize_type(_text(type_node, content))
        if parameter.type == "spread_parameter":
            value = f"{value}..."
        values.append(value)
    return tuple(values)


def _type_list_values(node: Node, content: bytes) -> tuple[str, ...]:
    type_list = next((child for child in node.named_children if child.type == "type_list"), node)
    return tuple(_normalize_type(_text(child, content)) for child in type_list.named_children)


def _annotations(node: Node | None, content: bytes) -> tuple[tuple[str, str], ...]:
    if node is None:
        return ()
    modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
    if modifiers is None:
        return ()
    values: list[tuple[str, str]] = []
    for child in modifiers.named_children:
        if child.type not in {"annotation", "marker_annotation"}:
            continue
        name = _field_text(child, "name", content)
        if name is not None:
            values.append((name, _text(child, content)))
    return tuple(values)


def _mapping_paths(annotations: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    paths: list[str] = []
    for name, source in annotations:
        if _simple_annotation_name(name) not in _MAPPING_METHODS:
            continue
        strings = _annotation_strings(source)
        paths.extend(strings or ("/",))
    return tuple(dict.fromkeys(paths))


def _mapping_endpoints(annotations: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for name, source in annotations:
        simple_name = _simple_annotation_name(name)
        default_method = _MAPPING_METHODS.get(simple_name)
        if default_method is None:
            continue
        methods = _REQUEST_METHOD.findall(source) if simple_name == "RequestMapping" else []
        http_methods = tuple(methods) or (default_method,)
        paths = _annotation_strings(source) or ("/",)
        values.extend((method, path) for method in http_methods for path in paths)
    return tuple(dict.fromkeys(values))


def _annotation_strings(source: str) -> tuple[str, ...]:
    return tuple(
        value.replace(r"\"", '"').replace(r"\\", "\\") for value in _STRING_LITERAL.findall(source)
    )


def _simple_annotation_name(value: str) -> str:
    return value.rsplit(".", maxsplit=1)[-1]


def _join_url_paths(base: str, child: str) -> str:
    parts = [value.strip("/") for value in (base, child) if value.strip("/")]
    return f"/{'/'.join(parts)}" if parts else "/"


def _normalize_type(value: str) -> str:
    return _WHITESPACE.sub("", value)


def _field_text(node: Node, field: str, content: bytes) -> str | None:
    child = node.child_by_field_name(field)
    return _text(child, content) if child is not None else None


def _text(node: Node, content: bytes) -> str:
    return content[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line(node: Node) -> int:
    return node.start_point.row + 1


def _end_line(node: Node) -> int:
    return node.end_point.row + 1


def _node_key(node: Node) -> tuple[int, int, str]:
    return (node.start_byte, node.end_byte, node.type)
