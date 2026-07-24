"""Discover, initialize and validate productized change-loop cases."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from operamind.application.change_loop_case import ChangeLoopCase


@dataclass(frozen=True, slots=True)
class CaseValidationIssue:
    """One actionable reason a discovered case is not ready."""

    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"code": self.code, "message": self.message}
        if self.path is not None:
            value["path"] = self.path
        return value


@dataclass(frozen=True, slots=True)
class DiscoveredChangeLoopCase:
    """A case plus resolved documents and validation evidence."""

    case_root: Path
    case_id: str
    case: ChangeLoopCase | None
    source_manifest: dict[str, Any] | None
    before_document: Path | None
    after_document: Path | None
    issues: tuple[CaseValidationIssue, ...]

    @property
    def ready(self) -> bool:
        return self.case is not None and self.case.is_approved and not self.issues

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "case_root": str(self.case_root),
            "review_status": (
                str(self.case.review.get("review_status")) if self.case is not None else None
            ),
            "before_document": (
                str(self.before_document) if self.before_document is not None else None
            ),
            "after_document": (
                str(self.after_document) if self.after_document is not None else None
            ),
            "status": "ready" if self.ready else "invalid",
            "issues": [issue.to_dict() for issue in self.issues],
        }


class ChangeLoopCaseCatalog:
    """Locate cases and validate cross-file, document and repository references."""

    def __init__(self, *, repository_root: Path, cases_root: Path) -> None:
        self._repository_root = repository_root.resolve(strict=True)
        self._cases_root = cases_root.resolve(strict=True)
        self._schema_path = self._cases_root.parent / "change-loop-case.schema.json"

    def discover(
        self,
        *,
        before_roots: tuple[Path, ...] = (),
        after_roots: tuple[Path, ...] = (),
        target_repository: Path | None = None,
        case_ids: frozenset[str] = frozenset(),
        require_after: bool = True,
    ) -> tuple[DiscoveredChangeLoopCase, ...]:
        roots = sorted(path.parent for path in self._cases_root.glob("*/change-loop-case.json"))
        discovered = tuple(
            self._inspect(
                root,
                before_roots=before_roots,
                after_roots=after_roots,
                target_repository=target_repository,
                require_after=require_after,
            )
            for root in roots
            if not case_ids or root.name in case_ids
        )
        found = {value.case_id for value in discovered}
        missing = sorted(case_ids - found)
        if missing:
            raise ValueError(f"Unknown change-loop case IDs: {missing}")
        return discovered

    def _inspect(
        self,
        root: Path,
        *,
        before_roots: tuple[Path, ...],
        after_roots: tuple[Path, ...],
        target_repository: Path | None,
        require_after: bool,
    ) -> DiscoveredChangeLoopCase:
        issues: list[CaseValidationIssue] = []
        try:
            case = ChangeLoopCase.load(
                root,
                require_approved=False,
                schema_path=self._schema_path,
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            return DiscoveredChangeLoopCase(
                case_root=root,
                case_id=root.name,
                case=None,
                source_manifest=None,
                before_document=None,
                after_document=None,
                issues=(CaseValidationIssue("case_invalid", str(error)),),
            )

        if not case.is_approved:
            issues.append(
                CaseValidationIssue(
                    "approval_required",
                    "Case review_status must be approved before planning or execution",
                    "change-loop-case.json",
                )
            )
        if not case.canonical_requirement:
            issues.append(
                CaseValidationIssue(
                    "canonical_requirement_missing",
                    "Natural-language batch planning requires canonical_requirement",
                    "change-loop-case.json",
                )
            )
        for relative in (
            "expected-changes.json",
            "source-manifest.json",
        ):
            if not (root / relative).is_file():
                issues.append(
                    CaseValidationIssue(
                        "case_file_missing", f"Required case file is missing: {relative}", relative
                    )
                )
        for profile_key, relative in (
            ("document.convention_profile", str(case.document["convention_profile"])),
            (
                "repository.code_profile",
                str(
                    case.repository.get(
                        "code_profile", "profiles/code-framework-profile.example.json"
                    )
                ),
            ),
        ):
            if not (self._repository_root / relative).is_file():
                issues.append(
                    CaseValidationIssue(
                        "profile_missing",
                        f"Referenced profile does not exist: {relative}",
                        profile_key,
                    )
                )

        manifest = _load_optional_object(root / "source-manifest.json", issues)
        before: Path | None = None
        after: Path | None = None
        if manifest is not None:
            _validate_manifest_identity(case, manifest, issues)
            sources = cast(dict[str, Any], manifest.get("document_sources", {}))
            changed_file = str(sources.get("changed_file", ""))
            if not changed_file:
                issues.append(
                    CaseValidationIssue(
                        "document_name_missing",
                        "source-manifest document_sources.changed_file is required",
                        "source-manifest.json",
                    )
                )
            else:
                before = _resolve_document(
                    name=changed_file,
                    digest=str(sources.get("before_sha256", "")),
                    roots=before_roots,
                    issue_prefix="before",
                    issues=issues,
                )
                fixture = sources.get("after_fixture")
                if fixture is not None:
                    fixture_path = (root / str(fixture)).resolve()
                    if not fixture_path.is_relative_to(root.resolve()):
                        issues.append(
                            CaseValidationIssue(
                                "after_document_unsafe",
                                "after_fixture escapes the case directory",
                                "source-manifest.json",
                            )
                        )
                    else:
                        after = _validate_document_candidate(
                            fixture_path,
                            str(sources.get("after_sha256", "")),
                            "after",
                            issues,
                        )
                elif require_after:
                    after = _resolve_document(
                        name=changed_file,
                        digest=str(sources.get("after_sha256", "")),
                        roots=after_roots,
                        issue_prefix="after",
                        issues=issues,
                    )

        if target_repository is not None:
            _validate_repository_references(case, target_repository, issues)

        return DiscoveredChangeLoopCase(
            case_root=root,
            case_id=case.case_id,
            case=case,
            source_manifest=manifest,
            before_document=before,
            after_document=after,
            issues=tuple(issues),
        )


def initialize_case(
    *,
    source_case_root: Path,
    target_case_root: Path,
    case_id: str,
    project_id: str | None = None,
) -> ChangeLoopCase:
    """Clone a complete case as a non-executable draft for safe customization."""

    if not case_id.strip() or "/" in case_id or "\\" in case_id or case_id in {".", ".."}:
        raise ValueError("case_id must be a non-blank directory-safe identifier")
    source = source_case_root.resolve(strict=True)
    schema_path = next(
        (
            parent / "change-loop-case.schema.json"
            for parent in (source, *source.parents)
            if (parent / "change-loop-case.schema.json").is_file()
        ),
        None,
    )
    if schema_path is None:
        raise FileNotFoundError(f"change-loop-case.schema.json was not found above {source}")
    target = target_case_root.absolute()
    if target.exists():
        raise FileExistsError(f"Target case already exists: {target}")
    shutil.copytree(source, target)
    try:
        config_path = target / "change-loop-case.json"
        config = _load_object(config_path)
        config["case_id"] = case_id
        if project_id is not None:
            config["project_id"] = project_id
        config["review"] = {
            "review_status": "draft",
            "reviewed_by": "pending-review",
            "reviewed_at": "1970-01-01T00:00:00Z",
        }
        _write_object(config_path, config)

        manifest_path = target / "source-manifest.json"
        if manifest_path.is_file():
            manifest = _load_object(manifest_path)
            manifest["case_id"] = case_id
            if project_id is not None:
                manifest["project_id"] = project_id
            manifest["dataset_stage"] = "draft"
            manifest["review_status"] = "draft"
            manifest["generated_by"] = "operamind-change-cases-init-v1"
            _write_object(manifest_path, manifest)
        return ChangeLoopCase.load(target, require_approved=False, schema_path=schema_path)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _load_optional_object(path: Path, issues: list[CaseValidationIssue]) -> dict[str, Any] | None:
    try:
        return _load_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        issues.append(CaseValidationIssue("source_manifest_invalid", str(error), path.name))
        return None


def _load_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _write_object(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_manifest_identity(
    case: ChangeLoopCase,
    manifest: dict[str, Any],
    issues: list[CaseValidationIssue],
) -> None:
    if manifest.get("review_status") != "approved":
        issues.append(
            CaseValidationIssue(
                "manifest_approval_required",
                "source-manifest review_status must be approved",
                "source-manifest.json",
            )
        )
    for field, expected in (("case_id", case.case_id), ("project_id", case.project_id)):
        actual = str(manifest.get(field, ""))
        if actual != expected:
            issues.append(
                CaseValidationIssue(
                    "manifest_identity_mismatch",
                    f"source-manifest {field} differs: expected={expected} actual={actual}",
                    "source-manifest.json",
                )
            )
    target = cast(dict[str, Any], manifest.get("target_repository", {}))
    actual_revision = str(target.get("base_commit", ""))
    expected_revision = str(case.repository["base_revision"])
    if actual_revision != expected_revision:
        issues.append(
            CaseValidationIssue(
                "manifest_revision_mismatch",
                "source-manifest base_commit differs from change-loop-case base_revision",
                "source-manifest.json",
            )
        )


def _resolve_document(
    *,
    name: str,
    digest: str,
    roots: tuple[Path, ...],
    issue_prefix: str,
    issues: list[CaseValidationIssue],
) -> Path | None:
    candidates: list[Path] = []
    for raw_root in roots:
        root = raw_root.expanduser().resolve()
        direct = root / name
        if direct.is_file():
            candidates.append(direct)
        elif root.is_dir():
            candidates.extend(path for path in root.rglob(name) if path.is_file())
    valid = [path for path in dict.fromkeys(candidates) if _sha256(path) == digest]
    if len(valid) == 1:
        return valid[0]
    if not valid:
        issues.append(
            CaseValidationIssue(
                f"{issue_prefix}_document_missing",
                f"No {issue_prefix} document matches name and SHA-256: {name}",
            )
        )
    else:
        issues.append(
            CaseValidationIssue(
                f"{issue_prefix}_document_ambiguous",
                f"Multiple {issue_prefix} documents match name and SHA-256: {name}",
            )
        )
    return None


def _validate_document_candidate(
    path: Path,
    digest: str,
    issue_prefix: str,
    issues: list[CaseValidationIssue],
) -> Path | None:
    if not path.is_file():
        issues.append(
            CaseValidationIssue(
                f"{issue_prefix}_document_missing", f"Document fixture is missing: {path}"
            )
        )
        return None
    actual = _sha256(path)
    if actual != digest:
        issues.append(
            CaseValidationIssue(
                f"{issue_prefix}_document_digest_mismatch",
                f"Document SHA-256 differs: expected={digest} actual={actual}",
                str(path),
            )
        )
        return None
    return path


def _validate_repository_references(
    case: ChangeLoopCase,
    target_repository: Path,
    issues: list[CaseValidationIssue],
) -> None:
    root = target_repository.expanduser().resolve()
    revision = str(case.repository["base_revision"])
    if not root.is_dir():
        issues.append(
            CaseValidationIssue("repository_missing", f"Target repository does not exist: {root}")
        )
        return
    if not _git_object_exists(root, f"{revision}^{{commit}}"):
        issues.append(
            CaseValidationIssue(
                "repository_revision_missing",
                f"Target repository does not contain base revision: {revision}",
            )
        )
        return
    paths = {
        *(str(value["path"]) for value in case.impact_candidates),
        *(
            str(value["path"])
            for value in cast(list[dict[str, Any]], case.execution["source_tests"])
        ),
    }
    for relative in sorted(paths):
        if not _git_object_exists(root, f"{revision}:{relative}"):
            issues.append(
                CaseValidationIssue(
                    "repository_path_missing",
                    f"Referenced path does not exist at the configured revision: {relative}",
                    relative,
                )
            )


def _git_object_exists(root: Path, object_name: str) -> bool:
    try:
        result = subprocess.run(
            ("git", "-C", str(root), "cat-file", "-e", object_name),
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
