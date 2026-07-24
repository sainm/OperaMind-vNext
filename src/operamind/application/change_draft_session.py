"""Stepwise confirmation and final materialization of generated change drafts."""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from operamind.application.change_draft import _document_operations, _expected_changes
from operamind.application.change_loop_batch import IsolatedGitWorktree
from operamind.application.change_loop_case import ChangeLoopCase
from operamind.application.document_diff import DocumentDiffRequest, DocumentDiffService
from operamind.contracts import ContractCatalog
from operamind.domain.document_conventions import DocumentConvention
from operamind.infrastructure.documents import (
    DocumentSignalExtractorRegistry,
    XlsxDocumentProposalWriter,
)
from operamind.profiles import ProfileCatalog


@dataclass(frozen=True, slots=True)
class ChangeDraftAnswerResult:
    session: dict[str, Any]
    session_path: Path
    next_question: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class FinalizedChangeDraft:
    case: ChangeLoopCase
    case_root: Path
    session_path: Path


class ChangeDraftSessionService:
    """Persist explicit choices without treating AI output as approval."""

    def __init__(self, *, repository_root: Path) -> None:
        self._root = repository_root.resolve(strict=True)
        self._session_schema = _load_object(
            self._root / "drafts/schemas/change-draft-session.schema.json"
        )
        self._ai_schema = _load_object(
            self._root / "drafts/schemas/change-draft-ai-response.schema.json"
        )

    def load(self, draft_root: Path) -> dict[str, Any]:
        root = draft_root.resolve(strict=True)
        session = _load_object(root / "draft-session.json")
        self._validate_session(session)
        return session

    def next_question(self, draft_root: Path) -> dict[str, Any] | None:
        session = self.load(draft_root)
        answered = {
            str(value["question_id"]) for value in cast(list[dict[str, Any]], session["answers"])
        }
        return next(
            (
                copy.deepcopy(value)
                for value in cast(list[dict[str, Any]], session["questions"])
                if str(value["question_id"]) not in answered
            ),
            None,
        )

    def answer(
        self,
        *,
        draft_root: Path,
        question_id: str,
        option_id: str,
        answered_by: str,
    ) -> ChangeDraftAnswerResult:
        root = draft_root.resolve(strict=True)
        if any(not value.strip() for value in (question_id, option_id, answered_by)):
            raise ValueError("Draft answer identity fields must not be blank")
        session = self.load(root)
        if session["status"] not in {"awaiting_confirmation", "revision_requested"}:
            raise ValueError(f"Draft does not accept answers in status={session['status']}")
        answers = cast(list[dict[str, Any]], session["answers"])
        if any(value["question_id"] == question_id for value in answers):
            raise ValueError(f"Draft question was already answered: {question_id}")
        question = next(
            (
                value
                for value in cast(list[dict[str, Any]], session["questions"])
                if value["question_id"] == question_id
            ),
            None,
        )
        if question is None:
            raise ValueError(f"Unknown draft question: {question_id}")
        option = next(
            (
                value
                for value in cast(list[dict[str, Any]], question["options"])
                if value["option_id"] == option_id
            ),
            None,
        )
        if option is None:
            raise ValueError(f"Unknown option for {question_id}: {option_id}")
        decision = str(option["decision"])
        if decision == "accept":
            for update in cast(list[dict[str, Any]], option["updates"]):
                _set_json_pointer(
                    cast(dict[str, Any], session["proposal"]),
                    str(update["path"]),
                    copy.deepcopy(update.get("value")),
                )
        answers.append(
            {
                "question_id": question_id,
                "option_id": option_id,
                "decision": decision,
                "answered_by": answered_by,
                "answered_at": datetime.now(UTC).isoformat(),
            }
        )
        step = str(question["step"])
        step_record = next(
            value for value in cast(list[dict[str, Any]], session["steps"]) if value["step"] == step
        )
        if decision == "revise":
            step_record["status"] = "revision_requested"
            session["status"] = "revision_requested"
        else:
            unanswered_same_step = any(
                value["step"] == step
                and not any(answer["question_id"] == value["question_id"] for answer in answers)
                for value in cast(list[dict[str, Any]], session["questions"])
            )
            if not unanswered_same_step:
                step_record["status"] = "confirmed"
            if all(
                any(answer["question_id"] == value["question_id"] for answer in answers)
                for value in cast(list[dict[str, Any]], session["questions"])
            ) and all(answer["decision"] == "accept" for answer in answers):
                session["status"] = "ready_for_approval"
        _validate_ai_envelope(
            {
                "schema_version": "v1",
                "case": cast(dict[str, Any], session["proposal"])["case"],
                "document_operations": cast(dict[str, Any], session["proposal"])[
                    "document_operations"
                ],
                "confidence": session["confidence"],
                "questions": session["questions"],
            },
            self._ai_schema,
        )
        case_payload = cast(dict[str, Any], cast(dict[str, Any], session["proposal"])["case"])
        _write_json(root / "change-loop-case.json", case_payload)
        self._validate_session(session)
        session_path = _write_json(root / "draft-session.json", session)
        return ChangeDraftAnswerResult(
            session=session,
            session_path=session_path,
            next_question=self.next_question(root),
        )

    def approve(
        self,
        *,
        draft_root: Path,
        final_case_root: Path,
        target_repository: Path,
        reviewed_by: str,
    ) -> FinalizedChangeDraft:
        root = draft_root.resolve(strict=True)
        if not reviewed_by.strip():
            raise ValueError("reviewed_by must not be blank")
        session = self.load(root)
        if session["status"] != "ready_for_approval":
            raise ValueError(
                f"Draft must complete confirmations before approval: {session['status']}"
            )
        target = final_case_root.absolute()
        if target.exists():
            raise FileExistsError(f"Final case already exists: {target}")
        repository = target_repository.resolve(strict=True)
        base_revision = str(cast(dict[str, Any], session["repository"])["base_revision"])
        worktree_path = root / ".approval-worktree"
        with IsolatedGitWorktree(
            repository=repository,
            path=worktree_path,
            revision=base_revision,
        ) as workspace:
            case_payload = copy.deepcopy(
                cast(dict[str, Any], cast(dict[str, Any], session["proposal"])["case"])
            )
            reviewed_at = datetime.now(UTC).isoformat()
            case_payload["review"] = {
                "review_status": "approved",
                "reviewed_by": reviewed_by,
                "reviewed_at": reviewed_at,
            }
            before = Path(str(cast(dict[str, Any], session["input"])["before_document"]))
            if not before.resolve(strict=True).is_file():
                raise FileNotFoundError(f"Draft before document is missing: {before}")
            materialized = root / "documents" / f"approved-{before.name}"
            if session["input_mode"] == "natural_language":
                operations = _document_operations(
                    cast(
                        list[dict[str, Any]],
                        cast(dict[str, Any], session["proposal"])["document_operations"],
                    ),
                    source_document=before.name,
                )
                XlsxDocumentProposalWriter().apply(
                    source_path=before,
                    target_path=materialized,
                    changes=operations,
                )
            else:
                after_value = cast(dict[str, Any], session["input"])["after_document"]
                if not isinstance(after_value, str):
                    raise ValueError("Documents draft is missing its after document")
                shutil.copyfile(Path(after_value).resolve(strict=True), materialized)
            result = _diff(
                repository_root=self._root,
                project_id=str(session["project_id"]),
                draft_id=str(session["draft_id"]),
                before=before,
                after=materialized,
                document_profile=str(case_payload["document"]["convention_profile"]),
            )
            expected = _expected_changes(str(session["case_id"]), result)
            _validate_final_case(
                repository_root=self._root,
                workspace=workspace,
                case_payload=case_payload,
                case_root=root,
                source_refs={
                    reference for change in result.changes for reference in change.source_refs
                },
            )
            staging = target.with_name(f".{target.name}.operamind-draft")
            if staging.exists():
                raise FileExistsError(f"Final case staging path exists: {staging}")
            staging.mkdir(parents=True)
            try:
                fixture = staging / "fixtures/after.xlsx"
                fixture.parent.mkdir(parents=True)
                shutil.copyfile(materialized, fixture)
                _write_json(staging / "change-loop-case.json", case_payload)
                _write_json(staging / "expected-changes.json", expected)
                _write_json(
                    staging / "source-manifest.json",
                    {
                        "case_id": session["case_id"],
                        "dataset_stage": "generated_candidate",
                        "portability_status": "reviewed_fixture_with_external_before",
                        "project_id": session["project_id"],
                        "document_sources": {
                            "changed_file": before.name,
                            "before_uri": f"urn:sha256:{_file_digest(before)}",
                            "before_sha256": _file_digest(before),
                            "after_fixture": "fixtures/after.xlsx",
                            "after_sha256": _file_digest(fixture),
                        },
                        "target_repository": {
                            "url": str(cast(dict[str, Any], session["repository"])["remote_url"]),
                            "base_commit": base_revision,
                        },
                        "generated_by": "operamind-change-draft-v1",
                        "review_status": "approved",
                    },
                )
                _write_json(
                    staging / "draft-review.json",
                    {
                        "draft_id": session["draft_id"],
                        "before_document": str(before),
                        "reviewed_by": reviewed_by,
                        "reviewed_at": reviewed_at,
                        "steps": session["steps"],
                        "answers": session["answers"],
                        "provider": session["provider"],
                    },
                )
                staging.replace(target)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        case = ChangeLoopCase.load(
            target,
            schema_path=self._root / "golden-dataset/change-loop-case.schema.json",
        )
        session["status"] = "finalized"
        session["final_case_root"] = str(target)
        self._validate_session(session)
        session_path = _write_json(root / "draft-session.json", session)
        return FinalizedChangeDraft(case=case, case_root=target, session_path=session_path)

    def _validate_session(self, session: dict[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(self._session_schema, format_checker=FormatChecker()).iter_errors(
                session
            ),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(value) for value in first.absolute_path) or "$"
            raise ValueError(f"Invalid change draft session at {location}: {first.message}")


def _set_json_pointer(root: dict[str, Any], pointer: str, value: object) -> None:
    if not pointer.startswith("/"):
        raise ValueError(f"Draft update path must be a JSON Pointer: {pointer}")
    parts = [value.replace("~1", "/").replace("~0", "~") for value in pointer[1:].split("/")]
    allowed = {
        "requirements",
        "analysis",
        "impact_candidates",
        "edit",
        "acceptance_criteria",
        "test_cases",
        "data_sets",
        "execution",
    }
    if not parts or not (
        parts[0] == "document_operations"
        or (len(parts) >= 2 and parts[0] == "case" and parts[1] in allowed)
    ):
        raise ValueError(f"Draft update path is outside mutable proposal fields: {pointer}")
    current: object = root
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ValueError(f"Draft update path does not exist: {pointer}")
    leaf = parts[-1]
    if isinstance(current, dict) and leaf in current:
        current[leaf] = value
    elif isinstance(current, list) and leaf.isdigit() and int(leaf) < len(current):
        current[int(leaf)] = value
    else:
        raise ValueError(f"Draft update target does not exist: {pointer}")


def _diff(
    *,
    repository_root: Path,
    project_id: str,
    draft_id: str,
    before: Path,
    after: Path,
    document_profile: str,
) -> Any:
    profiles = ProfileCatalog.load(repository_root / "profiles")
    profile = _load_object(repository_root / document_profile)
    profiles.validate_profile(profile)
    return DocumentDiffService(
        extractors=DocumentSignalExtractorRegistry.default(),
        contracts=ContractCatalog.load(repository_root / "contracts"),
    ).run(
        DocumentDiffRequest(
            project_id=project_id,
            domain="ui",
            fact_type="screen_element",
            source_snapshot_id=f"draft-final-before-{draft_id}",
            target_snapshot_id=f"draft-final-after-{draft_id}",
            before_path=before,
            after_path=after,
        ),
        DocumentConvention.from_validated_profile(profile),
    )


def _validate_final_case(
    *,
    repository_root: Path,
    workspace: Path,
    case_payload: dict[str, Any],
    case_root: Path,
    source_refs: set[str],
) -> None:
    case = ChangeLoopCase.from_payload(
        root=case_root,
        payload=case_payload,
        schema_path=repository_root / "golden-dataset/change-loop-case.schema.json",
    )
    referenced = {
        *(str(value["path"]) for value in case.impact_candidates),
        *(
            str(value["path"])
            for value in cast(list[dict[str, Any]], case.execution["source_tests"])
        ),
    }
    for relative in referenced:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"Final draft path is unsafe: {relative}")
        path = (workspace / relative).resolve(strict=True)
        if not path.is_relative_to(workspace) or not path.is_file() or path.is_symlink():
            raise ValueError(f"Final draft path is not a safe source file: {relative}")
    for replacement in case.replacements:
        content = (workspace / replacement.path).read_text(encoding="utf-8")
        if content.count(replacement.before) != 1:
            raise ValueError(
                f"Final draft replacement preimage must occur exactly once: {replacement.path}"
            )
    rule_refs = {
        str(reference)
        for rule in cast(list[dict[str, Any]], case.requirements["business_rules"])
        for reference in cast(list[str], rule["source_refs"])
    }
    if not rule_refs.issubset(source_refs):
        raise ValueError("Final draft business rules no longer match the document Diff")


def _validate_ai_envelope(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(value) for value in first.absolute_path) or "$"
        raise ValueError(f"Invalid updated AI proposal at {location}: {first.message}")


def _load_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
