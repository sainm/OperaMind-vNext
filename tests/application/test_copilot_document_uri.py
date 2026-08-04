from pathlib import Path
from types import SimpleNamespace

import pytest

from operamind.application.copilot_document_change import (
    CopilotDocumentChangeResult,
    CopilotDocumentChangeService,
    DocumentFieldEdit,
    _file_digest,
    _file_uri_path,
    _id,
    _source_namespace,
    _temporary_sibling,
    _trusted_file_path,
    _xlsx_location,
)


def test_windows_file_uri_preserves_drive_letter_and_decodes_spaces() -> None:
    path = _file_uri_path("file:///C:/work/design%20book.xlsx", platform_name="nt")

    assert str(path) == "C:\\work\\design book.xlsx"


def test_posix_file_uri_remains_absolute() -> None:
    path = _file_uri_path("file:///tmp/design%20book.xlsx", platform_name="posix")

    assert path == Path("/tmp/design book.xlsx")


def test_document_field_edit_rejects_blank_large_and_formula_values() -> None:
    for value, message in (
        (" ", "must not be blank"),
        ("x" * 20_001, "exceeds"),
        (" =SUM(A1:A2)", "formula"),
    ):
        with pytest.raises(ValueError, match=message):
            DocumentFieldEdit("document-1", "key-1", "summary", value)


def test_document_change_value_helpers_are_deterministic_and_bounded(tmp_path: Path) -> None:
    source = tmp_path / "design book.xlsx"
    source.write_bytes(b"design")
    replacement = _temporary_sibling(source, "replacement")
    try:
        assert replacement.parent == tmp_path
        assert replacement.suffix == ".xlsx"
        assert _xlsx_location(source, "design book.xlsx#Sheet 1!B2") == ("Sheet 1", "B2")
        assert _trusted_file_path(source.as_uri()) == source
        assert len(_file_digest(source)) == 64
        assert _id("change", "document-1", "revision-1") == _id(
            "change", "document-1", "revision-1"
        )
    finally:
        replacement.unlink()

    with pytest.raises(ValueError, match="invalid source cell"):
        _xlsx_location(source, "other.xlsx#Sheet 1!B2")
    with pytest.raises(ValueError, match="local file URI"):
        _trusted_file_path("https://example.invalid/design.xlsx")


def test_document_change_result_has_a_public_serializable_shape() -> None:
    result = CopilotDocumentChangeResult(
        source_snapshot_id="source-1",
        target_snapshot_id="target-1",
        document_ids=("document-1",),
        source_paths=(Path("/tmp/design.xlsx"),),
        change_refs=("change-1",),
    )

    assert result.to_dict() == {
        "source_snapshot_id": "source-1",
        "target_snapshot_id": "target-1",
        "document_ids": ["document-1"],
        "source_paths": ["/tmp/design.xlsx"],
        "change_refs": ["change-1"],
    }


def test_document_change_service_loads_repository_catalogs_and_namespaced_keys() -> None:
    root = Path(__file__).resolve().parents[2]

    service = CopilotDocumentChangeService(
        connection=SimpleNamespace(), repository_root=root
    )
    source = SimpleNamespace(
        document_id="document-1",
        snapshot=SimpleNamespace(
            facts=(
                SimpleNamespace(
                    fact=SimpleNamespace(stable_key="screen:document-1/status")
                ),
            )
        ),
    )

    assert service._contracts.root == root / "contracts"
    assert service._profiles.root == root / "profiles"
    assert _source_namespace(source) == "document-1"


def test_rollback_accepts_an_already_restored_trusted_document(tmp_path: Path) -> None:
    source_path = tmp_path / "design.xlsx"
    source_path.write_bytes(b"original design")
    source = SimpleNamespace(
        document_id="document-1",
        source_ref=source_path.as_uri(),
        content_digest=_file_digest(source_path),
    )
    target = SimpleNamespace(
        document_id="document-1",
        source_ref=source_path.as_uri(),
        content_digest="f" * 64,
    )
    service = object.__new__(CopilotDocumentChangeService)
    service._required_source = lambda **values: (  # type: ignore[method-assign]
        source if values["source_snapshot_id"] == "snapshot-before" else target
    )

    paths = service.rollback_materialized(
        project_id="project-1",
        source_snapshot_id="snapshot-before",
        target_snapshot_id="snapshot-after",
        document_ids=("document-1",),
    )

    assert paths == (source_path,)
    with pytest.raises(ValueError, match="non-empty and unique"):
        service.rollback_materialized(
            project_id="project-1",
            source_snapshot_id="snapshot-before",
            target_snapshot_id="snapshot-after",
            document_ids=(),
        )
