"""Deterministic typed-anchor traversal over one bounded Code Graph."""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class CodeAnchorKind(StrEnum):
    """Exact anchor namespaces accepted by the Scope Resolver."""

    PATH = "path"
    SYMBOL = "symbol"
    ENDPOINT = "endpoint"
    TABLE = "table"
    CONFIG_KEY = "config_key"
    UI_ROUTE = "ui_route"


@dataclass(frozen=True, slots=True)
class CodeAnchor:
    """One explicit code anchor justified by Canonical document evidence."""

    anchor_id: str
    kind: CodeAnchorKind
    value: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.anchor_id.strip() or not self.value.strip():
            raise ValueError("Code anchor identity and value must not be blank")
        if not self.evidence_refs or any(not value.strip() for value in self.evidence_refs):
            raise ValueError("Code anchor evidence_refs must be non-empty and non-blank")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("Code anchor evidence_refs must be unique")
        if self.kind is CodeAnchorKind.PATH:
            path = PurePosixPath(self.value)
            if path.is_absolute() or ".." in path.parts or "\\" in self.value:
                raise ValueError("Path anchor must be a workspace-relative POSIX path")

    @property
    def normalized_value(self) -> str:
        """Return namespace-specific exact-match material."""

        value = unicodedata.normalize("NFKC", self.value).strip()
        if self.kind is CodeAnchorKind.PATH:
            return PurePosixPath(value).as_posix()
        if self.kind is CodeAnchorKind.SYMBOL:
            return re.sub(r"\s+", "", value).casefold()
        if self.kind is CodeAnchorKind.ENDPOINT:
            if value.casefold().startswith("http:"):
                _, method, path = value.split(":", maxsplit=2)
                return f"http:{method.upper()}:{_url_path(path)}"
            parts = value.split(maxsplit=1)
            if len(parts) == 2 and parts[0].isalpha():
                return f"http:{parts[0].upper()}:{_url_path(parts[1])}"
            return f"http:*:{_url_path(value)}"
        if self.kind is CodeAnchorKind.TABLE:
            return value.strip('"').casefold()
        if self.kind is CodeAnchorKind.UI_ROUTE:
            return f"route:{_url_path(value.removeprefix('route:'))}"
        return value


@dataclass(frozen=True, slots=True)
class CodeRelationPolicy:
    """Profile-selected edge allowlist and traversal bound for one change domain."""

    change_domain: str
    edge_types: tuple[str, ...]
    max_depth: int
    include_reverse: bool

    def __post_init__(self) -> None:
        if not self.change_domain.strip() or not self.edge_types:
            raise ValueError("Code relation policy fields must not be blank")
        if len(self.edge_types) != len(set(self.edge_types)):
            raise ValueError("Code relation policy edge_types must be unique")
        if not 1 <= self.max_depth <= 8:
            raise ValueError("Code relation policy max_depth must be between 1 and 8")


@dataclass(frozen=True, slots=True)
class CodeAnchorMatch:
    """One direct node seed; via_edge_ids preserve external-anchor provenance."""

    anchor_id: str
    node_ref: str
    via_edge_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CodeScopeEdge:
    """One resolved, Profile-allowed traversal edge."""

    edge_id: str
    edge_type: str
    from_ref: str
    to_ref: str
    provenance: str = "static"
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.provenance not in {"static", "runtime", "static_runtime"}:
            raise ValueError("Code Scope edge provenance is invalid")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("Code Scope edge evidence_refs must be unique")
        if self.provenance == "static" and self.evidence_refs:
            raise ValueError("Static Code Scope edges cannot carry runtime evidence")
        if self.provenance != "static" and not self.evidence_refs:
            raise ValueError("Runtime Code Scope edges require evidence_refs")


@dataclass(frozen=True, slots=True)
class CodeGraphPath:
    """Shortest deterministic path from a typed anchor to one graph node."""

    anchor_id: str
    node_ref: str
    node_refs: tuple[str, ...]
    edge_ids: tuple[str, ...]
    directions: tuple[str, ...]
    distance: int


@dataclass(frozen=True, slots=True)
class CodeTraversalResult:
    """Bounded traversal ledger and whether the state ceiling truncated it."""

    paths: tuple[CodeGraphPath, ...]
    truncated: bool


