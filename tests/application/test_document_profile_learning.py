from pathlib import Path
from typing import Any, cast

from openpyxl import Workbook

from operamind.application.document_profile_learning import DocumentProfileLearningService

ROOT = Path(__file__).parents[2]


def _write_design(path: Path, *, value: str, extra_header: str | None = None) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Screen Items"
    headers = ["Screen ID", "Element ID", "Type", "Default Value", "Notes"]
    if extra_header is not None:
        headers.append(extra_header)
    sheet.append(headers)
    row = ["customer-list", "status-filter", "select", value, "filter"]
    if extra_header is not None:
        row.append("required")
    sheet.append(row)
    workbook.save(path)


def _service() -> DocumentProfileLearningService:
    return DocumentProfileLearningService(
        connection=cast(Any, object()),
        repository_root=ROOT,
    )


def test_structure_digest_ignores_business_values_but_detects_format_change(
    tmp_path: Path,
) -> None:
    design = tmp_path / "screen-design.xlsx"
    _write_design(design, value="all")
    service = _service()
    original = service.extract_structure(
        project_id="project-a",
        document_roots=(tmp_path,),
    )

    _write_design(design, value="returned")
    content_only = service.extract_structure(
        project_id="project-a",
        document_roots=(tmp_path,),
    )
    _write_design(design, value="returned", extra_header="Required")
    format_changed = service.extract_structure(
        project_id="project-a",
        document_roots=(tmp_path,),
    )

    assert content_only.digest == original.digest
    assert content_only.payload != original.payload
    assert format_changed.digest != original.digest


def test_structure_identity_is_isolated_per_project(tmp_path: Path) -> None:
    _write_design(tmp_path / "screen-design.xlsx", value="all")
    service = _service()

    first = service.extract_structure(project_id="project-a", document_roots=(tmp_path,))
    second = service.extract_structure(project_id="project-b", document_roots=(tmp_path,))

    assert first.digest != second.digest
    assert first.payload["project_id"] == "project-a"
    assert second.payload["project_id"] == "project-b"
