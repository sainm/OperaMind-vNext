"""Strict, declarative browser execution model with no arbitrary code escape hatch."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from urllib.parse import urlsplit

_SAFE_ENV_NAME = re.compile(r"^OPERAMIND_UI_[A-Z0-9_]+$")
_SAFE_CSS = re.compile(
    r"^(?:#[A-Za-z][A-Za-z0-9_-]*|\.[A-Za-z][A-Za-z0-9_-]*|"
    r"\[(?:data-[A-Za-z0-9_-]+|name|type)=(?:\"[^\"]+\"|'[^']+')\])$"
)


class LocatorStrategy(StrEnum):
    ROLE = "role"
    LABEL = "label"
    TEXT = "text"
    TEST_ID = "test_id"
    PLACEHOLDER = "placeholder"
    CSS = "css"


class BrowserActionKind(StrEnum):
    CLICK = "click"
    FILL = "fill"
    SELECT_OPTION = "select_option"
    CHECK = "check"
    UNCHECK = "uncheck"


class BrowserAssertionKind(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    TEXT_EQUALS = "text_equals"
    TEXT_CONTAINS = "text_contains"
    VALUE_EQUALS = "value_equals"
    COUNT_EQUALS = "count_equals"
    CHECKED = "checked"
    UNCHECKED = "unchecked"


class BrowserFailureCategory(StrEnum):
    BUSINESS_ASSERTION = "business_assertion"
    ENVIRONMENT = "environment"
    TEST_DATA = "test_data"
    LOCATOR = "locator"
    AUTHENTICATION = "authentication"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class BrowserValue:
    source: str
    value: str

    def __post_init__(self) -> None:
        if self.source not in {"literal", "env"}:
            raise ValueError("Browser value source must be literal or env")
        if self.source == "env" and not self.value:
            raise ValueError("Browser environment value name must not be empty")
        if self.source == "env" and _SAFE_ENV_NAME.fullmatch(self.value) is None:
            raise ValueError("Browser env values must use the OPERAMIND_UI_ prefix")

    @classmethod
    def from_dict(cls, raw: object) -> BrowserValue:
        value = _object(raw, "Browser value")
        _exact_keys(value, {"source", "value"}, "Browser value")
        return cls(source=_string(value, "source"), value=_string(value, "value", blank=True))

    def to_dict(self) -> dict[str, object]:
        return {"source": self.source, "value": self.value}


@dataclass(frozen=True, slots=True)
class BrowserLocator:
    strategy: LocatorStrategy | None = None
    value: str | None = None
    name: str | None = None
    exact: bool = True
    target_ref: str | None = None

    def __post_init__(self) -> None:
        if self.target_ref is not None:
            if not self.target_ref.strip():
                raise ValueError("Browser Locator target_ref must not be blank")
            if self.strategy is not None or self.value is not None or self.name is not None:
                raise ValueError("Browser Locator target_ref cannot include a concrete Locator")
            return
        if self.strategy is None or self.value is None or not self.value.strip():
            raise ValueError("Browser Locator requires strategy and value")
        if self.strategy is LocatorStrategy.ROLE:
            if self.name is None or not self.name.strip():
                raise ValueError("role Locator requires an accessible name")
        elif self.name is not None:
            raise ValueError("Only role Locator accepts name")
        if self.strategy is LocatorStrategy.CSS and _SAFE_CSS.fullmatch(self.value) is None:
            raise ValueError("CSS Locator must be a single stable ID, class, or attribute selector")

    @classmethod
    def from_dict(cls, raw: object) -> BrowserLocator:
        value = _object(raw, "Browser Locator")
        _exact_keys(value, {"strategy", "value", "name", "exact", "target_ref"}, "Browser Locator")
        target_ref = _optional_string(value, "target_ref")
        if target_ref is not None:
            return cls(target_ref=target_ref)
        return cls(
            strategy=LocatorStrategy(_string(value, "strategy")),
            value=_string(value, "value"),
            name=_optional_string(value, "name"),
            exact=_boolean(value, "exact", default=True),
        )

    def to_dict(self) -> dict[str, object]:
        if self.target_ref is not None:
            return {"target_ref": self.target_ref}
        if self.strategy is None or self.value is None:
            raise RuntimeError("Validated Browser Locator lost its concrete fields")
        result: dict[str, object] = {
            "strategy": self.strategy.value,
            "value": self.value,
            "exact": self.exact,
        }
        if self.name is not None:
            result["name"] = self.name
        return result


@dataclass(frozen=True, slots=True)
class BrowserAction:
    action_id: str
    kind: BrowserActionKind
    locator: BrowserLocator
    value: BrowserValue | None = None
    route_source_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise ValueError("Browser Action ID must not be blank")
        needs_value = self.kind in {BrowserActionKind.FILL, BrowserActionKind.SELECT_OPTION}
        if needs_value != (self.value is not None):
            raise ValueError(f"Browser Action {self.kind.value} value is inconsistent")
        if self.route_source_ref is not None and not self.route_source_ref.strip():
            raise ValueError("Browser Action route_source_ref must not be blank")

    @classmethod
    def from_dict(cls, raw: object) -> BrowserAction:
        value = _object(raw, "Browser Action")
        _exact_keys(
            value,
            {"action_id", "kind", "locator", "value", "route_source_ref"},
            "Browser Action",
        )
        raw_value = value.get("value")
        return cls(
            action_id=_string(value, "action_id"),
            kind=BrowserActionKind(_string(value, "kind")),
            locator=BrowserLocator.from_dict(value.get("locator")),
            value=BrowserValue.from_dict(raw_value) if raw_value is not None else None,
            route_source_ref=_optional_string(value, "route_source_ref"),
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "locator": self.locator.to_dict(),
        }
        if self.value is not None:
            result["value"] = self.value.to_dict()
        if self.route_source_ref is not None:
            result["route_source_ref"] = self.route_source_ref
        return result


@dataclass(frozen=True, slots=True)
class BrowserAssertion:
    assertion_id: str
    kind: BrowserAssertionKind
    locator: BrowserLocator
    expected: BrowserValue | None
    failure_category: BrowserFailureCategory

    def __post_init__(self) -> None:
        if not self.assertion_id.strip():
            raise ValueError("Browser Assertion ID must not be blank")
        needs_expected = self.kind in {
            BrowserAssertionKind.TEXT_EQUALS,
            BrowserAssertionKind.TEXT_CONTAINS,
            BrowserAssertionKind.VALUE_EQUALS,
            BrowserAssertionKind.COUNT_EQUALS,
        }
        if needs_expected != (self.expected is not None):
            raise ValueError(f"Browser Assertion {self.kind.value} expected value is inconsistent")
        if (
            self.kind is BrowserAssertionKind.COUNT_EQUALS
            and self.expected is not None
            and (self.expected.source != "literal" or not self.expected.value.isdigit())
        ):
            raise ValueError("count_equals requires a non-negative literal integer")
        if self.failure_category in {
            BrowserFailureCategory.ENVIRONMENT,
            BrowserFailureCategory.BLOCKED,
        }:
            raise ValueError("Browser Assertions cannot classify environment or blocked failures")

    @classmethod
    def from_dict(cls, raw: object) -> BrowserAssertion:
        value = _object(raw, "Browser Assertion")
        _exact_keys(
            value,
            {"assertion_id", "kind", "locator", "expected", "failure_category"},
            "Browser Assertion",
        )
        raw_expected = value.get("expected")
        return cls(
            assertion_id=_string(value, "assertion_id"),
            kind=BrowserAssertionKind(_string(value, "kind")),
            locator=BrowserLocator.from_dict(value.get("locator")),
            expected=BrowserValue.from_dict(raw_expected) if raw_expected is not None else None,
            failure_category=BrowserFailureCategory(
                _string(value, "failure_category", default="business_assertion")
            ),
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "assertion_id": self.assertion_id,
            "kind": self.kind.value,
            "locator": self.locator.to_dict(),
            "failure_category": self.failure_category.value,
        }
        if self.expected is not None:
            result["expected"] = self.expected.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class BrowserScenarioSpec:
    scenario_id: str
    trigger_path: str
    impact_item_refs: tuple[str, ...]
    actions: tuple[BrowserAction, ...]
    assertions: tuple[BrowserAssertion, ...]
    redaction_locators: tuple[BrowserLocator, ...]
    preflight_assertions: tuple[BrowserAssertion, ...] = ()

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("Browser Scenario ID must not be blank")
        _validate_trigger_path(self.trigger_path)
        _unique_non_blank(self.impact_item_refs, "Browser Scenario Impact refs")
        if not self.assertions:
            raise ValueError("Browser Scenario requires at least one machine assertion")
        _unique((item.action_id for item in self.actions), "Browser Action IDs")
        _unique((item.assertion_id for item in self.assertions), "Browser Assertion IDs")
        _unique(
            (item.assertion_id for item in self.preflight_assertions),
            "Browser Preflight Assertion IDs",
        )
        if any(
            item.failure_category
            not in {BrowserFailureCategory.AUTHENTICATION, BrowserFailureCategory.TEST_DATA}
            for item in self.preflight_assertions
        ):
            raise ValueError("Preflight Assertions must classify authentication or test_data")

    @classmethod
    def from_dict(cls, raw: object) -> BrowserScenarioSpec:
        value = _object(raw, "Browser Scenario")
        _exact_keys(
            value,
            {
                "scenario_id",
                "trigger_path",
                "impact_item_refs",
                "actions",
                "assertions",
                "redaction_locators",
                "preflight_assertions",
            },
            "Browser Scenario",
        )
        return cls(
            scenario_id=_string(value, "scenario_id"),
            trigger_path=_string(value, "trigger_path"),
            impact_item_refs=_strings(value, "impact_item_refs"),
            actions=tuple(
                BrowserAction.from_dict(item) for item in _array(value, "actions", required=False)
            ),
            assertions=tuple(
                BrowserAssertion.from_dict(item) for item in _array(value, "assertions")
            ),
            redaction_locators=tuple(
                BrowserLocator.from_dict(item)
                for item in _array(value, "redaction_locators", required=False)
            ),
            preflight_assertions=tuple(
                BrowserAssertion.from_dict(item)
                for item in _array(value, "preflight_assertions", required=False)
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "trigger_path": self.trigger_path,
            "impact_item_refs": list(self.impact_item_refs),
            "actions": [item.to_dict() for item in self.actions],
            "assertions": [item.to_dict() for item in self.assertions],
            "redaction_locators": [item.to_dict() for item in self.redaction_locators],
            "preflight_assertions": [item.to_dict() for item in self.preflight_assertions],
        }


@dataclass(frozen=True, slots=True)
class BrowserExecutionManifest:
    manifest_id: str
    plan_id: str
    project_id: str
    browser_name: str
    browser_channel: str | None
    headless: bool
    viewport_width: int
    viewport_height: int
    review_status: str
    reviewed_by: str | None
    scenarios: tuple[BrowserScenarioSpec, ...]
    ui_knowledge_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.manifest_id, self.plan_id, self.project_id)):
            raise ValueError("Browser Manifest identity must not be blank")
        if self.browser_name not in {"chromium", "firefox", "webkit"}:
            raise ValueError("Browser Manifest browser_name is invalid")
        if self.browser_channel is not None and (
            self.browser_name != "chromium" or self.browser_channel not in {"chrome", "msedge"}
        ):
            raise ValueError("Browser channel must be chrome/msedge on chromium")
        if not 320 <= self.viewport_width <= 3840 or not 240 <= self.viewport_height <= 2160:
            raise ValueError("Browser viewport is outside the safe range")
        if self.review_status not in {"draft", "approved", "rejected"}:
            raise ValueError("Browser Manifest review_status is invalid")
        if (self.review_status == "draft") != (self.reviewed_by is None):
            raise ValueError("Reviewed Browser Manifest requires reviewed_by; draft forbids it")
        if not self.scenarios:
            raise ValueError("Browser Manifest requires Scenario specs")
        _unique((item.scenario_id for item in self.scenarios), "Browser Manifest Scenario IDs")
        target_refs = tuple(
            locator.target_ref
            for scenario in self.scenarios
            for locator in (
                *(action.locator for action in scenario.actions),
                *(assertion.locator for assertion in scenario.assertions),
                *(assertion.locator for assertion in scenario.preflight_assertions),
                *scenario.redaction_locators,
            )
            if locator.target_ref is not None
        )
        if target_refs and self.ui_knowledge_snapshot_id is None:
            raise ValueError("Business target Locators require ui_knowledge_snapshot_id")

    @classmethod
    def from_dict(cls, raw: object) -> BrowserExecutionManifest:
        value = _object(raw, "Browser Manifest")
        _exact_keys(
            value,
            {
                "manifest_id",
                "plan_id",
                "project_id",
                "browser",
                "review_status",
                "reviewed_by",
                "ui_knowledge_snapshot_id",
                "scenarios",
            },
            "Browser Manifest",
        )
        browser = _object(value.get("browser"), "Browser config")
        _exact_keys(browser, {"name", "channel", "headless", "viewport"}, "Browser config")
        viewport = _object(browser.get("viewport"), "Browser viewport")
        _exact_keys(viewport, {"width", "height"}, "Browser viewport")
        return cls(
            manifest_id=_string(value, "manifest_id"),
            plan_id=_string(value, "plan_id"),
            project_id=_string(value, "project_id"),
            browser_name=_string(browser, "name"),
            browser_channel=_optional_string(browser, "channel"),
            headless=_boolean(browser, "headless", default=True),
            viewport_width=_integer(viewport, "width"),
            viewport_height=_integer(viewport, "height"),
            review_status=_string(value, "review_status"),
            reviewed_by=_optional_string(value, "reviewed_by"),
            scenarios=tuple(
                BrowserScenarioSpec.from_dict(item) for item in _array(value, "scenarios")
            ),
            ui_knowledge_snapshot_id=_optional_string(value, "ui_knowledge_snapshot_id"),
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "manifest_id": self.manifest_id,
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "browser": {
                "name": self.browser_name,
                "channel": self.browser_channel,
                "headless": self.headless,
                "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            },
            "review_status": self.review_status,
            "reviewed_by": self.reviewed_by,
            "scenarios": [item.to_dict() for item in self.scenarios],
        }
        if self.ui_knowledge_snapshot_id is not None:
            result["ui_knowledge_snapshot_id"] = self.ui_knowledge_snapshot_id
        return result


def _validate_trigger_path(value: str) -> None:
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError("Browser trigger_path must be an origin-relative path")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError("Browser trigger_path cannot change origin or contain a fragment")


def _object(raw: object, label: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], raw)


def _exact_keys(value: dict[str, object], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} has unknown fields: {unknown}")


def _string(
    value: dict[str, object], key: str, *, default: str | None = None, blank: bool = False
) -> str:
    item = value.get(key, default)
    if not isinstance(item, str) or (not blank and not item.strip()):
        raise ValueError(f"{key} must be a string")
    return item


def _optional_string(value: dict[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be null or a non-blank string")
    return item


def _boolean(value: dict[str, object], key: str, *, default: bool) -> bool:
    item = value.get(key, default)
    if not isinstance(item, bool):
        raise ValueError(f"{key} must be a boolean")
    return item


def _integer(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValueError(f"{key} must be an integer")
    return item


def _array(value: dict[str, object], key: str, *, required: bool = True) -> list[object]:
    item = value.get(key, [])
    if not isinstance(item, list) or (required and not item):
        raise ValueError(
            f"{key} must be a non-empty array" if required else f"{key} must be an array"
        )
    return cast(list[object], item)


def _strings(value: dict[str, object], key: str) -> tuple[str, ...]:
    values = _array(value, key)
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise ValueError(f"{key} must contain non-blank strings")
    return tuple(cast(list[str], values))


def _unique(values: Iterable[object], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")


def _unique_non_blank(values: tuple[str, ...], label: str) -> None:
    if not values or len(values) != len(set(values)) or any(not value.strip() for value in values):
        raise ValueError(f"{label} must be non-empty, unique, and non-blank")
