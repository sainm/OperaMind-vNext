from pathlib import Path
from typing import Any

from operamind.contracts import ContractCatalog
from operamind.unresolved_evidence import UnresolvedEvidenceReportBuilder

ROOT = Path(__file__).parents[2]


def _graph(*, snapshot_id: str = "graph-base") -> dict[str, Any]:
    return {
        "artifact_type": "CodeGraphSnapshot",
        "schema_version": "v1",
        "code_graph_snapshot_id": snapshot_id,
        "project_id": "project-customer",
        "repository_id": "repository-customer",
        "repository_revision": "revision-customer-1",
        "framework_profile_refs": ["generic-web@1"],
        "scan_roots": ["src"],
        "scan_status": "complete",
        "scan_mode": "full",
        "changed_paths": [],
        "affected_paths": ["src/customer.js", "src/CustomerController.java"],
        "scanned_file_count": 2,
        "reused_file_count": 0,
        "framework_markers_found": ["fetch"],
        "diagnostics": [],
        "files": [
            {
                "file_id": "file-ui",
                "path": "src/customer.js",
                "language": "javascript",
                "role": "production",
                "content_hash": "sha256:ui",
                "symbols": [
                    {
                        "symbol_id": "route-customer",
                        "symbol_type": "ui_route",
                        "name": "dynamic:customerUrl",
                        "signature": "route:GET:dynamic:customerUrl",
                        "start_line": 12,
                        "end_line": 12,
                    }
                ],
            },
            {
                "file_id": "file-controller",
                "path": "src/CustomerController.java",
                "language": "java",
                "role": "production",
                "content_hash": "sha256:controller",
                "symbols": [
                    {
                        "symbol_id": "endpoint-customer",
                        "symbol_type": "method",
                        "name": "customer",
                        "signature": "customer(String id)",
                        "start_line": 20,
                        "end_line": 24,
                    },
                    {
                        "symbol_id": "method-refresh",
                        "symbol_type": "method",
                        "name": "refresh",
                        "signature": "refresh()",
                        "start_line": 30,
                        "end_line": 32,
                    },
                ],
            },
        ],
        "edges": [
            {
                "edge_id": "edge-exposes-customer",
                "edge_type": "exposes",
                "from_ref": "endpoint-customer",
                "to_ref": "http:GET:/customers/{id}",
                "resolution_status": "external",
                "confidence": "high",
                "extractor": "generic_web",
                "profile_version": "generic-web@1",
                "provenance": "static",
                "evidence_refs": [],
                "source_location": {
                    "path": "src/CustomerController.java",
                    "start_line": 20,
                    "end_line": 20,
                },
            },
            {
                "edge_id": "edge-route-dynamic",
                "edge_type": "calls",
                "from_ref": "route-customer",
                "to_ref": "unresolved:endpoint:GET:dynamic:customerUrl",
                "resolution_status": "unresolved",
                "confidence": "low",
                "extractor": "web_ui_route",
                "profile_version": "generic-web@1",
                "provenance": "static",
                "evidence_refs": [],
                "source_location": {
                    "path": "src/customer.js",
                    "start_line": 12,
                    "end_line": 12,
                },
            },
            {
                "edge_id": "edge-call-refresh",
                "edge_type": "calls",
                "from_ref": "endpoint-customer",
                "to_ref": "unresolved:call:helper.refresh/0",
                "resolution_status": "unresolved",
                "confidence": "low",
                "extractor": "java_call",
                "profile_version": "generic-web@1",
                "provenance": "static",
                "evidence_refs": [],
                "source_location": {
                    "path": "src/CustomerController.java",
                    "start_line": 22,
                    "end_line": 22,
                },
            },
        ],
    }


