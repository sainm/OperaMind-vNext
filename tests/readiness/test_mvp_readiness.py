import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from operamind.commands.baseline import main as baseline_main
from operamind.golden import GoldenDatasetValidator
from operamind.readiness import (
    FULL_LOCAL_REGRESSION_COMMAND,
    FULL_LOCAL_REGRESSION_EXCLUDED_TESTS,
    REQUIRED_EVIDENCE_TYPE_BY_GATE,
    REQUIRED_MVP_GATE_IDS,
    SOURCE_TREE_DIGEST_ALGORITHM,
    MvpReadinessValidator,
)

ROOT = Path(__file__).parents[2]
OBSERVED_AT = "2026-07-16T12:00:00Z"


def _subject(
    gate_id: str,
    *,
    golden_manifest_sha256: str,
    source_tree_sha256: str,
    golden_dataset_id: str = "test-golden",
    golden_dataset_version: str = "1.0.0",
    golden_dataset_sha256: str = "0" * 64,
    golden_project_count: int = 1,
    golden_case_count: int = 1,
) -> dict[str, object]:
    values: dict[str, dict[str, object]] = {
        "golden_dataset": {
            "dataset_id": golden_dataset_id,
            "dataset_version": golden_dataset_version,
            "manifest_path": "golden-dataset/manifest.golden.json",
            "manifest_sha256": golden_manifest_sha256,
            "dataset_digest_algorithm": "operamind-golden-dataset-v1",
            "dataset_sha256": golden_dataset_sha256,
            "project_count": golden_project_count,
            "case_count": golden_case_count,
            "status": "frozen",
        },
        "embedding_provider_live": {
            "profile_version_id": "embedding-profile@1.0.0",
            "model": "embedding-model",
            "dimensions": 1536,
            "endpoint_origin": "https://embedding.internal",
            "test_command": ["pytest", "test_live_embedding_provider.py"],
            "exit_code": 0,
        },
        "human_approval_e2e": {
            "project_id": "project-1",
            "analysis_case_id": "case-1",
            "impact_report_id": "impact-1",
            "confirmation_id": "confirmation-1",
            "approval_grant_id": "grant-1",
            "decision": "approved",
        },
        "github_copilot_live": {
            "project_id": "project-1",
            "analysis_case_id": "case-1",
            "coding_task_id": "coding-task-1",
            "vscode_session_id": "vscode-session-1",
            "vscode_request_id": "vscode-request-1",
            "vscode_response_id": "vscode-response-1",
            "copilot_extension_version": "1.0.0",
            "copilot_model_id": "copilot/auto",
            "session_transcript_sha256": "c" * 64,
            "completed_mcp_tools": [
                "copilot_get_coding_task",
                "copilot_record_change_outputs",
                "copilot_run_task_command",
                "copilot_validate_task_diff",
                "copilot_record_task_result",
            ],
            "edit_packet_id": "packet-1",
            "approval_grant_id": "grant-1",
            "base_repository_revision": "base-commit",
            "result_repository_revision": "result-commit",
            "mcp_protocol_version": "2025-11-25",
            "tool_approval_status": "confirmed",
        },
        "target_deployment_e2e": {
            "project_id": "project-1",
            "analysis_case_id": "case-1",
            "ui_execution_plan_id": "plan-1",
            "ui_execution_run_id": "run-1",
            "verification_result_id": "result-1",
            "environment_id": "environment-1",
            "deployment_revision": "deployment-1",
            "repository_revision": "result-commit",
            "status": "passed",
            "evidence_ids": ["screenshot-1", "assertion-1"],
        },
        "full_local_regression": {
            "source_tree_algorithm": SOURCE_TREE_DIGEST_ALGORITHM,
            "source_tree_sha256": source_tree_sha256,
            "test_command": list(FULL_LOCAL_REGRESSION_COMMAND),
            "excluded_tests": list(FULL_LOCAL_REGRESSION_EXCLUDED_TESTS),
            "collected": 100,
            "passed": 100,
            "failed": 0,
            "skipped": 0,
            "database_version": "PostgreSQL 18",
            "browser_version": "Chrome 149",
        },
    }
    return values[gate_id]


def _promote_golden_fixture(dataset_root: Path) -> Path:
    return dataset_root / "manifest.golden.json"


