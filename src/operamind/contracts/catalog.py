"""Load the canonical contract catalog and validate Artifact payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from operamind.validation import ValidationIssue, ValidationReport

EXPECTED_ARTIFACT_TYPES = frozenset(
    {
        "ChangeRequest",
        "DocumentChangeProposal",
        "DocumentIngestionResult",
        "StructuredChange",
        "ContextPackage",
        "CodeGraphSnapshot",
        "RuntimeRouteEvidence",
        "UnresolvedEvidenceReport",
        "ImpactReport",
        "ImpactConfirmation",
        "CopilotEditPacket",
        "CopilotCodingTask",
        "ApprovalGrant",
        "UiVerificationResult",
        "TestPlan",
        "TestDataPlan",
        "BusinessDataTemplate",
        "TestDataExecutionResult",
        "AcceptanceCriteria",
        "BusinessCoverageReport",
        "ChangeClosureResult",
        "ChangeOrchestrationPlan",
        "TestCaseChangeProposal",
        "TestCaseRevision",
    }
)


class ContractCatalogError(ValueError):
    """Raised when the catalog itself is malformed or unsafe."""


class ArtifactValidationError(ValueError):
    """Raised when an Artifact does not satisfy its registered schema."""

    def __init__(self, report: ValidationReport) -> None:
        super().__init__("Artifact validation failed")
        self.report = report


@dataclass(frozen=True, slots=True)
class ContractCatalog:
    """A path-confined registry of versioned JSON Schemas."""

    root: Path
    schema_paths: dict[str, Path]

    @classmethod
    def load(cls, contracts_root: Path) -> ContractCatalog:
        """Load `catalog.json` and reject paths outside the contracts directory."""

        root = contracts_root.resolve()
        catalog_path = root / "catalog.json"
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("catalog_version") != "v1":
            raise ContractCatalogError("catalog.json must declare catalog_version v1")
        artifacts = raw.get("artifacts")
        if not isinstance(artifacts, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in artifacts.items()
        ):
            raise ContractCatalogError("catalog.json must map Artifact types to schema paths")

        schema_paths: dict[str, Path] = {}
        for artifact_type, relative_path in artifacts.items():
            schema_path = (root / relative_path).resolve()
            if not schema_path.is_relative_to(root):
                raise ContractCatalogError(f"schema path escapes contracts root: {relative_path}")
            if not schema_path.is_file():
                raise ContractCatalogError(f"schema file does not exist: {relative_path}")
            schema_paths[artifact_type] = schema_path
        return cls(root=root, schema_paths=schema_paths)

    def validate_catalog(self) -> ValidationReport:
        """Validate catalog completeness and every registered Draft 2020-12 schema."""

        issues: list[ValidationIssue] = []
        actual_types = frozenset(self.schema_paths)
        for missing in sorted(EXPECTED_ARTIFACT_TYPES - actual_types):
            issues.append(
                ValidationIssue(
                    code="contract.missing_artifact_type",
                    message=f"Missing core Artifact schema: {missing}",
                    location="contracts/catalog.json",
                )
            )
        for unexpected in sorted(actual_types - EXPECTED_ARTIFACT_TYPES):
            issues.append(
                ValidationIssue(
                    code="contract.unexpected_artifact_type",
                    message=f"Unexpected core Artifact schema: {unexpected}",
                    location="contracts/catalog.json",
                )
            )

        for artifact_type, schema_path in sorted(self.schema_paths.items()):
            try:
                schema = self._load_schema(schema_path)
                Draft202012Validator.check_schema(schema)
            except (json.JSONDecodeError, SchemaError, ValueError) as error:
                issues.append(
                    ValidationIssue(
                        code="contract.invalid_schema",
                        message=str(error),
                        location=str(schema_path.relative_to(self.root)),
                    )
                )
                continue
            if schema.get("title") != artifact_type:
                issues.append(
                    ValidationIssue(
                        code="contract.title_mismatch",
                        message=f"Schema title must be {artifact_type}",
                        location=str(schema_path.relative_to(self.root)),
                    )
                )
        return ValidationReport(tuple(issues))

    def validate_examples(self) -> ValidationReport:
        """Require one valid v1 example for every registered core Artifact."""

        issues: list[ValidationIssue] = []
        for artifact_type, schema_path in sorted(self.schema_paths.items()):
            example_name = schema_path.name.replace(".schema.json", ".v1.example.json")
            example_path = self.root / "examples" / example_name
            if not example_path.is_file():
                issues.append(
                    ValidationIssue(
                        code="contract.missing_example",
                        message=f"Missing v1 example for {artifact_type}",
                        location=str(example_path.relative_to(self.root)),
                    )
                )
                continue
            try:
                example = self._load_schema(example_path)
                self.validate_artifact(example)
            except (json.JSONDecodeError, ValueError, ArtifactValidationError) as error:
                message = str(error)
                if isinstance(error, ArtifactValidationError):
                    message = "; ".join(
                        f"{issue.location}: {issue.message}" for issue in error.report.issues
                    )
                issues.append(
                    ValidationIssue(
                        code="contract.invalid_example",
                        message=message,
                        location=str(example_path.relative_to(self.root)),
                    )
                )
        return ValidationReport(tuple(issues))

    def validate_artifact(self, artifact: dict[str, Any]) -> None:
        """Validate an Artifact against the schema selected by `artifact_type`."""

        artifact_type = artifact.get("artifact_type")
        if not isinstance(artifact_type, str) or artifact_type not in self.schema_paths:
            report = ValidationReport(
                (
                    ValidationIssue(
                        code="artifact.unknown_type",
                        message=f"Unknown artifact_type: {artifact_type!r}",
                        location="artifact_type",
                    ),
                )
            )
            raise ArtifactValidationError(report)

        schema = self._load_schema(self.schema_paths[artifact_type])
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        issues = tuple(
            ValidationIssue(
                code="artifact.schema_violation",
                message=error.message,
                location="/".join(str(part) for part in error.absolute_path) or "$",
            )
            for error in sorted(validator.iter_errors(artifact), key=lambda item: list(item.path))
        )
        if issues:
            raise ArtifactValidationError(ValidationReport(issues))

    @staticmethod
    def _load_schema(schema_path: Path) -> dict[str, Any]:
        raw: object = json.loads(schema_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Schema must be a JSON object: {schema_path}")
        return raw
