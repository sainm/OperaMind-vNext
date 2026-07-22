"""Generate a reviewable executable-case draft from new change input."""

from __future__ import annotations

import copy
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from operamind.application.change_loop_case import ChangeLoopCase
from operamind.application.document_diff import (
    DocumentDiffRequest,
    DocumentDiffResult,
    DocumentDiffService,
)
from operamind.contracts import ContractCatalog
from operamind.domain import (
    BrowserActionKind,
    BrowserAssertionKind,
    BrowserFailureCategory,
    CanonicalFactMapper,
    LocatorStrategy,
)
from operamind.domain.document_conventions import (
    ConventionMatcher,
    DocumentConvention,
    MatchStatus,
)
from operamind.infrastructure.code_graph import (
    CodeGraphScanner,
    GitRevisionEvidence,
    GitWorkspaceInspector,
    WorkspaceScanner,
)
from operamind.infrastructure.documents import (
    DocumentCellChange,
    DocumentSignalExtractorRegistry,
    XlsxDocumentProposalWriter,
)
from operamind.infrastructure.draft_generation import DraftGenerationProvider
from operamind.profiles import ProfileCatalog


class ChangeDraftBlockedError(ValueError):
    """Raised when deterministic evidence cannot support a safe AI proposal."""


class ChangeDraftInputMode(StrEnum):
    DOCUMENTS = "documents"
    NATURAL_LANGUAGE = "natural_language"


@dataclass(frozen=True, slots=True)
class ChangeDraftRequest:
    draft_id: str
    case_id: str
    project_id: str
    repository_id: str
    workspace_root: Path
    before_document: Path
    input_mode: ChangeDraftInputMode
    application_root: str
    scan_roots: tuple[str, ...]
    code_profile: str
    document_profile: str
    output_root: Path
    after_document: Path | None = None
    requirement_text: str | None = None
    max_candidate_files: int = 12

    def __post_init__(self) -> None:
        identities = (self.draft_id, self.case_id, self.project_id, self.repository_id)
        if any(not value.strip() for value in identities):
            raise ValueError("Change draft identity fields must not be blank")
        if not self.scan_roots or len(self.scan_roots) != len(set(self.scan_roots)):
            raise ValueError("Change draft scan_roots must be non-empty and unique")
        if self.input_mode is ChangeDraftInputMode.DOCUMENTS and self.after_document is None:
            raise ValueError("Documents draft generation requires after_document")
        if self.input_mode is ChangeDraftInputMode.NATURAL_LANGUAGE and (
            self.requirement_text is None or not self.requirement_text.strip()
        ):
            raise ValueError("Natural-language draft generation requires requirement_text")
        if not 1 <= self.max_candidate_files <= 50:
            raise ValueError("max_candidate_files must be between 1 and 50")


@dataclass(frozen=True, slots=True)
class ChangeDraftGenerationResult:
    session: dict[str, Any]
    session_path: Path
    proposed_after_document: Path
    expected_changes_path: Path
    case_config_path: Path

    @property
    def status(self) -> str:
        return str(self.session["status"])


@dataclass(frozen=True, slots=True)
class ChangeDraftHandoffResult:
    handoff_root: Path
    context_path: Path
    prompt_path: Path
    response_schema_path: Path
    response_path: Path


