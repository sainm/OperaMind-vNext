"""Build immutable management reports for every unresolved Code Graph edge."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, cast

from operamind.contracts import ContractCatalog


@dataclass(frozen=True, slots=True)
class UnresolvedEvidenceBuildResult:
    """One deterministic report and its lifecycle totals."""

    artifact: dict[str, Any]
    open_count: int
    closed_count: int


class UnresolvedEvidenceReportBuilder:
    """Classify unresolved edges and close only predecessor findings with unique proof."""

    def __init__(self, contracts: ContractCatalog) -> None:
        self._contracts = contracts

    def build(
        self,
        *,
        graph: dict[str, Any],
        predecessor: dict[str, Any] | None = None,
    ) -> UnresolvedEvidenceBuildResult:
        self._contracts.validate_artifact(graph)
        if graph.get("artifact_type") != "CodeGraphSnapshot":
            raise ValueError("Unresolved Evidence requires a CodeGraphSnapshot")
        if predecessor is not None:
            self._contracts.validate_artifact(predecessor)
            if predecessor.get("artifact_type") != "UnresolvedEvidenceReport":
                raise ValueError("Unresolved Evidence predecessor has the wrong Artifact type")
            if (
                predecessor["project_id"] != graph["project_id"]
                or predecessor["repository_id"] != graph["repository_id"]
            ):
                raise ValueError("Unresolved Evidence predecessor scope differs")

        snapshot_id = str(graph["code_graph_snapshot_id"])
        report_id = unresolved_evidence_report_id(snapshot_id)
        edges = cast(list[dict[str, Any]], graph["edges"])
        locations = _node_locations(graph)
        open_items = [
            _open_item(report_id, edge, graph=graph, locations=locations)
            for edge in edges
            if edge["resolution_status"] == "unresolved"
        ]
        closed_items = _closed_items(
            report_id=report_id,
            graph=graph,
            predecessor=predecessor,
            locations=locations,
        )
        items = sorted(
            [*open_items, *closed_items],
            key=lambda item: (str(item["status"]), str(item["finding_key"])),
        )
        runtime_refs = sorted(
            str(value)
            for value in cast(list[object], graph.get("runtime_evidence_refs", []))
        )
        artifact: dict[str, Any] = {
            "artifact_type": "UnresolvedEvidenceReport",
            "schema_version": "v1",
            "unresolved_evidence_report_id": report_id,
            "project_id": str(graph["project_id"]),
            "repository_id": str(graph["repository_id"]),
            "repository_revision": str(graph["repository_revision"]),
            "code_graph_snapshot_id": snapshot_id,
            "report_status": "needs_evidence" if open_items else "clear",
            "trigger": {
                "trigger_type": "runtime_evidence" if runtime_refs else "static_graph",
                "evidence_refs": [snapshot_id, *runtime_refs],
            },
            "open_count": len(open_items),
            "closed_count": len(closed_items),
            "items": items,
        }
        if predecessor is not None:
            artifact["predecessor_report_id"] = predecessor[
                "unresolved_evidence_report_id"
            ]
        self._contracts.validate_artifact(artifact)
        return UnresolvedEvidenceBuildResult(
            artifact=artifact,
            open_count=len(open_items),
            closed_count=len(closed_items),
        )


def unresolved_evidence_report_id(code_graph_snapshot_id: str) -> str:
    if not code_graph_snapshot_id.strip():
        raise ValueError("code_graph_snapshot_id must not be blank")
    digest = hashlib.sha256(code_graph_snapshot_id.encode()).hexdigest()[:24]
    return f"unresolved-evidence-{digest}"


def _open_item(
    report_id: str,
    edge: dict[str, Any],
    *,
    graph: dict[str, Any],
    locations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    category, namespace, detail = _classify_target(str(edge["to_ref"]), str(edge["edge_type"]))
    candidates = _candidate_targets(
        namespace=namespace,
        detail=detail,
        graph=graph,
        locations=locations,
    )
    reason, missing, suggestions = _guidance(
        namespace=namespace,
        detail=detail,
        candidate_count=len(candidates),
        target_ref=str(edge["to_ref"]),
    )
    finding_key = _finding_key(edge)
    return {
        "item_id": _item_id(
            report_id,
            finding_key,
            "open",
            edge_ref=str(edge["edge_id"]),
        ),
        "finding_key": finding_key,
        "edge_ref": str(edge["edge_id"]),
        "status": "open",
        "category": category,
        "reason": reason,
        "edge_type": str(edge["edge_type"]),
        "source_ref": str(edge["from_ref"]),
        "unresolved_target_ref": str(edge["to_ref"]),
        "source_location": dict(edge["source_location"]),
        "candidate_targets": candidates,
        "missing_evidence": missing,
        "resolution_suggestions": suggestions,
        "provenance": str(edge.get("provenance", "static")),
        "evidence_refs": sorted(
            str(value) for value in cast(list[object], edge.get("evidence_refs", []))
        ),
    }


def _closed_items(
    *,
    report_id: str,
    graph: dict[str, Any],
    predecessor: dict[str, Any] | None,
    locations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if predecessor is None:
        return []
    resolved_by_key: dict[str, list[dict[str, Any]]] = {}
    resolved_by_location: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for edge in cast(list[dict[str, Any]], graph["edges"]):
        if edge["resolution_status"] != "resolved":
            continue
        keys = {_finding_key(edge)}
        static_ref = edge.get("static_edge_ref")
        if isinstance(static_ref, str):
            keys.add(f"edge:{static_ref}")
        for key in keys:
            resolved_by_key.setdefault(key, []).append(edge)
        resolved_by_location.setdefault(_edge_location_key(edge), []).append(edge)
    prior_items = [
        prior
        for prior in cast(list[dict[str, Any]], predecessor["items"])
        if prior["status"] == "open"
    ]
    prior_location_counts: dict[tuple[str, ...], int] = {}
    for prior in prior_items:
        location_key = _item_location_key(prior)
        prior_location_counts[location_key] = prior_location_counts.get(location_key, 0) + 1
    values: list[dict[str, Any]] = []
    for prior in prior_items:
        candidates = resolved_by_key.get(str(prior["finding_key"]), [])
        candidates.extend(resolved_by_key.get(f"edge:{prior['edge_ref']}", []))
        location_key = _item_location_key(prior)
        if prior_location_counts[location_key] == 1:
            candidates.extend(resolved_by_location.get(location_key, []))
        unique = {str(edge["edge_id"]): edge for edge in candidates}
        if len(unique) != 1:
            continue
        edge = next(iter(unique.values()))
        evidence_refs = sorted(
            {
                f"code-graph:{graph['code_graph_snapshot_id']}:edge:{edge['edge_id']}",
                *(
                    str(value)
                    for value in cast(list[object], edge.get("evidence_refs", []))
                ),
            }
        )
        provenance = str(edge.get("provenance", "static"))
        proof_kind = {
            "static": "static_unique",
            "runtime": "runtime_unique",
            "static_runtime": "static_runtime_unique",
        }[provenance]
        target_ref = str(edge["to_ref"])
        source_location = locations.get(target_ref)
        candidate: dict[str, Any] = {
            "target_ref": target_ref,
            "match_basis": (
                "runtime_observation" if provenance != "static" else "definition"
            ),
        }
        if source_location is not None:
            candidate["source_location"] = source_location
        finding_key = str(prior["finding_key"])
        values.append(
            {
                "item_id": _item_id(
                    report_id,
                    finding_key,
                    "closed",
                    edge_ref=str(prior["edge_ref"]),
                ),
                "finding_key": finding_key,
                "edge_ref": str(prior["edge_ref"]),
                "status": "closed",
                "category": str(prior["category"]),
                "reason": "resolved_unique",
                "edge_type": str(prior["edge_type"]),
                "source_ref": str(prior["source_ref"]),
                "unresolved_target_ref": str(prior["unresolved_target_ref"]),
                "source_location": dict(prior["source_location"]),
                "candidate_targets": [candidate],
                "missing_evidence": [],
                "resolution_suggestions": list(prior["resolution_suggestions"]),
                "provenance": provenance,
                "evidence_refs": sorted(
                    str(value)
                    for value in cast(list[object], edge.get("evidence_refs", []))
                ),
                "closure": {
                    "resolved_target_ref": target_ref,
                    "resolved_edge_ref": str(edge["edge_id"]),
                    "proof_kind": proof_kind,
                    "evidence_refs": evidence_refs,
                },
            }
        )
    return values


def _finding_key(edge: dict[str, Any]) -> str:
    static_ref = edge.get("static_edge_ref")
    if isinstance(static_ref, str) and static_ref:
        return f"edge:{static_ref}"
    material = "\0".join(
        (
            str(edge["edge_type"]),
            str(edge["from_ref"]),
            str(edge["extractor"]),
            str(edge["profile_version"]),
            str(edge["source_location"]["path"]),
            str(edge["source_location"]["start_line"]),
            str(edge["source_location"]["end_line"]),
            str(edge["to_ref"]),
        )
    )
    return f"finding-{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _edge_location_key(edge: dict[str, Any]) -> tuple[str, ...]:
    location = cast(dict[str, Any], edge["source_location"])
    return (
        str(edge["edge_type"]),
        str(edge["from_ref"]),
        str(location["path"]),
        str(location["start_line"]),
        str(location["end_line"]),
    )


def _item_location_key(item: dict[str, Any]) -> tuple[str, ...]:
    location = cast(dict[str, Any], item["source_location"])
    return (
        str(item["edge_type"]),
        str(item["source_ref"]),
        str(location["path"]),
        str(location["start_line"]),
        str(location["end_line"]),
    )


def _item_id(
    report_id: str,
    finding_key: str,
    status: str,
    *,
    edge_ref: str,
) -> str:
    # Keep the normalized identity tied to the exact edge as an additional
    # safeguard even though finding_key is also unique inside one report.
    material = "\0".join((report_id, finding_key, status, edge_ref))
    return f"unresolved-item-{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _classify_target(target_ref: str, edge_type: str) -> tuple[str, str, str]:
    if target_ref.startswith("unresolved:"):
        parts = target_ref.split(":", maxsplit=2)
        namespace = parts[1] if len(parts) > 1 else "generic"
        detail = parts[2] if len(parts) > 2 else ""
    elif target_ref.startswith("external:"):
        namespace, detail = "external", target_ref.removeprefix("external:")
    else:
        namespace, detail = "generic", target_ref
    category = {
        "call": "call_target",
        "endpoint": "endpoint_route",
        "struts_endpoint": "endpoint_route",
        "table": "data_table",
        "entity": "entity_mapping",
        "config_key": "config_key",
        "route": "navigation_target",
        "struts_action": "navigation_target",
        "struts_forward": "navigation_target",
        "struts_global_forward": "navigation_target",
        "struts_jsp_route": "navigation_target",
        "struts_navigation": "navigation_target",
        "tiles_definition": "navigation_target",
    }.get(namespace, "navigation_target" if edge_type == "navigates_to" else "generic_relation")
    return category, namespace, detail


def _candidate_targets(
    *,
    namespace: str,
    detail: str,
    graph: dict[str, Any],
    locations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    refs: dict[str, str] = {}
    symbols = [
        symbol
        for file in cast(list[dict[str, Any]], graph["files"])
        for symbol in cast(list[dict[str, Any]], file["symbols"])
    ]
    if namespace == "call":
        match = re.search(r"(?:^|\.)(?P<name>[A-Za-z_$][\w$]*)(?:/(?P<arity>\d+))?$", detail)
        if match is not None:
            name = match.group("name")
            arity = match.group("arity")
            for symbol in symbols:
                if symbol.get("symbol_type") != "method" or symbol.get("name") != name:
                    continue
                signature = str(symbol.get("signature", ""))
                if arity is not None and _signature_arity(signature) != int(arity):
                    continue
                refs[str(symbol["symbol_id"])] = "signature"
    elif namespace in {"table", "config_key", "entity"}:
        expected_type = {
            "table": "db_table",
            "config_key": "config_key",
            "entity": "class",
        }[namespace]
        prefix = {"table": "table:", "config_key": "config:", "entity": ""}[namespace]
        for symbol in symbols:
            signature = str(symbol.get("signature", ""))
            name = str(symbol.get("name", ""))
            expected = detail.casefold() if namespace == "table" else detail
            actual = signature.removeprefix(prefix) if prefix else name.rsplit(".", 1)[-1]
            if symbol.get("symbol_type") == expected_type and (
                actual.casefold() if namespace == "table" else actual
            ) == expected:
                refs[str(symbol["symbol_id"])] = "definition"
    elif namespace == "endpoint" and "dynamic:" not in detail:
        normalized = detail.removeprefix("http:")
        if ":" in normalized:
            method, path = normalized.split(":", maxsplit=1)
            for edge in cast(list[dict[str, Any]], graph["edges"]):
                target = str(edge["to_ref"])
                if edge["edge_type"] != "exposes" or not target.startswith("http:"):
                    continue
                _, endpoint_method, endpoint_path = target.split(":", maxsplit=2)
                if endpoint_method in {method, "ANY"} and _route_matches(endpoint_path, path):
                    refs[str(edge["from_ref"])] = "route"
    return [
        {
            **{"target_ref": ref, "match_basis": basis},
            **({"source_location": locations[ref]} if ref in locations else {}),
        }
        for ref, basis in sorted(refs.items())
    ]


def _guidance(
    *, namespace: str, detail: str, candidate_count: int, target_ref: str
) -> tuple[str, list[str], list[str]]:
    if target_ref.startswith("external:"):
        return (
            "external_reference_unverified",
            ["external_dependency_declaration"],
            ["verify_external_dependency"],
        )
    if namespace == "endpoint" and "dynamic:" in detail:
        return (
            "runtime_observation_missing",
            ["runtime_route_observation"],
            ["collect_runtime_route"],
        )
    if "dynamic:" in detail:
        return (
            "dynamic_reference",
            ["runtime_value_or_static_constant"],
            ["configure_framework_adapter", "inspect_extractor_diagnostic"],
        )
    if candidate_count > 1:
        return (
            "target_ambiguous",
            ["unique_target_discriminator"],
            ["disambiguate_reference", "configure_framework_adapter"],
        )
    if candidate_count == 1:
        return (
            "unresolved_reference",
            ["extractor_resolution_proof"],
            ["configure_framework_adapter", "inspect_extractor_diagnostic"],
        )
    return (
        "target_definition_missing",
        ["target_definition"],
        ["add_static_definition", "configure_framework_adapter"],
    )


def _node_locations(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for file in cast(list[dict[str, Any]], graph["files"]):
        path = str(file["path"])
        values[str(file["file_id"])] = {"path": path, "start_line": 1, "end_line": 1}
        for symbol in cast(list[dict[str, Any]], file["symbols"]):
            values[str(symbol["symbol_id"])] = {
                "path": path,
                "start_line": int(symbol["start_line"]),
                "end_line": int(symbol["end_line"]),
            }
    return values


def _signature_arity(signature: str) -> int:
    if "(" not in signature or ")" not in signature:
        return -1
    body = signature.split("(", 1)[1].rsplit(")", 1)[0].strip()
    return 0 if not body else len(body.split(","))


def _route_matches(template: str, actual: str) -> bool:
    expected_parts = template.rstrip("/").split("/")
    actual_parts = actual.rstrip("/").split("/")
    return len(expected_parts) == len(actual_parts) and all(
        expected == observed
        or (expected.startswith("{") and expected.endswith("}"))
        or expected == "{*}"
        for expected, observed in zip(expected_parts, actual_parts, strict=True)
    )
