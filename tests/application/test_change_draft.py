import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from operamind.application.change_draft import (
    ChangeDraftInputMode,
    ChangeDraftRequest,
    ChangeDraftService,
)
from operamind.application.change_draft_session import ChangeDraftSessionService
from operamind.commands.change_draft import build_parser
from operamind.infrastructure.draft_generation import DraftGenerationResponse

ROOT = Path(__file__).parents[2]


class FakeDraftProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompt = ""

    def generate(
        self, *, prompt: str, workspace_root: Path, output_root: Path
    ) -> DraftGenerationResponse:
        self.prompt = prompt
        output_root.mkdir(parents=True, exist_ok=True)
        response = output_root / "ai-response.json"
        response.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        stdout = output_root / "stdout.log"
        stderr = output_root / "stderr.log"
        stdout.write_text("", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        return DraftGenerationResponse(
            payload=copy.deepcopy(self.payload),
            provider_id="fake-provider",
            stdout_path=stdout,
            stderr_path=stderr,
            response_path=response,
        )


def test_change_draft_cli_exposes_both_generation_entries() -> None:
    parser = build_parser()

    documents = parser.parse_args(
        [
            "generate",
            "documents",
            "--draft-root",
            "draft",
            "--response-file",
            "copilot-response.json",
            "--target-repository",
            "target",
            "--before-document",
            "before.xlsx",
            "--after-document",
            "after.xlsx",
            "--draft-id",
            "draft-1",
            "--case-id",
            "case-1",
            "--project-id",
            "project-1",
            "--repository-id",
            "repository-1",
            "--application-root",
            "app",
            "--scan-root",
            "app/src/main",
        ]
    )
    requirement = parser.parse_args(
        [
            "generate",
            "requirement",
            "--draft-root",
            "draft",
            "--response-file",
            "copilot-response.json",
            "--target-repository",
            "target",
            "--before-document",
            "before.xlsx",
            "--requirement",
            "change the default",
            "--draft-id",
            "draft-1",
            "--case-id",
            "case-1",
            "--project-id",
            "project-1",
            "--repository-id",
            "repository-1",
            "--application-root",
            "app",
            "--scan-root",
            "app/src/main",
        ]
    )

    assert documents.entry == "documents"
    assert requirement.entry == "requirement"


def test_change_draft_cli_exposes_copilot_handoff_and_response_import() -> None:
    parser = build_parser()
    common = [
        "--target-repository",
        "target",
        "--before-document",
        "before.xlsx",
        "--after-document",
        "after.xlsx",
        "--draft-id",
        "draft-1",
        "--case-id",
        "case-1",
        "--project-id",
        "project-1",
        "--repository-id",
        "repository-1",
        "--application-root",
        "app",
        "--scan-root",
        "app/src/main",
    ]

    prepared = parser.parse_args(["prepare", "documents", "--handoff-root", "handoff", *common])
    generated = parser.parse_args(
        [
            "generate",
            "documents",
            "--draft-root",
            "draft",
            "--response-file",
            "handoff/ai-response.json",
            *common,
        ]
    )

    assert prepared.command == "prepare"
    assert prepared.handoff_root == Path("handoff")
    assert generated.response_file == Path("handoff/ai-response.json")


def test_change_draft_cli_rejects_generation_without_copilot_response() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "generate",
                "documents",
                "--draft-root",
                "draft",
                "--target-repository",
                "target",
                "--before-document",
                "before.xlsx",
                "--after-document",
                "after.xlsx",
                "--draft-id",
                "draft-1",
                "--case-id",
                "case-1",
                "--project-id",
                "project-1",
                "--repository-id",
                "repository-1",
                "--application-root",
                "app",
                "--scan-root",
                "app/src/main",
            ]
        )


def test_change_draft_cli_does_not_expose_direct_execution() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "approve",
                "--draft-root",
                "draft",
                "--case-root",
                "case",
                "--target-repository",
                "target",
                "--reviewed-by",
                "developer",
                "--execute",
            ]
        )


