"""Validate Golden Dataset manifests, references, and freeze readiness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from operamind.validation import ValidationIssue, ValidationReport

CASE_JSON_REFERENCES = (
    "source_manifest",
    "expected_changes",
    "expected_rag_context",
    "expected_code_scope",
    "expected_ui_scenarios",
)
CASE_FILE_REFERENCES = (*CASE_JSON_REFERENCES, "review")


@dataclass(frozen=True, slots=True)
class GoldenDatasetValidator:
    """Validator for a dataset directory with path-confined case references."""

    dataset_root: Path

    def validate(self, manifest_path: Path, *, require_ready: bool = False) -> ValidationReport:
        """Validate schema, cross-references, payload identity, and optional readiness."""

        root = self.dataset_root.resolve()
        manifest = self._load_object(manifest_path)
        schema = self._load_object(root / "manifest.schema.json")
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
            issues.extend(self._validate_case_files(root, case, location))

        if require_ready:
            issues.extend(self._readiness_issues(manifest, cases, root))
        return ValidationReport(tuple(issues))

    def _validate_case_files(
        self, root: Path, case: dict[str, Any], location: str
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        case_id = str(case["case_id"])
        for key in CASE_FILE_REFERENCES:
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
            if key in CASE_JSON_REFERENCES:
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
        return issues

    @staticmethod
    def _readiness_issues(
        manifest: dict[str, Any], cases: list[dict[str, Any]], root: Path
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        checks = (
            (manifest.get("dataset_stage") == "golden", "dataset_stage must be golden"),
            (manifest.get("status") == "frozen", "status must be frozen"),
            (
                len(GoldenDatasetValidator._objects(manifest["projects"])) >= 2,
                "at least 2 projects are required",
            ),
            (len(cases) >= 12, "at least 12 cases are required by MVP-SCOPE"),
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
                if str(source.get("review_status", "")).startswith("needs_"):
                    issues.append(
                        ValidationIssue(
                            code="golden.pending_review",
                            message="Source manifest still requires human review",
                            location=f"cases/{index}/source_manifest",
                        )
                    )
            if review_path.is_file() and "- [ ]" in review_path.read_text(encoding="utf-8"):
                issues.append(
                    ValidationIssue(
                        code="golden.pending_review",
                        message="Review checklist contains unchecked decisions",
                        location=f"cases/{index}/review",
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
