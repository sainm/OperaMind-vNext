"""Isolated multi-case planning and execution with one summary report."""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from operamind.application.change_loop import (
    ChangeInputMode,
    ChangeLoopBlockedError,
    ChangeLoopPlanner,
    ChangeLoopPlanRequest,
)
from operamind.application.change_loop_catalog import DiscoveredChangeLoopCase


@dataclass(frozen=True, slots=True)
class ChangeLoopBatchRequest:
    """Settings shared by every isolated case run."""

    target_repository: Path
    output_root: Path
    input_mode: ChangeInputMode
    execute: bool = False
    keep_workspaces: bool = False

    def __post_init__(self) -> None:
        if self.input_mode not in {
            ChangeInputMode.DOCUMENTS,
            ChangeInputMode.NATURAL_LANGUAGE,
        }:
            raise ValueError("Batch change loops support documents or natural_language")
        if self.execute:
            raise ValueError(
                "Batch execution is disabled; execute each Case through "
                "Canonical Grant authorization"
            )


@dataclass(frozen=True, slots=True)
class ChangeLoopBatchResult:
    report: dict[str, Any]
    report_path: Path

    @property
    def successful(self) -> bool:
        return bool(self.report["status"] == "passed")


class ChangeLoopBatchRunner:
    """Plan cases in detached worktrees at their frozen revisions."""

    def __init__(self, *, repository_root: Path) -> None:
        self._root = repository_root.resolve(strict=True)

    def run(
        self,
        cases: tuple[DiscoveredChangeLoopCase, ...],
        request: ChangeLoopBatchRequest,
    ) -> ChangeLoopBatchResult:
        output = request.output_root.absolute()
        output.mkdir(parents=True, exist_ok=True)
        target = request.target_repository.resolve(strict=True)
        results: list[dict[str, Any]] = []
        for discovered in cases:
            results.append(self._run_case(discovered, request, target, output))

        successful_statuses = {"planned"}
        counts: dict[str, int] = {}
        for result in results:
            status = str(result["status"])
            counts[status] = counts.get(status, 0) + 1
        report: dict[str, Any] = {
            "schema_version": "v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "input_mode": request.input_mode.value,
            "operation": "plan",
            "status": (
                "passed"
                if results and all(str(value["status"]) in successful_statuses for value in results)
                else "failed"
            ),
            "case_count": len(results),
            "summary": counts,
            "results": results,
        }
        self._validate_report(report)
        report_path = output / "change-loop-batch-report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return ChangeLoopBatchResult(report=report, report_path=report_path)

    def _run_case(
        self,
        discovered: DiscoveredChangeLoopCase,
        request: ChangeLoopBatchRequest,
        target_repository: Path,
        output: Path,
    ) -> dict[str, Any]:
        case_output = output / "cases" / discovered.case_id
        case_output.mkdir(parents=True, exist_ok=True)
        relevant_issues = [
            issue
            for issue in discovered.issues
            if not (
                request.input_mode is ChangeInputMode.NATURAL_LANGUAGE
                and issue.code.startswith("after_document_")
            )
        ]
        if discovered.case is None or relevant_issues:
            message = "; ".join(issue.message for issue in relevant_issues) or "Invalid case"
            return _failure_result(
                discovered.case_id,
                classify_validation_issues(tuple(issue.code for issue in relevant_issues)),
                message,
                case_output,
            )
        case = discovered.case
        if discovered.before_document is None:
            return _failure_result(
                case.case_id,
                "environment_failed",
                "Before document was not resolved",
                case_output,
            )
        if request.input_mode is ChangeInputMode.DOCUMENTS and discovered.after_document is None:
            return _failure_result(
                case.case_id,
                "environment_failed",
                "After document was not resolved",
                case_output,
            )

        workspace_path = output / "workspaces" / case.case_id
        try:
            with IsolatedGitWorktree(
                repository=target_repository,
                path=workspace_path,
                revision=str(case.repository["base_revision"]),
                keep=request.keep_workspaces,
            ) as workspace:
                proposal = (
                    case_output / "document-proposal" / discovered.before_document.name
                    if request.input_mode is ChangeInputMode.NATURAL_LANGUAGE
                    else None
                )
                plan_request = ChangeLoopPlanRequest(
                    change_request_id=f"batch-{case.case_id}",
                    project_id=case.project_id,
                    case_root=case.root,
                    workspace_root=workspace,
                    before_document=discovered.before_document,
                    input_mode=request.input_mode,
                    after_document=(
                        discovered.after_document
                        if request.input_mode is ChangeInputMode.DOCUMENTS
                        else None
                    ),
                    requirement_text=(
                        case.canonical_requirement
                        if request.input_mode is ChangeInputMode.NATURAL_LANGUAGE
                        else None
                    ),
                    proposal_document=proposal,
                )
                plan = ChangeLoopPlanner(repository_root=self._root).plan(plan_request)
                paths = plan.write_artifacts(case_output / "artifacts")
                return {
                    "case_id": case.case_id,
                    "status": "planned",
                    "failure_class": None,
                    "message": "Plan artifacts generated in an isolated worktree",
                    "output": str(case_output),
                    "artifact_count": len(paths),
                    "closure_result": None,
                }
        except ChangeLoopBlockedError as error:
            failure_class = classify_blocked_error(str(error))
            return _failure_result(case.case_id, failure_class, str(error), case_output)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            return _failure_result(
                case.case_id,
                "environment_failed",
                f"{type(error).__name__}: {error}",
                case_output,
            )
        except (ValueError, KeyError, TypeError) as error:
            return _failure_result(
                case.case_id,
                "reanalysis_required",
                f"{type(error).__name__}: {error}",
                case_output,
            )

    def _validate_report(self, report: dict[str, Any]) -> None:
        schema_path = self._root / "golden-dataset/change-loop-batch-report.schema.json"
        schema: object = json.loads(schema_path.read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise ValueError(f"Expected JSON Schema object: {schema_path}")
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "$"
            raise ValueError(f"Invalid change-loop batch report at {location}: {first.message}")


class IsolatedGitWorktree(AbstractContextManager[Path]):
    """Create and reliably remove one detached worktree for a case."""

    def __init__(self, *, repository: Path, path: Path, revision: str, keep: bool = False) -> None:
        self._repository = repository
        self._path = path
        self._revision = revision
        self._keep = keep
        self._created = False

    def __enter__(self) -> Path:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            raise FileExistsError(f"Isolated worktree path already exists: {self._path}")
        _run_git(
            self._repository,
            "worktree",
            "add",
            "--detach",
            str(self._path),
            self._revision,
        )
        self._created = True
        return self._path.resolve(strict=True)

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._created and not self._keep:
            _run_git(
                self._repository,
                "worktree",
                "remove",
                "--force",
                str(self._path),
            )


def classify_validation_issues(codes: tuple[str, ...]) -> str:
    if any(code in {"approval_required", "manifest_approval_required"} for code in codes):
        return "needs_confirmation"
    if any(code.startswith(("before_document_", "after_document_")) for code in codes):
        return "environment_failed"
    if any(code == "repository_missing" for code in codes):
        return "environment_failed"
    return "reanalysis_required"


def classify_blocked_error(message: str) -> str:
    normalized = message.casefold()
    if any(value in normalized for value in ("confirmation", "ambigu", "conflict")):
        return "needs_confirmation"
    return "reanalysis_required"


def classify_closure(closure: dict[str, Any]) -> str:
    if closure.get("status") == "reanalysis_required":
        return "reanalysis_required"
    unresolved = [
        str(value).casefold() for value in cast(list[object], closure.get("unresolved_items", []))
    ]
    if any(value.startswith("runtime:") for value in unresolved):
        return "environment_failed"
    return "business_failed"


def _failure_result(case_id: str, failure_class: str, message: str, output: Path) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "status": failure_class,
        "failure_class": failure_class,
        "message": message,
        "output": str(output),
        "artifact_count": 0,
        "closure_result": None,
    }


def _run_git(root: Path, *arguments: str) -> None:
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
            capture_output=True,
            check=False,
            timeout=60,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Git worktree operation failed: {type(error).__name__}") from error
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Git worktree operation failed: {message or 'unknown git error'}")