def test_prepare_handoff_writes_bounded_copilot_packet(tmp_path: Path) -> None:
    workspace, revision = _workspace(tmp_path)
    before = tmp_path / "02_画面設計書_before.xlsx"
    after = tmp_path / "02_画面設計書_after.xlsx"
    _write_screen_design(before, "申請中")
    _write_screen_design(after, "すべて")
    handoff = tmp_path / "copilot-handoff"

    result = ChangeDraftService(repository_root=ROOT).prepare_handoff(
        _request(
            tmp_path,
            workspace=workspace,
            before=before,
            after=after,
            mode=ChangeDraftInputMode.DOCUMENTS,
        ),
        handoff_root=handoff,
    )

    assert result.response_path == handoff / "ai-response.json"
    assert not result.response_path.exists()
    assert {path.name for path in handoff.iterdir()} == {
        "COPILOT-INSTRUCTIONS.md",
        "change-draft-ai-response.schema.json",
        "draft-prompt.json",
        "generation-context.json",
        "handoff-manifest.json",
    }
    context = json.loads(result.context_path.read_text(encoding="utf-8"))
    assert context["repository"]["base_revision"] == revision
    assert context["document_changes"]
    prompt = result.prompt_path.read_text(encoding="utf-8")
    assert "candidate_file_contents" in prompt
    assert "app/src/main/java/example/ExpenseService.java" in prompt
    assert "runtime_execution_contract" in prompt
    assert '"action_id"' in prompt
    assert '"failure_category"' in prompt
    prompt_payload = json.loads(prompt)
    runtime_contract = prompt_payload["runtime_execution_contract"]
    assert "impact_item_refs" not in runtime_contract["browser_scenario"]["exact_fields"]
    assert runtime_contract["browser_action"]["value_required_for"] == [
        "fill",
        "select_option",
    ]
    assert runtime_contract["minimal_example"]["actions"][0]["action_id"]
    manifest = json.loads((handoff / "handoff-manifest.json").read_text(encoding="utf-8"))
    assert manifest["allowed_output_files"] == ["ai-response.json"]


