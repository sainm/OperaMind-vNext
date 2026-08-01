from pathlib import Path

from operamind.application.copilot_document_change import _file_uri_path


def test_windows_file_uri_preserves_drive_letter_and_decodes_spaces() -> None:
    path = _file_uri_path("file:///C:/work/design%20book.xlsx", platform_name="nt")

    assert str(path) == "C:\\work\\design book.xlsx"


def test_posix_file_uri_remains_absolute() -> None:
    path = _file_uri_path("file:///tmp/design%20book.xlsx", platform_name="posix")

    assert path == Path("/tmp/design book.xlsx")
