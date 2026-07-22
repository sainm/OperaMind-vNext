import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from operamind.application import (
    CanonicalExecutionBinding,
    ChangeInputMode,
    ChangeLoopBlockedError,
    ChangeLoopExecutionRequest,
    ChangeLoopExecutor,
    ChangeLoopPlan,
    ChangeLoopPlanner,
    ChangeLoopPlanRequest,
)
from operamind.application.change_loop_case import ChangeLoopCase
from operamind.commands.change_loop import build_parser

ROOT = Path(__file__).parents[2]


def test_change_loop_cli_exposes_two_primary_entries_and_hybrid_check() -> None:
    parser = build_parser()

    for entry in ("documents", "requirement", "hybrid"):
        with pytest.raises(SystemExit) as exit_info:
            parser.parse_args([entry, "--help"])
        assert exit_info.value.code == 0


def test_change_loop_cli_does_not_expose_monolithic_direct_execution() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["documents", "--execute"])


def test_execution_fails_closed_without_canonical_authorizer(tmp_path: Path) -> None:
    output = tmp_path / "execution"

    with pytest.raises(ChangeLoopBlockedError, match="Canonical RAG/Impact/Grant"):
        ChangeLoopExecutor(repository_root=ROOT).execute(
            cast(ChangeLoopPlan, object()),
            ChangeLoopExecutionRequest(output_root=output),
        )

    assert not output.exists()


def test_legacy_monolithic_runtime_stays_disabled_after_authorization(tmp_path: Path) -> None:
    class Authorizer:
        def authorize(self, *, plan: ChangeLoopPlan) -> CanonicalExecutionBinding:
            return CanonicalExecutionBinding(
                project_id="project-1",
                analysis_case_id="case-1",
                context_package_id="context-1",
                code_graph_snapshot_id="graph-1",
                impact_report_id="impact-1",
                confirmation_id="confirmation-1",
                edit_packet_id="packet-1",
                approval_grant_id="grant-1",
                base_revision="a" * 40,
            )

    output = tmp_path / "execution"
    with pytest.raises(ChangeLoopBlockedError, match="Monolithic P6 runtime is retired"):
        ChangeLoopExecutor(
            repository_root=ROOT,
            canonical_authorizer=Authorizer(),
        ).execute(
            cast(ChangeLoopPlan, object()),
            ChangeLoopExecutionRequest(output_root=output),
        )

    assert not output.exists()


