import copy
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    MigrationCatalog,
    MigrationRunner,
    PersistenceConflictError,
)
from tests.fixtures.visiondemo_target_e2e import (
    build_visiondemo_cross_screen_template,
)

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_validated_artifact_round_trip() -> None:
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    repository_id = f"repository-{suffix}"
    revision_id = f"revision-{suffix}"
    case_id = f"case-{suffix}"
    artifact_id = f"change-{suffix}"
    artifact = {
        "artifact_type": "StructuredChange",
        "schema_version": "v1",
        "change_id": artifact_id,
        "project_id": project_id,
        "source_snapshot_id": f"before-{suffix}",
        "target_snapshot_id": f"after-{suffix}",
        "stable_key": "api:GET:/expenses",
        "fact_type": "api_endpoint",
        "domain": "api",
        "change_type": "modified",
        "before": {
            "fact_ref": f"fact-before-{suffix}",
            "values": {"response": "v1"},
            "source_refs": ["document-node-1"],
        },
        "after": {
            "fact_ref": f"fact-after-{suffix}",
            "values": {"response": "v2"},
            "source_refs": ["document-node-1"],
        },
        "summary": "Expense response contract changed",
        "source_refs": ["document-node-1"],
        "confidence": "high",
        "review_status": "accepted",
    }

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES (%s, %s)",
                (project_id, "Integration test"),
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
                ) VALUES (%s, %s, %s, 'ingesting')
                """,
                (case_id, project_id, revision_id),
            )

        repository = ArtifactRepository(connection, ContractCatalog.load(ROOT / "contracts"))
        digest = repository.store(
            artifact_id=artifact_id,
            project_id=project_id,
            analysis_case_id=case_id,
            artifact=artifact,
        )

        assert len(digest) == 64
        assert (
            repository.store(
                artifact_id=artifact_id,
                project_id=project_id,
                analysis_case_id=case_id,
                artifact=artifact,
            )
            == digest
        )
        assert repository.get(artifact_id) == artifact
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT artifact_digest_drift_probe")
            cursor.execute(
                """
                UPDATE artifact_records
                SET payload = jsonb_set(payload, '{summary}', '"Tampered summary"'::jsonb)
                WHERE artifact_id = %s
                """,
                (artifact_id,),
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            repository.get(artifact_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT artifact_digest_drift_probe")
            cursor.execute("RELEASE SAVEPOINT artifact_digest_drift_probe")
        conflicting = copy.deepcopy(artifact)
        conflicting["summary"] = "Different immutable content"
        with pytest.raises(PersistenceConflictError):
            repository.store(
                artifact_id=artifact_id,
                project_id=project_id,
                analysis_case_id=case_id,
                artifact=conflicting,
            )
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_business_data_template_is_immutable_and_reusable_from_postgres() -> None:
    suffix = uuid4().hex
    project_id = f"template-project-{suffix}"
    artifact = copy.deepcopy(build_visiondemo_cross_screen_template())
    artifact["template_id"] = f"business-data-template-{suffix}"
    artifact["project_id"] = project_id

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES (%s, %s)",
                (project_id, "Business data template test"),
            )
        repository = ArtifactRepository(
            connection,
            ContractCatalog.load(ROOT / "contracts"),
        )

        repository.store(
            artifact_id=str(artifact["template_id"]),
            project_id=project_id,
            analysis_case_id=None,
            artifact=artifact,
        )

        assert repository.get(str(artifact["template_id"])) == artifact
        conflicting = copy.deepcopy(artifact)
        conflicting["template_version"] = "2.0.0"
        with pytest.raises(PersistenceConflictError):
            repository.store(
                artifact_id=str(artifact["template_id"]),
                project_id=project_id,
                analysis_case_id=None,
                artifact=conflicting,
            )
        connection.rollback()
