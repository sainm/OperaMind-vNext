import subprocess
from pathlib import Path

import pytest

from operamind.infrastructure.code_graph import GitWorkspaceInspector, GitWorktreeDiffInspector


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def initialized_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "remote", "add", "origin", "https://example.invalid/example.git")
    source = repository / "src/main/java/example"
    source.mkdir(parents=True)
    (source / "App.java").write_text("class App {}\n", encoding="utf-8")
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=OperaMind Test",
        "-c",
        "user.email=operamind@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    return repository, git(repository, "rev-parse", "HEAD")


def test_git_workspace_inspector_binds_clean_head_remote_and_tracked_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, head_sha = initialized_repository(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker-controlled"))

    evidence = GitWorkspaceInspector().inspect(repository)

    assert evidence.workspace_root == repository.resolve()
    assert evidence.head_sha == head_sha
    assert evidence.remote_url == "https://example.invalid/example.git"
    assert evidence.tracked_paths == frozenset({"src/main/java/example/App.java"})


def test_git_workspace_inspector_rejects_dirty_or_nested_worktree(tmp_path: Path) -> None:
    repository, _ = initialized_repository(tmp_path)
    (repository / "untracked.txt").write_text("not revision-bound\n", encoding="utf-8")

    with pytest.raises(ValueError, match="clean Git worktree"):
        GitWorkspaceInspector().inspect(repository)

    (repository / "untracked.txt").unlink()
    with pytest.raises(ValueError, match="Git repository root"):
        GitWorkspaceInspector().inspect(repository / "src")


def test_git_diff_inspector_covers_untracked_and_committed_rename(tmp_path: Path) -> None:
    repository, base_sha = initialized_repository(tmp_path)
    source = repository / "src/main/java/example/App.java"
    source.write_text("class App { int value; }\n", encoding="utf-8")
    untracked = repository / "src/main/java/example/New.java"
    untracked.write_text("class New {}\n", encoding="utf-8")

    working = GitWorktreeDiffInspector().inspect_worktree(repository, base_sha=base_sha)

    assert working.result_sha is None
    assert working.changed_paths == (
        "src/main/java/example/App.java",
        "src/main/java/example/New.java",
    )

    source.rename(repository / "src/main/java/example/RenamedApp.java")
    git(repository, "add", "-A")
    git(
        repository,
        "-c",
        "user.name=OperaMind Test",
        "-c",
        "user.email=operamind@example.invalid",
        "commit",
        "-q",
        "-m",
        "edit",
    )
    committed = GitWorktreeDiffInspector().inspect_committed(repository, base_sha=base_sha)

    assert committed.result_sha == git(repository, "rev-parse", "HEAD")
    assert committed.changed_paths == (
        "src/main/java/example/App.java",
        "src/main/java/example/New.java",
        "src/main/java/example/RenamedApp.java",
    )


def test_git_common_repository_dir_accepts_only_shared_linked_worktrees(
    tmp_path: Path,
) -> None:
    repository, _ = initialized_repository(tmp_path)
    linked_worktree = tmp_path / "linked-worktree"
    git(repository, "worktree", "add", "--detach", str(linked_worktree), "HEAD")
    unrelated_parent = tmp_path / "unrelated-parent"
    unrelated_parent.mkdir()
    unrelated, _ = initialized_repository(unrelated_parent)
    inspector = GitWorktreeDiffInspector()

    registered_common_dir = inspector.common_repository_dir(repository)

    assert inspector.common_repository_dir(linked_worktree) == registered_common_dir
    assert inspector.common_repository_dir(unrelated) != registered_common_dir
    assert inspector.linked_worktree_roots(linked_worktree) == tuple(
        sorted((repository.resolve(), linked_worktree.resolve()), key=str)
    )
