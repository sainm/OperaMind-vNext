"""Natural-language Test Case changes and immutable downstream regeneration."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from operamind.application.change_orchestration import ChangeOrchestrationResult
from operamind.application.data_identity import DEFAULT_DATA_IDENTITY_PROVIDER_TYPES
from operamind.application.test_data_flow import validate_test_data_plan_artifact
from operamind.contracts import ContractCatalog

_QUOTED = r"[「『“\"](?P<{name}>.+?)[」』”\"]"
_CASE_PATTERNS = (
    re.compile(r"(?:テスト)?ケース" + _QUOTED.format(name="case"), re.IGNORECASE),
    re.compile(
        r"(?:测试)?(?:case|用例)" + _QUOTED.format(name="case"),
        re.IGNORECASE,
    ),
)
_FULLWIDTH_DIGITS = str.maketrans(
    "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19",
    "0123456789",
)
_ANALYZER_IDENTITY_VERSION = "v2-whole-plan-regeneration"


@dataclass(frozen=True, slots=True)
class TestCaseChangeAnalysis:
    proposal: dict[str, Any]

    @property
    def deterministic(self) -> bool:
        return bool(self.proposal["analysis_status"] == "deterministic")


@dataclass(frozen=True, slots=True)
class TestCaseRevisionPlan:
    orchestration: ChangeOrchestrationResult
    revision: dict[str, Any]


class TestCaseChangeAnalyzer:
    """Parse a bounded Japanese/Chinese business instruction into auditable choices."""

    def __init__(self, *, repository_root: Path) -> None:
        self._contracts = ContractCatalog.load(repository_root.resolve() / "contracts")

    def analyze(
        self,
        *,
        bundle: dict[str, Any],
        instruction: str,
    ) -> TestCaseChangeAnalysis:
        value = instruction.strip()
        if not value:
            raise ValueError("Test Case modification instruction must not be blank")
        orchestration = cast(dict[str, Any], bundle["orchestration"])
        test_plan = cast(dict[str, Any], bundle["test_plan"])
        statements = [
            statement.strip() for statement in re.split(r"[\n;\uFF1B]+", value) if statement.strip()
        ]
        operations: list[dict[str, Any]] = []
        ambiguities: list[dict[str, Any]] = []
        blockers: list[str] = []
        for sequence, statement in enumerate(statements, start=1):
            intent = _parse_statement(statement)
            if intent is None:
                operations.append(
                    _whole_plan_regeneration_operation(
                        sequence=sequence,
                        instruction=statement,
                        test_plan=test_plan,
                    )
                )
                continue
            case_hint = _case_hint(statement)
            cases = _candidate_cases(test_plan, intent, case_hint)
            if not cases:
                blockers.append(f"{sequence} 行目に一致するテスト Case がありません。")
                continue
            choices: list[tuple[str, list[dict[str, Any]]]] = []
            choice_blockers: list[str] = []
            for case in cases:
                resolved, alternatives, reason = _resolve_intent(
                    bundle=bundle,
                    case=case,
                    intent=intent,
                    sequence=sequence,
                )
                if reason is not None:
                    choice_blockers.append(reason)
                elif alternatives:
                    choices.extend(alternatives)
                else:
                    choices.append((str(case["title"]), resolved))
            if not choices:
                blockers.extend(choice_blockers or [f"{sequence} 行目を適用できません。"])
                continue
            if len(choices) == 1:
                operations.extend(choices[0][1])
                continue
            ambiguity_id = _id("ambiguity", str(sequence), statement)
            ambiguities.append(
                {
                    "ambiguity_id": ambiguity_id,
                    "question": f"{sequence} 行目はどの対象に適用しますか?",
                    "options": [
                        {
                            "option_id": _id(ambiguity_id, str(index), label),
                            "label": label,
                            "operations": choice_operations,
                        }
                        for index, (label, choice_operations) in enumerate(choices, start=1)
                    ],
                }
            )
        if blockers:
            status = "blocked"
            operations = []
            ambiguities = []
        elif ambiguities:
            status = "needs_confirmation"
        else:
            status = "deterministic"
        proposal_id = _id(
            "test-case-proposal",
            _ANALYZER_IDENTITY_VERSION,
            str(orchestration["orchestration_id"]),
            value,
        )
        proposal: dict[str, Any] = {
            "artifact_type": "TestCaseChangeProposal",
            "schema_version": "v1",
            "proposal_kind": "modification",
            "proposal_id": proposal_id,
            "change_request_id": orchestration["change_request_id"],
            "project_id": orchestration["project_id"],
            "source_orchestration_id": orchestration["orchestration_id"],
            "source_test_plan_id": test_plan["test_plan_id"],
            "instruction": value,
            "analysis_status": status,
            "operations": operations,
            "ambiguities": ambiguities,
            "blocking_reasons": sorted(set(blockers)),
        }
        self._contracts.validate_artifact(proposal)
        return TestCaseChangeAnalysis(proposal=proposal)


class TestCaseRevisionPlanner:
    """Apply confirmed operations and regenerate every dependent planning Artifact."""

    def __init__(
        self,
        *,
        repository_root: Path,
        identity_provider_types_for_project: (
            Callable[[str], Mapping[str, str]] | None
        ) = None,
    ) -> None:
        self._contracts = ContractCatalog.load(repository_root.resolve() / "contracts")
        self._identity_provider_types_for_project = (
            identity_provider_types_for_project
            or (lambda _project_id: DEFAULT_DATA_IDENTITY_PROVIDER_TYPES)
        )

    def plan(
        self,
        *,
        source_bundle: dict[str, Any],
        proposal: dict[str, Any],
        operations: list[dict[str, Any]],
        applied_by: str,
        selections: dict[str, str] | None = None,
        stale_run_ids: list[str] | None = None,
        stale_artifact_refs: list[str] | None = None,
        stale_evidence_refs: list[str] | None = None,
        stale_closure_result_ids: list[str] | None = None,
    ) -> TestCaseRevisionPlan:
        if not applied_by.strip():
            raise ValueError("Test Case revision actor must not be blank")
        if not operations:
            raise ValueError("Test Case revision requires at least one operation")
        source_orchestration = cast(dict[str, Any], source_bundle["orchestration"])
        if proposal["source_orchestration_id"] != source_orchestration["orchestration_id"]:
            raise ValueError("Test Case proposal source Orchestration differs")
        acceptance = copy.deepcopy(cast(dict[str, Any], source_bundle["acceptance_criteria"]))
        test_plan = copy.deepcopy(cast(dict[str, Any], source_bundle["test_plan"]))
        test_data = copy.deepcopy(cast(dict[str, Any], source_bundle["test_data_plan"]))
        for operation in operations:
            _apply_operation(
                operation=operation,
                test_plan=test_plan,
                acceptance=acceptance,
                test_data=test_data,
            )
        return self._finalize(
            source_bundle=source_bundle,
            proposal=proposal,
            operations=operations,
            acceptance=acceptance,
            test_plan=test_plan,
            test_data=test_data,
            applied_by=applied_by,
            selections=selections,
            stale_run_ids=stale_run_ids,
            stale_artifact_refs=stale_artifact_refs,
            stale_evidence_refs=stale_evidence_refs,
            stale_closure_result_ids=stale_closure_result_ids,
        )

    def validate_regenerated_operation_effects(
        self,
        *,
        source_bundle: dict[str, Any],
        operations: list[dict[str, Any]],
        test_plan: dict[str, Any],
        test_data_plan: dict[str, Any],
    ) -> None:
        """Prove that complete AI output implements every confirmed operation."""

        expected_plan = copy.deepcopy(cast(dict[str, Any], source_bundle["test_plan"]))
        expected_acceptance = copy.deepcopy(
            cast(dict[str, Any], source_bundle["acceptance_criteria"])
        )
        expected_data = copy.deepcopy(cast(dict[str, Any], source_bundle["test_data_plan"]))
        bounded_operations = [
            operation for operation in operations if operation["field"] != "plan_structure"
        ]
        structural_operations = [
            operation for operation in operations if operation["field"] == "plan_structure"
        ]
        for operation in bounded_operations:
            _apply_operation(
                operation=operation,
                test_plan=expected_plan,
                acceptance=expected_acceptance,
                test_data=expected_data,
            )
        for operation in bounded_operations:
            try:
                expected = _operation_target(
                    operation=operation,
                    test_plan=expected_plan,
                    test_data=expected_data,
                )
                actual = _operation_target(
                    operation=operation,
                    test_plan=test_plan,
                    test_data=test_data_plan,
                )
            except (KeyError, StopIteration, TypeError, IndexError) as error:
                raise ValueError(
                    "Regenerated UI TestPlan is missing a confirmed operation target: "
                    f"{operation.get('operation_id')}"
                ) from error
            if actual != expected:
                raise ValueError(
                    "Regenerated UI TestPlan did not apply confirmed operation: "
                    f"{operation.get('operation_id')}"
                )
        if structural_operations and not _planning_content_changed(
            source_bundle=source_bundle,
            test_plan=test_plan,
            test_data_plan=test_data_plan,
        ):
            raise ValueError(
                "Regenerated UI TestPlan did not change planning content for the confirmed "
                "whole-plan instruction"
            )

    def restore(
        self,
        *,
        source_bundle: dict[str, Any],
        restore_bundle: dict[str, Any],
        proposal: dict[str, Any],
        applied_by: str,
        stale_run_ids: list[str] | None = None,
        stale_artifact_refs: list[str] | None = None,
        stale_evidence_refs: list[str] | None = None,
        stale_closure_result_ids: list[str] | None = None,
    ) -> TestCaseRevisionPlan:
        """Create a compensating immutable version with the prior bundle's content."""

        operations = copy.deepcopy(cast(list[dict[str, Any]], proposal["operations"]))
        return self._finalize(
            source_bundle=source_bundle,
            proposal=proposal,
            operations=operations,
            acceptance=copy.deepcopy(cast(dict[str, Any], restore_bundle["acceptance_criteria"])),
            test_plan=copy.deepcopy(cast(dict[str, Any], restore_bundle["test_plan"])),
            test_data=copy.deepcopy(cast(dict[str, Any], restore_bundle["test_data_plan"])),
            applied_by=applied_by,
            selections={},
            stale_run_ids=stale_run_ids,
            stale_artifact_refs=stale_artifact_refs,
            stale_evidence_refs=stale_evidence_refs,
            stale_closure_result_ids=stale_closure_result_ids,
        )

    def plan_regenerated(
        self,
        *,
        source_bundle: dict[str, Any],
        proposal: dict[str, Any],
        operations: list[dict[str, Any]],
        test_plan: dict[str, Any],
        test_data_plan: dict[str, Any],
        applied_by: str,
        selections: dict[str, str] | None = None,
        stale_run_ids: list[str] | None = None,
        stale_artifact_refs: list[str] | None = None,
        stale_evidence_refs: list[str] | None = None,
        stale_closure_result_ids: list[str] | None = None,
    ) -> TestCaseRevisionPlan:
        """Create a new immutable version from complete AI-regenerated UI plans."""

        acceptance = copy.deepcopy(cast(dict[str, Any], source_bundle["acceptance_criteria"]))
        _bind_regenerated_cases_to_acceptance(
            acceptance=acceptance,
            test_plan=test_plan,
        )
        return self._finalize(
            source_bundle=source_bundle,
            proposal=proposal,
            operations=operations,
            acceptance=acceptance,
            test_plan=copy.deepcopy(test_plan),
            test_data=copy.deepcopy(test_data_plan),
            applied_by=applied_by,
            selections=selections,
            stale_run_ids=stale_run_ids,
            stale_artifact_refs=stale_artifact_refs,
            stale_evidence_refs=stale_evidence_refs,
            stale_closure_result_ids=stale_closure_result_ids,
        )

    def _finalize(
        self,
        *,
        source_bundle: dict[str, Any],
        proposal: dict[str, Any],
        operations: list[dict[str, Any]],
        acceptance: dict[str, Any],
        test_plan: dict[str, Any],
        test_data: dict[str, Any],
        applied_by: str,
        selections: dict[str, str] | None,
        stale_run_ids: list[str] | None,
        stale_artifact_refs: list[str] | None,
        stale_evidence_refs: list[str] | None,
        stale_closure_result_ids: list[str] | None,
    ) -> TestCaseRevisionPlan:
        if not applied_by.strip():
            raise ValueError("Test Case revision actor must not be blank")
        if not operations:
            raise ValueError("Test Case revision requires at least one operation")
        source_orchestration = cast(dict[str, Any], source_bundle["orchestration"])
        if proposal["source_orchestration_id"] != source_orchestration["orchestration_id"]:
            raise ValueError("Test Case proposal source Orchestration differs")
        data_blockers = validate_test_data_plan_artifact(
            test_data,
            identity_provider_types=self._identity_provider_types_for_project(
                str(source_orchestration["project_id"])
            ),
        )
        test_data["status"] = "blocked" if data_blockers else "ready"
        test_data["blocking_reasons"] = data_blockers
        test_plan["status"] = "ready"
        test_plan["blocking_reasons"] = []
        material = _canonical_bytes(
            {
                "source_orchestration_id": source_orchestration["orchestration_id"],
                "proposal_id": proposal["proposal_id"],
                "operations": operations,
                "acceptance": acceptance["criteria"],
                "test_cases": test_plan["test_cases"],
                "test_data": {
                    "data_sets": test_data["data_sets"],
                    "generation_flows": test_data["generation_flows"],
                },
            }
        )
        revision_digest = hashlib.sha256(material).hexdigest()
        acceptance["acceptance_criteria_id"] = _id("acceptance", revision_digest)
        test_plan["test_plan_id"] = _id("test-plan", revision_digest)
        test_data["test_data_plan_id"] = _id("test-data-plan", revision_digest)
        test_data["test_plan_id"] = test_plan["test_plan_id"]
        coverage = _coverage(
            source_bundle=source_bundle,
            acceptance=acceptance,
            test_plan=test_plan,
            coverage_id=_id("coverage", revision_digest),
        )
        orchestration = copy.deepcopy(source_orchestration)
        orchestration["orchestration_id"] = _id("orchestration", revision_digest)
        orchestration["reviewed_case_digest"] = revision_digest
        orchestration["status"] = "blocked" if data_blockers else "ready"
        orchestration["blocking_reasons"] = data_blockers
        orchestration["artifact_refs"] = {
            "acceptance_criteria_id": acceptance["acceptance_criteria_id"],
            "test_plan_id": test_plan["test_plan_id"],
            "test_data_plan_id": test_data["test_data_plan_id"],
            "coverage_report_id": coverage["coverage_report_id"],
        }
        orchestration["ui_scenarios"] = [
            {
                "scenario_id": case["test_case_id"],
                "title": case["title"],
                "test_data_refs": copy.deepcopy(case["test_data_refs"]),
                "steps": copy.deepcopy(case["steps"]),
                "expected_results": copy.deepcopy(case["expected_results"]),
            }
            for case in cast(list[dict[str, Any]], test_plan["test_cases"])
            if case["level"] == "ui"
        ]
        result = ChangeOrchestrationResult(
            orchestration=orchestration,
            acceptance_criteria=acceptance,
            test_plan=test_plan,
            test_data_plan=test_data,
            coverage_report=coverage,
        )
        for artifact in result.artifacts:
            self._contracts.validate_artifact(artifact)
        revision: dict[str, Any] = {
            "artifact_type": "TestCaseRevision",
            "schema_version": "v1",
            "revision_kind": proposal.get("proposal_kind", "modification"),
            "revision_id": _id(
                "test-case-revision",
                str(proposal["proposal_id"]),
                str(orchestration["orchestration_id"]),
            ),
            "proposal_id": proposal["proposal_id"],
            "change_request_id": orchestration["change_request_id"],
            "project_id": orchestration["project_id"],
            "source_orchestration_id": source_orchestration["orchestration_id"],
            "target_orchestration_id": orchestration["orchestration_id"],
            "source_test_plan_id": proposal["source_test_plan_id"],
            "target_test_plan_id": test_plan["test_plan_id"],
            "selections": dict(sorted((selections or {}).items())),
            "applied_operations": copy.deepcopy(operations),
            "stale_run_ids": sorted(set(stale_run_ids or [])),
            "stale_artifact_refs": sorted(set(stale_artifact_refs or [])),
            "stale_evidence_refs": sorted(set(stale_evidence_refs or [])),
            "stale_closure_result_ids": sorted(set(stale_closure_result_ids or [])),
            "applied_by": applied_by,
        }
        if proposal.get("undo_of_revision_id") is not None:
            revision["undo_of_revision_id"] = proposal["undo_of_revision_id"]
        self._contracts.validate_artifact(revision)
        return TestCaseRevisionPlan(orchestration=result, revision=revision)


