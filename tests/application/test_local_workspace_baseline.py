import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from operamind.application import web_control_plane
from operamind.application.local_source_control import LocalSourceControlService
from operamind.application.web_control_plane import (
    ProjectInitializationInput,
    WebControlPlaneService,
    _initialize_managed_local_git_baseline,
)
from operamind.infrastructure.code_graph import GitWorkspaceInspector, GitWorktreeDiffInspector


def test_managed_local_baseline_supports_revision_bound_diffs(tmp_path: Path) -> None:
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=not-versioned\n", encoding="utf-8")

    _initialize_managed_local_git_baseline(
        workspace_root=tmp_path,
        project_id="local-project",
    )

    baseline = GitWorkspaceInspector().inspect(tmp_path)
    assert baseline.remote_url.startswith("operamind-local://")
    assert "src/service.py" in baseline.tracked_paths
    assert ".env" not in baseline.tracked_paths

    source.write_text("value = 2\n", encoding="utf-8")
    working = GitWorktreeDiffInspector().inspect_worktree(tmp_path, base_sha=baseline.head_sha)
    subprocess.run(
        ("git", "-C", str(tmp_path), "add", "src/service.py"),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "-C", str(tmp_path), "commit", "--quiet", "-m", "change"),
        check=True,
        capture_output=True,
    )
    committed = GitWorktreeDiffInspector().inspect_committed(tmp_path, base_sha=baseline.head_sha)

    assert working.content_digest == committed.content_digest


def test_document_root_reuses_enclosing_code_repository(tmp_path: Path) -> None:
    documents = tmp_path / "docs"
    documents.mkdir()
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (documents / "design.md").write_text("design\n", encoding="utf-8")
    service = LocalSourceControlService()

    code = service.ensure(
        root=tmp_path,
        project_id="project-1",
        source_kind="code",
        position=0,
        require_repository_root=True,
    )
    document = service.ensure(
        root=documents,
        project_id="project-1",
        source_kind="document",
        position=0,
    )

    assert document.repository_root == code.repository_root
    assert document.baseline_revision == code.baseline_revision
    assert not (documents / ".git").exists()


def test_independent_document_root_gets_its_own_local_baseline(tmp_path: Path) -> None:
    code = tmp_path / "code"
    documents = tmp_path / "documents"
    code.mkdir()
    documents.mkdir()
    (code / "app.py").write_text("value = 1\n", encoding="utf-8")
    (documents / "design.md").write_text("design\n", encoding="utf-8")
    service = LocalSourceControlService()

    service.ensure(
        root=code,
        project_id="project-1",
        source_kind="code",
        position=0,
        require_repository_root=True,
    )
    document = service.ensure(
        root=documents,
        project_id="project-1",
        source_kind="document",
        position=0,
    )

    assert document.repository_root == documents.resolve()
    assert document.management_kind == "operamind_local_git"
    assert (documents / ".git").is_dir()

    reused = service.ensure(
        root=documents,
        project_id="project-1",
        source_kind="document",
        position=0,
    )
    assert reused.management_kind == "operamind_local_git"


def test_failed_initialization_batch_removes_only_new_local_repositories(
    tmp_path: Path,
) -> None:
    code = tmp_path / "code"
    documents = tmp_path / "documents"
    existing = tmp_path / "existing"
    for root, name in (
        (code, "app.py"),
        (documents, "design.md"),
        (existing, "keep.py"),
    ):
        root.mkdir()
        (root / name).write_text("baseline\n", encoding="utf-8")
    service = LocalSourceControlService()
    service.ensure(
        root=existing,
        project_id="existing-project",
        source_kind="document",
        position=0,
    )
    existing_baseline = service.ensure(
        root=existing,
        project_id="existing-project",
        source_kind="document",
        position=0,
    )
    assert not existing_baseline.created_repository
    code_baseline = service.ensure(
        root=code,
        project_id="failed-project",
        source_kind="code",
        position=0,
        require_repository_root=True,
    )
    document_baseline = service.ensure(
        root=documents,
        project_id="failed-project",
        source_kind="document",
        position=0,
    )

    service.rollback_created_repositories(
        (code_baseline, document_baseline, existing_baseline)
    )

    assert not (code / ".git").exists()
    assert not (documents / ".git").exists()
    assert (existing / ".git").is_dir()


