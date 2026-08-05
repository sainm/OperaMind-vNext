from dataclasses import replace
from datetime import UTC, datetime

import pytest

from operamind.application.data_identity import DataIdentitySourceEvidence
from operamind.application.existing_test_data import (
    ExistingDataObservation,
    ExistingTestDataRegistrationInput,
    ExistingTestDataRegistrationService,
    ProjectDataIdentityProfile,
)
from operamind.infrastructure.test_data import default_data_identity_providers


class _ObservedResolver:
    def __init__(self, *, match_count: int = 1, business_no: str = "EXP-041") -> None:
        self.match_count = match_count
        self.business_no = business_no

    def resolve(self, *, registration, profile):  # type: ignore[no-untyped-def]
        del registration
        row = {"id": 41, "business_no": self.business_no, "status": "RETURNED"}
        observations = {
            "database": {"row_count": self.match_count, "rows": [row]},
            "response": {"body": {"count": self.match_count, "record": row}},
            "ui": {"match_count": self.match_count, "record": row},
        }
        evidence_by_type = {
            "database": ("sql",),
            "api": ("response",),
            "ui": ("step_log", "screenshot"),
            "hybrid": ("sql", "response", "step_log", "screenshot"),
        }
        return ExistingDataObservation(
            observations=observations,
            source_evidence=tuple(
                DataIdentitySourceEvidence(
                    evidence_type=value,
                    evidence_ref=f"evidence://{profile.provider_type}/{value}",
                    sanitized=True,
                )
                for value in evidence_by_type[profile.provider_type]
            ),
        )


@pytest.mark.parametrize("provider_type", ["database", "api", "ui", "hybrid"])
def test_existing_data_registration_resolves_all_real_provider_types(
    provider_type: str,
) -> None:
    service = ExistingTestDataRegistrationService(
        identity_providers=default_data_identity_providers(),
        observation_resolver=_ObservedResolver(),
    )

    result = service.register(_input(), profiles=[_profile(provider_type)])

    assert result.status == "candidate"
    assert result.provider_type == provider_type
    assert result.match_count == 1
    assert result.business_summary == {"business_no": "EXP-041"}
    assert result.identity_candidate is not None
    assert result.provider_revision == 1
    assert result.provider_digest == _profile(provider_type).content_digest
    assert result.identity_candidate["record_scope_locator"] == {
        "by": "css",
        "value": "[data-business-no='EXP-041']",
        "exact": True,
    }


@pytest.mark.parametrize("match_count", [0, 2])
def test_existing_data_registration_blocks_zero_or_multiple_matches(match_count: int) -> None:
    service = ExistingTestDataRegistrationService(
        identity_providers=default_data_identity_providers(),
        observation_resolver=_ObservedResolver(match_count=match_count),
    )

    result = service.register(_input(), profiles=[_profile("database")])

    assert result.status == "blocked"
    assert result.match_count == match_count
    assert result.evidence_refs == ("evidence://database/sql",)
    assert result.plan_data_definition is None
    assert any("一意" in value for value in result.blocking_reasons)


def test_existing_data_requires_human_confirmation_before_adopted_plan_definition() -> None:
    profile = _profile("database")
    service = ExistingTestDataRegistrationService(
        identity_providers=default_data_identity_providers(),
        observation_resolver=_ObservedResolver(),
    )
    candidate = service.register(_input(), profiles=[profile])

    assert candidate.plan_data_definition is None
    confirmed = service.confirm(
        candidate,
        profile=profile,
        actor="business-user",
        confirmed_at=datetime(2026, 8, 5, 10, 5, tzinfo=UTC),
    )

    assert confirmed.status == "confirmed"
    assert confirmed.plan_data_definition is not None
    data_set = confirmed.plan_data_definition["data_set"]
    assert data_set["identity_binding"]["binding_mode"] == "adopted"  # type: ignore[index]
    assert data_set["runtime_variable_writes"] == []  # type: ignore[index]
    assert data_set["cleanup_policy"] == "delete_after_run"  # type: ignore[index]
    flow = confirmed.plan_data_definition["generation_flow"]
    assert flow["steps"][0]["inputs"]["business_no"] == "EXP-041"  # type: ignore[index]
    assert flow["cleanup_steps"][0]["data_binding_ref"] == data_set[  # type: ignore[index]
        "test_data_id"
    ]
    assert flow["cleanup_steps"][0]["inputs"]["business_no"] == "EXP-041"  # type: ignore[index]


def test_existing_data_blocks_when_provider_returns_a_different_business_record() -> None:
    service = ExistingTestDataRegistrationService(
        identity_providers=default_data_identity_providers(),
        observation_resolver=_ObservedResolver(business_no="EXP-OTHER"),
    )

    result = service.register(_input(), profiles=[_profile("database")])

    assert result.status == "blocked"
    assert result.match_count == 1
    assert any("一致しません" in value for value in result.blocking_reasons)


def test_existing_data_confirmation_rejects_provider_configuration_drift() -> None:
    profile = _profile("database")
    service = ExistingTestDataRegistrationService(
        identity_providers=default_data_identity_providers(),
        observation_resolver=_ObservedResolver(),
    )
    candidate = service.register(_input(), profiles=[profile])

    with pytest.raises(ValueError, match="再登録"):
        service.confirm(
            candidate,
            profile=replace(profile, revision=2),
            actor="business-user",
            confirmed_at=datetime(2026, 8, 5, 10, 5, tzinfo=UTC),
        )


