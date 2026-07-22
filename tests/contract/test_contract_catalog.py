import json
from pathlib import Path

import pytest

from operamind.contracts.catalog import (
    EXPECTED_ARTIFACT_TYPES,
    ArtifactValidationError,
    ContractCatalog,
)

ROOT = Path(__file__).parents[2]


def test_core_contract_catalog_is_complete_and_valid() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")

    report = catalog.validate_catalog()

    assert report.is_valid, report.issues
    assert frozenset(catalog.schema_paths) == EXPECTED_ARTIFACT_TYPES


def test_every_core_contract_has_a_valid_v1_example() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")

    report = catalog.validate_examples()

    assert report.is_valid, report.issues


def test_unknown_artifact_type_is_rejected() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")

    with pytest.raises(ArtifactValidationError) as captured:
        catalog.validate_artifact({"artifact_type": "Unknown", "schema_version": "v1"})

    assert captured.value.report.issues[0].code == "artifact.unknown_type"


def test_ready_ingestion_artifact_requires_exact_build_and_profile_identity() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    artifact = json.loads(
        (ROOT / "contracts/examples/document-ingestion-result.v1.example.json").read_text()
    )

    for field in (
        "search_index_build_id",
        "embedding_profile_version_id",
        "embedding_profile_binding_key",
        "embedding_profile_ref",
    ):
        incomplete = dict(artifact)
        incomplete.pop(field)
        with pytest.raises(ArtifactValidationError):
            catalog.validate_artifact(incomplete)

    artifact["embedding_index_status"] = "stale"
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(artifact)


def test_code_graph_edge_requires_extraction_provenance() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    artifact = {
        "artifact_type": "CodeGraphSnapshot",
        "schema_version": "v1",
        "code_graph_snapshot_id": "graph-1",
        "project_id": "project-1",
        "repository_id": "repository-1",
        "repository_revision": "abc123",
        "framework_profile_refs": ["spring-web@1"],
        "scan_roots": ["src"],
        "scan_status": "complete",
        "framework_markers_found": ["org.springframework"],
        "diagnostics": [],
        "files": [],
        "edges": [
            {
                "edge_id": "edge-1",
                "edge_type": "calls",
                "from_ref": "symbol:a",
                "to_ref": "symbol:b",
                "resolution_status": "resolved",
                "confidence": "high",
            }
        ],
    }

    with pytest.raises(ArtifactValidationError) as captured:
        catalog.validate_artifact(artifact)

    messages = {issue.message for issue in captured.value.report.issues}
    assert any("extractor" in message for message in messages)
    assert any("profile_version" in message for message in messages)
    assert any("source_location" in message for message in messages)


def test_ui_recovery_artifact_must_be_a_blocked_evidence_free_closure() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    artifact = json.loads(
        (ROOT / "contracts/examples/ui-verification-result.v1.example.json").read_text()
    )
    artifact["status"] = "blocked"
    artifact["scenario_results"][0].update(
        {
            "status": "blocked",
            "evidence_refs": [],
            "failure_category": "blocked",
        }
    )
    artifact["recovery"] = {
        "recovery_id": artifact["verification_result_id"],
        "cause": "interrupted_execution",
        "actor": "operator@example.invalid",
        "reason": "browser worker process was interrupted",
        "stale_before": "2026-07-16T12:00:00Z",
    }

    catalog.validate_artifact(artifact)

    artifact["status"] = "passed"
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(artifact)


def test_copilot_coding_task_contract_reserves_future_api_provider_route() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    artifact = json.loads(
        (ROOT / "contracts/examples/copilot-coding-task.v1.example.json").read_text()
    )
    artifact["provider_contract"] = {
        "interface": "coding_task_provider_v1",
        "route": "api_provider",
        "provider_id": "production-coding-provider",
    }

    catalog.validate_artifact(artifact)
