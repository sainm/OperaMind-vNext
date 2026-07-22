"""Restricted Playwright runner with deterministic sanitized Evidence output."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    expect,
    sync_playwright,
)
from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from operamind.domain import (
    BrowserAction,
    BrowserActionKind,
    BrowserAssertion,
    BrowserAssertionKind,
    BrowserExecutionManifest,
    BrowserFailureCategory,
    BrowserLocator,
    BrowserScenarioSpec,
    BrowserValue,
    LocatorStrategy,
)

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|password|passwd|token|secret|cookie|set-cookie)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_SENSITIVE_KEY = re.compile(r"(?i)^(authorization|password|passwd|token|secret|cookie|set-cookie)$")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_ROUTE_RESOURCE_TYPES = frozenset({"document", "fetch", "xhr"})
_FORM_ROUTE_INIT_SCRIPT = """
document.addEventListener("submit", event => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const target = new URL(form.action || window.location.href, window.location.href);
  void window.__operamindRouteForm({
    method: (form.method || "GET").toUpperCase(),
    path: target.pathname || "/"
  });
}, true);
"""


@dataclass(frozen=True, slots=True)
class StoredBrowserEvidence:
    evidence_id: str
    scenario_id: str
    evidence_type: str
    evidence_ref: str
    content_digest: str
    sanitized: bool = True


@dataclass(frozen=True, slots=True)
class BrowserScenarioOutcome:
    scenario_id: str
    status: str
    impact_item_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    failure_category: str
    summary: str


@dataclass(frozen=True, slots=True)
class BrowserExecutionOutput:
    scenario_results: tuple[BrowserScenarioOutcome, ...]
    evidence: tuple[StoredBrowserEvidence, ...]


@dataclass(frozen=True, slots=True)
class BrowserPreflightObservation:
    check_type: str
    status: str
    evidence_ref: str | None
    reason: str | None = None


class BrowserPreflightProbe(Protocol):
    def inspect(
        self,
        *,
        manifest: BrowserExecutionManifest,
        base_url: str,
        attempt_id: str,
        storage_state: Path | None = None,
    ) -> tuple[BrowserPreflightObservation, ...]: ...


class BrowserExecutor(Protocol):
    def execute(
        self,
        *,
        manifest: BrowserExecutionManifest,
        base_url: str,
        run_id: str,
        storage_state: Path | None = None,
    ) -> BrowserExecutionOutput: ...


class PlaywrightBrowserPreflightProbe:
    """Inspect the five required readiness dimensions without mutating application data."""

    def __init__(
        self,
        *,
        timeout_ms: int = 5_000,
        navigation_timeout_ms: int = 10_000,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if not 100 <= timeout_ms <= 120_000 or not 100 <= navigation_timeout_ms <= 120_000:
            raise ValueError("Browser timeouts must be between 100 and 120000 milliseconds")
        self._timeout_ms = timeout_ms
        self._navigation_timeout_ms = navigation_timeout_ms
        self._environment = environment if environment is not None else os.environ

    def inspect(
        self,
        *,
        manifest: BrowserExecutionManifest,
        base_url: str,
        attempt_id: str,
        storage_state: Path | None = None,
    ) -> tuple[BrowserPreflightObservation, ...]:
        if not attempt_id.strip():
            raise ValueError("Browser Preflight Attempt ID must not be blank")
        origin = _validate_base_url(base_url)
        storage_error = False
        try:
            resolved_storage = _storage_state(storage_state)
        except (OSError, ValueError):
            resolved_storage = None
            storage_error = True
        requires_auth = any(
            assertion.failure_category is BrowserFailureCategory.AUTHENTICATION
            for scenario in manifest.scenarios
            for assertion in scenario.preflight_assertions
        )
        if storage_error or (requires_auth and resolved_storage is None):
            auth = _preflight_observation(
                attempt_id,
                "authentication",
                "blocked",
                "Authentication Preflight requires an approved storage state.",
            )
        else:
            auth = _preflight_observation(attempt_id, "authentication", "passed", None)
        statuses = {
            "environment": _preflight_observation(attempt_id, "environment", "passed", None),
            "authentication": auth,
            "test_data": _preflight_observation(attempt_id, "test_data", "passed", None),
            "trigger_path": _preflight_observation(attempt_id, "trigger_path", "passed", None),
            "locator": _preflight_observation(attempt_id, "locator", "passed", None),
        }
        try:
            with sync_playwright() as playwright:
                browser = PlaywrightBrowserExecutor._launch(playwright, manifest)
                try:
                    self._inspect_browser(
                        browser=browser,
                        manifest=manifest,
                        origin=origin,
                        storage_state=resolved_storage,
                        attempt_id=attempt_id,
                        statuses=statuses,
                    )
                finally:
                    browser.close()
        except PlaywrightError:
            statuses["environment"] = _preflight_observation(
                attempt_id,
                "environment",
                "blocked",
                "Browser runtime or target origin was unavailable.",
            )
            for check_type in ("authentication", "test_data", "trigger_path", "locator"):
                if statuses[check_type].status == "passed":
                    statuses[check_type] = _preflight_observation(
                        attempt_id,
                        check_type,
                        "blocked",
                        "Environment readiness was not established.",
                    )
        return tuple(statuses[name] for name in sorted(statuses))

    def _inspect_browser(
        self,
        *,
        browser: Browser,
        manifest: BrowserExecutionManifest,
        origin: str,
        storage_state: str | None,
        attempt_id: str,
        statuses: dict[str, BrowserPreflightObservation],
    ) -> None:
        context = browser.new_context(
            viewport={"width": manifest.viewport_width, "height": manifest.viewport_height},
            storage_state=storage_state,
        )
        context.set_default_timeout(self._timeout_ms)
        try:
            origin_page = context.new_page()
            origin_page.set_default_navigation_timeout(self._navigation_timeout_ms)
            response = origin_page.goto(origin, wait_until="domcontentloaded")
            if response is not None and response.status >= 500:
                raise PlaywrightError("Target origin returned a server error")
            origin_page.close()
            for scenario in manifest.scenarios:
                page = context.new_page()
                page.set_default_navigation_timeout(self._navigation_timeout_ms)
                target = urljoin(f"{origin}/", scenario.trigger_path.lstrip("/"))
                try:
                    response = page.goto(target, wait_until="domcontentloaded")
                    if response is not None and response.status >= 400:
                        raise PlaywrightError("Trigger Path returned an error response")
                except (PlaywrightTimeoutError, PlaywrightError):
                    statuses["trigger_path"] = _preflight_observation(
                        attempt_id,
                        "trigger_path",
                        "blocked",
                        f"Trigger Path was unavailable for Scenario {scenario.scenario_id}.",
                    )
                    statuses["locator"] = _preflight_observation(
                        attempt_id,
                        "locator",
                        "blocked",
                        "Locator readiness cannot be checked before Trigger Path readiness.",
                    )
                    page.close()
                    continue
                for assertion in scenario.preflight_assertions:
                    category = assertion.failure_category.value
                    if statuses[category].status != "passed":
                        continue
                    try:
                        self._perform_assertion(page, assertion)
                    except (AssertionError, PlaywrightTimeoutError, PlaywrightError, ValueError):
                        statuses[category] = _preflight_observation(
                            attempt_id,
                            category,
                            "blocked",
                            f"Preflight Assertion failed: {assertion.assertion_id}",
                        )
                try:
                    self._verify_locators(page, scenario)
                except (PlaywrightTimeoutError, PlaywrightError, ValueError):
                    statuses["locator"] = _preflight_observation(
                        attempt_id,
                        "locator",
                        "blocked",
                        f"Locator was not reliable for Scenario {scenario.scenario_id}.",
                    )
                page.close()
        finally:
            context.close()

    def _perform_assertion(self, page: Page, assertion: BrowserAssertion) -> None:
        _perform_browser_assertion(
            page=page,
            assertion=assertion,
            timeout_ms=self._timeout_ms,
            environment=self._environment,
        )

    def _verify_locators(self, page: Page, scenario: BrowserScenarioSpec) -> None:
        single = [action.locator for action in scenario.actions]
        single.extend(
            assertion.locator
            for assertion in (*scenario.preflight_assertions, *scenario.assertions)
            if assertion.kind
            not in {BrowserAssertionKind.COUNT_EQUALS, BrowserAssertionKind.HIDDEN}
        )
        for locator_value in single:
            locator = _locator(page, locator_value)
            expect(locator).to_have_count(1, timeout=self._timeout_ms)
            expect(locator).to_be_visible(timeout=self._timeout_ms)


class LocalEvidenceStore:
    """Write sanitized Evidence under one approved root and return opaque refs."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def store_json(
        self,
        *,
        project_id: str,
        run_id: str,
        evidence_id: str,
        scenario_id: str,
        evidence_type: str,
        payload: object,
    ) -> StoredBrowserEvidence:
        encoded = json.dumps(
            _sanitize_json(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode()
        return self._store(
            project_id=project_id,
            run_id=run_id,
            evidence_id=evidence_id,
            scenario_id=scenario_id,
            evidence_type=evidence_type,
            extension="json",
            content=encoded,
        )

    def store_screenshot(
        self,
        *,
        project_id: str,
        run_id: str,
        evidence_id: str,
        scenario_id: str,
        content: bytes,
    ) -> StoredBrowserEvidence:
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Browser screenshot Evidence must be PNG")
        return self._store(
            project_id=project_id,
            run_id=run_id,
            evidence_id=evidence_id,
            scenario_id=scenario_id,
            evidence_type="screenshot",
            extension="png",
            content=content,
        )

    def _store(
        self,
        *,
        project_id: str,
        run_id: str,
        evidence_id: str,
        scenario_id: str,
        evidence_type: str,
        extension: str,
        content: bytes,
    ) -> StoredBrowserEvidence:
        for value in (project_id, run_id, evidence_id, scenario_id, evidence_type, extension):
            if _SAFE_COMPONENT.fullmatch(value) is None:
                raise ValueError(f"Unsafe Browser Evidence path component: {value!r}")
        directory = (self._root / project_id / run_id).resolve()
        if not directory.is_relative_to(self._root):
            raise ValueError("Browser Evidence path escapes approved root")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{evidence_id}.{extension}"
        temporary = directory / f".{evidence_id}.{extension}.tmp"
        digest = hashlib.sha256(content).hexdigest()
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Browser Evidence identity has different content: {evidence_id}")
        else:
            temporary.write_bytes(content)
            temporary.replace(target)
        return StoredBrowserEvidence(
            evidence_id=evidence_id,
            scenario_id=scenario_id,
            evidence_type=evidence_type,
            evidence_ref=f"evidence://{project_id}/{run_id}/{evidence_id}",
            content_digest=digest,
        )


class PlaywrightBrowserExecutor:
    """Run only the reviewed DSL; never evaluate arbitrary JavaScript or fixed sleeps."""

    def __init__(
        self,
        *,
        evidence_store: LocalEvidenceStore,
        timeout_ms: int = 10_000,
        navigation_timeout_ms: int = 20_000,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if not 100 <= timeout_ms <= 120_000 or not 100 <= navigation_timeout_ms <= 120_000:
            raise ValueError("Browser timeouts must be between 100 and 120000 milliseconds")
        self._evidence_store = evidence_store
        self._timeout_ms = timeout_ms
        self._navigation_timeout_ms = navigation_timeout_ms
        self._environment = environment if environment is not None else os.environ

    def execute(
        self,
        *,
        manifest: BrowserExecutionManifest,
        base_url: str,
        run_id: str,
        storage_state: Path | None = None,
    ) -> BrowserExecutionOutput:
        origin = _validate_base_url(base_url)
        resolved_storage = _storage_state(storage_state)
        evidence: list[StoredBrowserEvidence] = []
        outcomes: list[BrowserScenarioOutcome] = []
        try:
            with sync_playwright() as playwright:
                browser = self._launch(playwright, manifest)
                try:
                    for scenario in manifest.scenarios:
                        outcome, observed = self._run_scenario(
                            browser=browser,
                            manifest=manifest,
                            scenario=scenario,
                            origin=origin,
                            run_id=run_id,
                            storage_state=resolved_storage,
                        )
                        outcomes.append(outcome)
                        evidence.extend(observed)
                finally:
                    browser.close()
        except PlaywrightError:
            return BrowserExecutionOutput(
                scenario_results=tuple(
                    BrowserScenarioOutcome(
                        scenario_id=scenario.scenario_id,
                        status="blocked",
                        impact_item_refs=scenario.impact_item_refs,
                        evidence_refs=(),
                        failure_category=BrowserFailureCategory.ENVIRONMENT.value,
                        summary="Browser runtime was unavailable.",
                    )
                    for scenario in manifest.scenarios
                ),
                evidence=(),
            )
        return BrowserExecutionOutput(tuple(outcomes), tuple(evidence))

    @staticmethod
    def _launch(playwright: Playwright, manifest: BrowserExecutionManifest) -> Browser:
        browser_type = {
            "chromium": playwright.chromium,
            "firefox": playwright.firefox,
            "webkit": playwright.webkit,
        }[manifest.browser_name]
        return browser_type.launch(
            headless=manifest.headless,
            channel=manifest.browser_channel,
        )

    def _run_scenario(
        self,
        *,
        browser: Browser,
        manifest: BrowserExecutionManifest,
        scenario: BrowserScenarioSpec,
        origin: str,
        run_id: str,
        storage_state: str | None,
    ) -> tuple[BrowserScenarioOutcome, tuple[StoredBrowserEvidence, ...]]:
        context: BrowserContext | None = None
        records: list[StoredBrowserEvidence] = []
        steps: list[dict[str, object]] = []
        assertion_results: list[dict[str, object]] = []
        network: list[dict[str, object]] = []
        route_observations: list[dict[str, object]] = []
        active_action: dict[str, str | None] = {"action_id": None, "route_source_ref": None}
        failure_category: BrowserFailureCategory | None = None
        failure_summary: str | None = None
        screenshot: bytes | None = None
        try:
            context = browser.new_context(
                viewport={
                    "width": manifest.viewport_width,
                    "height": manifest.viewport_height,
                },
                storage_state=storage_state,
            )
            context.set_default_timeout(self._timeout_ms)
            page = context.new_page()
            page.set_default_navigation_timeout(self._navigation_timeout_ms)
            page.on("response", lambda response: network.append(_response_summary(response)))
            page.on(
                "request",
                lambda request: _capture_request_routes(
                    request=request,
                    run_id=run_id,
                    scenario_id=scenario.scenario_id,
                    active_action=active_action,
                    observations=route_observations,
                ),
            )
            page.expose_binding(
                "__operamindRouteForm",
                lambda _source, payload: _capture_form_route(
                    payload=payload,
                    run_id=run_id,
                    scenario_id=scenario.scenario_id,
                    active_action=active_action,
                    observations=route_observations,
                ),
            )
            page.add_init_script(_FORM_ROUTE_INIT_SCRIPT)
            target = urljoin(f"{origin}/", scenario.trigger_path.lstrip("/"))
            if _origin(target) != origin:
                raise ValueError("Browser trigger_path escaped the approved origin")
            try:
                page.goto(target, wait_until="domcontentloaded")
            except (PlaywrightTimeoutError, PlaywrightError):
                failure_category = BrowserFailureCategory.ENVIRONMENT
                failure_summary = "The approved Trigger Path was unavailable."
            if failure_category is None:
                for action in scenario.actions:
                    try:
                        active_action["action_id"] = action.action_id
                        active_action["route_source_ref"] = action.route_source_ref
                        self._perform_action(page, action)
                        steps.append(
                            {
                                "action_id": action.action_id,
                                "kind": action.kind.value,
                                "status": "passed",
                            }
                        )
                    except (PlaywrightTimeoutError, PlaywrightError, ValueError):
                        steps.append(
                            {
                                "action_id": action.action_id,
                                "kind": action.kind.value,
                                "status": "failed",
                            }
                        )
                        failure_category = BrowserFailureCategory.LOCATOR
                        failure_summary = f"Approved Action failed: {action.action_id}"
                        break
                    finally:
                        active_action["action_id"] = None
                        active_action["route_source_ref"] = None
            if failure_category is None:
                for assertion in scenario.assertions:
                    try:
                        self._perform_assertion(page, assertion)
                        assertion_results.append(
                            {
                                "assertion_id": assertion.assertion_id,
                                "kind": assertion.kind.value,
                                "status": "passed",
                            }
                        )
                    except (
                        AssertionError,
                        PlaywrightTimeoutError,
                        PlaywrightError,
                        ValueError,
                    ):
                        assertion_results.append(
                            {
                                "assertion_id": assertion.assertion_id,
                                "kind": assertion.kind.value,
                                "status": "failed",
                                "message": "Assertion did not satisfy its approved condition.",
                            }
                        )
                        if failure_category is None:
                            failure_category = assertion.failure_category
                            failure_summary = f"Assertion failed: {assertion.assertion_id}"
            try:
                redactions = list(scenario.redaction_locators)
                redactions.extend(
                    action.locator
                    for action in scenario.actions
                    if action.value is not None and action.value.source == "env"
                )
                masks = [_locator(page, locator) for locator in redactions]
                screenshot = page.screenshot(full_page=True, mask=masks)
            except (PlaywrightTimeoutError, PlaywrightError):
                failure_category = BrowserFailureCategory.ENVIRONMENT
                failure_summary = "Sanitized Screenshot capture failed."
        except (PlaywrightTimeoutError, PlaywrightError):
            failure_category = BrowserFailureCategory.ENVIRONMENT
            failure_summary = "Browser Context creation or execution failed."
        finally:
            if context is not None:
                context.close()

        evidence_ids = _evidence_ids(run_id, scenario.scenario_id)
        if screenshot is not None:
            records.append(
                self._evidence_store.store_screenshot(
                    project_id=manifest.project_id,
                    run_id=run_id,
                    evidence_id=evidence_ids["screenshot"],
                    scenario_id=scenario.scenario_id,
                    content=screenshot,
                )
            )
        records.append(
            self._evidence_store.store_json(
                project_id=manifest.project_id,
                run_id=run_id,
                evidence_id=evidence_ids["assertion"],
                scenario_id=scenario.scenario_id,
                evidence_type="assertion",
                payload={"assertions": assertion_results},
            )
        )
        records.append(
            self._evidence_store.store_json(
                project_id=manifest.project_id,
                run_id=run_id,
                evidence_id=evidence_ids["step_log"],
                scenario_id=scenario.scenario_id,
                evidence_type="step_log",
                payload={"actions": steps},
            )
        )
        records.append(
            self._evidence_store.store_json(
                project_id=manifest.project_id,
                run_id=run_id,
                evidence_id=evidence_ids["network_summary"],
                scenario_id=scenario.scenario_id,
                evidence_type="network_summary",
                payload={"responses": network, "route_observations": route_observations},
            )
        )
        passed = failure_category is None
        scenario_status = (
            "passed"
            if passed
            else "failed"
            if failure_category is BrowserFailureCategory.BUSINESS_ASSERTION
            else "blocked"
        )
        return (
            BrowserScenarioOutcome(
                scenario_id=scenario.scenario_id,
                status=scenario_status,
                impact_item_refs=scenario.impact_item_refs,
                evidence_refs=tuple(item.evidence_id for item in records),
                failure_category=("none" if failure_category is None else failure_category.value),
                summary="All browser assertions passed."
                if passed
                else failure_summary or "Browser Scenario failed.",
            ),
            tuple(records),
        )

    def _perform_action(self, page: Page, action: BrowserAction) -> None:
        locator = _locator(page, action.locator)
        if action.kind is BrowserActionKind.CLICK:
            locator.click()
        elif action.kind is BrowserActionKind.FILL:
            locator.fill(self._resolve_value(_required_value(action.value)))
        elif action.kind is BrowserActionKind.SELECT_OPTION:
            locator.select_option(self._resolve_value(_required_value(action.value)))
        elif action.kind is BrowserActionKind.CHECK:
            locator.check()
        elif action.kind is BrowserActionKind.UNCHECK:
            locator.uncheck()

    def _perform_assertion(self, page: Page, assertion: BrowserAssertion) -> None:
        _perform_browser_assertion(
            page=page,
            assertion=assertion,
            timeout_ms=self._timeout_ms,
            environment=self._environment,
        )

    def _resolve_value(self, value: BrowserValue) -> str:
        if value.source == "literal":
            return value.value
        resolved = self._environment.get(value.value)
        if resolved is None or not resolved:
            raise ValueError(f"Required UI environment value is missing: {value.value}")
        return resolved


def _locator(page: Page, value: BrowserLocator) -> Locator:
    if value.target_ref is not None or value.strategy is None or value.value is None:
        raise ValueError("Business target Locator was not resolved from UI Knowledge")
    if value.strategy is LocatorStrategy.ROLE:
        return page.get_by_role(cast(Any, value.value), name=value.name, exact=value.exact)
    if value.strategy is LocatorStrategy.LABEL:
        return page.get_by_label(value.value, exact=value.exact)
    if value.strategy is LocatorStrategy.TEXT:
        return page.get_by_text(value.value, exact=value.exact)
    if value.strategy is LocatorStrategy.TEST_ID:
        return page.get_by_test_id(value.value)
    if value.strategy is LocatorStrategy.PLACEHOLDER:
        return page.get_by_placeholder(value.value, exact=value.exact)
    return page.locator(value.value)


def _perform_browser_assertion(
    *,
    page: Page,
    assertion: BrowserAssertion,
    timeout_ms: int,
    environment: Mapping[str, str],
) -> None:
    locator = _locator(page, assertion.locator)
    expected = assertion.expected

    def resolve(value: BrowserValue) -> str:
        return _resolve_browser_value(value, environment)

    if assertion.kind is BrowserAssertionKind.VISIBLE:
        expect(locator).to_be_visible(timeout=timeout_ms)
    elif assertion.kind is BrowserAssertionKind.HIDDEN:
        expect(locator).to_be_hidden(timeout=timeout_ms)
    elif assertion.kind is BrowserAssertionKind.TEXT_EQUALS:
        expect(locator).to_have_text(resolve(_required_value(expected)), timeout=timeout_ms)
    elif assertion.kind is BrowserAssertionKind.TEXT_CONTAINS:
        expect(locator).to_contain_text(resolve(_required_value(expected)), timeout=timeout_ms)
    elif assertion.kind is BrowserAssertionKind.VALUE_EQUALS:
        expect(locator).to_have_value(resolve(_required_value(expected)), timeout=timeout_ms)
    elif assertion.kind is BrowserAssertionKind.COUNT_EQUALS:
        count = int(resolve(_required_value(expected)))
        expect(locator).to_have_count(count, timeout=timeout_ms)
    elif assertion.kind is BrowserAssertionKind.CHECKED:
        expect(locator).to_be_checked(timeout=timeout_ms)
    elif assertion.kind is BrowserAssertionKind.UNCHECKED:
        expect(locator).not_to_be_checked(timeout=timeout_ms)


def _resolve_browser_value(value: BrowserValue, environment: Mapping[str, str]) -> str:
    if value.source == "literal":
        return value.value
    resolved = environment.get(value.value)
    if resolved is None or not resolved:
        raise ValueError(f"Required UI environment value is missing: {value.value}")
    return resolved


def _preflight_observation(
    attempt_id: str,
    check_type: str,
    status: str,
    reason: str | None,
) -> BrowserPreflightObservation:
    return BrowserPreflightObservation(
        check_type=check_type,
        status=status,
        evidence_ref=f"preflight://{attempt_id}/{check_type}",
        reason=reason,
    )


def _required_value(value: BrowserValue | None) -> BrowserValue:
    if value is None:
        raise RuntimeError("Validated Browser DSL lost a required value")
    return value


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Browser base_url must be an absolute HTTP(S) origin")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Browser base_url cannot contain credentials, query, or fragment")
    return f"{parsed.scheme}://{parsed.netloc}"


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"


def _storage_state(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Browser storage_state must be a file")
    return str(resolved)


def _response_summary(response: Any) -> dict[str, object]:
    parsed = urlsplit(str(response.url))
    return {
        "method": str(response.request.method),
        "path": parsed.path,
        "status": int(response.status),
    }


def _capture_request_routes(
    *,
    request: Any,
    run_id: str,
    scenario_id: str,
    active_action: Mapping[str, str | None],
    observations: list[dict[str, object]],
) -> None:
    if str(request.resource_type) not in _ROUTE_RESOURCE_TYPES:
        return
    parsed = urlsplit(str(request.url))
    if parsed.scheme not in {"http", "https"} or not parsed.path.startswith("/"):
        return
    _append_route_observation(
        observations=observations,
        run_id=run_id,
        scenario_id=scenario_id,
        event_kind="network_request",
        method=str(request.method).upper(),
        path=parsed.path,
        active_action=active_action,
    )
    if bool(request.is_navigation_request()):
        _append_route_observation(
            observations=observations,
            run_id=run_id,
            scenario_id=scenario_id,
            event_kind="navigation",
            method=str(request.method).upper(),
            path=parsed.path,
            active_action=active_action,
        )


def _capture_form_route(
    *,
    payload: object,
    run_id: str,
    scenario_id: str,
    active_action: Mapping[str, str | None],
    observations: list[dict[str, object]],
) -> None:
    if not isinstance(payload, dict):
        return
    method = payload.get("method")
    path = payload.get("path")
    if not isinstance(method, str) or not isinstance(path, str) or not path.startswith("/"):
        return
    _append_route_observation(
        observations=observations,
        run_id=run_id,
        scenario_id=scenario_id,
        event_kind="form_submission",
        method=method.upper(),
        path=urlsplit(path).path,
        active_action=active_action,
    )


def _append_route_observation(
    *,
    observations: list[dict[str, object]],
    run_id: str,
    scenario_id: str,
    event_kind: str,
    method: str,
    path: str,
    active_action: Mapping[str, str | None],
) -> None:
    sequence = len(observations) + 1
    material = "\0".join((run_id, scenario_id, str(sequence), event_kind, method, path))
    item: dict[str, object] = {
        "observation_id": f"route-observation-{hashlib.sha256(material.encode()).hexdigest()[:24]}",
        "scenario_id": scenario_id,
        "event_kind": event_kind,
        "method": method,
        "path": path,
    }
    if active_action.get("action_id") is not None:
        item["source_action_id"] = active_action["action_id"]
    if active_action.get("route_source_ref") is not None:
        item["source_route_ref"] = active_action["route_source_ref"]
    observations.append(item)


def _evidence_ids(run_id: str, scenario_id: str) -> dict[str, str]:
    return {
        evidence_type: _evidence_id(run_id, scenario_id, evidence_type)
        for evidence_type in ("screenshot", "assertion", "step_log", "network_summary")
    }


def _evidence_id(run_id: str, scenario_id: str, evidence_type: str) -> str:
    material = "\0".join((run_id, scenario_id, evidence_type))
    return f"evidence-{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _sanitize_json(value: object) -> object:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _SENSITIVE_KEY.fullmatch(str(key)) is not None
                else _sanitize_json(item)
            )
            for key, item in value.items()
        }
    return value


def _sanitize_text(value: str) -> str:
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)
