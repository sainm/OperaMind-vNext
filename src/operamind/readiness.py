"""Fail-closed validation for repository-wide MVP completion evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from operamind.golden import GOLDEN_DATASET_DIGEST_ALGORITHM, GoldenDatasetValidator
from operamind.validation import ValidationIssue, ValidationReport

REQUIRED_MVP_GATE_IDS = frozenset(
    {
        "embedding_provider_live",
        "full_local_regression",
        "github_copilot_live",
        "golden_dataset",
        "human_approval_e2e",
        "target_deployment_e2e",
    }
)
REQUIRED_EVIDENCE_TYPE_BY_GATE = {
    "embedding_provider_live": "provider_probe",
    "full_local_regression": "test_report",
    "github_copilot_live": "copilot_session",
    "golden_dataset": "golden_manifest",
    "human_approval_e2e": "human_review",
    "target_deployment_e2e": "deployment_run",
}
EVIDENCE_TEMPLATE_BY_GATE = {
    "embedding_provider_live": "readiness/templates/embedding-provider-live.example.json",
    "full_local_regression": "readiness/templates/full-local-regression.example.json",
    "github_copilot_live": "readiness/templates/github-copilot-live.example.json",
    "golden_dataset": None,
    "human_approval_e2e": "readiness/templates/human-approval-e2e.example.json",
    "target_deployment_e2e": "readiness/templates/target-deployment-e2e.example.json",
}
SOURCE_TREE_DIGEST_ALGORITHM = "operamind-source-tree-v1"
FULL_LOCAL_REGRESSION_EXCLUDED_TESTS = (
    "tests/integration/test_live_embedding_provider.py",
    "tests/integration/test_golden_screen_change.py",
)
FULL_LOCAL_REGRESSION_COMMAND = (
    ".venv/bin/pytest",
    "-q",
    *(f"--ignore={path}" for path in FULL_LOCAL_REGRESSION_EXCLUDED_TESTS),
)
_SOURCE_TREE_FILE_RULES = (
    (".github", frozenset({".yaml", ".yml"})),
    ("contracts", frozenset({".json"})),
    ("drafts", frozenset({".json"})),
    ("migrations", frozenset({".sql"})),
    ("profiles", frozenset({".json"})),
    ("quality", frozenset({".json"})),
    ("scripts", frozenset({".py", ".sh"})),
    ("src", frozenset({".css", ".html", ".js", ".py", ".typed"})),
    ("tests", frozenset({".py"})),
)
_SOURCE_TREE_EXACT_FILES = (
    "pyproject.toml",
    "requirements.lock",
    "readiness/mvp-evidence.schema.json",
    "readiness/mvp-readiness.schema.json",
)
_EFFECTIVE_BLOCKING_REASON_BY_ISSUE = {
    "readiness.source_tree_digest_mismatch": (
        "No verified fixed-command regression result binds the current OperaMind source tree."
    ),
}


def _invalid_evidence_reason(issue_codes: list[str]) -> str:
    for code in issue_codes:
        if code in _EFFECTIVE_BLOCKING_REASON_BY_ISSUE:
            return _EFFECTIVE_BLOCKING_REASON_BY_ISSUE[code]
    return "Referenced readiness Evidence is invalid for the current repository state."


@dataclass(frozen=True, slots=True)
class MvpGateSummary:
    """Compact status for one MVP evidence gate."""

    gate_id: str
    status: str
    expected_evidence_type: str
    evidence_template: str | None
    evidence_count: int
    blocking_reason: str | None
    validation_issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MvpReadinessSummary:
    """Human-readable readiness stage derived from the selected manifest."""

    manifest_status: str
    readiness_stage: str
    passed_gates: tuple[str, ...]
    pending_gates: tuple[str, ...]
    gates: tuple[MvpGateSummary, ...]
    validation_issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MvpReadinessValidator:
    """Validate versioned gate evidence without treating code presence as proof."""

    repository_root: Path

    def validate(
        self,
        manifest_path: Path,
        *,
        require_ready: bool = False,
        require_golden_ready: bool = False,
        golden_manifest_path: Path | None = None,
    ) -> ValidationReport:
        root = self.repository_root.resolve()
        path = manifest_path.resolve()
        if not path.is_relative_to(root):
            return ValidationReport(
                (
                    ValidationIssue(
                        code="readiness.manifest_path_escape",
                        message="MVP readiness manifest must stay within the repository",
                        location=str(manifest_path),
                    ),
                )
            )
        try:
            manifest = self._load_object(path)
            schema = self._load_object(root / "readiness/mvp-readiness.schema.json")
            evidence_schema = self._load_object(root / "readiness/mvp-evidence.schema.json")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return ValidationReport(
                (
                    ValidationIssue(
                        code="readiness.invalid_manifest",
                        message=str(error),
                        location=str(path.relative_to(root)),
                    ),
                )
            )

        issues = self._schema_issues(manifest, schema, path.relative_to(root).as_posix())
        if issues:
            return ValidationReport(tuple(issues))
        selected_golden_manifest = (
            golden_manifest_path.resolve() if golden_manifest_path is not None else None
        )
        gates = self._objects(manifest["gates"])
        gate_ids = [str(gate["gate_id"]) for gate in gates]
        duplicate_ids = sorted({gate_id for gate_id in gate_ids if gate_ids.count(gate_id) > 1})
        if duplicate_ids:
            issues.append(
                ValidationIssue(
                    code="readiness.duplicate_gate",
                    message=f"MVP readiness gate IDs must be unique: {duplicate_ids}",
                    location="gates",
                )
            )
        actual_ids = set(gate_ids)
        missing = sorted(REQUIRED_MVP_GATE_IDS - actual_ids)
        unexpected = sorted(actual_ids - REQUIRED_MVP_GATE_IDS)
        if missing or unexpected:
            issues.append(
                ValidationIssue(
                    code="readiness.gate_set_mismatch",
                    message=f"Gate set mismatch; missing={missing}, unexpected={unexpected}",
                    location="gates",
                )
            )
        gate_by_id = {str(gate["gate_id"]): gate for gate in gates}
        golden_gate = gate_by_id.get("golden_dataset")
        if require_golden_ready and (golden_gate is None or golden_gate.get("status") != "passed"):
            issues.append(
                ValidationIssue(
                    code="readiness.golden_dataset_gate_not_passed",
                    message="Golden Dataset readiness requires passed golden_dataset evidence",
                    location="gates/golden_dataset/status",
                )
            )

        all_passed = all(gate.get("status") == "passed" for gate in gates)
        if (manifest.get("status") == "ready") != all_passed:
            issues.append(
                ValidationIssue(
                    code="readiness.status_inconsistent",
                    message="Manifest status is ready if and only if every required gate passed",
                    location="status",
                )
            )
        all_evidence_ids: set[str] = set()
        all_evidence_paths: set[str] = set()
        for gate_index, gate in enumerate(gates):
            evidence_values = self._objects(gate["evidence_refs"])
            evidence_ids = [str(value["evidence_id"]) for value in evidence_values]
            if gate.get("status") == "pending":
                if evidence_values:
                    issues.append(
                        ValidationIssue(
                            code="readiness.pending_gate_has_evidence",
                            message="Pending MVP gates must not reference passed evidence",
                            location=f"gates/{gate_index}/evidence_refs",
                        )
                    )
                if gate.get("reviewers"):
                    issues.append(
                        ValidationIssue(
                            code="readiness.pending_gate_has_reviewers",
                            message="Pending MVP gates must not name reviewers",
                            location=f"gates/{gate_index}/reviewers",
                        )
                    )
            if gate.get("status") == "passed" and gate.get("blocking_reason"):
                issues.append(
                    ValidationIssue(
                        code="readiness.passed_gate_has_blocking_reason",
                        message="Passed MVP gates must not keep a blocking reason",
                        location=f"gates/{gate_index}/blocking_reason",
                    )
                )
            if len(evidence_ids) != len(set(evidence_ids)):
                issues.append(
                    ValidationIssue(
                        code="readiness.duplicate_evidence",
                        message="Evidence IDs must be unique within one gate",
                        location=f"gates/{gate_index}/evidence_refs",
                    )
                )
            for evidence_index, evidence in enumerate(evidence_values):
                evidence_id = str(evidence["evidence_id"])
                evidence_path = str(evidence["path"])
                if evidence_id in all_evidence_ids:
                    issues.append(
                        ValidationIssue(
                            code="readiness.duplicate_evidence_id",
                            message="Evidence IDs must be unique across the readiness manifest",
                            location=f"gates/{gate_index}/evidence_refs/{evidence_index}/evidence_id",
                        )
                    )
                all_evidence_ids.add(evidence_id)
                if evidence_path in all_evidence_paths:
                    issues.append(
                        ValidationIssue(
                            code="readiness.duplicate_evidence_path",
                            message="Evidence paths must be unique across the readiness manifest",
                            location=f"gates/{gate_index}/evidence_refs/{evidence_index}/path",
                        )
                    )
                all_evidence_paths.add(evidence_path)
            evidence_reviewers: set[str] = set()
            for evidence_index, evidence in enumerate(evidence_values):
                evidence_issues, reviewers = self._evidence_issues(
                    root=root,
                    evidence=evidence,
                    evidence_schema=evidence_schema,
                    gate_id=str(gate["gate_id"]),
                    location=f"gates/{gate_index}/evidence_refs/{evidence_index}",
                    selected_golden_manifest=selected_golden_manifest,
                )
                issues.extend(evidence_issues)
                evidence_reviewers.update(reviewers)
            if gate.get("status") == "passed" and evidence_reviewers != set(gate["reviewers"]):
                issues.append(
                    ValidationIssue(
                        code="readiness.reviewer_mismatch",
                        message="Gate reviewers must exactly match reviewed_by in its Evidence",
                        location=f"gates/{gate_index}/reviewers",
                    )
                )
            expected_type = REQUIRED_EVIDENCE_TYPE_BY_GATE.get(str(gate["gate_id"]))
            if (
                gate.get("status") == "passed"
                and expected_type is not None
                and expected_type
                not in {str(evidence["evidence_type"]) for evidence in evidence_values}
            ):
                issues.append(
                    ValidationIssue(
                        code="readiness.required_evidence_type_missing",
                        message=(
                            f"Passed gate {gate['gate_id']} requires {expected_type} evidence"
                        ),
                        location=f"gates/{gate_index}/evidence_refs",
                    )
                )

        if require_ready:
            if manifest.get("status") != "ready":
                issues.append(
                    ValidationIssue(
                        code="readiness.not_ready",
                        message="MVP readiness manifest status must be ready",
                        location="status",
                    )
                )
            for index, gate in enumerate(gates):
                if gate.get("status") != "passed":
                    issues.append(
                        ValidationIssue(
                            code="readiness.gate_not_passed",
                            message=f"MVP gate is still pending: {gate['gate_id']}",
                            location=f"gates/{index}/status",
                        )
                    )
        return ValidationReport(tuple(issues))

    def validate_candidate(self, candidate_path: Path) -> ValidationReport:
        """Validate a captured result that is complete but still awaits human review."""

        root = self.repository_root.resolve()
        path = candidate_path.resolve()
        if not path.is_relative_to(root):
            return ValidationReport(
                (
                    ValidationIssue(
                        code="readiness.candidate_path_escape",
                        message="Evidence candidate must stay within the repository",
                        location=str(candidate_path),
                    ),
                )
            )
        relative = path.relative_to(root).as_posix()
        pure = PurePosixPath(relative)
        if pure.parts[:2] != ("readiness", "candidates") or not relative.endswith(
            ".candidate.json"
        ):
            return ValidationReport(
                (
                    ValidationIssue(
                        code="readiness.candidate_location_invalid",
                        message=(
                            "Evidence candidates must use readiness/candidates/*.candidate.json"
                        ),
                        location=relative,
                    ),
                )
            )
        try:
            payload = self._load_object(path)
            evidence_schema = self._load_object(root / "readiness/mvp-evidence.schema.json")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return ValidationReport(
                (
                    ValidationIssue(
                        code="readiness.invalid_candidate",
                        message=str(error),
                        location=relative,
                    ),
                )
            )
        issues = self._typed_evidence_schema_issues(payload, evidence_schema, relative)
        if issues:
            return ValidationReport(tuple(issues))
        if payload["review_status"] != "pending":
            issues.append(
                ValidationIssue(
                    code="readiness.candidate_not_pending",
                    message="Evidence candidate review_status must remain pending",
                    location=f"{relative}/review_status",
                )
            )
        issues.extend(self._placeholder_issues(payload, relative))
        issues.extend(
            self._golden_manifest_evidence_issues(
                root=root,
                payload=payload,
                location=relative,
                selected_golden_manifest=None,
                require_ready=False,
            )
        )
        issues.extend(
            self._source_tree_evidence_issues(
                root=root,
                payload=payload,
                location=relative,
            )
        )
        return ValidationReport(tuple(issues))

    def validate_reviewed_evidence(
        self,
        evidence_path: Path,
        *,
        golden_manifest_path: Path | None = None,
    ) -> ValidationReport:
        """Preflight human-reviewed or deterministically verified final evidence."""

        root = self.repository_root.resolve()
        path = evidence_path.resolve()
        if not path.is_relative_to(root):
            return ValidationReport(
                (
                    ValidationIssue(
                        code="readiness.reviewed_evidence_path_escape",
                        message="Reviewed evidence must stay within the repository",
                        location=str(evidence_path),
                    ),
                )
            )
        relative = path.relative_to(root).as_posix()
        pure = PurePosixPath(relative)
        if (
            pure.parts[:2] != ("readiness", "evidence")
            or not relative.endswith(".json")
            or relative.endswith(".example.json")
        ):
            return ValidationReport(
                (
                    ValidationIssue(
                        code="readiness.reviewed_evidence_location_invalid",
                        message="Reviewed evidence must use readiness/evidence/*.json",
                        location=relative,
                    ),
                )
            )
        try:
            payload = self._load_object(path)
            evidence_schema = self._load_object(root / "readiness/mvp-evidence.schema.json")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return ValidationReport(
                (
                    ValidationIssue(
                        code="readiness.invalid_reviewed_evidence",
                        message=str(error),
                        location=relative,
                    ),
                )
            )
        issues = self._typed_evidence_schema_issues(payload, evidence_schema, relative)
        if issues:
            return ValidationReport(tuple(issues))
        if payload["review_status"] not in {"reviewed", "verified"}:
            issues.append(
                ValidationIssue(
                    code="readiness.evidence_not_reviewed",
                    message=(
                        "Final evidence preflight requires review_status reviewed or verified"
                    ),
                    location=f"{relative}/review_status",
                )
            )
        issues.extend(self._placeholder_issues(payload, relative))
        issues.extend(
            self._golden_manifest_evidence_issues(
                root=root,
                payload=payload,
                location=relative,
                selected_golden_manifest=(
                    golden_manifest_path.resolve() if golden_manifest_path is not None else None
                ),
                require_ready=True,
            )
        )
        issues.extend(
            self._source_tree_evidence_issues(
                root=root,
                payload=payload,
                location=relative,
            )
        )
        return ValidationReport(tuple(issues))

    def summarize(self, manifest_path: Path) -> MvpReadinessSummary:
        """Return one effective status derived from manifest and Evidence validation."""

        root = self.repository_root.resolve()
        path = manifest_path.resolve()
        if not path.is_relative_to(root):
            raise ValueError("MVP readiness manifest must stay within the repository")
        manifest = self._load_object(path)
        schema = self._load_object(root / "readiness/mvp-readiness.schema.json")
        schema_issues = self._schema_issues(
            manifest,
            schema,
            path.relative_to(root).as_posix(),
        )
        if schema_issues:
            raise ValueError(
                "; ".join(f"{issue.code}: {issue.location}" for issue in schema_issues)
            )
        gates = self._objects(manifest["gates"])
        report = self.validate(path)
        issues_by_gate: dict[int, list[str]] = {}
        for issue in report.issues:
            parts = issue.location.split("/")
            if len(parts) >= 2 and parts[0] == "gates" and parts[1].isdigit():
                gate_index = int(parts[1])
                if gate_index < len(gates):
                    issues_by_gate.setdefault(gate_index, []).append(issue.code)
        gate_summaries = tuple(
            MvpGateSummary(
                gate_id=str(gate["gate_id"]),
                status="pending" if index in issues_by_gate else str(gate["status"]),
                expected_evidence_type=REQUIRED_EVIDENCE_TYPE_BY_GATE[str(gate["gate_id"])],
                evidence_template=EVIDENCE_TEMPLATE_BY_GATE[str(gate["gate_id"])],
                evidence_count=(
                    0 if index in issues_by_gate else len(self._objects(gate["evidence_refs"]))
                ),
                blocking_reason=(
                    _invalid_evidence_reason(issues_by_gate[index])
                    if index in issues_by_gate
                    else str(gate["blocking_reason"])
                    if gate.get("blocking_reason")
                    else None
                ),
                validation_issues=tuple(issues_by_gate.get(index, ())),
            )
            for index, gate in enumerate(gates)
        )
        passed = tuple(gate.gate_id for gate in gate_summaries if gate.status == "passed")
        pending = tuple(gate.gate_id for gate in gate_summaries if gate.status == "pending")
        manifest_status = str(manifest["status"]) if report.is_valid else "stale"
        if manifest_status == "ready" and not pending:
            stage = "mvp_ready"
        elif "golden_dataset" in passed:
            stage = "golden_ready_partial"
        elif not passed:
            stage = "dev_silver"
        else:
            stage = "partial_ready"
        return MvpReadinessSummary(
            manifest_status=manifest_status,
            readiness_stage=stage,
            passed_gates=passed,
            pending_gates=pending,
            gates=gate_summaries,
            validation_issues=tuple(issue.code for issue in report.issues),
        )

    @staticmethod
    def _evidence_issues(
        *,
        root: Path,
        evidence: dict[str, Any],
        evidence_schema: dict[str, Any],
        gate_id: str,
        location: str,
        selected_golden_manifest: Path | None,
    ) -> tuple[list[ValidationIssue], set[str]]:
        raw_path = str(evidence["path"])
        pure = PurePosixPath(raw_path)
        if "\\" in raw_path or pure.is_absolute() or ".." in pure.parts:
            return (
                [
                    ValidationIssue(
                        code="readiness.evidence_path_escape",
                        message=f"Evidence path must be repository-relative POSIX: {raw_path}",
                        location=f"{location}/path",
                    )
                ],
                set(),
            )
        if raw_path.endswith(".example.json") or "templates" in pure.parts:
            return (
                [
                    ValidationIssue(
                        code="readiness.evidence_template_referenced",
                        message="Template evidence files cannot be referenced as passed evidence",
                        location=f"{location}/path",
                    )
                ],
                set(),
            )
        if pure.parts[:2] != ("readiness", "evidence"):
            return (
                [
                    ValidationIssue(
                        code="readiness.evidence_location_invalid",
                        message="Passed evidence files must live under readiness/evidence",
                        location=f"{location}/path",
                    )
                ],
                set(),
            )
        path = (root / raw_path).resolve()
        if not path.is_relative_to(root):
            return (
                [
                    ValidationIssue(
                        code="readiness.evidence_path_escape",
                        message=f"Evidence path escapes repository: {raw_path}",
                        location=f"{location}/path",
                    )
                ],
                set(),
            )
        if not path.is_file():
            return (
                [
                    ValidationIssue(
                        code="readiness.evidence_missing",
                        message=f"Evidence file does not exist: {raw_path}",
                        location=f"{location}/path",
                    )
                ],
                set(),
            )
        actual_digest = MvpReadinessValidator._file_digest(path)
        if actual_digest != evidence["sha256"]:
            return (
                [
                    ValidationIssue(
                        code="readiness.evidence_digest_mismatch",
                        message=f"Evidence SHA-256 does not match: {raw_path}",
                        location=f"{location}/sha256",
                    )
                ],
                set(),
            )
        try:
            payload = MvpReadinessValidator._load_object(path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return (
                [
                    ValidationIssue(
                        code="readiness.invalid_evidence",
                        message=str(error),
                        location=location,
                    )
                ],
                set(),
            )
        schema_issues = MvpReadinessValidator._typed_evidence_schema_issues(
            payload, evidence_schema, location
        )
        if schema_issues:
            return schema_issues, set()
        if payload["review_status"] not in {"reviewed", "verified"}:
            return (
                [
                    ValidationIssue(
                        code="readiness.evidence_not_reviewed",
                        message=(
                            "Passed gate evidence must have review_status reviewed or verified"
                        ),
                        location=f"{location}/review_status",
                    )
                ],
                set(),
            )
        placeholder_issues = MvpReadinessValidator._placeholder_issues(payload, location)
        if placeholder_issues:
            return placeholder_issues, set()
        identity = (
            str(payload["evidence_id"]),
            str(payload["gate_id"]),
            str(payload["evidence_type"]),
            str(payload["observed_at"]),
        )
        expected = (
            str(evidence["evidence_id"]),
            gate_id,
            str(evidence["evidence_type"]),
            str(evidence["observed_at"]),
        )
        if identity != expected:
            return (
                [
                    ValidationIssue(
                        code="readiness.evidence_identity_mismatch",
                        message="Evidence envelope does not match its manifest reference",
                        location=location,
                    )
                ],
                set(),
            )
        issues = MvpReadinessValidator._golden_manifest_evidence_issues(
            root=root,
            payload=payload,
            location=location,
            selected_golden_manifest=selected_golden_manifest,
            require_ready=True,
        )
        issues.extend(
            MvpReadinessValidator._source_tree_evidence_issues(
                root=root,
                payload=payload,
                location=location,
            )
        )
        return issues, {str(value) for value in payload["reviewed_by"]}

    @staticmethod
    def _placeholder_issues(payload: object, location: str) -> list[ValidationIssue]:
        blocked = ("replace-with", "placeholder", "example.invalid")
        issues: list[ValidationIssue] = []

        def visit(value: object, path: tuple[str, ...]) -> None:
            if isinstance(value, str) and any(token in value.lower() for token in blocked):
                issues.append(
                    ValidationIssue(
                        code="readiness.evidence_placeholder",
                        message="Passed evidence must not contain template placeholder values",
                        location=f"{location}/{'/'.join(path)}",
                    )
                )
            elif isinstance(value, dict):
                for key, child in value.items():
                    visit(child, (*path, str(key)))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, (*path, str(index)))

        visit(payload, ())
        return issues

    @staticmethod
    def _source_tree_evidence_issues(
        *, root: Path, payload: dict[str, Any], location: str
    ) -> list[ValidationIssue]:
        if payload["evidence_type"] != "test_report":
            return []
        issues: list[ValidationIssue] = []
        subject = payload["subject"]
        if not isinstance(subject, dict):
            raise AssertionError("Evidence subject passed schema validation")
        try:
            actual_digest = MvpReadinessValidator.source_tree_digest(root)
        except (OSError, ValueError) as error:
            return [
                ValidationIssue(
                    code="readiness.source_tree_invalid",
                    message=str(error),
                    location=f"{location}/subject/source_tree_sha256",
                )
            ]
        if subject["source_tree_algorithm"] != SOURCE_TREE_DIGEST_ALGORITHM:
            raise AssertionError("Source tree algorithm passed schema validation")
        if subject["source_tree_sha256"] != actual_digest:
            issues.append(
                ValidationIssue(
                    code="readiness.source_tree_digest_mismatch",
                    message="Test Report does not bind the current OperaMind source tree",
                    location=f"{location}/subject/source_tree_sha256",
                )
            )
        if tuple(subject["test_command"]) != FULL_LOCAL_REGRESSION_COMMAND:
            issues.append(
                ValidationIssue(
                    code="readiness.full_regression_command_mismatch",
                    message="Test Report must use the fixed full local regression command",
                    location=f"{location}/subject/test_command",
                )
            )
        if tuple(subject["excluded_tests"]) != FULL_LOCAL_REGRESSION_EXCLUDED_TESTS:
            issues.append(
                ValidationIssue(
                    code="readiness.full_regression_exclusions_mismatch",
                    message="Test Report exclusions must exactly match the separate external gates",
                    location=f"{location}/subject/excluded_tests",
                )
            )
        if subject["passed"] != subject["collected"]:
            issues.append(
                ValidationIssue(
                    code="readiness.full_regression_count_mismatch",
                    message="Every collected full-regression test must pass",
                    location=f"{location}/subject/passed",
                )
            )
        return issues

    @staticmethod
    def _golden_manifest_evidence_issues(
        *,
        root: Path,
        payload: dict[str, Any],
        location: str,
        selected_golden_manifest: Path | None,
        require_ready: bool,
    ) -> list[ValidationIssue]:
        if payload["evidence_type"] != "golden_manifest":
            return []
        subject = payload["subject"]
        if not isinstance(subject, dict):
            raise AssertionError("Evidence subject passed schema validation")
        raw_path = str(subject["manifest_path"])
        pure = PurePosixPath(raw_path)
        path = (root / raw_path).resolve()
        if (
            "\\" in raw_path
            or pure.is_absolute()
            or ".." in pure.parts
            or not path.is_relative_to(root)
        ):
            return [
                ValidationIssue(
                    code="readiness.golden_manifest_path_escape",
                    message="Golden manifest evidence path escapes the repository",
                    location=f"{location}/subject/manifest_path",
                )
            ]
        if not path.is_file():
            return [
                ValidationIssue(
                    code="readiness.golden_manifest_missing",
                    message=f"Golden manifest evidence does not exist: {raw_path}",
                    location=f"{location}/subject/manifest_path",
                )
            ]
        issues: list[ValidationIssue] = []
        if selected_golden_manifest is not None and path != selected_golden_manifest:
            issues.append(
                ValidationIssue(
                    code="readiness.golden_manifest_selection_mismatch",
                    message=(
                        "Golden evidence must bind the manifest selected for baseline validation"
                    ),
                    location=f"{location}/subject/manifest_path",
                )
            )
        if MvpReadinessValidator._file_digest(path) != subject["manifest_sha256"]:
            return [
                ValidationIssue(
                    code="readiness.golden_manifest_digest_mismatch",
                    message="Golden manifest evidence SHA-256 does not match",
                    location=f"{location}/subject/manifest_sha256",
                )
            ]
        golden_root = root / "golden-dataset"
        if not path.is_relative_to(golden_root.resolve()):
            issues.append(
                ValidationIssue(
                    code="readiness.golden_manifest_location_invalid",
                    message="Golden evidence must reference a manifest inside golden-dataset",
                    location=f"{location}/subject/manifest_path",
                )
            )
            return issues
        try:
            actual_dataset_digest = GoldenDatasetValidator(golden_root).dataset_digest(path)
        except (OSError, ValueError) as error:
            return [
                ValidationIssue(
                    code="readiness.invalid_golden_dataset_digest",
                    message=str(error),
                    location=f"{location}/subject/dataset_sha256",
                )
            ]
        if (
            subject["dataset_digest_algorithm"] != GOLDEN_DATASET_DIGEST_ALGORITHM
            or subject["dataset_sha256"] != actual_dataset_digest
        ):
            return [
                ValidationIssue(
                    code="readiness.golden_dataset_digest_mismatch",
                    message="Golden evidence does not bind the current referenced dataset files",
                    location=f"{location}/subject/dataset_sha256",
                )
            ]
        golden_report = GoldenDatasetValidator(golden_root).validate(
            path,
            require_ready=require_ready,
        )
        for issue in golden_report.issues:
            issues.append(
                ValidationIssue(
                    code=f"readiness.{issue.code}",
                    message=issue.message,
                    location=f"{location}/subject/manifest/{issue.location}",
                )
            )
        try:
            golden_manifest = MvpReadinessValidator._load_object(path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            issues.append(
                ValidationIssue(
                    code="readiness.invalid_golden_manifest_evidence",
                    message=str(error),
                    location=f"{location}/subject/manifest_path",
                )
            )
            return issues
        projects = golden_manifest.get("projects")
        cases = golden_manifest.get("cases")
        actual_identity = (
            golden_manifest.get("dataset_id"),
            golden_manifest.get("dataset_version"),
            len(projects) if isinstance(projects, list) else None,
            len(cases) if isinstance(cases, list) else None,
            golden_manifest.get("status"),
        )
        evidence_identity = (
            subject["dataset_id"],
            subject["dataset_version"],
            subject["project_count"],
            subject["case_count"],
            subject["status"],
        )
        if actual_identity != evidence_identity:
            issues.append(
                ValidationIssue(
                    code="readiness.golden_manifest_identity_mismatch",
                    message=("Golden evidence identity and counts do not match its manifest"),
                    location=f"{location}/subject",
                )
            )
        return issues

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def source_tree_digest(repository_root: Path) -> str:
        """Digest the executable MVP source and verification inputs deterministically."""

        root = repository_root.resolve()
        selected: set[Path] = set()
        for relative_root, suffixes in _SOURCE_TREE_FILE_RULES:
            directory = root / relative_root
            if not directory.is_dir():
                continue
            for path in directory.rglob("*"):
                if path.suffix in suffixes and (path.is_file() or path.is_symlink()):
                    selected.add(path)
        for relative_path in _SOURCE_TREE_EXACT_FILES:
            path = root / relative_path
            if path.is_file() or path.is_symlink():
                selected.add(path)
        if not selected:
            raise ValueError("OperaMind source tree digest has no selected files")
        digest = sha256()
        digest.update(f"{SOURCE_TREE_DIGEST_ALGORITHM}\0".encode())
        for path in sorted(selected, key=lambda value: value.relative_to(root).as_posix()):
            if path.is_symlink():
                raise ValueError(
                    "OperaMind source tree digest does not allow symlinks: "
                    f"{path.relative_to(root).as_posix()}"
                )
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError("OperaMind source tree file escapes the repository")
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(bytes.fromhex(MvpReadinessValidator._file_digest(path)))
            digest.update(b"\n")
        return digest.hexdigest()

    @staticmethod
    def _schema_issues(
        manifest: dict[str, Any], schema: dict[str, Any], location: str
    ) -> list[ValidationIssue]:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        return [
            ValidationIssue(
                code="readiness.schema_violation",
                message=error.message,
                location=("/".join(str(part) for part in error.absolute_path) or location),
            )
            for error in sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
        ]

    @staticmethod
    def _typed_evidence_schema_issues(
        payload: dict[str, Any], schema: dict[str, Any], location: str
    ) -> list[ValidationIssue]:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        return [
            ValidationIssue(
                code="readiness.evidence_schema_violation",
                message=error.message,
                location=(
                    f"{location}/" + "/".join(str(part) for part in error.absolute_path)
                ).rstrip("/"),
            )
            for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
        ]

    @staticmethod
    def _objects(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError("Expected a list of objects after schema validation")
        return value

    @staticmethod
    def _load_object(path: Path) -> dict[str, Any]:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Expected JSON object: {path}")
        return raw
