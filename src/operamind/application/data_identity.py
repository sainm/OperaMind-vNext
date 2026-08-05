"""Fail-closed identity resolution contract for real target-system observations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Protocol
from urllib.parse import urlsplit

IdentityScalar = str | int | float | bool
DEFAULT_DATA_IDENTITY_PROVIDER_TYPES: Mapping[str, str] = MappingProxyType(
    {
        "api.v1": "api",
        "database.v1": "database",
        "hybrid.v1": "hybrid",
        "ui.v1": "ui",
    }
)
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SENSITIVE_NAME_PARTS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "dsn",
        "passwd",
        "password",
        "private_key",
        "pwd",
        "secret",
        "session_id",
        "token",
    }
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:authorization|password|passwd|pwd|secret|token|api[_-]?key|"
    r"credential|dsn)\s*[:=]\s*\S+"
)
_BEARER_VALUE = re.compile(r"(?i)^\s*Bearer\s+\S+")
_LOCATOR_KEYS = frozenset({"by", "value", "name", "exact", "frame", "all"})
_LOCATOR_TYPES = frozenset(
    {"role", "label", "placeholder", "text", "alt_text", "title", "test_id", "css"}
)


@dataclass(frozen=True, slots=True)
class DataIdentityValue:
    """One named, non-secret identity value observed from a real provider."""

    name: str
    value: IdentityScalar

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Data identity value name must not be blank")
        if not isinstance(self.value, str | int | float | bool):
            raise ValueError("Data identity value must be scalar")
        if isinstance(self.value, float) and not isfinite(self.value):
            raise ValueError("Data identity numeric value must be finite")
        if is_sensitive_data_identity_name(self.name):
            raise ValueError("Secret-like fields cannot be used as Data identity values")
        if isinstance(self.value, str) and not self.value.strip():
            raise ValueError(f"Data identity value must not be blank: {self.name}")
        if isinstance(self.value, str) and _looks_like_secret_value(self.value):
            raise ValueError("Secret-like values cannot be persisted as Data identity")

    def to_mapping(self) -> dict[str, object]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True, slots=True)
class DataIdentitySourceEvidence:
    """Sanitized source Evidence metadata; payloads and secrets are never passed here."""

    evidence_type: str
    evidence_ref: str
    sanitized: bool

    def __post_init__(self) -> None:
        if not self.evidence_type.strip() or not self.evidence_ref.strip():
            raise ValueError("Data identity source Evidence metadata must not be blank")
        if self.sanitized is not True:
            raise ValueError("Data identity Provider requires sanitized source Evidence")


@dataclass(frozen=True, slots=True)
class DataIdentityResolveRequest:
    """Bounded input exposed to a DataIdentityProvider."""

    project_id: str
    run_id: str
    test_data_id: str
    provider_ref: str
    identity_definition: Mapping[str, object]
    observations: Mapping[str, object]
    source_evidence: tuple[DataIdentitySourceEvidence, ...]
    evidence_ref: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.project_id,
                self.run_id,
                self.test_data_id,
                self.provider_ref,
                self.evidence_ref,
            )
        ):
            raise ValueError("Data identity resolution scope must not be blank")


@dataclass(frozen=True, slots=True)
class DataIdentityResult:
    """Uniform result returned by database, API, UI and hybrid providers."""

    primary_key: DataIdentityValue
    business_unique_keys: tuple[DataIdentityValue, ...]
    screen_identity_values: tuple[DataIdentityValue, ...]
    record_scope_locator: Mapping[str, object]
    match_count: int
    evidence_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.primary_key, DataIdentityValue):
            raise ValueError("Data identity primary key must use DataIdentityValue")
        if any(
            not isinstance(value, DataIdentityValue)
            for value in (*self.business_unique_keys, *self.screen_identity_values)
        ):
            raise ValueError("Data identity keys must use DataIdentityValue")
        if not self.business_unique_keys:
            raise ValueError("Data identity requires at least one business unique key")
        if not self.screen_identity_values:
            raise ValueError("Data identity requires at least one screen identity value")
        if self.match_count != 1 or isinstance(self.match_count, bool):
            raise ValueError(
                "Data identity Provider must resolve exactly one record: "
                f"count={self.match_count!r}"
            )
        if not isinstance(self.record_scope_locator, Mapping):
            raise ValueError("Data identity record Scope Locator must be an object")
        if self.record_scope_locator.get("exact") is not True:
            raise ValueError("Data identity record Scope Locator must be exact")
        _validate_record_scope_locator(self.record_scope_locator)
        if not self.evidence_ref.strip():
            raise ValueError("Data identity Evidence ref must not be blank")
        if _looks_like_secret_value(self.evidence_ref):
            raise ValueError("Secret-like values cannot be persisted as Data identity Evidence")
        business_names = [value.name for value in self.business_unique_keys]
        screen_names = [value.name for value in self.screen_identity_values]
        if len(business_names) != len(set(business_names)):
            raise ValueError("Data identity business key names must be unique")
        if len(screen_names) != len(set(screen_names)):
            raise ValueError("Data identity screen value names must be unique")

    def to_mapping(self) -> dict[str, object]:
        """Return only the stable public Provider contract fields."""

        return {
            "primary_key": self.primary_key.to_mapping(),
            "business_unique_keys": [value.to_mapping() for value in self.business_unique_keys],
            "screen_identity_values": [value.to_mapping() for value in self.screen_identity_values],
            "record_scope_locator": dict(self.record_scope_locator),
            "match_count": self.match_count,
            "evidence_ref": self.evidence_ref,
        }


class DataIdentityProvider(Protocol):
    """Resolve one real record without receiving connection or authentication secrets."""

    @property
    def provider_type(self) -> str: ...

    def resolve(self, request: DataIdentityResolveRequest) -> DataIdentityResult: ...


class DataIdentityMatchCountError(ValueError):
    """A real Provider observation returned zero or more than one record."""

    def __init__(self, match_count: int) -> None:
        self.match_count = match_count
        super().__init__(
            f"Data identity Provider must resolve exactly one record: count={match_count!r}"
        )


def is_sensitive_data_identity_name(value: str) -> bool:
    """Recognize secret-bearing identity names without matching ordinary substrings."""

    normalized = _CAMEL_CASE_BOUNDARY.sub("_", value).casefold().replace("-", "_")
    parts = tuple(part for part in normalized.split("_") if part)
    candidates = {*parts, "_".join(parts)}
    return bool(candidates.intersection(_SENSITIVE_NAME_PARTS))


def redact_secret_evidence(value: object, *, field_name: str = "") -> object:
    """Recursively remove Secret-shaped values before persistence as Evidence."""

    if field_name and is_sensitive_data_identity_name(field_name):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(key): redact_secret_evidence(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_secret_evidence(item, field_name=field_name) for item in value]
    if isinstance(value, str) and _looks_like_secret_value(value):
        return "[REDACTED]"
    return value


def _validate_record_scope_locator(locator: Mapping[str, object]) -> None:
    if set(locator) - _LOCATOR_KEYS:
        raise ValueError("Data identity record Scope Locator has unsupported fields")
    if (
        locator.get("by") not in _LOCATOR_TYPES
        or not isinstance(locator.get("value"), str)
        or not str(locator["value"]).strip()
    ):
        raise ValueError("Data identity record Scope Locator is incomplete")
    if locator.get("exact") is not True:
        raise ValueError("Data identity record Scope Locator must be exact")
    for optional in ("name", "frame"):
        value = locator.get(optional)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Data identity record Scope Locator is incomplete")
    if locator.get("by") == "role" and not str(locator.get("name") or "").strip():
        raise ValueError("Data identity role Locator requires an accessible name")
    filters = locator.get("all")
    if filters is not None:
        if (
            not isinstance(filters, list)
            or not filters
            or any(not isinstance(value, Mapping) for value in filters)
        ):
            raise ValueError("Data identity composite record Scope Locator is incomplete")
        for value in filters:
            _validate_record_scope_locator(value)


def _looks_like_secret_value(value: str) -> bool:
    if _BEARER_VALUE.search(value) or _SECRET_ASSIGNMENT.search(value):
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(parsed.scheme and parsed.hostname and parsed.password is not None)