def test_documents_input_generates_complete_reviewable_draft(tmp_path: Path) -> None:
    workspace, revision = _workspace(tmp_path)
    before = tmp_path / "02_画面設計書_before.xlsx"
    after = tmp_path / "02_画面設計書_after.xlsx"
    _write_screen_design(before, "申請中")
    _write_screen_design(after, "すべて")
    provider = FakeDraftProvider(
        _response(
            revision=revision,
            source_document=before.name,
            document_operations=[],
            confidence={
                "document_change": "high",
                "code_scope": "medium",
                "edit_plan": "high",
                "verification_plan": "high",
            },
        )
    )

    result = ChangeDraftService(repository_root=ROOT, provider=provider).generate(
        _request(
            tmp_path,
            workspace=workspace,
            before=before,
            after=after,
            mode=ChangeDraftInputMode.DOCUMENTS,
        )
    )

    assert result.status == "awaiting_confirmation"
    assert result.session["questions"][0]["question_id"] == "confirm-code-scope"
    assert result.proposed_after_document.is_file()
    assert json.loads(result.expected_changes_path.read_text(encoding="utf-8"))["changes"][0][
        "field_deltas"
    ] == [
        {
            "field": "default_value",
            "before": "申請中",
            "after": "すべて",
            "source_ref": f"{before.name}#画面項目一覧!C2",
        }
    ]
    case = json.loads(result.case_config_path.read_text(encoding="utf-8"))
    assert case["review"]["review_status"] == "draft"
    assert case["repository"]["base_revision"] == revision
    assert "candidate_file_contents" in provider.prompt
    assert "app/src/main/java/example/ExpenseService.java" in provider.prompt


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("missing_endpoint", "runtime endpoints are absent"),
        ("control_only_ui", "downstream business result"),
        ("empty_business_result", "downstream business result"),
        ("non_executable_data", "bind an existing fixture path"),
        ("missing_nonblank_api", "no nonblank API branch"),
        ("missing_blank_api", "no blank API branch"),
    ],
)
def test_copilot_draft_rejects_non_executable_business_verification(
    tmp_path: Path, defect: str, message: str
) -> None:
    workspace, revision = _workspace(tmp_path)
    before = tmp_path / "02_画面設計書_before.xlsx"
    after = tmp_path / "02_画面設計書_after.xlsx"
    _write_screen_design(before, "申請中")
    _write_screen_design(after, "すべて")
    response = _response(
        revision=revision,
        source_document=before.name,
        document_operations=[],
        confidence={
            "document_change": "high",
            "code_scope": "high",
            "edit_plan": "high",
            "verification_plan": "high",
        },
    )
    case = response["case"]
    if defect == "missing_endpoint":
        case["execution"]["health_request"]["path"] = "/health"
    elif defect == "control_only_ui":
        case["execution"]["browser_scenarios"][0]["assertions"] = [
            {
                "assertion_id": "status-value-only",
                "kind": "value_equals",
                "locator": {"strategy": "css", "value": "#expense-status"},
                "expected": {"source": "literal", "value": ""},
                "failure_category": "business_assertion",
                }
            ]
    elif defect == "empty_business_result":
        case["execution"]["browser_scenarios"][0]["assertions"][0][
            "kind"
        ] = "text_contains"
        case["execution"]["browser_scenarios"][0]["assertions"][0]["expected"][
            "value"
        ] = ""
    elif defect == "non_executable_data":
        case["data_sets"][0]["setup_actions"] = [
            {"action": "seed_expenses", "dataset": "expense-status"}
        ]
    elif defect == "missing_nonblank_api":
        case["requirements"]["business_rules"].append(
            {
                "business_rule_id": "rule-preserve-nonblank",
                "text": "Nonblank status input must be preserved.",
                "source_refs": [f"{before.name}#画面項目一覧!C2"],
            }
        )
    else:
        case["requirements"]["business_rules"].append(
            {
                "business_rule_id": "rule-normalize-blank",
                "text": "Blank or null status input must be normalized.",
                "source_refs": [f"{before.name}#画面項目一覧!C2"],
            }
        )
        case["execution"]["api_tests"][0]["request"]["query"]["status"] = "承認済"

    with pytest.raises(ValueError, match=message):
        ChangeDraftService(
            repository_root=ROOT,
            provider=FakeDraftProvider(response),
        ).generate(
            _request(
                tmp_path,
                workspace=workspace,
                before=before,
                after=after,
                mode=ChangeDraftInputMode.DOCUMENTS,
            )
        )


def test_natural_language_input_generates_document_proposal_and_ready_draft(
    tmp_path: Path,
) -> None:
    workspace, revision = _workspace(tmp_path)
    before = tmp_path / "02_画面設計書_before.xlsx"
    _write_screen_design(before, "申請中")
    operations = [
        {
            "operation_id": "set-default-all",
            "sheet": "画面項目一覧",
            "cell": "C2",
            "field": "default_value",
            "before": "申請中",
            "after": "すべて",
            "source_ref": f"{before.name}#画面項目一覧!C2",
        }
    ]
    provider = FakeDraftProvider(
        _response(
            revision=revision,
            source_document=before.name,
            document_operations=operations,
            confidence={
                "document_change": "high",
                "code_scope": "high",
                "edit_plan": "high",
                "verification_plan": "high",
            },
        )
    )

    result = ChangeDraftService(repository_root=ROOT, provider=provider).generate(
        _request(
            tmp_path,
            workspace=workspace,
            before=before,
            after=None,
            mode=ChangeDraftInputMode.NATURAL_LANGUAGE,
        )
    )

    assert result.status == "ready_for_approval"
    assert result.session["questions"] == []
    assert result.session["steps"] == [
        {"step": "document_change", "status": "auto_confirmed"},
        {"step": "code_scope", "status": "auto_confirmed"},
        {"step": "edit_plan", "status": "auto_confirmed"},
        {"step": "verification_plan", "status": "auto_confirmed"},
    ]
    expected = json.loads(result.expected_changes_path.read_text(encoding="utf-8"))
    assert expected["expected_structured_change_count"] == 1


