from __future__ import annotations

from collections.abc import Mapping

import pytest

from operamind.application.data_identity import (
    DEFAULT_DATA_IDENTITY_PROVIDER_TYPES,
    DataIdentityProvider,
    DataIdentityResolveRequest,
    DataIdentitySourceEvidence,
)
from operamind.infrastructure.test_data.identity import (
    ApiDataIdentityProvider,
    DatabaseDataIdentityProvider,
    HybridDataIdentityProvider,
    UiDataIdentityProvider,
    configured_data_identity_providers,
    default_data_identity_providers,
)


def test_project_specific_provider_refs_bind_to_concrete_implementations() -> None:
    providers = configured_data_identity_providers(
        {
            "database.expense.v7": "database",
            "api.expense.v3": "api",
            "ui.expense.v2": "ui",
            "hybrid.expense.v1": "hybrid",
        }
    )

    assert {
        provider_ref: provider.provider_type
        for provider_ref, provider in providers.items()
    } == {
        "database.expense.v7": "database",
        "api.expense.v3": "api",
        "ui.expense.v2": "ui",
        "hybrid.expense.v1": "hybrid",
    }
    with pytest.raises(ValueError, match="unsupported"):
        configured_data_identity_providers({"oracle.expense.v1": "oracle"})


def _identity(source: str, record_path: str, count_path: str) -> dict[str, object]:
    return {
        "primary_key": {"name": "id", "source": source, "path": f"{record_path}.id"},
        "business_unique_keys": [
            {"name": "number", "source": source, "path": f"{record_path}.number"}
        ],
        "screen_key": {
            "name": "number",
            "source": source,
            "path": f"{record_path}.number",
            "locator_template": {
                "by": "css",
                "value": "[data-number='{{value}}']",
                "exact": True,
            },
        },
        "match_count": {"source": source, "path": count_path},
    }


def _request(
    *,
    identity: Mapping[str, object],
    observations: Mapping[str, object],
    evidence: tuple[DataIdentitySourceEvidence, ...],
) -> DataIdentityResolveRequest:
    return DataIdentityResolveRequest(
        project_id="visiondemo",
        run_id="run-1",
        test_data_id="expense-bound",
        provider_ref="provider.v1",
        identity_definition=identity,
        observations=observations,
        source_evidence=evidence,
        evidence_ref="artifact://result/data-bindings/binding-1",
    )


def _evidence(evidence_type: str) -> DataIdentitySourceEvidence:
    return DataIdentitySourceEvidence(
        evidence_type=evidence_type,
        evidence_ref=f"evidence://run/{evidence_type}",
        sanitized=True,
    )


@pytest.mark.parametrize(
    ("provider", "identity", "observations", "evidence"),
    [
        (
            DatabaseDataIdentityProvider(),
            _identity("database", "rows[0]", "row_count"),
            {
                "database": {
                    "row_count": 1,
                    "rows": [{"id": 41, "number": "EXP-041"}],
                }
            },
            (_evidence("sql"),),
        ),
        (
            ApiDataIdentityProvider(),
            _identity("api", "body.record", "body.match_count"),
            {
                "api": {
                    "status_code": 200,
                    "body": {
                        "match_count": 1,
                        "record": {"id": 41, "number": "EXP-041"},
                    },
                }
            },
            (_evidence("response"),),
        ),
        (
            UiDataIdentityProvider(),
            _identity("ui", "record", "match_count"),
            {
                "ui": {
                    "match_count": 1,
                    "record": {"id": 41, "number": "EXP-041"},
                }
            },
            (_evidence("step_log"), _evidence("screenshot")),
        ),
    ],
)
def test_single_source_providers_return_the_uniform_exact_identity_contract(
    provider: DataIdentityProvider,
    identity: Mapping[str, object],
    observations: Mapping[str, object],
    evidence: tuple[DataIdentitySourceEvidence, ...],
) -> None:
    result = provider.resolve(
        _request(identity=identity, observations=observations, evidence=evidence)
    ).to_mapping()

    assert set(result) == {
        "primary_key",
        "business_unique_keys",
        "screen_identity_values",
        "record_scope_locator",
        "match_count",
        "evidence_ref",
    }
    assert result == {
        "primary_key": {"name": "id", "value": 41},
        "business_unique_keys": [{"name": "number", "value": "EXP-041"}],
        "screen_identity_values": [{"name": "number", "value": "EXP-041"}],
        "record_scope_locator": {
            "by": "css",
            "value": "[data-number='EXP-041']",
            "exact": True,
        },
        "match_count": 1,
        "evidence_ref": "artifact://result/data-bindings/binding-1",
    }


