"""Deterministic Struts 1 configuration, Java, Tiles, and JSP graph extraction."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath

from operamind.infrastructure.code_graph.java import (
    JavaDirectEdge,
    JavaSymbol,
    JavaType,
    code_file_id,
)
from operamind.infrastructure.code_graph.workspace import DiscoveredCodeFile

STRUTS1_EXTRACTOR = "struts1_mvc"


@dataclass(frozen=True, slots=True)
class Struts1GraphResult:
    """Symbols, edges, and fail-closed diagnostics emitted by the Adapter."""

    symbol_additions: dict[str, tuple[dict[str, object], ...]]
    edges: tuple[JavaDirectEdge, ...]
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Target:
    ref: str
    resolution_status: str
    confidence: str


@dataclass(frozen=True, slots=True)
class _FormFact:
    config_path: str
    name: str
    java_type: str
    symbol_id: str
    line: int


@dataclass(frozen=True, slots=True)
class _ForwardFact:
    config_path: str
    action_path: str | None
    name: str
    target_path: str
    redirect: bool
    symbol_id: str
    line: int


@dataclass(frozen=True, slots=True)
class _ActionFact:
    config_path: str
    path: str
    java_type: str | None
    form_name: str | None
    input_path: str | None
    parameter: str | None
    direct_path: str | None
    symbol_id: str
    line: int


@dataclass(frozen=True, slots=True)
class _TileFact:
    path: str
    name: str
    page: str | None
    parent: str | None
    put_values: tuple[str, ...]
    symbol_id: str
    line: int


@dataclass(frozen=True, slots=True)
class _JspRoute:
    path: str
    kind: str
    target: str
    symbol_id: str
    line: int


_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE | re.DOTALL)
_UNSAFE_XML_DECLARATION = re.compile(r"<!ENTITY|<!DOCTYPE[^>]*\[", re.IGNORECASE | re.DOTALL)
_XML_TAG = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?(?P<tag>[A-Za-z_][\w.-]*)\b", re.IGNORECASE)
_FIND_FORWARD = re.compile(
    r"\b[A-Za-z_$][\w$]*\.findForward\s*\(\s*"
    r"(?P<quote>['\"])(?P<name>[^'\"]+)(?P=quote)\s*\)"
)
_ACTION_FORWARD_LITERAL = re.compile(
    r"\bnew\s+(?:[A-Za-z_$][\w$]*\.)*ActionForward\s*\(\s*"
    r"(?P<quote>['\"])(?P<path>/[^'\"]+)(?P=quote)\s*\)"
)
_JSP_TAG = re.compile(
    r"<(?P<tag>"
    r"html:(?:form|link|rewrite)|logic:forward|tiles:(?:insert|insertDefinition)|"
    r"jsp:(?:forward|include)|form|a"
    r")\b(?P<attributes>[^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
_JSP_ATTRIBUTE = re.compile(
    r"\b(?P<name>action|href|forward|name|definition|page)\s*=\s*"
    r"(?P<quote>['\"])(?P<value>[^'\"]+)(?P=quote)",
    re.IGNORECASE,
)


def extract_struts1_graph(
    *,
    files: tuple[DiscoveredCodeFile, ...],
    java_symbols: tuple[JavaSymbol, ...],
    java_types: tuple[JavaType, ...],
    edges: tuple[JavaDirectEdge, ...],
) -> Struts1GraphResult:
    """Build a traceable Struts 1 MVC graph without guessing ambiguous targets."""

    additions: dict[str, list[dict[str, object]]] = {}
    working_edges = list(edges)
    diagnostics: list[str] = []
    forms: list[_FormFact] = []
    actions: list[_ActionFact] = []
    forwards: list[_ForwardFact] = []
    tiles: list[_TileFact] = []
    servlet_patterns: list[str] = []
    struts_config_found = False

    for file in files:
        if file.language != "xml" or PurePosixPath(file.path).suffix.casefold() == ".jsp":
            continue
        content = file.content.decode("utf-8", errors="replace")
        document_kind = _xml_document_kind(content)
        if document_kind is None:
            continue
        root, parse_diagnostic = _parse_xml(content, file.path)
        if parse_diagnostic is not None:
            diagnostics.append(parse_diagnostic)
            continue
        assert root is not None
        lines = _element_lines(content)
        if document_kind == "web-app":
            servlet_patterns.extend(_action_servlet_patterns(root))
            continue
        if document_kind == "struts-config":
            struts_config_found = True
            file_forms, file_actions, file_forwards, file_diagnostics = _extract_config(
                file=file,
                root=root,
                lines=lines,
                additions=additions,
                edges=working_edges,
            )
            forms.extend(file_forms)
            actions.extend(file_actions)
            forwards.extend(file_forwards)
            diagnostics.extend(file_diagnostics)
            continue
        file_tiles, file_diagnostics = _extract_tiles(
            file=file,
            root=root,
            lines=lines,
            additions=additions,
            edges=working_edges,
        )
        tiles.extend(file_tiles)
        diagnostics.extend(file_diagnostics)

    if not struts_config_found:
        diagnostics.append("struts1_config_not_found")

    jsp_targets = _jsp_targets(files)
    type_targets = _java_type_targets(java_types)
    methods_by_owner = _methods_by_owner(java_symbols, java_types)
    forms_by_config_name = _group_forms(forms)
    actions_by_path = _group_actions(actions)
    actions_by_scope = _group_actions_by_scope(actions)
    tiles_by_name = _group_tiles(tiles)
    forwards_by_scope_name = _group_forwards(forwards)

    for form in forms:
        working_edges.append(
            _edge(
                edge_type="maps_to",
                from_ref=form.symbol_id,
                target=_resolve_java_type(form.java_type, type_targets),
                path=form.config_path,
                line=form.line,
            )
        )

    for action in actions:
        if action.java_type is not None:
            action_type = _resolve_java_type(action.java_type, type_targets)
            working_edges.append(
                _edge(
                    edge_type="maps_to",
                    from_ref=action.symbol_id,
                    target=action_type,
                    path=action.config_path,
                    line=action.line,
                )
            )
            working_edges.append(
                _edge(
                    edge_type="calls",
                    from_ref=action.symbol_id,
                    target=_resolve_action_method(
                        action,
                        action_type=action_type,
                        methods_by_owner=methods_by_owner,
                    ),
                    path=action.config_path,
                    line=action.line,
                )
            )
        if action.form_name is not None:
            working_edges.append(
                _edge(
                    edge_type="maps_to",
                    from_ref=action.symbol_id,
                    target=_resolve_form(
                        action,
                        forms_by_config_name=forms_by_config_name,
                    ),
                    path=action.config_path,
                    line=action.line,
                )
            )
        endpoints = tuple(
            endpoint
            for pattern in dict.fromkeys(servlet_patterns)
            if (endpoint := _action_endpoint(pattern, action.path)) is not None
        )
        if endpoints:
            for endpoint in endpoints:
                working_edges.append(
                    _edge(
                        edge_type="exposes",
                        from_ref=action.symbol_id,
                        target=_Target(f"http:ANY:{endpoint}", "external", "high"),
                        path=action.config_path,
                        line=action.line,
                    )
                )
        else:
            working_edges.append(
                _edge(
                    edge_type="exposes",
                    from_ref=action.symbol_id,
                    target=_Target(
                        f"unresolved:struts_endpoint:{_normalize_action_path(action.path)}",
                        "unresolved",
                        "low",
                    ),
                    path=action.config_path,
                    line=action.line,
                )
            )
        if action.input_path is not None:
            working_edges.append(
                _edge(
                    edge_type="navigates_to",
                    from_ref=action.symbol_id,
                    target=_resolve_navigation_target(
                        action.input_path,
                        actions_by_path=actions_by_path,
                        tiles_by_name=tiles_by_name,
                        jsp_targets=jsp_targets,
                    ),
                    path=action.config_path,
                    line=action.line,
                )
            )
        direct_path = action.direct_path
        if (
            direct_path is None
            and action.parameter is not None
            and action.java_type is not None
            and action.java_type.rsplit(".", maxsplit=1)[-1] in {"ForwardAction", "IncludeAction"}
        ):
            direct_path = action.parameter
        if direct_path is not None:
            working_edges.append(
                _edge(
                    edge_type="navigates_to",
                    from_ref=action.symbol_id,
                    target=_resolve_navigation_target(
                        direct_path,
                        actions_by_path=actions_by_path,
                        tiles_by_name=tiles_by_name,
                        jsp_targets=jsp_targets,
                    ),
                    path=action.config_path,
                    line=action.line,
                )
            )

    for forward in forwards:
        if forward.action_path is not None:
            owner_actions = actions_by_scope.get(
                (forward.config_path, _normalize_action_path(forward.action_path)),
                (),
            )
            owner = _unique_ref(
                (value.symbol_id for value in owner_actions),
                unresolved=f"unresolved:struts_action:{forward.action_path}",
            )
            if owner.resolution_status == "resolved":
                working_edges.append(
                    _edge(
                        edge_type="navigates_to",
                        from_ref=owner.ref,
                        target=_Target(forward.symbol_id, "resolved", "high"),
                        path=forward.config_path,
                        line=forward.line,
                    )
                )
        working_edges.append(
            _edge(
                edge_type="navigates_to",
                from_ref=forward.symbol_id,
                target=_resolve_navigation_target(
                    forward.target_path,
                    actions_by_path=actions_by_path,
                    tiles_by_name=tiles_by_name,
                    jsp_targets=jsp_targets,
                ),
                path=forward.config_path,
                line=forward.line,
            )
        )

    for tile in tiles:
        if tile.parent is not None:
            working_edges.append(
                _edge(
                    edge_type="maps_to",
                    from_ref=tile.symbol_id,
                    target=_resolve_tile(tile.parent, tiles_by_name),
                    path=tile.path,
                    line=tile.line,
                )
            )
        for target_path in dict.fromkeys(value for value in (tile.page, *tile.put_values) if value):
            working_edges.append(
                _edge(
                    edge_type="navigates_to",
                    from_ref=tile.symbol_id,
                    target=_resolve_navigation_target(
                        target_path,
                        actions_by_path=actions_by_path,
                        tiles_by_name=tiles_by_name,
                        jsp_targets=jsp_targets,
                    ),
                    path=tile.path,
                    line=tile.line,
                )
            )

    action_owner_configs = _action_owner_configs(actions, type_targets)
    for file in files:
        if file.language == "java":
            working_edges.extend(
                _java_forward_edges(
                    file=file,
                    java_symbols=java_symbols,
                    action_owner_configs=action_owner_configs,
                    forwards_by_scope_name=forwards_by_scope_name,
                    actions_by_path=actions_by_path,
                    tiles_by_name=tiles_by_name,
                    jsp_targets=jsp_targets,
                )
            )
        if PurePosixPath(file.path).suffix.casefold() == ".jsp":
            routes = _extract_jsp_routes(file, additions=additions, edges=working_edges)
            for route in routes:
                working_edges.append(
                    _edge(
                        edge_type="calls" if route.kind == "form_action" else "navigates_to",
                        from_ref=route.symbol_id,
                        target=_resolve_jsp_route(
                            route,
                            actions_by_path=actions_by_path,
                            forwards_by_scope_name=forwards_by_scope_name,
                            tiles_by_name=tiles_by_name,
                            jsp_targets=jsp_targets,
                        ),
                        path=route.path,
                        line=route.line,
                    )
                )

    return Struts1GraphResult(
        symbol_additions={path: tuple(values) for path, values in additions.items()},
        edges=tuple(working_edges),
        diagnostics=tuple(sorted(set(diagnostics))),
    )


def _extract_config(
    *,
    file: DiscoveredCodeFile,
    root: ET.Element,
    lines: dict[str, list[int]],
    additions: dict[str, list[dict[str, object]]],
    edges: list[JavaDirectEdge],
) -> tuple[list[_FormFact], list[_ActionFact], list[_ForwardFact], list[str]]:
    forms: list[_FormFact] = []
    actions: list[_ActionFact] = []
    forwards: list[_ForwardFact] = []
    diagnostics: list[str] = []
    seen_forms: set[str] = set()
    seen_actions: set[str] = set()
    global_forward_names: set[str] = set()
    local_forward_names: dict[str, set[str]] = {}
    line_cursors: dict[str, int] = {}
    parent_by_id = {id(child): parent for parent in root.iter() for child in parent}
    for element in root.iter():
        tag = _local_name(element.tag)
        line = _next_line(tag, lines, line_cursors)
        if tag == "form-bean":
            name = _attribute(element, "name")
            java_type = _attribute(element, "type")
            if name is None or java_type is None:
                diagnostics.append(f"struts1_invalid_form_bean:{file.path}:{line}")
                continue
            if name in seen_forms:
                diagnostics.append(f"struts1_duplicate_form_name:{file.path}:{name}")
            seen_forms.add(name)
            symbol = _add_symbol(
                file=file,
                additions=additions,
                edges=edges,
                symbol_type="struts_action_form",
                name=name,
                signature=f"struts:form:{file.path}:{name}",
                line=line,
            )
            forms.append(_FormFact(file.path, name, java_type, str(symbol["symbol_id"]), line))
        elif tag == "action":
            path = _attribute(element, "path")
            java_type = _attribute(element, "type")
            direct_path = _attribute(element, "forward") or _attribute(element, "include")
            if path is None or (java_type is None and direct_path is None):
                diagnostics.append(f"struts1_invalid_action_mapping:{file.path}:{line}")
                continue
            normalized_path = _normalize_action_path(path)
            if normalized_path in seen_actions:
                diagnostics.append(f"struts1_duplicate_action_path:{file.path}:{normalized_path}")
            seen_actions.add(normalized_path)
            symbol = _add_symbol(
                file=file,
                additions=additions,
                edges=edges,
                symbol_type="struts_action_mapping",
                name=normalized_path,
                signature=f"struts:action:{file.path}:{normalized_path}",
                line=line,
            )
            actions.append(
                _ActionFact(
                    config_path=file.path,
                    path=normalized_path,
                    java_type=java_type,
                    form_name=_attribute(element, "name"),
                    input_path=_attribute(element, "input"),
                    parameter=_attribute(element, "parameter"),
                    direct_path=direct_path,
                    symbol_id=str(symbol["symbol_id"]),
                    line=line,
                )
            )
        elif tag == "forward":
            parent = parent_by_id.get(id(element))
            if parent is None:
                continue
            parent_tag = _local_name(parent.tag)
            if parent_tag == "action":
                raw_action_path = _attribute(parent, "path")
                action_path = (
                    _normalize_action_path(raw_action_path) if raw_action_path is not None else None
                )
            elif parent_tag == "global-forwards":
                action_path = None
            else:
                continue
            forward = _forward_fact(
                file=file,
                element=element,
                action_path=action_path,
                line=line,
                additions=additions,
                edges=edges,
            )
            if forward is None:
                diagnostics.append(f"struts1_invalid_action_forward:{file.path}:{line}")
                continue
            if action_path is None:
                if forward.name in global_forward_names:
                    diagnostics.append(
                        f"struts1_duplicate_global_forward:{file.path}:{forward.name}"
                    )
                global_forward_names.add(forward.name)
            else:
                names = local_forward_names.setdefault(action_path, set())
                if forward.name in names:
                    diagnostics.append(
                        f"struts1_duplicate_local_forward:{file.path}:{action_path}:{forward.name}"
                    )
                names.add(forward.name)
            forwards.append(forward)
    return forms, actions, forwards, diagnostics


def _forward_fact(
    *,
    file: DiscoveredCodeFile,
    element: ET.Element,
    action_path: str | None,
    line: int,
    additions: dict[str, list[dict[str, object]]],
    edges: list[JavaDirectEdge],
) -> _ForwardFact | None:
    name = _attribute(element, "name")
    target_path = _attribute(element, "path")
    if name is None or target_path is None:
        return None
    scope = action_path or "global"
    symbol = _add_symbol(
        file=file,
        additions=additions,
        edges=edges,
        symbol_type="struts_action_forward",
        name=name,
        signature=f"struts:forward:{file.path}:{scope}:{name}",
        line=line,
    )
    return _ForwardFact(
        config_path=file.path,
        action_path=action_path,
        name=name,
        target_path=target_path,
        redirect=(_attribute(element, "redirect") or "false").casefold() == "true",
        symbol_id=str(symbol["symbol_id"]),
        line=line,
    )


def _extract_tiles(
    *,
    file: DiscoveredCodeFile,
    root: ET.Element,
    lines: dict[str, list[int]],
    additions: dict[str, list[dict[str, object]]],
    edges: list[JavaDirectEdge],
) -> tuple[list[_TileFact], list[str]]:
    tiles: list[_TileFact] = []
    diagnostics: list[str] = []
    seen: set[str] = set()
    line_cursors: dict[str, int] = {}
    for element in root.iter():
        if _local_name(element.tag) != "definition":
            continue
        line = _next_line("definition", lines, line_cursors)
        name = _attribute(element, "name")
        if name is None:
            diagnostics.append(f"struts1_invalid_tiles_definition:{file.path}:{line}")
            continue
        if name in seen:
            diagnostics.append(f"struts1_duplicate_tiles_definition:{file.path}:{name}")
        seen.add(name)
        symbol = _add_symbol(
            file=file,
            additions=additions,
            edges=edges,
            symbol_type="tiles_definition",
            name=name,
            signature=f"tiles:definition:{file.path}:{name}",
            line=line,
        )
        put_values = tuple(
            value
            for child in element
            if _local_name(child.tag) in {"put", "put-attribute"}
            and (value := _attribute(child, "value")) is not None
            and (_looks_like_jsp(value) or value.startswith("/"))
        )
        tiles.append(
            _TileFact(
                path=file.path,
                name=name,
                page=_attribute(element, "path") or _attribute(element, "template"),
                parent=_attribute(element, "extends"),
                put_values=put_values,
                symbol_id=str(symbol["symbol_id"]),
                line=line,
            )
        )
    return tiles, diagnostics


def _extract_jsp_routes(
    file: DiscoveredCodeFile,
    *,
    additions: dict[str, list[dict[str, object]]],
    edges: list[JavaDirectEdge],
) -> tuple[_JspRoute, ...]:
    content = file.content.decode("utf-8", errors="replace")
    values: list[_JspRoute] = []
    seen: set[tuple[str, str]] = set()
    for match in _JSP_TAG.finditer(content):
        tag = match.group("tag").casefold()
        attributes = {
            value.group("name").casefold(): value.group("value")
            for value in _JSP_ATTRIBUTE.finditer(match.group("attributes"))
        }
        kind: str | None = None
        target: str | None = None
        if tag == "html:form":
            kind, target = "form_action", attributes.get("action")
        elif tag in {"html:link", "html:rewrite"}:
            if "action" in attributes:
                kind, target = "action_link", attributes["action"]
            elif "forward" in attributes:
                kind, target = "forward", attributes["forward"]
            elif "page" in attributes:
                kind, target = "jsp", attributes["page"]
            elif "href" in attributes:
                target = attributes["href"]
                kind = "action_link" if target.split("?", maxsplit=1)[0].endswith(".do") else "jsp"
        elif tag == "logic:forward":
            kind, target = "forward", attributes.get("name")
        elif tag.startswith("tiles:"):
            if "definition" in attributes:
                kind, target = "tiles", attributes["definition"]
            elif "page" in attributes:
                kind, target = "jsp", attributes["page"]
        elif tag.startswith("jsp:"):
            kind, target = "jsp", attributes.get("page")
        elif tag == "form" and "action" in attributes:
            kind, target = "form_action", attributes["action"]
        elif tag == "a" and attributes.get("href", "").split("?", maxsplit=1)[0].endswith(".do"):
            kind, target = "action_link", attributes["href"]
        if kind is None or target is None:
            continue
        if (kind, target) in seen:
            continue
        seen.add((kind, target))
        line = _line(content, match.start())
        signature = f"struts:jsp-route:{kind}:{target}"
        symbol = _add_symbol(
            file=file,
            additions=additions,
            edges=edges,
            symbol_type="struts_jsp_route",
            name=target,
            signature=signature,
            line=line,
        )
        values.append(
            _JspRoute(
                path=file.path,
                kind=kind,
                target=target,
                symbol_id=str(symbol["symbol_id"]),
                line=line,
            )
        )
    return tuple(values)


def _java_forward_edges(
    *,
    file: DiscoveredCodeFile,
    java_symbols: tuple[JavaSymbol, ...],
    action_owner_configs: dict[str, tuple[_ActionFact, ...]],
    forwards_by_scope_name: dict[tuple[str, str | None, str], tuple[_ForwardFact, ...]],
    actions_by_path: dict[str, tuple[_ActionFact, ...]],
    tiles_by_name: dict[str, tuple[_TileFact, ...]],
    jsp_targets: dict[str, tuple[str, ...]],
) -> list[JavaDirectEdge]:
    content = file.content.decode("utf-8", errors="replace")
    file_methods = [
        value for value in java_symbols if value.path == file.path and value.symbol_type == "method"
    ]
    values: list[JavaDirectEdge] = []
    for match in _FIND_FORWARD.finditer(content):
        line = _line(content, match.start())
        method = _enclosing_method(file_methods, line)
        if method is None or method.owner_type is None:
            continue
        candidates: list[_ForwardFact] = []
        owner_actions = action_owner_configs.get(method.owner_type, ())
        for action in owner_actions:
            local_candidates = forwards_by_scope_name.get(
                (action.config_path, action.path, match.group("name")),
                (),
            )
            if local_candidates:
                candidates.extend(local_candidates)
            else:
                candidates.extend(
                    forwards_by_scope_name.get(
                        (action.config_path, None, match.group("name")),
                        (),
                    )
                )
        if not owner_actions:
            candidates.extend(
                forward
                for (config_path, action_path, name), scoped in forwards_by_scope_name.items()
                if action_path is None and name == match.group("name")
                for forward in scoped
            )
        target = _unique_ref(
            (value.symbol_id for value in candidates),
            unresolved=f"unresolved:struts_forward:{match.group('name')}",
        )
        values.append(
            _edge(
                edge_type="navigates_to",
                from_ref=method.symbol_id,
                target=target,
                path=file.path,
                line=line,
            )
        )
    for match in _ACTION_FORWARD_LITERAL.finditer(content):
        line = _line(content, match.start())
        method = _enclosing_method(file_methods, line)
        if method is None:
            continue
        values.append(
            _edge(
                edge_type="navigates_to",
                from_ref=method.symbol_id,
                target=_resolve_navigation_target(
                    match.group("path"),
                    actions_by_path=actions_by_path,
                    tiles_by_name=tiles_by_name,
                    jsp_targets=jsp_targets,
                ),
                path=file.path,
                line=line,
            )
        )
    return values


def _resolve_action_method(
    action: _ActionFact,
    *,
    action_type: _Target,
    methods_by_owner: dict[str, tuple[JavaSymbol, ...]],
) -> _Target:
    assert action.java_type is not None
    if action.parameter is not None and action.java_type.rsplit(".", maxsplit=1)[-1].endswith(
        "DispatchAction"
    ):
        return _Target(
            f"unresolved:struts_dispatch:{action.java_type}:{action.parameter}",
            "unresolved",
            "medium",
        )
    if action_type.resolution_status != "resolved":
        return _Target(
            f"external:struts_action_method:{action.java_type}#execute",
            "external" if action_type.resolution_status == "external" else "unresolved",
            "medium",
        )
    owner = action_type.ref
    candidates = [
        value
        for value in methods_by_owner.get(owner, ())
        if value.name in {"execute", "perform"} and value.arity == 4
    ]
    if not candidates:
        candidates = [
            value
            for value in methods_by_owner.get(owner, ())
            if value.name in {"execute", "perform"}
        ]
    return _unique_ref(
        (value.symbol_id for value in candidates),
        unresolved=f"unresolved:struts_action_method:{action.java_type}#execute",
    )


def _resolve_java_type(
    name: str,
    targets: dict[str, tuple[JavaType, ...]],
) -> _Target:
    candidates = targets.get(name, ())
    if len(candidates) == 1:
        return _Target(candidates[0].symbol_id, "resolved", "high")
    if len(candidates) > 1:
        return _Target(f"unresolved:java_type:{name}", "unresolved", "low")
    return _Target(f"external:java_type:{name}", "external", "high")


def _resolve_form(
    action: _ActionFact,
    *,
    forms_by_config_name: dict[tuple[str, str], tuple[_FormFact, ...]],
) -> _Target:
    assert action.form_name is not None
    candidates = forms_by_config_name.get((action.config_path, action.form_name), ())
    return _unique_ref(
        (value.symbol_id for value in candidates),
        unresolved=f"unresolved:struts_form:{action.config_path}:{action.form_name}",
    )


def _resolve_navigation_target(
    target: str,
    *,
    actions_by_path: dict[str, tuple[_ActionFact, ...]],
    tiles_by_name: dict[str, tuple[_TileFact, ...]],
    jsp_targets: dict[str, tuple[str, ...]],
) -> _Target:
    if _is_dynamic(target):
        return _Target(f"unresolved:struts_navigation:{target}", "unresolved", "low")
    normalized_action = _normalize_action_path(target)
    action_candidates = actions_by_path.get(normalized_action, ())
    if action_candidates:
        return _unique_ref(
            (value.symbol_id for value in action_candidates),
            unresolved=f"unresolved:struts_action:{normalized_action}",
        )
    tile_candidates = tiles_by_name.get(target, ()) or tiles_by_name.get(
        target.removeprefix("/"),
        (),
    )
    if tile_candidates:
        return _unique_ref(
            (value.symbol_id for value in tile_candidates),
            unresolved=f"unresolved:tiles_definition:{target}",
        )
    normalized_jsp = _normalize_web_path(target)
    jsp_candidates = jsp_targets.get(normalized_jsp, ())
    if jsp_candidates:
        return _unique_ref(
            jsp_candidates,
            unresolved=f"unresolved:jsp:{normalized_jsp}",
        )
    if _looks_like_jsp(target):
        return _Target(f"external:jsp:{normalized_jsp}", "external", "medium")
    return _Target(f"external:route:{target}", "external", "medium")


def _resolve_jsp_route(
    route: _JspRoute,
    *,
    actions_by_path: dict[str, tuple[_ActionFact, ...]],
    forwards_by_scope_name: dict[tuple[str, str | None, str], tuple[_ForwardFact, ...]],
    tiles_by_name: dict[str, tuple[_TileFact, ...]],
    jsp_targets: dict[str, tuple[str, ...]],
) -> _Target:
    if _is_dynamic(route.target):
        return _Target(
            f"unresolved:struts_jsp_route:{route.target}",
            "unresolved",
            "low",
        )
    if route.kind in {"form_action", "action_link"}:
        path = _normalize_action_path(route.target)
        return _unique_ref(
            (value.symbol_id for value in actions_by_path.get(path, ())),
            unresolved=f"unresolved:struts_action:{path}",
        )
    if route.kind == "forward":
        candidates = [
            value
            for (_, action_path, name), values in forwards_by_scope_name.items()
            if action_path is None and name == route.target
            for value in values
        ]
        return _unique_ref(
            (value.symbol_id for value in candidates),
            unresolved=f"unresolved:struts_global_forward:{route.target}",
        )
    if route.kind == "tiles":
        return _resolve_tile(route.target, tiles_by_name)
    return _resolve_navigation_target(
        route.target,
        actions_by_path=actions_by_path,
        tiles_by_name=tiles_by_name,
        jsp_targets=jsp_targets,
    )


def _resolve_tile(
    name: str,
    tiles_by_name: dict[str, tuple[_TileFact, ...]],
) -> _Target:
    candidates = tiles_by_name.get(name, ())
    return _unique_ref(
        (value.symbol_id for value in candidates),
        unresolved=f"unresolved:tiles_definition:{name}",
    )


def _unique_ref(refs: Iterable[str], *, unresolved: str) -> _Target:
    unique = tuple(dict.fromkeys(str(value) for value in refs))
    if len(unique) == 1:
        return _Target(unique[0], "resolved", "high")
    return _Target(unresolved, "unresolved", "low")


def _add_symbol(
    *,
    file: DiscoveredCodeFile,
    additions: dict[str, list[dict[str, object]]],
    edges: list[JavaDirectEdge],
    symbol_type: str,
    name: str,
    signature: str,
    line: int,
) -> dict[str, object]:
    symbol_id = _symbol_id(file.path, symbol_type, signature)
    existing = next(
        (value for value in additions.get(file.path, ()) if value["symbol_id"] == symbol_id),
        None,
    )
    if existing is not None:
        return existing
    symbol: dict[str, object] = {
        "symbol_id": symbol_id,
        "symbol_type": symbol_type,
        "name": name,
        "signature": signature,
        "start_line": line,
        "end_line": line,
    }
    additions.setdefault(file.path, []).append(symbol)
    edges.append(
        JavaDirectEdge(
            edge_type="contains",
            from_ref=code_file_id(file.path),
            to_ref=symbol_id,
            resolution_status="resolved",
            confidence="high",
            extractor=STRUTS1_EXTRACTOR,
            source_path=file.path,
            start_line=line,
            end_line=line,
        )
    )
    return symbol


def _edge(
    *,
    edge_type: str,
    from_ref: str,
    target: _Target,
    path: str,
    line: int,
) -> JavaDirectEdge:
    return JavaDirectEdge(
        edge_type=edge_type,
        from_ref=from_ref,
        to_ref=target.ref,
        resolution_status=target.resolution_status,
        confidence=target.confidence,
        extractor=STRUTS1_EXTRACTOR,
        source_path=path,
        start_line=line,
        end_line=line,
    )


def _parse_xml(content: str, path: str) -> tuple[ET.Element | None, str | None]:
    if _UNSAFE_XML_DECLARATION.search(content):
        return None, f"struts1_unsafe_xml_declaration:{path}"
    try:
        return ET.fromstring(_DOCTYPE.sub("", content)), None
    except ET.ParseError as error:
        return None, f"struts1_xml_parse_error:{path}:{error.position[0]}:{error.position[1]}"


def _xml_document_kind(content: str) -> str | None:
    folded = content.casefold()
    for value in ("struts-config", "tiles-definitions", "web-app"):
        if f"<{value}" in folded or re.search(rf"<[\w.-]+:{value}\b", folded):
            return value
    return None


def _element_lines(content: str) -> dict[str, list[int]]:
    values: dict[str, list[int]] = {}
    for match in _XML_TAG.finditer(content):
        values.setdefault(match.group("tag").casefold(), []).append(_line(content, match.start()))
    return values


def _next_line(tag: str, lines: dict[str, list[int]], cursors: dict[str, int]) -> int:
    index = cursors.get(tag, 0)
    values = lines.get(tag, [])
    cursors[tag] = index + 1
    return values[index] if index < len(values) else 1


def _action_servlet_patterns(root: ET.Element) -> tuple[str, ...]:
    action_servlets: set[str] = set()
    for element in root.iter():
        if _local_name(element.tag) != "servlet":
            continue
        name = _child_text(element, "servlet-name")
        servlet_class = _child_text(element, "servlet-class")
        if name and servlet_class and servlet_class.endswith(".ActionServlet"):
            action_servlets.add(name)
    values: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) != "servlet-mapping":
            continue
        name = _child_text(element, "servlet-name")
        pattern = _child_text(element, "url-pattern")
        if name in action_servlets and pattern:
            values.append(pattern)
    return tuple(dict.fromkeys(values))


def _action_endpoint(pattern: str, action_path: str) -> str | None:
    path = _normalize_action_path(action_path)
    if pattern.startswith("*."):
        return f"{path}.{pattern.removeprefix('*.')}"
    if pattern.endswith("/*"):
        prefix = pattern.removesuffix("/*").rstrip("/")
        return f"{prefix}{path}" if prefix else path
    if pattern in {"/", "/*"}:
        return path
    return None


def _java_type_targets(java_types: tuple[JavaType, ...]) -> dict[str, tuple[JavaType, ...]]:
    values: dict[str, list[JavaType]] = {}
    for java_type in java_types:
        values.setdefault(java_type.fqn, []).append(java_type)
        values.setdefault(java_type.simple_name, []).append(java_type)
    return {
        name: tuple({value.symbol_id: value for value in targets}.values())
        for name, targets in values.items()
    }


def _methods_by_owner(
    java_symbols: tuple[JavaSymbol, ...],
    java_types: tuple[JavaType, ...],
) -> dict[str, tuple[JavaSymbol, ...]]:
    values: dict[str, list[JavaSymbol]] = {}
    type_ref_by_fqn = {value.fqn: value.symbol_id for value in java_types}
    for symbol in java_symbols:
        if symbol.symbol_type == "method" and symbol.owner_type is not None:
            values.setdefault(symbol.owner_type, []).append(symbol)
            type_ref = type_ref_by_fqn.get(symbol.owner_type)
            if type_ref is not None:
                values.setdefault(type_ref, []).append(symbol)
    return {owner: tuple(symbols) for owner, symbols in values.items()}


def _group_forms(
    forms: list[_FormFact],
) -> dict[tuple[str, str], tuple[_FormFact, ...]]:
    values: dict[tuple[str, str], list[_FormFact]] = {}
    for form in forms:
        values.setdefault((form.config_path, form.name), []).append(form)
    return {key: tuple(group) for key, group in values.items()}


def _group_actions(actions: list[_ActionFact]) -> dict[str, tuple[_ActionFact, ...]]:
    values: dict[str, list[_ActionFact]] = {}
    for action in actions:
        values.setdefault(_normalize_action_path(action.path), []).append(action)
    return {key: tuple(group) for key, group in values.items()}


def _group_actions_by_scope(
    actions: list[_ActionFact],
) -> dict[tuple[str, str], tuple[_ActionFact, ...]]:
    values: dict[tuple[str, str], list[_ActionFact]] = {}
    for action in actions:
        values.setdefault(
            (action.config_path, _normalize_action_path(action.path)),
            [],
        ).append(action)
    return {key: tuple(group) for key, group in values.items()}


def _group_tiles(tiles: list[_TileFact]) -> dict[str, tuple[_TileFact, ...]]:
    values: dict[str, list[_TileFact]] = {}
    for tile in tiles:
        values.setdefault(tile.name, []).append(tile)
    return {key: tuple(group) for key, group in values.items()}


def _group_forwards(
    forwards: list[_ForwardFact],
) -> dict[tuple[str, str | None, str], tuple[_ForwardFact, ...]]:
    values: dict[tuple[str, str | None, str], list[_ForwardFact]] = {}
    for forward in forwards:
        values.setdefault(
            (forward.config_path, forward.action_path, forward.name),
            [],
        ).append(forward)
    return {key: tuple(group) for key, group in values.items()}


def _action_owner_configs(
    actions: list[_ActionFact],
    type_targets: dict[str, tuple[JavaType, ...]],
) -> dict[str, tuple[_ActionFact, ...]]:
    values: dict[str, list[_ActionFact]] = {}
    for action in actions:
        if action.java_type is None:
            continue
        target = _resolve_java_type(action.java_type, type_targets)
        if target.resolution_status != "resolved":
            continue
        java_type = next(
            (
                value
                for candidates in type_targets.values()
                for value in candidates
                if value.symbol_id == target.ref
            ),
            None,
        )
        if java_type is not None:
            values.setdefault(java_type.fqn, []).append(action)
    return {owner: tuple(group) for owner, group in values.items()}


def _jsp_targets(files: tuple[DiscoveredCodeFile, ...]) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for file in files:
        if PurePosixPath(file.path).suffix.casefold() != ".jsp":
            continue
        web_path = _workspace_web_path(file.path)
        values.setdefault(web_path, []).append(code_file_id(file.path))
    return {path: tuple(dict.fromkeys(refs)) for path, refs in values.items()}


def _workspace_web_path(path: str) -> str:
    normalized = PurePosixPath(path).as_posix()
    for marker in ("/webapp/", "/web/", "/WebContent/"):
        if marker in normalized:
            return _normalize_web_path(normalized.split(marker, maxsplit=1)[1])
    if "WEB-INF/" in normalized:
        return _normalize_web_path(normalized[normalized.index("WEB-INF/") :])
    return _normalize_web_path(PurePosixPath(normalized).name)


def _normalize_action_path(value: str) -> str:
    raw = value.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0].strip()
    raw = raw.removesuffix(".do")
    return "/" + raw.strip("/")


def _normalize_web_path(value: str) -> str:
    raw = value.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0].strip()
    return "/" + raw.strip("/")


def _looks_like_jsp(value: str) -> bool:
    return value.split("?", maxsplit=1)[0].casefold().endswith((".jsp", ".jspx", ".jspf"))


def _is_dynamic(value: str) -> bool:
    return any(marker in value for marker in ("${", "#{", "<%", "*"))


def _enclosing_method(methods: list[JavaSymbol], line: int) -> JavaSymbol | None:
    candidates = [value for value in methods if value.start_line <= line <= value.end_line]
    return min(
        candidates,
        key=lambda value: value.end_line - value.start_line,
        default=None,
    )


def _attribute(element: ET.Element, name: str) -> str | None:
    value = element.attrib.get(name)
    return value.strip() if value is not None and value.strip() else None


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


def _local_name(value: str) -> str:
    return value.rsplit("}", maxsplit=1)[-1].rsplit(":", maxsplit=1)[-1].casefold()


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _symbol_id(path: str, symbol_type: str, signature: str) -> str:
    material = "\x00".join((path, symbol_type, signature))
    return f"symbol-{sha256(material.encode()).hexdigest()[:24]}"
