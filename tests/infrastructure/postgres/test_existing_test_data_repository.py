from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import MethodType

import pytest

from operamind.application.existing_test_data import (
    ExistingTestDataRegistration,
    ProjectDataIdentityProfile,
)
from operamind.infrastructure.postgres.existing_test_data_repository import (
    ExistingTestDataRepository,
)


class _Context:
    def __init__(self, value: object) -> None:
        self.value = value

    def __enter__(self) -> object:
        return self.value

    def __exit__(self, *_args: object) -> None:
        return None


class _Cursor:
    def __init__(self, profile_row: tuple[object, ...]) -> None:
        self.profile_row = profile_row
        self.rowcount = 1
        self.executions: list[str] = []

    def execute(self, query: str, _parameters: object = None) -> None:
        self.executions.append(query)

    def fetchone(self) -> tuple[object, ...]:
        return self.profile_row


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def transaction(self) -> _Context:
        return _Context(object())

    def cursor(self) -> _Context:
        return _Context(self._cursor)


class _ReadCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, object]] = []

    def execute(self, query: str, parameters: object = None) -> None:
        self.executions.append((query, parameters))

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


def test_confirmation_rechecks_provider_revision_under_the_repository_lock() -> None:
    profile = _profile(revision=1)
    candidate = _registration(profile, status="candidate")
    confirmed = replace(
        candidate,
        status="confirmed",
        plan_data_definition={"data_set": {"test_data_id": "data-1"}},
        confirmed_by="qa-user",
        confirmed_at=datetime(2026, 8, 5, 11, tzinfo=UTC),
    )
    changed_profile = _profile(revision=2)
    cursor = _Cursor(
        (
            changed_profile.provider_type,
            list(changed_profile.lookup_steps),
            list(changed_profile.cleanup_steps),
            dict(changed_profile.identity_definition),
            list(changed_profile.business_summary_fields),
            changed_profile.revision,
        )
    )
    repository = ExistingTestDataRepository(_Connection(cursor))  # type: ignore[arg-type]

    def get_candidate(
        _self: object,
        _cursor: object,
        _registration_id: str,
        *,
        for_update: bool,
    ) -> ExistingTestDataRegistration:
        assert for_update is True
        return candidate

    repository._get = MethodType(get_candidate, repository)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="設定が候補生成後に変更"):
        repository.confirm(confirmed)

    assert len(cursor.executions) == 1
    assert "FOR UPDATE" in cursor.executions[0]


def test_fixed_binding_views_scope_frozen_runs_to_the_selected_change_request() -> None:
    cursor = _ReadCursor()
    repository = ExistingTestDataRepository(_Connection(cursor))  # type: ignore[arg-type]

    result = repository.fixed_binding_views(
        "project-1",
        change_request_id="change-1",
    )

    assert result == ()
    binding_query, parameters = cursor.executions[0]
    assert "JOIN change_orchestrations AS orchestration" in binding_query
    assert "orchestration.change_request_id = %s" in binding_query
    assert parameters == ("project-1", "change-1", "change-1")


def _profile(*, revision: int) -> ProjectDataIdentityProfile:
    lookup = {
        "step_id": "lookup-existing",
        "sequence": 1,
        "channel": "sql",
        "business_action": "業務番号で既存データを確認する",
        "target": "read-existing",
        "inputs": {"business_no": "{{business_unique_value}}"},
        "depends_on": [],
        "output_bindings": [],
        "postconditions": [
            {
                "assertion_id": "one-record",
                "observe_via": "database",
                "subject": "row_count",
                "operator": "count_equals",
                "expected": 1,
            }
        ],
    }
    return ProjectDataIdentityProfile(
        project_id="project-1",
        provider_ref="database.v1",
        provider_type="database",
        lookup_steps=(lookup,),
        cleanup_steps=(),
        identity_definition={"source_step_id": "lookup-existing"},
        business_summary_fields=("business_no",),
        revision=revision,
    )


def _registration(
    profile: ProjectDataIdentityProfile,
    *,
    status: str,
) -> ExistingTestDataRegistration:
    return ExistingTestDataRegistration(
        registration_id="registration-1",
        project_id="project-1",
        change_request_id="change-1",
        data_name="既存データ",
        business_unique_value="BUSINESS-1",
        test_case_ref="case-1",
        retain_after_test=True,
        status=status,
        provider_ref=profile.provider_ref,
        provider_type=profile.provider_type,
        match_count=1,
        business_summary={"business_no": "BUSINESS-1"},
        identity_candidate={"match_count": 1},
        evidence_refs=("evidence://existing-1",),
        blocking_reasons=(),
        requested_by="qa-user",
        requested_at=datetime(2026, 8, 5, 10, tzinfo=UTC),
        provider_revision=profile.revision,
        provider_digest=profile.content_digest,
    )