def test_hybrid_provider_resolves_only_from_multiple_real_source_observations() -> None:
    identity = _identity("database", "rows[0]", "row_count")
    identity["screen_key"].update(  # type: ignore[union-attr]
        {"source": "ui", "path": "record.number"}
    )
    result = HybridDataIdentityProvider().resolve(
        _request(
            identity=identity,
            observations={
                "database": {
                    "row_count": 1,
                    "rows": [{"id": 41, "number": "EXP-041"}],
                },
                "ui": {"record": {"number": "EXP-041"}},
            },
            evidence=(
                _evidence("sql"),
                _evidence("step_log"),
                _evidence("screenshot"),
            ),
        )
    )

    assert result.match_count == 1
    assert result.primary_key.value == 41
    assert result.business_unique_keys[0].value == "EXP-041"
    assert result.screen_identity_values[0].value == "EXP-041"


def test_hybrid_provider_blocks_sources_that_do_not_prove_the_same_record() -> None:
    identity = _identity("database", "rows[0]", "row_count")
    identity["business_unique_keys"][0].update(  # type: ignore[index,union-attr]
        {"source": "api", "path": "body.record.number"}
    )
    identity["screen_key"].update(  # type: ignore[union-attr]
        {"source": "ui", "path": "record.number"}
    )
    with pytest.raises(ValueError, match="same business record"):
        HybridDataIdentityProvider().resolve(
            _request(
                identity=identity,
                observations={
                    "database": {
                        "row_count": 1,
                        "rows": [{"id": 41, "number": "EXP-041"}],
                    },
                    "api": {"body": {"record": {"number": "EXP-041"}}},
                    "ui": {"record": {"number": "EXP-041"}},
                },
                evidence=(
                    _evidence("sql"),
                    _evidence("response"),
                    _evidence("step_log"),
                    _evidence("screenshot"),
                ),
            )
        )


@pytest.mark.parametrize("count", [0, 2])
def test_provider_blocks_when_real_match_count_is_not_exactly_one(count: int) -> None:
    with pytest.raises(ValueError, match="exactly one record"):
        DatabaseDataIdentityProvider().resolve(
            _request(
                identity=_identity("database", "rows[0]", "row_count"),
                observations={
                    "database": {
                        "row_count": count,
                        "rows": [{"id": 41, "number": "EXP-041"}],
                    }
                },
                evidence=(_evidence("sql"),),
            )
        )


def test_provider_blocks_source_mismatch_missing_evidence_and_secret_identity() -> None:
    database_identity = _identity("database", "rows[0]", "row_count")
    observations = {
        "database": {
            "row_count": 1,
            "rows": [{"id": 41, "number": "EXP-041", "access_token": "secret"}],
        }
    }
    with pytest.raises(ValueError, match="different source"):
        ApiDataIdentityProvider().resolve(
            _request(
                identity=database_identity,
                observations=observations,
                evidence=(_evidence("sql"),),
            )
        )
    with pytest.raises(ValueError, match="source Evidence"):
        DatabaseDataIdentityProvider().resolve(
            _request(identity=database_identity, observations=observations, evidence=())
        )
    database_identity["primary_key"].update(  # type: ignore[union-attr]
        {"name": "accessToken", "path": "rows[0].access_token"}
    )
    with pytest.raises(ValueError, match="Secret-like fields"):
        DatabaseDataIdentityProvider().resolve(
            _request(
                identity=database_identity,
                observations=observations,
                evidence=(_evidence("sql"),),
            )
        )


