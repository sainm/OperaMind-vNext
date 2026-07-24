from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from operamind.application.runtime_routes import (
    RuntimeRouteReconciler,
    RuntimeRouteReconcileRequest,
)
from operamind.contracts import ContractCatalog

ROOT = Path(__file__).parents[2]


def _graph(*, ambiguous: bool = False, multiple_routes: bool = False) -> dict[str, Any]:
    profile = "generic-web@1.0.0"
    symbols = [
        {
            "symbol_id": "route-dynamic",
            "symbol_type": "ui_route",
            "name": "dynamic:options.url",
            "signature": "route:GET:dynamic:options.url",
            "start_line": 4,
            "end_line": 4,
        },
        {
            "symbol_id": "endpoint-customer",
            "symbol_type": "method",
            "name": "readCustomer",
            "signature": "example.CustomerController.readCustomer/1",
            "start_line": 10,
            "end_line": 12,
        },
    ]
    if ambiguous:
        symbols.append(
            {
                "symbol_id": "endpoint-customer-alternate",
                "symbol_type": "method",
                "name": "readCustomerAlternate",
                "signature": "example.AlternateController.readCustomer/1",
                "start_line": 20,
                "end_line": 22,
            }
        )
    if multiple_routes:
        symbols.append(
            {
                "symbol_id": "endpoint-account",
                "symbol_type": "method",
                "name": "readAccount",
                "signature": "example.AccountController.readAccount/1",
                "start_line": 30,
                "end_line": 32,
            }
        )
    edges: list[dict[str, Any]] = [
        _edge("contains-route", "contains", "file-app", "route-dynamic", "resolved", 4),
        _edge("contains-customer", "contains", "file-app", "endpoint-customer", "resolved", 10),
        _edge(
            "exposes-customer",
            "exposes",
            "endpoint-customer",
            "http:GET:/api/customers/{id}",
            "external",
            10,
        ),
        _edge(
            "calls-dynamic",
            "calls",
            "route-dynamic",
            "unresolved:endpoint:GET:dynamic:options.url",
            "unresolved",
            4,
        ),
    ]
    if ambiguous:
        edges.extend(
            [
                _edge(
                    "contains-alternate",
                    "contains",
                    "file-app",
                    "endpoint-customer-alternate",
                    "resolved",
                    20,
                ),
                _edge(
                    "exposes-alternate",
                    "exposes",
                    "endpoint-customer-alternate",
                    "http:GET:/api/customers/{customerId}",
                    "external",
                    20,
                ),
            ]
        )
    if multiple_routes:
        edges.extend(
            [
                _edge(
                    "contains-account",
                    "contains",
                    "file-app",
                    "endpoint-account",
                    "resolved",
                    30,
                ),
                _edge(
                    "exposes-account",
                    "exposes",
                    "endpoint-account",
                    "http:GET:/api/accounts/{id}",
                    "external",
                    30,
                ),
            ]
        )
    return {
        "artifact_type": "CodeGraphSnapshot",
        "schema_version": "v1",
        "code_graph_snapshot_id": "graph-static",
        "project_id": "project-generic",
        "repository_id": "repository-generic",
        "repository_revision": "abcdef123456",
        "framework_profile_refs": [profile],
        "scan_roots": ["src/main"],
        "scan_status": "complete",
        "scan_mode": "full",
        "framework_markers_found": ["generic.web.Controller"],
        "diagnostics": [],
        "files": [
            {
                "file_id": "file-app",
                "path": "src/main/app.js",
                "language": "javascript",
                "role": "production",
                "content_hash": "sha256:generic",
                "symbols": symbols,
            }
        ],
        "edges": edges,
    }


def _edge(
    edge_id: str,
    edge_type: str,
    from_ref: str,
    to_ref: str,
    status: str,
    line: int,
) -> dict[str, Any]:
    return {
        "edge_id": edge_id,
        "edge_type": edge_type,
        "from_ref": from_ref,
        "to_ref": to_ref,
        "resolution_status": status,
        "confidence": "low" if status == "unresolved" else "high",
        "extractor": "web_ui_route",
        "profile_version": "generic-web@1.0.0",
        "provenance": "static",
        "evidence_refs": [],
        "source_location": {
            "path": "src/main/app.js",
            "start_line": line,
            "end_line": line,
        },
    }


def _request() -> RuntimeRouteReconcileRequest:
    return RuntimeRouteReconcileRequest(
        runtime_route_evidence_id="runtime-evidence-1",
        merged_code_graph_snapshot_id="graph-runtime-1",
        browser_run_id="browser-run-1",
        captured_at=datetime(2026, 7, 20, tzinfo=UTC),
        source_evidence_ref="evidence://project-generic/browser-run-1/network",
    )


