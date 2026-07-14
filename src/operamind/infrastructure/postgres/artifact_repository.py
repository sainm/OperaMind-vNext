"""Validated immutable Artifact persistence in PostgreSQL."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog


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
        """Validate and insert an immutable Artifact, returning its SHA-256 digest."""

        if not artifact_id.strip():
            raise ValueError("artifact_id must not be blank")
        if not project_id.strip():
            raise ValueError("project_id must not be blank")
        artifact_project_id = artifact.get("project_id")
        if artifact_project_id is not None and artifact_project_id != project_id:
            raise ValueError("Artifact project_id does not match the repository scope")

        self._catalog.validate_artifact(artifact)
        canonical_payload = json.dumps(
            artifact,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload_digest = hashlib.sha256(canonical_payload.encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
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
        return payload_digest

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        """Load an Artifact by ID and validate it again at the persistence boundary."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload FROM artifact_records WHERE artifact_id = %s",
                (artifact_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        payload = cast(dict[str, Any], row[0])
        self._catalog.validate_artifact(payload)
        return payload
