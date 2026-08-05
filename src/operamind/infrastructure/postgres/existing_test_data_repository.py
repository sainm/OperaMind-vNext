"""PostgreSQL persistence for reviewed identity profiles and existing-data adoption."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from psycopg import Connection

from operamind.application.data_identity import is_sensitive_data_identity_name
from operamind.application.existing_test_data import (
    ExistingTestDataRegistration,
    ProjectDataIdentityProfile,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError


class ExistingTestDataRepository:
    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def upsert_profile(
        self,
        profile: ProjectDataIdentityProfile,
        *,
        actor: str,
    ) -> None:
        _validate_profile(profile, actor=actor)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            _upsert_profile(cursor, profile, actor=actor)

    def replace_profiles(
        self,
        *,
        project_id: str,
        profiles: tuple[ProjectDataIdentityProfile, ...],
        actor: str,
    ) -> None:
        if not project_id.strip() or not actor.strip():
            raise ValueError("DataIdentityProvider Project/reviewer must not be blank")
        if any(profile.project_id != project_id for profile in profiles):
            raise ValueError("DataIdentityProvider profile Project scope differs")
        refs = [profile.provider_ref for profile in profiles]
        if len(refs) != len(set(refs)):
            raise ValueError("DataIdentityProvider refs must be unique")
        for profile in profiles:
            _validate_profile(profile, actor=actor)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_data_identity_profiles
                SET active = false, revision = revision + 1,
                    reviewed_by = %s, reviewed_at = now()
                WHERE project_id = %s AND active
                """,
                (actor, project_id),
            )
            for profile in profiles:
                _upsert_profile(cursor, profile, actor=actor)

    def profiles(self, project_id: str) -> tuple[ProjectDataIdentityProfile, ...]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT provider_ref, provider_type, lookup_steps, cleanup_steps,
                       identity_definition, business_summary_fields, revision
                FROM project_data_identity_profiles
                WHERE project_id = %s AND active
                ORDER BY provider_ref
                """,
                (project_id,),
            )
            rows = cursor.fetchall()
        return tuple(
            ProjectDataIdentityProfile(
                project_id=project_id,
                provider_ref=str(row[0]),
                provider_type=str(row[1]),
                lookup_steps=tuple(cast(list[Mapping[str, object]], row[2])),
                cleanup_steps=tuple(cast(list[Mapping[str, object]], row[3])),
                identity_definition=cast(Mapping[str, object], row[4]),
                business_summary_fields=tuple(str(value) for value in cast(list[object], row[5])),
                revision=int(row[6]),
            )
            for row in rows
        )

    def save(self, value: ExistingTestDataRegistration) -> None:
        if _contains_secret_shape(
            {
                "business_summary": value.business_summary,
                "identity_candidate": value.identity_candidate,
                "plan_data_definition": value.plan_data_definition,
            }
        ):
            raise ValueError("Existing test data registration must not persist Secret fields")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO existing_test_data_registrations (
                    registration_id, project_id, change_request_id,
                    data_name, business_unique_value,
                    test_case_ref, retain_after_test, status, provider_ref,
                    provider_type, match_count, business_summary, identity_candidate,
                    evidence_refs, plan_data_definition, blocking_reasons,
                    requested_by, requested_at, confirmed_by, confirmed_at,
                    provider_revision, provider_digest
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (registration_id) DO NOTHING
                """,
                _write_values(value),
            )
            if cursor.rowcount == 0:
                existing = self._get(cursor, value.registration_id, for_update=True)
                if existing != value:
                    raise PersistenceConflictError(
                        f"Existing test data registration differs: {value.registration_id}"
                    )

    def confirm(self, value: ExistingTestDataRegistration) -> None:
        if value.status != "confirmed":
            raise ValueError("Existing test data confirmation requires confirmed state")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            current = self._get(cursor, value.registration_id, for_update=True)
            if current is None:
                raise ValueError("Existing test data registration does not exist")
            if current.status == "confirmed":
                if current != value:
                    raise PersistenceConflictError(
                        f"Existing test data confirmation differs: {value.registration_id}"
                    )
                return
            if current.status != "candidate":
                raise ValueError("Blocked existing test data cannot be confirmed")
            if value.provider_ref is None:
                raise ValueError("Existing test data confirmation has no Provider")
            cursor.execute(
                """
                SELECT provider_type, lookup_steps, cleanup_steps,
                       identity_definition, business_summary_fields, revision
                FROM project_data_identity_profiles
                WHERE project_id = %s AND provider_ref = %s AND active
                FOR UPDATE
                """,
                (value.project_id, value.provider_ref),
            )
            profile_row = cursor.fetchone()
            if profile_row is None:
                raise ValueError("確認済み DataIdentityProvider が存在しません")
            profile = ProjectDataIdentityProfile(
                project_id=value.project_id,
                provider_ref=value.provider_ref,
                provider_type=str(profile_row[0]),
                lookup_steps=tuple(cast(list[Mapping[str, object]], profile_row[1])),
                cleanup_steps=tuple(cast(list[Mapping[str, object]], profile_row[2])),
                identity_definition=cast(Mapping[str, object], profile_row[3]),
                business_summary_fields=tuple(
                    str(item) for item in cast(list[object], profile_row[4])
                ),
                revision=int(profile_row[5]),
            )
            if (
                value.provider_type != profile.provider_type
                or value.provider_revision != profile.revision
                or value.provider_digest != profile.content_digest
            ):
                raise ValueError(
                    "DataIdentityProvider 設定が候補生成後に変更されました。再登録してください。"
                )
            cursor.execute(
                """
                UPDATE existing_test_data_registrations
                SET status = 'confirmed', plan_data_definition = %s::jsonb,
                    confirmed_by = %s, confirmed_at = %s
                WHERE registration_id = %s AND project_id = %s
                  AND status = 'candidate' AND match_count = 1
                """,
                (
                    _json(value.plan_data_definition),
                    value.confirmed_by,
                    value.confirmed_at,
                    value.registration_id,
                    value.project_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("Existing test data confirmation lost its lock")

    def get(self, registration_id: str) -> ExistingTestDataRegistration | None:
        with self._connection.cursor() as cursor:
            return self._get(cursor, registration_id, for_update=False)

    def list_for_project(
        self,
        project_id: str,
        *,
        change_request_id: str | None = None,
    ) -> tuple[ExistingTestDataRegistration, ...]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT registration_id
                FROM existing_test_data_registrations
                WHERE project_id = %s
                  AND (%s::text IS NULL OR change_request_id = %s)
                ORDER BY requested_at DESC, registration_id DESC
                """,
                (project_id, change_request_id, change_request_id),
            )
            ids = [str(row[0]) for row in cursor.fetchall()]
            return tuple(
                value
                for registration_id in ids
                if (value := self._get(cursor, registration_id, for_update=False)) is not None
            )

    def fixed_binding_views(self, project_id: str) -> tuple[dict[str, object], ...]:
        """Return an internal read model that the Web layer can safely redact."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT binding.binding_id, binding.run_id, binding.test_data_id,
                       binding.binding_mode, binding.identity_provider_type,
                       binding.business_unique_keys, binding.screen_identity_values,
                       binding.identity_digest, binding.frozen_at,
                       run.test_data_token, run.status,
                       registration.data_name, registration.test_case_ref,
                       registration.retain_after_test
                FROM test_data_identity_bindings AS binding
                JOIN test_data_execution_runs AS run
                  ON run.run_id = binding.run_id
                 AND run.project_id = binding.project_id
                LEFT JOIN existing_test_data_registrations AS registration
                  ON registration.project_id = binding.project_id
                 AND registration.plan_data_definition -> 'data_set'
                     ->> 'test_data_id' = binding.test_data_id
                WHERE binding.project_id = %s
                ORDER BY binding.frozen_at DESC, binding.test_data_id
                """,
                (project_id,),
            )
            bindings = cursor.fetchall()
            cursor.execute(
                """
                SELECT step.run_id, step.flow_id, step.phase, step.step_id,
                       step.status, ref.value
                FROM test_data_step_results AS step
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    step.test_data_binding_refs
                ) AS ref(value)
                WHERE step.project_id = %s
                ORDER BY step.run_id, step.flow_id, step.phase, step.sequence
                """,
                (project_id,),
            )
            usage_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT run_id, phase, step_id, evidence_type, evidence_ref,
                       test_data_binding_ref
                FROM test_data_execution_evidence
                WHERE project_id = %s AND test_data_binding_ref IS NOT NULL
                ORDER BY run_id, evidence_id
                """,
                (project_id,),
            )
            evidence_rows = cursor.fetchall()
        usages: dict[str, list[dict[str, object]]] = {}
        for row in usage_rows:
            usages.setdefault(str(row[5]), []).append(
                {
                    "run_id": str(row[0]),
                    "flow_id": str(row[1]),
                    "phase": str(row[2]),
                    "step_id": str(row[3]),
                    "status": str(row[4]),
                }
            )
        evidence: dict[str, list[dict[str, object]]] = {}
        for row in evidence_rows:
            evidence.setdefault(str(row[5]), []).append(
                {
                    "run_id": str(row[0]),
                    "phase": str(row[1]),
                    "step_id": str(row[2]),
                    "evidence_type": str(row[3]),
                    "evidence_ref": str(row[4]),
                }
            )
        return tuple(
            {
                "binding_id": str(row[0]),
                "run_id": str(row[1]),
                "test_data_id": str(row[2]),
                "binding_mode": str(row[3]),
                "provider_type": str(row[4]) if row[4] is not None else None,
                "business_unique_keys": row[5],
                "screen_identity_values": row[6],
                "identity_digest": str(row[7]) if row[7] is not None else None,
                "frozen_at": cast(datetime, row[8]).astimezone(UTC),
                "test_data_token": str(row[9]) if row[9] is not None else None,
                "run_status": str(row[10]),
                "data_name": str(row[11]) if row[11] is not None else None,
                "test_case_ref": str(row[12]) if row[12] is not None else None,
                "retain_after_test": bool(row[13]) if row[13] is not None else None,
                "usages": usages.get(str(row[0]), []),
                "evidence": evidence.get(str(row[0]), []),
            }
            for row in bindings
        )

    @staticmethod
    def _get(
        cursor: Any,
        registration_id: str,
        *,
        for_update: bool,
    ) -> ExistingTestDataRegistration | None:
        cursor.execute(
            """
            SELECT project_id, change_request_id, data_name, business_unique_value,
                   test_case_ref,
                   retain_after_test, status, provider_ref, provider_type,
                   match_count, business_summary, identity_candidate,
                   evidence_refs, blocking_reasons, requested_by, requested_at,
                   plan_data_definition, confirmed_by, confirmed_at,
                   provider_revision, provider_digest
            FROM existing_test_data_registrations
            WHERE registration_id = %s
            """
            + (" FOR UPDATE" if for_update else ""),
            (registration_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ExistingTestDataRegistration(
            registration_id=registration_id,
            project_id=str(row[0]),
            change_request_id=str(row[1]) if row[1] is not None else None,
            data_name=str(row[2]),
            business_unique_value=str(row[3]),
            test_case_ref=str(row[4]),
            retain_after_test=bool(row[5]),
            status=str(row[6]),
            provider_ref=str(row[7]) if row[7] is not None else None,
            provider_type=str(row[8]) if row[8] is not None else None,
            match_count=int(row[9]) if row[9] is not None else None,
            business_summary=cast(Mapping[str, object] | None, row[10]),
            identity_candidate=cast(Mapping[str, object] | None, row[11]),
            evidence_refs=tuple(str(value) for value in cast(list[object], row[12])),
            blocking_reasons=tuple(str(value) for value in cast(list[object], row[13])),
            requested_by=str(row[14]),
            requested_at=cast(datetime, row[15]).astimezone(UTC),
            plan_data_definition=cast(Mapping[str, object] | None, row[16]),
            confirmed_by=str(row[17]) if row[17] is not None else None,
            confirmed_at=(
                cast(datetime, row[18]).astimezone(UTC) if row[18] is not None else None
            ),
            provider_revision=int(row[19]) if row[19] is not None else None,
            provider_digest=str(row[20]) if row[20] is not None else None,
        )


def _write_values(value: ExistingTestDataRegistration) -> tuple[object, ...]:
    return (
        value.registration_id,
        value.project_id,
        value.change_request_id,
        value.data_name,
        value.business_unique_value,
        value.test_case_ref,
        value.retain_after_test,
        value.status,
        value.provider_ref,
        value.provider_type,
        value.match_count,
        _optional_json(value.business_summary),
        _optional_json(value.identity_candidate),
        _json(value.evidence_refs),
        _optional_json(value.plan_data_definition),
        _json(value.blocking_reasons),
        value.requested_by,
        value.requested_at,
        value.confirmed_by,
        value.confirmed_at,
        value.provider_revision,
        value.provider_digest,
    )


def _validate_profile(profile: ProjectDataIdentityProfile, *, actor: str) -> None:
    if not actor.strip():
        raise ValueError("DataIdentityProvider reviewer must not be blank")
    payloads: tuple[object, ...] = (
        profile.lookup_steps,
        profile.cleanup_steps,
        profile.identity_definition,
        profile.business_summary_fields,
    )
    if any(_contains_secret_shape(value) for value in payloads):
        raise ValueError("DataIdentityProvider profile must not persist Secret fields")


def _upsert_profile(
    cursor: Any,
    profile: ProjectDataIdentityProfile,
    *,
    actor: str,
) -> None:
    cursor.execute(
        """
        INSERT INTO project_data_identity_profiles (
            project_id, provider_ref, provider_type, lookup_steps,
            cleanup_steps, identity_definition, business_summary_fields,
            reviewed_by
        ) VALUES (
            %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s
        )
        ON CONFLICT (project_id, provider_ref) DO UPDATE
        SET provider_type = EXCLUDED.provider_type,
            lookup_steps = EXCLUDED.lookup_steps,
            cleanup_steps = EXCLUDED.cleanup_steps,
            identity_definition = EXCLUDED.identity_definition,
            business_summary_fields = EXCLUDED.business_summary_fields,
            revision = project_data_identity_profiles.revision + 1,
            active = true,
            reviewed_by = EXCLUDED.reviewed_by,
            reviewed_at = now()
        """,
        (
            profile.project_id,
            profile.provider_ref,
            profile.provider_type,
            _json(profile.lookup_steps),
            _json(profile.cleanup_steps),
            _json(profile.identity_definition),
            _json(profile.business_summary_fields),
            actor,
        ),
    )


def _contains_secret_shape(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            is_sensitive_data_identity_name(str(key)) or _contains_secret_shape(item)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_contains_secret_shape(item) for item in value)
    if isinstance(value, str):
        lowered = value.casefold()
        return "authorization: bearer " in lowered or "password=" in lowered
    return False


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _optional_json(value: object | None) -> str | None:
    return None if value is None else _json(value)
