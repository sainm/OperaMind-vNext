"""Idempotent P0 Project, Repository, Revision, and Analysis Case registration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class AnalysisRegistration:
    project_id: str
    repository_id: str
    repository_revision_id: str
    analysis_case_id: str
    status: str
    created: bool


class AnalysisRepository:
    """Create the immutable P0 identity chain without resetting existing state."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def start(
        self,
        *,
        project_id: str,
        project_name: str,
        repository_id: str,
        remote_url: str,
        workspace_root: str,
        repository_revision_id: str,
        commit_sha: str,
        analysis_case_id: str,
    ) -> AnalysisRegistration:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO projects (project_id, name)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (project_id, project_name),
            )
            self._require_row(
                cursor,
                "SELECT name FROM projects WHERE project_id = %s",
                (project_id,),
                (project_name,),
                "Project identity differs",
            )
            cursor.execute(
                """
                INSERT INTO repositories (
                    repository_id, project_id, remote_url, workspace_root
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (repository_id, project_id, remote_url, workspace_root),
            )
            self._require_row(
                cursor,
                """
                SELECT project_id, remote_url, workspace_root
                FROM repositories
                WHERE repository_id = %s
                """,
                (repository_id,),
                (project_id, remote_url, workspace_root),
                "Repository identity differs",
            )
            cursor.execute(
                """
                INSERT INTO repository_revisions (
                    repository_revision_id, repository_id, commit_sha
                ) VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (repository_revision_id, repository_id, commit_sha),
            )
            self._require_row(
                cursor,
                """
                SELECT repository_id, commit_sha
                FROM repository_revisions
                WHERE repository_revision_id = %s
                """,
                (repository_revision_id,),
                (repository_id, commit_sha),
                "Repository Revision identity differs",
            )
            cursor.execute(
                """
                INSERT INTO analysis_cases (
                    analysis_case_id, project_id, repository_revision_id, status
                ) VALUES (%s, %s, %s, 'ingesting')
                ON CONFLICT DO NOTHING
                """,
                (analysis_case_id, project_id, repository_revision_id),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT project_id, repository_revision_id, status
                FROM analysis_cases
                WHERE analysis_case_id = %s
                """,
                (analysis_case_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise PersistenceConflictError("Analysis Case was not persisted")
            if (str(row[0]), str(row[1])) != (project_id, repository_revision_id):
                raise PersistenceConflictError("Analysis Case identity differs")
            return AnalysisRegistration(
                project_id=project_id,
                repository_id=repository_id,
                repository_revision_id=repository_revision_id,
                analysis_case_id=analysis_case_id,
                status=str(row[2]),
                created=created,
            )

    @staticmethod
    def _require_row(
        cursor: Any,
        query: str,
        parameters: tuple[str, ...],
        expected: tuple[str, ...],
        message: str,
    ) -> None:
        cursor.execute(query, parameters)
        row = cursor.fetchone()
        if row is None or tuple(str(value) for value in row) != expected:
            raise PersistenceConflictError(message)
