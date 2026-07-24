"""Configuration-driven dual-entry planning for executable change loops."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from operamind.application.change_loop_case import ChangeLoopCase
from operamind.application.document_diff import (
    DocumentDiffRequest,
    DocumentDiffResult,
    DocumentDiffService,
)
from operamind.application.test_data_flow import build_test_data_plan_flows
from operamind.contracts import ContractCatalog
from operamind.domain.document_conventions import DocumentConvention
from operamind.infrastructure.code_graph import (
    CodeGraphScanner,
    GitRevisionEvidence,
    GitWorkspaceInspector,
    TextReplacement,
    WorkspaceScanner,
)
from operamind.infrastructure.documents import (
    DocumentCellChange,
    DocumentSignalExtractorRegistry,
    XlsxDocumentProposalWriter,
)
from operamind.profiles import ProfileCatalog


class ChangeLoopBlockedError(ValueError):
    """Raised when ambiguity or evidence mismatch requires user confirmation."""


class ChangeInputMode(StrEnum):
    DOCUMENTS = "documents"
    NATURAL_LANGUAGE = "natural_language"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class ChangeLoopPlanRequest:
    change_request_id: str
    project_id: str
    case_root: Path
    workspace_root: Path
    before_document: Path
    input_mode: ChangeInputMode
    after_document: Path | None = None
    requirement_text: str | None = None
    proposal_document: Path | None = None

    def __post_init__(self) -> None:
        if not self.change_request_id.strip() or not self.project_id.strip():
            raise ValueError("Change loop request identity must not be blank")
        if self.input_mode in {ChangeInputMode.NATURAL_LANGUAGE, ChangeInputMode.HYBRID} and (
            self.requirement_text is None or not self.requirement_text.strip()
        ):
            raise ValueError("Natural-language and hybrid input require requirement_text")
        if self.input_mode in {ChangeInputMode.DOCUMENTS, ChangeInputMode.HYBRID} and (
            self.after_document is None
        ):
            raise ValueError("Document and hybrid input require after_document")
        if self.input_mode is ChangeInputMode.NATURAL_LANGUAGE and self.proposal_document is None:
            raise ValueError("Natural-language input requires a proposal_document output")


@dataclass(frozen=True, slots=True)
class ChangeLoopPlan:
    request: ChangeLoopPlanRequest
    case: ChangeLoopCase
    git: GitRevisionEvidence
    document_diff: DocumentDiffResult
    artifacts: tuple[dict[str, Any], ...]
    replacements: tuple[TextReplacement, ...]
    allowed_edit_paths: frozenset[str]
    forbidden_paths: frozenset[str]

    def artifact(self, artifact_type: str) -> dict[str, Any]:
        matches = [value for value in self.artifacts if value["artifact_type"] == artifact_type]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one {artifact_type} Artifact")
        return matches[0]

    def write_artifacts(self, output_root: Path) -> tuple[Path, ...]:
        root = output_root.absolute()
        root.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        totals: dict[str, int] = {}
        for artifact in self.artifacts:
            artifact_type = str(artifact["artifact_type"])
            totals[artifact_type] = totals.get(artifact_type, 0) + 1
        indexes: dict[str, int] = {}
        for artifact in self.artifacts:
            artifact_type = str(artifact["artifact_type"])
            indexes[artifact_type] = indexes.get(artifact_type, 0) + 1
            suffix = f"-{indexes[artifact_type]}" if totals[artifact_type] > 1 else ""
            path = root / f"{_kebab(artifact_type)}{suffix}.json"
            path.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            written.append(path)
        return tuple(written)

    @property
    def code_graph_artifact(self) -> dict[str, Any]:
        return self.artifact("CodeGraphSnapshot")


class ChangeLoopPlanner:
    """Ground either input in Document Diff and a reviewed case configuration."""

    def __init__(
        self,
        *,
        repository_root: Path,
        contracts: ContractCatalog | None = None,
        profiles: ProfileCatalog | None = None,
    ) -> None:
        self._root = repository_root.resolve(strict=True)
        self._contracts = contracts or ContractCatalog.load(self._root / "contracts")
        self._profiles = profiles or ProfileCatalog.load(self._root / "profiles")
        self._proposal = XlsxDocumentProposalWriter()
        self._document_diff = DocumentDiffService(
            extractors=DocumentSignalExtractorRegistry.default(),
            contracts=self._contracts,
        )

    def plan(self, request: ChangeLoopPlanRequest) -> ChangeLoopPlan:
        case = ChangeLoopCase.load(request.case_root)
        if request.project_id != case.project_id:
            raise ChangeLoopBlockedError(
                f"Case project mismatch: expected={case.project_id} actual={request.project_id}"
            )
        expected_changes = _load_object(case.root / "expected-changes.json")
        operations = _document_operations(expected_changes)
        business_rules = tuple(
            copy.deepcopy(cast(list[dict[str, Any]], case.requirements["business_rules"]))
        )
        ambiguities = _requirement_ambiguities(request, case)
        change_request = _change_request_artifact(request, business_rules, ambiguities)
        self._contracts.validate_artifact(change_request)
        if ambiguities:
            raise ChangeLoopBlockedError(
                "Requirement needs confirmation: " + "; ".join(ambiguities)
            )

        pair = self._prepare_document_pair(request, operations)
        proposal = {
            "artifact_type": "DocumentChangeProposal",
            "schema_version": "v1",
            "proposal_id": _id("proposal", request.change_request_id),
            "change_request_id": request.change_request_id,
            "project_id": request.project_id,
            "source_document_ref": request.before_document.resolve(strict=True).as_uri(),
            "target_document_ref": pair.target_path.as_uri(),
            "source_content_digest": pair.source_content_digest,
            "target_content_digest": pair.target_content_digest,
            "status": "applied",
            "operations": [operation.to_artifact() for operation in operations],
        }
        self._contracts.validate_artifact(proposal)

        profile_path = self._root / str(case.document["convention_profile"])
        profile = _load_object(profile_path)
        self._profiles.validate_profile(profile)
        diff = self._document_diff.run(
            DocumentDiffRequest(
                project_id=request.project_id,
                domain=str(case.document["domain"]),
                fact_type=str(case.document["fact_type"]),
                source_snapshot_id=_id("snapshot-before", request.change_request_id),
                target_snapshot_id=_id("snapshot-after", request.change_request_id),
                before_path=request.before_document,
                after_path=pair.target_path,
            ),
            DocumentConvention.from_validated_profile(profile),
        )
        accepted_changes = _accepted_changes(
            diff,
            expected_changes,
            source_document_name=request.before_document.name,
            target_document_name=pair.target_path.name,
        )

        git = GitWorkspaceInspector().inspect(request.workspace_root)
        expected_revision = str(case.repository["base_revision"])
        if git.head_sha != expected_revision:
            raise ChangeLoopBlockedError(
                f"Workspace revision mismatch: expected={expected_revision} actual={git.head_sha}"
            )
        graph = self._scan_workspace(request, case, git)
        impact, confirmation, edit_packet = _impact_artifacts(
            request=request,
            case=case,
            git=git,
            graph=graph,
            changes=accepted_changes,
        )
        acceptance = _acceptance_artifact(request, case)
        test_plan = _test_plan_artifact(request, case, acceptance)
        test_data = _test_data_artifact(request, case, test_plan)
        coverage = _coverage_artifact(request, business_rules, acceptance, test_plan)

        artifacts = (
            change_request,
            proposal,
            *accepted_changes,
            graph,
            impact,
            confirmation,
            edit_packet,
            acceptance,
            test_plan,
            test_data,
            coverage,
        )
        for artifact in artifacts:
            self._contracts.validate_artifact(artifact)
        return ChangeLoopPlan(
            request=request,
            case=case,
            git=git,
            document_diff=diff,
            artifacts=artifacts,
            replacements=case.replacements,
            allowed_edit_paths=case.editable_paths,
            forbidden_paths=case.forbidden_paths,
        )

    def _prepare_document_pair(
        self,
        request: ChangeLoopPlanRequest,
        operations: tuple[DocumentCellChange, ...],
    ) -> Any:
        if request.input_mode is ChangeInputMode.NATURAL_LANGUAGE:
            assert request.proposal_document is not None
            return self._proposal.apply(
                source_path=request.before_document,
                target_path=request.proposal_document,
                changes=operations,
            )
        assert request.after_document is not None
        return self._proposal.verify_pair(
            source_path=request.before_document,
            target_path=request.after_document,
            changes=operations,
        )

    def _scan_workspace(
        self,
        request: ChangeLoopPlanRequest,
        case: ChangeLoopCase,
        git: GitRevisionEvidence,
    ) -> dict[str, Any]:
        profile_path = self._root / str(
            case.repository.get("code_profile", "profiles/code-framework-profile.example.json")
        )
        profile = _load_object(profile_path)
        self._profiles.validate_profile(profile)
        scan_roots = tuple(
            str(value)
            for value in cast(list[str], case.repository["scan_roots"])
            if (git.workspace_root / str(value)).is_dir()
        )
        if not scan_roots:
            raise ChangeLoopBlockedError("No configured Code Graph scan root exists")
        files = WorkspaceScanner().discover(
            workspace_root=git.workspace_root,
            scan_roots=scan_roots,
            excluded_globs=tuple(cast(list[str], profile["excluded_globs"])),
            languages=tuple(cast(list[str], profile["languages"])),
        )
        return (
            CodeGraphScanner()
            .scan(
                code_graph_snapshot_id=_id("code-graph", request.change_request_id),
                project_id=request.project_id,
                repository_id=str(case.repository["repository_id"]),
                repository_revision=git.head_sha,
                scan_roots=scan_roots,
                profile=profile,
                files=files,
            )
            .artifact
        )


def _document_operations(payload: dict[str, Any]) -> tuple[DocumentCellChange, ...]:
    operations: list[DocumentCellChange] = []
    for change_index, change in enumerate(cast(list[dict[str, Any]], payload["changes"]), start=1):
        for field_index, delta in enumerate(
            cast(list[dict[str, object]], change["field_deltas"]), start=1
        ):
            operations.append(
                DocumentCellChange.from_field_delta(
                    operation_id=f"document-op-{change_index}-{field_index}", delta=delta
                )
            )
    return tuple(operations)


def _requirement_ambiguities(
    request: ChangeLoopPlanRequest, case: ChangeLoopCase
) -> tuple[str, ...]:
    if request.input_mode is ChangeInputMode.DOCUMENTS:
        return ()
    text = (request.requirement_text or "").casefold()
    ambiguities: list[str] = []
    for check in cast(list[dict[str, Any]], case.requirements.get("required_intents", [])):
        tokens = [str(value).casefold() for value in cast(list[str], check.get("any_of", []))]
        patterns = cast(list[str], check.get("patterns", []))
        token_missing = bool(tokens) and not any(token in text for token in tokens)
        pattern_missing = bool(patterns) and not any(
            re.search(pattern, text) for pattern in patterns
        )
        if token_missing or pattern_missing:
            ambiguities.append(str(check["message"]))
    for conflict in cast(list[dict[str, Any]], case.requirements.get("conflicts", [])):
        tokens = [str(value).casefold() for value in cast(list[str], conflict["any_of"])]
        if any(token in text for token in tokens):
            ambiguities.append(str(conflict["message"]))
    return tuple(dict.fromkeys(ambiguities))


def _change_request_artifact(
    request: ChangeLoopPlanRequest,
    business_rules: tuple[dict[str, Any], ...],
    ambiguities: tuple[str, ...],
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_type": "ChangeRequest",
        "schema_version": "v1",
        "change_request_id": request.change_request_id,
        "project_id": request.project_id,
        "input_mode": request.input_mode.value,
        "business_rules": list(business_rules),
        "ambiguity_status": "needs_confirmation" if ambiguities else "clear",
        "confirmation_required": bool(ambiguities),
        "ambiguities": list(ambiguities),
    }
    if request.requirement_text is not None:
        artifact["requirement_text"] = request.requirement_text
    if request.input_mode in {ChangeInputMode.DOCUMENTS, ChangeInputMode.HYBRID}:
        assert request.after_document is not None
        artifact["source_document_ref"] = request.before_document.resolve(strict=True).as_uri()
        artifact["target_document_ref"] = request.after_document.resolve(strict=True).as_uri()
    return artifact


def _accepted_changes(
    result: DocumentDiffResult,
    expected: dict[str, Any],
    *,
    source_document_name: str,
    target_document_name: str,
) -> tuple[dict[str, Any], ...]:
    expected_values = {
        str(value["stable_key"]): value for value in cast(list[dict[str, Any]], expected["changes"])
    }
    actual_values = {value.stable_key: value for value in result.changes}
    if actual_values.keys() != expected_values.keys():
        raise ChangeLoopBlockedError("Document Diff identities do not match case expectation")
    accepted: list[dict[str, Any]] = []
    for stable_key in expected_values:
        actual = actual_values[stable_key]
        raw_expected = expected_values[stable_key]
        if (
            actual.fact_type != raw_expected["fact_type"]
            or actual.domain != raw_expected["domain"]
            or actual.change_type.value != raw_expected["change_type"]
            or actual.before is None
            or actual.after is None
        ):
            raise ChangeLoopBlockedError("Document Diff identity differs from case expectation")
        expected_deltas = cast(list[dict[str, Any]], raw_expected["field_deltas"])
        actual_changed_fields = {
            key
            for key in actual.before.values.keys() | actual.after.values.keys()
            if actual.before.values.get(key) != actual.after.values.get(key)
        }
        if actual_changed_fields != {str(value["field"]) for value in expected_deltas}:
            raise ChangeLoopBlockedError(
                "Document Diff changed fields differ from case expectation"
            )
        for delta in expected_deltas:
            field = str(delta["field"])
            if (
                actual.before.values.get(field) != delta["before"]
                or actual.after.values.get(field) != delta["after"]
            ):
                raise ChangeLoopBlockedError(f"Document Diff field differs from case: {field}")
        artifact = cast(
            dict[str, Any],
            _canonical_document_refs(
                actual.to_artifact(),
                source_document_name=source_document_name,
                target_document_name=target_document_name,
            ),
        )
        artifact["review_status"] = "accepted"
        accepted.append(artifact)
    return tuple(accepted)


def _canonical_document_refs(
    value: object, *, source_document_name: str, target_document_name: str
) -> object:
    if isinstance(value, str):
        prefix = f"{target_document_name}#"
        return (
            f"{source_document_name}#{value.removeprefix(prefix)}"
            if value.startswith(prefix)
            else value
        )
    if isinstance(value, list):
        normalized = [
            _canonical_document_refs(
                item,
                source_document_name=source_document_name,
                target_document_name=target_document_name,
            )
            for item in value
        ]
        if all(isinstance(item, str) for item in normalized):
            return list(dict.fromkeys(cast(list[str], normalized)))
        return normalized
    if isinstance(value, dict):
        return {
            key: _canonical_document_refs(
                item,
                source_document_name=source_document_name,
                target_document_name=target_document_name,
            )
            for key, item in value.items()
        }
    return value


def _impact_artifacts(
    *,
    request: ChangeLoopPlanRequest,
    case: ChangeLoopCase,
    git: GitRevisionEvidence,
    graph: dict[str, Any],
    changes: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    graph_paths = {str(value["path"]) for value in cast(list[dict[str, Any]], graph["files"])}
    missing = sorted(
        str(value["path"]) for value in case.impact_candidates if value["path"] not in graph_paths
    )
    if missing:
        raise ChangeLoopBlockedError(f"Code Graph is missing configured paths: {missing}")
    report_id = _id("impact-report", request.change_request_id)
    change_refs = [str(value["change_id"]) for value in changes]
    evidence_refs = sorted(
        {reference for value in changes for reference in cast(list[str], value["source_refs"])}
    )
    items: list[dict[str, Any]] = []
    for candidate in case.impact_candidates:
        path = str(candidate["path"])
        editable = path in case.editable_paths
        items.append(
            {
                "impact_item_id": _id("impact-item", report_id, path),
                "structured_change_refs": change_refs,
                "target_path": path,
                "target_symbols": list(cast(list[str], candidate["symbols"])),
                "impact_level": "high" if editable else "medium",
                "impact_score": 1.0 if editable else 0.8,
                "recommended_action": "modify" if editable else "review_only",
                "rationale": str(candidate["reason"]),
                "evidence_refs": evidence_refs,
                "graph_path_refs": [],
                "test_file_refs": [],
                "requires_confirmation": False,
                "unknowns": [],
            }
        )
    ui_refs = [str(value["test_case_id"]) for value in case.test_cases if value["level"] == "ui"]
    impact = {
        "artifact_type": "ImpactReport",
        "schema_version": "v1",
        "impact_report_id": report_id,
        "analysis_case_id": request.change_request_id,
        "project_id": request.project_id,
        "document_snapshot_id": _id("snapshot-after", request.change_request_id),
        "context_package_id": _id("context", request.change_request_id),
        "code_graph_snapshot_id": graph["code_graph_snapshot_id"],
        "repository_revision": git.head_sha,
        "analysis_policy_version": str(case.analysis["policy_version"]),
        "status": "confirmed",
        "summary": str(case.analysis["summary"]),
        "items": items,
        "ui_impact_status": "impacted" if ui_refs else "not_impacted",
        "required_ui_scenario_refs": ui_refs,
        "blocking_unknowns": [],
    }
    confirmation_id = _id("impact-confirmation", request.change_request_id)
    confirmation = {
        "artifact_type": "ImpactConfirmation",
        "schema_version": "v1",
        "confirmation_id": confirmation_id,
        "impact_report_id": report_id,
        "confirmed_by": str(case.review["reviewed_by"]),
        "approved_item_ids": [str(value["impact_item_id"]) for value in items],
        "rejected_item_ids": [],
        "user_note": "Reused the approved case judgment; deterministic matches auto-pass.",
        "confirmed_at": case.review["reviewed_at"],
    }
    editable_items = [value for value in items if value["recommended_action"] == "modify"]
    constraints_by_path = {
        str(value["path"]): value
        for value in cast(list[dict[str, Any]], case.edit.get("allowed_items", []))
    }
    edit_packet = {
        "artifact_type": "CopilotEditPacket",
        "schema_version": "v1",
        "edit_packet_id": _id("edit-packet", request.change_request_id),
        "impact_report_id": report_id,
        "confirmation_id": confirmation_id,
        "project_id": request.project_id,
        "repository_id": str(case.repository["repository_id"]),
        "base_repository_revision": git.head_sha,
        "editable_files": [str(value["target_path"]) for value in editable_items],
        "read_only_files": sorted(
            str(value["target_path"])
            for value in items
            if value["recommended_action"] == "review_only"
        ),
        "test_files": [],
        "forbidden_globs": list(case.forbidden_paths),
        "allowed_items": [
            {
                "impact_item_id": value["impact_item_id"],
                "target_path": value["target_path"],
                "target_symbols": value["target_symbols"],
                "allowed_actions": ["modify"],
                "business_summary": str(
                    constraints_by_path.get(str(value["target_path"]), {}).get(
                        "business_summary", case.analysis["summary"]
                    )
                ),
                "implementation_constraints": list(
                    cast(
                        list[str],
                        constraints_by_path.get(str(value["target_path"]), {}).get(
                            "implementation_constraints", []
                        ),
                    )
                ),
            }
            for value in editable_items
        ],
        "required_ui_scenario_refs": ui_refs,
        "out_of_scope_policy": "stop_and_reanalyze",
        "must_not_fetch_context_package": True,
    }
    return impact, confirmation, edit_packet


def _acceptance_artifact(request: ChangeLoopPlanRequest, case: ChangeLoopCase) -> dict[str, Any]:
    return {
        "artifact_type": "AcceptanceCriteria",
        "schema_version": "v1",
        "acceptance_criteria_id": _id("acceptance", request.change_request_id),
        "change_request_id": request.change_request_id,
        "project_id": request.project_id,
        "criteria": copy.deepcopy(case.acceptance_criteria),
    }


def _test_plan_artifact(
    request: ChangeLoopPlanRequest,
    case: ChangeLoopCase,
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    cases = copy.deepcopy(case.test_cases)
    criterion_ids = {
        str(value["criterion_id"]) for value in cast(list[dict[str, Any]], acceptance["criteria"])
    }
    referenced = {
        str(reference)
        for value in cases
        for reference in cast(list[str], value["acceptance_criteria_refs"])
    }
    if criterion_ids != referenced:
        raise ValueError("Test Plan and Acceptance Criteria coverage differ")
    return {
        "artifact_type": "TestPlan",
        "schema_version": "v1",
        "test_plan_id": _id("test-plan", request.change_request_id),
        "change_request_id": request.change_request_id,
        "project_id": request.project_id,
        "status": "ready",
        "test_cases": cases,
        "blocking_reasons": [],
    }


def _test_data_artifact(
    request: ChangeLoopPlanRequest,
    case: ChangeLoopCase,
    test_plan: dict[str, Any],
) -> dict[str, Any]:
    flows, blocking_reasons = build_test_data_plan_flows(case)
    return {
        "artifact_type": "TestDataPlan",
        "schema_version": "v1",
        "test_data_plan_id": _id("test-data-plan", request.change_request_id),
        "test_plan_id": test_plan["test_plan_id"],
        "project_id": request.project_id,
        "status": "blocked" if blocking_reasons else "ready",
        "data_sets": copy.deepcopy(case.data_sets),
        "generation_flows": flows,
        "blocking_reasons": blocking_reasons,
    }


def _coverage_artifact(
    request: ChangeLoopPlanRequest,
    rules: tuple[dict[str, Any], ...],
    acceptance: dict[str, Any],
    test_plan: dict[str, Any],
) -> dict[str, Any]:
    criteria = cast(list[dict[str, Any]], acceptance["criteria"])
    cases = cast(list[dict[str, Any]], test_plan["test_cases"])
    items: list[dict[str, Any]] = []
    for rule in rules:
        rule_id = str(rule["business_rule_id"])
        test_refs = sorted(
            str(case["test_case_id"])
            for case in cases
            if rule_id in cast(list[str], case["business_rule_refs"])
        )
        criterion_refs = sorted(
            str(criterion["criterion_id"])
            for criterion in criteria
            if rule_id in cast(list[str], criterion["business_rule_refs"])
        )
        covered = bool(test_refs and criterion_refs)
        items.append(
            {
                "business_rule_id": rule_id,
                "test_case_refs": test_refs,
                "criterion_refs": criterion_refs,
                "status": "covered" if covered else "uncovered",
            }
        )
    covered_count = sum(value["status"] == "covered" for value in items)
    percent = covered_count * 100 / len(items)
    return {
        "artifact_type": "BusinessCoverageReport",
        "schema_version": "v1",
        "coverage_report_id": _id("coverage", request.change_request_id),
        "change_request_id": request.change_request_id,
        "test_plan_id": test_plan["test_plan_id"],
        "acceptance_criteria_id": acceptance["acceptance_criteria_id"],
        "project_id": request.project_id,
        "business_rule_count": len(items),
        "covered_rule_count": covered_count,
        "coverage_percent": percent,
        "items": items,
        "status": "passed" if covered_count == len(items) else "failed",
    }


def _load_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _id(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode()
    return f"{prefix}-{sha256(material).hexdigest()[:24]}"


def _kebab(value: str) -> str:
    output: list[str] = []
    for index, character in enumerate(value):
        if character.isupper() and index:
            output.append("-")
        output.append(character.casefold())
    return "".join(output)
