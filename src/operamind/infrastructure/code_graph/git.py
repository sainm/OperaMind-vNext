"""Read-only Git evidence for revision-bound Code Graph scans."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GitRevisionEvidence:
    """Exact local repository evidence captured before scanning."""

    workspace_root: Path
    head_sha: str
    remote_url: str
    tracked_paths: frozenset[str]


class GitWorkspaceInspector:
    """Reject mutable or mismatched worktrees before reading source files."""

    def common_repository_dir(self, workspace_root: Path) -> Path:
        """Return the shared Git directory for a repository root or linked worktree."""

        root = self._repository_root(workspace_root)
        common_dir = Path(self._run(root, "rev-parse", "--git-common-dir"))
        if not common_dir.is_absolute():
            common_dir = root / common_dir
        return common_dir.resolve(strict=True)

    def linked_worktree_roots(self, workspace_root: Path) -> tuple[Path, ...]:
        """Return all existing worktree roots sharing the repository's common Git dir."""

        root = self._repository_root(workspace_root)
        roots: list[Path] = []
        for line in self._run(root, "worktree", "list", "--porcelain").splitlines():
            if not line.startswith("worktree "):
                continue
            candidate = Path(line.removeprefix("worktree ")).resolve(strict=True)
            if not candidate.is_dir():
                raise ValueError("Git worktree root must be a directory")
            roots.append(candidate)
        if root not in roots:
            raise ValueError("Current Git worktree is absent from the common worktree list")
        return tuple(sorted(set(roots), key=str))

    def inspect(self, workspace_root: Path) -> GitRevisionEvidence:
        """Return HEAD/remote/tracked paths only for a clean repository root."""

        root = self._repository_root(workspace_root)
        head_sha = self._run(root, "rev-parse", "--verify", "HEAD")
        if not head_sha.strip():
            raise ValueError("Git HEAD must not be blank")
        status = self._run(root, "status", "--porcelain=v1", "--untracked-files=all")
        if status:
            raise ValueError("Code Graph scan requires a clean Git worktree")
        remote_url = self._run(root, "remote", "get-url", "origin")
        if not remote_url.strip():
            raise ValueError("Git origin URL must not be blank")
        tracked_output = self._run_bytes(
            root,
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            "HEAD",
        )
        tracked_paths = frozenset(
            value.decode("utf-8", errors="strict")
            for value in tracked_output.split(b"\x00")
            if value
        )
        if not tracked_paths:
            raise ValueError("Git revision contains no tracked files")
        return GitRevisionEvidence(
            workspace_root=root,
            head_sha=head_sha,
            remote_url=remote_url,
            tracked_paths=tracked_paths,
        )

    def _repository_root(self, workspace_root: Path) -> Path:
        root = workspace_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("workspace_root must be a directory")
        actual_root = Path(self._run(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
        if actual_root != root:
            raise ValueError("workspace_root must be the Git repository root")
        return root

    def _run(self, root: Path, *arguments: str) -> str:
        return self._run_bytes(root, *arguments).decode("utf-8", errors="strict").strip()

    @staticmethod
    def _run_bytes(root: Path, *arguments: str) -> bytes:
        environment = os.environ.copy()
        for name in (
            "GIT_COMMON_DIR",
            "GIT_DIR",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_WORK_TREE",
        ):
            environment.pop(name, None)
        try:
            result = subprocess.run(
                ("git", "-C", str(root), *arguments),
                check=False,
                capture_output=True,
                timeout=15,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"Git inspection failed: {type(error).__name__}") from error
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"Git inspection failed: {message or 'unknown git error'}")
        return result.stdout


@dataclass(frozen=True, slots=True)
class GitPathChange:
    """One path-level Git change without diff content."""

    status: str
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GitDiffEvidence:
    """Path-only evidence for an editing or committed worktree."""

    workspace_root: Path
    base_sha: str
    result_sha: str | None
    remote_url: str
    changes: tuple[GitPathChange, ...]

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(sorted({path for change in self.changes for path in change.paths}))


class GitWorktreeDiffInspector(GitWorkspaceInspector):
    """Inspect changed paths while retaining Git environment hardening."""

    def inspect_worktree(self, workspace_root: Path, *, base_sha: str) -> GitDiffEvidence:
        root, head_sha, remote_url = self._identity(workspace_root)
        if head_sha != base_sha:
            raise ValueError("Working tree HEAD no longer matches Edit Packet Base Revision")
        tracked = _parse_name_status(self._run_bytes(root, "diff", "--name-status", "-z", "HEAD"))
        untracked = tuple(
            GitPathChange("A", (path,))
            for path in _parse_nul_paths(
                self._run_bytes(root, "ls-files", "--others", "--exclude-standard", "-z")
            )
        )
        return GitDiffEvidence(root, base_sha, None, remote_url, (*tracked, *untracked))

    def inspect_committed(self, workspace_root: Path, *, base_sha: str) -> GitDiffEvidence:
        root, head_sha, remote_url = self._identity(workspace_root)
        if head_sha == base_sha:
            raise ValueError("Committed Edit Result requires a new HEAD")
        if self._run(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ValueError("Committed Edit Result requires a clean Git worktree")
        self._run(root, "merge-base", "--is-ancestor", base_sha, head_sha)
        changes = _parse_name_status(
            self._run_bytes(
                root,
                "diff",
                "--name-status",
                "-z",
                "--find-renames",
                base_sha,
                head_sha,
            )
        )
        return GitDiffEvidence(root, base_sha, head_sha, remote_url, changes)

    def _identity(self, workspace_root: Path) -> tuple[Path, str, str]:
        root = self._repository_root(workspace_root)
        return (
            root,
            self._run(root, "rev-parse", "--verify", "HEAD"),
            self._run(root, "remote", "get-url", "origin"),
        )


def _parse_nul_paths(value: bytes) -> tuple[str, ...]:
    return tuple(item.decode("utf-8", errors="strict") for item in value.split(b"\x00") if item)


def _parse_name_status(value: bytes) -> tuple[GitPathChange, ...]:
    fields = _parse_nul_paths(value)
    changes: list[GitPathChange] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        path_count = 2 if status.startswith(("R", "C")) else 1
        if index + path_count > len(fields):
            raise ValueError("Git name-status output is incomplete")
        paths = fields[index : index + path_count]
        index += path_count
        changes.append(GitPathChange(status, paths))
    return tuple(changes)