def _repository_with_current_readiness(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    for directory in ("contracts", "golden-dataset", "profiles", "readiness"):
        shutil.copytree(ROOT / directory, repository / directory)
    source_root = repository / "src"
    source_root.mkdir()
    (source_root / "baseline.py").write_text("VALUE = 'tested'\n", encoding="utf-8")

    manifest_path = repository / "readiness/mvp-readiness.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    gate = next(
        item for item in manifest["gates"] if item["gate_id"] == "full_local_regression"
    )
    evidence_ref = gate["evidence_refs"][0]
    evidence_path = repository / evidence_ref["path"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["subject"]["source_tree_sha256"] = MvpReadinessValidator.source_tree_digest(
        repository
    )
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    evidence_ref["sha256"] = sha256(evidence_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return repository


def test_pending_manifest_is_valid_but_not_mvp_ready() -> None:
    validator = MvpReadinessValidator(ROOT)
    path = ROOT / "readiness/mvp-readiness.silver.json"

    structural = validator.validate(path)
    ready = validator.validate(path, require_ready=True)
    summary = validator.summarize(path)

    assert structural.is_valid, structural.issues
    assert not ready.is_valid
    assert summary.readiness_stage == "dev_silver"
    assert summary.passed_gates == ()
    assert set(summary.pending_gates) == REQUIRED_MVP_GATE_IDS
    assert {issue.code for issue in ready.issues} == {
        "readiness.gate_not_passed",
        "readiness.not_ready",
    }


def test_repository_manifest_records_finalized_local_gates() -> None:
    validator = MvpReadinessValidator(ROOT)
    path = ROOT / "readiness/mvp-readiness.json"

    structural = validator.validate(
        path,
        golden_manifest_path=ROOT / "golden-dataset/manifest.golden.json",
    )
    ready = validator.validate(
        path,
        require_ready=True,
        golden_manifest_path=ROOT / "golden-dataset/manifest.golden.json",
    )
    summary = validator.summarize(path)

    issue_codes = {issue.code for issue in structural.issues}
    assert issue_codes <= {"readiness.source_tree_digest_mismatch"}
    assert not ready.is_valid
    assert summary.readiness_stage == "golden_ready_partial"
    assert summary.passed_gates[:4] == (
        "golden_dataset",
        "embedding_provider_live",
        "human_approval_e2e",
        "target_deployment_e2e",
    )
    if structural.is_valid:
        assert summary.manifest_status == "pending"
        assert summary.passed_gates[4:] == ("full_local_regression",)
        assert summary.validation_issues == ()
    else:
        assert summary.manifest_status == "stale"
        assert summary.passed_gates[4:] == ()
        assert "full_local_regression" in summary.pending_gates
        assert summary.validation_issues == ("readiness.source_tree_digest_mismatch",)
    assert "github_copilot_live" in summary.pending_gates
    assert "target_deployment_e2e" not in summary.pending_gates
    assert "golden_dataset" not in {issue.message.rsplit(": ", 1)[-1] for issue in ready.issues}


def test_unknown_readiness_gate_is_schema_violation(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    manifest_path = repository / "readiness/mvp-readiness.silver.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["gates"][0]["gate_id"] = "unknown_gate"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate(manifest_path)

    assert {issue.code for issue in report.issues} == {"readiness.schema_violation"}


def test_ready_manifest_requires_typed_digest_bound_final_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    readiness = repository / "readiness"
    evidence_root = readiness / "evidence"
    evidence_root.mkdir(parents=True)
    shutil.copy(ROOT / "readiness/mvp-readiness.schema.json", readiness)
    shutil.copy(ROOT / "readiness/mvp-evidence.schema.json", readiness)
    golden_root = repository / "golden-dataset"
    shutil.copytree(ROOT / "golden-dataset", golden_root)
    golden_manifest = _promote_golden_fixture(golden_root)
    golden_payload = json.loads(golden_manifest.read_text(encoding="utf-8"))
    golden_manifest_sha256 = sha256(golden_manifest.read_bytes()).hexdigest()
    golden_dataset_sha256 = GoldenDatasetValidator(golden_root).dataset_digest(golden_manifest)
    source_root = repository / "src"
    source_root.mkdir()
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    source_tree_sha256 = MvpReadinessValidator.source_tree_digest(repository)
    gates: list[dict[str, object]] = []
    for gate_id in sorted(REQUIRED_MVP_GATE_IDS):
        evidence_id = f"evidence-{gate_id}"
        evidence_type = REQUIRED_EVIDENCE_TYPE_BY_GATE[gate_id]
        review_status = "verified" if gate_id == "full_local_regression" else "reviewed"
        attestor = (
            "automation:operamind-baseline"
            if gate_id == "full_local_regression"
            else "reviewer:verified-test-actor"
        )
        evidence_path = evidence_root / f"{gate_id}.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "evidence_format_version": "v1",
                    "evidence_id": evidence_id,
                    "gate_id": gate_id,
                    "evidence_type": evidence_type,
                    "outcome": "passed",
                    "observed_at": OBSERVED_AT,
                    "review_status": review_status,
                    "reviewed_by": [attestor],
                    "subject": _subject(
                        gate_id,
                        golden_manifest_sha256=golden_manifest_sha256,
                        source_tree_sha256=source_tree_sha256,
                        golden_dataset_id=golden_payload["dataset_id"],
                        golden_dataset_version=golden_payload["dataset_version"],
                        golden_dataset_sha256=golden_dataset_sha256,
                        golden_project_count=len(golden_payload["projects"]),
                        golden_case_count=len(golden_payload["cases"]),
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        gates.append(
            {
                "gate_id": gate_id,
                "policy_version": "v1",
                "status": "passed",
                "evidence_refs": [
                    {
                        "evidence_id": evidence_id,
                        "evidence_type": evidence_type,
                        "path": f"readiness/evidence/{gate_id}.json",
                        "sha256": sha256(evidence_path.read_bytes()).hexdigest(),
                        "observed_at": OBSERVED_AT,
                    }
                ],
                "reviewers": [attestor],
            }
        )
    manifest_path = readiness / "mvp-readiness.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_id": "test-mvp-readiness",
                "manifest_version": "1.0.0",
                "status": "ready",
                "gates": gates,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    validator = MvpReadinessValidator(repository)
    report = validator.validate(
        manifest_path,
        require_ready=True,
        golden_manifest_path=golden_manifest,
    )
    summary = validator.summarize(manifest_path)

    assert report.is_valid, report.issues
    assert summary.readiness_stage == "mvp_ready"
    assert len(summary.passed_gates) == len(REQUIRED_MVP_GATE_IDS)
    assert summary.pending_gates == ()

    first_case = golden_payload["cases"][0]
    case_path = golden_root / first_case["expected_changes"]
    original_case = case_path.read_bytes()
    case_payload = json.loads(original_case)
    case_payload["changes"][0]["business_summary"] += " tampered"
    case_path.write_text(
        json.dumps(case_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    changed_dataset = validator.validate(
        manifest_path,
        require_ready=True,
        golden_manifest_path=golden_manifest,
    )
    assert {issue.code for issue in changed_dataset.issues} == {
        "readiness.golden_dataset_digest_mismatch"
    }
    case_path.write_bytes(original_case)

    (source_root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    changed_source = validator.validate(
        manifest_path,
        require_ready=True,
        golden_manifest_path=golden_manifest,
    )
    assert {issue.code for issue in changed_source.issues} == {
        "readiness.source_tree_digest_mismatch"
    }
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    other_manifest = golden_root / "other-manifest.golden.json"
    shutil.copy(golden_manifest, other_manifest)
    mismatch = validator.validate(
        manifest_path,
        require_ready=True,
        golden_manifest_path=other_manifest,
    )
    assert {issue.code for issue in mismatch.issues} == {
        "readiness.golden_manifest_selection_mismatch"
    }


def test_source_tree_digest_includes_web_static_assets(tmp_path: Path) -> None:
    static_root = tmp_path / "src" / "operamind" / "web" / "static"
    static_root.mkdir(parents=True)
    script = static_root / "app.js"
    stylesheet = static_root / "app.css"
    page = static_root / "index.html"
    script.write_text("const state = 'ready';\n", encoding="utf-8")
    stylesheet.write_text("body { color: green; }\n", encoding="utf-8")
    page.write_text('<html lang="ja"></html>\n', encoding="utf-8")
    original = MvpReadinessValidator.source_tree_digest(tmp_path)

    script.write_text("const state = 'blocked';\n", encoding="utf-8")
    script_changed = MvpReadinessValidator.source_tree_digest(tmp_path)
    script.write_text("const state = 'ready';\n", encoding="utf-8")
    stylesheet.write_text("body { color: red; }\n", encoding="utf-8")
    stylesheet_changed = MvpReadinessValidator.source_tree_digest(tmp_path)
    stylesheet.write_text("body { color: green; }\n", encoding="utf-8")
    page.write_text('<html lang="en"></html>\n', encoding="utf-8")
    page_changed = MvpReadinessValidator.source_tree_digest(tmp_path)

    assert len({original, script_changed, stylesheet_changed, page_changed}) == 4


def test_source_tree_digest_includes_quality_pipeline_inputs(tmp_path: Path) -> None:
    workflow = tmp_path / ".github/workflows/quality.yml"
    script = tmp_path / "scripts/check_critical_coverage.py"
    policy = tmp_path / "quality/critical-coverage.json"
    lock = tmp_path / "requirements.lock"
    for path in (workflow, script, policy):
        path.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("name: quality\n", encoding="utf-8")
    script.write_text("MINIMUM = 80\n", encoding="utf-8")
    policy.write_text('{"minimum": 80}\n', encoding="utf-8")
    lock.write_text("pytest==8.4.2\n", encoding="utf-8")
    original = MvpReadinessValidator.source_tree_digest(tmp_path)
    changed: set[str] = set()

    for path, replacement in (
        (workflow, "name: relaxed\n"),
        (script, "MINIMUM = 0\n"),
        (policy, '{"minimum": 0}\n'),
        (lock, "pytest==8.4.1\n"),
    ):
        before = path.read_text(encoding="utf-8")
        path.write_text(replacement, encoding="utf-8")
        changed.add(MvpReadinessValidator.source_tree_digest(tmp_path))
        path.write_text(before, encoding="utf-8")

    assert original not in changed
    assert len(changed) == 4


def test_full_regression_evidence_uses_fixed_scope_and_complete_counts() -> None:
    source_tree_sha256 = MvpReadinessValidator.source_tree_digest(ROOT)
    payload: dict[str, object] = {
        "evidence_type": "test_report",
        "subject": _subject(
            "full_local_regression",
            golden_manifest_sha256="0" * 64,
            source_tree_sha256=source_tree_sha256,
        ),
    }

    assert not MvpReadinessValidator._source_tree_evidence_issues(
        root=ROOT,
        payload=payload,
        location="evidence",
    )

    subject = payload["subject"]
    assert isinstance(subject, dict)
    subject["test_command"] = [".venv/bin/pytest", "-q", "--ignore=tests"]
    subject["excluded_tests"] = ["tests"]
    subject["passed"] = 99

    issue_codes = {
        issue.code
        for issue in MvpReadinessValidator._source_tree_evidence_issues(
            root=ROOT,
            payload=payload,
            location="evidence",
        )
    }
    assert issue_codes == {
        "readiness.full_regression_command_mismatch",
        "readiness.full_regression_count_mismatch",
        "readiness.full_regression_exclusions_mismatch",
    }


def test_pending_review_evidence_cannot_pass_a_gate(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    evidence_path = repository / "readiness/evidence/full-regression.json"
    evidence_path.parent.mkdir(exist_ok=True)
    source_tree_sha256 = MvpReadinessValidator.source_tree_digest(repository)
    evidence = {
        "evidence_format_version": "v1",
        "evidence_id": "pending-full-regression",
        "gate_id": "full_local_regression",
        "evidence_type": "test_report",
        "outcome": "passed",
        "observed_at": OBSERVED_AT,
        "review_status": "pending",
        "reviewed_by": [],
        "subject": _subject(
            "full_local_regression",
            golden_manifest_sha256="0" * 64,
            source_tree_sha256=source_tree_sha256,
        ),
    }
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    manifest_path = repository / "readiness/mvp-readiness.silver.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    gate = next(gate for gate in manifest["gates"] if gate["gate_id"] == "full_local_regression")
    gate["status"] = "passed"
    gate.pop("blocking_reason", None)
    gate["evidence_refs"] = [
        {
            "evidence_id": evidence["evidence_id"],
            "evidence_type": evidence["evidence_type"],
            "path": "readiness/evidence/full-regression.json",
            "sha256": sha256(evidence_path.read_bytes()).hexdigest(),
            "observed_at": OBSERVED_AT,
        }
    ]
    gate["reviewers"] = ["reviewer:verified-test-actor"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate(manifest_path)

    assert "readiness.evidence_not_reviewed" in {issue.code for issue in report.issues}


def test_historical_full_regression_evidence_is_stale_after_source_change() -> None:
    path = ROOT / "readiness/evidence/full-local-regression-2026-07-17.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    report = MvpReadinessValidator(ROOT).validate_reviewed_evidence(path)

    assert {issue.code for issue in report.issues} == {"readiness.source_tree_digest_mismatch"}
    assert payload["review_status"] == "verified"
    assert payload["reviewed_by"] == ["automation:operamind-baseline"]


def test_repository_golden_evidence_is_reviewed_and_valid() -> None:
    path = ROOT / "readiness/evidence/golden-dataset-1.0.0.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    report = MvpReadinessValidator(ROOT).validate_reviewed_evidence(
        path,
        golden_manifest_path=ROOT / "golden-dataset/manifest.golden.json",
    )

    assert report.is_valid, report.issues
    assert payload["review_status"] == "reviewed"
    assert payload["reviewed_by"] == ["conversation:user"]
    assert payload["subject"]["status"] == "frozen"


@pytest.mark.parametrize(
    ("relative_path", "gate_id"),
    [
        (
            "readiness/evidence/local-embedding-provider-2026-07-18.json",
            "embedding_provider_live",
        ),
        (
            "readiness/evidence/visiondemo-human-approval-p6-v3.json",
            "human_approval_e2e",
        ),
    ],
)
def test_repository_external_review_evidence_is_reviewed_and_valid(
    relative_path: str,
    gate_id: str,
) -> None:
    path = ROOT / relative_path
    payload = json.loads(path.read_text(encoding="utf-8"))

    report = MvpReadinessValidator(ROOT).validate_reviewed_evidence(path)

    assert report.is_valid, report.issues
    assert payload["gate_id"] == gate_id
    assert payload["review_status"] == "reviewed"
    assert payload["reviewed_by"] == ["conversation:user"]


def test_reviewed_golden_evidence_can_be_preflighted_before_gate_update(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    readiness_root = repository / "readiness"
    evidence_root = readiness_root / "evidence"
    evidence_root.mkdir(parents=True)
    shutil.copy(ROOT / "readiness/mvp-readiness.schema.json", readiness_root)
    shutil.copy(ROOT / "readiness/mvp-evidence.schema.json", readiness_root)
    golden_root = repository / "golden-dataset"
    shutil.copytree(ROOT / "golden-dataset", golden_root)
    golden_manifest = _promote_golden_fixture(golden_root)
    golden_payload = json.loads(golden_manifest.read_text(encoding="utf-8"))
    evidence_path = evidence_root / "golden-dataset-1.0.0.json"
    evidence_path.write_text(
        json.dumps(
            {
                "evidence_format_version": "v1",
                "evidence_id": "reviewed-golden-evidence",
                "gate_id": "golden_dataset",
                "evidence_type": "golden_manifest",
                "outcome": "passed",
                "observed_at": OBSERVED_AT,
                "review_status": "reviewed",
                "reviewed_by": ["reviewer:verified-test-actor"],
                "subject": _subject(
                    "golden_dataset",
                    golden_manifest_sha256=sha256(golden_manifest.read_bytes()).hexdigest(),
                    source_tree_sha256="0" * 64,
                    golden_dataset_id=golden_payload["dataset_id"],
                    golden_dataset_version=golden_payload["dataset_version"],
                    golden_dataset_sha256=GoldenDatasetValidator(golden_root).dataset_digest(
                        golden_manifest
                    ),
                    golden_project_count=len(golden_payload["projects"]),
                    golden_case_count=len(golden_payload["cases"]),
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report = MvpReadinessValidator(repository).validate_reviewed_evidence(
        evidence_path,
        golden_manifest_path=golden_manifest,
    )

    assert report.is_valid, report.issues


def test_reviewed_golden_evidence_cannot_claim_needs_review_subject(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    readiness_root = repository / "readiness"
    evidence_root = readiness_root / "evidence"
    evidence_root.mkdir(parents=True)
    shutil.copy(ROOT / "readiness/mvp-evidence.schema.json", readiness_root)
    evidence_path = evidence_root / "golden.json"
    payload = {
        "evidence_format_version": "v1",
        "evidence_id": "invalid-reviewed-golden",
        "gate_id": "golden_dataset",
        "evidence_type": "golden_manifest",
        "outcome": "passed",
        "observed_at": OBSERVED_AT,
        "review_status": "reviewed",
        "reviewed_by": ["reviewer:verified-test-actor"],
        "subject": {
            "dataset_id": "dataset",
            "dataset_version": "1.0.0",
            "manifest_path": "golden-dataset/manifest.golden.json",
            "manifest_sha256": "0" * 64,
            "dataset_digest_algorithm": "operamind-golden-dataset-v1",
            "dataset_sha256": "0" * 64,
            "project_count": 1,
            "case_count": 1,
            "status": "needs_review",
        },
    }
    evidence_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate_reviewed_evidence(evidence_path)

    assert {issue.code for issue in report.issues} == {"readiness.evidence_schema_violation"}


def test_reviewed_envelope_cannot_remain_in_candidate_area(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    candidate_path = (
        repository / "readiness/candidates/full-local-regression-2026-07-17.candidate.json"
    )
    candidate_path.parent.mkdir(exist_ok=True)
    source_path = repository / "readiness/evidence/full-local-regression-2026-07-17.json"
    candidate = json.loads(source_path.read_text(encoding="utf-8"))
    candidate["subject"]["source_tree_sha256"] = MvpReadinessValidator.source_tree_digest(
        repository
    )
    candidate_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate_candidate(candidate_path)

    assert {issue.code for issue in report.issues} == {"readiness.candidate_not_pending"}


def test_evidence_tampering_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    manifest_path = repository / "readiness/mvp-readiness.silver.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_path = repository / "readiness/evidence/evidence.json"
    evidence_path.parent.mkdir(exist_ok=True)
    evidence_path.write_text("original\n", encoding="utf-8")
    gate = manifest["gates"][5]
    gate["status"] = "passed"
    gate.pop("blocking_reason", None)
    gate["evidence_refs"] = [
        {
            "evidence_id": "partial-evidence",
            "evidence_type": "test_report",
            "path": "readiness/evidence/evidence.json",
            "sha256": sha256(evidence_path.read_bytes()).hexdigest(),
            "observed_at": OBSERVED_AT,
        }
    ]
    gate["reviewers"] = ["reviewer:verified-test-actor"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    evidence_path.write_text("tampered\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate(manifest_path)

    assert "readiness.evidence_digest_mismatch" in {issue.code for issue in report.issues}


def test_digest_correct_but_unready_golden_manifest_evidence_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    shutil.copytree(ROOT / "golden-dataset", repository / "golden-dataset")
    manifest_path = repository / "golden-dataset/manifest.golden.json"
    golden_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_case = golden_manifest["cases"][0]
    source_path = repository / "golden-dataset" / first_case["source_manifest"]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["portability_status"] = "local_only"
    source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n")

    evidence_path = repository / "readiness/evidence/golden-dataset.json"
    evidence_path.parent.mkdir(exist_ok=True)
    evidence = {
        "evidence_format_version": "v1",
        "evidence_id": "reviewed-golden",
        "gate_id": "golden_dataset",
        "evidence_type": "golden_manifest",
        "outcome": "passed",
        "observed_at": OBSERVED_AT,
        "review_status": "reviewed",
        "reviewed_by": ["reviewer:verified-test-actor"],
        "subject": _subject(
            "golden_dataset",
            golden_manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
            source_tree_sha256="0" * 64,
            golden_dataset_id=golden_manifest["dataset_id"],
            golden_dataset_version=golden_manifest["dataset_version"],
            golden_dataset_sha256=GoldenDatasetValidator(
                repository / "golden-dataset"
            ).dataset_digest(manifest_path),
            golden_project_count=len(golden_manifest["projects"]),
            golden_case_count=len(golden_manifest["cases"]),
        ),
    }
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")

    readiness_path = repository / "readiness/mvp-readiness.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    gate = readiness["gates"][0]
    gate["status"] = "passed"
    gate.pop("blocking_reason", None)
    gate["evidence_refs"] = [
        {
            "evidence_id": "reviewed-golden",
            "evidence_type": "golden_manifest",
            "path": "readiness/evidence/golden-dataset.json",
            "sha256": sha256(evidence_path.read_bytes()).hexdigest(),
            "observed_at": OBSERVED_AT,
        }
    ]
    gate["reviewers"] = ["reviewer:verified-test-actor"]
    readiness_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n")

    report = MvpReadinessValidator(repository).validate(
        readiness_path,
        golden_manifest_path=manifest_path,
    )

    assert "readiness.golden.local_only_source" in {issue.code for issue in report.issues}


def test_digest_correct_but_hollow_evidence_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    manifest_path = repository / "readiness/mvp-readiness.silver.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_path = repository / "readiness/evidence/hollow-evidence.json"
    evidence_path.parent.mkdir(exist_ok=True)
    evidence_path.write_text("{}\n", encoding="utf-8")
    gate = manifest["gates"][0]
    gate["status"] = "passed"
    gate.pop("blocking_reason")
    gate["evidence_refs"] = [
        {
            "evidence_id": "hollow-evidence",
            "evidence_type": "golden_manifest",
            "path": "readiness/evidence/hollow-evidence.json",
            "sha256": sha256(evidence_path.read_bytes()).hexdigest(),
            "observed_at": OBSERVED_AT,
        }
    ]
    gate["reviewers"] = ["reviewer:verified-test-actor"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate(manifest_path)

    assert "readiness.evidence_schema_violation" in {issue.code for issue in report.issues}


def test_template_evidence_path_cannot_be_referenced(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    manifest_path = repository / "readiness/mvp-readiness.silver.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    template_path = repository / "readiness/templates/provider-probe.example.json"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("{}\n", encoding="utf-8")
    gate = manifest["gates"][1]
    gate["status"] = "passed"
    gate.pop("blocking_reason")
    gate["evidence_refs"] = [
        {
            "evidence_id": "template-evidence",
            "evidence_type": "provider_probe",
            "path": "readiness/templates/provider-probe.example.json",
            "sha256": sha256(template_path.read_bytes()).hexdigest(),
            "observed_at": OBSERVED_AT,
        }
    ]
    gate["reviewers"] = ["reviewer:verified-test-actor"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate(manifest_path)

    assert "readiness.evidence_template_referenced" in {issue.code for issue in report.issues}


def test_passed_evidence_must_live_under_readiness_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    manifest_path = repository / "readiness/mvp-readiness.silver.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_path = repository / "readiness/provider.json"
    evidence_payload = {
        "evidence_format_version": "v1",
        "evidence_id": "provider-evidence",
        "gate_id": "embedding_provider_live",
        "evidence_type": "provider_probe",
        "outcome": "passed",
        "observed_at": OBSERVED_AT,
        "review_status": "reviewed",
        "reviewed_by": ["reviewer:verified-test-actor"],
        "subject": {
            "profile_version_id": "embedding-profile@1.0.0",
            "model": "embedding-model",
            "dimensions": 1536,
            "endpoint_origin": "https://embedding.internal",
            "test_command": ["pytest", "-q"],
            "exit_code": 0,
        },
    }
    evidence_path.write_text(json.dumps(evidence_payload, indent=2) + "\n", encoding="utf-8")
    gate = manifest["gates"][1]
    gate["status"] = "passed"
    gate.pop("blocking_reason")
    gate["evidence_refs"] = [
        {
            "evidence_id": "provider-evidence",
            "evidence_type": "provider_probe",
            "path": "readiness/provider.json",
            "sha256": sha256(evidence_path.read_bytes()).hexdigest(),
            "observed_at": OBSERVED_AT,
        }
    ]
    gate["reviewers"] = ["reviewer:verified-test-actor"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate(manifest_path)

    assert "readiness.evidence_location_invalid" in {issue.code for issue in report.issues}


def test_evidence_identity_and_path_must_be_unique_across_manifest(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    manifest_path = repository / "readiness/mvp-readiness.silver.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    duplicate_ref = {
        "evidence_id": "duplicate-evidence",
        "evidence_type": "test_report",
        "path": "readiness/evidence/duplicate.json",
        "sha256": "0" * 64,
        "observed_at": OBSERVED_AT,
    }
    for gate in manifest["gates"][:2]:
        gate["status"] = "passed"
        gate.pop("blocking_reason")
        gate["evidence_refs"] = [duplicate_ref]
        gate["reviewers"] = ["reviewer:verified-test-actor"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate(manifest_path)

    issue_codes = {issue.code for issue in report.issues}
    assert "readiness.duplicate_evidence_id" in issue_codes
    assert "readiness.duplicate_evidence_path" in issue_codes


def test_pending_gate_cannot_reference_passed_evidence_or_reviewers(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    manifest_path = repository / "readiness/mvp-readiness.silver.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_path = repository / "readiness/evidence/evidence.json"
    evidence_path.parent.mkdir(exist_ok=True)
    evidence_path.write_text("{}\n", encoding="utf-8")
    gate = manifest["gates"][0]
    gate["evidence_refs"] = [
        {
            "evidence_id": "pending-evidence",
            "evidence_type": "golden_manifest",
            "path": "readiness/evidence/evidence.json",
            "sha256": sha256(evidence_path.read_bytes()).hexdigest(),
            "observed_at": OBSERVED_AT,
        }
    ]
    gate["reviewers"] = ["reviewer:verified-test-actor"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate(manifest_path)

    assert "readiness.schema_violation" in {issue.code for issue in report.issues}
    assert {
        "readiness.pending_gate_has_evidence",
        "readiness.pending_gate_has_reviewers",
    }.isdisjoint({issue.code for issue in report.issues})


def test_passed_gate_cannot_keep_blocking_reason(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    manifest_path = repository / "readiness/mvp-readiness.silver.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["gates"][0]["status"] = "passed"
    manifest["gates"][0]["blocking_reason"] = "old blocker"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = MvpReadinessValidator(repository).validate(manifest_path)

    assert {issue.code for issue in report.issues} == {"readiness.schema_violation"}


def test_copied_template_placeholder_evidence_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    readiness_root = repository / "readiness"
    evidence_root = readiness_root / "evidence"
    evidence_root.mkdir(parents=True)
    shutil.copy(ROOT / "readiness/mvp-readiness.schema.json", readiness_root)
    shutil.copy(ROOT / "readiness/mvp-evidence.schema.json", readiness_root)
    evidence_path = evidence_root / "provider.json"
    evidence_payload = {
        "evidence_format_version": "v1",
        "evidence_id": "provider-evidence",
        "gate_id": "embedding_provider_live",
        "evidence_type": "provider_probe",
        "outcome": "passed",
        "observed_at": OBSERVED_AT,
        "review_status": "reviewed",
        "reviewed_by": ["reviewer:verified-test-actor"],
        "subject": {
            "profile_version_id": "replace-with-profile",
            "model": "embedding-model",
            "dimensions": 1536,
            "endpoint_origin": "https://embedding.internal",
            "test_command": ["pytest", "-q"],
            "exit_code": 0,
        },
    }
    evidence_path.write_text(json.dumps(evidence_payload, indent=2) + "\n", encoding="utf-8")
    manifest_path = readiness_root / "mvp-readiness.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_id": "test-mvp-readiness",
                "manifest_version": "1.0.0",
                "status": "pending",
                "gates": [
                    {
                        "gate_id": "golden_dataset",
                        "policy_version": "v1",
                        "status": "pending",
                        "blocking_reason": "not selected",
                        "evidence_refs": [],
                        "reviewers": [],
                    },
                    {
                        "gate_id": "embedding_provider_live",
                        "policy_version": "v1",
                        "status": "passed",
                        "evidence_refs": [
                            {
                                "evidence_id": "provider-evidence",
                                "evidence_type": "provider_probe",
                                "path": "readiness/evidence/provider.json",
                                "sha256": sha256(evidence_path.read_bytes()).hexdigest(),
                                "observed_at": OBSERVED_AT,
                            }
                        ],
                        "reviewers": ["reviewer:verified-test-actor"],
                    },
                    {
                        "gate_id": "human_approval_e2e",
                        "policy_version": "v1",
                        "status": "pending",
                        "blocking_reason": "not selected",
                        "evidence_refs": [],
                        "reviewers": [],
                    },
                    {
                        "gate_id": "github_copilot_live",
                        "policy_version": "v1",
                        "status": "pending",
                        "blocking_reason": "not selected",
                        "evidence_refs": [],
                        "reviewers": [],
                    },
                    {
                        "gate_id": "target_deployment_e2e",
                        "policy_version": "v1",
                        "status": "pending",
                        "blocking_reason": "not selected",
                        "evidence_refs": [],
                        "reviewers": [],
                    },
                    {
                        "gate_id": "full_local_regression",
                        "policy_version": "v1",
                        "status": "pending",
                        "blocking_reason": "not selected",
                        "evidence_refs": [],
                        "reviewers": [],
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report = MvpReadinessValidator(repository).validate(manifest_path)

    assert "readiness.evidence_placeholder" in {issue.code for issue in report.issues}


def test_repository_baseline_cannot_claim_mvp_ready(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert baseline_main(["--root", str(ROOT), "--require-mvp-ready"]) == 1
    assert "readiness.gate_not_passed" in capsys.readouterr().out


def test_repository_baseline_accepts_reviewed_golden_readiness(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repository_with_current_readiness(tmp_path)
    assert (
        baseline_main(
            [
                "--root",
                str(repository),
                "--manifest",
                "golden-dataset/manifest.golden.json",
                "--readiness-manifest",
                "readiness/mvp-readiness.json",
                "--require-ready",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "OperaMind baseline validation passed" in output


def test_repository_baseline_requires_golden_readiness_evidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        baseline_main(
            [
                "--root",
                str(ROOT),
                "--manifest",
                "golden-dataset/manifest.golden.json",
                "--require-ready",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "readiness.golden_dataset_gate_not_passed" in output


def test_repository_with_reviewed_golden_still_cannot_claim_mvp_ready(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        baseline_main(
            [
                "--root",
                str(ROOT),
                "--manifest",
                "golden-dataset/manifest.golden.json",
                "--readiness-manifest",
                "readiness/mvp-readiness.json",
                "--require-mvp-ready",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "github_copilot_live" in output
    assert "MVP gate is still pending: target_deployment_e2e" not in output
    assert "MVP gate is still pending: golden_dataset" not in output


def test_baseline_prints_readiness_status_for_selected_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repository_with_current_readiness(tmp_path)
    assert (
        baseline_main(
            [
                "--root",
                str(repository),
                "--manifest",
                "golden-dataset/manifest.golden.json",
                "--readiness-manifest",
                "readiness/mvp-readiness.json",
                "--print-readiness-status",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Readiness stage: golden_ready_partial" in output
    assert (
        "Passed gates: golden_dataset, embedding_provider_live, human_approval_e2e, "
        "target_deployment_e2e" in output
    )
    assert "golden_dataset" in output
    assert "embedding_provider_live" in output


def test_baseline_prints_machine_readable_readiness_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repository_with_current_readiness(tmp_path)
    assert (
        baseline_main(
            [
                "--root",
                str(repository),
                "--manifest",
                "golden-dataset/manifest.golden.json",
                "--readiness-manifest",
                "readiness/mvp-readiness.json",
                "--print-readiness-json",
            ]
        )
        == 0
    )
    lines = capsys.readouterr().out.splitlines()
    payload = json.loads(lines[-2])

    assert payload["readiness_stage"] == "golden_ready_partial"
    assert payload["manifest_status"] == "pending"
    assert payload["passed_gates"][:4] == [
        "golden_dataset",
        "embedding_provider_live",
        "human_approval_e2e",
        "target_deployment_e2e",
    ]
    assert set(payload["passed_gates"][4:]) <= {"full_local_regression"}
    assert payload["pending_gates"][0] == "github_copilot_live"
    assert set(payload["pending_gates"][1:]) <= {"full_local_regression"}
    assert payload["gates"][0] == {
        "blocking_reason": None,
        "evidence_template": None,
        "evidence_count": 1,
        "expected_evidence_type": "golden_manifest",
        "gate_id": "golden_dataset",
        "status": "passed",
        "validation_issues": [],
    }
    provider_gate = next(
        gate for gate in payload["gates"] if gate["gate_id"] == "embedding_provider_live"
    )
    assert provider_gate["expected_evidence_type"] == "provider_probe"
    assert (
        provider_gate["evidence_template"]
        == "readiness/templates/embedding-provider-live.example.json"
    )


def test_baseline_can_require_selected_readiness_stage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repository_with_current_readiness(tmp_path)
    assert (
        baseline_main(
            [
                "--root",
                str(repository),
                "--manifest",
                "golden-dataset/manifest.golden.json",
                "--readiness-manifest",
                "readiness/mvp-readiness.json",
                "--require-readiness-stage",
                "golden_ready_partial",
            ]
        )
        == 0
    )
    assert "OperaMind baseline validation passed" in capsys.readouterr().out


def test_baseline_rejects_wrong_readiness_stage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        baseline_main(
            [
                "--root",
                str(ROOT),
                "--manifest",
                "golden-dataset/manifest.golden.json",
                "--readiness-manifest",
                "readiness/mvp-readiness.json",
                "--require-readiness-stage",
                "mvp_ready",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "readiness.stage_mismatch" in output
    assert "expected=mvp_ready actual=golden_ready_partial" in output


def test_baseline_readiness_summary_fails_closed_for_invalid_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "contracts", repository / "contracts")
    shutil.copytree(ROOT / "profiles", repository / "profiles")
    shutil.copytree(ROOT / "golden-dataset", repository / "golden-dataset")
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    invalid_manifest = repository / "readiness/invalid-readiness.json"
    invalid_manifest.write_text('{"manifest_id": "broken"}\n', encoding="utf-8")

    assert (
        baseline_main(
            [
                "--root",
                str(repository),
                "--readiness-manifest",
                "readiness/invalid-readiness.json",
                "--print-readiness-json",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "readiness.schema_violation" in output
    assert "readiness.summary_unavailable" in output


def test_baseline_prints_repository_relative_evidence_digest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    relative_path = "readiness/evidence/full-local-regression-2026-07-17.json"
    evidence_path = ROOT / relative_path
    expected_digest = sha256(evidence_path.read_bytes()).hexdigest()

    assert (
        baseline_main(
            [
                "--root",
                str(ROOT),
                "--print-evidence-digest",
                relative_path,
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert (f"Readiness evidence digest {relative_path} {expected_digest}") in output


def test_baseline_rejects_stale_verified_full_regression_evidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = "readiness/evidence/full-local-regression-2026-07-17.json"

    assert (
        baseline_main(
            [
                "--root",
                str(ROOT),
                "--validate-reviewed-evidence",
                evidence,
            ]
        )
        == 1
    )

    assert "readiness.source_tree_digest_mismatch" in capsys.readouterr().out


def test_baseline_validates_reviewed_golden_evidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = "readiness/evidence/golden-dataset-1.0.0.json"

    assert (
        baseline_main(
            [
                "--root",
                str(ROOT),
                "--manifest",
                "golden-dataset/manifest.golden.json",
                "--validate-reviewed-evidence",
                evidence,
            ]
        )
        == 0
    )

    assert f"Reviewed readiness evidence valid: {evidence}" in capsys.readouterr().out


def test_baseline_rejects_candidate_as_reviewed_evidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate = "readiness/candidates/full-local-regression-2026-07-17.candidate.json"

    assert (
        baseline_main(
            [
                "--root",
                str(ROOT),
                "--validate-reviewed-evidence",
                candidate,
            ]
        )
        == 1
    )

    assert "readiness.reviewed_evidence_location_invalid" in capsys.readouterr().out


def test_baseline_rejects_evidence_digest_path_escape(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        baseline_main(
            [
                "--root",
                str(ROOT),
                "--print-evidence-digest",
                "../outside.json",
            ]
        )
        == 1
    )

    assert "readiness.evidence_digest_path_invalid" in capsys.readouterr().out
