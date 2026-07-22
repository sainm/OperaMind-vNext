"""Preimage-checked, path-confined local workspace editing."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath

from operamind.infrastructure.code_graph.git import GitWorktreeDiffInspector


@dataclass(frozen=True, slots=True)
class TextReplacement:
    path: str
    before: str
    after: str

    def __post_init__(self) -> None:
        pure = PurePosixPath(self.path)
        if not self.path.strip() or pure.is_absolute() or ".." in pure.parts or "\\" in self.path:
            raise ValueError(f"Workspace edit path is unsafe: {self.path}")
        if not self.before or self.before == self.after:
            raise ValueError("Workspace edit must replace non-empty text with a different value")


@dataclass(frozen=True, slots=True)
class WorkspaceEditResult:
    base_revision: str
    modified_paths: tuple[str, ...]
    before_digests: tuple[tuple[str, str], ...]
    after_digests: tuple[tuple[str, str], ...]


class SafeWorkspaceEditor:
    """Apply exact replacements and fail closed on any out-of-scope Git change."""

    def __init__(self, diff_inspector: GitWorktreeDiffInspector | None = None) -> None:
        self._diff = diff_inspector or GitWorktreeDiffInspector()

    def apply(
        self,
        *,
        workspace_root: Path,
        base_revision: str,
        replacements: tuple[TextReplacement, ...],
        allowed_paths: frozenset[str],
        forbidden_paths: frozenset[str] = frozenset(),
    ) -> WorkspaceEditResult:
        root = workspace_root.resolve(strict=True)
        if not replacements:
            raise ValueError("Workspace edit requires at least one replacement")
        paths = [replacement.path for replacement in replacements]
        if len(paths) != len(set(paths)):
            raise ValueError("Workspace edit currently allows one replacement per path")
        if not set(paths).issubset(allowed_paths) or set(paths) & forbidden_paths:
            raise ValueError("Workspace edit replacement is outside the approved path scope")

        original: dict[str, str] = {}
        before_digests: list[tuple[str, str]] = []
        try:
            for replacement in replacements:
                path = (root / replacement.path).resolve(strict=True)
                if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
                    raise ValueError(f"Workspace edit target is unsafe: {replacement.path}")
                content = path.read_text(encoding="utf-8")
                if content.count(replacement.before) != 1:
                    raise ValueError(
                        f"Workspace edit preimage must occur exactly once: {replacement.path}"
                    )
                original[replacement.path] = content
                before_digests.append((replacement.path, _text_digest(content)))
                path.write_text(
                    content.replace(replacement.before, replacement.after), encoding="utf-8"
                )

            evidence = self._diff.inspect_worktree(root, base_sha=base_revision)
            changed = frozenset(evidence.changed_paths)
            if changed != frozenset(paths) or not changed.issubset(allowed_paths):
                raise ValueError(
                    "Workspace changed paths differ from the approved replacements: "
                    f"expected={sorted(paths)} actual={sorted(changed)}"
                )
            if changed & forbidden_paths:
                raise ValueError("Workspace contains a forbidden changed path")
        except Exception:
            for relative, content in original.items():
                (root / relative).write_text(content, encoding="utf-8")
            raise

        return WorkspaceEditResult(
            base_revision=base_revision,
            modified_paths=tuple(sorted(paths)),
            before_digests=tuple(sorted(before_digests)),
            after_digests=tuple(
                sorted(
                    (path, _text_digest((root / path).read_text(encoding="utf-8")))
                    for path in paths
                )
            ),
        )


class PreEditedWorkspaceVerifier(SafeWorkspaceEditor):
    """Accept an external editor only when it produced the exact approved result."""

    def apply(
        self,
        *,
        workspace_root: Path,
        base_revision: str,
        replacements: tuple[TextReplacement, ...],
        allowed_paths: frozenset[str],
        forbidden_paths: frozenset[str] = frozenset(),
    ) -> WorkspaceEditResult:
        root = workspace_root.resolve(strict=True)
        if re.fullmatch(r"[0-9a-f]{40}", base_revision) is None:
            raise ValueError("Pre-edited workspace requires a full Git commit SHA")
        if not replacements:
            raise ValueError("Pre-edited workspace requires at least one replacement")
        paths = [replacement.path for replacement in replacements]
        if len(paths) != len(set(paths)):
            raise ValueError("Pre-edited workspace allows one replacement per path")
        if not set(paths).issubset(allowed_paths) or set(paths) & forbidden_paths:
            raise ValueError("Pre-edited workspace is outside the approved path scope")

        evidence = self._diff.inspect_worktree(root, base_sha=base_revision)
        changed = frozenset(evidence.changed_paths)
        if changed != frozenset(paths) or not changed.issubset(allowed_paths):
            raise ValueError(
                "Pre-edited workspace paths differ from the approved replacements: "
                f"expected={sorted(paths)} actual={sorted(changed)}"
            )
        if changed & forbidden_paths:
            raise ValueError("Pre-edited workspace contains a forbidden changed path")

        before_digests: list[tuple[str, str]] = []
        after_digests: list[tuple[str, str]] = []
        for replacement in replacements:
            path = (root / replacement.path).resolve(strict=True)
            if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
                raise ValueError(f"Pre-edited workspace target is unsafe: {replacement.path}")
            base_content = _git_text(root, base_revision, replacement.path)
            if base_content.count(replacement.before) != 1:
                raise ValueError(
                    f"Approved preimage is not unique at the base revision: {replacement.path}"
                )
            expected = base_content.replace(replacement.before, replacement.after)
            actual = path.read_text(encoding="utf-8")
            if actual != expected:
                raise ValueError(
                    "External editor result differs from the exact approved replacement: "
                    f"{replacement.path}"
                )
            before_digests.append((replacement.path, _text_digest(base_content)))
            after_digests.append((replacement.path, _text_digest(actual)))
        return WorkspaceEditResult(
            base_revision=base_revision,
            modified_paths=tuple(sorted(paths)),
            before_digests=tuple(sorted(before_digests)),
            after_digests=tuple(sorted(after_digests)),
        )


def _git_text(root: Path, revision: str, relative_path: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), "show", f"{revision}:{relative_path}"),
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"Approved source does not exist at the base revision: {relative_path}")
    try:
        return result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"Approved source is not UTF-8 text: {relative_path}") from error


def _text_digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()
