import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psycopg
import pytest

from operamind.application import (
    CodeGraphBuildBlockedError,
    CodeGraphBuildRequest,
    CodeGraphBuildService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    CodeGraphPublishResult,
    CodeGraphSnapshotRepository,
    MigrationCatalog,
    MigrationRunner,
    PersistenceConflictError,
    ProfileRepository,
)
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


def code_graph_artifact(
    *,
    suffix: str,
    project_id: str,
    repository_id: str,
    commit_sha: str,
    snapshot_label: str,
) -> dict[str, Any]:
    profile_ref = "spring-web-example@1.0.0"
    production_path = "src/main/java/example/ExpenseService.java"
    test_path = "src/test/java/example/ExpenseServiceTest.java"
    return {
        "artifact_type": "CodeGraphSnapshot",
        "schema_version": "v1",
        "code_graph_snapshot_id": f"code-graph-{snapshot_label}-{suffix}",
        "project_id": project_id,
        "repository_id": repository_id,
        "repository_revision": commit_sha,
        "framework_profile_refs": [profile_ref],
        "scan_roots": ["src/main", "src/test"],
        "scan_status": "complete",
        "framework_markers_found": ["org.springframework.web.bind.annotation"],
        "diagnostics": [],
        "files": [
            {
                "file_id": f"file-service-{snapshot_label}-{suffix}",
                "path": production_path,
                "language": "java",
                "role": "production",
                "content_hash": f"sha256:service-{snapshot_label}-{suffix}",
                "symbols": [
                    {
                        "symbol_id": f"symbol-search-{snapshot_label}-{suffix}",
                        "symbol_type": "method",
                        "name": "search",
                        "signature": "search(String status)",
                        "start_line": 20,
                        "end_line": 34,
                    }
                ],
            },
            {
                "file_id": f"file-test-{snapshot_label}-{suffix}",
                "path": test_path,
                "language": "java",
                "role": "test",
                "content_hash": f"sha256:test-{snapshot_label}-{suffix}",
                "symbols": [
                    {
                        "symbol_id": f"symbol-test-{snapshot_label}-{suffix}",
                        "symbol_type": "method",
                        "name": "searchReturnsAllExpenses",
                        "signature": "searchReturnsAllExpenses()",
                        "start_line": 12,
                        "end_line": 24,
                    }
                ],
            },
        ],
        "edges": [
            {
                "edge_id": f"edge-contains-{snapshot_label}-{suffix}",
                "edge_type": "contains",
                "from_ref": f"file-service-{snapshot_label}-{suffix}",
                "to_ref": f"symbol-search-{snapshot_label}-{suffix}",
                "resolution_status": "resolved",
                "confidence": "high",
                "extractor": "java_symbol",
                "profile_version": profile_ref,
                "source_location": {
                    "path": production_path,
                    "start_line": 20,
                    "end_line": 34,
                },
            },
            {
                "edge_id": f"edge-tests-{snapshot_label}-{suffix}",
                "edge_type": "tests",
                "from_ref": f"symbol-test-{snapshot_label}-{suffix}",
                "to_ref": f"symbol-search-{snapshot_label}-{suffix}",
                "resolution_status": "resolved",
                "confidence": "high",
                "extractor": "junit_test",
                "profile_version": profile_ref,
                "source_location": {
                    "path": test_path,
                    "start_line": 12,
                    "end_line": 24,
                },
            },
            {
                "edge_id": f"edge-calls-{snapshot_label}-{suffix}",
                "edge_type": "calls",
                "from_ref": f"symbol-search-{snapshot_label}-{suffix}",
                "to_ref": "external:expenseRepository.search",
                "resolution_status": "unresolved",
                "confidence": "medium",
                "extractor": "java_symbol",
                "profile_version": profile_ref,
                "source_location": {
                    "path": production_path,
                    "start_line": 23,
                    "end_line": 23,
                },
            },
        ],
    }


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def create_scannable_repository(tmp_path: Path, suffix: str) -> tuple[Path, str, str]:
    repository = tmp_path / f"target-repository-{suffix}"
    repository.mkdir()
    remote_url = f"https://example.invalid/{suffix}.git"
    git(repository, "init", "-q")
    git(repository, "remote", "add", "origin", remote_url)
    source = repository / "src/main/java/example"
    tests = repository / "src/test/java/example"
    source.mkdir(parents=True)
    tests.mkdir(parents=True)
    (repository / ".gitignore").write_text("Ignored.java\n", encoding="utf-8")
    (source / "ExpenseService.java").write_text(
        """package example;
import org.springframework.web.bind.annotation.GetMapping;
class ExpenseService {
  @GetMapping("/expenses") String search(String status) { return status; }
}
""",
        encoding="utf-8",
    )
    (tests / "ExpenseServiceTest.java").write_text(
        """package example;
import org.junit.jupiter.api.Test;
class ExpenseServiceTest {
  private final ExpenseService service = new ExpenseService();
  @Test void findsExpenses() { service.search(null); }
}
""",
        encoding="utf-8",
    )
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=OperaMind Test",
        "-c",
        "user.email=operamind@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    (source / "Ignored.java").write_text("class Ignored {}\n", encoding="utf-8")
    return repository, git(repository, "rev-parse", "HEAD"), remote_url


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_code_graph_publication_is_normalized_current_and_replay_safe() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    repository_id = f"repository-{suffix}"
    repository_revision_id = f"revision-{suffix}"
    profile_version_id = f"code-profile-{suffix}"
    commit_sha = f"commit-{suffix}"
    profile_ref = "spring-web-example@1.0.0"

    with psycopg.connect(DATABASE_URL) as connection:
        contracts = ContractCatalog.load(ROOT / "contracts")
        profiles = ProfileCatalog.load(ROOT / "profiles")
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES (%s, %s)",
                (project_id, "Code Graph integration test"),
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
                    repository_revision_id,
                    repository_id,
                    commit_sha
                ) VALUES (%s, %s, %s)
                """,
                (repository_revision_id, repository_id, commit_sha),
            )
        ProfileRepository(connection, profiles).store_version(
            profile_version_id=profile_version_id,
            profile=_load_profile(ROOT / "profiles/code-framework-profile.example.json"),
        )
        repository = CodeGraphSnapshotRepository(connection, contracts)
        first_artifact = code_graph_artifact(
            suffix=suffix,
            project_id=project_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            snapshot_label="first",
        )

        first = repository.publish(
            artifact=first_artifact,
            repository_revision_id=repository_revision_id,
            profile_version_ids={profile_ref: profile_version_id},
        )
        replay = repository.publish(
            artifact=first_artifact,
            repository_revision_id=repository_revision_id,
            profile_version_ids={profile_ref: profile_version_id},
        )

        assert first.created
        assert first.status == "complete"
        assert first.is_current
        assert (first.file_count, first.symbol_count, first.edge_count) == (2, 2, 3)
        assert first.unresolved_edge_count == 1
        assert first.test_binding_count == 1
        assert not replay.created
        assert repository.get(first.code_graph_snapshot_id) == first_artifact
        assert (
            repository.get_current(
                project_id=project_id,
                repository_id=repository_id,
            )
            == replay
        )
        alternate_profile_version_id = f"alternate-code-profile-{suffix}"
        alternate_profile = _load_profile(ROOT / "profiles/code-framework-profile.example.json")
        alternate_profile["profile_id"] = "spring-web-alternate"
        ProfileRepository(connection, profiles).store_version(
            profile_version_id=alternate_profile_version_id,
            profile=alternate_profile,
        )
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT code_graph_profile_mapping_probe")
            cursor.execute(
                """
                UPDATE code_graph_snapshot_profiles
                SET profile_ref = %s, profile_version_id = %s
                WHERE code_graph_snapshot_id = %s
                """,
                (
                    "spring-web-alternate@1.0.0",
                    alternate_profile_version_id,
                    first.code_graph_snapshot_id,
                ),
            )
        with pytest.raises(PersistenceConflictError, match="Profile mapping differs"):
            repository.publish(
                artifact=first_artifact,
                repository_revision_id=repository_revision_id,
                profile_version_ids={profile_ref: profile_version_id},
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT code_graph_profile_mapping_probe")
            cursor.execute("RELEASE SAVEPOINT code_graph_profile_mapping_probe")

        failed_artifact = {
            **first_artifact,
            "code_graph_snapshot_id": f"code-graph-failed-{suffix}",
            "scan_status": "failed",
            "framework_markers_found": [],
            "diagnostics": ["scanner_runtime_failure"],
            "files": [],
            "edges": [],
        }
        failed = repository.publish(
            artifact=failed_artifact,
            repository_revision_id=repository_revision_id,
            profile_version_ids={profile_ref: profile_version_id},
            failure_reason="RuntimeError: scanner failed",
        )
        failed_replay = repository.publish(
            artifact=failed_artifact,
            repository_revision_id=repository_revision_id,
            profile_version_ids={profile_ref: profile_version_id},
            failure_reason="RuntimeError: scanner failed",
        )
        assert failed.created and failed.status == "failed" and not failed.is_current
        assert not failed_replay.created
        with pytest.raises(PersistenceConflictError, match="publication identity differs"):
            repository.publish(
                artifact=failed_artifact,
                repository_revision_id=repository_revision_id,
                profile_version_ids={profile_ref: profile_version_id},
                failure_reason="ValueError: different failure",
            )

        second_artifact = code_graph_artifact(
            suffix=suffix,
            project_id=project_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            snapshot_label="second",
        )
        second = repository.publish(
            artifact=second_artifact,
            repository_revision_id=repository_revision_id,
            profile_version_ids={profile_ref: profile_version_id},
        )
        stale_replay = repository.publish(
            artifact=first_artifact,
            repository_revision_id=repository_revision_id,
            profile_version_ids={profile_ref: profile_version_id},
        )

        assert second.created and second.is_current
        assert stale_replay.status == "stale"
        assert not stale_replay.is_current
        assert repository.get_current(
            project_id=project_id,
            repository_id=repository_id,
        ) == _without_created(second)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM code_files WHERE project_id = %s),
                    (SELECT count(*) FROM code_symbols WHERE project_id = %s),
                    (SELECT count(*) FROM code_edges WHERE project_id = %s),
                    (SELECT count(*) FROM code_test_bindings WHERE project_id = %s),
                    (SELECT count(*) FROM code_graph_snapshots
                     WHERE project_id = %s AND is_current)
                """,
                (project_id, project_id, project_id, project_id, project_id),
            )
            assert cursor.fetchone() == (4, 4, 6, 2, 1)

            cursor.execute("SAVEPOINT code_graph_edge_drift_probe")
            cursor.execute(
                """
                UPDATE code_edges
                SET extractor = 'drifted-extractor'
                WHERE code_graph_snapshot_id = %s
                """,
                (second.code_graph_snapshot_id,),
            )
        with pytest.raises(PersistenceConflictError, match="Edge ledger differs"):
            repository.get(second.code_graph_snapshot_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT code_graph_edge_drift_probe")
            cursor.execute("RELEASE SAVEPOINT code_graph_edge_drift_probe")

            cursor.execute("SAVEPOINT code_graph_symbol_delete_probe")
            cursor.execute(
                "DELETE FROM code_symbols WHERE code_graph_snapshot_id = %s",
                (second.code_graph_snapshot_id,),
            )
        with pytest.raises(PersistenceConflictError, match="Symbol ledger differs"):
            repository.get(second.code_graph_snapshot_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT code_graph_symbol_delete_probe")
            cursor.execute("RELEASE SAVEPOINT code_graph_symbol_delete_probe")

            cursor.execute("SAVEPOINT code_graph_test_binding_drift_probe")
            cursor.execute(
                """
                UPDATE code_test_bindings
                SET confidence = 'medium'
                WHERE code_graph_snapshot_id = %s
                """,
                (second.code_graph_snapshot_id,),
            )
        with pytest.raises(PersistenceConflictError, match="Test Binding ledger differs"):
            repository.get(second.code_graph_snapshot_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT code_graph_test_binding_drift_probe")
            cursor.execute("RELEASE SAVEPOINT code_graph_test_binding_drift_probe")

        conflicting = {**first_artifact, "scan_roots": ["src/main"]}
        with pytest.raises(PersistenceConflictError, match="different content"):
            repository.publish(
                artifact=conflicting,
                repository_revision_id=repository_revision_id,
                profile_version_ids={profile_ref: profile_version_id},
            )
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_code_graph_build_service_binds_git_and_publishes_tracked_tree_sitter_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    repository_id = f"repository-{suffix}"
    repository_revision_id = f"revision-{suffix}"
    profile_version_id = f"code-profile-{suffix}"
    workspace, commit_sha, remote_url = create_scannable_repository(tmp_path, suffix)
    profile = _load_profile(ROOT / "profiles/code-framework-profile.example.json")
    request = CodeGraphBuildRequest(
        code_graph_snapshot_id=f"code-graph-{suffix}",
        project_id=project_id,
        repository_id=repository_id,
        repository_revision_id=repository_revision_id,
        workspace_root=workspace,
        scan_roots=("src/main", "src/test"),
        profile_version_id=profile_version_id,
        profile_binding_key=f"code-framework:{repository_id}",
        profile_activation_event_id=f"code-profile-activation-{suffix}",
        activated_by="scanner@example.invalid",
        activation_reason="Confirmed target repository scan roots",
    )

    with psycopg.connect(DATABASE_URL) as connection:
        contracts = ContractCatalog.load(ROOT / "contracts")
        profiles = ProfileCatalog.load(ROOT / "profiles")
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES (%s, %s)",
                (project_id, "Code Graph build integration test"),
            )
            cursor.execute(
                """
                INSERT INTO repositories (
                    repository_id,
                    project_id,
                    remote_url,
                    workspace_root
                ) VALUES (%s, %s, %s, %s)
                """,
                (repository_id, project_id, remote_url, str(workspace.resolve())),
            )
            cursor.execute(
                """
                INSERT INTO repository_revisions (
                    repository_revision_id,
                    repository_id,
                    commit_sha
                ) VALUES (%s, %s, %s)
                """,
                (repository_revision_id, repository_id, commit_sha),
            )
        service = CodeGraphBuildService(
            connection=connection,
            contracts=contracts,
            profiles=profiles,
        )

        first = service.run(request, profile=profile)
        replay = service.run(request, profile=profile)

        assert first.publication.created
        assert first.publication.status == "complete"
        assert first.publication.file_count == 2
        assert first.publication.symbol_count >= 5
        assert first.publication.test_binding_count == 1
        assert first.scan.diagnostics == ()
        assert not replay.publication.created
        artifact_files = cast(list[dict[str, Any]], first.scan.artifact["files"])
        assert {file["path"] for file in artifact_files} == {
            "src/main/java/example/ExpenseService.java",
            "src/test/java/example/ExpenseServiceTest.java",
        }
        active = ProfileRepository(connection, profiles).get_active(
            project_id=project_id,
            binding_key=f"code-framework:{repository_id}",
        )
        assert active is not None
        assert active.profile_version_id == profile_version_id
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload::text LIKE '%%return status%%'
                FROM artifact_records
                WHERE artifact_id = %s
                """,
                (request.code_graph_snapshot_id,),
            )
            assert cursor.fetchone() == (False,)

        tracked_source = workspace / "src/main/java/example/ExpenseService.java"
        tracked_source.write_text(
            tracked_source.read_text(encoding="utf-8").replace(
                "return status;", "return status == null ? null : status.trim();"
            ),
            encoding="utf-8",
        )
        git(workspace, "add", tracked_source.relative_to(workspace).as_posix())
        git(
            workspace,
            "-c",
            "user.name=OperaMind Test",
            "-c",
            "user.email=operamind@example.invalid",
            "commit",
            "-q",
            "-m",
            "incremental fixture",
        )
        incremental_sha = git(workspace, "rev-parse", "HEAD")
        incremental_revision_id = f"revision-incremental-{suffix}"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO repository_revisions (
                    repository_revision_id, repository_id, commit_sha
                ) VALUES (%s, %s, %s)
                """,
                (incremental_revision_id, repository_id, incremental_sha),
            )
        incremental_request = replace(
            request,
            code_graph_snapshot_id=f"code-graph-incremental-{suffix}",
            repository_revision_id=incremental_revision_id,
            profile_activation_event_id=f"code-profile-incremental-activation-{suffix}",
            activation_reason="Revision incremental scan",
        )
        incremental_result = service.run(incremental_request, profile=profile)

        assert incremental_result.scan.artifact["scan_mode"] == "incremental"
        assert incremental_result.scan.artifact["base_code_graph_snapshot_id"] == (
            request.code_graph_snapshot_id
        )
        assert incremental_result.scan.artifact["scanned_file_count"] == 1
        assert incremental_result.scan.artifact["reused_file_count"] == 1
        assert incremental_result.scan.artifact["affected_paths"] == [
            "src/main/java/example/ExpenseService.java"
        ]
        assert incremental_result.publication.scan_mode == "incremental"
        assert incremental_result.publication.base_code_graph_snapshot_id == (
            request.code_graph_snapshot_id
        )
        assert incremental_result.publication.scanned_file_count == 1
        assert incremental_result.publication.reused_file_count == 1
        assert incremental_result.publication.file_count == 2
        assert incremental_result.publication.test_binding_count == 1

        divergent_sha = git(
            workspace,
            "-c",
            "user.name=OperaMind Test",
            "-c",
            "user.email=operamind@example.invalid",
            "commit-tree",
            f"{incremental_sha}^{{tree}}",
            "-m",
            "non-ancestor fixture",
        )
        git(workspace, "checkout", "-q", "--detach", divergent_sha)
        divergent_revision_id = f"revision-divergent-{suffix}"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO repository_revisions (
                    repository_revision_id, repository_id, commit_sha
                ) VALUES (%s, %s, %s)
                """,
                (divergent_revision_id, repository_id, divergent_sha),
            )
        divergent_request = replace(
            request,
            code_graph_snapshot_id=f"code-graph-divergent-{suffix}",
            repository_revision_id=divergent_revision_id,
            profile_activation_event_id=f"code-profile-divergent-activation-{suffix}",
            activation_reason="Non-ancestor full scan fallback",
        )
        divergent_result = service.run(divergent_request, profile=profile)

        assert divergent_result.scan.artifact["scan_mode"] == "full"
        assert "base_code_graph_snapshot_id" not in divergent_result.scan.artifact
        assert divergent_result.scan.artifact["scanned_file_count"] == 2
        assert divergent_result.scan.artifact["reused_file_count"] == 0
        assert divergent_result.publication.scan_mode == "full"
        assert divergent_result.publication.base_code_graph_snapshot_id is None
        assert divergent_result.publication.test_binding_count == 1

        failed_request = replace(
            request,
            code_graph_snapshot_id=f"code-graph-runtime-failed-{suffix}",
            profile_activation_event_id=f"code-profile-failed-activation-{suffix}",
            repository_revision_id=divergent_revision_id,
            incremental=False,
        )
        with monkeypatch.context() as scanner_patch:
            scanner_patch.setattr(
                service._scanner,
                "scan",
                lambda **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("simulated scanner runtime failure")
                ),
            )
            with pytest.raises(CodeGraphBuildBlockedError, match="failed Snapshot was recorded"):
                service.run(failed_request, profile=profile)
        failed_artifact = CodeGraphSnapshotRepository(
            connection,
            contracts,
        ).get(failed_request.code_graph_snapshot_id)
        assert failed_artifact is not None
        assert failed_artifact["scan_status"] == "failed"
        assert failed_artifact["diagnostics"] == ["scan_runtime_failure:RuntimeError"]
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, is_current, failure_reason
                FROM code_graph_snapshots
                WHERE code_graph_snapshot_id = %s
                """,
                (failed_request.code_graph_snapshot_id,),
            )
            assert cursor.fetchone() == (
                "failed",
                False,
                "RuntimeError: Code Graph scan failed",
            )
            cursor.execute(
                """
                SELECT count(*) FROM profile_activation_events
                WHERE activation_event_id = %s
                """,
                (failed_request.profile_activation_event_id,),
            )
            assert cursor.fetchone() == (0,)

        tracked_source.write_text("class Dirty {}\n", encoding="utf-8")
        with pytest.raises(CodeGraphBuildBlockedError, match="clean Git worktree"):
            service.run(
                CodeGraphBuildRequest(
                    code_graph_snapshot_id=f"code-graph-dirty-{suffix}",
                    project_id=project_id,
                    repository_id=repository_id,
                    repository_revision_id=repository_revision_id,
                    workspace_root=workspace,
                    scan_roots=("src/main",),
                    profile_version_id=profile_version_id,
                    profile_binding_key=f"code-framework:{repository_id}",
                    profile_activation_event_id=f"dirty-activation-{suffix}",
                    activated_by="scanner@example.invalid",
                    activation_reason="Must not publish dirty source",
                ),
                profile=profile,
            )
        connection.rollback()


def _load_profile(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _without_created(result: CodeGraphPublishResult) -> CodeGraphPublishResult:
    return replace(result, created=False)
