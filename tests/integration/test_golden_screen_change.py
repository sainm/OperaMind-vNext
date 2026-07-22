import json
from pathlib import Path
from typing import Any

import pytest

from operamind.contracts import ContractCatalog
from operamind.domain import (
    CanonicalFactMapper,
    CanonicalSnapshot,
    SnapshotFact,
    StructuredChangeBuilder,
)
from operamind.domain.document_conventions import (
    ConventionMatcher,
    DocumentConvention,
    MatchStatus,
)
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
    match = ConventionMatcher().match(convention, registry.extract(path))
    assert match.status is MatchStatus.AUTO_MATCHED
    variant = next(
        item for item in convention.variants if item.variant_id == match.selected_variant_id
    )
    records = registry.extract_records(path, variant)
    assert len(records) == 10
    mapper = CanonicalFactMapper()
    facts: list[SnapshotFact] = []
    for index, record in enumerate(records, start=1):
        result = mapper.map_record(
            convention=convention,
            match=match,
            fact_type="screen_element",
            record=record,
        )
        assert result.fact is not None, result
        facts.append(
            SnapshotFact(
                fact_ref=f"{snapshot_id}:screen-element:{index}",
                fact=result.fact,
            )
        )
    return CanonicalSnapshot(snapshot_id=snapshot_id, facts=tuple(facts))


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