class ChangeDraftService:
    """Build bounded context, validate AI output and persist an unapproved draft."""

    def __init__(
        self,
        *,
        repository_root: Path,
        provider: DraftGenerationProvider | None = None,
    ) -> None:
        self._root = repository_root.resolve(strict=True)
        self._provider = provider
        self._contracts = ContractCatalog.load(self._root / "contracts")
        self._profiles = ProfileCatalog.load(self._root / "profiles")
        self._extractors = DocumentSignalExtractorRegistry.default()
        self._diff = DocumentDiffService(
            extractors=self._extractors,
            contracts=self._contracts,
        )
        self._proposal = XlsxDocumentProposalWriter()

    def prepare_handoff(
        self,
        request: ChangeDraftRequest,
        *,
        handoff_root: Path,
    ) -> ChangeDraftHandoffResult:
        """Write a bounded Copilot handoff without invoking any model."""

        output = handoff_root.absolute()
        if output.exists() and any(output.iterdir()):
            raise FileExistsError(f"Change draft handoff is not empty: {output}")
        output.mkdir(parents=True, exist_ok=True)
        before = request.before_document.resolve(strict=True)
        git = GitWorkspaceInspector().inspect(request.workspace_root)
        code_profile = _load_object(self._root / request.code_profile)
        document_profile = _load_object(self._root / request.document_profile)
        self._profiles.validate_profile(code_profile)
        self._profiles.validate_profile(document_profile)
        convention = DocumentConvention.from_validated_profile(document_profile)
        files, graph = self._scan(request, git, code_profile)
        source_facts = self._source_facts(before, convention)
        initial_diff: DocumentDiffResult | None = None
        if request.input_mode is ChangeDraftInputMode.DOCUMENTS:
            assert request.after_document is not None
            initial_diff = self._run_diff(
                request,
                before=before,
                after=request.after_document.resolve(strict=True),
                suffix="handoff",
                convention=convention,
            )
            if not initial_diff.changes:
                raise ChangeDraftBlockedError("Before/after documents contain no semantic changes")
        context = self._context(
            request=request,
            git=git,
            source_facts=source_facts,
            initial_diff=initial_diff,
            graph=graph,
            files=files,
        )
        context_path = _write_json(output / "generation-context.json", context)
        prompt = _draft_prompt(
            context,
            case_schema=_load_object(self._root / "golden-dataset/change-loop-case.schema.json"),
        )
        prompt_path = output / "draft-prompt.json"
        prompt_path.write_text(prompt + "\n", encoding="utf-8")
        response_schema_path = output / "change-draft-ai-response.schema.json"
        shutil.copyfile(
            self._root / "drafts/schemas/change-draft-ai-response.schema.json",
            response_schema_path,
        )
        response_path = output / "ai-response.json"
        (output / "COPILOT-INSTRUCTIONS.md").write_text(
            """# OperaMind Draft Handoff

Use only `draft-prompt.json` and the evidence embedded in it. Generate one JSON object that
conforms to `change-draft-ai-response.schema.json` and save it as `ai-response.json` in this
directory. Do not modify any other file. Do not approve the case. Preserve exact code
replacement preimages from the supplied candidate contents. Add selectable questions for every
material uncertainty.
""",
            encoding="utf-8",
        )
        _write_json(
            output / "handoff-manifest.json",
            {
                "schema_version": "v1",
                "draft_id": request.draft_id,
                "case_id": request.case_id,
                "project_id": request.project_id,
                "input_mode": request.input_mode.value,
                "base_revision": git.head_sha,
                "workspace_was_clean": True,
                "response_file": response_path.name,
                "allowed_output_files": [response_path.name],
            },
        )
        return ChangeDraftHandoffResult(
            handoff_root=output,
            context_path=context_path,
            prompt_path=prompt_path,
            response_schema_path=response_schema_path,
            response_path=response_path,
        )

    def generate(self, request: ChangeDraftRequest) -> ChangeDraftGenerationResult:
        if self._provider is None:
            raise ValueError("Change draft generation requires a Draft provider")
        output = request.output_root.absolute()
        if output.exists() and any(output.iterdir()):
            raise FileExistsError(f"Change draft output is not empty: {output}")
        output.mkdir(parents=True, exist_ok=True)
        before = request.before_document.resolve(strict=True)
        git = GitWorkspaceInspector().inspect(request.workspace_root)
        code_profile = _load_object(self._root / request.code_profile)
        document_profile = _load_object(self._root / request.document_profile)
        self._profiles.validate_profile(code_profile)
        self._profiles.validate_profile(document_profile)
        convention = DocumentConvention.from_validated_profile(document_profile)
        files, graph = self._scan(request, git, code_profile)
        source_facts = self._source_facts(before, convention)

        initial_diff: DocumentDiffResult | None = None
        if request.input_mode is ChangeDraftInputMode.DOCUMENTS:
            assert request.after_document is not None
            initial_diff = self._run_diff(
                request,
                before=before,
                after=request.after_document.resolve(strict=True),
                suffix="input",
                convention=convention,
            )
            if not initial_diff.changes:
                raise ChangeDraftBlockedError("Before/after documents contain no semantic changes")

        context = self._context(
            request=request,
            git=git,
            source_facts=source_facts,
            initial_diff=initial_diff,
            graph=graph,
            files=files,
        )
        context_path = _write_json(output / "generation-context.json", context)
        prompt = _draft_prompt(
            context,
            case_schema=_load_object(self._root / "golden-dataset/change-loop-case.schema.json"),
        )
        response = self._provider.generate(
            prompt=prompt,
            workspace_root=git.workspace_root,
            output_root=output / "provider",
        )
        payload = copy.deepcopy(response.payload)
        raw_case = payload.get("case")
        if not isinstance(raw_case, dict):
            raise ValueError("AI draft response case must be an object")
        case_payload = self._case_payload(request, git, cast(dict[str, Any], raw_case))

        documents_root = output / "documents"
        documents_root.mkdir(parents=True, exist_ok=True)
        proposed_after = documents_root / before.name
        if request.input_mode is ChangeDraftInputMode.DOCUMENTS:
            assert request.after_document is not None
            if payload["document_operations"]:
                raise ValueError("Documents-mode AI response must not redefine document operations")
            shutil.copyfile(request.after_document.resolve(strict=True), proposed_after)
        else:
            operations = _document_operations(
                cast(list[dict[str, Any]], payload["document_operations"]),
                source_document=before.name,
            )
            if not operations:
                raise ValueError("Natural-language AI response requires document operations")
            self._proposal.apply(
                source_path=before,
                target_path=proposed_after,
                changes=operations,
            )

        verified_diff = self._run_diff(
            request,
            before=before,
            after=proposed_after,
            suffix="proposal",
            convention=convention,
        )
        if not verified_diff.changes:
            raise ValueError("AI document proposal produced no semantic changes")
        if initial_diff is not None and _change_signatures(initial_diff) != _change_signatures(
            verified_diff
        ):
            raise ValueError("Copied after document no longer matches the initial semantic Diff")

        expected_changes = _expected_changes(request.case_id, verified_diff)
        self._validate_case(
            request=request,
            case_payload=case_payload,
            files={value.path: value.content.decode("utf-8", errors="strict") for value in files},
            graph=graph,
            change_source_refs={
                reference for change in verified_diff.changes for reference in change.source_refs
            },
            output=output,
        )
        questions = _normalized_questions(
            cast(list[dict[str, Any]], payload["questions"]),
            cast(dict[str, str], payload["confidence"]),
        )
        steps = _step_statuses(questions)
        status = "awaiting_confirmation" if questions else "ready_for_approval"
        session: dict[str, Any] = {
            "schema_version": "v1",
            "draft_id": request.draft_id,
            "case_id": request.case_id,
            "project_id": request.project_id,
            "status": status,
            "input_mode": request.input_mode.value,
            "input": {
                "before_document": str(before),
                "after_document": (
                    str(request.after_document.resolve(strict=True))
                    if request.after_document is not None
                    else None
                ),
                "requirement_text": request.requirement_text,
            },
            "repository": {
                "workspace_root": str(git.workspace_root),
                "repository_id": request.repository_id,
                "base_revision": git.head_sha,
                "remote_url": git.remote_url,
            },
            "provider": {
                "provider_id": response.provider_id,
                "response_path": str(response.response_path),
            },
            "artifacts": {
                "generation_context": str(context_path),
                "proposed_after_document": str(proposed_after),
                "expected_changes": str(output / "expected-changes.json"),
                "case_config": str(output / "change-loop-case.json"),
            },
            "confidence": payload["confidence"],
            "proposal": {
                "case": case_payload,
                "document_operations": payload["document_operations"],
            },
            "steps": steps,
            "questions": questions,
            "answers": [],
        }
        case_path = _write_json(output / "change-loop-case.json", case_payload)
        expected_path = _write_json(output / "expected-changes.json", expected_changes)
        session_path = _write_json(output / "draft-session.json", session)
        return ChangeDraftGenerationResult(
            session=session,
            session_path=session_path,
            proposed_after_document=proposed_after,
            expected_changes_path=expected_path,
            case_config_path=case_path,
        )

    def _scan(
        self,
        request: ChangeDraftRequest,
        git: GitRevisionEvidence,
        profile: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        scan_roots = tuple(
            value for value in request.scan_roots if (git.workspace_root / value).is_dir()
        )
        if not scan_roots:
            raise ChangeDraftBlockedError("No configured scan root exists in the workspace")
        files = WorkspaceScanner().discover(
            workspace_root=git.workspace_root,
            scan_roots=scan_roots,
            excluded_globs=tuple(cast(list[str], profile["excluded_globs"])),
            languages=tuple(cast(list[str], profile["languages"])),
        )
        graph = (
            CodeGraphScanner()
            .scan(
                code_graph_snapshot_id=_identifier("draft-code-graph", request.draft_id),
                project_id=request.project_id,
                repository_id=request.repository_id,
                repository_revision=git.head_sha,
                scan_roots=scan_roots,
                profile=profile,
                files=files,
            )
            .artifact
        )
        return files, graph

    def _source_facts(self, path: Path, convention: DocumentConvention) -> list[dict[str, Any]]:
        match = ConventionMatcher().match(convention, self._extractors.extract(path))
        if match.status is not MatchStatus.AUTO_MATCHED or match.selected_variant_id is None:
            raise ChangeDraftBlockedError(
                f"Document Convention requires confirmation: {match.reason}"
            )
        variant = next(
            value for value in convention.variants if value.variant_id == match.selected_variant_id
        )
        mapper = CanonicalFactMapper()
        facts: list[dict[str, Any]] = []
        for record in self._extractors.extract_records(path, variant):
            mapped = mapper.map_record(
                convention=convention,
                match=match,
                fact_type="screen_element",
                record=record,
            )
            if mapped.fact is None:
                raise ChangeDraftBlockedError(
                    f"Canonical source record requires confirmation: {record.record_ref}"
                )
            facts.append(
                {
                    "stable_key": mapped.fact.stable_key,
                    "values": dict(mapped.fact.values),
                    "source_refs": list(mapped.fact.source_refs),
                }
            )
        if not facts:
            raise ChangeDraftBlockedError("Source document contains no canonical facts")
        return facts

    def _run_diff(
        self,
        request: ChangeDraftRequest,
        *,
        before: Path,
        after: Path,
        suffix: str,
        convention: DocumentConvention,
    ) -> DocumentDiffResult:
        return self._diff.run(
            DocumentDiffRequest(
                project_id=request.project_id,
                domain="ui",
                fact_type="screen_element",
                source_snapshot_id=_identifier("draft-before", request.draft_id, suffix),
                target_snapshot_id=_identifier("draft-after", request.draft_id, suffix),
                before_path=before,
                after_path=after,
            ),
            convention,
        )

    def _context(
        self,
        *,
        request: ChangeDraftRequest,
        git: GitRevisionEvidence,
        source_facts: list[dict[str, Any]],
        initial_diff: DocumentDiffResult | None,
        graph: dict[str, Any],
        files: tuple[Any, ...],
    ) -> dict[str, Any]:
        changes = (
            [change.to_artifact() for change in initial_diff.changes]
            if initial_diff is not None
            else []
        )
        terms = _anchor_terms(
            request.requirement_text or "",
            request.before_document.name,
            source_facts,
            changes,
        )
        summaries = _rank_files(graph, files, terms)
        shortlisted = summaries[: request.max_candidate_files]
        content_by_path = {
            value.path: value.content.decode("utf-8", errors="strict") for value in files
        }
        return {
            "schema_version": "v1",
            "draft_id": request.draft_id,
            "case_id": request.case_id,
            "project_id": request.project_id,
            "input_mode": request.input_mode.value,
            "requirement_text": request.requirement_text,
            "source_document": {
                "name": request.before_document.name,
                "canonical_facts": source_facts,
            },
            "document_changes": changes,
            "repository": {
                "repository_id": request.repository_id,
                "base_revision": git.head_sha,
                "application_root": request.application_root,
                "scan_roots": list(request.scan_roots),
                "code_profile": request.code_profile,
            },
            "code_index": summaries,
            "candidate_file_contents": [
                {
                    "path": value["path"],
                    "role": value["role"],
                    "content": content_by_path[str(value["path"])],
                }
                for value in shortlisted
            ],
            "test_index": [value for value in summaries if value["role"] == "test"],
            "constraints": {
                "exact_replacements_only": True,
                "new_files_forbidden": True,
                "candidate_paths_must_come_from_code_index": True,
                "source_and_api_checks_must_be_deterministic": True,
                "ui_scenarios_must_use_visible_business_assertions": True,
            },
        }

    def _case_payload(
        self,
        request: ChangeDraftRequest,
        git: GitRevisionEvidence,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        value = copy.deepcopy(raw)
        value.update(
            {
                "schema_version": "v1",
                "case_id": request.case_id,
                "project_id": request.project_id,
                "repository": {
                    "repository_id": request.repository_id,
                    "base_revision": git.head_sha,
                    "application_root": request.application_root,
                    "scan_roots": list(request.scan_roots),
                    "code_profile": request.code_profile,
                },
                "document": {
                    "domain": "ui",
                    "fact_type": "screen_element",
                    "convention_profile": request.document_profile,
                },
                "review": {
                    "review_status": "draft",
                    "reviewed_by": "pending-review",
                    "reviewed_at": "1970-01-01T00:00:00Z",
                },
            }
        )
        if request.requirement_text is not None:
            requirements = value.get("requirements")
            if isinstance(requirements, dict):
                requirements["canonical_requirement"] = request.requirement_text.strip()
        return value

    def _validate_case(
        self,
        *,
        request: ChangeDraftRequest,
        case_payload: dict[str, Any],
        files: dict[str, str],
        graph: dict[str, Any],
        change_source_refs: set[str],
        output: Path,
    ) -> None:
        case = ChangeLoopCase.from_payload(
            root=output,
            payload=case_payload,
            require_approved=False,
            schema_path=self._root / "golden-dataset/change-loop-case.schema.json",
        )
        referenced_paths = {
            *(str(value["path"]) for value in case.impact_candidates),
            *(
                str(value["path"])
                for value in cast(list[dict[str, Any]], case.execution["source_tests"])
            ),
        }
        missing = sorted(referenced_paths - files.keys())
        if missing:
            raise ValueError(f"AI draft references paths outside the Code Index: {missing}")
        for replacement in case.replacements:
            content = files[replacement.path]
            if content.count(replacement.before) != 1:
                raise ValueError(
                    f"AI draft replacement preimage must occur exactly once: {replacement.path}"
                )
        rule_refs = {
            str(reference)
            for rule in cast(list[dict[str, Any]], case.requirements["business_rules"])
            for reference in cast(list[str], rule["source_refs"])
        }
        if not rule_refs or not rule_refs.issubset(change_source_refs):
            raise ValueError(
                "AI draft business rules must reference only the verified document changes"
            )
        semantic_issues: list[str] = []
        validations: tuple[Callable[[], None], ...] = (
            lambda: _validate_runtime_endpoints(case, graph),
            lambda: _validate_executable_test_data(case, files),
            lambda: _validate_business_result_assertions(case),
            lambda: _validate_api_branches(case),
        )
        for validation in validations:
            try:
                validation()
            except ValueError as error:
                semantic_issues.append(str(error))
        if semantic_issues:
            raise ValueError("AI draft semantic validation failed: " + "; ".join(semantic_issues))


def _draft_prompt(context: dict[str, Any], *, case_schema: dict[str, Any]) -> str:
    envelope = {
        "task": (
            "Generate a complete, unapproved OperaMind change-loop case. Use only supplied "
            "document facts, Code Index paths and candidate file contents. Propose exact text "
            "replacements with unique preimages, deterministic source/API checks, test data, "
            "acceptance criteria and executable business-visible browser scenarios. For a "
            "natural-language input, also propose exact XLSX cell operations. For documents "
            "input, document_operations must be empty. Do not approve the case. Every material "
            "uncertainty must be an explicit step question with selectable options; "
            "high-confidence sections may have no question. Return JSON only."
        ),
        "response_case_schema": case_schema,
        "runtime_execution_contract": _runtime_execution_contract(),
        "context": context,
    }
    return json.dumps(envelope, ensure_ascii=False, indent=2)


def _validate_runtime_endpoints(case: ChangeLoopCase, graph: dict[str, Any]) -> None:
    exposed = {
        str(edge["to_ref"])
        for edge in cast(list[dict[str, Any]], graph.get("edges", []))
        if edge.get("edge_type") == "exposes"
        and edge.get("resolution_status") == "external"
        and edge.get("extractor") == "spring_endpoint"
    }
    execution = case.execution
    requests = [
        cast(dict[str, Any], execution["health_request"]),
        *(
            cast(dict[str, Any], value["request"])
            for value in cast(list[dict[str, Any]], execution["api_tests"])
        ),
        *(
            cast(dict[str, Any], value["request"])
            for value in cast(list[dict[str, Any]], execution["setup_requests"])
        ),
    ]
    requested = {_http_endpoint_ref(value) for value in requests}
    requested.update(
        f"http:GET:{urlsplit(str(value['trigger_path'])).path}"
        for value in cast(list[dict[str, Any]], execution["browser_scenarios"])
    )
    missing = sorted(requested - exposed)
    if missing:
        raise ValueError(f"AI draft runtime endpoints are absent from the Code Graph: {missing}")


def _validate_executable_test_data(case: ChangeLoopCase, files: dict[str, str]) -> None:
    setup_requests = cast(list[dict[str, Any]], case.execution["setup_requests"])
    invalid: list[str] = []
    for data_set in case.data_sets:
        actions = cast(list[dict[str, Any]], data_set["setup_actions"])
        if not actions:
            invalid.append(str(data_set["test_data_id"]))
            continue
        if setup_requests:
            continue
        if any(
            not isinstance(action.get("target"), str) or action.get("target") not in files
            for action in actions
        ):
            invalid.append(str(data_set["test_data_id"]))
    if invalid:
        raise ValueError(
            "AI draft test data without setup requests must bind an existing fixture path: "
            f"{sorted(invalid)}"
        )


def _validate_business_result_assertions(case: ChangeLoopCase) -> None:
    invalid: list[str] = []
    for scenario in cast(list[dict[str, Any]], case.execution["browser_scenarios"]):
        action_locators = {
            _locator_identity(cast(dict[str, Any], action["locator"]))
            for action in cast(list[dict[str, Any]], scenario["actions"])
        }
        assertions = cast(list[dict[str, Any]], scenario["assertions"])
        has_business_result = any(
            str(assertion["kind"]) in {"text_equals", "text_contains", "count_equals"}
            and _locator_identity(cast(dict[str, Any], assertion["locator"])) not in action_locators
            and bool(
                str(cast(dict[str, Any], assertion["expected"]).get("value", "")).strip()
            )
            for assertion in assertions
        )
        if not has_business_result:
            invalid.append(str(scenario["scenario_id"]))
    if invalid:
        raise ValueError(
            "AI draft UI scenarios must assert a downstream business result, "
            f"not only control state: {sorted(invalid)}"
        )


def _validate_api_branches(case: ChangeLoopCase) -> None:
    rule_text = " ".join(
        str(value["text"]).casefold()
        for value in cast(list[dict[str, Any]], case.requirements["business_rules"])
    )
    queries = [
        cast(dict[str, Any], cast(dict[str, Any], value["request"]).get("query", {}))
        for value in cast(list[dict[str, Any]], case.execution["api_tests"])
    ]
    status_values = [value for query in queries for key, value in query.items() if "status" in key]
    requires_blank = "blank" in rule_text and "null" in rule_text and "normaliz" in rule_text
    if requires_blank and not any(value is None or value == "" for value in status_values):
        raise ValueError("AI draft promises blank normalization but has no blank API branch")
    preservation_terms = ("preserv", "unchang", "pass through", "passed through")
    requires_nonblank = "nonblank" in rule_text and any(
        value in rule_text for value in preservation_terms
    )
    if requires_nonblank and not any(
        isinstance(value, str) and bool(value.strip()) for value in status_values
    ):
        raise ValueError(
            "AI draft promises to preserve nonblank input but has no nonblank API branch"
        )


def _http_endpoint_ref(request: dict[str, Any]) -> str:
    return f"http:{str(request.get('method', 'GET')).upper()}:{request['path']}"


def _locator_identity(locator: dict[str, Any]) -> tuple[str, str]:
    return str(locator["strategy"]), str(locator["value"])


def _runtime_execution_contract() -> dict[str, Any]:
    """Expose strict runtime-only UI shapes that the broad case schema cannot express."""

    return {
        "case_validation_rules": [
            "Every health/API/setup/browser path must exist as a Spring endpoint in Code Graph",
            "Test data must use executable setup_requests or bind an existing fixture path",
            "Every UI scenario must assert a downstream business result, not only input state",
            "Blank normalization requires a blank API test branch",
            "A promise to preserve nonblank input requires a distinct nonblank API test branch",
        ],
        "browser_scenario": {
            "exact_fields": [
                "scenario_id",
                "trigger_path",
                "actions",
                "assertions",
                "redaction_locators",
                "preflight_assertions",
            ],
            "rule": "scenario_id must exactly equal its UI test_case_id",
        },
        "browser_action": {
            "exact_fields": ["action_id", "kind", "locator", "value"],
            "kind_values": [value.value for value in BrowserActionKind],
            "value_required_for": ["fill", "select_option"],
            "value_forbidden_for": ["click", "check", "uncheck"],
        },
        "browser_assertion": {
            "exact_fields": [
                "assertion_id",
                "kind",
                "locator",
                "expected",
                "failure_category",
            ],
            "kind_values": [value.value for value in BrowserAssertionKind],
            "expected_required_for": [
                "text_equals",
                "text_contains",
                "value_equals",
                "count_equals",
            ],
            "expected_forbidden_for": ["visible", "hidden", "checked", "unchecked"],
            "failure_category_values": [
                value.value
                for value in BrowserFailureCategory
                if value
                not in {
                    BrowserFailureCategory.ENVIRONMENT,
                    BrowserFailureCategory.BLOCKED,
                }
            ],
        },
        "browser_locator": {
            "concrete_fields": ["strategy", "value", "name", "exact"],
            "strategy_values": [value.value for value in LocatorStrategy],
            "safe_css_rule": "Use one stable ID, class, or attribute selector only",
        },
        "browser_value": {
            "exact_fields": ["source", "value"],
            "source_values": ["literal", "env"],
            "value_type": "string",
        },
        "minimal_example": {
            "scenario_id": "same-as-ui-test-case-id",
            "trigger_path": "/relative/path",
            "actions": [
                {
                    "action_id": "select-status",
                    "kind": "select_option",
                    "locator": {"strategy": "css", "value": "#status"},
                    "value": {"source": "literal", "value": "差戻し"},
                }
            ],
            "assertions": [
                {
                    "assertion_id": "filtered-result-count",
                    "kind": "count_equals",
                    "locator": {"strategy": "css", "value": "#results tbody tr"},
                    "expected": {"source": "literal", "value": "2"},
                    "failure_category": "business_assertion",
                }
            ],
            "redaction_locators": [],
            "preflight_assertions": [],
        },
    }


def _document_operations(
    raw: list[dict[str, Any]], *, source_document: str
) -> tuple[DocumentCellChange, ...]:
    operations = tuple(
        DocumentCellChange.from_field_delta(
            operation_id=str(value["operation_id"]),
            delta={
                "field": value["field"],
                "before": value.get("before"),
                "after": value.get("after"),
                "source_ref": value["source_ref"],
            },
        )
        for value in raw
    )
    if any(value.document != source_document for value in operations):
        raise ValueError("AI document operation references another source document")
    return operations


def _expected_changes(case_id: str, result: DocumentDiffResult) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    for change in result.changes:
        before = dict(change.before.values) if change.before is not None else {}
        after = dict(change.after.values) if change.after is not None else {}
        deltas = []
        for field in sorted(before.keys() | after.keys()):
            if before.get(field) == after.get(field):
                continue
            snapshot = (
                result.target_snapshot
                if change.after is not None and field in after
                else result.source_snapshot
            )
            snapshot_fact = next(
                value for value in snapshot.facts if value.fact.stable_key == change.stable_key
            )
            evidence = next(
                (
                    value
                    for value in snapshot_fact.fact.field_evidence
                    if value.canonical_field == field
                ),
                None,
            )
            matching = list(evidence.source_refs) if evidence is not None else []
            if not matching:
                raise ValueError(f"Changed field has no exact cell source_ref: {field}")
            deltas.append(
                {
                    "field": field,
                    "before": before.get(field),
                    "after": after.get(field),
                    "source_ref": matching[0],
                }
            )
        changes.append(
            {
                "stable_key": change.stable_key,
                "fact_type": change.fact_type,
                "domain": change.domain,
                "change_type": change.change_type.value,
                "business_summary": change.summary,
                "field_deltas": deltas,
                "confidence": change.confidence.value,
                "review_status": "approved",
            }
        )
    return {
        "case_id": case_id,
        "dataset_stage": "draft",
        "expected_structured_change_count": len(changes),
        "changes": changes,
    }


def _change_signatures(result: DocumentDiffResult) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            change.stable_key,
            change.change_type.value,
            tuple(sorted(dict(change.before.values).items())) if change.before else None,
            tuple(sorted(dict(change.after.values).items())) if change.after else None,
        )
        for change in result.changes
    )


