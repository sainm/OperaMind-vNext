import json
from copy import deepcopy
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


def test_every_core_contract_has_valid_versioned_examples() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")

    report = catalog.validate_examples()

    assert report.is_valid, report.issues


def test_unknown_artifact_type_is_rejected() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")

    with pytest.raises(ArtifactValidationError) as captured:
        catalog.validate_artifact({"artifact_type": "Unknown", "schema_version": "v1"})

    assert captured.value.report.issues[0].code == "artifact.unknown_type"


def test_v2_test_data_plan_requires_real_dom_identity_observations() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    artifact = json.loads(
        (ROOT / "contracts/examples/test-data-plan.v2.example.json").read_text()
    )

    catalog.validate_artifact(artifact)
    identity = artifact["data_sets"][0]["identity_binding"]
    without_business_observation = deepcopy(artifact)
    del without_business_observation["data_sets"][0]["identity_binding"][
        "business_unique_keys"
    ][0]["dom_observation"]
    without_screen_observation = deepcopy(artifact)
    del without_screen_observation["data_sets"][0]["identity_binding"]["screen_key"][
        "dom_observation"
    ]

    assert identity["business_unique_keys"][0]["dom_observation"]
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(without_business_observation)
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(without_screen_observation)


def test_execution_result_dom_identity_locator_cannot_escape_its_container() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    artifact = json.loads(
        (ROOT / "contracts/examples/test-data-execution-result.v1.example.json").read_text()
    )
    artifact["schema_version"] = "v2"
    artifact["data_bindings"] = [
        {
            "binding_id": "binding-expense",
            "run_id": "test-data-run-expense",
            "test_data_id": "expense-returned-data",
            "binding_mode": "generated",
            "source_flow_id": "flow-expense",
            "source_step_id": "create-expense",
            "identity_provider_type": "database",
            "identity_provider_ref": "database.v1",
            "primary_key": {"name": "id", "value": 41},
            "business_unique_keys": [
                {"name": "expense_number", "value": "EXP-041"}
            ],
            "screen_key": {"name": "expense_number", "value": "EXP-041"},
            "screen_identity_values": [
                {"name": "expense_number", "value": "EXP-041"}
            ],
            "screen_locator": {
                "by": "css",
                "value": "[data-expense-number='EXP-041']",
                "exact": True,
            },
            "record_scope_locator": {
                "by": "css",
                "value": "[data-expense-number='EXP-041']",
                "exact": True,
            },
            "identity_observations": {
                "business_unique_keys": [
                    {
                        "name": "expense_number",
                        "kind": "attribute",
                        "attribute_name": "data-expense-number",
                        "locator": {
                            "by": "css",
                            "value": ":scope",
                            "exact": True,
                        },
                    }
                ],
                "screen_key": {
                    "name": "expense_number",
                    "kind": "attribute",
                    "attribute_name": "data-expense-number",
                },
            },
            "identity_digest": "b" * 64,
            "match_count": 1,
            "frozen_at": "2026-08-04T00:00:00Z",
            "content_digest": "c" * 64,
            "evidence_ref": "evidence://visiondemo/test-data-run-expense/binding-expense",
        }
    ]

    catalog.validate_artifact(artifact)
    incomplete_current = deepcopy(artifact)
    del incomplete_current["data_bindings"][0]["identity_digest"]
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(incomplete_current)
    legacy_with_v2_binding = deepcopy(artifact)
    legacy_with_v2_binding["schema_version"] = "v1"
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(legacy_with_v2_binding)
    artifact["data_bindings"][0]["identity_observations"]["business_unique_keys"][0][
        "locator"
    ]["frame"] = "iframe#foreign"
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(artifact)


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


def test_legacy_ingestion_artifact_without_variant_provenance_remains_valid() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    artifact = json.loads(
        (ROOT / "contracts/examples/document-ingestion-result.v1.example.json").read_text()
    )
    for field in (
        "source_variant_ids",
        "target_variant_ids",
        "source_fact_variant_ids",
        "target_fact_variant_ids",
        "source_ignored_sections",
        "target_ignored_sections",
    ):
        artifact.pop(field, None)

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


def test_ui_verification_v2_requires_orchestration_identity_and_keeps_v1_immutable() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    legacy = json.loads(
        (ROOT / "contracts/examples/ui-verification-result.v1.example.json").read_text()
    )
    current = json.loads(
        (ROOT / "contracts/examples/ui-verification-result.v2.example.json").read_text()
    )

    catalog.validate_artifact(legacy)
    catalog.validate_artifact(current)

    legacy["orchestration_id"] = "orchestration-legacy"
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(legacy)

    current.pop("orchestration_id")
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(current)

    current = json.loads(
        (ROOT / "contracts/examples/ui-verification-result.v2.example.json").read_text()
    )
    current.pop("test_data_execution_result_id")
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(current)


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


def test_copilot_change_task_v2_requires_the_single_six_stage_flow() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    artifact = json.loads(
        (ROOT / "contracts/examples/copilot-coding-task.v2.example.json").read_text()
    )

    catalog.validate_artifact(artifact)
    artifact["workflow"]["stage_order"] = ["requirement", "document_change"]

    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(artifact)


def test_changed_line_coverage_contract_cannot_lower_system_threshold() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    artifact = json.loads(
        (ROOT / "contracts/examples/changed-line-coverage-report.v1.example.json").read_text()
    )
    artifact["minimum_coverage_percent"] = 79

    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(artifact)


def test_change_closure_contract_keeps_v1_immutable_and_requires_v2_coverage() -> None:
    catalog = ContractCatalog.load(ROOT / "contracts")
    legacy = json.loads(
        (ROOT / "contracts/examples/change-closure-result.v1.example.json").read_text()
    )
    current = json.loads(
        (ROOT / "contracts/examples/change-closure-result.v2.example.json").read_text()
    )

    catalog.validate_artifact(legacy)
    catalog.validate_artifact(current)

    legacy["changed_line_coverage_percent"] = 100
    legacy["changed_line_coverage_status"] = "passed"
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(legacy)

    current.pop("changed_line_coverage_status")
    with pytest.raises(ArtifactValidationError):
        catalog.validate_artifact(current)