def test_report_classifies_every_unresolved_edge_and_exposes_guidance() -> None:
    contracts = ContractCatalog.load(ROOT / "contracts")
    result = UnresolvedEvidenceReportBuilder(contracts).build(graph=_graph())

    assert result.open_count == 2
    assert result.closed_count == 0
    assert result.artifact["report_status"] == "needs_evidence"
    items = {item["edge_ref"]: item for item in result.artifact["items"]}
    route = items["edge-route-dynamic"]
    assert route["category"] == "endpoint_route"
    assert route["reason"] == "runtime_observation_missing"
    assert route["missing_evidence"] == ["runtime_route_observation"]
    assert route["resolution_suggestions"] == ["collect_runtime_route"]
    assert route["source_location"]["path"] == "src/customer.js"
    call = items["edge-call-refresh"]
    assert call["candidate_targets"] == [
        {
            "target_ref": "method-refresh",
            "match_basis": "signature",
            "source_location": {
                "path": "src/CustomerController.java",
                "start_line": 30,
                "end_line": 32,
            },
        }
    ]
    assert call["reason"] == "unresolved_reference"


def test_multiple_static_candidates_remain_open_and_are_all_visible() -> None:
    contracts = ContractCatalog.load(ROOT / "contracts")
    graph = _graph()
    graph["files"][1]["symbols"].append(
        {
            "symbol_id": "method-refresh-alternative",
            "symbol_type": "method",
            "name": "refresh",
            "signature": "refresh()",
            "start_line": 35,
            "end_line": 37,
        }
    )

    report = UnresolvedEvidenceReportBuilder(contracts).build(graph=graph).artifact
    call = next(item for item in report["items"] if item["edge_ref"] == "edge-call-refresh")

    assert call["status"] == "open"
    assert call["reason"] == "target_ambiguous"
    assert [item["target_ref"] for item in call["candidate_targets"]] == [
        "method-refresh",
        "method-refresh-alternative",
    ]
    assert call["missing_evidence"] == ["unique_target_discriminator"]


def test_runtime_unique_proof_closes_prior_finding_without_erasing_history() -> None:
    contracts = ContractCatalog.load(ROOT / "contracts")
    builder = UnresolvedEvidenceReportBuilder(contracts)
    base = _graph()
    predecessor = builder.build(graph=base).artifact
    enriched = _graph(snapshot_id="graph-runtime")
    enriched.update(
        scan_mode="runtime_enriched",
        base_code_graph_snapshot_id="graph-base",
        changed_paths=[],
        affected_paths=[],
        scanned_file_count=0,
        reused_file_count=2,
        runtime_evidence_refs=["runtime-route-customer"],
    )
    enriched["edges"] = [
        edge
        for edge in enriched["edges"]
        if edge["edge_id"] != "edge-route-dynamic"
    ]
    enriched["edges"].append(
        {
            "edge_id": "edge-route-runtime",
            "edge_type": "calls",
            "from_ref": "route-customer",
            "to_ref": "endpoint-customer",
            "resolution_status": "resolved",
            "confidence": "high",
            "extractor": "runtime_route_evidence",
            "profile_version": "generic-web@1",
            "provenance": "static_runtime",
            "evidence_refs": ["browser-network-summary-customer"],
            "static_edge_ref": "edge-route-dynamic",
            "source_location": {
                "path": "src/customer.js",
                "start_line": 12,
                "end_line": 12,
            },
        }
    )

    result = builder.build(graph=enriched, predecessor=predecessor)

    assert result.open_count == 1
    assert result.closed_count == 1
    assert predecessor["items"][0]["status"] == "open"
    closed = next(item for item in result.artifact["items"] if item["status"] == "closed")
    assert closed["edge_ref"] == "edge-route-dynamic"
    assert closed["closure"] == {
        "resolved_target_ref": "endpoint-customer",
        "resolved_edge_ref": "edge-route-runtime",
        "proof_kind": "static_runtime_unique",
        "evidence_refs": [
            "browser-network-summary-customer",
            "code-graph:graph-runtime:edge:edge-route-runtime",
        ],
    }
    assert result.artifact["predecessor_report_id"] == predecessor[
        "unresolved_evidence_report_id"
    ]