def _anchor_terms(
    requirement: str,
    document_name: str,
    source_facts: list[dict[str, Any]],
    changes: list[dict[str, Any]],
) -> frozenset[str]:
    material = [requirement, document_name]
    for fact in source_facts:
        material.append(str(fact["stable_key"]))
    for change in changes:
        material.extend(
            (
                str(change.get("stable_key", "")),
                json.dumps(change.get("before"), ensure_ascii=False),
                json.dumps(change.get("after"), ensure_ascii=False),
            )
        )
    tokens = {
        token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", " ".join(material))
    }
    return frozenset(tokens - {"screen", "element", "document", "xlsx"})


def _rank_files(
    graph: dict[str, Any], files: tuple[Any, ...], terms: frozenset[str]
) -> list[dict[str, Any]]:
    graph_by_path = {
        str(value["path"]): value for value in cast(list[dict[str, Any]], graph["files"])
    }
    ranked: list[dict[str, Any]] = []
    for file in files:
        graph_file = graph_by_path[file.path]
        symbols = cast(list[dict[str, Any]], graph_file["symbols"])
        haystack = " ".join(
            (
                file.path,
                *(str(value.get("name", "")) for value in symbols),
                *(str(value.get("signature", "")) for value in symbols),
                file.content.decode("utf-8", errors="replace")[:200_000],
            )
        ).casefold()
        matches = sorted(term for term in terms if term in haystack)
        path_matches = [term for term in matches if term in file.path.casefold()]
        score = len(matches) + 3 * len(path_matches)
        ranked.append(
            {
                "path": file.path,
                "language": file.language,
                "role": file.role,
                "content_hash": file.content_hash,
                "score": score,
                "matched_terms": matches,
                "symbols": [
                    {
                        "name": value.get("name"),
                        "signature": value.get("signature"),
                        "start_line": value.get("start_line"),
                        "end_line": value.get("end_line"),
                    }
                    for value in symbols
                ],
            }
        )
    return sorted(
        ranked,
        key=lambda value: (-int(value["score"]), str(value["role"]), str(value["path"])),
    )


