"""Real-observation DataIdentityProvider implementations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from operamind.application.data_identity import (
    DataIdentityMatchCountError,
    DataIdentityProvider,
    DataIdentityResolveRequest,
    DataIdentityResult,
    DataIdentityValue,
    is_sensitive_data_identity_name,
)

_ARRAY_INDEX = re.compile(r"\[(\d+)\]")
_SAFE_CSS_VALUE = re.compile(r"^[A-Za-z0-9._:-]+$")
_SOURCE_GROUP = {
    "database": "database",
    "api": "api",
    "response": "api",
    "ui": "ui",
}
_REQUIRED_EVIDENCE = {
    "database": frozenset({"sql"}),
    "api": frozenset({"response"}),
    "ui": frozenset({"step_log", "screenshot"}),
}


@dataclass(frozen=True, slots=True)
class _ObservedDataIdentityProvider:
    provider_type: str

    def resolve(self, request: DataIdentityResolveRequest) -> DataIdentityResult:
        identity = request.identity_definition
        specifications = _identity_specifications(identity)
        source_groups = {_source_group(spec) for spec in specifications}
        if self.provider_type == "hybrid":
            if len(source_groups) < 2:
                raise ValueError("Hybrid DataIdentityProvider requires at least two real sources")
        elif source_groups != {self.provider_type}:
            raise ValueError(
                f"{self.provider_type} DataIdentityProvider received a different source: "
                f"{sorted(source_groups)}"
            )
        _require_source_evidence(request, source_groups)
        match_count = _identity_source_value(
            cast(Mapping[str, object], identity["match_count"]), request
        )
        if isinstance(match_count, bool) or not isinstance(match_count, int):
            raise ValueError("Data identity match_count must be an integer observation")
        if match_count != 1:
            raise DataIdentityMatchCountError(match_count)
        primary = _identity_value(cast(Mapping[str, object], identity["primary_key"]), request)
        business_specs = cast(list[Mapping[str, object]], identity["business_unique_keys"])
        business = tuple(_identity_value(value, request) for value in business_specs)
        screen_specs = _screen_identity_specifications(identity)
        screen = tuple(_identity_value(spec, request) for spec in screen_specs)
        if self.provider_type == "hybrid":
            _require_hybrid_record_consistency(
                (
                    (
                        _source_group(cast(Mapping[str, object], identity["primary_key"])),
                        primary,
                    ),
                    *(
                        (_source_group(spec), value)
                        for spec, value in zip(business_specs, business, strict=True)
                    ),
                    *(
                        (_source_group(spec), value)
                        for spec, value in zip(screen_specs, screen, strict=True)
                    ),
                ),
                source_groups,
            )
        locator = _render_scope_locator(
            request.test_data_id,
            cast(Mapping[str, object], screen_specs[0]["locator_template"]),
            {value.name: value.value for value in screen},
            primary_value=screen[0].value,
        )
        return DataIdentityResult(
            primary_key=primary,
            business_unique_keys=business,
            screen_identity_values=screen,
            record_scope_locator=locator,
            match_count=match_count,
            evidence_ref=request.evidence_ref,
        )


class DatabaseDataIdentityProvider(_ObservedDataIdentityProvider):
    def __init__(self) -> None:
        super().__init__("database")


class ApiDataIdentityProvider(_ObservedDataIdentityProvider):
    def __init__(self) -> None:
        super().__init__("api")


class UiDataIdentityProvider(_ObservedDataIdentityProvider):
    def __init__(self) -> None:
        super().__init__("ui")


class HybridDataIdentityProvider(_ObservedDataIdentityProvider):
    def __init__(self) -> None:
        super().__init__("hybrid")


def default_data_identity_providers() -> Mapping[str, DataIdentityProvider]:
    """Explicit production registry; absence of a ref is always fail closed."""

    return {
        "database.v1": DatabaseDataIdentityProvider(),
        "api.v1": ApiDataIdentityProvider(),
        "ui.v1": UiDataIdentityProvider(),
        "hybrid.v1": HybridDataIdentityProvider(),
    }


def configured_data_identity_providers(
    provider_types: Mapping[str, str],
) -> Mapping[str, DataIdentityProvider]:
    """Bind reviewed Project refs to the matching concrete Provider implementation."""

    factories = {
        "database": DatabaseDataIdentityProvider,
        "api": ApiDataIdentityProvider,
        "ui": UiDataIdentityProvider,
        "hybrid": HybridDataIdentityProvider,
    }
    providers: dict[str, DataIdentityProvider] = {}
    for provider_ref, provider_type in provider_types.items():
        if not provider_ref.strip():
            raise ValueError("Project DataIdentityProvider ref must not be blank")
        factory = factories.get(provider_type)
        if factory is None:
            raise ValueError(
                f"Project DataIdentityProvider type is unsupported: {provider_type!r}"
            )
        providers[provider_ref] = factory()
    return providers


def _identity_specifications(
    identity: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    primary = identity.get("primary_key")
    business = identity.get("business_unique_keys")
    screen = _screen_identity_specifications(identity)
    match_count = identity.get("match_count")
    if (
        not isinstance(primary, Mapping)
        or not isinstance(business, list)
        or not business
        or any(not isinstance(value, Mapping) for value in business)
        or not screen
        or not isinstance(match_count, Mapping)
    ):
        raise ValueError("Data identity definition is incomplete")
    return (
        primary,
        *cast(list[Mapping[str, object]], business),
        *screen,
        match_count,
    )


def _screen_identity_specifications(
    identity: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    primary = identity.get("screen_key")
    configured = identity.get("screen_identity_values")
    if configured is None:
        if not isinstance(primary, Mapping):
            raise ValueError("Data identity screen key is incomplete")
        return (primary,)
    if (
        not isinstance(configured, list)
        or len(configured) < 2
        or any(not isinstance(value, Mapping) for value in configured)
    ):
        raise ValueError("Data identity screen_identity_values are incomplete")
    values = tuple(cast(list[Mapping[str, object]], configured))
    if not isinstance(primary, Mapping) or any(
        primary.get(key) != values[0].get(key) for key in ("name", "source", "path")
    ):
        raise ValueError("Data identity screen_key must equal the first screen identity value")
    names = [str(value.get("name", "")) for value in values]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("Data identity screen identity names must be unique")
    return values


def _source_group(spec: Mapping[str, object]) -> str:
    source = str(spec.get("source", ""))
    group = _SOURCE_GROUP.get(source)
    if group is None:
        raise ValueError(f"Data identity source is not a real Provider source: {source!r}")
    return group


def _require_source_evidence(
    request: DataIdentityResolveRequest,
    source_groups: set[str],
) -> None:
    actual = {value.evidence_type for value in request.source_evidence}
    missing = set().union(*(_REQUIRED_EVIDENCE[group] for group in source_groups)) - actual
    if missing:
        raise ValueError(
            f"DataIdentityProvider has no real sanitized source Evidence: {sorted(missing)}"
        )


def _require_hybrid_record_consistency(
    observed: tuple[tuple[str, DataIdentityValue], ...],
    source_groups: set[str],
) -> None:
    """Require shared equal identity values to connect every hybrid source."""

    connected = {group: {group} for group in source_groups}
    for index, (left_group, left_value) in enumerate(observed):
        for right_group, right_value in observed[index + 1 :]:
            if (
                left_group != right_group
                and left_value.name == right_value.name
                and left_value.value == right_value.value
            ):
                connected[left_group].add(right_group)
                connected[right_group].add(left_group)
    reached = {next(iter(source_groups))}
    pending = list(reached)
    while pending:
        group = pending.pop()
        unseen = connected[group] - reached
        reached.update(unseen)
        pending.extend(unseen)
    if reached != source_groups:
        raise ValueError(
            "Hybrid DataIdentityProvider sources do not prove the same business record"
        )


def _identity_value(
    spec: Mapping[str, object], request: DataIdentityResolveRequest
) -> DataIdentityValue:
    name = str(spec.get("name", ""))
    path = str(spec.get("path", ""))
    _reject_sensitive_identity(name=name, path=path)
    value = _identity_source_value(spec, request)
    if not isinstance(value, str | int | float | bool):
        raise ValueError(f"Data identity value must be scalar: {name}")
    return DataIdentityValue(name=name, value=value)


def _identity_source_value(
    spec: Mapping[str, object], request: DataIdentityResolveRequest
) -> object:
    source_name = str(spec.get("source", ""))
    source = request.observations.get(source_name)
    exists, value = _extract(source, str(spec.get("path", "")))
    if not exists:
        raise ValueError(
            f"Data identity source was not observed: {source_name}.{spec.get('path', '')}"
        )
    return value


def _reject_sensitive_identity(*, name: str, path: str) -> None:
    components = [name, *re.split(r"[.\[\]]+", path)]
    if any(is_sensitive_data_identity_name(value) for value in components if value):
        raise ValueError("Secret-like fields cannot be used as Data identity values")


def _render_scope_locator(
    test_data_id: str,
    template: Mapping[str, object],
    values: Mapping[str, object],
    *,
    primary_value: object,
) -> dict[str, object]:
    for value in values.values():
        if _SAFE_CSS_VALUE.fullmatch(str(value)) is None and _template_uses_css(template):
            raise ValueError(
                f"{test_data_id} screen identity is unsafe for the reviewed CSS Locator"
            )
    replacements = {"value": primary_value, **values}
    locator = cast(dict[str, object], _render_locator_value(template, replacements))
    if locator.get("exact") is not True:
        raise ValueError("Data identity record Scope Locator must use exact matching")
    return locator


def _render_locator_value(value: object, replacements: Mapping[str, object]) -> object:
    if isinstance(value, str):
        rendered = value
        for name, replacement in replacements.items():
            rendered = rendered.replace("{{" + name + "}}", str(replacement))
        if "{{" in rendered or "}}" in rendered:
            raise ValueError("Data identity Locator contains an unresolved placeholder")
        return rendered
    if isinstance(value, list):
        return [_render_locator_value(item, replacements) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _render_locator_value(item, replacements) for key, item in value.items()}
    return value


def _template_uses_css(value: object) -> bool:
    if isinstance(value, Mapping):
        return value.get("by") == "css" or any(_template_uses_css(item) for item in value.values())
    if isinstance(value, list):
        return any(_template_uses_css(item) for item in value)
    return False


def _extract(source: object, path: str) -> tuple[bool, object | None]:
    if path in {"", "$"}:
        return source is not None, source
    normalized = path[2:] if path.startswith("$.") else path
    normalized = _ARRAY_INDEX.sub(r".\1", normalized)
    if "[" in normalized or "]" in normalized:
        return False, None
    current = source
    for component in normalized.split("."):
        if not component:
            return False, None
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif isinstance(current, list) and component.isdigit() and int(component) < len(current):
            current = current[int(component)]
        else:
            return False, None
    return True, current
