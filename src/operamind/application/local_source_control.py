"""Detect and establish revision-bound Git baselines for local project sources."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from operamind.infrastructure.code_graph import GitWorkspaceInspector

_LOCAL_GIT_EXCLUDES = (
    ".env",
    ".env.*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "credentials",
    "credentials.*",
    "secrets.*",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    ".venv/",
    "node_modules/",
    "build/",
    "dist/",
    "target/",
    "__pycache__/",
    ".DS_Store",
    "~$*",
)


@dataclass(frozen=True, slots=True)
class LocalSourceBaseline:
    """One configured source root bound to an exact Git repository revision."""

    source_kind: str
    configured_root: Path
    repository_root: Path
    repository_identity: str
    baseline_revision: str
    management_kind: str
    position: int
    created_repository: bool = False


class LocalSourceControlService:
    """Reuse an enclosing Git repository or create a safe local-only baseline."""

    def __init__(self) -> None:
        self._git = shutil.which("git")
        if self._git is None:
            raise ValueError("ローカルソースの基線管理には Git が必要です")

    def ensure(
        self,
        *,
        root: Path,
        project_id: str,
        source_kind: str,
        position: int,
        require_repository_root: bool = False,
    ) -> LocalSourceBaseline:
        configured_root = root.resolve(strict=True)
        repository_root = self._discover_repository_root(configured_root)
        created = repository_root is None
        if repository_root is None:
            repository_root = configured_root
            self._initialize_repository(repository_root)
        elif require_repository_root and repository_root != configured_root:
            raise ValueError(
                "コード Workspace は既に上位 Git Repository に含まれています。"
                f"Repository Root を指定してください: {repository_root}"
            )

        try:
            self._ensure_head(repository_root)
            self._ensure_repository_identity(
                repository_root=repository_root,
                project_id=project_id,
            )
            evidence = GitWorkspaceInspector().inspect(repository_root)
        except Exception:
            if created:
                shutil.rmtree(repository_root / ".git", ignore_errors=True)
            raise
        return LocalSourceBaseline(
            source_kind=source_kind,
            configured_root=configured_root,
            repository_root=repository_root,
            repository_identity=evidence.remote_url,
            baseline_revision=evidence.head_sha,
            management_kind=(
                "operamind_local_git"
                if created or self._is_managed_repository(repository_root)
                else "existing_git"
            ),
            position=position,
            created_repository=created,
        )

    def rollback_created_repositories(
        self,
        baselines: tuple[LocalSourceBaseline, ...],
    ) -> None:
        """Remove only repositories created by the current failed initialization batch."""

        rolled_back: set[Path] = set()
        for baseline in reversed(baselines):
            repository_root = baseline.repository_root
            if not baseline.created_repository or repository_root in rolled_back:
                continue
            if not self._is_managed_repository(repository_root):
                raise RuntimeError(
                    "初期化失敗後の Git 基線が OperaMind 管理状態ではありません"
                )
            head = self._run(repository_root, "rev-parse", "--verify", "HEAD")
            if head.stdout.decode("utf-8", errors="strict").strip() != baseline.baseline_revision:
                raise RuntimeError("初期化失敗後の Git 基線 Revision が変更されています")
            status = self._run(
                repository_root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
            if status.stdout:
                raise RuntimeError("初期化失敗後の Git 基線に追加変更があります")
            git_directory = repository_root / ".git"
            if not git_directory.is_dir():
                raise RuntimeError("初期化失敗後の OperaMind Git Directory がありません")
            shutil.rmtree(git_directory)
            rolled_back.add(repository_root)

    def restore_tracked_files(
        self,
        *,
        paths: tuple[Path, ...],
        expected_digests: dict[Path, str],
    ) -> tuple[Path, ...]:
        """Restore bounded worktree files only when HEAD matches the expected baseline."""

        if not paths or len(paths) != len(set(paths)):
            raise ValueError("復元対象ファイルは空ではなく一意である必要があります")
        resolved = tuple(path.resolve(strict=True) for path in paths)
        expected = {path.resolve(strict=True): digest for path, digest in expected_digests.items()}
        if set(resolved) != set(expected) or any(not value.strip() for value in expected.values()):
            raise ValueError("復元対象と期待する Canonical Digest が一致しません")

        repositories: dict[Path, list[tuple[Path, str]]] = {}
        for path in resolved:
            repository_root = self._discover_repository_root(path.parent)
            if repository_root is None:
                raise ValueError(f"復元対象が Git Repository に含まれていません: {path}")
            relative = path.relative_to(repository_root).as_posix()
            baseline = self._run(repository_root, "show", f"HEAD:{relative}")
            if hashlib.sha256(baseline.stdout).hexdigest() != expected[path]:
                raise ValueError(
                    f"Git HEAD が Canonical 文書基線と一致しないため復元できません: {path}"
                )
            staged = self._run(
                repository_root,
                "diff",
                "--cached",
                "--quiet",
                "--",
                relative,
                check=False,
            )
            if staged.returncode != 0:
                raise ValueError(f"復元対象に Staged 変更があります: {path}")
            repositories.setdefault(repository_root, []).append((path, relative))

        for repository_root, entries in repositories.items():
            self._run(
                repository_root,
                "restore",
                "--source=HEAD",
                "--worktree",
                "--",
                *(relative for _path, relative in entries),
            )
        for path in resolved:
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected[path]:
                raise RuntimeError(f"Git 復元後の文書が Canonical 基線と一致しません: {path}")
        return resolved

    def _discover_repository_root(self, root: Path) -> Path | None:
        result = self._run(root, "rev-parse", "--show-toplevel", check=False)
        if result.returncode != 0:
            return None
        value = result.stdout.decode("utf-8", errors="strict").strip()
        if not value:
            return None
        repository_root = Path(value).resolve(strict=True)
        if root != repository_root and not root.is_relative_to(repository_root):
            raise ValueError("Git Repository Root が設定フォルダーを包含していません")
        return repository_root

    def _initialize_repository(self, root: Path) -> None:
        if (root / ".git").exists():
            raise ValueError("設定フォルダーに未認識の .git が存在します")
        self._run(root, "init", "--quiet")
        self._run(root, "config", "--local", "operamind.managedBaseline", "true")
        self._prepare_unborn_repository(root)

    def _is_managed_repository(self, repository_root: Path) -> bool:
        value = self._run(
            repository_root,
            "config",
            "--local",
            "--get",
            "operamind.managedBaseline",
            check=False,
        )
        return value.returncode == 0 and value.stdout.decode().strip().lower() == "true"

    def _prepare_unborn_repository(self, root: Path) -> None:
        if not self._run(
            root, "config", "--local", "--get", "user.name", check=False
        ).stdout.strip():
            self._run(root, "config", "--local", "user.name", "OperaMind Local Baseline")
        if not self._run(
            root, "config", "--local", "--get", "user.email", check=False
        ).stdout.strip():
            self._run(
                root,
                "config",
                "--local",
                "user.email",
                "local-baseline@operamind.invalid",
            )
        if not self._run(
            root, "config", "--local", "--get", "core.autocrlf", check=False
        ).stdout.strip():
            self._run(root, "config", "--local", "core.autocrlf", "false")
        exclude = root / ".git" / "info" / "exclude"
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        additions = [value for value in _LOCAL_GIT_EXCLUDES if value not in existing.splitlines()]
        if additions:
            prefix = "" if not existing or existing.endswith("\n") else "\n"
            exclude.write_text(
                existing
                + prefix
                + "# OperaMind local-only baseline exclusions\n"
                + "\n".join(additions)
                + "\n",
                encoding="utf-8",
            )

    def _ensure_head(self, repository_root: Path) -> None:
        head = self._run(
            repository_root,
            "rev-parse",
            "--verify",
            "HEAD",
            check=False,
        )
        if head.returncode == 0 and head.stdout.strip():
            status = self._run(
                repository_root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ).stdout
            if status:
                raise ValueError(
                    "既存 Git Repository に未 Commit の変更があります。"
                    "Project 初期化前に確認して Commit してください"
                )
            return
        self._prepare_unborn_repository(repository_root)
        candidates = _parse_nul_paths(
            self._run(
                repository_root,
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ).stdout
        )
        unsafe = sorted(value for value in candidates if _is_sensitive_baseline_path(value))
        if unsafe:
            raise ValueError(
                "Git 初回基線に秘密ファイルが含まれています。除外してから再実行してください: "
                f"{unsafe}"
            )
        self._run(repository_root, "add", "--all")
        tracked = self._run(repository_root, "ls-files", "-z").stdout
        if not tracked:
            raise ValueError("設定フォルダーに基線化できるファイルがありません")
        self._run(repository_root, "commit", "--quiet", "-m", "OperaMind local baseline")

    def _ensure_repository_identity(self, *, repository_root: Path, project_id: str) -> None:
        configured = self._run(
            repository_root,
            "config",
            "--local",
            "--get",
            "operamind.repositoryIdentity",
            check=False,
        )
        if configured.returncode == 0 and configured.stdout.strip():
            return
        current = self._run(
            repository_root,
            "remote",
            "get-url",
            "origin",
            check=False,
        )
        if current.returncode == 0 and current.stdout.strip():
            return
        remote_identity = hashlib.sha256(f"{project_id}\0{repository_root}".encode()).hexdigest()[
            :24
        ]
        self._run(
            repository_root,
            "config",
            "--local",
            "operamind.repositoryIdentity",
            f"operamind-local://{remote_identity}",
        )

    def _run(
        self,
        root: Path,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
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
                (str(self._git), "-C", str(root), *arguments),
                check=False,
                capture_output=True,
                timeout=60,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"Git 基線操作に失敗しました: {type(error).__name__}") from error
        if check and result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"Git 基線操作に失敗しました: {message or ' '.join(arguments)}")
        return result


def _parse_nul_paths(value: bytes) -> tuple[str, ...]:
    return tuple(item.decode("utf-8", errors="strict") for item in value.split(b"\0") if item)


def _is_sensitive_baseline_path(value: str) -> bool:
    path = PurePosixPath(value)
    name = path.name.casefold()
    return any(
        fnmatch(name, pattern.casefold())
        for pattern in _LOCAL_GIT_EXCLUDES
        if not pattern.endswith("/") and pattern not in {".DS_Store", "~$*"}
    )


__all__ = ["LocalSourceBaseline", "LocalSourceControlService"]