def test_execution_rejects_operamind_direct_edit_origin(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only accepts VS Code GitHub Copilot"):
        ChangeLoopExecutionRequest(
            output_root=tmp_path,
            edit_origin="operamind_exact_replacement",
        )


def test_natural_language_ambiguity_blocks_before_document_or_code_changes(
    tmp_path: Path,
) -> None:
    request = ChangeLoopPlanRequest(
        change_request_id="ambiguous-change",
        project_id="visiondemo",
        case_root=ROOT / "golden-dataset/cases/visiondemo-expense-status-filter-golden",
        workspace_root=tmp_path,
        before_document=tmp_path / "missing-before.xlsx",
        input_mode=ChangeInputMode.NATURAL_LANGUAGE,
        requirement_text="ステータスを変更してください",
        proposal_document=tmp_path / "proposal.xlsx",
    )

    with pytest.raises(ChangeLoopBlockedError, match="needs confirmation"):
        ChangeLoopPlanner(repository_root=ROOT).plan(request)

    assert not request.proposal_document.exists()


def test_natural_language_conflict_requires_confirmation(tmp_path: Path) -> None:
    request = ChangeLoopPlanRequest(
        change_request_id="conflicting-change",
        project_id="visiondemo",
        case_root=ROOT / "golden-dataset/cases/visiondemo-expense-status-filter-golden",
        workspace_root=tmp_path,
        before_document=tmp_path / "missing-before.xlsx",
        input_mode=ChangeInputMode.NATURAL_LANGUAGE,
        requirement_text="初期値はすべて、差戻しを追加し、空の場合は0件を返す",
        proposal_document=tmp_path / "proposal.xlsx",
    )

    with pytest.raises(ChangeLoopBlockedError, match="conflicts"):
        ChangeLoopPlanner(repository_root=ROOT).plan(request)

    assert not request.proposal_document.exists()


def test_all_executable_cases_are_loaded_from_reviewed_configuration() -> None:
    roots = sorted(
        path.parent for path in (ROOT / "golden-dataset/cases").glob("*/change-loop-case.json")
    )

    cases = [ChangeLoopCase.load(root) for root in roots]

    assert {case.case_id for case in cases} == {
        "visiondemo-employee-blank-name",
        "visiondemo-expense-status-filter-golden",
        "visiondemo-order-normalized-filters",
    }
    assert all(case.replacements for case in cases)
    assert all(case.review["review_status"] == "approved" for case in cases)


def test_multiple_structured_changes_are_written_to_distinct_files(tmp_path: Path) -> None:
    plan = ChangeLoopPlan(  # type: ignore[arg-type]
        request=None,
        case=None,
        git=None,
        document_diff=None,
        artifacts=(
            {"artifact_type": "StructuredChange", "change_id": "one"},
            {"artifact_type": "StructuredChange", "change_id": "two"},
        ),
        replacements=(),
        allowed_edit_paths=frozenset(),
        forbidden_paths=frozenset(),
    )

    paths = plan.write_artifacts(tmp_path)

    assert [path.name for path in paths] == [
        "structured-change-1.json",
        "structured-change-2.json",
    ]
    assert [path.read_text(encoding="utf-8") for path in paths] != [
        paths[0].read_text(encoding="utf-8"),
        paths[0].read_text(encoding="utf-8"),
    ]


@pytest.mark.parametrize(
    ("case_name", "requirement", "message"),
    [
        (
            "visiondemo-employee-blank-name",
            "社員の氏名検索を変更する",
            "missing whitespace condition",
        ),
        (
            "visiondemo-order-normalized-filters",
            "発注検索で空白は0件、仕入先の空白を保持する",
            "conflicts",
        ),
    ],
)
def test_configured_ambiguity_and_conflicts_require_confirmation(
    tmp_path: Path, case_name: str, requirement: str, message: str
) -> None:
    request = ChangeLoopPlanRequest(
        change_request_id=f"blocked-{case_name}",
        project_id="visiondemo",
        case_root=ROOT / "golden-dataset/cases" / case_name,
        workspace_root=tmp_path,
        before_document=tmp_path / "missing-before.xlsx",
        input_mode=ChangeInputMode.NATURAL_LANGUAGE,
        requirement_text=requirement,
        proposal_document=tmp_path / "proposal.xlsx",
    )

    with pytest.raises(ChangeLoopBlockedError, match=message):
        ChangeLoopPlanner(repository_root=ROOT).plan(request)

    assert not request.proposal_document.exists()


def test_case_configuration_cannot_expand_edit_scope_silently(tmp_path: Path) -> None:
    source_root = ROOT / "golden-dataset/cases/visiondemo-employee-blank-name"
    case_root = tmp_path / "cases" / "tampered-case"
    case_root.mkdir(parents=True)
    payload = json.loads((source_root / "change-loop-case.json").read_text(encoding="utf-8"))
    payload["edit"]["replacements"][0]["path"] = "VisionDemo/pom.xml"
    (case_root / "change-loop-case.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "change-loop-case.schema.json").write_text(
        (ROOT / "golden-dataset/change-loop-case.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not an impact candidate"):
        ChangeLoopCase.load(case_root)


def test_case_configuration_rejects_duplicate_setup_ids() -> None:
    case_root = ROOT / "golden-dataset/cases/visiondemo-expense-status-filter-golden"
    payload = json.loads((case_root / "change-loop-case.json").read_text(encoding="utf-8"))
    setup = deepcopy(payload["execution"]["setup_requests"][0])
    payload["execution"]["setup_requests"].append(setup)

    with pytest.raises(ValueError, match="setup IDs must be unique"):
        ChangeLoopCase.from_payload(root=case_root, payload=payload)