@pytest.mark.parametrize(
    "secret_value",
    [
        "Bearer live-api-credential",
        "password=live-password",
        "postgresql://user:live-password@127.0.0.1/database",
    ],
)
def test_provider_refuses_obvious_secret_values_before_they_can_be_persisted(
    secret_value: str,
) -> None:
    with pytest.raises(ValueError, match="cannot be persisted"):
        DatabaseDataIdentityProvider().resolve(
            _request(
                identity=_identity("database", "rows[0]", "row_count"),
                observations={
                    "database": {
                        "row_count": 1,
                        "rows": [{"id": secret_value, "number": "EXP-041"}],
                    }
                },
                evidence=(_evidence("sql"),),
            )
        )


def test_provider_blocks_unsafe_or_non_exact_record_scope_locator() -> None:
    identity = _identity("database", "rows[0]", "row_count")
    observations = {
        "database": {
            "row_count": 1,
            "rows": [{"id": 41, "number": "EXP' OTHER"}],
        }
    }
    with pytest.raises(ValueError, match="unsafe"):
        DatabaseDataIdentityProvider().resolve(
            _request(
                identity=identity,
                observations=observations,
                evidence=(_evidence("sql"),),
            )
        )
    identity["screen_key"]["locator_template"]["exact"] = False  # type: ignore[index]
    observations["database"]["rows"][0]["number"] = "EXP-041"  # type: ignore[index]
    with pytest.raises(ValueError, match="exact matching"):
        DatabaseDataIdentityProvider().resolve(
            _request(
                identity=identity,
                observations=observations,
                evidence=(_evidence("sql"),),
            )
        )


def test_default_registry_contains_only_the_four_real_provider_types() -> None:
    providers = default_data_identity_providers()

    assert set(providers) == {"database.v1", "api.v1", "ui.v1", "hybrid.v1"}
    assert {
        provider_ref: provider.provider_type for provider_ref, provider in providers.items()
    } == dict(DEFAULT_DATA_IDENTITY_PROVIDER_TYPES)


def test_provider_renders_all_composite_screen_identity_values_into_one_scope() -> None:
    identity = _identity("database", "rows[0]", "row_count")
    primary_screen = identity["screen_key"]
    assert isinstance(primary_screen, dict)
    primary_screen["locator_template"] = {
        "by": "css",
        "value": "tr.expense",
        "exact": True,
        "all": [
            {"by": "text", "value": "{{number}}", "exact": True},
            {"by": "text", "value": "{{employee_no}}", "exact": True},
            {"by": "text", "value": "{{application_date}}", "exact": True},
        ],
    }
    identity["screen_identity_values"] = [
        primary_screen,
        {
            "name": "employee_no",
            "source": "database",
            "path": "rows[0].employee_no",
            "locator_template": {"by": "text", "value": "{{value}}", "exact": True},
        },
        {
            "name": "application_date",
            "source": "database",
            "path": "rows[0].application_date",
            "locator_template": {"by": "text", "value": "{{value}}", "exact": True},
        },
    ]

    result = DatabaseDataIdentityProvider().resolve(
        _request(
            identity=identity,
            observations={
                "database": {
                    "row_count": 1,
                    "rows": [
                        {
                            "id": 41,
                            "number": "EXP-041",
                            "employee_no": "EMP-009",
                            "application_date": "2026-08-05",
                        }
                    ],
                }
            },
            evidence=(_evidence("sql"),),
        )
    )

    assert [value.value for value in result.screen_identity_values] == [
        "EXP-041",
        "EMP-009",
        "2026-08-05",
    ]
    assert result.record_scope_locator == {
        "by": "css",
        "value": "tr.expense",
        "exact": True,
        "all": [
            {"by": "text", "value": "EXP-041", "exact": True},
            {"by": "text", "value": "EMP-009", "exact": True},
            {"by": "text", "value": "2026-08-05", "exact": True},
        ],
    }