def _observation(path: str, *, observation_id: str = "observation-1") -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "scenario_id": "customer-detail",
        "event_kind": "network_request",
        "method": "GET",
        "path": path,
        "source_action_id": "open-customer",
        "source_route_ref": "route-dynamic",
    }


def test_runtime_route_reconciliation_resolves_only_one_proven_endpoint() -> None:
    contracts = ContractCatalog.load(ROOT / "contracts")
    result = RuntimeRouteReconciler(contracts).reconcile(
        request=_request(),
        base_graph=_graph(),
        capture={"route_observations": [_observation("/api/customers/42?secret=removed")]},
    )

    assert (result.resolved_count, result.unresolved_count) == (1, 0)
    resolution = result.evidence_artifact["resolutions"][0]
    assert resolution["endpoint_ref"] == "endpoint-customer"
    assert result.graph_artifact["scan_mode"] == "runtime_enriched"
    calls = [
        edge
        for edge in cast(list[dict[str, Any]], result.graph_artifact["edges"])
        if edge["edge_type"] == "calls"
    ]
    assert calls[0]["to_ref"] == "endpoint-customer"
    assert calls[0]["provenance"] == "static_runtime"
    assert calls[0]["static_edge_ref"] == "calls-dynamic"
    assert calls[0]["evidence_refs"] == ["evidence://project-generic/browser-run-1/network"]


def test_runtime_route_reconciliation_keeps_missing_or_ambiguous_evidence_unresolved() -> None:
    contracts = ContractCatalog.load(ROOT / "contracts")
    missing_source = _observation("/api/customers/42")
    missing_source.pop("source_route_ref")
    missing = RuntimeRouteReconciler(contracts).reconcile(
        request=_request(),
        base_graph=_graph(),
        capture={"route_observations": [missing_source]},
    )
    ambiguous = RuntimeRouteReconciler(contracts).reconcile(
        request=_request(),
        base_graph=_graph(ambiguous=True),
        capture={"route_observations": [_observation("/api/customers/42")]},
    )

    assert missing.evidence_artifact["resolutions"][0]["reason"] == ("missing_source_route_ref")
    assert ambiguous.evidence_artifact["resolutions"][0]["reason"] == ("endpoint_ambiguous")
    assert missing.graph_artifact["edges"] == [
        *sorted(missing.graph_artifact["edges"], key=lambda item: str(item["edge_id"]))
    ]
    assert all(
        edge["to_ref"] != "endpoint-customer"
        for edge in cast(list[dict[str, Any]], ambiguous.graph_artifact["edges"])
        if edge["edge_type"] == "calls"
    )


def test_one_dynamic_source_observed_at_multiple_endpoints_remains_unresolved() -> None:
    result = RuntimeRouteReconciler(ContractCatalog.load(ROOT / "contracts")).reconcile(
        request=_request(),
        base_graph=_graph(multiple_routes=True),
        capture={
            "route_observations": [
                _observation("/api/customers/42", observation_id="observation-customer"),
                _observation("/api/accounts/7", observation_id="observation-account"),
            ]
        },
    )

    assert result.resolved_count == 0
    assert {item["reason"] for item in result.evidence_artifact["resolutions"]} == {
        "source_maps_multiple_endpoints"
    }
    calls = [
        edge
        for edge in cast(list[dict[str, Any]], result.graph_artifact["edges"])
        if edge["edge_type"] == "calls"
    ]
    assert calls[0]["resolution_status"] == "unresolved"
    assert calls[0]["provenance"] == "static"


def test_dynamic_source_with_multiple_unresolved_edges_is_not_guessed() -> None:
    graph = _graph()
    calls = next(
        edge for edge in cast(list[dict[str, Any]], graph["edges"]) if edge["edge_type"] == "calls"
    )
    duplicate = dict(calls)
    duplicate["edge_id"] = "calls-dynamic-duplicate"
    cast(list[dict[str, Any]], graph["edges"]).append(duplicate)

    result = RuntimeRouteReconciler(ContractCatalog.load(ROOT / "contracts")).reconcile(
        request=_request(),
        base_graph=graph,
        capture={"route_observations": [_observation("/api/customers/42")]},
    )

    assert result.resolved_count == 0
    assert result.evidence_artifact["resolutions"][0]["reason"] == ("source_route_ambiguous")
    assert (
        sum(
            edge["resolution_status"] == "unresolved"
            for edge in cast(list[dict[str, Any]], result.graph_artifact["edges"])
            if edge["edge_type"] == "calls"
        )
        == 2
    )