class CodeGraphTraversalPlanner:
    """Run per-anchor BFS with typed edge and reverse-expansion gates."""

    def traverse(
        self,
        *,
        anchors: tuple[CodeAnchor, ...],
        matches: tuple[CodeAnchorMatch, ...],
        edges: tuple[CodeScopeEdge, ...],
        policy: CodeRelationPolicy,
        max_states: int,
    ) -> CodeTraversalResult:
        """Keep one shortest stable path per anchor/node and never cross a cycle."""

        if not 1 <= max_states <= 1_000_000:
            raise ValueError("max_states must be between 1 and 1000000")
        anchor_ids = {anchor.anchor_id for anchor in anchors}
        if len(anchor_ids) != len(anchors):
            raise ValueError("Code anchor IDs must be unique")
        if any(match.anchor_id not in anchor_ids for match in matches):
            raise ValueError("Code anchor match references an unknown anchor")
        allowed = frozenset(policy.edge_types)
        if any(edge.edge_type not in allowed for edge in edges):
            raise ValueError("Traversal edge is outside the relation policy")

        forward: dict[str, list[CodeScopeEdge]] = {}
        reverse: dict[str, list[CodeScopeEdge]] = {}
        for edge in sorted(edges, key=lambda value: value.edge_id):
            forward.setdefault(edge.from_ref, []).append(edge)
            if policy.include_reverse:
                reverse.setdefault(edge.to_ref, []).append(edge)

        paths: dict[tuple[str, str], CodeGraphPath] = {}
        queue: deque[CodeGraphPath] = deque()
        for match in sorted(
            matches, key=lambda value: (value.anchor_id, value.node_ref, value.via_edge_ids)
        ):
            key = (match.anchor_id, match.node_ref)
            if key in paths:
                continue
            path = CodeGraphPath(
                anchor_id=match.anchor_id,
                node_ref=match.node_ref,
                node_refs=(match.node_ref,),
                edge_ids=match.via_edge_ids,
                directions=tuple("anchor" for _ in match.via_edge_ids),
                distance=0,
            )
            paths[key] = path
            queue.append(path)
        truncated = len(paths) > max_states
        while queue and not truncated:
            current = queue.popleft()
            if current.distance >= policy.max_depth:
                continue
            expansions = [
                (edge, edge.to_ref, "forward") for edge in forward.get(current.node_ref, ())
            ]
            expansions.extend(
                (edge, edge.from_ref, "reverse") for edge in reverse.get(current.node_ref, ())
            )
            for edge, next_ref, direction in sorted(
                expansions,
                key=lambda value: (value[0].edge_id, value[1], value[2]),
            ):
                if next_ref in current.node_refs:
                    continue
                key = (current.anchor_id, next_ref)
                if key in paths:
                    continue
                next_path = CodeGraphPath(
                    anchor_id=current.anchor_id,
                    node_ref=next_ref,
                    node_refs=(*current.node_refs, next_ref),
                    edge_ids=(*current.edge_ids, edge.edge_id),
                    directions=(*current.directions, direction),
                    distance=current.distance + 1,
                )
                paths[key] = next_path
                if len(paths) > max_states:
                    truncated = True
                    break
                queue.append(next_path)
        ordered = tuple(
            sorted(
                paths.values(),
                key=lambda value: (
                    value.distance,
                    value.anchor_id,
                    value.node_ref,
                    value.edge_ids,
                ),
            )
        )
        return CodeTraversalResult(paths=ordered[:max_states], truncated=truncated)


def relation_policy_for_domain(
    profile: dict[str, object], change_domain: str
) -> CodeRelationPolicy | None:
    """Select exactly one validated Profile policy; no generic fallback is allowed."""

    policies = profile.get("relation_policies")
    if not isinstance(policies, list):
        raise ValueError("Code Framework Profile relation_policies must be a list")
    matches = [
        value
        for value in policies
        if isinstance(value, dict) and value.get("change_domain") == change_domain
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("Code Framework Profile has duplicate change-domain policies")
    policy = matches[0]
    edge_types = policy.get("edge_types")
    if not isinstance(edge_types, list) or not all(isinstance(value, str) for value in edge_types):
        raise ValueError("Code relation policy edge_types must be strings")
    return CodeRelationPolicy(
        change_domain=change_domain,
        edge_types=tuple(edge_types),
        max_depth=int(policy["max_depth"]),
        include_reverse=bool(policy["include_reverse"]),
    )


def _url_path(value: str) -> str:
    normalized = re.sub(r"/+", "/", value.strip())
    return normalized if normalized.startswith("/") else f"/{normalized}"