def test_existing_data_summary_can_include_an_approved_real_observation_field() -> None:
    profile = replace(
        _profile("database"),
        business_summary_fields=("business_no", "status"),
    )
    result = ExistingTestDataRegistrationService(
        identity_providers=default_data_identity_providers(),
        observation_resolver=_ObservedResolver(),
    ).register(_input(), profiles=[profile])

    assert result.business_summary == {
        "business_no": "EXP-041",
        "status": "RETURNED",
    }


def test_identity_profile_must_use_the_requested_business_unique_value() -> None:
    profile = _profile("database")

    with pytest.raises(ValueError, match="business_unique_value"):
        replace(
            profile,
            lookup_steps=tuple(
                {**dict(step), "inputs": {"business_no": "constant-value"}}
                for step in profile.lookup_steps
            ),
        )

    with pytest.raises(ValueError, match="cleanup must use"):
        replace(
            profile,
            cleanup_steps=tuple(
                {**dict(step), "inputs": {"business_no": "hard-coded-other-record"}}
                for step in profile.cleanup_steps
            ),
        )


def _input() -> ExistingTestDataRegistrationInput:
    return ExistingTestDataRegistrationInput(
        registration_id="registration-001",
        project_id="project-001",
        change_request_id="change-001",
        data_name="差戻し済み経費",
        business_unique_value="EXP-041",
        test_case_ref="case-search-returned",
        retain_after_test=False,
        requested_by="business-user",
        requested_at=datetime(2026, 8, 5, 10, tzinfo=UTC),
    )


def _profile(provider_type: str) -> ProjectDataIdentityProfile:
    source = {
        "database": "database",
        "api": "response",
        "ui": "ui",
        "hybrid": "database",
    }[provider_type]
    primary = {"name": "business_no", "source": source, "path": _path(source, "business_no")}
    business_source = "response" if provider_type == "hybrid" else source
    screen_source = "ui" if provider_type == "hybrid" else source
    identity = {
        "source_step_id": "lookup-existing",
        "primary_key": primary,
        "business_unique_keys": [
            {
                "name": "business_no",
                "source": business_source,
                "path": _path(business_source, "business_no"),
                "dom_observation": {
                    "kind": "attribute",
                    "attribute_name": "data-business-no",
                },
            }
        ],
        "screen_key": {
            "name": "business_no",
            "source": screen_source,
            "path": _path(screen_source, "business_no"),
            "dom_observation": {
                "kind": "attribute",
                "attribute_name": "data-business-no",
            },
            "locator_template": {
                "by": "css",
                "value": "[data-business-no='{{value}}']",
                "exact": True,
            },
        },
        "match_count": {
            "source": source,
            "path": {
                "database": "row_count",
                "response": "body.count",
                "ui": "match_count",
            }[source],
        },
    }
    channels = (
        ("sql", "http", "ui")
        if provider_type == "hybrid"
        else (
            {
                "database": "sql",
                "api": "http",
                "ui": "ui",
            }[provider_type],
        )
    )
    lookup_steps = tuple(
        _reviewed_step(
            step_id="lookup-existing" if index == 1 else f"observe-{channel}",
            sequence=index,
            channel=channel,
            expected=1,
        )
        for index, channel in enumerate(channels, start=1)
    )
    cleanup_steps = tuple(
        _reviewed_step(
            step_id=f"cleanup-{channel}",
            sequence=index,
            channel=channel,
            expected=0,
        )
        for index, channel in enumerate(channels, start=1)
    )
    identity["source_step_id"] = lookup_steps[-1]["step_id"]
    return ProjectDataIdentityProfile(
        project_id="project-001",
        provider_ref=f"{provider_type}.v1",
        provider_type=provider_type,
        lookup_steps=lookup_steps,
        cleanup_steps=cleanup_steps,
        identity_definition=identity,
        business_summary_fields=("business_no",),
    )


def _reviewed_step(
    *,
    step_id: str,
    sequence: int,
    channel: str,
    expected: int,
) -> dict[str, object]:
    observe_via = {"sql": "database", "http": "response", "ui": "ui"}[channel]
    step: dict[str, object] = {
        "step_id": step_id,
        "sequence": sequence,
        "channel": channel,
        "business_action": "既存データを確認する" if expected else "既存データを削除する",
        "inputs": {"business_no": "{{business_unique_value}}"},
        "depends_on": [],
        "output_bindings": [],
        "postconditions": [
            {
                "assertion_id": f"{step_id}-count",
                "observe_via": observe_via,
                "subject": (
                    "cleanup_record_scope_match_count"
                    if channel == "ui" and expected == 0
                    else "row_count"
                ),
                "operator": "count_equals",
                "expected": expected,
            }
        ],
    }
    if channel == "ui":
        step.update(
            {
                "screen_ref": "expense-list",
                "ui_action_ref": "observe-expense" if expected else "delete-expense",
                "operation_scope": "screen" if expected else "bound_record",
                "playwright": {
                    "action": "wait_for" if expected else "click",
                    "locator": {"by": "css", "value": ".expense", "exact": True},
                    "state": "visible" if expected else None,
                    "observations": [],
                    "mask_locators": [],
                },
            }
        )
        if expected == 0:
            step["playwright"] = {
                "action": "click",
                "locator": {"by": "role", "value": "button", "name": "削除", "exact": True},
                "observations": [],
                "mask_locators": [],
            }
    else:
        step["target"] = f"{step_id}.v1"
    return step


def _path(source: str, field: str) -> str:
    return {
        "database": f"rows[0].{field}",
        "response": f"body.record.{field}",
        "ui": f"record.{field}",
    }[source]
