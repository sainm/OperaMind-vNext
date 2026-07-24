"""Trusted, reviewed configuration for one executable change-loop case."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from operamind.domain.ui_execution import BrowserScenarioSpec
from operamind.infrastructure.code_graph import TextReplacement


@dataclass(frozen=True, slots=True)
class ChangeLoopCase:
    """Configuration that turns a reviewed document change into executable work."""

    root: Path
    payload: dict[str, Any]

    @classmethod
    def load(
        cls,
        case_root: Path,
        *,
        require_approved: bool = True,
        schema_path: Path | None = None,
    ) -> ChangeLoopCase:
        root = case_root.resolve(strict=True)
        path = root / "change-loop-case.json"
        value: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object: {path}")
        return cls.from_payload(
            root=root,
            payload=cast(dict[str, Any], value),
            require_approved=require_approved,
            schema_path=schema_path,
        )

    @classmethod
    def from_payload(
        cls,
        *,
        root: Path,
        payload: dict[str, Any],
        require_approved: bool = True,
        schema_path: Path | None = None,
    ) -> ChangeLoopCase:
        """Validate an in-memory proposal before it is allowed onto disk."""

        resolved_root = root.resolve(strict=True)
        resolved_schema = schema_path or _find_schema(resolved_root)
        schema: object = json.loads(resolved_schema.read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise ValueError(f"Expected JSON Schema object: {resolved_schema}")
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "$"
            raise ValueError(f"Invalid change-loop case at {location}: {first.message}")
        case = cls(root=resolved_root, payload=payload)
        case._validate()
        if require_approved and not case.is_approved:
            raise ValueError("Only approved change-loop case configurations are executable")
        return case

    @property
    def case_id(self) -> str:
        return str(self.payload["case_id"])

    @property
    def project_id(self) -> str:
        return str(self.payload["project_id"])

    @property
    def repository(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.payload["repository"])

    @property
    def document(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.payload["document"])

    @property
    def requirements(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.payload["requirements"])

    @property
    def analysis(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.payload["analysis"])

    @property
    def impact_candidates(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.payload["impact_candidates"])

    @property
    def edit(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.payload["edit"])

    @property
    def acceptance_criteria(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.payload["acceptance_criteria"])

    @property
    def test_cases(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.payload["test_cases"])

    @property
    def data_sets(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.payload["data_sets"])

    @property
    def data_generation_flows(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.payload.get("data_generation_flows", []))

    @property
    def execution(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.payload["execution"])

    @property
    def review(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.payload["review"])

    @property
    def is_approved(self) -> bool:
        return bool(self.review.get("review_status") == "approved")

    @property
    def canonical_requirement(self) -> str:
        return str(self.requirements.get("canonical_requirement", "")).strip()

    @property
    def replacements(self) -> tuple[TextReplacement, ...]:
        return tuple(
            TextReplacement(
                path=str(value["path"]),
                before=str(value["before"]),
                after=str(value["after"]),
            )
            for value in cast(list[dict[str, Any]], self.edit["replacements"])
        )

    @property
    def editable_paths(self) -> frozenset[str]:
        return frozenset(value.path for value in self.replacements)

    @property
    def forbidden_paths(self) -> frozenset[str]:
        return frozenset(cast(list[str], self.edit["forbidden_paths"]))

    def _validate(self) -> None:
        required = {
            "schema_version",
            "case_id",
            "project_id",
            "repository",
            "document",
            "requirements",
            "analysis",
            "impact_candidates",
            "edit",
            "acceptance_criteria",
            "test_cases",
            "data_sets",
            "execution",
            "review",
        }
        missing = sorted(required - self.payload.keys())
        if missing:
            raise ValueError(f"Change-loop case is missing fields: {missing}")
        if self.payload["schema_version"] != "v1":
            raise ValueError("Unsupported change-loop case schema_version")
        if not self.case_id.strip() or not self.project_id.strip():
            raise ValueError("Change-loop case identity must not be blank")
        repository = self.repository
        for key in ("repository_id", "base_revision", "application_root", "scan_roots"):
            if key not in repository:
                raise ValueError(f"Change-loop repository is missing {key}")
        _safe_path(str(repository["application_root"]))
        for value in cast(list[str], repository["scan_roots"]):
            _safe_path(value)
        document = self.document
        if document.get("domain") != "ui" or document.get("fact_type") != "screen_element":
            raise ValueError("Only reviewed UI screen-element cases are executable")
        if not self.requirements.get("business_rules"):
            raise ValueError("Change-loop case must define business_rules")
        business_rules = cast(list[dict[str, Any]], self.requirements["business_rules"])
        rule_id_values = [str(value["business_rule_id"]) for value in business_rules]
        if len(rule_id_values) != len(set(rule_id_values)):
            raise ValueError("Change-loop business_rule_id values must be unique")
        rule_ids = set(rule_id_values)
        if not self.impact_candidates or not self.acceptance_criteria or not self.test_cases:
            raise ValueError("Change-loop case must define impact, acceptance and tests")
        test_ids = [str(value["test_case_id"]) for value in self.test_cases]
        if len(test_ids) != len(set(test_ids)):
            raise ValueError("Change-loop test_case_id values must be unique")
        criterion_id_values = [str(value["criterion_id"]) for value in self.acceptance_criteria]
        if len(criterion_id_values) != len(set(criterion_id_values)):
            raise ValueError("Change-loop criterion_id values must be unique")
        criterion_ids = set(criterion_id_values)
        if any(
            not set(cast(list[str], value["business_rule_refs"])).issubset(rule_ids)
            for value in self.acceptance_criteria
        ):
            raise ValueError("Acceptance Criteria reference an unknown business rule")
        if any(
            not set(cast(list[str], value["business_rule_refs"])).issubset(rule_ids)
            for value in self.test_cases
        ):
            raise ValueError("Test cases reference an unknown business rule")
        referenced_criteria = {
            str(reference)
            for test in self.test_cases
            for reference in cast(list[str], test["acceptance_criteria_refs"])
        }
        if criterion_ids != referenced_criteria:
            raise ValueError("Configured tests must cover every acceptance criterion exactly")
        for criterion in self.acceptance_criteria:
            criterion_id = str(criterion["criterion_id"])
            declared = set(cast(list[str], criterion["test_case_refs"]))
            actual = {
                str(test["test_case_id"])
                for test in self.test_cases
                if criterion_id in cast(list[str], test["acceptance_criteria_refs"])
            }
            if declared != actual:
                raise ValueError(f"Acceptance criterion test references disagree: {criterion_id}")
        candidate_values = [str(value["path"]) for value in self.impact_candidates]
        if len(candidate_values) != len(set(candidate_values)):
            raise ValueError("Change-loop impact candidate paths must be unique")
        candidates = set(candidate_values)
        for path in candidates:
            _safe_path(path)
        replacements = cast(list[dict[str, Any]], self.edit.get("replacements", []))
        if not replacements:
            raise ValueError("Change-loop case must define at least one exact replacement")
        replacement_paths = [str(value["path"]) for value in replacements]
        if len(replacement_paths) != len(set(replacement_paths)):
            raise ValueError("Change-loop replacement paths must be unique")
        for replacement in replacements:
            path = str(replacement["path"])
            _safe_path(path)
            if path not in candidates:
                raise ValueError(f"Replacement path is not an impact candidate: {path}")
            if not str(replacement["before"]) or not str(replacement["after"]):
                raise ValueError("Exact replacements must define non-empty before and after text")
        for path in cast(list[str], self.edit["forbidden_paths"]):
            _safe_path(path)
        forbidden = set(cast(list[str], self.edit["forbidden_paths"]))
        if set(replacement_paths) & forbidden:
            raise ValueError("Change-loop replacement paths must not be forbidden")
        allowed_item_paths = [
            str(value["path"]) for value in cast(list[dict[str, Any]], self.edit["allowed_items"])
        ]
        if len(allowed_item_paths) != len(set(allowed_item_paths)):
            raise ValueError("Change-loop allowed item paths must be unique")
        if set(allowed_item_paths) != set(replacement_paths):
            raise ValueError("Change-loop allowed items must match replacement paths")
        data_id_values = [str(value["test_data_id"]) for value in self.data_sets]
        if len(data_id_values) != len(set(data_id_values)):
            raise ValueError("Change-loop test_data_id values must be unique")
        data_ids = set(data_id_values)
        if any(
            not set(cast(list[str], value["test_data_refs"])).issubset(data_ids)
            for value in self.test_cases
        ):
            raise ValueError("Test cases reference unknown configured test data")
        if any(
            not set(cast(list[str], value["test_case_refs"])).issubset(test_ids)
            for value in self.data_sets
        ):
            raise ValueError("Test data references an unknown test case")
        for data_set in self.data_sets:
            data_id = str(data_set["test_data_id"])
            declared = set(cast(list[str], data_set["test_case_refs"]))
            actual = {
                str(test["test_case_id"])
                for test in self.test_cases
                if data_id in cast(list[str], test["test_data_refs"])
            }
            if declared != actual:
                raise ValueError(f"Test data references disagree: {data_id}")
        if self.data_generation_flows:
            from operamind.application.test_data_flow import (
                validate_test_data_plan_flows,
            )

            flow_errors = validate_test_data_plan_flows(self, deepcopy(self.data_generation_flows))
            if flow_errors:
                raise ValueError("Invalid test-data generation flows: " + "; ".join(flow_errors))
        execution = self.execution
        for key in (
            "source_tests",
            "health_request",
            "api_tests",
            "setup_requests",
            "browser_phases",
            "browser_scenarios",
        ):
            if key not in execution:
                raise ValueError(f"Change-loop execution is missing {key}")
        _validate_http_request(cast(dict[str, Any], execution["health_request"]))
        for api_test in cast(list[dict[str, Any]], execution["api_tests"]):
            _validate_http_request(cast(dict[str, Any], api_test["request"]))
        for setup_request in cast(list[dict[str, Any]], execution["setup_requests"]):
            _validate_http_request(cast(dict[str, Any], setup_request["request"]))
        source_id_values = [
            str(value["test_case_id"])
            for value in cast(list[dict[str, Any]], execution["source_tests"])
        ]
        api_id_values = [
            str(value["test_case_id"])
            for value in cast(list[dict[str, Any]], execution["api_tests"])
        ]
        if len(source_id_values) != len(set(source_id_values)) or len(api_id_values) != len(
            set(api_id_values)
        ):
            raise ValueError("Configured source/API execution IDs must be unique")
        configured_source_ids = set(source_id_values)
        configured_api_ids = set(api_id_values)
        expected_source_ids = {
            str(value["test_case_id"]) for value in self.test_cases if value["level"] == "source"
        }
        expected_api_ids = {
            str(value["test_case_id"]) for value in self.test_cases if value["level"] == "api"
        }
        if configured_source_ids != expected_source_ids or configured_api_ids != expected_api_ids:
            raise ValueError("Configured source/API execution must match their test cases")
        scenario_id_values = [
            str(value["scenario_id"])
            for value in cast(list[dict[str, Any]], execution["browser_scenarios"])
        ]
        if len(scenario_id_values) != len(set(scenario_id_values)):
            raise ValueError("Configured browser scenario IDs must be unique")
        scenario_ids = set(scenario_id_values)
        configured_ui_ids = {
            str(value["test_case_id"]) for value in self.test_cases if value["level"] == "ui"
        }
        if scenario_ids != configured_ui_ids:
            raise ValueError("Browser scenarios and UI test cases must have identical IDs")
        for configured_scenario in cast(list[dict[str, Any]], execution["browser_scenarios"]):
            scenario = deepcopy(configured_scenario)
            scenario["impact_item_refs"] = ["configured-impact-item"]
            BrowserScenarioSpec.from_dict(scenario)
        phase_scenarios = [
            str(value)
            for phase in cast(list[dict[str, Any]], execution["browser_phases"])
            for value in cast(list[str], phase["scenario_ids"])
        ]
        if (
            len(phase_scenarios) != len(set(phase_scenarios))
            or set(phase_scenarios) != scenario_ids
        ):
            raise ValueError("Browser phases must schedule every UI scenario exactly once")
        phase_ids = [
            str(value["phase_id"])
            for value in cast(list[dict[str, Any]], execution["browser_phases"])
        ]
        if len(phase_ids) != len(set(phase_ids)):
            raise ValueError("Configured browser phase IDs must be unique")
        setup_ids = [
            str(value["setup_id"])
            for value in cast(list[dict[str, Any]], execution["setup_requests"])
        ]
        if len(setup_ids) != len(set(setup_ids)):
            raise ValueError("Configured setup IDs must be unique")


def _find_schema(root: Path) -> Path:
    for parent in (root, *root.parents):
        candidate = parent / "change-loop-case.schema.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"change-loop-case.schema.json was not found above {root}")


def _safe_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not value.strip():
        raise ValueError(f"Unsafe repository-relative path: {value}")


def _validate_http_request(value: dict[str, Any]) -> None:
    method = str(value.get("method", "GET")).upper()
    path = str(value.get("path", ""))
    if method not in {"GET", "POST"}:
        raise ValueError(f"Unsupported configured HTTP method: {method}")
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise ValueError(f"HTTP request must use an origin-relative path: {path}")
