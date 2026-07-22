import subprocess
from pathlib import Path

import pytest

from operamind.infrastructure.code_graph import (
    PreEditedWorkspaceVerifier,
    SafeWorkspaceEditor,
    TextReplacement,
)


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "remote", "add", "origin", "https://example.invalid/repository.git")
    source = root / "Service.java"
    source.write_text("return search(status);\n", encoding="utf-8")
    _git(root, "add", "Service.java")
    _git(root, "commit", "-q", "-m", "base")
    return _git(root, "rev-parse", "HEAD")


def test_safe_workspace_editor_applies_only_approved_exact_replacement(tmp_path: Path) -> None:
    revision = _repository(tmp_path)

    result = SafeWorkspaceEditor().apply(
        workspace_root=tmp_path,
        base_revision=revision,
        replacements=(
            TextReplacement(
                path="Service.java",
                before="return search(status);",
                after="return search(normalize(status));",
            ),
        ),
        allowed_paths=frozenset({"Service.java"}),
    )

    assert result.modified_paths == ("Service.java",)
    assert (tmp_path / "Service.java").read_text(encoding="utf-8") == (
        "return search(normalize(status));\n"
    )


def test_safe_workspace_editor_rejects_out_of_scope_path(tmp_path: Path) -> None:
    revision = _repository(tmp_path)

    with pytest.raises(ValueError, match="outside the approved path scope"):
        SafeWorkspaceEditor().apply(
            workspace_root=tmp_path,
            base_revision=revision,
            replacements=(TextReplacement("Service.java", "status", "normalized"),),
            allowed_paths=frozenset({"Controller.java"}),
        )

    assert (tmp_path / "Service.java").read_text(encoding="utf-8") == "return search(status);\n"


def test_preedited_workspace_verifier_accepts_only_exact_approved_result(
    tmp_path: Path,
) -> None:
    revision = _repository(tmp_path)
    (tmp_path / "Service.java").write_text(
        "return search(normalize(status));\n", encoding="utf-8"
    )

    result = PreEditedWorkspaceVerifier().apply(
        workspace_root=tmp_path,
        base_revision=revision,
        replacements=(
            TextReplacement(
                path="Service.java",
                before="return search(status);",
                after="return search(normalize(status));",
            ),
        ),
        allowed_paths=frozenset({"Service.java"}),
    )

    assert result.modified_paths == ("Service.java",)


def test_preedited_workspace_verifier_rejects_extra_same_file_edit(tmp_path: Path) -> None:
    revision = _repository(tmp_path)
    (tmp_path / "Service.java").write_text(
        "// unrelated\nreturn search(normalize(status));\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="differs from the exact approved replacement"):
        PreEditedWorkspaceVerifier().apply(
            workspace_root=tmp_path,
            base_revision=revision,
            replacements=(
                TextReplacement(
                    path="Service.java",
                    before="return search(status);",
                    after="return search(normalize(status));",
                ),
            ),
            allowed_paths=frozenset({"Service.java"}),
        )
