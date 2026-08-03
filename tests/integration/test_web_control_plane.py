import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from operamind.application.web_control_plane import (
    BusinessRuleInput,
    ChangeRequestInput,
    ProjectInitializationInput,
    WebControlPlaneService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    ChangeAutomationRepository,
    MigrationCatalog,
    MigrationRunner,
    OrchestrationTaskRepository,
    ProfileRepository,
    WebControlPlaneRepository,
)
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_project_initialization_accepts_local_code_and_document_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"local-project-{suffix}"
    workspace = tmp_path / "code"
    documents = tmp_path / "design"
    shared_documents = tmp_path / "shared-api"
    workspace.mkdir()
    documents.mkdir()
    shared_documents.mkdir()
    (workspace / "README.md").write_text("local project\n", encoding="utf-8")
    (documents / "screen-design.md").write_text("screen design\n", encoding="utf-8")
    (shared_documents / "api-design.md").write_text("api design\n", encoding="utf-8")
    monkeypatch.setattr(
        "operamind.application.web_control_plane.ProjectDocumentBaselineService",
        lambda **_values: SimpleNamespace(
            ensure=lambda **_arguments: SimpleNamespace(
                snapshot_id="snapshot-local",
                document_count=1,
                index_build_id="index-local",
                generated_vector_count=1,
                embedding_profile_binding_key="embedding:document_search",
            )
        ),
    )

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        service = WebControlPlaneService(connection=connection, repository_root=ROOT)
        value = ProjectInitializationInput(
            project_id=project_id,
            name="Local files project",
            workspace_root=workspace,
            document_roots=(documents, shared_documents),
            configured_by="local-user",
        )

        created = service.initialize_project(value)
        replay = service.initialize_project(value)
        projects = service.list_projects()["projects"]
        assert isinstance(projects, list)
        listed = next(
            project
            for project in projects
            if isinstance(project, dict) and project.get("project_id") == project_id
        )

        assert created["created"] is True
        assert replay["created"] is False
        project = created["project"]
        assert project["project_id"] == project_id
        assert project["name"] == "Local files project"
        assert project["workspace_root"] == str(workspace.resolve())
        assert project["document_roots"] == [
            str(documents.resolve()),
            str(shared_documents.resolve()),
        ]
        assert project["source_control_kind"] == "local_files"
        assert project["test_base_url"] is None
        source_baselines = project["source_git_baselines"]
        assert len(source_baselines) == 3
        assert {item["source_kind"] for item in source_baselines} == {
            "code",
            "document",
        }
        assert all(len(item["baseline_revision"]) == 40 for item in source_baselines)
        assert (workspace / ".git").is_dir()
        assert (documents / ".git").is_dir()
        assert (shared_documents / ".git").is_dir()
        assert listed["workspace_root"] == str(workspace.resolve())
        assert listed["document_roots"] == [
            str(documents.resolve()),
            str(shared_documents.resolve()),
        ]
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_local_files_project_creates_internal_digest_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"local-project-{suffix}"
    workspace = tmp_path / "code"
    documents = tmp_path / "design"
    workspace.mkdir()
    documents.mkdir()
    (workspace / "README.md").write_text("local project\n", encoding="utf-8")
    (documents / "design.md").write_text("design\n", encoding="utf-8")
    monkeypatch.setattr(
        "operamind.application.web_control_plane.ProjectDocumentBaselineService",
        lambda **_values: SimpleNamespace(
            ensure=lambda **_arguments: SimpleNamespace(
                snapshot_id="snapshot-local",
                document_count=1,
                index_build_id="index-local",
                generated_vector_count=1,
                embedding_profile_binding_key="embedding:document_search",
            )
        ),
    )

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        service = WebControlPlaneService(connection=connection, repository_root=ROOT)
        service.initialize_project(
            ProjectInitializationInput(
                project_id=project_id,
                name="Local files project",
                workspace_root=workspace,
                document_roots=(documents,),
                configured_by="local-user",
            )
        )
        registration = WebControlPlaneRepository(
            connection,
            ContractCatalog.load(ROOT / "contracts"),
        ).project_workspace_registration(project_id)
        assert registration is not None
        assert registration["source_control_kind"] == "local_files"
        assert (workspace / ".git").is_dir()
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_change_request_auto_binds_detected_springboot15_runtime_profiles(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"spring15-project-{suffix}"
    repository_id = f"spring15-repository-{suffix}"
    request_id = f"spring15-request-{suffix}"
    workspace, remote_url = _springboot15_workspace(tmp_path, suffix)

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES (%s, 'Spring Boot 1.5 test')",
                (project_id,),
            )
            cursor.execute(
                """
                INSERT INTO repositories (
                    repository_id, project_id, remote_url, workspace_root
                ) VALUES (%s, %s, %s, %s)
                """,
                (repository_id, project_id, remote_url, str(workspace.resolve())),
            )

        service = WebControlPlaneService(connection=connection, repository_root=ROOT)
        request = ChangeRequestInput(
            change_request_id=request_id,
            project_id=project_id,
            analysis_case_id=None,
            input_mode="natural_language",
            requirement_text="検索条件に承認待ちを追加する",
            source_document_ref=None,
            target_document_ref=None,
            business_rules=(
                BusinessRuleInput(
                    "rule-status",
                    "承認待ちのデータを検索できること",
                    (),
                ),
            ),
            ambiguity_status="clear",
            ambiguities=(),
            submitted_by="integration-reviewer",
        )

        created = service.submit_change_request(request)
        replay = service.submit_change_request(request)
        bindings = ProfileRepository(
            connection,
            ProfileCatalog.load(ROOT / "profiles"),
        ).list_active_by_type(
            project_id=project_id,
            profile_type="CodeFrameworkProfile",
        ) + ProfileRepository(
            connection,
            ProfileCatalog.load(ROOT / "profiles"),
        ).list_active_by_type(
            project_id=project_id,
            profile_type="CommandExecutionProfile",
        )

        assert created["created"] is True
        assert replay["created"] is False
        assert created["change_request"]["analysis_case_id"] is not None
        assert created["copilot_task"]["task"]["target_project"]["stack_id"] == (
            "springboot15-thymeleaf-gradle"
        )
        assert {binding.binding_key for binding in bindings} == {
            f"code-framework:{repository_id}",
            f"command-execution:{repository_id}",
        }
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                FROM profile_activation_events
                WHERE project_id = %s
                """,
                (project_id,),
            )
            assert cursor.fetchone() == (2,)
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_change_request_diff_waits_for_shared_human_confirmation() -> None:
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
        diff = web_repository.document_diff(request_id)
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
        claimed_review_task = OrchestrationTaskRepository(connection).claim_next(
            executor_kind="human",
            executor_id="integration-reviewer",
            capabilities=("requirement_review",),
            project_id=project_id,
        )
        assert claimed_review_task is not None
        stored = web_repository.get_change_request(request_id)
        resumed_automation = automation["run"]
        automation_repository = ChangeAutomationRepository(connection)
        run_id = str(resumed_automation["automation_run_id"])
        recorded = automation_repository.record_confirmation(
            confirmation_id=f"confirmation-{suffix}",
            run_id=run_id,
            checkpoint="requirement",
            subject_digest="a" * 64,
            decision="confirmed",
            surface="web",
            actor="integration-reviewer",
            note=None,
        )
        replayed = automation_repository.record_confirmation(
            confirmation_id=f"confirmation-{suffix}",
            run_id=run_id,
            checkpoint="requirement",
            subject_digest="a" * 64,
            decision="confirmed",
            surface="web",
            actor="integration-reviewer",
            note=None,
        )

        assert first["created"] is True
        assert replay["created"] is False
        assert diff["total"] == 1
        assert automation["created"] is True
        assert automation_replay["created"] is False
        assert automation["run"]["current_stage"] == "requirement_confirmation"
        assert isinstance(resumed_automation, dict)
        assert resumed_automation["current_stage"] == "requirement_confirmation"
        assert resumed_automation["current_task"]["action"] == "confirm_requirement"
        assert len(resumed_automation["events"]) == 1
        assert diff["changes"][0]["summary"] == "费用状态筛选增加差戻し选项"
        assert stored["document_review"]["status"] == "pending"
        assert recorded["created"] is True
        assert replayed["created"] is False
        assert automation_repository.current_confirmations(
            run_id=run_id,
            subject_digests={"requirement": "a" * 64},
        )["requirement"]["surface"] == "web"
        assert automation_repository.current_confirmations(
            run_id=run_id,
            subject_digests={"requirement": "b" * 64},
        ) == {}
        assert automation_repository.latest_confirmation(
            run_id=run_id,
            checkpoint="requirement",
        )["subject_digest"] == "a" * 64
        discovery = {
            "status": "ready",
            "mode": "requirement_hybrid_rag",
            "candidates": [{"document_id": "document-1", "summary": "対象設計書"}],
            "blocking_reason": None,
        }
        stored_discovery = automation_repository.record_rag_discovery(
            run_id=run_id,
            discovery=discovery,
        )
        replayed_discovery = automation_repository.record_rag_discovery(
            run_id=run_id,
            discovery=discovery,
        )
        assert stored_discovery["created"] is True
        assert replayed_discovery["created"] is False
        assert automation_repository.rag_discovery(run_id) == discovery

        replacement = service.start_change_automation(
            request_id=request_id,
            idempotency_key="revised-test-plan",
            actor="integration-reviewer",
        )
        assert automation_repository.view(run_id)["status"] == "superseded"
        assert (
            automation_repository.latest_for_request(request_id)["automation_run_id"]
            == replacement["run"]["automation_run_id"]
        )
        assert all(
            task["state"] == "superseded"
            for task in OrchestrationTaskRepository(connection).list_for_run(run_id)
        )
        assert first["copilot_task"] is None
        assert first["task_blocker"]
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


def _springboot15_workspace(tmp_path: Path, suffix: str) -> tuple[Path, str]:
    workspace = tmp_path / f"springboot15-{suffix}"
    template = workspace / "src" / "main" / "resources" / "templates" / "expense"
    wrapper = workspace / "gradle" / "wrapper"
    template.mkdir(parents=True)
    wrapper.mkdir(parents=True)
    (workspace / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (wrapper / "gradle-wrapper.properties").write_text(
        "distributionUrl=https://services.gradle.org/distributions/gradle-4.10.3-bin.zip\n",
        encoding="utf-8",
    )
    (workspace / "build.gradle").write_text(
        """
buildscript {
    ext { springBootVersion = '1.5.22.RELEASE' }
    dependencies {
        classpath("org.springframework.boot:spring-boot-gradle-plugin:${springBootVersion}")
    }
}
apply plugin: 'org.springframework.boot'
apply plugin: 'jacoco'
jacocoTestReport { reports { xml.enabled true } }
dependencies {
    compile 'org.springframework.boot:spring-boot-starter-thymeleaf'
}
""",
        encoding="utf-8",
    )
    (template / "list.html").write_text(
        '<html xmlns:th="http://www.thymeleaf.org"></html>\n',
        encoding="utf-8",
    )
    test = workspace / "src" / "test" / "java" / "example" / "SmokeTest.java"
    test.parent.mkdir(parents=True)
    test.write_text("class SmokeTest {}\n", encoding="utf-8")
    remote_url = f"https://example.invalid/{suffix}.git"
    _git(workspace, "init", "-q")
    _git(workspace, "remote", "add", "origin", remote_url)
    _git(workspace, "add", ".")
    _git(
        workspace,
        "-c",
        "user.name=OperaMind Test",
        "-c",
        "user.email=operamind@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    return workspace, remote_url


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