def test_project_initialization_rolls_back_local_git_when_document_baseline_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    documents = tmp_path / "documents"
    workspace.mkdir()
    documents.mkdir()
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    (documents / "design.md").write_text("design\n", encoding="utf-8")
    registered_projects: list[str] = []

    class Repository:
        def project_workspace_registration(self, _project_id: str) -> None:
            return None

        def initialize_project(self, **values: object) -> SimpleNamespace:
            registered_projects.append(str(values["project_id"]))
            return SimpleNamespace(
                created=True,
                project_id=values["project_id"],
                name=values["name"],
                workspace_root=values["workspace_root"],
                document_roots=values["document_roots"],
                source_control_kind=values["source_control_kind"],
                test_base_url=values["test_base_url"],
                source_git_baselines=values["source_git_baselines"],
            )

    class FailingDocumentBaselineService:
        def __init__(self, **_values: object) -> None:
            pass

        def ensure(self, **_values: object) -> None:
            raise RuntimeError("document indexing failed")

    class Transaction:
        def __enter__(self) -> None:
            return None

        def __exit__(self, error_type: object, *_args: object) -> None:
            if error_type is not None:
                registered_projects.clear()

    class Connection:
        def transaction(self) -> Transaction:
            return Transaction()

    monkeypatch.setattr(
        web_control_plane,
        "ProjectDocumentBaselineService",
        FailingDocumentBaselineService,
    )
    service = WebControlPlaneService.__new__(WebControlPlaneService)
    service._repository = Repository()
    service._connection = Connection()
    service._root = tmp_path

    with pytest.raises(RuntimeError, match="document indexing failed"):
        service.initialize_project(
            ProjectInitializationInput(
                project_id="failed-project",
                name="Failed Project",
                workspace_root=workspace,
                document_roots=(documents,),
                configured_by="tester",
            )
        )

    assert not (workspace / ".git").exists()
    assert not (documents / ".git").exists()
    assert registered_projects == []


