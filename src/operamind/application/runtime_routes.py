"""Reconcile sanitized browser Route observations with one immutable static Code Graph."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlsplit

from operamind.contracts import ContractCatalog


@dataclass(frozen=True, slots=True)
class RuntimeRouteReconcileRequest:
    """Explicit identities for one browser capture and its derived graph."""

    runtime_route_evidence_id: str
    merged_code_graph_snapshot_id: str
    browser_run_id: str
    captured_at: datetime
    source_evidence_ref: str

    def __post_init__(self) -> None:
        values = (
            self.runtime_route_evidence_id,
            self.merged_code_graph_snapshot_id,
            self.browser_run_id,
            self.source_evidence_ref,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Runtime Route reconciliation identities must not be blank")
        if self.captured_at.tzinfo is None:
            raise ValueError("Runtime Route captured_at must include a timezone")


@dataclass(frozen=True, slots=True)
class RuntimeRouteReconcileResult:
    """Contract artifacts plus deterministic resolution totals."""

    evidence_artifact: dict[str, Any]
    graph_artifact: dict[str, Any]
    observation_count: int
    resolved_count: int
    unresolved_count: int


class RuntimeRouteReconciler:
    """Merge only uniquely proven runtime Routes; retain every other unresolved edge."""

    def __init__(self, contracts: ContractCatalog) -> None:
        self._contracts = contracts

    def reconcile(
        self,
        *,
        request: RuntimeRouteReconcileRequest,
        base_graph: dict[str, Any],
        capture: dict[str, Any],
    ) -> RuntimeRouteReconcileResult:
        self._contracts.validate_artifact(base_graph)
        if base_graph.get("artifact_type") != "CodeGraphSnapshot":
            raise ValueError("Runtime Route reconciliation requires a CodeGraphSnapshot")
        if base_graph.get("scan_status") not in {"complete", "truncated"}:
            raise ValueError("Runtime Route reconciliation requires a usable Code Graph")
        observations = _observations(capture, request.source_evidence_ref)
        symbols = {
            str(symbol["symbol_id"]): symbol
            for file in cast(list[dict[str, Any]], base_graph["files"])
            for symbol in cast(list[dict[str, Any]], file["symbols"])
        }
        edges = cast(list[dict[str, Any]], base_graph["edges"])
        unresolved_by_source: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            if (
                edge["edge_type"] == "calls"
                and edge["resolution_status"] == "unresolved"
                and str(edge["to_ref"]).startswith("unresolved:endpoint:")
            ):
                unresolved_by_source.setdefault(str(edge["from_ref"]), []).append(edge)
        endpoints = _endpoints(edges)
        resolutions = [
            _resolve_observation(
                observation,
                symbols=symbols,
                unresolved_by_source=unresolved_by_source,
                endpoints=endpoints,
            )
            for observation in observations
        ]
        _invalidate_conflicting_sources(resolutions)
        evidence = {
            "artifact_type": "RuntimeRouteEvidence",
            "schema_version": "v1",
            "runtime_route_evidence_id": request.runtime_route_evidence_id,
            "project_id": str(base_graph["project_id"]),
            "repository_id": str(base_graph["repository_id"]),
            "repository_revision": str(base_graph["repository_revision"]),
            "code_graph_snapshot_id": str(base_graph["code_graph_snapshot_id"]),
            "browser_run_id": request.browser_run_id,
            "captured_at": request.captured_at.isoformat().replace("+00:00", "Z"),
            "source_evidence_refs": [request.source_evidence_ref],
            "observations": observations,
            "resolutions": resolutions,
        }
        self._contracts.validate_artifact(evidence)
        graph = _enriched_graph(
            base_graph=base_graph,
            merged_snapshot_id=request.merged_code_graph_snapshot_id,
            evidence_ref=request.runtime_route_evidence_id,
            observations=observations,
            resolutions=resolutions,
        )
        self._contracts.validate_artifact(graph)
        resolved = sum(item["status"] == "resolved" for item in resolutions)
        return RuntimeRouteReconcileResult(
            evidence_artifact=evidence,
            graph_artifact=graph,
            observation_count=len(observations),
            resolved_count=resolved,
            unresolved_count=len(observations) - resolved,
        )


def _observations(capture: dict[str, Any], evidence_ref: str) -> list[dict[str, Any]]:
    raw = capture.get("route_observations")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Runtime Route capture requires non-empty route_observations")
    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed = {
        "observation_id",
        "scenario_id",
        "event_kind",
        "method",
        "path",
        "source_action_id",
        "source_route_ref",
    }
    for item in raw:
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError("Runtime Route observation has unknown fields")
        required = ("observation_id", "scenario_id", "event_kind", "method", "path")
        if any(
            not isinstance(item.get(key), str) or not str(item[key]).strip() for key in required
        ):
            raise ValueError("Runtime Route observation fields must be non-blank strings")
        observation_id = str(item["observation_id"])
        if observation_id in seen:
            raise ValueError("Runtime Route observation IDs must be unique")
        seen.add(observation_id)
        event_kind = str(item["event_kind"])
        if event_kind not in {"network_request", "navigation", "form_submission"}:
            raise ValueError("Runtime Route observation event_kind is invalid")
        method = str(item["method"]).upper()
        path = _safe_path(str(item["path"]))
        value: dict[str, Any] = {
            "observation_id": observation_id,
            "scenario_id": str(item["scenario_id"]),
            "event_kind": event_kind,
            "method": method,
            "path": path,
            "evidence_ref": evidence_ref,
        }
        for key in ("source_action_id", "source_route_ref"):
            optional = item.get(key)
            if optional is not None:
                if not isinstance(optional, str) or not optional.strip():
                    raise ValueError(f"Runtime Route {key} must be non-blank when present")
                value[key] = optional
        values.append(value)
    return values


def _resolve_observation(
    observation: dict[str, Any],
    *,
    symbols: dict[str, dict[str, Any]],
    unresolved_by_source: dict[str, list[dict[str, Any]]],
    endpoints: tuple[tuple[str, str, str], ...],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "observation_id": observation["observation_id"],
        "status": "unresolved",
        "reason": "missing_source_route_ref",
        "candidate_endpoint_refs": [],
    }
    source_ref = observation.get("source_route_ref")
    if source_ref is None:
        return result
    result["source_route_ref"] = source_ref
    symbol = symbols.get(str(source_ref))
    if symbol is None or symbol.get("symbol_type") != "ui_route":
        result["reason"] = "source_route_not_found"
        return result
    source_edges = unresolved_by_source.get(str(source_ref), [])
    if not source_edges:
        result["reason"] = "source_route_not_unresolved"
        return result
    if len(source_edges) != 1:
        result["reason"] = "source_route_ambiguous"
        return result
    signature = str(symbol.get("signature", ""))
    parts = signature.split(":", maxsplit=2)
    if len(parts) != 3 or parts[0] != "route" or parts[1] != observation["method"]:
        result["reason"] = "method_mismatch"
        return result
    candidates = sorted(
        {
            endpoint_ref
            for method, template, endpoint_ref in endpoints
            if method in {str(observation["method"]), "ANY"}
            and _route_matches(template, str(observation["path"]))
        }
    )
    result["candidate_endpoint_refs"] = candidates
    if not candidates:
        result["reason"] = "endpoint_not_found"
    elif len(candidates) > 1:
        result["reason"] = "endpoint_ambiguous"
    else:
        result.update(
            status="resolved",
            reason="resolved_unique",
            endpoint_ref=candidates[0],
        )
    return result


def _invalidate_conflicting_sources(resolutions: list[dict[str, Any]]) -> None:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in resolutions:
        source = item.get("source_route_ref")
        if isinstance(source, str) and item["status"] == "resolved":
            by_source.setdefault(source, []).append(item)
    for items in by_source.values():
        targets = {str(item["endpoint_ref"]) for item in items}
        if len(targets) <= 1:
            continue
        candidates = sorted(targets)
        for item in items:
            item["status"] = "unresolved"
            item["reason"] = "source_maps_multiple_endpoints"
            item["candidate_endpoint_refs"] = candidates
            item.pop("endpoint_ref", None)


def _endpoints(edges: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    values: set[tuple[str, str, str]] = set()
    for edge in edges:
        target = str(edge["to_ref"])
        if edge["edge_type"] != "exposes" or not target.startswith("http:"):
            continue
        _, method, path = target.split(":", maxsplit=2)
        values.add((method, _safe_path(path), str(edge["from_ref"])))
    return tuple(sorted(values))


def _route_matches(template: str, actual: str) -> bool:
    expected_parts = template.rstrip("/").split("/")
    actual_parts = actual.rstrip("/").split("/")
    return len(expected_parts) == len(actual_parts) and all(
        expected == observed or (expected.startswith("{") and expected.endswith("}"))
        for expected, observed in zip(expected_parts, actual_parts, strict=True)
    )


def _safe_path(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/"):
        raise ValueError("Runtime Route path must be origin-relative")
    return parsed.path or "/"


def _enriched_graph(
    *,
    base_graph: dict[str, Any],
    merged_snapshot_id: str,
    evidence_ref: str,
    observations: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
) -> dict[str, Any]:
    observation_by_id = {str(item["observation_id"]): item for item in observations}
    resolved_by_source: dict[str, tuple[str, list[str]]] = {}
    for resolution in resolutions:
        if resolution["status"] != "resolved":
            continue
        source_ref = str(resolution["source_route_ref"])
        endpoint_ref = str(resolution["endpoint_ref"])
        evidence_refs = resolved_by_source.setdefault(source_ref, (endpoint_ref, []))[1]
        evidence_refs.append(
            str(observation_by_id[str(resolution["observation_id"])]["evidence_ref"])
        )
    enriched_edges: list[dict[str, Any]] = []
    for raw_edge in cast(list[dict[str, Any]], base_graph["edges"]):
        edge = dict(raw_edge)
        source_ref = str(edge["from_ref"])
        runtime_target = resolved_by_source.get(source_ref)
        is_runtime_target = (
            runtime_target is not None
            and edge["edge_type"] == "calls"
            and edge["resolution_status"] == "unresolved"
            and str(edge["to_ref"]).startswith("unresolved:endpoint:")
        )
        if is_runtime_target and runtime_target is not None:
            static_edge_ref = str(edge["edge_id"])
            edge.update(
                edge_id=_runtime_edge_id(static_edge_ref, runtime_target[0], evidence_ref),
                to_ref=runtime_target[0],
                resolution_status="resolved",
                confidence="high",
                extractor="runtime_route_evidence",
                provenance="static_runtime",
                evidence_refs=sorted(set(runtime_target[1])),
                static_edge_ref=static_edge_ref,
            )
        else:
            edge.setdefault("provenance", "static")
            edge.setdefault("evidence_refs", [])
        enriched_edges.append(edge)
    graph = dict(base_graph)
    graph.update(
        code_graph_snapshot_id=merged_snapshot_id,
        scan_mode="runtime_enriched",
        base_code_graph_snapshot_id=base_graph["code_graph_snapshot_id"],
        changed_paths=[],
        affected_paths=[],
        scanned_file_count=0,
        reused_file_count=len(cast(list[object], base_graph["files"])),
        runtime_evidence_refs=[evidence_ref],
        edges=sorted(enriched_edges, key=lambda item: str(item["edge_id"])),
    )
    return graph


def _runtime_edge_id(static_edge_ref: str, endpoint_ref: str, evidence_ref: str) -> str:
    material = "\0".join((static_edge_ref, endpoint_ref, evidence_ref))
    return f"edge-runtime-{hashlib.sha256(material.encode()).hexdigest()[:24]}"
