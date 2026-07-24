"""Tree-sitter semantic adapters for JavaScript, TypeScript, Python, and Kotlin."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath

import tree_sitter_javascript
import tree_sitter_kotlin
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from operamind.infrastructure.code_graph.java import (
    JavaDirectEdge,
    code_file_id,
)
from operamind.infrastructure.code_graph.workspace import DiscoveredCodeFile

SEMANTIC_EXTRACTOR_BY_LANGUAGE = {
    "javascript": "javascript_symbol",
    "kotlin": "kotlin_symbol",
    "python": "python_symbol",
    "typescript": "typescript_symbol",
}

_TYPE_NODES = {
    "javascript": {
        "class_declaration": "class",
    },
    "typescript": {
        "abstract_class_declaration": "class",
        "class_declaration": "class",
        "enum_declaration": "enum",
        "interface_declaration": "interface",
        "type_alias_declaration": "type_alias",
    },
    "python": {
        "class_definition": "class",
    },
    "kotlin": {
        "class_declaration": "class",
        "companion_object": "object",
        "enum_class_body": "enum",
        "object_declaration": "object",
    },
}

_CALLABLE_NODES = {
    "javascript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
    },
    "typescript": {
        "abstract_method_signature": "method",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "method_signature": "method",
    },
    "python": {
        "function_definition": "function",
        "lambda": "lambda",
    },
    "kotlin": {
        "anonymous_function": "lambda",
        "function_declaration": "function",
        "lambda_literal": "lambda",
    },
}

_CALL_NODES = {
    "javascript": "call_expression",
    "typescript": "call_expression",
    "python": "call",
    "kotlin": "call_expression",
}

_IMPORT_NODES = {
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
    "python": {"import_statement", "import_from_statement"},
    "kotlin": {"import"},
}

_ARGUMENT_NODE_TYPES = {
    "javascript": {"arguments"},
    "typescript": {"arguments"},
    "python": {"argument_list"},
    "kotlin": {"value_arguments"},
}

_PARAMETER_NODE_TYPES = {
    "javascript": {"formal_parameters"},
    "typescript": {"formal_parameters"},
    "python": {"parameters"},
    "kotlin": {"function_value_parameters"},
}

_PARAMETER_TYPES = {
    "javascript": {
        "identifier",
        "object_pattern",
        "array_pattern",
        "assignment_pattern",
        "rest_pattern",
    },
    "typescript": {
        "identifier",
        "object_pattern",
        "array_pattern",
        "assignment_pattern",
        "rest_pattern",
        "required_parameter",
        "optional_parameter",
    },
    "python": {
        "identifier",
        "default_parameter",
        "typed_parameter",
        "typed_default_parameter",
        "list_splat",
        "dictionary_splat",
    },
    "kotlin": {"parameter"},
}


@dataclass(frozen=True, slots=True)
class SemanticSymbol:
    """One language-neutral declaration emitted by a semantic adapter."""

    symbol_id: str
    file_id: str
    path: str
    language: str
    role: str
    symbol_type: str
    name: str
    signature: str
    start_line: int
    end_line: int
    owner_signature: str | None
    arity: int | None
    declared_type: str | None = None

    def to_artifact(self) -> dict[str, object]:
        artifact: dict[str, object] = {
            "symbol_id": self.symbol_id,
            "symbol_type": self.symbol_type,
            "name": self.name,
            "signature": self.signature,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
        if self.declared_type:
            artifact["declared_type"] = self.declared_type
        return artifact


@dataclass(frozen=True, slots=True)
class SemanticRelation:
    """A relation candidate resolved only after the complete bounded file set is parsed."""

    relation_kind: str
    from_ref: str
    target: str
    source_path: str
    source_language: str
    source_role: str
    start_line: int
    end_line: int
    extractor: str
    arity: int | None = None
    import_source: str | None = None
    imported_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticFileExtraction:
    """Symbols, relation candidates, and direct containment edges for one file."""

    symbols: tuple[SemanticSymbol, ...]
    relations: tuple[SemanticRelation, ...]
    direct_edges: tuple[JavaDirectEdge, ...]
    diagnostics: tuple[str, ...]


class SemanticAdapterRegistry:
    """Select the exact Tree-sitter grammar for a supported semantic language."""

    def __init__(self) -> None:
        self._parsers = {
            "javascript": Parser(Language(tree_sitter_javascript.language())),
            "python": Parser(Language(tree_sitter_python.language())),
            "kotlin": Parser(Language(tree_sitter_kotlin.language())),
        }
        self._typescript = Parser(Language(tree_sitter_typescript.language_typescript()))
        self._tsx = Parser(Language(tree_sitter_typescript.language_tsx()))

    @property
    def languages(self) -> frozenset[str]:
        return frozenset(SEMANTIC_EXTRACTOR_BY_LANGUAGE)

    def extract(
        self,
        *,
        file: DiscoveredCodeFile,
        enabled_extractors: frozenset[str],
    ) -> SemanticFileExtraction:
        extractor = SEMANTIC_EXTRACTOR_BY_LANGUAGE.get(file.language)
        if extractor is None:
            raise ValueError(f"No semantic Adapter is registered for {file.language}")
        if extractor not in enabled_extractors:
            return SemanticFileExtraction((), (), (), ())
        parser = (
            self._tsx
            if file.language == "typescript"
            and PurePosixPath(file.path).suffix.casefold() == ".tsx"
            else self._typescript
            if file.language == "typescript"
            else self._parsers[file.language]
        )
        tree = parser.parse(file.content)
        root = tree.root_node
        diagnostics = (
            (f"tree_sitter_parse_error:{file.language}:{file.path}",) if root.has_error else ()
        )
        module_name = _module_name(file, root)
        symbol_by_node: dict[tuple[int, int, str], SemanticSymbol] = {}
        symbols: list[SemanticSymbol] = []
        direct_edges: list[JavaDirectEdge] = []

        for node in _walk(root):
            symbol_type = _TYPE_NODES[file.language].get(node.type)
            if symbol_type is None:
                continue
            name = _declaration_name(node, file.content)
            if not name:
                continue
            owner = _nearest_symbol(node, symbol_by_node, _TYPE_NODES[file.language])
            owner_signature = owner.signature if owner is not None else None
            signature = f"{owner_signature}.{name}" if owner_signature else f"{module_name}.{name}"
            symbol = _symbol(
                file=file,
                node=node,
                symbol_type=symbol_type,
                name=name,
                signature=signature,
                owner_signature=owner_signature,
                arity=None,
                declared_type=None,
            )
            symbols.append(symbol)
            symbol_by_node[_node_key(node)] = symbol
            direct_edges.append(_contains_edge(file, node, symbol, extractor))

        for node in _walk(root):
            symbol_type = _CALLABLE_NODES[file.language].get(node.type)
            if symbol_type is None:
                continue
            name = _callable_name(node, file.content)
            if not name:
                continue
            owner = _nearest_symbol(node, symbol_by_node, _TYPE_NODES[file.language])
            owner_signature = owner.signature if owner is not None else module_name
            arity = _parameter_count(node, file.content, file.language)
            signature = f"{owner_signature}#{name}/{arity}"
            declared_type = _declared_return_type(node, file.content, file.language)
            symbol = _symbol(
                file=file,
                node=node,
                symbol_type=(
                    "method" if owner is not None and symbol_type == "function" else symbol_type
                ),
                name=name,
                signature=signature,
                owner_signature=owner.signature if owner is not None else None,
                arity=arity,
                declared_type=declared_type,
            )
            symbols.append(symbol)
            symbol_by_node[_node_key(node)] = symbol
            direct_edges.append(_contains_edge(file, node, symbol, extractor))

        if file.language in {"javascript", "typescript"}:
            for node in _walk(root):
                if node.type != "variable_declarator":
                    continue
                value = node.child_by_field_name("value")
                if value is None or value.type not in {
                    "arrow_function",
                    "function_expression",
                    "generator_function",
                }:
                    continue
                name = _declaration_name(node, file.content)
                if not name:
                    continue
                owner = _nearest_symbol(
                    node,
                    symbol_by_node,
                    _TYPE_NODES[file.language],
                )
                owner_signature = owner.signature if owner is not None else module_name
                arity = _parameter_count(value, file.content, file.language)
                symbol = _symbol(
                    file=file,
                    node=node,
                    symbol_type="method" if owner is not None else "function",
                    name=name,
                    signature=f"{owner_signature}#{name}/{arity}",
                    owner_signature=owner.signature if owner is not None else None,
                    arity=arity,
                    declared_type=None,
                )
                symbols.append(symbol)
                symbol_by_node[_node_key(node)] = symbol
                symbol_by_node[_node_key(value)] = symbol
                direct_edges.append(_contains_edge(file, node, symbol, extractor))

        relations: list[SemanticRelation] = []
        for node in _walk(root):
            if node.type in _IMPORT_NODES[file.language]:
                target = _import_target(node, file.content, file.language)
                if target:
                    relations.append(
                        SemanticRelation(
                            relation_kind="import",
                            from_ref=code_file_id(file.path),
                            target=target,
                            source_path=file.path,
                            source_language=file.language,
                            source_role=file.role,
                            start_line=_line(node),
                            end_line=_end_line(node),
                            extractor=extractor,
                            imported_names=_imported_names(
                                node,
                                file.content,
                                file.language,
                            ),
                        )
                    )
            if node.type == _CALL_NODES[file.language]:
                target = _call_target(node, file.content, file.language)
                if target:
                    owner = _nearest_callable_symbol(node, symbol_by_node, file.language)
                    relations.append(
                        SemanticRelation(
                            relation_kind="call",
                            from_ref=owner.symbol_id
                            if owner is not None
                            else code_file_id(file.path),
                            target=target,
                            source_path=file.path,
                            source_language=file.language,
                            source_role=file.role,
                            start_line=_line(node),
                            end_line=_end_line(node),
                            extractor=extractor,
                            arity=_argument_count(node, file.language),
                        )
                    )
            if node.type in _TYPE_NODES[file.language]:
                owner = symbol_by_node.get(_node_key(node))
                if owner is not None:
                    relations.extend(
                        SemanticRelation(
                            relation_kind="implements",
                            from_ref=owner.symbol_id,
                            target=target,
                            source_path=file.path,
                            source_language=file.language,
                            source_role=file.role,
                            start_line=_line(node),
                            end_line=_end_line(node),
                            extractor=extractor,
                        )
                        for target in _supertype_names(node, file.content, file.language)
                    )
        return SemanticFileExtraction(
            symbols=tuple(symbols),
            relations=tuple(relations),
            direct_edges=tuple(direct_edges),
            diagnostics=diagnostics,
        )


def resolve_semantic_relations(
    *,
    relations: tuple[SemanticRelation, ...],
    symbols: tuple[SemanticSymbol, ...],
    files: tuple[DiscoveredCodeFile, ...],
) -> tuple[JavaDirectEdge, ...]:
    """Resolve imports, inheritance, and calls without inventing ambiguous targets."""

    file_by_path = {file.path: file for file in files}
    file_id_by_module: dict[str, list[str]] = {}
    for file in files:
        for alias in _module_aliases(file.path):
            file_id_by_module.setdefault(alias, []).append(code_file_id(file.path))
    type_by_name: dict[str, list[SemanticSymbol]] = {}
    callable_by_name_arity: dict[tuple[str, int], list[SemanticSymbol]] = {}
    symbol_by_id = {symbol.symbol_id: symbol for symbol in symbols}
    for symbol in symbols:
        if symbol.symbol_type in {"class", "enum", "interface", "object", "type_alias"}:
            type_by_name.setdefault(symbol.name, []).append(symbol)
            type_by_name.setdefault(symbol.signature, []).append(symbol)
        if symbol.arity is not None:
            callable_by_name_arity.setdefault((symbol.name, symbol.arity), []).append(symbol)

    edges: list[JavaDirectEdge] = []
    for relation in relations:
        if relation.relation_kind == "import":
            target_ref, status = _resolve_import(
                relation,
                file_by_path=file_by_path,
                file_id_by_module=file_id_by_module,
                type_by_name=type_by_name,
            )
            edge_type = "imports"
            confidence = "high"
        elif relation.relation_kind == "implements":
            compatible_languages = _compatible_languages(relation.source_language)
            type_candidates = tuple(
                value
                for value in _unique_symbols(type_by_name.get(relation.target, []))
                if value.language in compatible_languages
            )
            target_ref, status = _candidate_target(
                type_candidates,
                unresolved=f"unresolved:type:{relation.target}",
                external=f"external:type:{relation.target}",
                imported=_is_imported_target(relation, relations),
            )
            edge_type = "implements"
            confidence = "high" if status != "unresolved" else "low"
        else:
            arity = relation.arity if relation.arity is not None else 0
            target_name = relation.target.rsplit(".", maxsplit=1)[-1]
            callable_candidates = tuple(
                value
                for value in _unique_symbols(callable_by_name_arity.get((target_name, arity), []))
                if value.symbol_id != relation.from_ref
                and value.language in _compatible_languages(relation.source_language)
            )
            target_ref, status = _candidate_target(
                callable_candidates,
                unresolved=f"unresolved:call:{relation.target}/{arity}",
                external=f"external:call:{relation.target}/{arity}",
                imported=(
                    _is_imported_target(relation, relations)
                    or target_name in _BUILTIN_CALLS[relation.source_language]
                ),
            )
            target_symbol = symbol_by_id.get(target_ref)
            edge_type = (
                "tests"
                if relation.source_role == "test"
                and target_symbol is not None
                and target_symbol.role == "production"
                else "calls"
            )
            confidence = "high" if status == "resolved" else "medium"
        edges.append(
            JavaDirectEdge(
                edge_type=edge_type,
                from_ref=relation.from_ref,
                to_ref=target_ref,
                resolution_status=status,
                confidence=confidence,
                extractor=relation.extractor,
                source_path=relation.source_path,
                start_line=relation.start_line,
                end_line=relation.end_line,
            )
        )
    return tuple(edges)


def _resolve_import(
    relation: SemanticRelation,
    *,
    file_by_path: dict[str, DiscoveredCodeFile],
    file_id_by_module: dict[str, list[str]],
    type_by_name: dict[str, list[SemanticSymbol]],
) -> tuple[str, str]:
    target = relation.target
    if relation.source_language in {"javascript", "typescript"} and target.startswith("."):
        base = PurePosixPath(relation.source_path).parent
        normalized = _normalize_relative_module(base, target)
        matches = [
            code_file_id(path)
            for path, file in file_by_path.items()
            if file.language in _compatible_languages(relation.source_language)
            and _path_without_extension(path) in {normalized, f"{normalized}/index"}
        ]
        unique_matches = tuple(dict.fromkeys(matches))
        if len(unique_matches) == 1:
            return unique_matches[0], "resolved"
        if len(unique_matches) > 1:
            return f"unresolved:module:{target}", "unresolved"
    compatible_file_ids = {
        code_file_id(path)
        for path, file in file_by_path.items()
        if file.language in _compatible_languages(relation.source_language)
    }
    module_matches = tuple(
        value
        for value in dict.fromkeys(file_id_by_module.get(target, []))
        if value in compatible_file_ids
    )
    if len(module_matches) == 1:
        return module_matches[0], "resolved"
    if len(module_matches) > 1:
        return f"unresolved:module:{target}", "unresolved"
    type_matches = tuple(
        value
        for value in _unique_symbols(type_by_name.get(target, []))
        if value.language in _compatible_languages(relation.source_language)
    )
    if len(type_matches) == 1:
        return type_matches[0].symbol_id, "resolved"
    if len(type_matches) > 1:
        return f"unresolved:module:{target}", "unresolved"
    return f"external:module:{target}", "external"


def _candidate_target(
    candidates: tuple[SemanticSymbol, ...],
    *,
    unresolved: str,
    external: str,
    imported: bool,
) -> tuple[str, str]:
    if len(candidates) == 1:
        return candidates[0].symbol_id, "resolved"
    if len(candidates) > 1:
        return unresolved, "unresolved"
    return (external, "external") if imported else (unresolved, "unresolved")


def _is_imported_target(
    relation: SemanticRelation,
    all_relations: tuple[SemanticRelation, ...],
) -> bool:
    target_root = relation.target.split(".", maxsplit=1)[0]
    return any(
        value.relation_kind == "import"
        and value.source_path == relation.source_path
        and (
            target_root in value.imported_names
            or value.target == target_root
            or value.target.endswith(f".{target_root}")
            or relation.target.startswith(f"{value.target}.")
        )
        for value in all_relations
    )


def _symbol(
    *,
    file: DiscoveredCodeFile,
    node: Node,
    symbol_type: str,
    name: str,
    signature: str,
    owner_signature: str | None,
    arity: int | None,
    declared_type: str | None,
) -> SemanticSymbol:
    material = "\x00".join((file.path, symbol_type, signature))
    return SemanticSymbol(
        symbol_id=f"symbol-{sha256(material.encode()).hexdigest()[:24]}",
        file_id=code_file_id(file.path),
        path=file.path,
        language=file.language,
        role=file.role,
        symbol_type=symbol_type,
        name=name,
        signature=signature,
        start_line=_line(node),
        end_line=_end_line(node),
        owner_signature=owner_signature,
        arity=arity,
        declared_type=declared_type,
    )


def _contains_edge(
    file: DiscoveredCodeFile,
    node: Node,
    symbol: SemanticSymbol,
    extractor: str,
) -> JavaDirectEdge:
    return JavaDirectEdge(
        edge_type="contains",
        from_ref=code_file_id(file.path),
        to_ref=symbol.symbol_id,
        resolution_status="resolved",
        confidence="high",
        extractor=extractor,
        source_path=file.path,
        start_line=_line(node),
        end_line=_end_line(node),
    )


def _declaration_name(node: Node, source: bytes) -> str | None:
    name = node.child_by_field_name("name")
    if name is not None:
        return _text(name, source)
    return next(
        (
            _text(child, source)
            for child in node.named_children
            if child.type in {"identifier", "type_identifier"}
        ),
        None,
    )


def _callable_name(node: Node, source: bytes) -> str | None:
    if node.type in {"lambda", "lambda_literal", "anonymous_function"}:
        return f"<lambda@{_line(node)}>"
    return _declaration_name(node, source)


def _declared_return_type(node: Node, source: bytes, language: str) -> str | None:
    if language not in {"python", "typescript", "javascript"}:
        return None
    value = node.child_by_field_name("return_type")
    if value is None:
        return None
    return _normalize_source(_text(value, source).removeprefix(":").strip()) or None


def _parameter_count(node: Node, source: bytes, language: str) -> int:
    parameter_node = next(
        (child for child in node.named_children if child.type in _PARAMETER_NODE_TYPES[language]),
        None,
    )
    if parameter_node is None:
        return 0
    parameters = [
        child for child in parameter_node.named_children if child.type in _PARAMETER_TYPES[language]
    ]
    if language == "python" and parameters and _text(parameters[0], source) in {"self", "cls"}:
        return len(parameters) - 1
    return len(parameters)


def _argument_count(node: Node, language: str) -> int:
    arguments = next(
        (child for child in node.named_children if child.type in _ARGUMENT_NODE_TYPES[language]),
        None,
    )
    return len(arguments.named_children) if arguments is not None else 0


def _import_target(node: Node, source: bytes, language: str) -> str | None:
    if language in {"javascript", "typescript"}:
        source_node = node.child_by_field_name("source")
        return _strip_string(_text(source_node, source)) if source_node is not None else None
    if language == "python":
        module = node.child_by_field_name("module_name")
        if module is not None:
            return _text(module, source)
        text = _text(node, source).removeprefix("import").strip()
        return text.split(",", maxsplit=1)[0].split(" as ", maxsplit=1)[0].strip()
    qualified = next(
        (child for child in node.named_children if child.type == "qualified_identifier"),
        None,
    )
    return _text(qualified, source) if qualified is not None else None


def _imported_names(node: Node, source: bytes, language: str) -> tuple[str, ...]:
    text = _text(node, source).strip()
    if language in {"javascript", "typescript"}:
        if " from " not in text:
            return ()
        clause = text.removeprefix("import").split(" from ", maxsplit=1)[0].strip()
        clause = clause.removeprefix("type ").strip()
        names: list[str] = []
        default_clause = clause.split(",", maxsplit=1)[0].strip()
        if default_clause and not default_clause.startswith(("{", "*")):
            names.append(default_clause)
        namespace_alias = re.search(r"\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)", clause)
        if namespace_alias is not None:
            names.append(namespace_alias.group(1))
        named_clause = re.search(r"\{(?P<names>.*?)\}", clause, flags=re.DOTALL)
        if named_clause is not None:
            for value in named_clause.group("names").split(","):
                candidate = value.strip().removeprefix("type ").strip()
                if not candidate:
                    continue
                names.append(
                    candidate.rsplit(" as ", maxsplit=1)[-1].strip()
                    if " as " in candidate
                    else candidate
                )
        return tuple(dict.fromkeys(name for name in names if name.isidentifier()))
    if language == "python":
        if text.startswith("from ") and " import " in text:
            clause = text.split(" import ", maxsplit=1)[1]
        elif text.startswith("import "):
            clause = text.removeprefix("import ")
        else:
            return ()
        names = []
        for value in clause.strip("()").split(","):
            candidate = value.strip()
            if not candidate or candidate == "*":
                continue
            local_name = (
                candidate.rsplit(" as ", maxsplit=1)[-1]
                if " as " in candidate
                else candidate.split(".", maxsplit=1)[0]
            )
            names.append(local_name.strip())
        return tuple(dict.fromkeys(name for name in names if name.isidentifier()))
    if language == "kotlin":
        candidate = text.removeprefix("import").strip()
        local_name = (
            candidate.rsplit(" as ", maxsplit=1)[-1]
            if " as " in candidate
            else candidate.rsplit(".", maxsplit=1)[-1]
        )
        return (local_name,) if local_name.isidentifier() else ()
    return ()


def _call_target(node: Node, source: bytes, language: str) -> str | None:
    function = node.child_by_field_name("function")
    if function is None and language == "kotlin":
        function = node.named_children[0] if node.named_children else None
    if function is None:
        return None
    identifiers = [
        _text(value, source)
        for value in _walk(function)
        if value.type in {"identifier", "property_identifier", "type_identifier"}
    ]
    return ".".join(identifiers[-2:]) if identifiers else None


def _supertype_names(node: Node, source: bytes, language: str) -> tuple[str, ...]:
    if language == "python":
        container = node.child_by_field_name("superclasses")
    else:
        container = next(
            (
                child
                for child in node.named_children
                if child.type in {"class_heritage", "delegation_specifiers"}
            ),
            None,
        )
    if container is None:
        return ()
    values = [
        _text(value, source).split("<", maxsplit=1)[0]
        for value in _walk(container)
        if value.type in {"identifier", "type_identifier", "user_type"}
    ]
    return tuple(dict.fromkeys(value for value in values if value))


def _nearest_symbol(
    node: Node,
    symbols: dict[tuple[int, int, str], SemanticSymbol],
    allowed_nodes: dict[str, str],
) -> SemanticSymbol | None:
    current = node.parent
    while current is not None:
        if current.type in allowed_nodes:
            return symbols.get(_node_key(current))
        current = current.parent
    return None


def _nearest_callable_symbol(
    node: Node,
    symbols: dict[tuple[int, int, str], SemanticSymbol],
    language: str,
) -> SemanticSymbol | None:
    current = node.parent
    while current is not None:
        if current.type in _CALLABLE_NODES[language]:
            return symbols.get(_node_key(current))
        if language in {"javascript", "typescript"} and current.type in {
            "arrow_function",
            "function_expression",
            "generator_function",
        }:
            return symbols.get(_node_key(current))
        current = current.parent
    return None


def _module_name(file: DiscoveredCodeFile, root: Node) -> str:
    if file.language == "kotlin":
        package = next(
            (child for child in root.named_children if child.type == "package_header"),
            None,
        )
        if package is not None:
            qualified = next(
                (child for child in package.named_children if child.type == "qualified_identifier"),
                None,
            )
            if qualified is not None:
                return _text(qualified, file.content)
    return _path_without_extension(file.path).replace("/", ".")


def _module_aliases(path: str) -> tuple[str, ...]:
    base = _path_without_extension(path)
    dotted = base.replace("/", ".")
    values = {base, dotted, PurePosixPath(base).name}
    dotted_parts = dotted.split(".")
    values.update(".".join(dotted_parts[index:]) for index in range(len(dotted_parts)))
    if base.endswith("/__init__"):
        package = base.removesuffix("/__init__")
        values.update({package, package.replace("/", ".")})
    return tuple(sorted(values))


def _compatible_languages(language: str) -> frozenset[str]:
    if language in {"javascript", "typescript"}:
        return frozenset({"javascript", "typescript"})
    return frozenset({language})


def _normalize_relative_module(base: PurePosixPath, target: str) -> str:
    parts = list(base.parts)
    normalized_target = _path_without_extension(target)
    for part in PurePosixPath(normalized_target).parts:
        if part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts).as_posix()


def _path_without_extension(path: str) -> str:
    pure = PurePosixPath(path)
    return str(pure.with_suffix(""))


def _unique_symbols(values: list[SemanticSymbol]) -> tuple[SemanticSymbol, ...]:
    return tuple({value.symbol_id: value for value in values}.values())


def _walk(node: Node) -> tuple[Node, ...]:
    values: list[Node] = [node]
    for child in node.named_children:
        values.extend(_walk(child))
    return tuple(values)


def _node_key(node: Node) -> tuple[int, int, str]:
    return node.start_byte, node.end_byte, node.type


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    if not source:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _normalize_source(value: str) -> str:
    return "".join(value.split())


def _strip_string(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "`"}:
        return value[1:-1]
    return value


def _line(node: Node) -> int:
    return node.start_point.row + 1


def _end_line(node: Node) -> int:
    return node.end_point.row + 1


_BUILTIN_CALLS = {
    "javascript": frozenset(
        {"Array", "Boolean", "Date", "Error", "JSON", "Number", "Object", "Promise", "String"}
    ),
    "typescript": frozenset(
        {"Array", "Boolean", "Date", "Error", "JSON", "Number", "Object", "Promise", "String"}
    ),
    "python": frozenset(
        {
            "dict",
            "enumerate",
            "filter",
            "float",
            "int",
            "len",
            "list",
            "map",
            "print",
            "range",
            "set",
            "str",
            "tuple",
            "zip",
        }
    ),
    "kotlin": frozenset(
        {"arrayOf", "emptyList", "listOf", "mapOf", "mutableListOf", "println", "setOf"}
    ),
}
