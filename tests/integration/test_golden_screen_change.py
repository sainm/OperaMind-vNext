import json
from pathlib import Path
from typing import Any

import pytest

from operamind.application import DocumentDiffService
from operamind.contracts import ContractCatalog
from operamind.domain import (
    CanonicalSnapshot,
    StructuredChangeBuilder,
)
from operamind.domain.document_conventions import DocumentConvention
from operamind.infrastructure.documents import DocumentSignalExtractorRegistry
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
CASE_ROOT = ROOT / "golden-dataset/cases/visiondemo-expense-status-filter"


def load_json(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def build_snapshot(
    *,
    root: Path,
    filename: str,
    snapshot_id: str,
    convention: DocumentConvention,
) -> CanonicalSnapshot:
    path = root / filename
    registry = DocumentSignalExtractorRegistry.default()
    return (
        DocumentDiffService(
            extractors=registry,
            contracts=ContractCatalog.load(ROOT / "contracts"),
        )
        .build_snapshot(
            path=path,
            snapshot_id=snapshot_id,
            fact_type="screen_element",
            convention=convention,
        )
        .snapshot
    )


@pytest.mark.integration
def test_real_screen_design_diff_matches_silver_expected_change() -> None:
    source_manifest = load_json(CASE_ROOT / "source-manifest.json")
    sources = source_manifest["document_sources"]
    assert isinstance(sources, dict)
    before_root = Path(str(sources["before_root"]))
    after_root = Path(str(sources["after_root"]))
    changed_file = str(sources["changed_file"])
    if not (before_root / changed_file).is_file() or not (after_root / changed_file).is_file():
        pytest.skip("Local-only Golden source documents are unavailable")

    profile = load_json(ROOT / "profiles/screen-design-convention-profile.example.json")
    ProfileCatalog.load(ROOT / "profiles").validate_profile(profile)
    convention = DocumentConvention.from_validated_profile(profile)
    before = build_snapshot(
        root=before_root,
        filename=changed_file,
        snapshot_id="document-snapshot-before",
        convention=convention,
    )
    after = build_snapshot(
        root=after_root,
        filename=changed_file,
        snapshot_id="document-snapshot-after",
        convention=convention,
    )

    changes = StructuredChangeBuilder().diff(
        project_id=str(source_manifest["project_id"]),
        source=before,
        target=after,
        domain="ui",
    )
    expected = load_json(CASE_ROOT / "expected-changes.silver.json")
    expected_changes = expected["changes"]
    assert isinstance(expected_changes, list)

    assert len(changes) == expected["expected_structured_change_count"] == 1
    actual = changes[0]
    expected_change = expected_changes[0]
    assert isinstance(expected_change, dict)
    assert actual.stable_key == expected_change["stable_key"]
    assert actual.fact_type == expected_change["fact_type"]
    assert actual.domain == expected_change["domain"]
    assert actual.change_type.value == expected_change["change_type"]
    assert actual.confidence.value == expected_change["confidence"]
    assert actual.review_status.value == expected_change["review_status"]
    assert actual.before is not None
    assert actual.after is not None
    field_deltas = expected_change["field_deltas"]
    assert isinstance(field_deltas, list)
    for raw_delta in field_deltas:
        assert isinstance(raw_delta, dict)
        field = str(raw_delta["field"])
        assert actual.before.values[field] == raw_delta["before"]
        assert actual.after.values[field] == raw_delta["after"]
        assert raw_delta["source_ref"] in actual.before.source_refs
        assert raw_delta["source_ref"] in actual.after.source_refs

    ContractCatalog.load(ROOT / "contracts").validate_artifact(actual.to_artifact())


@pytest.mark.integration
def test_real_api_designs_use_all_sheet_variants_without_false_changes() -> None:
    source_manifest = load_json(CASE_ROOT / "source-manifest.json")
    sources = source_manifest["document_sources"]
    assert isinstance(sources, dict)
    before_root = Path(str(sources["before_root"]))
    after_root = Path(str(sources["after_root"]))
    before_paths = sorted(before_root.glob("*API*.xlsx"))
    if not before_paths or any(not (after_root / path.name).is_file() for path in before_paths):
        pytest.skip("Local-only Golden API source documents are unavailable")

    profile = load_json(ROOT / "profiles/document-convention-profile.example.json")
    ProfileCatalog.load(ROOT / "profiles").validate_profile(profile)
    convention = DocumentConvention.from_validated_profile(profile)
    service = DocumentDiffService(
        extractors=DocumentSignalExtractorRegistry.default(),
        contracts=ContractCatalog.load(ROOT / "contracts"),
    )
    total_before_facts = 0
    total_after_facts = 0
    for index, before_path in enumerate(before_paths):
        after_path = after_root / before_path.name
        before = service.build_snapshot(
            path=before_path,
            snapshot_id=f"api-before-{index}",
            fact_type="api_field",
            convention=convention,
        )
        after = service.build_snapshot(
            path=after_path,
            snapshot_id=f"api-after-{index}",
            fact_type="api_field",
            convention=convention,
        )

        assert before.selected_variant_ids == ("api-object-table", "api-list-url")
        assert after.selected_variant_ids == ("api-object-table", "api-list-url")
        assert before.ignored_sections == ()
        assert after.ignored_sections == ()
        assert len(before.fact_variant_ids) == len(before.snapshot.facts)
        assert len(after.fact_variant_ids) == len(after.snapshot.facts)
        assert set(dict(before.fact_variant_ids).values()) == {
            "api-object-table",
            "api-list-url",
        }
        assert set(dict(after.fact_variant_ids).values()) == {
            "api-object-table",
            "api-list-url",
        }
        assert (
            StructuredChangeBuilder().diff(
                project_id=str(source_manifest["project_id"]),
                source=before.snapshot,
                target=after.snapshot,
                domain="api",
            )
            == ()
        )
        total_before_facts += len(before.snapshot.facts)
        total_after_facts += len(after.snapshot.facts)

    assert len(before_paths) == 6
    assert total_before_facts == total_after_facts == 81