def _normalized_questions(
    raw: list[dict[str, Any]], confidence: dict[str, str]
) -> list[dict[str, Any]]:
    questions = copy.deepcopy(raw)
    ids = [str(value["question_id"]) for value in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("AI draft question IDs must be unique")
    for question in questions:
        option_ids = [str(value["option_id"]) for value in question["options"]]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError(
                f"AI draft question option IDs must be unique: {question['question_id']}"
            )
        recommended = question.get("recommended_option_id")
        if recommended is not None and recommended not in option_ids:
            raise ValueError(f"AI draft recommended option is unknown: {question['question_id']}")
    questioned_steps = {str(value["step"]) for value in questions}
    for step, value in confidence.items():
        if value == "high" or step in questioned_steps:
            continue
        questions.append(
            {
                "question_id": f"confirm-{step.replace('_', '-')}",
                "step": step,
                "prompt": f"Confirm the proposed {step.replace('_', ' ')}?",
                "reason": (
                    f"AI confidence is {value}; deterministic validation cannot decide semantics."
                ),
                "recommended_option_id": "accept",
                "options": [
                    {
                        "option_id": "accept",
                        "label": "Accept proposal",
                        "decision": "accept",
                        "updates": [],
                    },
                    {
                        "option_id": "revise",
                        "label": "Request revision",
                        "decision": "revise",
                        "updates": [],
                    },
                ],
            }
        )
    return questions


def _step_statuses(questions: list[dict[str, Any]]) -> list[dict[str, str]]:
    questioned = {str(value["step"]) for value in questions}
    return [
        {
            "step": step,
            "status": "needs_confirmation" if step in questioned else "auto_confirmed",
        }
        for step in (
            "document_change",
            "code_scope",
            "edit_plan",
            "verification_plan",
        )
    ]


def _load_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _identifier(prefix: str, *values: str) -> str:
    material = "\x00".join(values).encode()
    return f"{prefix}-{sha256(material).hexdigest()[:24]}"