def test_confirmation_answer_advances_draft_to_ready_for_approval(
    tmp_path: Path,
) -> None:
    workspace, revision = _workspace(tmp_path)
    before = tmp_path / "02_画面設計書_before.xlsx"
    after = tmp_path / "02_画面設計書_after.xlsx"
    _write_screen_design(before, "申請中")
    _write_screen_design(after, "すべて")
    provider = FakeDraftProvider(
        _response(
            revision=revision,
            source_document=before.name,
            document_operations=[],
            confidence={
                "document_change": "high",
                "code_scope": "medium",
                "edit_plan": "high",
                "verification_plan": "high",
            },
        )
    )
    generated = ChangeDraftService(repository_root=ROOT, provider=provider).generate(
        _request(
            tmp_path,
            workspace=workspace,
            before=before,
            after=after,
            mode=ChangeDraftInputMode.DOCUMENTS,
        )
    )
    sessions = ChangeDraftSessionService(repository_root=ROOT)

    question = sessions.next_question(generated.session_path.parent)
    assert question is not None
    answered = sessions.answer(
        draft_root=generated.session_path.parent,
        question_id=str(question["question_id"]),
        option_id="accept",
        answered_by="developer@example.com",
    )

    assert answered.session["status"] == "ready_for_approval"
    assert answered.next_question is None
    assert answered.session["steps"][1] == {
        "step": "code_scope",
        "status": "confirmed",
    }


def test_approval_materializes_reviewed_executable_case(tmp_path: Path) -> None:
    workspace, revision = _workspace(tmp_path)
    before = tmp_path / "02_画面設計書_before.xlsx"
    _write_screen_design(before, "申請中")
    operations = [
        {
            "operation_id": "set-default-all",
            "sheet": "画面項目一覧",
            "cell": "C2",
            "field": "default_value",
            "before": "申請中",
            "after": "すべて",
            "source_ref": f"{before.name}#画面項目一覧!C2",
        }
    ]
    provider = FakeDraftProvider(
        _response(
            revision=revision,
            source_document=before.name,
            document_operations=operations,
            confidence={
                "document_change": "high",
                "code_scope": "high",
                "edit_plan": "high",
                "verification_plan": "high",
            },
        )
    )
    generated = ChangeDraftService(repository_root=ROOT, provider=provider).generate(
        _request(
            tmp_path,
            workspace=workspace,
            before=before,
            after=None,
            mode=ChangeDraftInputMode.NATURAL_LANGUAGE,
        )
    )

    final = ChangeDraftSessionService(repository_root=ROOT).approve(
        draft_root=generated.session_path.parent,
        final_case_root=tmp_path / "final-case",
        target_repository=workspace,
        reviewed_by="developer@example.com",
    )

    assert final.case.is_approved
    assert final.case.case_id == "expense-default-all-generated"
    assert (final.case_root / "fixtures/after.xlsx").is_file()
    manifest = json.loads((final.case_root / "source-manifest.json").read_text(encoding="utf-8"))
    assert manifest["review_status"] == "approved"
    session = json.loads(final.session_path.read_text(encoding="utf-8"))
    assert session["status"] == "finalized"