def resolve_ambiguities(
    proposal: dict[str, Any], selections: dict[str, str]
) -> list[dict[str, Any]]:
    """Return base and selected operations, rejecting missing or foreign choices."""

    ambiguities = cast(list[dict[str, Any]], proposal["ambiguities"])
    expected = {str(value["ambiguity_id"]) for value in ambiguities}
    if set(selections) != expected:
        raise ValueError("Every ambiguity must have exactly one selected option")
    operations = copy.deepcopy(cast(list[dict[str, Any]], proposal["operations"]))
    for ambiguity in ambiguities:
        ambiguity_id = str(ambiguity["ambiguity_id"])
        selected = selections[ambiguity_id]
        option = next(
            (
                item
                for item in cast(list[dict[str, Any]], ambiguity["options"])
                if item["option_id"] == selected
            ),
            None,
        )
        if option is None:
            raise ValueError("Selected Test Case modification option does not exist")
        operations.extend(copy.deepcopy(cast(list[dict[str, Any]], option["operations"])))
    return operations


def build_undo_proposal(
    *,
    repository_root: Path,
    current_bundle: dict[str, Any],
    revision: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Describe an undo as a reviewable compensating diff, never a history rewrite."""

    if not idempotency_key.strip():
        raise ValueError("Test Case undo idempotency key must not be blank")
    current = cast(dict[str, Any], current_bundle["orchestration"])
    if revision["target_orchestration_id"] != current["orchestration_id"]:
        raise ValueError("Only the current Test Case revision can be undone")
    operations: list[dict[str, Any]] = []
    for operation in reversed(cast(list[dict[str, Any]], revision["applied_operations"])):
        restored = {
            "operation_id": _id(
                "test-case-undo-operation",
                str(revision["revision_id"]),
                str(operation["operation_id"]),
            ),
            "test_case_id": operation["test_case_id"],
            "case_title": operation["case_title"],
            "field": operation["field"],
            "action": "restore",
            "summary_before": operation["summary_after"],
            "summary_after": operation["summary_before"],
        }
        if "after" in operation:
            restored["before"] = copy.deepcopy(operation["after"])
        if "before" in operation:
            restored["after"] = copy.deepcopy(operation["before"])
        operations.append(restored)
    proposal: dict[str, Any] = {
        "artifact_type": "TestCaseChangeProposal",
        "schema_version": "v1",
        "proposal_kind": "undo",
        "undo_of_revision_id": revision["revision_id"],
        "proposal_id": _id(
            "test-case-undo-proposal",
            str(current["orchestration_id"]),
            str(revision["revision_id"]),
            idempotency_key,
        ),
        "change_request_id": current["change_request_id"],
        "project_id": current["project_id"],
        "source_orchestration_id": current["orchestration_id"],
        "source_test_plan_id": current_bundle["test_plan"]["test_plan_id"],
        "instruction": f"改訂 {revision['revision_id']} を取り消す",
        "analysis_status": "deterministic",
        "operations": operations,
        "ambiguities": [],
        "blocking_reasons": [],
    }
    ContractCatalog.load(repository_root.resolve() / "contracts").validate_artifact(proposal)
    return proposal


def _parse_statement(statement: str) -> dict[str, Any] | None:
    variable_patterns = (
        re.compile(
            r"変数"
            + _QUOTED.format(name="variable")
            + r"の(?:取得元|出力元|参照先)を"
            + _QUOTED.format(name="after")
            + r"(?:に変更|へ変更|に修正)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:将)?变量"
            + _QUOTED.format(name="variable")
            + r"的(?:来源|取值路径|输出路径|引用路径)(?:修改|更改)为"
            + _QUOTED.format(name="after"),
            re.IGNORECASE,
        ),
    )
    for pattern in variable_patterns:
        match = pattern.search(statement)
        if match:
            return {
                "field": "variable_bindings",
                "action": "replace",
                **match.groupdict(),
            }
    process_step_patterns = (
        (
            "generation_steps",
            re.compile(
                r"(?:生成ステップ|データ生成ステップ)"
                + _QUOTED.format(name="before")
                + r"(?:を)?"
                + _QUOTED.format(name="after")
                + r"(?:に変更|へ変更|に修正)",
                re.IGNORECASE,
            ),
        ),
        (
            "generation_steps",
            re.compile(
                r"(?:将)?(?:生成步骤|数据生成步骤)"
                + _QUOTED.format(name="before")
                + r"(?:修改|更改)为"
                + _QUOTED.format(name="after"),
                re.IGNORECASE,
            ),
        ),
        (
            "cleanup_steps",
            re.compile(
                r"(?:クリーンアップステップ|後始末ステップ)"
                + _QUOTED.format(name="before")
                + r"(?:を)?"
                + _QUOTED.format(name="after")
                + r"(?:に変更|へ変更|に修正)",
                re.IGNORECASE,
            ),
        ),
        (
            "cleanup_steps",
            re.compile(
                r"(?:将)?(?:清理步骤|清除步骤)"
                + _QUOTED.format(name="before")
                + r"(?:修改|更改)为"
                + _QUOTED.format(name="after"),
                re.IGNORECASE,
            ),
        ),
    )
    for field, pattern in process_step_patterns:
        match = pattern.search(statement)
        if match:
            return {"field": field, "action": "replace", **match.groupdict()}
    indexed_step_patterns = (
        re.compile(
            r"(?:第)?(?P<index>[0-9\uFF10-\uFF19]+)(?:番目)?のステップ(?:を)?"
            + _QUOTED.format(name="after")
            + r"(?:に変更|へ変更|に修正)",
            re.IGNORECASE,
        ),
        re.compile(
            r"ステップ(?:第)?(?P<index>[0-9\uFF10-\uFF19]+)(?:番目)?(?:を)?"
            + _QUOTED.format(name="after")
            + r"(?:に変更|へ変更|に修正)",
            re.IGNORECASE,
        ),
    )
    for pattern in indexed_step_patterns:
        match = pattern.search(statement)
        if match:
            position = int(match.group("index").translate(_FULLWIDTH_DIGITS))
            return {
                "field": "steps",
                "action": "replace",
                "index": position - 1,
                "after": match.group("after"),
            }
    patterns: tuple[tuple[str, str, re.Pattern[str]], ...] = (
        (
            "steps",
            "insert_after",
            re.compile(
                r"(?:ステップ|步骤)"
                + _QUOTED.format(name="before")
                + r"(?:の後に|后(?:面)?)"
                + _QUOTED.format(name="after")
                + r"(?:を)?(?:追加|添加)",
                re.IGNORECASE,
            ),
        ),
        (
            "steps",
            "insert_after",
            re.compile(
                r"(?:在)?(?:ステップ|步骤)"
                + _QUOTED.format(name="before")
                + r"(?:之后|后)"
                + r"(?:追加|添加)"
                + _QUOTED.format(name="after"),
                re.IGNORECASE,
            ),
        ),
        *tuple(
            (
                field,
                "replace",
                re.compile(
                    rf"(?:将)?{chinese}"
                    + _QUOTED.format(name="before")
                    + r"(?:修改|更改)为"
                    + _QUOTED.format(name="after"),
                    re.IGNORECASE,
                ),
            )
            for field, chinese in (
                ("steps", "步骤"),
                ("expected_results", "预期结果"),
                ("test_data_refs", "测试数据"),
                ("business_assertions", "业务断言"),
            )
        ),
        *tuple(
            (
                field,
                "replace",
                re.compile(
                    rf"(?:{japanese}|{chinese})"
                    + _QUOTED.format(name="before")
                    + r"(?:を)?"
                    + _QUOTED.format(name="after")
                    + r"(?:に変更|へ変更|に修正|修改为|更改为)",
                    re.IGNORECASE,
                ),
            )
            for field, japanese, chinese in (
                ("steps", "ステップ", "(?:将)?步骤"),
                ("expected_results", "期待結果", "(?:将)?预期结果"),
                ("test_data_refs", "テストデータ", "(?:将)?测试数据"),
                ("business_assertions", "業務アサーション", "(?:将)?业务断言"),
            )
        ),
        *tuple(
            (
                field,
                "remove",
                re.compile(
                    rf"(?:{japanese}|{chinese})"
                    + _QUOTED.format(name="before")
                    + r"(?:を)?(?:削除|删除)",
                    re.IGNORECASE,
                ),
            )
            for field, japanese, chinese in (
                ("steps", "ステップ", "(?:删除)?步骤"),
                ("expected_results", "期待結果", "(?:删除)?预期结果"),
                ("test_data_refs", "テストデータ", "(?:删除)?测试数据"),
            )
        ),
        *tuple(
            (
                field,
                "append",
                re.compile(
                    rf"(?:{japanese}(?:に)?|(?:添加)?{chinese})"
                    + _QUOTED.format(name="after")
                    + r"(?:を)?(?:追加|添加)?",
                    re.IGNORECASE,
                ),
            )
            for field, japanese, chinese in (
                ("steps", "ステップ", "步骤"),
                ("expected_results", "期待結果", "预期结果"),
                ("test_data_refs", "テストデータ", "测试数据"),
            )
        ),
    )
    data_value_patterns = (
        re.compile(
            r"テストデータ"
            + _QUOTED.format(name="data_set")
            + r"の(?:項目|フィールド)"
            + _QUOTED.format(name="key")
            + r"を"
            + _QUOTED.format(name="after")
            + r"に変更",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:将)?测试数据"
            + _QUOTED.format(name="data_set")
            + r"的(?:字段|项目)"
            + _QUOTED.format(name="key")
            + r"(?:修改|更改)为"
            + _QUOTED.format(name="after"),
            re.IGNORECASE,
        ),
    )
    for pattern in data_value_patterns:
        match = pattern.search(statement)
        if match:
            return {"field": "test_data_values", "action": "replace", **match.groupdict()}
    for field, action, pattern in patterns:
        match = pattern.search(statement)
        if match:
            return {"field": field, "action": action, **match.groupdict()}
    return None


def _case_hint(statement: str) -> str | None:
    for pattern in _CASE_PATTERNS:
        match = pattern.search(statement)
        if match:
            return match.group("case").strip()
    return None


def _candidate_cases(
    test_plan: dict[str, Any], intent: dict[str, Any], hint: str | None
) -> list[dict[str, Any]]:
    cases = cast(list[dict[str, Any]], test_plan["test_cases"])
    if hint is not None:
        exact = [case for case in cases if hint in {str(case["test_case_id"]), str(case["title"])}]
        if exact:
            return exact
        return [
            case
            for case in cases
            if hint in str(case["title"]) or hint in str(case["test_case_id"])
        ]
    before = intent.get("before")
    if before is not None and intent["field"] in {
        "steps",
        "expected_results",
        "test_data_refs",
    }:
        field = str(intent["field"])
        matching = [case for case in cases if before in cast(list[object], case[field])]
        if matching:
            return matching
    return cases if len(cases) > 1 else cases[:1]


def _resolve_intent(
    *,
    bundle: dict[str, Any],
    case: dict[str, Any],
    intent: dict[str, Any],
    sequence: int,
) -> tuple[
    list[dict[str, Any]],
    list[tuple[str, list[dict[str, Any]]]],
    str | None,
]:
    field = str(intent["field"])
    action = str(intent["action"])
    case_id = str(case["test_case_id"])
    if field in {"steps", "expected_results", "test_data_refs"}:
        values = cast(list[object], case[field])
        index = intent.get("index")
        if index is not None:
            if not isinstance(index, int) or index < 0 or index >= len(values):
                return (
                    [],
                    [],
                    (
                        f"{case['title']} に {int(index) + 1 if isinstance(index, int) else index}"
                        " 番目のステップがありません。"
                    ),
                )
            before = values[index]
        else:
            before = intent.get("before")
        after = intent.get("after")
        if action in {"replace", "remove", "insert_after"} and before not in values:
            return [], [], f"{case['title']} に変更前の内容「{before}」がありません。"
        if action in {"replace", "append", "insert_after"} and after in values:
            return [], [], f"{case['title']} には変更後の内容「{after}」が既にあります。"
        if field == "test_data_refs" and after is not None:
            known = {
                str(item["test_data_id"])
                for item in cast(list[dict[str, Any]], bundle["test_data_plan"]["data_sets"])
            }
            if str(after) not in known:
                alternatives = [
                    (
                        f"{case['title']}: テストデータ {candidate}",
                        [
                            _operation(
                                sequence=sequence,
                                case=case,
                                field=field,
                                action=action,
                                before=before,
                                after=candidate,
                            )
                        ],
                    )
                    for candidate in sorted(known - {str(before)})
                ]
                if len(alternatives) >= 2:
                    return [], alternatives, None
                return [], [], f"テストデータ「{after}」は生成済み計画にありません。"
        operation = _operation(
            sequence=sequence,
            case=case,
            field=field,
            action=action,
            before=before,
            after=after,
        )
        if index is not None:
            operation["index"] = index
        if field == "expected_results" and action in {"replace", "remove"}:
            linked = _linked_criteria(bundle, case_id)
            exact = [item for item in linked if str(item.get("expected")) == str(before)]
            candidates = exact or (linked if len(linked) == 1 else [])
            if len(candidates) == 1:
                operation["criterion_id"] = candidates[0]["criterion_id"]
            elif len(linked) > 1:
                return (
                    [],
                    [
                        (
                            f"{case['title']}: 受入基準 {criterion['criterion_id']}",
                            [{**operation, "criterion_id": criterion["criterion_id"]}],
                        )
                        for criterion in linked
                    ],
                    None,
                )
        return [operation], [], None
    if field == "test_data_values":
        data_sets = cast(list[dict[str, Any]], bundle["test_data_plan"]["data_sets"])
        selected = [item for item in data_sets if item["test_data_id"] == intent["data_set"]]
        if len(selected) != 1:
            return [], [], f"テストデータ「{intent['data_set']}」が一意に見つかりません。"
        value_matches = _leaf_paths(selected[0], str(intent["key"]))
        if not value_matches:
            return [], [], f"テストデータに項目「{intent['key']}」がありません。"
        data_choices: list[tuple[str, list[dict[str, Any]]]] = []
        for path, before in value_matches:
            after = _coerce_value(str(intent["after"]), before)
            operation = _operation(
                sequence=sequence,
                case=case,
                field=field,
                action="replace",
                before=before,
                after=after,
            )
            operation["data_set_id"] = intent["data_set"]
            operation["json_path"] = path
            label = f"{case['title']}: {_path_label(path)} = {before}"
            data_choices.append((label, [operation]))
        return (
            (data_choices[0][1], [], None) if len(data_choices) == 1 else ([], data_choices, None)
        )
    if field == "business_assertions":
        before = intent.get("before")
        after = intent.get("after")
        assertion_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for flow in cast(list[dict[str, Any]], bundle["test_data_plan"]["generation_flows"]):
            if case_id not in cast(list[str], flow["test_case_refs"]):
                continue
            for assertion in cast(list[dict[str, Any]], flow["final_assertions"]):
                if str(assertion.get("expected")) == str(before):
                    assertion_matches.append((flow, assertion))
        if not assertion_matches:
            return [], [], f"{case['title']} に業務アサーション「{before}」がありません。"
        linked = _linked_criteria(bundle, case_id)
        criterion = next(
            (item for item in linked if str(item.get("expected")) == str(before)),
            linked[0] if len(linked) == 1 else None,
        )
        assertion_choices: list[tuple[str, list[dict[str, Any]]]] = []
        for flow, assertion in assertion_matches:
            operation = _operation(
                sequence=sequence,
                case=case,
                field=field,
                action="replace",
                before=before,
                after=after,
            )
            operation["flow_id"] = flow["flow_id"]
            operation["assertion_id"] = assertion["assertion_id"]
            if criterion is not None:
                operation["criterion_id"] = criterion["criterion_id"]
            assertion_choices.append(
                (
                    f"{case['title']}: {assertion['subject']} = {before}",
                    [operation],
                )
            )
        return (
            (assertion_choices[0][1], [], None)
            if len(assertion_choices) == 1
            else ([], assertion_choices, None)
        )
    if field in {"generation_steps", "cleanup_steps"}:
        list_name = "steps" if field == "generation_steps" else "cleanup_steps"
        before = intent.get("before")
        after = intent.get("after")
        step_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for flow in cast(list[dict[str, Any]], bundle["test_data_plan"]["generation_flows"]):
            if case_id not in cast(list[str], flow["test_case_refs"]):
                continue
            for step in cast(list[dict[str, Any]], flow[list_name]):
                if str(step.get("business_action")) == str(before):
                    step_matches.append((flow, step))
        if not step_matches:
            label = "生成ステップ" if field == "generation_steps" else "クリーンアップ"
            return [], [], f"{case['title']} に{label}「{before}」がありません。"
        choices: list[tuple[str, list[dict[str, Any]]]] = []
        for flow, step in step_matches:
            operation = _operation(
                sequence=sequence,
                case=case,
                field=field,
                action="replace",
                before=before,
                after=after,
            )
            operation["flow_id"] = flow["flow_id"]
            operation["step_id"] = step["step_id"]
            choices.append((f"{case['title']}: {flow['title']} / {before}", [operation]))
        return (choices[0][1], [], None) if len(choices) == 1 else ([], choices, None)
    if field == "variable_bindings":
        variable = str(intent["variable"])
        binding_matches: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for flow in cast(list[dict[str, Any]], bundle["test_data_plan"]["generation_flows"]):
            if case_id not in cast(list[str], flow["test_case_refs"]):
                continue
            for step in [
                *cast(list[dict[str, Any]], flow["steps"]),
                *cast(list[dict[str, Any]], flow["cleanup_steps"]),
            ]:
                for binding in cast(list[dict[str, Any]], step["output_bindings"]):
                    if str(binding.get("variable")) == variable:
                        binding_matches.append((flow, step, binding))
        if not binding_matches:
            return [], [], f"{case['title']} に変数「{variable}」がありません。"
        choices = []
        for flow, step, binding in binding_matches:
            operation = _operation(
                sequence=sequence,
                case=case,
                field=field,
                action="replace",
                before=binding.get("path"),
                after=intent["after"],
            )
            operation["flow_id"] = flow["flow_id"]
            operation["step_id"] = step["step_id"]
            operation["variable"] = variable
            choices.append(
                (
                    f"{case['title']}: {variable} ({step['business_action']})",
                    [operation],
                )
            )
        return (choices[0][1], [], None) if len(choices) == 1 else ([], choices, None)
    return [], [], "対応していない変更対象です。"


def _operation(
    *,
    sequence: int,
    case: dict[str, Any],
    field: str,
    action: str,
    before: object | None,
    after: object | None,
) -> dict[str, Any]:
    labels = {
        "steps": "手順",
        "test_data_refs": "テストデータ",
        "test_data_values": "テストデータ項目",
        "expected_results": "期待結果",
        "business_assertions": "業務アサーション",
        "generation_steps": "データ生成手順",
        "cleanup_steps": "クリーンアップ手順",
        "variable_bindings": "変数の取得元",
    }
    operation: dict[str, Any] = {
        "operation_id": _id(
            "test-case-operation",
            str(sequence),
            str(case["test_case_id"]),
            field,
            action,
            str(before),
            str(after),
        ),
        "test_case_id": case["test_case_id"],
        "case_title": case["title"],
        "field": field,
        "action": action,
        "summary_before": f"{labels[field]}: {before if before is not None else 'なし'}",
        "summary_after": f"{labels[field]}: {after if after is not None else '削除'}",
    }
    if before is not None:
        operation["before"] = before
    if after is not None:
        operation["after"] = after
    return operation


def _whole_plan_regeneration_operation(
    *,
    sequence: int,
    instruction: str,
    test_plan: dict[str, Any],
) -> dict[str, Any]:
    """Keep free-form structural changes auditable without pretending to parse them locally."""

    cases = cast(list[dict[str, Any]], test_plan["test_cases"])
    return {
        "operation_id": _id(
            "test-case-operation",
            str(sequence),
            "whole-plan",
            instruction,
        ),
        "test_case_id": "whole-ui-test-plan",
        "case_title": "UI TestPlan / TestDataPlan 全体",
        "field": "plan_structure",
        "action": "regenerate",
        "before": {
            "test_case_count": len(cases),
            "test_case_titles": [str(case["title"]) for case in cases],
        },
        "after": instruction,
        "summary_before": f"現在の計画: {len(cases)} Test Case",
        "summary_after": f"自然言語要求: {instruction}",
    }


def _planning_content_changed(
    *,
    source_bundle: dict[str, Any],
    test_plan: dict[str, Any],
    test_data_plan: dict[str, Any],
) -> bool:
    source = {
        "test_cases": source_bundle["test_plan"]["test_cases"],
        "data_sets": source_bundle["test_data_plan"]["data_sets"],
        "generation_flows": source_bundle["test_data_plan"]["generation_flows"],
    }
    regenerated = {
        "test_cases": test_plan["test_cases"],
        "data_sets": test_data_plan["data_sets"],
        "generation_flows": test_data_plan["generation_flows"],
    }
    return _canonical_bytes(source) != _canonical_bytes(regenerated)


def _linked_criteria(bundle: dict[str, Any], case_id: str) -> list[dict[str, Any]]:
    return [
        criterion
        for criterion in cast(list[dict[str, Any]], bundle["acceptance_criteria"]["criteria"])
        if case_id in cast(list[str], criterion["test_case_refs"])
    ]


def _leaf_paths(
    value: object,
    key: str,
    path: list[str | int] | None = None,
) -> list[tuple[list[str | int], object]]:
    current = path or []
    matches: list[tuple[list[str | int], object]] = []
    if isinstance(value, dict):
        for name, child in value.items():
            child_path = [*current, name]
            if name == key and not isinstance(child, (dict, list)):
                matches.append((child_path, child))
            matches.extend(_leaf_paths(child, key, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(_leaf_paths(child, key, [*current, index]))
    return matches


def _path_label(path: list[str | int]) -> str:
    return " / ".join(str(value) for value in path)


def _coerce_value(value: str, before: object) -> object:
    if isinstance(before, bool):
        lowered = value.lower()
        if lowered in {"true", "はい", "是"}:
            return True
        if lowered in {"false", "いいえ", "否"}:
            return False
    if isinstance(before, int) and not isinstance(before, bool):
        try:
            return int(value)
        except ValueError:
            return value
    if isinstance(before, float):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _apply_operation(
    *,
    operation: dict[str, Any],
    test_plan: dict[str, Any],
    acceptance: dict[str, Any],
    test_data: dict[str, Any],
) -> None:
    if operation["field"] == "plan_structure":
        raise ValueError("Whole-plan Test Case changes require Copilot regeneration")
    case = next(
        item
        for item in cast(list[dict[str, Any]], test_plan["test_cases"])
        if item["test_case_id"] == operation["test_case_id"]
    )
    field = str(operation["field"])
    action = str(operation["action"])
    if field in {"steps", "expected_results", "test_data_refs"}:
        values = cast(list[Any], case[field])
        step_ids = cast(list[Any], case.get("step_ids", [])) if field == "steps" else []
        before = operation.get("before")
        after = operation.get("after")
        if action == "replace":
            values[values.index(before)] = after
        elif action == "remove":
            removed_index = values.index(before)
            values.remove(before)
            if step_ids:
                step_ids.pop(removed_index)
        elif action == "insert_after":
            insert_at = values.index(before) + 1
            values.insert(insert_at, after)
            if step_ids:
                step_ids.insert(insert_at, _id("test-step", str(case["test_case_id"]), str(after)))
        elif action == "append":
            values.append(after)
            if step_ids:
                step_ids.append(_id("test-step", str(case["test_case_id"]), str(after)))
        if not values:
            raise ValueError(f"Test Case {field} must not become empty")
        if field == "expected_results":
            _update_acceptance(operation, case, acceptance)
            _regenerate_final_assertions(operation, case, test_data)
        if field == "test_data_refs":
            _update_test_data_refs(operation, case, test_data)
        return
    if field == "test_data_values":
        data_set = next(
            item
            for item in cast(list[dict[str, Any]], test_data["data_sets"])
            if item["test_data_id"] == operation["data_set_id"]
        )
        target: object = data_set
        path = cast(list[str | int], operation["json_path"])
        for part in path[:-1]:
            target = target[part]  # type: ignore[index]
        target[path[-1]] = operation["after"]  # type: ignore[index]
        return
    if field == "business_assertions":
        flow = next(
            item
            for item in cast(list[dict[str, Any]], test_data["generation_flows"])
            if item["flow_id"] == operation["flow_id"]
        )
        assertion = next(
            item
            for item in cast(list[dict[str, Any]], flow["final_assertions"])
            if item["assertion_id"] == operation["assertion_id"]
        )
        assertion["expected"] = operation["after"]
        _update_acceptance(operation, case, acceptance)
        return
    if field in {"generation_steps", "cleanup_steps"}:
        flow = next(
            item
            for item in cast(list[dict[str, Any]], test_data["generation_flows"])
            if item["flow_id"] == operation["flow_id"]
        )
        list_name = "steps" if field == "generation_steps" else "cleanup_steps"
        step = next(
            item
            for item in cast(list[dict[str, Any]], flow[list_name])
            if item["step_id"] == operation["step_id"]
        )
        step["business_action"] = operation["after"]
        return
    if field == "variable_bindings":
        flow = next(
            item
            for item in cast(list[dict[str, Any]], test_data["generation_flows"])
            if item["flow_id"] == operation["flow_id"]
        )
        step = next(
            item
            for item in [
                *cast(list[dict[str, Any]], flow["steps"]),
                *cast(list[dict[str, Any]], flow["cleanup_steps"]),
            ]
            if item["step_id"] == operation["step_id"]
        )
        binding = next(
            item
            for item in cast(list[dict[str, Any]], step["output_bindings"])
            if item["variable"] == operation["variable"]
        )
        binding["path"] = operation["after"]
        return
    raise ValueError(f"Unsupported Test Case operation field: {field}")


def _operation_target(
    *,
    operation: dict[str, Any],
    test_plan: dict[str, Any],
    test_data: dict[str, Any],
) -> object:
    case = next(
        item
        for item in cast(list[dict[str, Any]], test_plan["test_cases"])
        if item["test_case_id"] == operation["test_case_id"]
    )
    field = str(operation["field"])
    if field in {"steps", "expected_results", "test_data_refs"}:
        return copy.deepcopy(case[field])
    if field == "test_data_values":
        data_set = next(
            item
            for item in cast(list[dict[str, Any]], test_data["data_sets"])
            if item["test_data_id"] == operation["data_set_id"]
        )
        target: object = data_set
        for part in cast(list[str | int], operation["json_path"]):
            target = target[part]  # type: ignore[index]
        return copy.deepcopy(target)
    flow = next(
        item
        for item in cast(list[dict[str, Any]], test_data["generation_flows"])
        if item["flow_id"] == operation["flow_id"]
    )
    if field == "business_assertions":
        assertion = next(
            item
            for item in cast(list[dict[str, Any]], flow["final_assertions"])
            if item["assertion_id"] == operation["assertion_id"]
        )
        return copy.deepcopy(assertion["expected"])
    if field in {"generation_steps", "cleanup_steps"}:
        collection = "steps" if field == "generation_steps" else "cleanup_steps"
        step = next(
            item
            for item in cast(list[dict[str, Any]], flow[collection])
            if item["step_id"] == operation["step_id"]
        )
        return copy.deepcopy(step["business_action"])
    if field == "variable_bindings":
        step = next(
            item
            for item in [
                *cast(list[dict[str, Any]], flow["steps"]),
                *cast(list[dict[str, Any]], flow["cleanup_steps"]),
            ]
            if item["step_id"] == operation["step_id"]
        )
        binding = next(
            item
            for item in cast(list[dict[str, Any]], step["output_bindings"])
            if item["variable"] == operation["variable"]
        )
        return copy.deepcopy(binding["path"])
    raise ValueError(f"Unsupported Test Case operation field: {field}")


def _update_acceptance(
    operation: dict[str, Any],
    case: dict[str, Any],
    acceptance: dict[str, Any],
) -> None:
    criteria = cast(list[dict[str, Any]], acceptance["criteria"])
    criterion_id = operation.get("criterion_id")
    if criterion_id is not None:
        criterion = next(item for item in criteria if item["criterion_id"] == criterion_id)
        if operation["action"] == "remove":
            criterion["test_case_refs"].remove(case["test_case_id"])
            case["acceptance_criteria_refs"].remove(criterion_id)
            if not criterion["test_case_refs"]:
                criteria.remove(criterion)
        else:
            criterion["expected"] = operation["after"]
        return
    if operation["action"] != "append":
        return
    criterion_id = _id(
        "criterion-natural-language",
        str(case["test_case_id"]),
        str(operation["after"]),
    )
    criteria.append(
        {
            "criterion_id": criterion_id,
            "business_rule_refs": copy.deepcopy(case["business_rule_refs"]),
            "assertion_type": case["level"],
            "subject": case["title"],
            "operator": "contains",
            "expected": operation["after"],
            "test_case_refs": [case["test_case_id"]],
        }
    )
    case["acceptance_criteria_refs"].append(criterion_id)
    case["acceptance_criteria_refs"] = sorted(set(case["acceptance_criteria_refs"]))


def _regenerate_final_assertions(
    operation: dict[str, Any],
    case: dict[str, Any],
    test_data: dict[str, Any],
) -> None:
    """Synchronize final assertions derived from one Case expected result."""

    case_id = str(case["test_case_id"])
    action = str(operation["action"])
    before = operation.get("before")
    after = operation.get("after")
    for flow in cast(list[dict[str, Any]], test_data["generation_flows"]):
        if case_id not in cast(list[str], flow["test_case_refs"]):
            continue
        assertions = cast(list[dict[str, Any]], flow["final_assertions"])
        linked = [
            assertion
            for assertion in assertions
            if str(assertion.get("subject")) == case_id
            and assertion.get("observe_via") == "test"
            and (action == "append" or assertion.get("expected") == before)
        ]
        if action == "replace":
            for assertion in linked:
                assertion["expected"] = after
        elif action == "remove":
            for assertion in linked:
                assertions.remove(assertion)
        elif action == "append":
            assertions.append(
                {
                    "assertion_id": _id(
                        "assertion-natural-language",
                        str(flow["flow_id"]),
                        case_id,
                        str(after),
                    ),
                    "observe_via": "test",
                    "subject": case_id,
                    "operator": "satisfies",
                    "expected": after,
                }
            )


def _update_test_data_refs(
    operation: dict[str, Any], case: dict[str, Any], test_data: dict[str, Any]
) -> None:
    case_id = str(case["test_case_id"])
    current_refs = set(cast(list[str], case["test_data_refs"]))
    for data_set in list(cast(list[dict[str, Any]], test_data["data_sets"])):
        refs = cast(list[str], data_set["test_case_refs"])
        data_id = str(data_set["test_data_id"])
        if data_id in current_refs and case_id not in refs:
            refs.append(case_id)
        if data_id not in current_refs and case_id in refs:
            refs.remove(case_id)
    valid_data = {
        str(item["test_data_id"])
        for item in cast(list[dict[str, Any]], test_data["data_sets"])
        if cast(list[str], item["test_case_refs"])
    }
    test_data["data_sets"] = [
        item
        for item in cast(list[dict[str, Any]], test_data["data_sets"])
        if item["test_data_id"] in valid_data
    ]
    for flow in list(cast(list[dict[str, Any]], test_data["generation_flows"])):
        flow_data = set(cast(list[str], flow["test_data_refs"]))
        refs = cast(list[str], flow["test_case_refs"])
        if flow_data & current_refs and case_id not in refs:
            refs.append(case_id)
        if not flow_data & current_refs and case_id in refs:
            refs.remove(case_id)
    test_data["generation_flows"] = [
        flow
        for flow in cast(list[dict[str, Any]], test_data["generation_flows"])
        if cast(list[str], flow["test_case_refs"])
        and set(cast(list[str], flow["test_data_refs"])) <= valid_data
    ]


def _bind_regenerated_cases_to_acceptance(
    *, acceptance: dict[str, Any], test_plan: dict[str, Any]
) -> None:
    criteria = cast(list[dict[str, Any]], acceptance["criteria"])
    cases = cast(list[dict[str, Any]], test_plan["test_cases"])
    criterion_ids = {str(item["criterion_id"]) for item in criteria}
    referenced = {
        str(reference)
        for case in cases
        for reference in cast(list[object], case["acceptance_criteria_refs"])
    }
    if not referenced or not referenced.issubset(criterion_ids):
        raise ValueError("Regenerated UI TestPlan must reference only current acceptance criteria")
    known_rules = {
        str(reference)
        for criterion in criteria
        for reference in cast(list[object], criterion["business_rule_refs"])
    }
    for case in cases:
        case_rules = {str(value) for value in cast(list[object], case["business_rule_refs"])}
        if not case_rules.issubset(known_rules):
            raise ValueError(
                "Regenerated UI TestPlan references an unknown business rule: "
                f"{case['test_case_id']}"
            )
    for criterion in criteria:
        criterion_id = str(criterion["criterion_id"])
        test_refs = sorted(
            str(case["test_case_id"])
            for case in cases
            if criterion_id
            in {str(value) for value in cast(list[object], case["acceptance_criteria_refs"])}
        )
        if not test_refs:
            raise ValueError(
                f"Regenerated UI TestPlan leaves an acceptance criterion uncovered: {criterion_id}"
            )
        criterion["test_case_refs"] = test_refs
        criterion["assertion_type"] = "ui"


def _coverage(
    *,
    source_bundle: dict[str, Any],
    acceptance: dict[str, Any],
    test_plan: dict[str, Any],
    coverage_id: str,
) -> dict[str, Any]:
    source = cast(dict[str, Any], source_bundle["coverage_report"])
    rule_ids = [
        str(item["business_rule_id"]) for item in cast(list[dict[str, Any]], source["items"])
    ]
    tests = cast(list[dict[str, Any]], test_plan["test_cases"])
    criteria = cast(list[dict[str, Any]], acceptance["criteria"])
    items: list[dict[str, Any]] = []
    for rule_id in rule_ids:
        test_refs = sorted(
            str(item["test_case_id"])
            for item in tests
            if rule_id in cast(list[str], item["business_rule_refs"])
        )
        criterion_refs = sorted(
            str(item["criterion_id"])
            for item in criteria
            if rule_id in cast(list[str], item["business_rule_refs"])
        )
        verification_sources = [
            {
                "source_kind": "ui_test",
                "source_refs": [str(item["test_case_id"]), *criterion_refs],
                "assertion": " / ".join(
                    str(value) for value in cast(list[object], item.get("expected_results", []))
                ),
            }
            for item in tests
            if rule_id in cast(list[str], item["business_rule_refs"])
            and criterion_refs
            and cast(list[object], item.get("expected_results", []))
        ]
        items.append(
            {
                "business_rule_id": rule_id,
                "test_case_refs": test_refs,
                "criterion_refs": criterion_refs,
                "verification_sources": verification_sources,
                "status": "covered" if verification_sources else "uncovered",
            }
        )
    covered = sum(item["status"] == "covered" for item in items)
    return {
        "artifact_type": "BusinessCoverageReport",
        "schema_version": "v1",
        "coverage_report_id": coverage_id,
        "change_request_id": source["change_request_id"],
        "test_plan_id": test_plan["test_plan_id"],
        "acceptance_criteria_id": acceptance["acceptance_criteria_id"],
        "project_id": source["project_id"],
        "business_rule_count": len(items),
        "covered_rule_count": covered,
        "coverage_percent": covered * 100 / len(items),
        "items": items,
        "status": "passed" if covered == len(items) else "failed",
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _id(prefix: str, *values: str) -> str:
    material = "\0".join(values).encode()
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"
