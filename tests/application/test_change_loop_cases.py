import json
import subprocess
from pathlib import Path

import pytest

from operamind.application.change_loop import ChangeInputMode
from operamind.application.change_loop_batch import (
    ChangeLoopBatchRequest,
    ChangeLoopBatchRunner,
    IsolatedGitWorktree,
    classify_blocked_error,
    classify_closure,
    classify_validation_issues,
)
from operamind.application.change_loop_case import ChangeLoopCase
from operamind.application.change_loop_catalog import (
    CaseValidationIssue,
    ChangeLoopCaseCatalog,
    DiscoveredChangeLoopCase,
    initialize_case,
)
from operamind.commands.change_cases import build_parser

ROOT = Path(__file__).parents[2]
CASES = ROOT / "golden-dataset/cases"


def test_change_cases_cli_exposes_productized_operations() -> None:
    parser = build_parser()

    for command in ("init", "validate", "plan"):
        with pytest.raises(SystemExit) as exit_info:
            parser.parse_args([command, "--help"])
        assert exit_info.value.code == 0

    with pytest.raises(SystemExit) as removed:
        parser.parse_args(["run", "--help"])
    assert removed.value.code == 2


def test_catalog_discovers_all_cases_and_reports_missing_external_documents() -> None:
    discovered = ChangeLoopCaseCatalog(repository_root=ROOT, cases_root=CASES).discover(
        require_after=True
    )

    assert {case.case_id for case in discovered} == {
        "visiondemo-employee-blank-name",
        "visiondemo-expense-status-filter-golden",
        "visiondemo-order-normalized-filters",
    }
    assert all(case.case is not None for case in discovered)
    assert all(
        "before_document_missing" in {issue.code for issue in case.issues} for case in discovered
    )
    assert {
        case.case_id
        for case in discovered
        if "after_document_missing" in {issue.code for issue in case.issues}
    } == {"visiondemo-expense-status-filter-golden"}


def test_case_initializer_clones_complete_case_as_non_executable_draft(
    tmp_path: Path,
) -> None:
    cases_root = tmp_path / "golden-dataset/cases"
    cases_root.mkdir(parents=True)
    (cases_root.parent / "change-loop-case.schema.json").write_text(
        (ROOT / "golden-dataset/change-loop-case.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    target = cases_root / "new-employee-case"

    initialized = initialize_case(
        source_case_root=CASES / "visiondemo-employee-blank-name",
        target_case_root=target,
        case_id="new-employee-case",
    )

    assert initialized.case_id == "new-employee-case"
    assert initialized.review["review_status"] == "draft"
    assert (target / "fixtures/after.xlsx").is_file()
    source = ChangeLoopCase.load(CASES / "visiondemo-employee-blank-name")
    assert source.case_id == "visiondemo-employee-blank-name"
    with pytest.raises(ValueError, match="Only approved"):
        ChangeLoopCase.load(target)


def test_catalog_detects_manifest_identity_mismatch(tmp_path: Path) -> None:
    cases_root = tmp_path / "golden-dataset/cases"
    cases_root.mkdir(parents=True)
    (cases_root.parent / "change-loop-case.schema.json").write_text(
        (ROOT / "golden-dataset/change-loop-case.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    target = cases_root / "tampered"
    initialize_case(
        source_case_root=CASES / "visiondemo-employee-blank-name",
        target_case_root=target,
        case_id="tampered",
    )
    config = json.loads((target / "change-loop-case.json").read_text(encoding="utf-8"))
    config["review"]["review_status"] = "approved"
    (target / "change-loop-case.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )
    manifest = json.loads((target / "source-manifest.json").read_text(encoding="utf-8"))
    manifest["case_id"] = "wrong-case"
    (target / "source-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    discovered = ChangeLoopCaseCatalog(repository_root=ROOT, cases_root=cases_root).discover()

    assert len(discovered) == 1
    assert "manifest_identity_mismatch" in {issue.code for issue in discovered[0].issues}


def test_failure_classification_has_four_explicit_outcomes() -> None:
    assert classify_blocked_error("Requirement needs confirmation") == "needs_confirmation"
    assert classify_blocked_error("Workspace revision mismatch") == "reanalysis_required"
    assert classify_validation_issues(("before_document_missing",)) == "environment_failed"
    assert classify_closure({"status": "failed", "unresolved_items": []}) == "business_failed"


def test_batch_runner_writes_schema_valid_failure_summary(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    discovered = DiscoveredChangeLoopCase(
        case_root=tmp_path / "case",
        case_id="draft-case",
        case=None,
        source_manifest=None,
        before_document=None,
        after_document=None,
        issues=(CaseValidationIssue("approval_required", "Approval is required"),),
    )

    result = ChangeLoopBatchRunner(repository_root=ROOT).run(
        (discovered,),
        ChangeLoopBatchRequest(
            target_repository=target,
            output_root=tmp_path / "output",
            input_mode=ChangeInputMode.DOCUMENTS,
        ),
    )

    assert not result.successful
    assert result.report["summary"] == {"needs_confirmation": 1}
    assert json.loads(result.report_path.read_text(encoding="utf-8")) == result.report


def test_batch_execution_is_not_an_authorized_execution_entry(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Batch execution is disabled"):
        ChangeLoopBatchRequest(
            target_repository=tmp_path,
            output_root=tmp_path / "output",
            input_mode=ChangeInputMode.DOCUMENTS,
            execute=True,
        )


def test_isolated_git_worktree_is_removed_after_use(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-q", "-m", "base")
    revision = _git(repository, "rev-parse", "HEAD").stdout.strip()
    worktree = tmp_path / "worktree"

    with IsolatedGitWorktree(repository=repository, path=worktree, revision=revision) as isolated:
        assert (isolated / "tracked.txt").read_text(encoding="utf-8") == "base\n"

    assert not worktree.exists()
    assert str(worktree) not in _git(repository, "worktree", "list").stdout


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
