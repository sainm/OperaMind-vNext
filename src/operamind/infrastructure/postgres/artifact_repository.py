"""Validated immutable Artifact persistence in PostgreSQL."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.errors import PersistenceConflictError


class ArtifactRepository:
    """Round-trip validated Artifact exchange records without storing source text."""

    def __init__(self, connection: Connection[Any], catalog: ContractCatalog) -> None:
        self._connection = connection
        self._catalog = catalog

    def store(
        self,
        *,
        artifact_id: str,
        project_id: str,
        analysis_case_id: str | None,
        artifact: dict[str, Any],
    ) -> str:
        """Validate and idempotently store an immutable Artifact."""

        if not artifact_id.strip():
            raise ValueError("artifact_id must not be blank")
        if not project_id.strip():
            raise ValueError("project_id must not be blank")
        artifact_project_id = artifact.get("project_id")
        if artifact_project_id is not None and artifact_project_id != project_id:
            raise ValueError("Artifact project_id does not match the repository scope")
        artifact_case_id = artifact.get("analysis_case_id")
        if artifact_case_id is not None and artifact_case_id != analysis_case_id:
            raise ValueError("Artifact analysis_case_id does not match the repository scope")

        self._catalog.validate_artifact(artifact)
        canonical_payload = json.dumps(
            artifact,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload_digest = hashlib.sha256(canonical_payload.encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            if analysis_case_id is not None:
                cursor.execute(
                    "SELECT project_id FROM analysis_cases WHERE analysis_case_id = %s",
                    (analysis_case_id,),
                )
                case = cursor.fetchone()
                if case is None:
                    raise ValueError(f"Analysis case does not exist: {analysis_case_id}")
                if str(case[0]) != project_id:
                    raise ValueError("Analysis case does not belong to the Artifact project")
            cursor.execute(
                """
                INSERT INTO artifact_records (
                    artifact_id,
                    artifact_type,
                    schema_version,
                    project_id,
                    analysis_case_id,
                    payload,
                    payload_digest
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    artifact_id,
                    artifact["artifact_type"],
                    artifact["schema_version"],
                    project_id,
                    analysis_case_id,
                    canonical_payload,
                    payload_digest,
                ),
            )
            cursor.execute(
                """
                SELECT artifact_type, schema_version, project_id,
                       analysis_case_id, payload, payload_digest
                FROM artifact_records
                WHERE artifact_id = %s
                """,
                (artifact_id,),
            )
            stored = cursor.fetchone()
            expected = (
                artifact["artifact_type"],
                artifact["schema_version"],
                project_id,
                analysis_case_id,
                artifact,
                payload_digest,
            )
            if stored is None or tuple(stored) != expected:
                raise PersistenceConflictError(
                    f"Artifact identity has different content: {artifact_id}"
                )
        return payload_digest

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        """Load an Artifact and revalidate its full immutable database envelope."""

        return self._get(artifact_id, for_share=False)

    def get_for_share(self, artifact_id: str) -> dict[str, Any] | None:
        """Load and revalidate an Artifact while locking it in the caller transaction."""

        return self._get(artifact_id, for_share=True)

    def _get(self, artifact_id: str, *, for_share: bool) -> dict[str, Any] | None:
        if not artifact_id.strip():
            raise ValueError("artifact_id must not be blank")

        with self._connection.cursor() as cursor:
            query = (
                """
                SELECT artifact_type, schema_version, project_id,
                       analysis_case_id, payload, payload_digest
                FROM artifact_records
                WHERE artifact_id = %s
                FOR SHARE
                """
                if for_share
                else """
                SELECT artifact_type, schema_version, project_id,
                       analysis_case_id, payload, payload_digest
                FROM artifact_records
                WHERE artifact_id = %s
                """
            )
            cursor.execute(query, (artifact_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        payload = cast(dict[str, Any], row[4])
        self._catalog.validate_artifact(payload)
        canonical_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload_digest = hashlib.sha256(canonical_payload.encode()).hexdigest()
        envelope_matches = (
            row[0] == payload["artifact_type"]
            and row[1] == payload["schema_version"]
            and row[4] == payload
            and row[5] == payload_digest
        )
        payload_project_id = payload.get("project_id")
        if payload_project_id is not None:
            envelope_matches = envelope_matches and row[2] == payload_project_id
        payload_case_id = payload.get("analysis_case_id")
        if payload_case_id is not None:
            envelope_matches = envelope_matches and row[3] == payload_case_id
        if not envelope_matches:
            raise PersistenceConflictError(f"Artifact normalized identity differs: {artifact_id}")
        return payload
