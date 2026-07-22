"""Restricted Playwright observer for reviewable UI Knowledge enrichment."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Browser, Locator, Page, ViewportSize, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from operamind.domain import (
    BrowserLocator,
    LocatorStrategy,
    UiKnowledgeSnapshot,
    UiLocatorObservationStatus,
    UiRuntimeLocatorObservation,
    UiRuntimeObservationEvidence,
    UiRuntimeObservationIssue,
    UiRuntimeObservationMerger,
    UiRuntimeObservationResult,
    runtime_candidate_id,
    runtime_observation_id,
)
from operamind.infrastructure.browser.playwright import LocalEvidenceStore, _locator


class UiKnowledgeRuntimeObserver(Protocol):
    def observe(
        self,
        *,
        source: UiKnowledgeSnapshot,
        base_url: str,
        observation_run_id: str,
        result_snapshot_id: str,
        result_snapshot_version: str,
        storage_state: Path | None = None,
    ) -> UiRuntimeObservationResult: ...


class PlaywrightUiKnowledgeRuntimeObserver:
    """Observe only finite Locator APIs; never evaluate page-provided JavaScript."""

    def __init__(
        self,
        *,
        browser_name: str = "chromium",
        browser_channel: str | None = "chrome",
        headless: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        timeout_ms: int = 5_000,
        navigation_timeout_ms: int = 10_000,
        evidence_store: LocalEvidenceStore | None = None,
    ) -> None:
        if browser_name not in {"chromium", "firefox", "webkit"}:
            raise ValueError("UI Knowledge Observer browser_name is invalid")
        if browser_channel is not None and (
            browser_name != "chromium" or browser_channel not in {"chrome", "msedge"}
        ):
            raise ValueError("UI Knowledge Observer browser channel is invalid")
        if not 320 <= viewport_width <= 3840 or not 240 <= viewport_height <= 2160:
            raise ValueError("UI Knowledge Observer viewport is outside the safe range")
        if not 100 <= timeout_ms <= 120_000 or not 100 <= navigation_timeout_ms <= 120_000:
            raise ValueError("Browser timeouts must be between 100 and 120000 milliseconds")
        self._browser_name = browser_name
        self._browser_channel = browser_channel
        self._headless = headless
        self._viewport: ViewportSize = {
            "width": viewport_width,
            "height": viewport_height,
        }
        self._timeout_ms = timeout_ms
        self._navigation_timeout_ms = navigation_timeout_ms
        self._evidence_store = evidence_store
        self._merger = UiRuntimeObservationMerger()

    def observe(
        self,
        *,
        source: UiKnowledgeSnapshot,
        base_url: str,
        observation_run_id: str,
        result_snapshot_id: str,
        result_snapshot_version: str,
        storage_state: Path | None = None,
    ) -> UiRuntimeObservationResult:
        if any(
            not value.strip()
            for value in (
                observation_run_id,
                result_snapshot_id,
                result_snapshot_version,
            )
        ):
            raise ValueError("UI Knowledge Observation identity must not be blank")
        origin = _validate_origin(base_url)
        try:
            resolved_storage = _resolve_storage_state(storage_state)
        except (OSError, ValueError):
            return _blocked_result("storage_state_invalid", "Storage state was unavailable.")
        observations: list[UiRuntimeLocatorObservation] = []
        evidence: list[UiRuntimeObservationEvidence] = []
        issues: list[UiRuntimeObservationIssue] = []
        partial = False
        try:
            with sync_playwright() as playwright:
                browser_type = {
                    "chromium": playwright.chromium,
                    "firefox": playwright.firefox,
                    "webkit": playwright.webkit,
                }[self._browser_name]
                browser = browser_type.launch(
                    headless=self._headless,
                    channel=self._browser_channel,
                )
                try:
                    for target in source.targets:
                        if target.trigger_path is None:
                            partial = True
                            issues.append(
                                UiRuntimeObservationIssue(
                                    target.target_ref,
                                    "trigger_path_missing",
                                    "Runtime observation requires an approved Trigger Path.",
                                )
                            )
                            continue
                        (
                            target_observations,
                            target_issues,
                            target_evidence,
                            navigated,
                        ) = self._observe_target(
                            browser=browser,
                            origin=origin,
                            source=source,
                            target_ref=target.target_ref,
                            trigger_path=target.trigger_path,
                            observation_run_id=observation_run_id,
                            storage_state=resolved_storage,
                        )
                        observations.extend(target_observations)
                        issues.extend(target_issues)
                        if target_evidence is not None:
                            evidence.append(target_evidence)
                        partial = partial or not navigated
                finally:
                    browser.close()
        except PlaywrightError:
            return _blocked_result(
                "browser_runtime_unavailable",
                "Browser runtime or target deployment was unavailable.",
            )
        snapshot = self._merger.merge(
            source=source,
            observations=tuple(observations),
            result_snapshot_id=result_snapshot_id,
            result_snapshot_version=result_snapshot_version,
        )
        return UiRuntimeObservationResult(
            status="partial" if partial else "completed",
            snapshot=snapshot,
            observations=tuple(observations),
            issues=tuple(issues),
            evidence=tuple(evidence),
        )

    def _observe_target(
        self,
        *,
        browser: Browser,
        origin: str,
        source: UiKnowledgeSnapshot,
        target_ref: str,
        trigger_path: str,
        observation_run_id: str,
        storage_state: str | None,
    ) -> tuple[
        tuple[UiRuntimeLocatorObservation, ...],
        tuple[UiRuntimeObservationIssue, ...],
        UiRuntimeObservationEvidence | None,
        bool,
    ]:
        target = next(item for item in source.targets if item.target_ref == target_ref)
        context = browser.new_context(viewport=self._viewport, storage_state=storage_state)
        context.set_default_timeout(self._timeout_ms)
        page = context.new_page()
        page.set_default_navigation_timeout(self._navigation_timeout_ms)
        destination = urljoin(f"{origin}/", trigger_path.lstrip("/"))
        if _origin(destination) != origin:
            context.close()
            raise ValueError("UI Knowledge Trigger Path escaped approved origin")
        try:
            try:
                response = page.goto(destination, wait_until="domcontentloaded")
                if response is not None and response.status >= 400:
                    raise PlaywrightError("Trigger Path returned an error response")
            except (PlaywrightTimeoutError, PlaywrightError):
                return (
                    tuple(
                        _navigation_failed_observation(
                            observation_run_id,
                            target_ref,
                            candidate.candidate_id,
                            candidate.locator,
                        )
                        for candidate in target.candidates
                    ),
                    (
                        UiRuntimeObservationIssue(
                            target_ref,
                            "trigger_path_unavailable",
                            "Target Trigger Path was unavailable during runtime observation.",
                        ),
                    ),
                    None,
                    False,
                )
            observations: list[UiRuntimeLocatorObservation] = []
            issues: list[UiRuntimeObservationIssue] = []
            screenshot_source: tuple[UiRuntimeLocatorObservation, Locator] | None = None
            known_keys = {_locator_key(candidate.locator) for candidate in target.candidates}
            discovered_keys: set[tuple[object, ...]] = set()
            for candidate in target.candidates:
                observation, locator = _observe_locator(
                    page=page,
                    run_id=observation_run_id,
                    target_ref=target_ref,
                    candidate_id=candidate.candidate_id,
                    locator_value=candidate.locator,
                    discovered=False,
                )
                observations.append(observation)
                if observation.status is not UiLocatorObservationStatus.UNIQUE_VISIBLE:
                    issues.append(
                        _candidate_issue(target_ref, candidate.candidate_id, observation.status)
                    )
                    continue
                if screenshot_source is None:
                    screenshot_source = (observation, locator)
                for discovered in _discover_semantic_locators(locator):
                    key = _locator_key(discovered)
                    if key in known_keys or key in discovered_keys:
                        continue
                    discovered_keys.add(key)
                    discovered_id = runtime_candidate_id(target_ref, discovered)
                    discovered_observation, _ = _observe_locator(
                        page=page,
                        run_id=observation_run_id,
                        target_ref=target_ref,
                        candidate_id=discovered_id,
                        locator_value=discovered,
                        discovered=True,
                    )
                    observations.append(discovered_observation)
            evidence = self._capture_target_evidence(
                source=source,
                observation_run_id=observation_run_id,
                target_ref=target_ref,
                screenshot_source=screenshot_source,
            )
            if self._evidence_store is not None and evidence is None:
                issues.append(
                    UiRuntimeObservationIssue(
                        target_ref,
                        "evidence_screenshot_unavailable",
                        "A review screenshot could not be captured for the target.",
                    )
                )
            complete = self._evidence_store is None or evidence is not None
            return tuple(observations), tuple(issues), evidence, complete
        finally:
            context.close()

    def _capture_target_evidence(
        self,
        *,
        source: UiKnowledgeSnapshot,
        observation_run_id: str,
        target_ref: str,
        screenshot_source: tuple[UiRuntimeLocatorObservation, Locator] | None,
    ) -> UiRuntimeObservationEvidence | None:
        if self._evidence_store is None or screenshot_source is None:
            return None
        observation, locator = screenshot_source
        evidence_id = _observation_evidence_id(observation_run_id, target_ref)
        try:
            screenshot = locator.screenshot(type="png")
            stored = self._evidence_store.store_screenshot(
                project_id=source.project_id,
                run_id=observation_run_id,
                evidence_id=evidence_id,
                scenario_id=evidence_id,
                content=screenshot,
            )
        except (OSError, PlaywrightError, ValueError):
            return None
        return UiRuntimeObservationEvidence(
            evidence_id=stored.evidence_id,
            observation_id=observation.observation_id,
            target_ref=target_ref,
            evidence_ref=stored.evidence_ref,
            content_digest=stored.content_digest,
            sanitized=stored.sanitized,
        )


def _observe_locator(
    *,
    page: Page,
    run_id: str,
    target_ref: str,
    candidate_id: str,
    locator_value: BrowserLocator,
    discovered: bool,
) -> tuple[UiRuntimeLocatorObservation, Locator]:
    locator = _locator(page, locator_value)
    match_count = locator.count()
    visible_count = sum(1 for index in range(match_count) if locator.nth(index).is_visible())
    if match_count == 0:
        status = UiLocatorObservationStatus.NOT_FOUND
    elif match_count > 1:
        status = UiLocatorObservationStatus.AMBIGUOUS
    elif visible_count == 1:
        status = UiLocatorObservationStatus.UNIQUE_VISIBLE
    else:
        status = UiLocatorObservationStatus.HIDDEN
    return (
        UiRuntimeLocatorObservation(
            observation_id=runtime_observation_id(run_id, target_ref, candidate_id),
            target_ref=target_ref,
            candidate_id=candidate_id,
            locator=locator_value,
            status=status,
            match_count=match_count,
            visible_count=visible_count,
            discovered=discovered,
        ),
        locator,
    )


def _discover_semantic_locators(locator: Locator) -> tuple[BrowserLocator, ...]:
    result: list[BrowserLocator] = []
    aria_label = locator.get_attribute("aria-label")
    explicit_role = locator.get_attribute("role")
    test_id = locator.get_attribute("data-testid")
    placeholder = locator.get_attribute("placeholder")
    if aria_label is not None and aria_label.strip():
        result.append(BrowserLocator(strategy=LocatorStrategy.LABEL, value=aria_label.strip()))
        if explicit_role is not None and explicit_role.strip():
            result.append(
                BrowserLocator(
                    strategy=LocatorStrategy.ROLE,
                    value=explicit_role.strip(),
                    name=aria_label.strip(),
                )
            )
    if test_id is not None and test_id.strip():
        result.append(BrowserLocator(strategy=LocatorStrategy.TEST_ID, value=test_id.strip()))
    if placeholder is not None and placeholder.strip():
        result.append(
            BrowserLocator(strategy=LocatorStrategy.PLACEHOLDER, value=placeholder.strip())
        )
    return tuple(result)


def _candidate_issue(
    target_ref: str,
    candidate_id: str,
    status: UiLocatorObservationStatus,
) -> UiRuntimeObservationIssue:
    return UiRuntimeObservationIssue(
        target_ref=target_ref,
        code=f"candidate_{status.value}",
        message=f"Locator candidate {candidate_id} observed status {status.value}.",
    )


def _navigation_failed_observation(
    run_id: str,
    target_ref: str,
    candidate_id: str,
    locator: BrowserLocator,
) -> UiRuntimeLocatorObservation:
    return UiRuntimeLocatorObservation(
        observation_id=runtime_observation_id(run_id, target_ref, candidate_id),
        target_ref=target_ref,
        candidate_id=candidate_id,
        locator=locator,
        status=UiLocatorObservationStatus.NAVIGATION_FAILED,
        match_count=0,
        visible_count=0,
        discovered=False,
    )


def _blocked_result(code: str, message: str) -> UiRuntimeObservationResult:
    return UiRuntimeObservationResult(
        status="blocked",
        snapshot=None,
        observations=(),
        issues=(UiRuntimeObservationIssue("runtime", code, message),),
    )


def _observation_evidence_id(run_id: str, target_ref: str) -> str:
    material = f"{run_id}\0{target_ref}\0screenshot"
    return f"ui-knowledge-evidence-{hashlib.sha256(material.encode()).hexdigest()[:20]}"


def _locator_key(locator: BrowserLocator) -> tuple[object, ...]:
    return (locator.strategy, locator.value, locator.name, locator.exact)


def _validate_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("UI Knowledge base_url must be an absolute HTTP(S) origin")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("UI Knowledge base_url cannot contain credentials, query, or fragment")
    return f"{parsed.scheme}://{parsed.netloc}"


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"


def _resolve_storage_state(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("UI Knowledge storage_state must be a file")
    return str(resolved)
