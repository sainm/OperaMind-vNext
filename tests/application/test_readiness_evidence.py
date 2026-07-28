import shutil
from datetime import UTC, datetime
from pathlib import Path

from operamind.application.readiness_evidence import ReadinessEvidenceSyncService
from operamind.infrastructure.postgres import ReadinessEvidenceInput
from operamind.readiness import (
    FULL_LOCAL_REGRESSION_COMMAND,
    FULL_LOCAL_REGRESSION_EXCLUDED_TESTS,
    SOURCE_TREE_DIGEST_ALGORITHM,
    MvpReadinessValidator,
)

ROOT = Path(__file__).parents[2]
OBSERVED_AT = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


class FakeSource:
    def __init__(self, values: dict[str, ReadinessEvidenceInput | None]) -> None:
        self.values = values

    def embedding_provider(self, project_id: str) -> ReadinessEvidenceInput | None:
        return self.values.get("embedding_provider_live")

    def human_approval(
        self, project_id: str, analysis_case_id: str
    ) -> ReadinessEvidenceInput | None:
        return self.values.get("human_approval_e2e")

    def copilot(self, project_id: str, analysis_case_id: str) -> ReadinessEvidenceInput | None:
        return self.values.get("github_copilot_live")

    def deployment(self, project_id: str, analysis_case_id: str) -> ReadinessEvidenceInput | None:
        return self.values.get("target_deployment_e2e")

    def full_regression(self) -> ReadinessEvidenceInput | None:
        return self.values.get("full_local_regression")


def test_missing_canonical_evidence_stays_pending_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = ReadinessEvidenceSyncService(
        repository_root=repository,
        source=FakeSource({}),
    )

    first = service.sync(project_id="visiondemo", analysis_case_id="case-1")
    first_bytes = (repository / "readiness/mvp-readiness.json").read_bytes()
    second = service.sync(project_id="visiondemo", analysis_case_id="case-1")

    assert first.changed
    assert not second.changed
    assert first.manifest_version == second.manifest_version
    assert (repository / "readiness/mvp-readiness.json").read_bytes() == first_bytes
    assert first.summary.passed_gates == ("golden_dataset",)
    assert set(first.summary.pending_gates) == {
        "embedding_provider_live",
        "human_approval_e2e",
        "github_copilot_live",
        "target_deployment_e2e",
        "full_local_regression",
    }
    assert not list((repository / "readiness/evidence").glob("auto-*.json"))


def test_all_real_inputs_publish_valid_ready_manifest_idempotently(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    source_digest = MvpReadinessValidator.source_tree_digest(repository)
    source = FakeSource(_all_inputs(source_digest))
    service = ReadinessEvidenceSyncService(repository_root=repository, source=source)

    first = service.sync(project_id="visiondemo", analysis_case_id="case-1")
    evidence_bytes = {
        path.name: path.read_bytes()
        for path in (repository / "readiness/evidence").glob("auto-*.json")
    }
    second = service.sync(project_id="visiondemo", analysis_case_id="case-1")

    assert first.changed
    assert not second.changed
    assert first.summary.readiness_stage == "mvp_ready"
    assert second.manifest_version == first.manifest_version
    assert len(evidence_bytes) == 5
    assert evidence_bytes == {
        path.name: path.read_bytes()
        for path in (repository / "readiness/evidence").glob("auto-*.json")
    }
    report = MvpReadinessValidator(repository).validate(
        repository / "readiness/mvp-readiness.json",
        require_ready=True,
        golden_manifest_path=repository / "golden-dataset/manifest.golden.json",
    )
    assert report.is_valid, report.issues


def test_stale_full_regression_observation_is_demoted_to_pending(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    values = _all_inputs("0" * 64)
    service = ReadinessEvidenceSyncService(
        repository_root=repository,
        source=FakeSource(values),
    )

    result = service.sync(project_id="visiondemo", analysis_case_id="case-1")

    assert "full_local_regression" in result.summary.pending_gates
    assert not list((repository / "readiness/evidence").glob("auto-full-local-regression-*.json"))


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "readiness", repository / "readiness")
    for generated in (repository / "readiness/evidence").glob("auto-*.json"):
        generated.unlink()
    shutil.copytree(ROOT / "golden-dataset", repository / "golden-dataset")
    (repository / "src").mkdir()
    (repository / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return repository


def _input(
    gate_id: str,
    evidence_type: str,
    subject: dict[str, object],
    *,
    review_status: str = "reviewed",
) -> ReadinessEvidenceInput:
    reviewer = (
        "automation:operamind-readiness" if review_status == "verified" else "reviewer:integration"
    )
    return ReadinessEvidenceInput(
        evidence_id=f"evidence-{gate_id}",
        gate_id=gate_id,
        evidence_type=evidence_type,
        observed_at=OBSERVED_AT,
        review_status=review_status,
        reviewed_by=(reviewer,),
        subject=subject,
    )


def _all_inputs(source_digest: str) -> dict[str, ReadinessEvidenceInput]:
    return {
        "embedding_provider_live": _input(
            "embedding_provider_live",
            "provider_probe",
            {
                "profile_version_id": "local-openai-compatible-v1@1.0.0",
                "model": "nomic-embed-text-v1.5",
                "dimensions": 768,
                "endpoint_origin": "http://127.0.0.1:1234",
                "test_command": [".venv/bin/pytest", "tests/provider.py"],
                "exit_code": 0,
            },
            review_status="verified",
        ),
        "human_approval_e2e": _input(
            "human_approval_e2e",
            "human_review",
            {
                "project_id": "visiondemo",
                "analysis_case_id": "case-1",
                "impact_report_id": "report-1",
                "confirmation_id": "confirmation-1",
                "approval_grant_id": "grant-1",
                "decision": "approved",
            },
        ),
        "github_copilot_live": _input(
            "github_copilot_live",
            "copilot_session",
            {
                "project_id": "visiondemo",
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
                "base_repository_revision": "a" * 40,
                "result_repository_revision": "b" * 40,
                "mcp_protocol_version": "2025-11-25",
                "tool_approval_status": "confirmed",
            },
        ),
        "target_deployment_e2e": _input(
            "target_deployment_e2e",
            "deployment_run",
            {
                "project_id": "visiondemo",
                "analysis_case_id": "case-1",
                "ui_execution_plan_id": "plan-1",
                "ui_execution_run_id": "run-1",
                "verification_result_id": "verification-1",
                "environment_id": "environment-1",
                "deployment_revision": "deployment-1",
                "repository_revision": "b" * 40,
                "status": "passed",
                "evidence_ids": ["ui-evidence-1"],
            },
            review_status="verified",
        ),
        "full_local_regression": _input(
            "full_local_regression",
            "test_report",
            {
                "source_tree_algorithm": SOURCE_TREE_DIGEST_ALGORITHM,
                "source_tree_sha256": source_digest,
                "test_command": list(FULL_LOCAL_REGRESSION_COMMAND),
                "excluded_tests": list(FULL_LOCAL_REGRESSION_EXCLUDED_TESTS),
                "collected": 10,
                "passed": 10,
                "failed": 0,
                "skipped": 0,
                "database_version": "PostgreSQL 18.4",
                "browser_version": "Chromium 150",
            },
            review_status="verified",
        ),
    }