def test_existing_repository_without_remote_is_not_reported_as_operamind_managed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(tmp_path), "init", "--quiet"), check=True)
    subprocess.run(
        ("git", "-C", str(tmp_path), "config", "user.name", "Test User"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"),
        check=True,
    )
    subprocess.run(("git", "-C", str(tmp_path), "add", "app.py"), check=True)
    subprocess.run(
        ("git", "-C", str(tmp_path), "commit", "--quiet", "-m", "baseline"),
        check=True,
    )

    baseline = LocalSourceControlService().ensure(
        root=tmp_path,
        project_id="existing-local-project",
        source_kind="code",
        position=0,
        require_repository_root=True,
    )

    assert baseline.management_kind == "existing_git"
    assert baseline.repository_identity.startswith("operamind-local://")


def test_nested_code_workspace_is_detected_without_creating_nested_git(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repository"
    nested = project / "application"
    nested.mkdir(parents=True)
    (nested / "app.py").write_text("value = 1\n", encoding="utf-8")
    service = LocalSourceControlService()
    service.ensure(
        root=project,
        project_id="project-1",
        source_kind="code",
        position=0,
        require_repository_root=True,
    )

    with pytest.raises(ValueError, match="上位 Git Repository"):
        service.ensure(
            root=nested,
            project_id="project-1",
            source_kind="code",
            position=0,
            require_repository_root=True,
        )

    assert not (nested / ".git").exists()


def test_existing_dirty_repository_is_not_auto_committed(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    service = LocalSourceControlService()
    service.ensure(
        root=tmp_path,
        project_id="project-1",
        source_kind="code",
        position=0,
        require_repository_root=True,
    )
    source.write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="未 Commit"):
        service.ensure(
            root=tmp_path,
            project_id="project-1",
            source_kind="code",
            position=0,
            require_repository_root=True,
        )


def test_restore_tracked_files_restores_only_requested_matching_baseline(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "selected.xlsx"
    unrelated = tmp_path / "unrelated.txt"
    selected.write_bytes(b"canonical")
    unrelated.write_text("baseline\n", encoding="utf-8")
    service = LocalSourceControlService()
    service.ensure(
        root=tmp_path,
        project_id="project-restore",
        source_kind="document",
        position=0,
        require_repository_root=True,
    )
    selected.write_bytes(b"rejected-draft")
    unrelated.write_text("keep-user-change\n", encoding="utf-8")

    restored = service.restore_tracked_files(
        paths=(selected,),
        expected_digests={selected: hashlib.sha256(b"canonical").hexdigest()},
    )

    assert restored == (selected.resolve(),)
    assert selected.read_bytes() == b"canonical"
    assert unrelated.read_text(encoding="utf-8") == "keep-user-change\n"


def test_restore_tracked_files_rejects_head_outside_canonical_baseline(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "selected.xlsx"
    selected.write_bytes(b"head-version")
    service = LocalSourceControlService()
    service.ensure(
        root=tmp_path,
        project_id="project-restore-mismatch",
        source_kind="document",
        position=0,
        require_repository_root=True,
    )
    selected.write_bytes(b"rejected-draft")

    with pytest.raises(ValueError, match="Canonical 文書基線"):
        service.restore_tracked_files(
            paths=(selected,),
            expected_digests={selected: hashlib.sha256(b"other-baseline").hexdigest()},
        )

    assert selected.read_bytes() == b"rejected-draft"


def test_existing_unborn_repository_applies_secret_exclusions_before_commit(
    tmp_path: Path,
) -> None:
    subprocess.run(("git", "-C", str(tmp_path), "init", "--quiet"), check=True)
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=not-versioned\n", encoding="utf-8")

    baseline = LocalSourceControlService().ensure(
        root=tmp_path,
        project_id="project-unborn",
        source_kind="code",
        position=0,
        require_repository_root=True,
    )

    evidence = GitWorkspaceInspector().inspect(tmp_path)
    assert evidence.head_sha == baseline.baseline_revision
    assert evidence.tracked_paths == frozenset({"app.py"})


def test_managed_baseline_excludes_common_package_manager_credentials(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".npmrc").write_text("//registry/:_authToken=secret\n", encoding="utf-8")
    (tmp_path / ".pypirc").write_text("password=secret\n", encoding="utf-8")
    (tmp_path / ".git-credentials").write_text(
        "https://user:secret@example.invalid\n", encoding="utf-8"
    )

    LocalSourceControlService().ensure(
        root=tmp_path,
        project_id="project-package-credentials",
        source_kind="code",
        position=0,
        require_repository_root=True,
    )

    assert GitWorkspaceInspector().inspect(tmp_path).tracked_paths == frozenset({"app.py"})


def test_existing_unborn_repository_rejects_forced_staged_npm_credentials(
    tmp_path: Path,
) -> None:
    subprocess.run(("git", "-C", str(tmp_path), "init", "--quiet"), check=True)
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".npmrc").write_text("//registry/:_authToken=secret\n", encoding="utf-8")
    subprocess.run(
        ("git", "-C", str(tmp_path), "add", "--force", ".npmrc"),
        check=True,
    )

    with pytest.raises(ValueError, match="秘密ファイル"):
        LocalSourceControlService().ensure(
            root=tmp_path,
            project_id="project-staged-package-credentials",
            source_kind="code",
            position=0,
            require_repository_root=True,
        )


def test_existing_unborn_repository_rejects_an_already_staged_secret(
    tmp_path: Path,
) -> None:
    subprocess.run(("git", "-C", str(tmp_path), "init", "--quiet"), check=True)
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=staged\n", encoding="utf-8")
    subprocess.run(
        ("git", "-C", str(tmp_path), "add", "--force", ".env"),
        check=True,
    )

    with pytest.raises(ValueError, match="秘密ファイル"):
        LocalSourceControlService().ensure(
            root=tmp_path,
            project_id="project-unborn",
            source_kind="code",
            position=0,
            require_repository_root=True,
        )

    assert (
        subprocess.run(
            ("git", "-C", str(tmp_path), "rev-parse", "--verify", "HEAD"),
            check=False,
            capture_output=True,
        ).returncode
        != 0
    )


def test_existing_unborn_repository_rejects_uppercase_staged_secret(
    tmp_path: Path,
) -> None:
    subprocess.run(("git", "-C", str(tmp_path), "init", "--quiet"), check=True)
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "CLIENT.PEM").write_text("SECRET=staged\n", encoding="utf-8")
    subprocess.run(
        ("git", "-C", str(tmp_path), "add", "--force", "CLIENT.PEM"),
        check=True,
    )

    with pytest.raises(ValueError, match="秘密ファイル"):
        LocalSourceControlService().ensure(
            root=tmp_path,
            project_id="project-unborn",
            source_kind="code",
            position=0,
            require_repository_root=True,
        )


def test_existing_repository_without_origin_keeps_origin_available(
    tmp_path: Path,
) -> None:
    subprocess.run(("git", "-C", str(tmp_path), "init", "--quiet"), check=True)
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(tmp_path), "add", "app.py"), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=OperaMind Test",
            "-c",
            "user.email=operamind@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ),
        check=True,
    )

    baseline = LocalSourceControlService().ensure(
        root=tmp_path,
        project_id="project-existing",
        source_kind="code",
        position=0,
        require_repository_root=True,
    )

    remotes = subprocess.run(
        ("git", "-C", str(tmp_path), "remote"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "origin" not in remotes
    assert baseline.repository_identity.startswith("operamind-local://")
    assert GitWorkspaceInspector().inspect(tmp_path).remote_url == baseline.repository_identity
