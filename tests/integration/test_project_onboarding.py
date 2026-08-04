import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from openpyxl import Workbook

from operamind.application import project_onboarding as onboarding_module
from operamind.application.document_profile_learning import DocumentProfileLearningService
from operamind.application.project_onboarding import ProjectOnboardingService
from operamind.application.web_control_plane import (
    ProjectInitializationInput,
    ProjectSettingsUpdateInput,
    WebControlPlaneService,
)
from operamind.infrastructure.postgres import (
    MigrationCatalog,
    MigrationRunner,
    ProjectOnboardingRepository,
)
from operamind.infrastructure.postgres.document_profile_learning_repository import (
    DocumentProfileLearningRepository,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


def _project(connection: psycopg.Connection[object], tmp_path: Path) -> tuple[str, Path, Path]:
    project_id = f"onboarding-{uuid4().hex}"
    workspace = tmp_path / "code"
    documents = tmp_path / "documents"
    workspace.mkdir()
    documents.mkdir()
    (workspace / ".git").mkdir()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, 'Onboarding Test')",
            (project_id,),
        )
        cursor.execute(
            """
            INSERT INTO project_workspaces (
                project_id, workspace_root, source_control_kind, configured_by
            ) VALUES (%s, %s, 'local_files', 'test')
            """,
            (project_id, str(workspace)),
        )
        cursor.execute(
            """
            INSERT INTO project_document_roots (
                document_root_id, project_id, root_path, position
            ) VALUES (%s, %s, %s, 0)
            """,
            (f"root-{uuid4().hex}", project_id, str(documents)),
        )
    return project_id, workspace, documents


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_onboarding_advances_one_persisted_stage_at_a_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None

    class Baseline:
        def __init__(self, **_values: object) -> None:
            pass

        def discover(self, **_values: object) -> SimpleNamespace:
            return SimpleNamespace(ready=True, review_required=())

        def store_documents(self, **_values: object) -> SimpleNamespace:
            return SimpleNamespace(snapshot_id="snapshot-current", document_count=2)

        def build_index(self, **_values: object) -> SimpleNamespace:
            return SimpleNamespace(index_build_id="index-current", generated_vector_count=7)

    monkeypatch.setattr(onboarding_module, "ProjectDocumentBaselineService", Baseline)
    monkeypatch.setattr(
        onboarding_module,
        "DocumentProfileLearningService",
        lambda **_values: SimpleNamespace(
            ensure_task=lambda **_arguments: (
                SimpleNamespace(learning_run_id="confirmed-learning"),
                True,
            )
        ),
    )
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        project_id, _workspace, _documents = _project(connection, tmp_path)
        service = ProjectOnboardingService(connection=connection, repository_root=ROOT)
        queued = service.enqueue(project_id=project_id, action="initialize", actor="test")

        first = service.advance_one(owner="worker-a")
        after_first = service.latest(project_id)
        second = service.advance_one(owner="worker-a")
        after_second = service.latest(project_id)
        third = service.advance_one(owner="worker-a")
        completed = service.latest(project_id)

    assert queued.current_stage == "discover"
    assert first.stage == "discover"
    assert after_first is not None and after_first["current_stage"] == "documents"
    assert second.stage == "documents"
    assert after_second is not None and after_second["current_stage"] == "index"
    assert third.stage == "index"
    assert completed is not None
    assert completed["status"] == "ready"
    assert completed["current_stage"] == "complete"
    assert completed["document_snapshot_id"] == "snapshot-current"
    assert completed["search_index_build_id"] == "index-current"
    assert completed["generated_vector_count"] == 7


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_expired_onboarding_lease_is_reclaimed_and_failed_stage_can_retry(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        project_id, _workspace, _documents = _project(connection, tmp_path)
        repository = ProjectOnboardingRepository(connection)
        run = repository.enqueue(
            onboarding_run_id=f"run-{uuid4().hex}",
            project_id=project_id,
            settings_revision=1,
            requested_action="rescan",
            requested_by="test",
        )
        first = repository.claim_next(owner="worker-a", lease_seconds=30)
        assert first is not None
        assert repository.heartbeat(claim=first, lease_seconds=30)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_onboarding_runs
                SET lease_expires_at = %s
                WHERE onboarding_run_id = %s
                """,
                (datetime.now(UTC) - timedelta(seconds=1), run.onboarding_run_id),
            )
        recovered = repository.claim_next(owner="worker-b", lease_seconds=30)
        assert recovered is not None
        assert not repository.heartbeat(claim=first, lease_seconds=30)
        failed = repository.fail(claim=recovered, reason="embedding unavailable")
        retried = repository.retry(onboarding_run_id=run.onboarding_run_id, actor="operator")
        cleanup = repository.claim_next(owner="test-cleanup", lease_seconds=30)
        assert cleanup is not None
        assert cleanup.record.onboarding_run_id == run.onboarding_run_id
        repository.fail(claim=cleanup, reason="test cleanup")

    assert recovered.lease_token != first.lease_token
    assert recovered.record.attempt_count == 2
    assert failed.status == "failed"
    assert failed.failure_reason == "embedding unavailable"
    assert retried.status == "queued"
    assert retried.current_stage == "discover"


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_settings_revision_supersedes_stale_onboarding_run(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        project_id, _workspace, _documents = _project(connection, tmp_path)
        repository = ProjectOnboardingRepository(connection)
        repository.enqueue(
            onboarding_run_id=f"run-{uuid4().hex}",
            project_id=project_id,
            settings_revision=1,
            requested_action="rescan",
            requested_by="test",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE project_workspaces SET settings_revision = 2 WHERE project_id = %s",
                (project_id,),
            )

        assert repository.claim_next(owner="worker-a") is None
        latest = repository.latest(project_id)

    assert latest is not None
    assert latest.status == "superseded"
    assert latest.failure_reason == "Project settings changed before this run completed"


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_discovery_failure_is_persisted_for_operator_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None

    class Baseline:
        def __init__(self, **_values: object) -> None:
            pass

        def discover(self, **_values: object) -> SimpleNamespace:
            return SimpleNamespace(
                ready=False,
                review_required=("ambiguous design profile",),
            )

    monkeypatch.setattr(onboarding_module, "ProjectDocumentBaselineService", Baseline)
    monkeypatch.setattr(
        onboarding_module,
        "DocumentProfileLearningService",
        lambda **_values: SimpleNamespace(
            ensure_task=lambda **_arguments: (_ for _ in ()).throw(
                ValueError("ambiguous design profile")
            )
        ),
    )
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        project_id, _workspace, _documents = _project(connection, tmp_path)
        service = ProjectOnboardingService(connection=connection, repository_root=ROOT)
        service.enqueue(project_id=project_id, action="initialize", actor="test")

        iteration = service.advance_one(owner="worker-a")
        failed = service.latest(project_id)

    assert iteration.outcome == "failed"
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["current_stage"] == "discover"
    assert failed["failure_reason"] == "ambiguous design profile"


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_new_project_waits_for_project_specific_document_learning(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        project_id, workspace, documents = _project(connection, tmp_path)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Screen Items"
        sheet.append(["Screen ID", "Element ID", "Type", "Default Value", "Notes"])
        sheet.append(["customer-list", "status-filter", "select", "all", "filter"])
        workbook.save(documents / "screen-design.xlsx")
        service = ProjectOnboardingService(connection=connection, repository_root=ROOT)
        service.enqueue(project_id=project_id, action="initialize", actor="operator")

        iteration = service.advance_one(owner="onboarding-worker")
        onboarding = service.latest(project_id)
        learning_repository = DocumentProfileLearningRepository(connection)
        learning = learning_repository.latest(project_id)
        claimed = learning_repository.claim_next(
            workspace_root=str(workspace.resolve()),
            consumer_id="vscode-test",
        )
        assert claimed is not None
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_document_learning_runs
                SET claim_expires_at = %s
                WHERE learning_run_id = %s
                """,
                (
                    datetime.now(UTC) - timedelta(seconds=1),
                    claimed.record.learning_run_id,
                ),
            )
        with pytest.raises(PersistenceConflictError, match="claim is unavailable"):
            learning_repository.accept(
                learning_run_id=claimed.record.learning_run_id,
                workspace_root=str(workspace.resolve()),
                consumer_id="vscode-test",
                claim_token=claimed.claim_token,
                actor="github-copilot",
            )
        reclaimed = learning_repository.claim_next(
            workspace_root=str(workspace.resolve()),
            consumer_id="vscode-test",
        )

    assert iteration.outcome == "waiting_for_profile"
    assert onboarding is not None
    assert onboarding["status"] == "waiting_for_profile"
    assert onboarding["current_stage"] == "learn"
    assert learning is not None and learning.status == "pending"
    assert learning.project_id == project_id
    assert learning.sample_count == 1
    assert reclaimed is not None
    assert reclaimed.record.learning_run_id == learning.learning_run_id
    assert reclaimed.claim_token != claimed.claim_token


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_confirmed_project_profile_resumes_canonical_onboarding(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        project_id, workspace, documents = _project(connection, tmp_path)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Screen Items"
        sheet.append(["Screen ID", "Element ID", "Type", "Default Value", "Notes"])
        sheet.append(["customer-list", "status-filter", "select", "all", "filter"])
        workbook.save(documents / "screen-design.xlsx")
        onboarding_service = ProjectOnboardingService(
            connection=connection,
            repository_root=ROOT,
        )
        onboarding_service.enqueue(
            project_id=project_id,
            action="initialize",
            actor="operator",
        )
        onboarding_service.advance_one(owner="onboarding-worker")
        learning_service = DocumentProfileLearningService(
            connection=connection,
            repository_root=ROOT,
        )
        learning_repository = DocumentProfileLearningRepository(connection)
        learning = learning_repository.latest(project_id)
        assert learning is not None
        claim = learning_repository.claim_next(
            workspace_root=str(workspace.resolve()),
            consumer_id="vscode-copilot",
        )
        assert claim is not None
        learning_service.accept(
            learning_run_id=learning.learning_run_id,
            workspace_root=workspace,
            consumer_id="vscode-copilot",
            claim_token=claim.claim_token,
            actor="github-copilot",
        )
        context = learning_service.mcp_context(
            learning_run_id=learning.learning_run_id,
            workspace_root=workspace,
            consumer_id="vscode-copilot",
            claim_token=claim.claim_token,
        )
        profile = json.loads(
            (ROOT / "profiles/screen-design-convention-profile.example.json").read_text(
                encoding="utf-8"
            )
        )
        project_identity = hashlib.sha256(project_id.encode()).hexdigest()[:8]
        profile["profile_id"] = f"project-{project_id}-{project_identity}-screen-design"
        profile["profile_version"] = "1.0.0"
        sample_id = str(learning.source_structure["samples"][0]["sample_id"])
        draft = {
            "artifact_type": "DocumentProfileLearningDraft",
            "schema_version": "v1",
            "learning_run_id": learning.learning_run_id,
            "project_id": project_id,
            "source_structure_digest": learning.source_structure_digest,
            "generated_by": "vscode_github_copilot",
            "profiles": [profile],
            "document_assignments": [
                {
                    "sample_id": sample_id,
                    "profile_id": profile["profile_id"],
                    "variant_id": "screen-item-table-en",
                    "reason": "Sheet and headers uniquely match",
                }
            ],
            "ambiguities": [],
        }

        with pytest.raises(PersistenceConflictError, match="not writable"):
            learning_service.record_draft(
                learning_run_id=learning.learning_run_id,
                workspace_root=workspace,
                consumer_id="stale-vscode-consumer",
                claim_token=claim.claim_token,
                draft=draft,
            )

        with pytest.raises(PersistenceConflictError, match="not writable"):
            learning_service.record_draft(
                learning_run_id=learning.learning_run_id,
                workspace_root=workspace,
                consumer_id="vscode-copilot",
                claim_token="wrong-claim-token",
                draft=draft,
            )

        incomplete_draft = {**draft, "document_assignments": []}
        incomplete = learning_service.record_draft(
            learning_run_id=learning.learning_run_id,
            workspace_root=workspace,
            consumer_id="vscode-copilot",
            claim_token=claim.claim_token,
            draft=incomplete_draft,
        )

        recorded = learning_service.record_draft(
            learning_run_id=learning.learning_run_id,
            workspace_root=workspace,
            consumer_id="vscode-copilot",
            claim_token=claim.claim_token,
            draft=draft,
        )
        terminal_view = learning_service.resume(
            learning_run_id=learning.learning_run_id,
            workspace_root=workspace,
            consumer_id="vscode-copilot",
            claim_token=claim.claim_token,
        )
        confirmed = learning_service.confirm(
            project_id=project_id,
            learning_run_id=learning.learning_run_id,
            actor="operator",
        )
        onboarding = onboarding_service.latest(project_id)
        profile_ids = learning_repository.profile_version_ids(learning.learning_run_id)

    assert context["inputs"]["profile_contract"]["title"] == "DocumentConventionProfile"
    assert context["inputs"]["structure_diff"]["has_previous_version"] is False
    assert incomplete["learning"]["status"] == "in_progress"
    assert incomplete["stage_status"]["outcome"] == "blocked"
    assert recorded["learning"]["status"] == "draft_ready"
    assert recorded["learning"]["coverage_percent"] == 100.0
    assert terminal_view["state"] == "draft_ready"
    assert "claim_token" not in terminal_view
    assert confirmed["status"] == "confirmed"
    assert onboarding is not None
    assert onboarding["status"] == "queued"
    assert onboarding["current_stage"] == "documents"
    assert profile_ids == (
        f"project-{project_id}-{project_identity}-screen-design-1.0.0",
    )


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_project_settings_replace_document_bindings_and_queue_rescan(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    project_id = f"settings-{uuid4().hex}"
    workspace = tmp_path / "code"
    first_documents = tmp_path / "design-v1"
    second_documents = tmp_path / "design-v2"
    workspace.mkdir()
    first_documents.mkdir()
    second_documents.mkdir()
    (workspace / "README.md").write_text("code\n", encoding="utf-8")
    (first_documents / "notes.md").write_text("v1\n", encoding="utf-8")
    (second_documents / "notes.md").write_text("v2\n", encoding="utf-8")

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        service = WebControlPlaneService(connection=connection, repository_root=ROOT)
        created = service.initialize_project(
            ProjectInitializationInput(
                project_id=project_id,
                name="Settings Project",
                workspace_root=workspace,
                document_roots=(first_documents,),
                configured_by="operator",
            )
        )
        updated = service.update_project_settings(
            ProjectSettingsUpdateInput(
                project_id=project_id,
                name="Settings Project Updated",
                document_roots=(second_documents,),
                test_base_url="http://127.0.0.1:8080/app",
                expected_revision=1,
                updated_by="operator",
            )
        )
        configuration = service._repository.project_configuration(project_id)

    assert created["project"]["settings_revision"] == 1
    assert updated["project"]["settings_revision"] == 2
    assert updated["onboarding"]["requested_action"] == "rescan"
    assert updated["onboarding"]["status"] == "queued"
    assert configuration["name"] == "Settings Project Updated"
    assert configuration["document_roots"] == [str(second_documents.resolve())]
    assert configuration["test_base_url"] == "http://127.0.0.1:8080/app"
    baselines = configuration["source_git_baselines"]
    assert isinstance(baselines, list)
    document_baselines = [item for item in baselines if item["source_kind"] == "document"]
    assert [item["configured_root"] for item in document_baselines] == [
        str(second_documents.resolve())
    ]
