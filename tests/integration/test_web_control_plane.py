import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from operamind.application.web_control_plane import (
    BusinessRuleInput,
    ChangeRequestInput,
    WebControlPlaneService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    MigrationCatalog,
    MigrationRunner,
    WebControlPlaneRepository,
)

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_change_request_diff_and_human_review_are_canonical_and_idempotent() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"web-project-{suffix}"
    repository_id = f"web-repository-{suffix}"
    revision_id = f"web-revision-{suffix}"
    case_id = f"web-case-{suffix}"
    change_id = f"web-change-{suffix}"
    request_id = f"web-request-{suffix}"

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        _seed_scope(connection, project_id, repository_id, revision_id, case_id, suffix)
        contracts = ContractCatalog.load(ROOT / "contracts")
        ArtifactRepository(connection, contracts).store(
            artifact_id=change_id,
            project_id=project_id,
            analysis_case_id=case_id,
            artifact=_structured_change(project_id, change_id, suffix),
        )
        service = WebControlPlaneService(connection=connection, repository_root=ROOT)
        web_repository = WebControlPlaneRepository(connection, contracts)
        request = ChangeRequestInput(
            change_request_id=request_id,
            project_id=project_id,
            analysis_case_id=case_id,
            input_mode="natural_language",
            requirement_text="费用状态筛选增加差戻し选项",
            source_document_ref=None,
            target_document_ref=None,
            business_rules=(BusinessRuleInput("rule-status", "差戻し必须可选", ()),),
            ambiguity_status="clear",
            ambiguities=(),
            submitted_by="integration-reviewer",
        )

        first = service.submit_change_request(request)
        replay = service.submit_change_request(request)
        diff = service.document_diff(request_id)
        automation = service.start_change_automation(
            request_id=request_id,
            idempotency_key="one-click-key",
            actor="integration-reviewer",
        )
        automation_replay = service.start_change_automation(
            request_id=request_id,
            idempotency_key="one-click-key",
            actor="integration-reviewer",
        )
        claimed_review_task = service.claim_orchestration_task(
            executor_kind="human",
            executor_id="integration-reviewer",
            capabilities=("document_review",),
            project_id=project_id,
        )["task"]
        assert claimed_review_task is not None
        with pytest.raises(ValueError, match="must be confirmed"):
            web_repository.require_confirmed_document_review(
                request_id=request_id, project_id=project_id, case_id=case_id
            )
        reviewed = service.review_document_diff(
            idempotency_key="review-key",
            request_id=request_id,
            project_id=project_id,
            decision="confirmed",
            actor="integration-reviewer",
            note="与设计意图一致",
        )
        review_replay = service.review_document_diff(
            idempotency_key="review-key",
            request_id=request_id,
            project_id=project_id,
            decision="confirmed",
            actor="integration-reviewer",
            note="与设计意图一致",
        )
        stored = service.get_change_request(request_id)
        resumed_automation = service.change_automation(request_id)["run"]
        web_repository.require_confirmed_document_review(
            request_id=request_id, project_id=project_id, case_id=case_id
        )

        assert first["created"] is True
        assert replay["created"] is False
        assert diff["total"] == 1
        assert automation["created"] is True
        assert automation_replay["created"] is False
        assert automation["run"]["current_stage"] == "document_confirmation"
        assert isinstance(resumed_automation, dict)
        assert resumed_automation["current_stage"] == "impact_analysis"
        review_task = next(
            task
            for task in resumed_automation["orchestration_tasks"]
            if task["action"] == "confirm_document_diff"
        )
        assert review_task["state"] == "completed"
        assert review_task["claims"][0]["status"] == "completed"
        assert review_task["results"][0]["evidence"]["canonical_state_advanced"] is True
        assert resumed_automation["current_task"]["action"] == "prepare_canonical_analysis"
        assert len(resumed_automation["events"]) == 2
        assert diff["changes"][0]["summary"] == "费用状态筛选增加差戻し选项"
        assert reviewed["created"] is True
        assert review_replay["created"] is False
        assert stored["document_review"]["status"] == "confirmed"
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_natural_language_automation_binds_imported_case_and_resumes() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"binding-project-{suffix}"
    case_id = f"binding-case-{suffix}"
    request_id = f"binding-request-{suffix}"
    change_id = f"binding-change-{suffix}"
    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        _seed_scope(
            connection,
            project_id,
            f"binding-repository-{suffix}",
            f"binding-revision-{suffix}",
            case_id,
            suffix,
        )
        contracts = ContractCatalog.load(ROOT / "contracts")
        ArtifactRepository(connection, contracts).store(
            artifact_id=change_id,
            project_id=project_id,
            analysis_case_id=case_id,
            artifact=_structured_change(project_id, change_id, suffix),
        )
        service = WebControlPlaneService(connection=connection, repository_root=ROOT)
        service.submit_change_request(
            ChangeRequestInput(
                change_request_id=request_id,
                project_id=project_id,
                analysis_case_id=None,
                input_mode="natural_language",
                requirement_text="差戻し状態を追加する",
                source_document_ref=None,
                target_document_ref=None,
                business_rules=(BusinessRuleInput("rule-status", "差戻しを表示する", ()),),
                ambiguity_status="clear",
                ambiguities=(),
                submitted_by="product-owner",
            )
        )
        started = service.start_change_automation(
            request_id=request_id,
            idempotency_key="automation-1",
            actor="product-owner",
        )
        bound = service.bind_change_request_case(
            request_id=request_id,
            project_id=project_id,
            case_id=case_id,
            idempotency_key="binding-1",
            actor="product-owner",
        )
        replay = service.bind_change_request_case(
            request_id=request_id,
            project_id=project_id,
            case_id=case_id,
            idempotency_key="binding-1",
            actor="product-owner",
        )
        current = service.change_automation(request_id)["run"]

        assert started["run"]["current_stage"] == "document_generation"
        assert bound["created"] is True
        assert replay["created"] is False
        assert isinstance(current, dict)
        assert current["current_stage"] == "document_confirmation"
        assert service.get_change_request(request_id)["analysis_case_id"] == case_id
        connection.rollback()


