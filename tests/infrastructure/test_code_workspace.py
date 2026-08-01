from pathlib import Path

import pytest

from operamind.infrastructure.code_graph import WorkspaceScanLimits, WorkspaceScanner
from operamind.infrastructure.code_graph import workspace as workspace_module


def test_workspace_scanner_is_bounded_deterministic_and_profile_filtered(
    tmp_path: Path,
) -> None:
    (tmp_path / "src/main/java/example").mkdir(parents=True)
    (tmp_path / "src/test/java/example").mkdir(parents=True)
    (tmp_path / "src/main/resources").mkdir(parents=True)
    (tmp_path / "src/main/generated").mkdir(parents=True)
    (tmp_path / "src/main/java/example/App.java").write_text("class App {}\n", encoding="utf-8")
    (tmp_path / "src/test/java/example/AppTest.java").write_text(
        "class AppTest {}\n", encoding="utf-8"
    )
    (tmp_path / "src/main/resources/app.properties").write_text(
        "feature.enabled=true\n", encoding="utf-8"
    )
    (tmp_path / "src/main/generated/Generated.java").write_text(
        "class Generated {}\n", encoding="utf-8"
    )
    (tmp_path / "src/main/notes.txt").write_text("ignored\n", encoding="utf-8")

    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main", "src"),
        excluded_globs=("**/generated/**", "**/target/**"),
        languages=("java", "properties"),
    )

    assert [file.path for file in files] == [
        "src/main/java/example/App.java",
        "src/main/resources/app.properties",
        "src/test/java/example/AppTest.java",
    ]
    assert [file.role for file in files] == ["production", "config", "test"]
    assert all(file.content_hash.startswith("sha256:") for file in files)
    assert files[0].content == b"class App {}\n"


def test_workspace_scanner_supports_lexical_css_and_gradle_files(tmp_path: Path) -> None:
    (tmp_path / "src/main/resources/static/css").mkdir(parents=True)
    (tmp_path / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
    (tmp_path / "src/main/resources/static/css/app.css").write_text(
        "body { overflow: hidden; }\n", encoding="utf-8"
    )

    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=(".",),
        excluded_globs=(".git/**",),
        languages=("css", "gradle"),
    )

    assert [(file.path, file.language) for file in files] == [
        ("build.gradle", "gradle"),
        ("src/main/resources/static/css/app.css", "css"),
    ]


def test_workspace_scanner_skips_optional_scan_roots_that_do_not_exist(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {}\n", encoding="utf-8")

    scanner = WorkspaceScanner()
    files = scanner.discover(
        workspace_root=tmp_path,
        scan_roots=("src/main", "src/test"),
        excluded_globs=(),
        languages=("java",),
    )
    explicit_files = scanner.discover(
        workspace_root=tmp_path,
        scan_roots=("src/main", "src/test"),
        excluded_globs=(),
        languages=("java",),
        allowed_paths=frozenset(
            {
                "src/main/java/example/App.java",
                "src/test/java/example/AppTest.java",
            }
        ),
    )

    assert [file.path for file in files] == ["src/main/java/example/App.java"]
    assert [file.path for file in explicit_files] == [
        "src/main/java/example/App.java"
    ]


@pytest.mark.parametrize(
    ("scan_roots", "excluded_globs", "message"),
    [
        (("../outside",), (), "stay within"),
        (("/tmp",), (), "stay within"),
        (("src",), ("!src/generated/**",), "positive POSIX"),
        (("src",), ("../outside/**",), "escapes workspace semantics"),
    ],
)
def test_workspace_scanner_rejects_unsafe_scope_syntax(
    tmp_path: Path,
    scan_roots: tuple[str, ...],
    excluded_globs: tuple[str, ...],
    message: str,
) -> None:
    (tmp_path / "src").mkdir()

    with pytest.raises(ValueError, match=message):
        WorkspaceScanner().discover(
            workspace_root=tmp_path,
            scan_roots=scan_roots,
            excluded_globs=excluded_globs,
            languages=("java",),
        )


def test_workspace_scanner_does_not_follow_symlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "Secret.java").write_text("class Secret {}\n", encoding="utf-8")
    (workspace / "linked-root").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        WorkspaceScanner().discover(
            workspace_root=workspace,
            scan_roots=("linked-root",),
            excluded_globs=(),
            languages=("java",),
        )

    source = workspace / "src"
    source.mkdir()
    (source / "Linked.java").symlink_to(outside / "Secret.java")
    assert (
        WorkspaceScanner().discover(
            workspace_root=workspace,
            scan_roots=("src",),
            excluded_globs=(),
            languages=("java",),
        )
        == ()
    )


def test_workspace_scanner_explicit_paths_do_not_walk_and_reject_symlink_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "Selected.java").write_text("class Selected {}\n", encoding="utf-8")
    (source / "Skipped.java").write_text("class Skipped {}\n", encoding="utf-8")

    def fail_walk(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"explicit path discovery must not call os.walk: {args}, {kwargs}")

    monkeypatch.setattr(workspace_module.os, "walk", fail_walk)
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src",),
        excluded_globs=(),
        languages=("java",),
        allowed_paths=frozenset({"src/Selected.java"}),
    )
    assert [file.path for file in files] == ["src/Selected.java"]

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="must not be a symlink"):
        WorkspaceScanner().discover(
            workspace_root=tmp_path,
            scan_roots=("linked",),
            excluded_globs=(),
            languages=("java",),
            allowed_paths=frozenset(),
        )


def test_workspace_scanner_enforces_resource_limits(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "Large.java").write_bytes(b"x" * 5)

    with pytest.raises(ValueError, match="max_file_bytes"):
        WorkspaceScanner().discover(
            workspace_root=tmp_path,
            scan_roots=("src",),
            excluded_globs=(),
            languages=("java",),
            limits=WorkspaceScanLimits(max_files=1, max_file_bytes=4, max_total_bytes=4),
        )
