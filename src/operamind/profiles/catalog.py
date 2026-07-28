"""Load and validate versioned runtime Profiles."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from operamind.validation import ValidationIssue, ValidationReport

EXPECTED_PROFILE_TYPES = frozenset(
    {
        "CodeFrameworkProfile",
        "CommandExecutionProfile",
        "DocumentConventionProfile",
        "DocumentRelationProfile",
        "EmbeddingProfile",
        "UiLocatorProfile",
    }
)
WHITESPACE = re.compile(r"\s+")
EXAMPLE_NAMES = {
    "EmbeddingProfile": ("embedding-profile.example.json",),
    "DocumentConventionProfile": (
        "document-convention-profile.example.json",
        "screen-design-convention-profile.example.json",
    ),
    "DocumentRelationProfile": ("document-relation-profile.example.json",),
    "CodeFrameworkProfile": (
        "code-framework-profile.example.json",
        "polyglot-code-framework-profile.example.json",
        "springboot15-thymeleaf-gradle-code-framework-profile.example.json",
        "struts1-code-framework-profile.example.json",
    ),
    "CommandExecutionProfile": (
        "command-execution-profile.example.json",
        "springboot15-thymeleaf-gradle-command-profile.example.json",
    ),
    "UiLocatorProfile": ("ui-locator-profile.example.json",),
}


class ProfileCatalogError(ValueError):
    """Raised when the Profile catalog itself is malformed or unsafe."""


class ProfileValidationError(ValueError):
    """Raised when a Profile violates its JSON Schema or semantic invariants."""

    def __init__(self, report: ValidationReport) -> None:
        super().__init__("Profile validation failed")
        self.report = report


@dataclass(frozen=True, slots=True)
class ProfileCatalog:
    """A path-confined registry of versioned Profile schemas."""

    root: Path
    schema_paths: dict[str, Path]

    @classmethod
    def load(cls, profiles_root: Path) -> ProfileCatalog:
        """Load the v1 Profile catalog and confine all schema paths to its root."""

        root = profiles_root.resolve()
        raw = cls._load_object(root / "catalog.json")
        if raw.get("catalog_version") != "v1":
            raise ProfileCatalogError("catalog.json must declare catalog_version v1")
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in profiles.items()
        ):
            raise ProfileCatalogError("catalog.json must map Profile types to schema paths")

        schema_paths: dict[str, Path] = {}
        for profile_type, relative_path in profiles.items():
            schema_path = (root / relative_path).resolve()
            if not schema_path.is_relative_to(root):
                raise ProfileCatalogError(f"schema path escapes profiles root: {relative_path}")
            if not schema_path.is_file():
                raise ProfileCatalogError(f"schema file does not exist: {relative_path}")
            schema_paths[profile_type] = schema_path
        return cls(root=root, schema_paths=schema_paths)

    def validate_catalog(self) -> ValidationReport:
        """Validate catalog completeness and every Draft 2020-12 Profile schema."""

        issues: list[ValidationIssue] = []
        actual_types = frozenset(self.schema_paths)
        for missing in sorted(EXPECTED_PROFILE_TYPES - actual_types):
            issues.append(
                ValidationIssue(
                    code="profile.missing_type",
                    message=f"Missing Profile schema: {missing}",
                    location="profiles/catalog.json",
                )
            )
        for unexpected in sorted(actual_types - EXPECTED_PROFILE_TYPES):
            issues.append(
                ValidationIssue(
                    code="profile.unexpected_type",
                    message=f"Unexpected Profile schema: {unexpected}",
                    location="profiles/catalog.json",
                )
            )
        for profile_type, schema_path in sorted(self.schema_paths.items()):
            try:
                schema = self._load_object(schema_path)
                Draft202012Validator.check_schema(schema)
            except (json.JSONDecodeError, SchemaError, ValueError) as error:
                issues.append(
                    ValidationIssue(
                        code="profile.invalid_schema",
                        message=str(error),
                        location=str(schema_path.relative_to(self.root)),
                    )
                )
                continue
            if schema.get("title") != profile_type:
                issues.append(
                    ValidationIssue(
                        code="profile.title_mismatch",
                        message=f"Schema title must be {profile_type}",
                        location=str(schema_path.relative_to(self.root)),
                    )
                )
        return ValidationReport(tuple(issues))

    def validate_examples(self) -> ValidationReport:
        """Require one valid example for each registered Profile type."""

        issues: list[ValidationIssue] = []
        for profile_type in sorted(self.schema_paths):
            example_names = EXAMPLE_NAMES.get(profile_type)
            if example_names is None:
                continue
            for example_name in example_names:
                example_path = self.root / example_name
                if not example_path.is_file():
                    issues.append(
                        ValidationIssue(
                            code="profile.missing_example",
                            message=f"Missing example for {profile_type}",
                            location=example_name,
                        )
                    )
                    continue
                try:
                    self.validate_profile(self._load_object(example_path))
                except ProfileValidationError as error:
                    issues.extend(error.report.issues)
        return ValidationReport(tuple(issues))

    def validate_profile(self, profile: dict[str, Any]) -> None:
        """Validate a Profile schema and cross-field semantic invariants."""

        profile_type = profile.get("profile_type")
        if not isinstance(profile_type, str) or profile_type not in self.schema_paths:
            raise ProfileValidationError(
                ValidationReport(
                    (
                        ValidationIssue(
                            code="profile.unknown_type",
                            message=f"Unknown profile_type: {profile_type!r}",
                            location="profile_type",
                        ),
                    )
                )
            )

        schema = self._load_object(self.schema_paths[profile_type])
        validator = Draft202012Validator(schema)
        issues = [
            ValidationIssue(
                code="profile.schema_violation",
                message=error.message,
                location="/".join(str(part) for part in error.absolute_path) or "$",
            )
            for error in sorted(validator.iter_errors(profile), key=lambda item: list(item.path))
        ]
        if not issues and profile_type == "DocumentConventionProfile":
            issues.extend(self._document_profile_issues(profile))
        if not issues and profile_type == "DocumentRelationProfile":
            issues.extend(self._document_relation_profile_issues(profile))
        if not issues and profile_type == "CodeFrameworkProfile":
            issues.extend(self._framework_profile_issues(profile))
        if not issues and profile_type == "CommandExecutionProfile":
            issues.extend(self._command_execution_profile_issues(profile))
        if issues:
            raise ProfileValidationError(ValidationReport(tuple(issues)))

    @staticmethod
    def _document_profile_issues(profile: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        variants = profile["variants"]
        variant_ids = [str(variant["variant_id"]) for variant in variants]
        if len(variant_ids) != len(set(variant_ids)):
            issues.append(
                ValidationIssue(
                    code="profile.duplicate_variant_id",
                    message="variant_id values must be unique within a Profile",
                    location="variants",
                )
            )
        for index, variant in enumerate(variants):
            weight_sum = sum(float(signal["weight"]) for signal in variant["signals"])
            if not math.isclose(weight_sum, 1.0, abs_tol=1e-9):
                issues.append(
                    ValidationIssue(
                        code="profile.invalid_signal_weights",
                        message=f"Signal weights must sum to 1.0, got {weight_sum}",
                        location=f"variants/{index}/signals",
                    )
                )
            aliases = set(variant["field_aliases"])
            unknown_stable_fields = sorted(set(variant["stable_key_fields"]) - aliases)
            if unknown_stable_fields:
                issues.append(
                    ValidationIssue(
                        code="profile.unknown_stable_key_field",
                        message=f"Stable key fields have no aliases: {unknown_stable_fields}",
                        location=f"variants/{index}/stable_key_fields",
                    )
                )
            stable_fields = set(variant["stable_key_fields"])
            normalizer_fields = set(variant["stable_key_normalizers"])
            if stable_fields != normalizer_fields:
                issues.append(
                    ValidationIssue(
                        code="profile.invalid_stable_key_normalizers",
                        message=(
                            "stable_key_normalizers keys must exactly match stable_key_fields"
                        ),
                        location=f"variants/{index}/stable_key_normalizers",
                    )
                )
            normalized_alias_owners: dict[str, str] = {}
            for canonical_field, field_aliases in variant["field_aliases"].items():
                for alias in field_aliases:
                    normalized_alias = WHITESPACE.sub(
                        " ", unicodedata.normalize("NFKC", str(alias)).strip()
                    ).casefold()
                    existing = normalized_alias_owners.get(normalized_alias)
                    if existing is not None and existing != canonical_field:
                        issues.append(
                            ValidationIssue(
                                code="profile.ambiguous_field_alias",
                                message=(
                                    f"Alias {alias!r} maps to both {existing!r} "
                                    f"and {canonical_field!r}"
                                ),
                                location=f"variants/{index}/field_aliases",
                            )
                        )
                    normalized_alias_owners[normalized_alias] = str(canonical_field)
        return issues

    @staticmethod
    def _document_relation_profile_issues(profile: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        rules = profile["rules"]
        rule_ids = [str(rule["rule_id"]) for rule in rules]
        if len(rule_ids) != len(set(rule_ids)):
            issues.append(
                ValidationIssue(
                    code="profile.duplicate_document_relation_rule_id",
                    message="Document relation rule_id values must be unique",
                    location="rules",
                )
            )
        for index, rule in enumerate(rules):
            source_count = len(rule["source_fields"])
            target_count = len(rule["target_fields"])
            normalizer_count = len(rule["value_normalizers"])
            if source_count != target_count or source_count != normalizer_count:
                issues.append(
                    ValidationIssue(
                        code="profile.invalid_document_relation_field_arity",
                        message=(
                            "source_fields, target_fields, and value_normalizers "
                            "must have equal lengths"
                        ),
                        location=f"rules/{index}",
                    )
                )
        return issues

    @staticmethod
    def _framework_profile_issues(profile: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        domains = [str(policy["change_domain"]) for policy in profile["relation_policies"]]
        if len(domains) != len(set(domains)):
            issues.append(
                ValidationIssue(
                    code="profile.duplicate_relation_domain",
                    message="change_domain values must be unique within relation_policies",
                    location="relation_policies",
                )
            )
        languages = {str(value) for value in profile["languages"]}
        extractors = {str(value) for value in profile["anchor_extractors"]}
        extractor_languages = {
            "config_key": "properties",
            "java_field_access": "java",
            "java_symbol": "java",
            "javascript_symbol": "javascript",
            "junit_test": "java",
            "kotlin_symbol": "kotlin",
            "python_symbol": "python",
            "spring_config_binding": "java",
            "spring_data_access": "java",
            "spring_endpoint": "java",
            "sql_table": "sql",
            "typescript_symbol": "typescript",
        }
        for extractor, language in extractor_languages.items():
            if extractor in extractors and language not in languages:
                issues.append(
                    ValidationIssue(
                        code="profile.extractor_language_missing",
                        message=f"{extractor} requires language {language}",
                        location="anchor_extractors",
                    )
                )
        dependencies = {
            "java_field_access": {"java_symbol"},
            "junit_test": {"java_symbol"},
            "spring_endpoint": {"java_symbol"},
            "spring_config_binding": {"config_key", "java_symbol"},
            "spring_data_access": {"java_symbol", "sql_table"},
            "struts1_mvc": {"java_symbol"},
        }
        for dependent, required in dependencies.items():
            missing = sorted(required - extractors)
            if dependent in extractors and missing:
                issues.append(
                    ValidationIssue(
                        code="profile.extractor_dependency_missing",
                        message=f"{dependent} requires {', '.join(missing)}",
                        location="anchor_extractors",
                    )
                )
        if "web_ui_route" in extractors and not languages.intersection({"javascript", "xml"}):
            issues.append(
                ValidationIssue(
                    code="profile.extractor_language_missing",
                    message="web_ui_route requires language javascript or xml",
                    location="anchor_extractors",
                )
            )
        if "struts1_mvc" in extractors:
            for language in ("java", "xml"):
                if language not in languages:
                    issues.append(
                        ValidationIssue(
                            code="profile.extractor_language_missing",
                            message=f"struts1_mvc requires language {language}",
                            location="anchor_extractors",
                        )
                    )
        return issues

    @staticmethod
    def _command_execution_profile_issues(profile: dict[str, Any]) -> list[ValidationIssue]:
        templates = profile["templates"]
        command_refs = [str(template["command_ref"]) for template in templates]
        issues: list[ValidationIssue] = []
        if len(command_refs) != len(set(command_refs)):
            issues.append(
                ValidationIssue(
                    code="profile.duplicate_command_ref",
                    message="command_ref values must be unique within a Command Profile",
                    location="templates",
                )
            )
        for index, template in enumerate(templates):
            working_directory = str(template["working_directory"])
            executable = str(template["argv"][0])
            if _unsafe_workspace_path(working_directory):
                issues.append(
                    ValidationIssue(
                        code="profile.unsafe_command_working_directory",
                        message="Command working_directory must stay within the Workspace",
                        location=f"templates/{index}/working_directory",
                    )
                )
            if "/" in executable and _unsafe_workspace_path(executable):
                issues.append(
                    ValidationIssue(
                        code="profile.unsafe_command_executable",
                        message="Relative command executable must stay within the Workspace",
                        location=f"templates/{index}/argv/0",
                    )
                )
        return issues

    @staticmethod
    def _load_object(path: Path) -> dict[str, Any]:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Expected a JSON object: {path}")
        return raw


def _unsafe_workspace_path(value: str) -> bool:
    path = Path(value)
    return path.is_absolute() or ".." in path.parts or "\\" in value
