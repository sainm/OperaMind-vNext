"""Validate Golden Dataset manifests, references, and freeze readiness."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from operamind.application.test_data_flow import validate_test_data_plan_artifact
from operamind.validation import ValidationIssue, ValidationReport

CASE_JSON_REFERENCES = (
    "source_manifest",
    "expected_changes",
    "expected_rag_context",
    "expected_code_scope",
    "expected_ui_scenarios",
)
OPTIONAL_CASE_JSON_REFERENCES = ("test_data_plan",)
CASE_FILE_REFERENCES = (*CASE_JSON_REFERENCES, *OPTIONAL_CASE_JSON_REFERENCES, "review")
GOLDEN_DATASET_DIGEST_ALGORITHM = "operamind-golden-dataset-v1"
GOLDEN_DATASET_SCHEMA_FILES = (
    "manifest.schema.json",
    "expected-rag-context.schema.json",
    "expected-ui-scenarios.schema.json",
    "review.schema.json",
)
REQUIRED_REVIEW_STEP_IDS = frozenset(
    {"source_identity", "expected_change_and_rag", "code_scope", "ui_scenarios"}
)


@dataclass(frozen=True, slots=True)
class GoldenDatasetValidator:
    """Validator for a dataset directory with path-confined case references."""

    dataset_root: Path

    def validate(self, manifest_path: Path, *, require_ready: bool = False) -> ValidationReport:
        """Validate schema, cross-references, payload identity, and optional readiness."""

        root = self.dataset_root.resolve()
        manifest = self._load_object(manifest_path)
        schema = self._load_object(root / "manifest.schema.json")
        rag_expectation_schema = self._load_object(root / "expected-rag-context.schema.json")
        ui_expectation_schema = self._load_object(root / "expected-ui-scenarios.schema.json")
        review_schema = self._load_object(root / "review.schema.json")
        issues = self._schema_issues(manifest, schema, manifest_path)
        if issues:
            return ValidationReport(tuple(issues))

        projects = self._objects(manifest["projects"])
        cases = self._objects(manifest["cases"])
        issues.extend(self._duplicate_id_issues(projects, "project_id", "project"))
        issues.extend(self._duplicate_id_issues(cases, "case_id", "case"))
        project_ids = {str(project["project_id"]) for project in projects}

        for index, case in enumerate(cases):
            location = f"cases/{index}"
            project_id = str(case["project_id"])
            if project_id not in project_ids:
                issues.append(
                    ValidationIssue(
                        code="golden.unknown_project",
                        message=f"Case references unknown project_id: {project_id}",
                        location=f"{location}/project_id",
                    )
                )
            issues.extend(
                self._validate_case_files(
                    root,
                    case,
                    location,
                    rag_expectation_schema=rag_expectation_schema,
                    ui_expectation_schema=ui_expectation_schema,
                    review_schema=review_schema,
                )
            )

        if require_ready:
            issues.extend(self._readiness_issues(manifest, cases, root))
        return ValidationReport(tuple(issues))

    def dataset_digest(self, manifest_path: Path) -> str:
        """Digest the selected manifest, schemas, and every referenced case file."""

        root = self.dataset_root.resolve()
        manifest_input = manifest_path.absolute()
        if manifest_input.is_symlink():
            raise ValueError("Golden Dataset digest does not allow a symlink manifest")
        path = manifest_input.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Golden Dataset manifest must be a file inside dataset_root")
        manifest = self._load_object(path)
        raw_cases = manifest.get("cases")
        if not isinstance(raw_cases, list) or not all(isinstance(case, dict) for case in raw_cases):
            raise ValueError("Golden Dataset manifest cases must be a list of objects")
        selected = {path, *(root / name for name in GOLDEN_DATASET_SCHEMA_FILES)}
        for case in raw_cases:
            for key in CASE_FILE_REFERENCES:
                raw_reference = case.get(key)
                if key in OPTIONAL_CASE_JSON_REFERENCES and raw_reference is None:
                    continue
                if not isinstance(raw_reference, str) or not raw_reference:
                    raise ValueError(f"Golden Dataset case is missing {key}")
                referenced_path = root / raw_reference
                if not referenced_path.resolve().is_relative_to(root):
                    raise ValueError(f"Golden Dataset reference escapes root: {raw_reference}")
                selected.add(referenced_path)

        digest = sha256()
        digest.update(f"{GOLDEN_DATASET_DIGEST_ALGORITHM}\0".encode())
        for selected_path in sorted(
            selected,
            key=lambda value: value.relative_to(root).as_posix(),
        ):
            if selected_path.is_symlink():
                raise ValueError("Golden Dataset digest does not allow symlinks")
            if not selected_path.is_file():
                raise ValueError(
                    "Golden Dataset digest input is missing: "
                    f"{selected_path.relative_to(root).as_posix()}"
                )
            relative = selected_path.relative_to(root).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(bytes.fromhex(self._file_digest(selected_path)))
            digest.update(b"\n")
        return digest.hexdigest()

    def _validate_case_files(
        self,
        root: Path,
        case: dict[str, Any],
        location: str,
        *,
        rag_expectation_schema: dict[str, Any],
        ui_expectation_schema: dict[str, Any],
        review_schema: dict[str, Any],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        case_id = str(case["case_id"])
        for key in CASE_FILE_REFERENCES:
            if key in OPTIONAL_CASE_JSON_REFERENCES and key not in case:
                continue
            raw_reference = str(case[key])
            referenced_path = (root / raw_reference).resolve()
            if not referenced_path.is_relative_to(root):
                issues.append(
                    ValidationIssue(
                        code="golden.path_escape",
                        message=f"Reference escapes dataset root: {raw_reference}",
                        location=f"{location}/{key}",
                    )
                )
                continue
            if not referenced_path.is_file():
                issues.append(
                    ValidationIssue(
                        code="golden.missing_reference",
                        message=f"Referenced file does not exist: {raw_reference}",
                        location=f"{location}/{key}",
                    )
                )
                continue
            if key in (*CASE_JSON_REFERENCES, *OPTIONAL_CASE_JSON_REFERENCES):
                try:
                    payload = self._load_object(referenced_path)
                except (json.JSONDecodeError, ValueError) as error:
                    issues.append(
                        ValidationIssue(
                            code="golden.invalid_json",
                            message=str(error),
                            location=f"{location}/{key}",
                        )
                    )
                    continue
                if key != "test_data_plan" and payload.get("case_id") != case_id:
                    issues.append(
                        ValidationIssue(
                            code="golden.case_id_mismatch",
                            message=f"Referenced payload must use case_id {case_id}",
                            location=f"{location}/{key}",
                        )
                    )
                if key == "test_data_plan":
                    issues.extend(
                        self._test_data_plan_issues(
                            root=root,
                            payload=payload,
                            case=case,
                            location=f"{location}/{key}",
                        )
                    )
                elif key == "expected_rag_context":
                    validator = Draft202012Validator(rag_expectation_schema)
                    issues.extend(
                        ValidationIssue(
                            code="golden.rag_expectation_schema_violation",
                            message=error.message,
                            location=(
                                f"{location}/{key}/"
                                + "/".join(str(part) for part in error.absolute_path)
                            ).rstrip("/"),
                        )
                        for error in sorted(
                            validator.iter_errors(payload),
                            key=lambda item: list(item.path),
                        )
                    )
                if key == "expected_ui_scenarios":
                    validator = Draft202012Validator(ui_expectation_schema)
                    issues.extend(
                        ValidationIssue(
                            code="golden.ui_expectation_schema_violation",
                            message=error.message,
                            location=(
                                f"{location}/{key}/"
                                + "/".join(str(part) for part in error.absolute_path)
                            ).rstrip("/"),
                        )
                        for error in sorted(
                            validator.iter_errors(payload),
                            key=lambda item: list(item.path),
                        )
                    )
                    issues.extend(
                        self._ui_expectation_issues(
                            payload=payload,
                            case=case,
                            location=f"{location}/{key}",
                        )
                    )
            elif key == "review" and referenced_path.suffix == ".json":
                try:
                    payload = self._load_object(referenced_path)
                except (json.JSONDecodeError, ValueError) as error:
                    issues.append(
                        ValidationIssue(
                            code="golden.invalid_json",
                            message=str(error),
                            location=f"{location}/{key}",
                        )
                    )
                    continue
                if payload.get("case_id") != case_id:
                    issues.append(
                        ValidationIssue(
                            code="golden.case_id_mismatch",
                            message=f"Referenced payload must use case_id {case_id}",
                            location=f"{location}/{key}",
                        )
                    )
                validator = Draft202012Validator(review_schema)
                issues.extend(
                    ValidationIssue(
                        code="golden.review_schema_violation",
                        message=error.message,
                        location=(
                            f"{location}/{key}/"
                            + "/".join(str(part) for part in error.absolute_path)
                        ).rstrip("/"),
                    )
                    for error in sorted(
                        validator.iter_errors(payload),
                        key=lambda item: list(item.path),
                    )
                )
                issues.extend(
                    self._review_step_issues(
                        payload=payload,
                        location=f"{location}/{key}",
                    )
                )
        return issues

    @staticmethod
    def _test_data_plan_issues(
        *,
        root: Path,
        payload: dict[str, Any],
        case: dict[str, Any],
        location: str,
    ) -> list[ValidationIssue]:
        schema_path = root.parent / "contracts/schemas/test-data-plan.schema.json"
        if not schema_path.is_file():
            schema_path = (
                Path(__file__).parents[3]
                / "contracts/schemas/test-data-plan.schema.json"
            )
        schema = GoldenDatasetValidator._load_object(schema_path)
        issues = [
            ValidationIssue(
                code="golden.test_data_plan_schema_violation",
                message=error.message,
                location=(
                    location + "/" + "/".join(str(part) for part in error.absolute_path)
                ).rstrip("/"),
            )
            for error in sorted(
                Draft202012Validator(schema).iter_errors(payload),
                key=lambda item: list(item.path),
            )
        ]
        if not issues:
            issues.extend(
                ValidationIssue(
                    code="golden.test_data_plan_semantic_violation",
                    message=reason,
                    location=location,
                )
                for reason in validate_test_data_plan_artifact(payload)
            )
        if payload.get("project_id") != case.get("project_id"):
            issues.append(
                ValidationIssue(
                    code="golden.test_data_plan_project_mismatch",
                    message="TestDataPlan project_id must match the case",
                    location=f"{location}/project_id",
                )
            )
        if payload.get("artifact_type") != "TestDataPlan":
            issues.append(
                ValidationIssue(
                    code="golden.test_data_plan_type_mismatch",
                    message="test_data_plan must reference a TestDataPlan Artifact",
                    location=f"{location}/artifact_type",
                )
            )
        return issues

    @staticmethod
    def _review_step_issues(*, payload: dict[str, Any], location: str) -> list[ValidationIssue]:
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            return []
        step_ids = [
            str(step.get("step_id"))
            for step in raw_steps
            if isinstance(step, dict) and isinstance(step.get("step_id"), str)
        ]
        if (
            len(step_ids) == len(REQUIRED_REVIEW_STEP_IDS)
            and set(step_ids) == REQUIRED_REVIEW_STEP_IDS
        ):
            return []
        return [
            ValidationIssue(
                code="golden.review_step_set_mismatch",
                message="Review must contain each required decision step exactly once",
                location=f"{location}/steps",
            )
        ]

    @staticmethod
    def _ui_expectation_issues(
        *,
        payload: dict[str, Any],
        case: dict[str, Any],
        location: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if payload.get("project_id") != case.get("project_id"):
            issues.append(
                ValidationIssue(
                    code="golden.ui_project_id_mismatch",
                    message="UI expectation project_id must match the case",
                    location=f"{location}/project_id",
                )
            )
        raw_scenarios = payload.get("scenarios")
        if not isinstance(raw_scenarios, list):
            return issues
        scenario_ids = [
            str(item["scenario_id"])
            for item in raw_scenarios
            if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
        ]
        if len(scenario_ids) != len(set(scenario_ids)):
            issues.append(
                ValidationIssue(
                    code="golden.duplicate_ui_scenario_id",
                    message="UI expectation scenario_id values must be unique",
                    location=f"{location}/scenarios",
                )
            )
        raw_outcomes = payload.get("expected_current_base_outcome")
        if isinstance(raw_outcomes, dict) and set(raw_outcomes) != set(scenario_ids):
            issues.append(
                ValidationIssue(
                    code="golden.ui_outcome_coverage",
                    message="Current-base outcomes must cover every UI scenario exactly once",
                    location=f"{location}/expected_current_base_outcome",
                )
            )
        return issues

    @staticmethod
    def _readiness_issues(
        manifest: dict[str, Any], cases: list[dict[str, Any]], root: Path
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        projects = GoldenDatasetValidator._objects(manifest["projects"])
        project_by_id = {str(project["project_id"]): project for project in projects}
        checks = (
            (manifest.get("dataset_stage") == "golden", "dataset_stage must be golden"),
            (manifest.get("status") == "frozen", "status must be frozen"),
            (
                len(projects) >= 1,
                "at least 1 real project is required",
            ),
            (
                1 <= len(cases) <= 5,
                "MVP Golden Dataset must contain between 1 and 5 real reviewed cases",
            ),
        )
        for passed, message in checks:
            if not passed:
                issues.append(
                    ValidationIssue(
                        code="golden.not_ready",
                        message=message,
                        location="manifest",
                    )
                )

        raw_reviewers = manifest.get("reviewers")
        reviewers = (
            {str(reviewer) for reviewer in raw_reviewers}
            if isinstance(raw_reviewers, list)
            else set()
        )
        if not reviewers or any(_is_placeholder_identity(reviewer) for reviewer in reviewers):
            issues.append(
                ValidationIssue(
                    code="golden.invalid_reviewer",
                    message="Golden readiness requires real, non-placeholder reviewer identities",
                    location="reviewers",
                )
            )
        for index, project in enumerate(projects):
            repository_url = str(project["repository_url"])
            repository_commit = str(project["repository_commit"])
            if _is_placeholder_identity(repository_url):
                issues.append(
                    ValidationIssue(
                        code="golden.placeholder_repository",
                        message="Golden projects must reference a real immutable repository",
                        location=f"projects/{index}/repository_url",
                    )
                )
            if re.fullmatch(r"[0-9a-f]{40}", repository_commit) is None:
                issues.append(
                    ValidationIssue(
                        code="golden.invalid_repository_commit",
                        message="Golden repository_commit must be a full lowercase Git SHA-1",
                        location=f"projects/{index}/repository_commit",
                    )
                )

        for index, case in enumerate(cases):
            source_path = (root / str(case["source_manifest"])).resolve()
            review_path = (root / str(case["review"])).resolve()
            if source_path.is_file():
                source = GoldenDatasetValidator._load_object(source_path)
                if source.get("portability_status") == "local_only":
                    issues.append(
                        ValidationIssue(
                            code="golden.local_only_source",
                            message="Source documents must use immutable portable references",
                            location=f"cases/{index}/source_manifest",
                        )
                    )
                if source.get("review_status") != "approved":
                    issues.append(
                        ValidationIssue(
                            code="golden.pending_review",
                            message="Source manifest still requires human review",
                            location=f"cases/{index}/source_manifest",
                        )
                    )
                matched_project = project_by_id.get(str(case["project_id"]))
                target = source.get("target_repository")
                if matched_project is not None and isinstance(target, dict):
                    expected_repository = (
                        str(matched_project["repository_url"]),
                        str(matched_project["repository_commit"]),
                    )
                    actual_repository = (
                        str(target.get("url", "")),
                        str(target.get("base_commit", "")),
                    )
                    if actual_repository != expected_repository:
                        issues.append(
                            ValidationIssue(
                                code="golden.source_repository_mismatch",
                                message=(
                                    "Source manifest repository must match its Golden project"
                                ),
                                location=f"cases/{index}/source_manifest/target_repository",
                            )
                        )
            if review_path.is_file():
                if review_path.suffix != ".json":
                    review: dict[str, Any] = {}
                else:
                    review = GoldenDatasetValidator._load_object(review_path)
                raw_steps = review.get("steps")
                steps = raw_steps if isinstance(raw_steps, list) else []
                decisions = {
                    str(step.get("step_id")): str(step.get("decision"))
                    for step in steps
                    if isinstance(step, dict)
                }
                review_complete = (
                    review.get("dataset_stage") == "golden"
                    and review.get("review_status") == "approved"
                    and len(steps) == len(REQUIRED_REVIEW_STEP_IDS)
                    and set(decisions) == REQUIRED_REVIEW_STEP_IDS
                    and all(decision == "approved" for decision in decisions.values())
                )
                if not review_complete:
                    issues.append(
                        ValidationIssue(
                            code="golden.pending_review",
                            message="All required human judgment steps must be approved",
                            location=f"cases/{index}/review",
                        )
                    )
                reviewed_by = str(review.get("reviewed_by", ""))
                if _is_placeholder_identity(reviewed_by) or reviewed_by not in reviewers:
                    issues.append(
                        ValidationIssue(
                            code="golden.review_identity_mismatch",
                            message="Case reviewer must be a real identity listed in reviewers",
                            location=f"cases/{index}/review/reviewed_by",
                        )
                    )
            changes_path = (root / str(case["expected_changes"])).resolve()
            if changes_path.is_file():
                expectation = GoldenDatasetValidator._load_object(changes_path)
                raw_changes = expectation.get("changes")
                changes = raw_changes if isinstance(raw_changes, list) else []
                if (
                    expectation.get("dataset_stage") != "golden"
                    or not changes
                    or any(
                        not isinstance(change, dict) or change.get("review_status") != "approved"
                        for change in changes
                    )
                ):
                    issues.append(
                        ValidationIssue(
                            code="golden.changes_not_ready",
                            message="Expected changes must be frozen and human-approved",
                            location=f"cases/{index}/expected_changes",
                        )
                    )
            scope_path = (root / str(case["expected_code_scope"])).resolve()
            if scope_path.is_file():
                expectation = GoldenDatasetValidator._load_object(scope_path)
                if (
                    expectation.get("dataset_stage") != "golden"
                    or expectation.get("review_status") != "approved"
                ):
                    issues.append(
                        ValidationIssue(
                            code="golden.code_scope_not_ready",
                            message="Expected code scope must be frozen and human-approved",
                            location=f"cases/{index}/expected_code_scope",
                        )
                    )
            rag_path = (root / str(case["expected_rag_context"])).resolve()
            if rag_path.is_file():
                expectation = GoldenDatasetValidator._load_object(rag_path)
                expected_purposes = {
                    "business_behavior",
                    "precise_anchor",
                    "acceptance_criteria",
                }
                raw_queries = expectation.get("query_expectations")
                purposes = (
                    {
                        str(item.get("query_purpose"))
                        for item in raw_queries
                        if isinstance(item, dict)
                    }
                    if isinstance(raw_queries, list)
                    else set()
                )
                required_contexts = expectation.get("required_contexts")
                canonical_ids_frozen = (
                    isinstance(required_contexts, list)
                    and bool(required_contexts)
                    and all(
                        isinstance(item, dict)
                        and isinstance(item.get("canonical_node_ids"), list)
                        and bool(item["canonical_node_ids"])
                        for item in required_contexts
                    )
                )
                thresholds = expectation.get("quality_thresholds")
                rag_checks = (
                    (
                        expectation.get("dataset_stage") == "golden",
                        "RAG expectation must be golden",
                    ),
                    (
                        expectation.get("project_id") == case.get("project_id"),
                        "RAG expectation project_id must match the case",
                    ),
                    (
                        expectation.get("canonical_id_status") == "frozen" and canonical_ids_frozen,
                        "RAG Canonical node IDs must be frozen",
                    ),
                    (
                        expectation.get("review_status") == "approved",
                        "RAG expectation must be human-approved",
                    ),
                    (
                        purposes == expected_purposes and len(raw_queries or []) == 3,
                        "RAG expectation must cover the three query purposes exactly once",
                    ),
                    (
                        isinstance(thresholds, dict)
                        and thresholds.get("max_cross_project_leaks") == 0,
                        "RAG quality thresholds must require zero cross-project leaks",
                    ),
                )
                for passed, message in rag_checks:
                    if not passed:
                        issues.append(
                            ValidationIssue(
                                code="golden.rag_not_ready",
                                message=message,
                                location=f"cases/{index}/expected_rag_context",
                            )
                        )
            ui_path = (root / str(case["expected_ui_scenarios"])).resolve()
            if ui_path.is_file():
                expectation = GoldenDatasetValidator._load_object(ui_path)
                ui_checks = (
                    (
                        expectation.get("dataset_stage") == "golden",
                        "UI expectation must be golden",
                    ),
                    (
                        expectation.get("project_id") == case.get("project_id"),
                        "UI expectation project_id must match the case",
                    ),
                    (
                        expectation.get("ui_impact_status") in {"impacted", "not_impacted"},
                        "UI impact status must be decided",
                    ),
                    (
                        expectation.get("review_status") == "approved",
                        "UI expectation must be business and QA approved",
                    ),
                )
                for passed, message in ui_checks:
                    if not passed:
                        issues.append(
                            ValidationIssue(
                                code="golden.ui_not_ready",
                                message=message,
                                location=f"cases/{index}/expected_ui_scenarios",
                            )
                        )
        return issues

    @staticmethod
    def _schema_issues(
        instance: dict[str, Any], schema: dict[str, Any], manifest_path: Path
    ) -> list[ValidationIssue]:
        validator = Draft202012Validator(schema)
        return [
            ValidationIssue(
                code="golden.manifest_schema_violation",
                message=error.message,
                location="/".join(str(part) for part in error.absolute_path) or manifest_path.name,
            )
            for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path))
        ]

    @staticmethod
    def _duplicate_id_issues(
        objects: list[dict[str, Any]], key: str, label: str
    ) -> list[ValidationIssue]:
        seen: set[str] = set()
        issues: list[ValidationIssue] = []
        for index, value in enumerate(objects):
            identifier = str(value[key])
            if identifier in seen:
                issues.append(
                    ValidationIssue(
                        code=f"golden.duplicate_{label}_id",
                        message=f"Duplicate {key}: {identifier}",
                        location=f"{label}s/{index}/{key}",
                    )
                )
            seen.add(identifier)
        return issues

    @staticmethod
    def _objects(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError("Expected a list of objects after schema validation")
        return value

    @staticmethod
    def _load_object(path: Path) -> dict[str, Any]:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Expected a JSON object: {path}")
        return raw

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


def _is_placeholder_identity(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or any(
        token in normalized for token in (".invalid", "placeholder", "replace-with", "pending")
    )