def test_new_static_graph_closes_only_the_same_finding_with_one_resolved_edge() -> None:
    contracts = ContractCatalog.load(ROOT / "contracts")
    builder = UnresolvedEvidenceReportBuilder(contracts)
    predecessor = builder.build(graph=_graph()).artifact
    current = _graph(snapshot_id="graph-static-2")
    current["edges"] = [
        edge for edge in current["edges"] if edge["edge_id"] != "edge-call-refresh"
    ]
    current["edges"].append(
        {
            "edge_id": "edge-call-refresh-resolved",
            "edge_type": "calls",
            "from_ref": "endpoint-customer",
            "to_ref": "method-refresh",
            "resolution_status": "resolved",
            "confidence": "high",
            "extractor": "java_call",
            "profile_version": "generic-web@1",
            "provenance": "static",
            "evidence_refs": [],
            "source_location": {
                "path": "src/CustomerController.java",
                "start_line": 22,
                "end_line": 22,
            },
        }
    )

    result = builder.build(graph=current, predecessor=predecessor)

    assert result.open_count == 1
    assert result.closed_count == 1
    closed = next(item for item in result.artifact["items"] if item["status"] == "closed")
    assert closed["edge_ref"] == "edge-call-refresh"
    assert closed["closure"]["proof_kind"] == "static_unique"
    assert closed["closure"]["resolved_target_ref"] == "method-refresh"
    assert result.artifact["trigger"]["trigger_type"] == "static_graph"


def test_multiple_resolved_edges_do_not_close_one_prior_finding() -> None:
    contracts = ContractCatalog.load(ROOT / "contracts")
    builder = UnresolvedEvidenceReportBuilder(contracts)
    predecessor = builder.build(graph=_graph()).artifact
    current = _graph(snapshot_id="graph-ambiguous")
    current["edges"] = [
        edge for edge in current["edges"] if edge["edge_id"] != "edge-route-dynamic"
    ]
    for suffix in ("a", "b"):
        current["edges"].append(
            {
                "edge_id": f"edge-route-{suffix}",
                "edge_type": "calls",
                "from_ref": "route-customer",
                "to_ref": f"endpoint-customer-{suffix}",
                "resolution_status": "resolved",
                "confidence": "high",
                "extractor": "runtime_route_evidence",
                "profile_version": "generic-web@1",
                "provenance": "static_runtime",
                "evidence_refs": [f"runtime-{suffix}"],
                "static_edge_ref": "edge-route-dynamic",
                "source_location": {
                    "path": "src/customer.js",
                    "start_line": 12,
                    "end_line": 12,
                },
            }
        )

    result = builder.build(graph=current, predecessor=predecessor)

    assert result.closed_count == 0
    assert all(item["edge_ref"] != "edge-route-dynamic" for item in result.artifact["items"])


def test_all_unresolved_namespaces_receive_a_category_and_actionable_evidence_gap() -> None:
    contracts = ContractCatalog.load(ROOT / "contracts")
    graph = _graph()
    cases = [
        ("table", "reads", "unresolved:table:customers", "data_table"),
        ("entity", "maps_to", "unresolved:entity:Customer", "entity_mapping"),
        ("config", "reads", "unresolved:config_key:customer.limit", "config_key"),
        ("route", "navigates_to", "unresolved:route:/customers", "navigation_target"),
        (
            "struts-route",
            "exposes",
            "unresolved:struts_endpoint:/expenses",
            "endpoint_route",
        ),
        (
            "struts-forward",
            "navigates_to",
            "unresolved:struts_forward:success",
            "navigation_target",
        ),
        ("generic", "implements", "unresolved:unknown:CustomerPort", "generic_relation"),
        ("external", "calls", "external:call:vendor.lookup", "generic_relation"),
    ]
    for line, (name, edge_type, target, _category) in enumerate(cases, start=40):
        graph["edges"].append(
            {
                "edge_id": f"edge-{name}",
                "edge_type": edge_type,
                "from_ref": "endpoint-customer",
                "to_ref": target,
                "resolution_status": "unresolved",
                "confidence": "low",
                "extractor": "generic_relation",
                "profile_version": "generic-web@1",
                "provenance": "static",
                "evidence_refs": [],
                "source_location": {
                    "path": "src/CustomerController.java",
                    "start_line": line,
                    "end_line": line,
                },
            }
        )

    report = UnresolvedEvidenceReportBuilder(contracts).build(graph=graph).artifact
    items = {item["edge_ref"]: item for item in report["items"]}

    for name, _edge_type, _target, category in cases:
        item = items[f"edge-{name}"]
        assert item["category"] == category
        assert item["missing_evidence"]
        assert item["resolution_suggestions"]
    assert items["edge-external"]["reason"] == "external_reference_unverified"
    assert items["edge-route"]["category"] == "navigation_target"
