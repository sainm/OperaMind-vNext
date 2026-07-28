"""Validated immutable Profile versions and project activation audit."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from psycopg import Connection

from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.profiles import ProfileCatalog


@dataclass(frozen=True, slots=True)
class ActiveProfileBinding:
    """Current project binding plus its validated Profile payload."""

    project_id: str
    binding_key: str
    profile_version_id: str
    activated_by: str
    activated_at: datetime
    profile: dict[str, Any]


class ProfileRepository:
    """Store immutable Profile versions and append activation events transactionally."""

    def __init__(self, connection: Connection[Any], catalog: ProfileCatalog) -> None:
        self._connection = connection
        self._catalog = catalog

    def store_version(self, *, profile_version_id: str, profile: dict[str, Any]) -> str:
        """Validate and idempotently store one immutable Profile version."""

        if not profile_version_id.strip():
            raise ValueError("profile_version_id must not be blank")
        self._catalog.validate_profile(profile)
        canonical_payload = _canonical_json(profile)
        digest = hashlib.sha256(canonical_payload.encode()).hexdigest()
        expected = (
            str(profile["profile_type"]),
            str(profile["profile_id"]),
            str(profile["profile_version"]),
            profile,
            digest,
        )
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO profile_versions (
                    profile_version_id,
                    profile_type,
                    profile_id,
                    semantic_version,
                    payload,
                    payload_digest
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    profile_version_id,
                    expected[0],
                    expected[1],
                    expected[2],
                    canonical_payload,
                    digest,
                ),
            )
            cursor.execute(
                """
                SELECT profile_type, profile_id, semantic_version, payload, payload_digest
                FROM profile_versions
                WHERE profile_version_id = %s
                """,
                (profile_version_id,),
            )
            row = cursor.fetchone()
            if row is None or tuple(row) != expected:
                raise PersistenceConflictError(
                    f"Profile version identity has different content: {profile_version_id}"
                )
        return digest

    def get_version(self, profile_version_id: str) -> dict[str, Any] | None:
        """Load and revalidate one Profile payload."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT profile_type, profile_id, semantic_version, payload, payload_digest
                FROM profile_versions
                WHERE profile_version_id = %s
                """,
                (profile_version_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._validated_version(profile_version_id, tuple(row))

    def activate(
        self,
        *,
        activation_event_id: str,
        project_id: str,
        binding_key: str,
        profile_version_id: str,
        activated_by: str,
        reason: str,
    ) -> bool:
        """Activate a Profile and append one audit event; exact event replay is a no-op."""

        values = (
            activation_event_id,
            project_id,
            binding_key,
            profile_version_id,
            activated_by,
            reason,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Profile activation fields must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, binding_key, activated_profile_version_id,
                       activated_by, reason
                FROM profile_activation_events
                WHERE activation_event_id = %s
                """,
                (activation_event_id,),
            )
            existing_event = cursor.fetchone()
            expected_event = (
                project_id,
                binding_key,
                profile_version_id,
                activated_by,
                reason,
            )
            if existing_event is not None:
                if tuple(existing_event) != expected_event:
                    raise PersistenceConflictError(
                        f"Activation event ID has different content: {activation_event_id}"
                    )
                return False

            cursor.execute(
                """
                SELECT profile_type FROM profile_versions
                WHERE profile_version_id = %s
                FOR SHARE
                """,
                (profile_version_id,),
            )
            target_version = cursor.fetchone()
            if target_version is None:
                raise ValueError("Profile version does not exist")
            target_profile_type = str(target_version[0])

            cursor.execute(
                """
                SELECT active_profile_version_id
                FROM project_profile_bindings
                WHERE project_id = %s AND binding_key = %s
                FOR UPDATE
                """,
                (project_id, binding_key),
            )
            binding = cursor.fetchone()
            previous_profile_version_id = str(binding[0]) if binding is not None else None
            if previous_profile_version_id is not None:
                cursor.execute(
                    """
                    SELECT profile_type FROM profile_versions
                    WHERE profile_version_id = %s
                    FOR SHARE
                    """,
                    (previous_profile_version_id,),
                )
                previous_version = cursor.fetchone()
                if previous_version is None or str(previous_version[0]) != target_profile_type:
                    raise ValueError("Profile binding cannot change Profile type")
            cursor.execute(
                """
                INSERT INTO profile_activation_events (
                    activation_event_id,
                    project_id,
                    binding_key,
                    previous_profile_version_id,
                    activated_profile_version_id,
                    activated_by,
                    reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    activation_event_id,
                    project_id,
                    binding_key,
                    previous_profile_version_id,
                    profile_version_id,
                    activated_by,
                    reason,
                ),
            )
            cursor.execute(
                """
                INSERT INTO project_profile_bindings (
                    project_id,
                    binding_key,
                    active_profile_version_id,
                    activated_by
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (project_id, binding_key) DO UPDATE SET
                    active_profile_version_id = EXCLUDED.active_profile_version_id,
                    activated_by = EXCLUDED.activated_by,
                    activated_at = now()
                """,
                (project_id, binding_key, profile_version_id, activated_by),
            )
            from operamind.infrastructure.postgres.profile_drift_repository import (
                ProfileDriftRepository,
            )

            ProfileDriftRepository(self._connection).detect_activation(
                activation_event_id=activation_event_id,
                cursor=cursor,
            )
        return True

    def get_active(self, *, project_id: str, binding_key: str) -> ActiveProfileBinding | None:
        """Return the current binding and revalidate its payload."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT b.active_profile_version_id, b.activated_by, b.activated_at,
                       v.profile_type, v.profile_id, v.semantic_version,
                       v.payload, v.payload_digest
                FROM project_profile_bindings AS b
                JOIN profile_versions AS v
                  ON v.profile_version_id = b.active_profile_version_id
                WHERE b.project_id = %s AND b.binding_key = %s
                """,
                (project_id, binding_key),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        profile_version_id = str(row[0])
        profile = self._validated_version(profile_version_id, tuple(row[3:]))
        return ActiveProfileBinding(
            project_id=project_id,
            binding_key=binding_key,
            profile_version_id=profile_version_id,
            activated_by=str(row[1]),
            activated_at=cast(datetime, row[2]),
            profile=profile,
        )

    def list_active_by_type(
        self, *, project_id: str, profile_type: str
    ) -> tuple[ActiveProfileBinding, ...]:
        """Return every active project binding of one validated Profile type."""

        if not project_id.strip() or not profile_type.strip():
            raise ValueError("Project ID and Profile type must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT b.binding_key, b.active_profile_version_id,
                       b.activated_by, b.activated_at,
                       v.profile_type, v.profile_id, v.semantic_version,
                       v.payload, v.payload_digest
                FROM project_profile_bindings AS b
                JOIN profile_versions AS v
                  ON v.profile_version_id = b.active_profile_version_id
                WHERE b.project_id = %s AND v.profile_type = %s
                ORDER BY b.binding_key
                """,
                (project_id, profile_type),
            )
            rows = cursor.fetchall()
        return tuple(
            ActiveProfileBinding(
                project_id=project_id,
                binding_key=str(row[0]),
                profile_version_id=str(row[1]),
                activated_by=str(row[2]),
                activated_at=cast(datetime, row[3]),
                profile=self._validated_version(str(row[1]), tuple(row[4:])),
            )
            for row in rows
        )

    def _validated_version(
        self,
        profile_version_id: str,
        row: tuple[object, ...],
    ) -> dict[str, Any]:
        profile = cast(dict[str, Any], row[3])
        self._catalog.validate_profile(profile)
        return validate_profile_payload_identity(
            profile_version_id=profile_version_id,
            row=row,
        )


def validate_profile_payload_identity(
    *,
    profile_version_id: str,
    row: tuple[object, ...],
    expected_profile_type: str | None = None,
) -> dict[str, Any]:
    """Revalidate one normalized Profile row at non-catalog SQL read boundaries."""

    profile = cast(dict[str, Any], row[3])
    digest = hashlib.sha256(_canonical_json(profile).encode()).hexdigest()
    expected = (
        str(profile.get("profile_type")),
        str(profile.get("profile_id")),
        str(profile.get("profile_version")),
        profile,
        digest,
    )
    if row != expected:
        raise PersistenceConflictError(
            f"Profile version normalized identity differs: {profile_version_id}"
        )
    if expected_profile_type is not None and profile.get("profile_type") != expected_profile_type:
        raise PersistenceConflictError(f"Profile version has unexpected type: {profile_version_id}")
    return profile


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
