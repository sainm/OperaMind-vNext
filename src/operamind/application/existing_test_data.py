"""Register and confirm existing real target-system data without exposing internals."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from operamind.application.data_identity import (
    DataIdentityMatchCountError,
    DataIdentityProvider,
    DataIdentityResolveRequest,
    DataIdentityResult,
    DataIdentitySourceEvidence,
    is_sensitive_data_identity_name,
)

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class ExistingTestDataRegistrationInput:
    registration_id: str
    project_id: str
    data_name: str
    business_unique_value: str
    test_case_ref: str
    retain_after_test: bool
    requested_by: str
    requested_at: datetime

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.registration_id,
                self.project_id,
                self.data_name,
                self.business_unique_value,
                self.test_case_ref,
                self.requested_by,
            )
        ):
            raise ValueError("Existing test data registration fields must not be blank")
        if self.requested_at.utcoffset() is None:
            raise ValueError("Existing test data requested_at must include a timezone")


@dataclass(frozen=True, slots=True)
class ProjectDataIdentityProfile:
    project_id: str
    provider_ref: str
    provider_type: str
    lookup_steps: tuple[Mapping[str, object], ...]
    cleanup_steps: tuple[Mapping[str, object], ...]
    identity_definition: Mapping[str, object]
    business_summary_fields: tuple[str, ...]
    revision: int = 1

    def __post_init__(self) -> None:
        if self.provider_type not in {"database", "api", "ui", "hybrid"}:
            raise ValueError("Project DataIdentityProvider type is invalid")
        if not self.project_id.strip() or not self.provider_ref.strip():
            raise ValueError("Project DataIdentityProvider identity must not be blank")
        if not self.lookup_steps or not self.business_summary_fields:
            raise ValueError("Project DataIdentityProvider lookup/summary must not be empty")
        if self.revision < 1:
            raise ValueError("Project DataIdentityProvider revision must be positive")
        if not _contains_business_unique_value_reference(self.lookup_steps):
            raise ValueError(
                "Project DataIdentityProvider lookup must bind business_unique_value"
            )
        channels = {str(step.get("channel", "")) for step in self.lookup_steps}
        expected = {"database": "sql", "api": "http", "ui": "ui"}
        if self.provider_type == "hybrid":
            if len(channels.intersection({"sql", "http", "ui"})) < 2:
                raise ValueError(
                    "Hybrid Project DataIdentityProvider requires multiple real lookup channels"
                )
        elif channels != {expected[self.provider_type]}:
            raise ValueError("Project DataIdentityProvider lookup channel differs")
        _validate_reviewed_steps(self.lookup_steps, phase="lookup")
        _validate_reviewed_steps(self.cleanup_steps, phase="cleanup")
        if self.identity_definition.get("source_step_id") != self.lookup_steps[-1].get(
            "step_id"
        ):
            raise ValueError(
                "Project DataIdentityProvider identity source must be the final lookup Step"
            )
        lookup_outputs = {
            str(output.get("variable", ""))
            for step in self.lookup_steps
            for output in cast(list[Mapping[str, object]], step.get("output_bindings", []))
        }
        for step in self.cleanup_steps:
            if step.get("channel") == "ui":
                continue
            cleanup_variables = _variable_references(step.get("inputs", {}))
            if not cleanup_variables.intersection(
                {"business_unique_value", *lookup_outputs}
            ):
                raise ValueError(
                    "Project DataIdentityProvider cleanup must use the registered business "
                    "value or a reviewed lookup output"
                )

    @property
    def content_digest(self) -> str:
        payload = {
            "project_id": self.project_id,
            "provider_ref": self.provider_ref,
            "provider_type": self.provider_type,
            "lookup_steps": self.lookup_steps,
            "cleanup_steps": self.cleanup_steps,
            "identity_definition": self.identity_definition,
            "business_summary_fields": self.business_summary_fields,
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class ExistingDataObservation:
    observations: Mapping[str, object]
    source_evidence: tuple[DataIdentitySourceEvidence, ...]


class ExistingDataObservationResolver(Protocol):
    """Execute only the reviewed lookup Steps held by one Project profile."""

    def resolve(
        self,
        *,
        registration: ExistingTestDataRegistrationInput,
        profile: ProjectDataIdentityProfile,
    ) -> ExistingDataObservation: ...


@dataclass(frozen=True, slots=True)
class ExistingTestDataRegistration:
    registration_id: str
    project_id: str
    data_name: str
    business_unique_value: str
    test_case_ref: str
    retain_after_test: bool
    status: str
    provider_ref: str | None
    provider_type: str | None
    match_count: int | None
    business_summary: Mapping[str, object] | None
    identity_candidate: Mapping[str, object] | None
    evidence_refs: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    requested_by: str
    requested_at: datetime
    provider_revision: int | None = None
    provider_digest: str | None = None
    plan_data_definition: Mapping[str, object] | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class ExistingTestDataRegistrationService:
    """Resolve one real record and produce an adopted TestDataPlan fragment."""

    def __init__(
        self,
        *,
        identity_providers: Mapping[str, DataIdentityProvider],
        observation_resolver: ExistingDataObservationResolver,
    ) -> None:
        self._identity_providers = dict(identity_providers)
        self._observation_resolver = observation_resolver

    def register(
        self,
        value: ExistingTestDataRegistrationInput,
        *,
        profiles: Sequence[ProjectDataIdentityProfile],
    ) -> ExistingTestDataRegistration:
        applicable = [profile for profile in profiles if profile.project_id == value.project_id]
        if not applicable:
            return _blocked(value, "DataIdentityProvider が Project に設定されていません。")
        matches: list[
            tuple[ProjectDataIdentityProfile, DataIdentityResult, ExistingDataObservation]
        ] = []
        failures: list[str] = []
        observed_counts: list[int] = []
        blocked_evidence_refs: set[str] = set()
        for profile in applicable:
            provider = self._identity_providers.get(profile.provider_ref)
            if provider is None or provider.provider_type != profile.provider_type:
                failures.append(
                    f"DataIdentityProvider が登録されていません: {profile.provider_ref}"
                )
                continue
            try:
                observation = self._observation_resolver.resolve(
                    registration=value,
                    profile=profile,
                )
                blocked_evidence_refs.update(
                    item.evidence_ref for item in observation.source_evidence
                )
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
                failures.append(f"{profile.provider_ref}: {error}")
                continue
            try:
                result = provider.resolve(
                    DataIdentityResolveRequest(
                        project_id=value.project_id,
                        run_id=f"registration:{value.registration_id}",
                        test_data_id=_test_data_id(value.registration_id),
                        provider_ref=profile.provider_ref,
                        identity_definition=profile.identity_definition,
                        observations=observation.observations,
                        source_evidence=observation.source_evidence,
                        evidence_ref=(
                            f"registration://{value.registration_id}/identity-candidate"
                        ),
                    )
                )
                matches.append((profile, result, observation))
            except DataIdentityMatchCountError as error:
                observed_counts.append(error.match_count)
                failures.append(f"{profile.provider_ref}: {error}")
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
                failures.append(f"{profile.provider_ref}: {error}")
        if len(matches) != 1:
            reason = (
                "複数の Provider が同じ既存データを返しました。"
                if len(matches) > 1
                else "既存データを一意に確認できませんでした。"
            )
            match_count = len(matches) if len(matches) > 1 else (
                observed_counts[0] if len(observed_counts) == 1 else None
            )
            return _blocked(
                value,
                reason,
                *failures,
                match_count=match_count,
                evidence_refs=tuple(sorted(blocked_evidence_refs)),
            )
        profile, result, observation = matches[0]
        observed_business_values = {
            str(item.value).strip() for item in result.business_unique_keys
        }
        if value.business_unique_value.strip() not in observed_business_values:
            return _blocked(
                value,
                "入力した業務一意値と Provider が返した実レコードが一致しません。",
                match_count=1,
                evidence_refs=tuple(
                    sorted(item.evidence_ref for item in observation.source_evidence)
                ),
            )
        summary = _business_summary(
            result,
            profile.business_summary_fields,
            observations=observation.observations,
        )
        candidate = result.to_mapping()
        return ExistingTestDataRegistration(
            registration_id=value.registration_id,
            project_id=value.project_id,
            data_name=value.data_name,
            business_unique_value=value.business_unique_value,
            test_case_ref=value.test_case_ref,
            retain_after_test=value.retain_after_test,
            status="candidate",
            provider_ref=profile.provider_ref,
            provider_type=profile.provider_type,
            match_count=1,
            business_summary=summary,
            identity_candidate=candidate,
            evidence_refs=tuple(
                sorted(value.evidence_ref for value in observation.source_evidence)
            ),
            blocking_reasons=(),
            requested_by=value.requested_by,
            requested_at=value.requested_at,
            provider_revision=profile.revision,
            provider_digest=profile.content_digest,
        )

    def confirm(
        self,
        registration: ExistingTestDataRegistration,
        *,
        profile: ProjectDataIdentityProfile,
        actor: str,
        confirmed_at: datetime,
    ) -> ExistingTestDataRegistration:
        if registration.status != "candidate" or registration.match_count != 1:
            raise ValueError("Only a unique existing-data candidate can be confirmed")
        if (
            profile.project_id != registration.project_id
            or profile.provider_ref != registration.provider_ref
            or profile.provider_type != registration.provider_type
        ):
            raise ValueError("Existing-data confirmation Provider scope differs")
        if (
            registration.provider_revision != profile.revision
            or registration.provider_digest != profile.content_digest
        ):
            raise ValueError(
                "DataIdentityProvider 設定が候補生成後に変更されました。再登録してください。"
            )
        if not actor.strip() or confirmed_at.utcoffset() is None:
            raise ValueError("Existing-data confirmation identity/time is invalid")
        if not registration.retain_after_test and not profile.cleanup_steps:
            raise ValueError(
                "保持しない既存テストデータには確認済み cleanup Step が必要です"
            )
        definition = _adopted_plan_definition(registration, profile)
        return ExistingTestDataRegistration(
            **{
                field: getattr(registration, field)
                for field in (
                    "registration_id",
                    "project_id",
                    "data_name",
                    "business_unique_value",
                    "test_case_ref",
                    "retain_after_test",
                    "provider_ref",
                    "provider_type",
                    "match_count",
                    "business_summary",
                    "identity_candidate",
                    "evidence_refs",
                    "requested_by",
                    "requested_at",
                    "provider_revision",
                    "provider_digest",
                )
            },
            status="confirmed",
            blocking_reasons=(),
            plan_data_definition=definition,
            confirmed_by=actor,
            confirmed_at=confirmed_at,
        )


def _blocked(
    value: ExistingTestDataRegistrationInput,
    *reasons: str,
    match_count: int | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> ExistingTestDataRegistration:
    return ExistingTestDataRegistration(
        registration_id=value.registration_id,
        project_id=value.project_id,
        data_name=value.data_name,
        business_unique_value=value.business_unique_value,
        test_case_ref=value.test_case_ref,
        retain_after_test=value.retain_after_test,
        status="blocked",
        provider_ref=None,
        provider_type=None,
        match_count=match_count,
        business_summary=None,
        identity_candidate=None,
        evidence_refs=evidence_refs,
        blocking_reasons=tuple(sorted(set(reasons))),
        requested_by=value.requested_by,
        requested_at=value.requested_at,
    )


def _business_summary(
    result: DataIdentityResult,
    allowed_fields: Sequence[str],
    *,
    observations: Mapping[str, object],
) -> dict[str, object]:
    values = {
        value.name: value.value
        for value in (
            result.primary_key,
            *result.business_unique_keys,
            *result.screen_identity_values,
        )
    }
    observed = _public_observation_fields(observations)
    summary: dict[str, object] = {}
    for name in allowed_fields:
        if is_sensitive_data_identity_name(name):
            raise ValueError("Business summary fields must not contain Secret names")
        if name in values:
            summary[name] = values[name]
        elif name in observed:
            summary[name] = observed[name]
    if not summary:
        raise ValueError("DataIdentityProvider returned no approved business summary fields")
    return summary


def _public_observation_fields(value: object) -> dict[str, object]:
    """Collect unambiguous scalar fields from reviewed real observations."""

    collected: dict[str, set[str]] = {}
    originals: dict[tuple[str, str], object] = {}

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                name = str(key)
                if is_sensitive_data_identity_name(name):
                    continue
                if isinstance(child, str | int | float | bool) and not isinstance(
                    child, bytes
                ):
                    normalized = json.dumps(child, ensure_ascii=False, sort_keys=True)
                    collected.setdefault(name, set()).add(normalized)
                    originals[(name, normalized)] = child
                else:
                    visit(child)
        elif isinstance(item, list | tuple):
            for child in item:
                visit(child)

    visit(value)
    return {
        name: originals[(name, next(iter(normalized_values)))]
        for name, normalized_values in collected.items()
        if len(normalized_values) == 1
    }


def _adopted_plan_definition(
    registration: ExistingTestDataRegistration,
    profile: ProjectDataIdentityProfile,
) -> dict[str, object]:
    test_data_id = _test_data_id(registration.registration_id)
    flow_id = f"adopt-{test_data_id}"
    identity = dict(profile.identity_definition)
    source_step_id = str(identity.pop("source_step_id", ""))
    if not source_step_id or source_step_id not in {
        str(step.get("step_id", "")) for step in profile.lookup_steps
    }:
        raise ValueError("Reviewed existing-data identity source Step does not exist")
    identity.update(
        {
            "provider": {
                "type": profile.provider_type,
                "provider_ref": profile.provider_ref,
            },
            "binding_mode": "adopted",
            "source_flow_id": flow_id,
            "source_step_id": source_step_id,
        }
    )
    cleanup_steps: list[dict[str, object]] = []
    for raw in profile.cleanup_steps:
        step = {
            **cast(
                dict[str, object],
                _replace_business_unique_value(
                    dict(raw),
                    registration.business_unique_value,
                ),
            ),
            "data_binding_ref": test_data_id,
        }
        if step.get("channel") == "ui":
            step["operation_scope"] = "bound_record"
        cleanup_steps.append(step)
    return {
        "schema_version": "v3",
        "data_set": {
            "test_data_id": test_data_id,
            "test_case_refs": [registration.test_case_ref],
            "setup_actions": [],
            "cleanup_policy": (
                "retain" if registration.retain_after_test else "delete_after_run"
            ),
            "identity_binding": identity,
            "coverage_conditions": [],
            "runtime_variable_writes": [],
        },
        "generation_flow": {
            "flow_id": flow_id,
            "title": f"既存テストデータを確認: {registration.data_name}",
            "depends_on_flows": [],
            "test_data_refs": [test_data_id],
            "test_case_refs": [registration.test_case_ref],
            "steps": [
                cast(
                    dict[str, object],
                    _replace_business_unique_value(
                        dict(value),
                        registration.business_unique_value,
                    ),
                )
                for value in profile.lookup_steps
            ],
            "final_assertions": [
                {
                    "assertion_id": f"{test_data_id}-unique",
                    "observe_via": "test",
                    "subject": test_data_id,
                    "operator": "satisfies",
                    "expected": "frozen",
                }
            ],
            "cleanup_policy": (
                "retain" if registration.retain_after_test else "delete_after_run"
            ),
            "cleanup_steps": cleanup_steps,
        },
        "registration_evidence_refs": list(registration.evidence_refs),
    }


def _test_data_id(registration_id: str) -> str:
    digest = hashlib.sha256(registration_id.encode()).hexdigest()[:20]
    return f"adopted-data-{digest}"


def _contains_business_unique_value_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_business_unique_value_reference(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_business_unique_value_reference(item) for item in value)
    return isinstance(value, str) and "{{business_unique_value}}" in value


def _variable_references(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return set().union(*(_variable_references(item) for item in value.values()), set())
    if isinstance(value, list | tuple):
        return set().union(*(_variable_references(item) for item in value), set())
    return set(_VARIABLE.findall(value)) if isinstance(value, str) else set()


def _replace_business_unique_value(value: object, business_unique_value: str) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _replace_business_unique_value(item, business_unique_value)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [
            _replace_business_unique_value(item, business_unique_value)
            for item in value
        ]
    if isinstance(value, str):
        return value.replace("{{business_unique_value}}", business_unique_value)
    return value


def _validate_reviewed_steps(
    steps: Sequence[Mapping[str, object]],
    *,
    phase: str,
) -> None:
    if not steps:
        return
    ids = [str(step.get("step_id", "")) for step in steps]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"Project DataIdentityProvider {phase} Step IDs are invalid")
    if [step.get("sequence") for step in steps] != list(range(1, len(steps) + 1)):
        raise ValueError(
            f"Project DataIdentityProvider {phase} Step sequence must be contiguous"
        )
    for step in steps:
        channel = str(step.get("channel", ""))
        if channel not in {"sql", "http", "ui"}:
            raise ValueError(
                f"Project DataIdentityProvider {phase} Step channel is invalid"
            )
        if not str(step.get("business_action", "")).strip():
            raise ValueError(
                f"Project DataIdentityProvider {phase} Step business_action is required"
            )
        if not isinstance(step.get("inputs"), Mapping):
            raise ValueError(
                f"Project DataIdentityProvider {phase} Step inputs must be an object"
            )
        if not isinstance(step.get("depends_on"), list) or not isinstance(
            step.get("output_bindings"), list
        ):
            raise ValueError(
                f"Project DataIdentityProvider {phase} Step dependencies/outputs are invalid"
            )
        assertions = step.get("postconditions")
        if (
            not isinstance(assertions, list)
            or not assertions
            or any(
                not isinstance(assertion, Mapping)
                or not {
                    "assertion_id",
                    "observe_via",
                    "subject",
                    "operator",
                    "expected",
                }.issubset(assertion)
                for assertion in assertions
            )
        ):
            raise ValueError(
                f"Project DataIdentityProvider {phase} Step postconditions are incomplete"
            )
        if channel == "ui":
            if (
                not str(step.get("screen_ref", "")).strip()
                or not str(step.get("ui_action_ref", "")).strip()
                or not isinstance(step.get("playwright"), Mapping)
            ):
                raise ValueError(
                    f"Project DataIdentityProvider {phase} UI Step is incomplete"
                )
        elif not str(step.get("target", "")).strip():
            raise ValueError(
                f"Project DataIdentityProvider {phase} Step target is required"
            )