def _seed_scope(
    connection: psycopg.Connection[object],
    project_id: str,
    repository_id: str,
    revision_id: str,
    case_id: str,
    suffix: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, 'Web test')",
            (project_id,),
        )
        cursor.execute(
            """
            INSERT INTO repositories (repository_id, project_id, remote_url)
            VALUES (%s, %s, %s)
            """,
            (repository_id, project_id, f"https://example.invalid/{suffix}.git"),
        )
        cursor.execute(
            """
            INSERT INTO repository_revisions (
                repository_revision_id, repository_id, commit_sha
            ) VALUES (%s, %s, %s)
            """,
            (revision_id, repository_id, suffix),
        )
        cursor.execute(
            """
            INSERT INTO analysis_cases (
                analysis_case_id, project_id, repository_revision_id, status
            ) VALUES (%s, %s, %s, 'ready_for_impact')
            """,
            (case_id, project_id, revision_id),
        )


def _structured_change(project_id: str, change_id: str, suffix: str) -> dict[str, object]:
    return {
        "artifact_type": "StructuredChange",
        "schema_version": "v1",
        "change_id": change_id,
        "project_id": project_id,
        "source_snapshot_id": f"before-{suffix}",
        "target_snapshot_id": f"after-{suffix}",
        "stable_key": "screen:expense/status-filter",
        "fact_type": "screen_element",
        "domain": "ui",
        "change_type": "modified",
        "before": {
            "fact_ref": "before",
            "values": {"options": ["申請中"]},
            "source_refs": ["node-1"],
        },
        "after": {
            "fact_ref": "after",
            "values": {"options": ["申請中", "差戻し"]},
            "source_refs": ["node-2"],
        },
        "summary": "费用状态筛选增加差戻し选项",
        "source_refs": ["node-1", "node-2"],
        "confidence": "high",
        "review_status": "accepted",
    }
