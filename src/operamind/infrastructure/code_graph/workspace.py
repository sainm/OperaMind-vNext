"""Bounded, deterministic source-file discovery inside an approved workspace."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath

import pathspec
from pathspec.pattern import Pattern


@dataclass(frozen=True, slots=True)
class WorkspaceScanLimits:
    """Hard resource ceilings applied before parser work starts."""

    max_files: int = 100_000
    max_file_bytes: int = 5 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024

    def __post_init__(self) -> None:
        if not 1 <= self.max_files <= 1_000_000:
            raise ValueError("max_files must be between 1 and 1000000")
        if not 1 <= self.max_file_bytes <= 100 * 1024 * 1024:
            raise ValueError("max_file_bytes must be between 1 and 104857600")
        if not self.max_file_bytes <= self.max_total_bytes <= 10 * 1024 * 1024 * 1024:
            raise ValueError("max_total_bytes must cover one file and be at most 10 GiB")


@dataclass(frozen=True, slots=True)
class DiscoveredCodeFile:
    """One immutable-in-memory scan input; content is never a database field."""

    path: str
    language: str
    role: str
    content_hash: str
    content: bytes


class WorkspaceScanner:
    """Discover Profile-supported regular files without following symlinks."""

    def discover(
        self,
        *,
        workspace_root: Path,
        scan_roots: tuple[str, ...],
        excluded_globs: tuple[str, ...],
        languages: tuple[str, ...],
        limits: WorkspaceScanLimits | None = None,
        allowed_paths: frozenset[str] | None = None,
    ) -> tuple[DiscoveredCodeFile, ...]:
        """Return a stable path-ordered file set confined to the resolved workspace."""

        root = workspace_root.resolve(strict=True)
        limits = limits or WorkspaceScanLimits()
        if not root.is_dir():
            raise ValueError("workspace_root must be a directory")
        if not scan_roots or len(scan_roots) != len(set(scan_roots)):
            raise ValueError("scan_roots must be non-empty and unique")
        if not languages or any(not language.strip() for language in languages):
            raise ValueError("languages must be non-empty and non-blank")
        normalized_languages = frozenset(language.casefold() for language in languages)
        unsupported = sorted(normalized_languages - frozenset(_EXTENSIONS_BY_LANGUAGE))
        if unsupported:
            raise ValueError(f"Unsupported Code Graph languages: {unsupported}")

        patterns = tuple(_validate_excluded_glob(value) for value in excluded_globs)
        exclusions = pathspec.GitIgnoreSpec.from_lines(patterns)
        resolved_scan_roots = tuple(
            (_validate_relative_path(value, field_name="scan root"), value) for value in scan_roots
        )
        if allowed_paths is not None:
            return self._discover_allowed_paths(
                root=root,
                resolved_scan_roots=resolved_scan_roots,
                exclusions=exclusions,
                normalized_languages=normalized_languages,
                limits=limits,
                allowed_paths=allowed_paths,
            )
        discovered: dict[str, DiscoveredCodeFile] = {}
        total_bytes = 0
        for relative_root, original_root in resolved_scan_roots:
            scan_root_path = root / relative_root
            if scan_root_path.is_symlink():
                raise ValueError(f"Scan root must not be a symlink: {original_root}")
            if not scan_root_path.exists():
                # Framework profiles describe the roots that may contain code.  A
                # repository is not required to materialize every optional root
                # (for example, src/test before its first generated test).
                continue
            absolute_root = scan_root_path.resolve(strict=True)
            if not absolute_root.is_relative_to(root):
                raise ValueError(f"Scan root escapes workspace: {original_root}")
            if not absolute_root.is_dir():
                raise ValueError(f"Scan root is not a directory: {original_root}")
            for directory, directory_names, file_names in os.walk(
                absolute_root,
                topdown=True,
                followlinks=False,
            ):
                directory_path = Path(directory)
                directory_names[:] = sorted(
                    name
                    for name in directory_names
                    if self._include_directory(
                        root=root,
                        directory=directory_path / name,
                        exclusions=exclusions,
                    )
                )
                for file_name in sorted(file_names):
                    file_path = directory_path / file_name
                    if file_path.is_symlink() or not file_path.is_file():
                        continue
                    resolved_file = file_path.resolve(strict=True)
                    if not resolved_file.is_relative_to(root):
                        raise ValueError(f"Discovered file escapes workspace: {file_path}")
                    relative_file = resolved_file.relative_to(root).as_posix()
                    if exclusions.match_file(relative_file):
                        continue
                    if allowed_paths is not None and relative_file not in allowed_paths:
                        continue
                    language = _language_for_path(relative_file)
                    if language is None or language not in normalized_languages:
                        continue
                    size = resolved_file.stat().st_size
                    if size > limits.max_file_bytes:
                        raise ValueError(
                            f"Code file exceeds max_file_bytes: {relative_file} ({size})"
                        )
                    if len(discovered) >= limits.max_files and relative_file not in discovered:
                        raise ValueError("Workspace scan exceeds max_files")
                    if relative_file in discovered:
                        continue
                    content = resolved_file.read_bytes()
                    if len(content) != size:
                        raise ValueError(f"Code file changed during scan: {relative_file}")
                    total_bytes += len(content)
                    if total_bytes > limits.max_total_bytes:
                        raise ValueError("Workspace scan exceeds max_total_bytes")
                    discovered[relative_file] = DiscoveredCodeFile(
                        path=relative_file,
                        language=language,
                        role=_role_for_path(relative_file, language=language),
                        content_hash=f"sha256:{sha256(content).hexdigest()}",
                        content=content,
                    )
        return tuple(discovered[path] for path in sorted(discovered))

    @staticmethod
    def _discover_allowed_paths(
        *,
        root: Path,
        resolved_scan_roots: tuple[tuple[PurePosixPath, str], ...],
        exclusions: pathspec.PathSpec[Pattern],
        normalized_languages: frozenset[str],
        limits: WorkspaceScanLimits,
        allowed_paths: frozenset[str],
    ) -> tuple[DiscoveredCodeFile, ...]:
        """Read only explicit tracked paths; do not walk a large repository tree."""

        roots: list[PurePosixPath] = []
        for relative_root, original_root in resolved_scan_roots:
            scan_root_path = root / relative_root
            if scan_root_path.is_symlink():
                raise ValueError(f"Scan root must not be a symlink: {original_root}")
            if not scan_root_path.exists():
                continue
            absolute_root = scan_root_path.resolve(strict=True)
            if not absolute_root.is_relative_to(root):
                raise ValueError(f"Scan root escapes workspace: {original_root}")
            if not absolute_root.is_dir():
                raise ValueError(f"Scan root is not a directory: {original_root}")
            roots.append(relative_root)
        discovered: list[DiscoveredCodeFile] = []
        total_bytes = 0
        for raw_path in sorted(allowed_paths):
            relative = _validate_relative_path(raw_path, field_name="allowed path")
            if not any(
                relative == scan_root or relative.is_relative_to(scan_root) for scan_root in roots
            ):
                continue
            relative_path = relative.as_posix()
            if exclusions.match_file(relative_path):
                continue
            language = _language_for_path(relative_path)
            if language is None or language not in normalized_languages:
                continue
            file_path = root / relative
            if file_path.is_symlink() or not file_path.is_file():
                continue
            resolved_file = file_path.resolve(strict=True)
            if not resolved_file.is_relative_to(root):
                raise ValueError(f"Discovered file escapes workspace: {relative_path}")
            size = resolved_file.stat().st_size
            if size > limits.max_file_bytes:
                raise ValueError(f"Code file exceeds max_file_bytes: {relative_path} ({size})")
            if len(discovered) >= limits.max_files:
                raise ValueError("Workspace scan exceeds max_files")
            content = resolved_file.read_bytes()
            if len(content) != size:
                raise ValueError(f"Code file changed during scan: {relative_path}")
            total_bytes += len(content)
            if total_bytes > limits.max_total_bytes:
                raise ValueError("Workspace scan exceeds max_total_bytes")
            discovered.append(
                DiscoveredCodeFile(
                    path=relative_path,
                    language=language,
                    role=_role_for_path(relative_path, language=language),
                    content_hash=f"sha256:{sha256(content).hexdigest()}",
                    content=content,
                )
            )
        return tuple(discovered)

    @staticmethod
    def _include_directory(
        *,
        root: Path,
        directory: Path,
        exclusions: pathspec.PathSpec[Pattern],
    ) -> bool:
        if directory.is_symlink():
            return False
        resolved = directory.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError(f"Discovered directory escapes workspace: {directory}")
        relative = resolved.relative_to(root).as_posix()
        return not exclusions.match_file(f"{relative}/")


_EXTENSIONS_BY_LANGUAGE = {
    "css": (".css",),
    "gradle": (".gradle",),
    "java": (".java",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "json": (".json",),
    "kotlin": (".kt", ".kts"),
    "properties": (".properties",),
    "python": (".py",),
    "scala": (".scala",),
    "shell": (".sh",),
    "sql": (".sql",),
    "typescript": (".ts", ".tsx", ".mts", ".cts"),
    "xml": (".xml", ".xsd", ".xsl", ".xslt", ".html", ".jsp", ".tag"),
    "yaml": (".yaml", ".yml"),
}


def _validate_relative_path(value: str, *, field_name: str) -> PurePosixPath:
    if not value.strip() or "\\" in value:
        raise ValueError(f"{field_name} must be a non-blank POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_name} must stay within the workspace: {value}")
    return path


def _validate_excluded_glob(value: str) -> str:
    if not value.strip() or value.startswith("!") or "\\" in value:
        raise ValueError("excluded_globs must be non-blank positive POSIX patterns")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Excluded glob escapes workspace semantics: {value}")
    return value


def _language_for_path(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix.casefold()
    for language, extensions in _EXTENSIONS_BY_LANGUAGE.items():
        if suffix in extensions:
            return language
    return None


def _role_for_path(path: str, *, language: str) -> str:
    pure = PurePosixPath(path)
    folded_parts = tuple(part.casefold() for part in pure.parts)
    folded_name = pure.name.casefold()
    test_name_markers = (
        folded_name.startswith("test_"),
        ".test." in folded_name,
        ".spec." in folded_name,
        folded_name.endswith(("test.java", "test.kt", "test.kts", "_test.py")),
    )
    if "test" in folded_parts or "tests" in folded_parts or any(test_name_markers):
        return "test"
    if "migration" in folded_parts or "migrations" in folded_parts:
        return "migration"
    if "contract" in folded_parts or "contracts" in folded_parts:
        return "contract"
    if language == "shell":
        return "script"
    if language in {"properties", "xml", "yaml", "json"}:
        return "config"
    return "production"
