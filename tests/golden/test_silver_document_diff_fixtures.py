from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from openpyxl import Workbook

from operamind.application import DocumentDiffRequest, DocumentDiffService
from operamind.contracts import ContractCatalog
from operamind.domain.document_conventions import DocumentConvention
from operamind.infrastructure.documents import DocumentSignalExtractorRegistry
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
DATASET_ROOT = ROOT / "golden-dataset"


def _load_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_workbook(root: Path, specification: dict[str, Any]) -> Path:
    path = root / str(specification["filename"])
    workbook = Workbook()
    first = workbook.active
    assert first is not None
    for index, raw_sheet in enumerate(cast(list[dict[str, Any]], specification["sheets"])):
        sheet = first if index == 0 else workbook.create_sheet()
        sheet.title = str(raw_sheet["name"])
        for row in cast(list[list[object]], raw_sheet["rows"]):
            sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


def _profile_for(fixture: dict[str, Any]) -> dict[str, Any]:
    filename = (
        "screen-design-convention-profile.example.json"
        if fixture["profile"] == "screen_design"
        else "document-convention-profile.example.json"
    )
    profile = _load_object(ROOT / "profiles" / filename)
    ProfileCatalog.load(ROOT / "profiles").validate_profile(profile)
    return profile


def test_portable_silver_document_diff_fixtures_match_expected_changes(
    tmp_path: Path,
) -> None:
    manifest = _load_object(DATASET_ROOT / "manifest.silver.json")
    cases = cast(list[dict[str, Any]], manifest["cases"])
    fixture_cases = [case for case in cases if "source_fixture" in case]
    assert len(fixture_cases) == 3
    observed_change_types: set[str] = set()
    service = DocumentDiffService(
        extractors=DocumentSignalExtractorRegistry.default(),
        contracts=ContractCatalog.load(ROOT / "contracts"),
    )

    for case in fixture_cases:
        fixture = _load_object(DATASET_ROOT / str(case["source_fixture"]))
        expected = _load_object(DATASET_ROOT / str(case["expected_changes"]))
        case_root = tmp_path / str(case["case_id"])
        case_root.mkdir()
        before_path = _write_workbook(
            case_root,
            cast(dict[str, Any], fixture["before"]),
        )
        after_path = _write_workbook(
            case_root,
            cast(dict[str, Any], fixture["after"]),
        )
        result = service.run(
            DocumentDiffRequest(
                project_id=str(fixture["project_id"]),
                domain=str(fixture["domain"]),
                fact_type=str(fixture["fact_type"]),
                source_snapshot_id=f"{case['case_id']}-before",
                target_snapshot_id=f"{case['case_id']}-after",
                before_path=before_path,
                after_path=after_path,
            ),
            DocumentConvention.from_validated_profile(_profile_for(fixture)),
        )

        expected_changes = cast(list[dict[str, Any]], expected["changes"])
        assert len(result.changes) == expected["expected_structured_change_count"]
        assert len(result.changes) == len(expected_changes) == 1
        actual_change = result.changes[0]
        expected_change = expected_changes[0]
        assert actual_change.stable_key == expected_change["stable_key"]
        assert actual_change.fact_type == expected_change["fact_type"]
        assert actual_change.domain == expected_change["domain"]
        assert actual_change.change_type.value == expected_change["change_type"]
        observed_change_types.add(actual_change.change_type.value)
        for delta in cast(list[dict[str, object]], expected_change["field_deltas"]):
            field = str(delta["field"])
            before_value = (
                actual_change.before.values.get(field) if actual_change.before is not None else None
            )
            after_value = (
                actual_change.after.values.get(field) if actual_change.after is not None else None
            )
            assert before_value == delta["before"]
            assert after_value == delta["after"]
            assert str(delta["source_ref"]) in actual_change.source_refs
        assert len(result.source_fact_variant_ids) == len(result.source_snapshot.facts)
        assert len(result.target_fact_variant_ids) == len(result.target_snapshot.facts)

    assert observed_change_types == {"added", "deleted", "modified"}


def test_silver_manifest_covers_add_delete_cross_screen_and_api_change() -> None:
    manifest = _load_object(DATASET_ROOT / "manifest.silver.json")
    cases = cast(list[dict[str, Any]], manifest["cases"])
    by_id = {str(case["case_id"]): case for case in cases}

    assert len(cases) == 5
    assert "visiondemo-screen-approval-note-added" in by_id
    assert "visiondemo-screen-cancel-event-deleted" in by_id
    assert "visiondemo-expense-employee-cross-screen" in by_id
    assert "test_data_plan" in by_id["visiondemo-expense-employee-cross-screen"]
    assert "visiondemo-api-total-count-type-changed" in by_id

    for case in cases:
        source = _load_object(DATASET_ROOT / str(case["source_manifest"]))
        assert source["dataset_stage"] == "silver"
        assert source["review_status"] != "approved"