def _request(
    tmp_path: Path,
    *,
    workspace: Path,
    before: Path,
    after: Path | None,
    mode: ChangeDraftInputMode,
) -> ChangeDraftRequest:
    return ChangeDraftRequest(
        draft_id="draft-expense-default",
        case_id="expense-default-all-generated",
        project_id="visiondemo",
        repository_id="visiondemo-repository",
        workspace_root=workspace,
        before_document=before,
        after_document=after,
        requirement_text=(
            "経費ステータスの初期値をすべてにする"
            if mode is ChangeDraftInputMode.NATURAL_LANGUAGE
            else None
        ),
        input_mode=mode,
        application_root="app",
        scan_roots=("app/src/main", "app/src/test"),
        code_profile="profiles/code-framework-profile.example.json",
        document_profile="profiles/screen-design-convention-profile.example.json",
        output_root=tmp_path / "draft-output",
    )


def _workspace(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "workspace"
    service = workspace / "app/src/main/java/example/ExpenseService.java"
    controller = workspace / "app/src/main/java/example/ExpenseController.java"
    test = workspace / "app/src/test/java/example/ExpenseServiceTest.java"
    data = workspace / "app/src/main/resources/data.sql"
    service.parent.mkdir(parents=True)
    controller.parent.mkdir(parents=True, exist_ok=True)
    test.parent.mkdir(parents=True)
    data.parent.mkdir(parents=True)
    service.write_text(
        """package example;

import org.springframework.stereotype.Service;

@Service
public class ExpenseService {
    public String normalize(String status) {
        return status;
    }
}
""",
        encoding="utf-8",
    )
    test.write_text(
        """package example;

class ExpenseServiceTest {
}
""",
        encoding="utf-8",
    )
    controller.write_text(
        """package example;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseBody;

@Controller
@RequestMapping("/expense")
public class ExpenseController {
    @GetMapping("/api/search")
    @ResponseBody
    public String search() { return "ok"; }

    @GetMapping("/page")
    public String page() { return "expense"; }
}
""",
        encoding="utf-8",
    )
    data.write_text("-- deterministic expense fixture\n", encoding="utf-8")
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test User")
    _git(workspace, "remote", "add", "origin", "https://example.invalid/demo.git")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-q", "-m", "base")
    revision = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    return workspace, revision


def _response(
    *,
    revision: str,
    source_document: str,
    document_operations: list[dict[str, Any]],
    confidence: dict[str, str],
) -> dict[str, Any]:
    case = json.loads(
        (
            ROOT / "golden-dataset/cases/visiondemo-employee-blank-name/change-loop-case.json"
        ).read_text(encoding="utf-8")
    )
    path = "app/src/main/java/example/ExpenseService.java"
    before = """    public String normalize(String status) {
        return status;
    }
"""
    after = """    public String normalize(String status) {
        return status == null || status.isBlank() ? null : status;
    }
"""
    case["repository"]["base_revision"] = revision
    case["requirements"] = {
        "canonical_requirement": "Default the expense status to all.",
        "business_rules": [
            {
                "business_rule_id": "rule-expense-default-all",
                "text": "The expense status defaults to all.",
                "source_refs": [f"{source_document}#画面項目一覧!C2"],
            }
        ],
        "required_intents": [],
        "conflicts": [],
    }
    case["impact_candidates"] = [
        {"path": path, "symbols": ["normalize"], "reason": "Normalizes status"}
    ]
    case["edit"] = {
        "forbidden_paths": [],
        "replacements": [{"path": path, "before": before, "after": after}],
        "allowed_items": [
            {
                "path": path,
                "business_summary": "Normalize blank status.",
                "implementation_constraints": ["Preserve nonblank status."],
            }
        ],
    }
    source_test_id = "test-source-status"
    api_test_id = "test-api-status"
    ui_test_id = "test-ui-status"
    case["acceptance_criteria"] = [
        {
            "criterion_id": "criterion-source-status",
            "business_rule_refs": ["rule-expense-default-all"],
            "assertion_type": "source",
            "subject": f"{path}#normalize",
            "operator": "contains",
            "expected": "status.isBlank()",
            "test_case_refs": [source_test_id],
        },
        {
            "criterion_id": "criterion-api-status",
            "business_rule_refs": ["rule-expense-default-all"],
            "assertion_type": "api",
            "subject": "GET /expense/api/search",
            "operator": "exists",
            "expected": True,
            "test_case_refs": [api_test_id],
        },
        {
            "criterion_id": "criterion-ui-status",
            "business_rule_refs": ["rule-expense-default-all"],
            "assertion_type": "ui",
            "subject": ui_test_id,
            "operator": "exists",
            "expected": True,
            "test_case_refs": [ui_test_id],
        },
    ]
    case["test_cases"] = [
        _test_case(source_test_id, "source", "deterministic", "criterion-source-status"),
        _test_case(api_test_id, "api", "deterministic", "criterion-api-status"),
        _test_case(ui_test_id, "ui", "browser", "criterion-ui-status"),
    ]
    case["data_sets"] = [
        {
            "test_data_id": "data-expenses",
            "test_case_refs": [api_test_id, ui_test_id],
            "setup_actions": [
                {
                    "action_id": "seed-expense",
                    "action_type": "fixture",
                    "target": "app/src/main/resources/data.sql",
                    "payload": {"count": 1},
                }
            ],
            "cleanup_policy": "isolated_environment",
        }
    ]
    case["test_cases"][1]["test_data_refs"] = ["data-expenses"]
    case["test_cases"][2]["test_data_refs"] = ["data-expenses"]
    case["execution"] = {
        "source_tests": [
            {
                "test_case_id": source_test_id,
                "path": path,
                "contains": ["status.isBlank()"],
                "success_summary": "Source contains blank normalization.",
            }
        ],
        "health_request": {"method": "GET", "path": "/expense/api/search"},
        "api_tests": [
            {
                "test_case_id": api_test_id,
                "request": {
                    "method": "GET",
                    "path": "/expense/api/search",
                    "query": {"status": ""},
                },
                "assertions": [{"path": "content", "operator": "exists"}],
                "success_summary": "API returned content.",
            }
        ],
        "setup_requests": [],
        "browser_phases": [
            {"phase_id": "expense", "after_setup": False, "scenario_ids": [ui_test_id]}
        ],
        "browser_scenarios": [
            {
                "scenario_id": ui_test_id,
                "trigger_path": "/expense/page",
                "actions": [],
                "assertions": [
                    {
                        "assertion_id": "expense-result-count",
                        "kind": "count_equals",
                        "locator": {
                            "strategy": "css",
                            "value": "#expense-result-count",
                        },
                        "expected": {"source": "literal", "value": "1"},
                        "failure_category": "business_assertion",
                    }
                ],
                "redaction_locators": [],
            }
        ],
    }
    return {
        "schema_version": "v1",
        "case": case,
        "document_operations": document_operations,
        "confidence": confidence,
        "questions": [],
    }


def _test_case(test_id: str, level: str, execution_mode: str, criterion: str) -> dict[str, Any]:
    return {
        "test_case_id": test_id,
        "title": test_id,
        "level": level,
        "execution_mode": execution_mode,
        "business_rule_refs": ["rule-expense-default-all"],
        "acceptance_criteria_refs": [criterion],
        "preconditions": [],
        "steps": ["Run the configured check"],
        "expected_results": ["The configured check passes"],
        "test_data_refs": [],
    }


def _write_screen_design(path: Path, default_value: str) -> None:
    workbook = Workbook()
    overview = workbook.active
    assert overview is not None
    overview.title = "画面概要"
    overview.append(["画面ID", "SCREEN_EXPENSE_LIST"])
    items = workbook.create_sheet("画面項目一覧")
    items.append(["項目名", "種別", "初期値", "備考"])
    items.append(["expense-search-status", "セレクト", default_value, "ステータス"])
    workbook.save(path)
    workbook.close()


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
