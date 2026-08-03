"""Evidence-producing executors for reviewed TestDataPlan channel bindings."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import IO, Any, Literal, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from operamind.application.test_data_execution import (
    TestDataExecutionEvidence,
    TestDataExecutionRequest,
    TestDataStepExecution,
)
from operamind.infrastructure.browser import LocalEvidenceStore, StoredBrowserEvidence

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_RESPONSE_LIMIT = 1_048_576


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str


class HttpTransport(Protocol):
    def send(
        self,
        *,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse: ...


class UrllibHttpTransport:
    """Small standard-library transport that returns HTTP errors as observations."""

    def send(
        self,
        *,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        request = Request(url=url, data=body, headers=dict(headers), method=method)
        try:
            opener = build_opener(_SameOriginRedirectHandler())
            with opener.open(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(_RESPONSE_LIMIT + 1),
                    final_url=response.geturl(),
                )
        except HTTPError as error:
            return HttpResponse(
                status_code=error.code,
                headers=dict(error.headers.items()),
                body=error.read(_RESPONSE_LIMIT + 1),
                final_url=error.geturl(),
            )
        except URLError as error:
            raise OSError(f"HTTP test data request failed: {error.reason}") from error


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only while the request remains on its approved origin."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        if _http_origin(req.full_url) != _http_origin(newurl):
            raise URLError("HTTP Test data redirect escaped the approved origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SafeHttpTestDataExecutor:
    """Call only an approved base origin and persist sanitized request/response Evidence."""

    def __init__(
        self,
        *,
        evidence_store: LocalEvidenceStore,
        transport: HttpTransport | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        if not 0.1 <= timeout_seconds <= 120:
            raise ValueError("HTTP Test data timeout must be between 0.1 and 120 seconds")
        self._evidence_store = evidence_store
        self._transport = transport or UrllibHttpTransport()
        self._timeout_seconds = timeout_seconds

    def execute(
        self,
        *,
        request: TestDataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> TestDataStepExecution:
        del variables
        if request.base_url is None:
            raise ValueError("HTTP Test data execution requires a base_url")
        method, path = _http_target(step, resolved_inputs)
        query = resolved_inputs.get("query", {})
        if not isinstance(query, Mapping):
            raise ValueError("HTTP Test data query must be an object")
        url = _target_url(request.base_url, path, query)
        payload = resolved_inputs.get("json")
        body = None
        if payload is not None:
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
        response = self._transport.send(
            method=method,
            url=url,
            body=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout_seconds=self._timeout_seconds,
        )
        if _http_origin(response.final_url) != _http_origin(url):
            raise OSError("HTTP Test data redirect escaped the approved origin")
        if len(response.body) > _RESPONSE_LIMIT:
            raise OSError("HTTP Test data response exceeded the Evidence size limit")
        decoded = _decode_response(response.body)
        request_evidence = self._store_json(
            request=request,
            flow_id=flow_id,
            step_id=str(step["step_id"]),
            phase=phase,
            evidence_type="request",
            payload={"method": method, "path": path, "query": dict(query), "json": payload},
        )
        response_evidence = self._store_json(
            request=request,
            flow_id=flow_id,
            step_id=str(step["step_id"]),
            phase=phase,
            evidence_type="response",
            payload={
                "status_code": response.status_code,
                "content_type": _header(response.headers, "content-type"),
                "body": decoded,
            },
        )
        failure = None
        if not 200 <= response.status_code < 300:
            failure = f"HTTP Test data request returned status {response.status_code}"
        return TestDataStepExecution(
            source_values={
                "response": decoded,
                "api": {"status_code": response.status_code, "body": decoded},
            },
            evidence=(request_evidence, response_evidence),
            failure_reason=failure,
        )

    def _store_json(
        self,
        *,
        request: TestDataExecutionRequest,
        flow_id: str,
        step_id: str,
        phase: str,
        evidence_type: str,
        payload: object,
    ) -> TestDataExecutionEvidence:
        evidence_id = _evidence_id(request.run_id, flow_id, step_id, phase, evidence_type)
        stored = self._evidence_store.store_json(
            project_id=request.project_id,
            run_id=request.run_id,
            evidence_id=evidence_id,
            scenario_id=_flow_component(flow_id),
            evidence_type=evidence_type,
            payload=payload,
        )
        return _execution_evidence(
            stored=stored,
            flow_id=flow_id,
            step_id=step_id,
            phase=phase,
        )


FixtureBinding = Callable[[Mapping[str, object]], Mapping[str, object]]
SqlBinding = Callable[[Mapping[str, object]], Mapping[str, object]]


class BoundFixtureTestDataExecutor:
    """Load only fixture refs explicitly bound by the target deployment."""

    def __init__(
        self,
        *,
        evidence_store: LocalEvidenceStore,
        bindings: Mapping[str, FixtureBinding],
    ) -> None:
        self._evidence_store = evidence_store
        self._bindings = dict(bindings)

    def execute(
        self,
        *,
        request: TestDataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> TestDataStepExecution:
        del variables
        target = str(step.get("target", ""))
        binding = self._bindings.get(target)
        if binding is None:
            raise ValueError(f"Fixture target has no approved binding: {target}")
        observed = dict(binding(resolved_inputs))
        evidence = _store_bound_json(
            evidence_store=self._evidence_store,
            request=request,
            flow_id=flow_id,
            step_id=str(step["step_id"]),
            phase=phase,
            evidence_type="fixture",
            payload={"target": target, "observed": observed},
        )
        return TestDataStepExecution(source_values={"fixture": observed}, evidence=(evidence,))


class BoundSqlTestDataExecutor:
    """Execute a named, injected query binding; raw SQL text is never evaluated here."""

    def __init__(
        self,
        *,
        evidence_store: LocalEvidenceStore,
        bindings: Mapping[str, SqlBinding],
    ) -> None:
        self._evidence_store = evidence_store
        self._bindings = dict(bindings)

    def execute(
        self,
        *,
        request: TestDataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> TestDataStepExecution:
        del variables
        target = str(step.get("target", ""))
        binding = self._bindings.get(target)
        if binding is None:
            raise ValueError(f"SQL target has no approved query binding: {target}")
        observed = dict(binding(resolved_inputs))
        evidence = _store_bound_json(
            evidence_store=self._evidence_store,
            request=request,
            flow_id=flow_id,
            step_id=str(step["step_id"]),
            phase=phase,
            evidence_type="sql",
            payload={"query_ref": target, "observed": observed},
        )
        return TestDataStepExecution(source_values={"database": observed}, evidence=(evidence,))


@dataclass(frozen=True, slots=True)
class UiDataActionResult:
    observations: Mapping[str, object]
    screenshot: bytes | None = None
    failure_reason: str | None = None


UiBinding = Callable[
    [TestDataExecutionRequest, Mapping[str, object], Mapping[str, object]],
    UiDataActionResult,
]


class BoundUiTestDataExecutor:
    """Invoke only a reviewed screen/action binding and persist its step evidence."""

    def __init__(
        self,
        *,
        evidence_store: LocalEvidenceStore,
        bindings: Mapping[tuple[str, str], UiBinding],
    ) -> None:
        self._evidence_store = evidence_store
        self._bindings = dict(bindings)

    def execute(
        self,
        *,
        request: TestDataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> TestDataStepExecution:
        screen_ref = str(step.get("screen_ref", ""))
        action_ref = str(step.get("ui_action_ref", ""))
        binding = self._bindings.get((screen_ref, action_ref))
        if binding is None:
            raise ValueError(f"UI screen/action has no approved binding: {screen_ref}/{action_ref}")
        result = binding(request, resolved_inputs, variables)
        step_id = str(step["step_id"])
        evidence: list[TestDataExecutionEvidence] = [
            _store_bound_json(
                evidence_store=self._evidence_store,
                request=request,
                flow_id=flow_id,
                step_id=step_id,
                phase=phase,
                evidence_type="step_log",
                payload={
                    "screen_ref": screen_ref,
                    "ui_action_ref": action_ref,
                    "observed": dict(result.observations),
                },
            )
        ]
        if result.screenshot is not None:
            evidence_id = _evidence_id(request.run_id, flow_id, step_id, phase, "screenshot")
            stored = self._evidence_store.store_screenshot(
                project_id=request.project_id,
                run_id=request.run_id,
                evidence_id=evidence_id,
                scenario_id=_flow_component(flow_id),
                content=result.screenshot,
            )
            evidence.append(
                _execution_evidence(
                    stored=stored,
                    flow_id=flow_id,
                    step_id=step_id,
                    phase=phase,
                )
            )
        return TestDataStepExecution(
            source_values={"ui": dict(result.observations)},
            evidence=tuple(evidence),
            failure_reason=result.failure_reason,
        )


@dataclass(frozen=True, slots=True)
class PlaywrightActionResult:
    """Observed browser state and one sanitized screenshot for a reviewed UI step."""

    observations: Mapping[str, object]
    screenshot: bytes


@dataclass(frozen=True, slots=True)
class ComputerUseActionResult:
    """Sanitized AI action trace; business observations come from Playwright."""

    action_kinds: tuple[str, ...]


class PlaywrightCapabilityError(ValueError):
    """A bounded UI capability gap eligible for an explicitly reviewed AI fallback."""


class ComputerUseSession(Protocol):
    """Injectable AI visual-control boundary; no provider is enabled by default."""

    def execute(
        self,
        *,
        base_url: str,
        objective: str,
        max_actions: int,
        observations: tuple[Mapping[str, object], ...],
    ) -> ComputerUseActionResult: ...

    def close(self) -> None: ...


class PlaywrightSession(Protocol):
    """Small injectable boundary around a real Playwright browser session."""

    def execute(
        self,
        *,
        base_url: str,
        action: Mapping[str, object],
    ) -> PlaywrightActionResult: ...

    def observe(
        self,
        *,
        base_url: str,
        observations: tuple[Mapping[str, object], ...],
        mask_locators: tuple[Mapping[str, object], ...],
    ) -> PlaywrightActionResult: ...

    def close(self) -> None: ...


PlaywrightSessionFactory = Callable[[], PlaywrightSession]
ComputerUseSessionFactory = Callable[[PlaywrightSession], ComputerUseSession]


class PlaywrightUiTestDataExecutor:
    """Execute the reviewed Playwright description without evaluating arbitrary code."""

    def __init__(
        self,
        *,
        evidence_store: LocalEvidenceStore,
        session_factory: PlaywrightSessionFactory | None = None,
        computer_use_session_factory: ComputerUseSessionFactory | None = None,
        browser_channel: str | None = None,
        headless: bool = True,
    ) -> None:
        self._evidence_store = evidence_store
        channel = browser_channel or os.getenv("OPERAMIND_PLAYWRIGHT_CHANNEL", "").strip()
        if not channel:
            channel = "msedge" if os.name == "nt" else "chrome"
        self._session_factory = session_factory or (
            lambda: _SyncPlaywrightSession(browser_channel=channel, headless=headless)
        )
        self._computer_use_session_factory = computer_use_session_factory
        self._session: PlaywrightSession | None = None
        self._computer_use_session: ComputerUseSession | None = None

    def execute(
        self,
        *,
        request: TestDataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> TestDataStepExecution:
        del resolved_inputs, variables
        if request.base_url is None:
            raise ValueError("Playwright UI execution requires a base_url")
        action = step.get("playwright")
        if not isinstance(action, Mapping):
            raise ValueError("UI step requires a reviewed playwright action")
        if self._session is None:
            self._session = self._session_factory()
        driver = "playwright"
        fallback_reason: str | None = None
        action_kinds: tuple[str, ...] = ()
        try:
            result = self._session.execute(base_url=request.base_url, action=action)
        except PlaywrightCapabilityError as error:
            fallback = step.get("computer_use_fallback")
            if not isinstance(fallback, Mapping):
                raise
            if self._computer_use_session_factory is None:
                raise ValueError(
                    "AI computer-use fallback was reviewed but no provider is configured"
                ) from error
            if fallback.get("requires_confirmation") is not True:
                raise ValueError("AI computer-use fallback requires confirmation") from error
            raw_observations = fallback.get("observations", [])
            if not isinstance(raw_observations, list) or any(
                not isinstance(value, Mapping) for value in raw_observations
            ):
                raise ValueError("AI computer-use observations must be reviewed objects") from error
            if self._computer_use_session is None:
                self._computer_use_session = self._computer_use_session_factory(self._session)
            fallback_result = self._computer_use_session.execute(
                base_url=request.base_url,
                objective=str(fallback.get("objective") or ""),
                max_actions=int(fallback.get("max_actions") or 0),
                observations=tuple(cast(Mapping[str, object], value) for value in raw_observations),
            )
            result = self._session.observe(
                base_url=request.base_url,
                observations=tuple(cast(Mapping[str, object], value) for value in raw_observations),
                mask_locators=_reviewed_mask_locators(action),
            )
            driver = "computer_use"
            fallback_reason = str(fallback.get("reason") or "")
            action_kinds = fallback_result.action_kinds
        step_id = str(step["step_id"])
        log = _store_bound_json(
            evidence_store=self._evidence_store,
            request=request,
            flow_id=flow_id,
            step_id=step_id,
            phase=phase,
            evidence_type="step_log",
            payload={
                "screen_ref": step.get("screen_ref"),
                "ui_action_ref": step.get("ui_action_ref"),
                "action": action.get("action"),
                "driver": driver,
                "fallback_reason": fallback_reason,
                "computer_use_action_kinds": list(action_kinds),
                "observed": dict(result.observations),
            },
        )
        evidence_id = _evidence_id(request.run_id, flow_id, step_id, phase, "screenshot")
        screenshot = self._evidence_store.store_screenshot(
            project_id=request.project_id,
            run_id=request.run_id,
            evidence_id=evidence_id,
            scenario_id=_flow_component(flow_id),
            content=result.screenshot,
        )
        return TestDataStepExecution(
            source_values={"ui": dict(result.observations)},
            evidence=(
                log,
                _execution_evidence(
                    stored=screenshot,
                    flow_id=flow_id,
                    step_id=step_id,
                    phase=phase,
                ),
            ),
        )

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
        if self._computer_use_session is not None:
            self._computer_use_session.close()
            self._computer_use_session = None


class _SyncPlaywrightSession:
    """One browser context reused across every UI step in a TestDataPlan run."""

    def __init__(self, *, browser_channel: str, headless: bool) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(
                channel=browser_channel,
                headless=headless,
            )
        except Exception:
            self._playwright.stop()
            raise
        self._context = self._browser.new_context()
        self._page = self._context.new_page()

    def execute(
        self,
        *,
        base_url: str,
        action: Mapping[str, object],
    ) -> PlaywrightActionResult:
        if _http_origin(base_url) is None:
            raise ValueError("Playwright base_url must be a credential-free HTTP(S) origin")
        action_name = str(action.get("action") or "")
        locator = None
        locator_spec = action.get("locator")
        if isinstance(locator_spec, Mapping):
            locator = _playwright_locator(self._page, locator_spec)
            if action_name != "wait_for":
                locator_count = locator.count()
                if locator_count != 1:
                    raise PlaywrightCapabilityError(
                        "Playwright locator must resolve to exactly one element: "
                        f"count={locator_count}"
                    )
        timeout = action.get("timeout_ms")
        timeout_ms = int(timeout) if isinstance(timeout, int) else None
        timeout_options = {"timeout": timeout_ms} if timeout_ms is not None else {}
        if action_name == "goto":
            path = str(action.get("path") or "")
            target = _target_url(base_url, path, {})
            self._page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
        elif action_name == "reload":
            self._page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
        elif action_name == "go_back":
            self._page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
        elif action_name == "go_forward":
            self._page.go_forward(wait_until="domcontentloaded", timeout=timeout_ms)
        elif action_name == "click" and locator is not None:
            locator.click(**_pointer_options(action, timeout_options))
        elif action_name == "double_click" and locator is not None:
            locator.dblclick(**_pointer_options(action, timeout_options))
        elif action_name == "fill" and locator is not None:
            locator.fill(str(action.get("value") or ""), **timeout_options)
        elif action_name == "type" and locator is not None:
            locator.type(str(action.get("value") or ""), **timeout_options)
        elif action_name == "clear" and locator is not None:
            locator.clear(**timeout_options)
        elif action_name == "select_option" and locator is not None:
            locator.select_option(action.get("value"), **timeout_options)
        elif action_name == "check" and locator is not None:
            locator.check(**timeout_options)
        elif action_name == "uncheck" and locator is not None:
            locator.uncheck(**timeout_options)
        elif action_name == "press" and locator is not None:
            locator.press(str(action.get("key") or ""), **timeout_options)
        elif action_name == "hover" and locator is not None:
            locator.hover(**_pointer_options(action, timeout_options, include_button=False))
        elif action_name == "focus" and locator is not None:
            locator.focus(**timeout_options)
        elif action_name == "blur" and locator is not None:
            locator.blur(**timeout_options)
        elif action_name == "scroll_into_view" and locator is not None:
            locator.scroll_into_view_if_needed(**timeout_options)
        elif action_name == "drag_to" and locator is not None:
            target_spec = action.get("target_locator")
            if not isinstance(target_spec, Mapping):
                raise ValueError("Playwright drag_to requires target_locator")
            target_locator = _playwright_locator(self._page, target_spec)
            target_count = target_locator.count()
            if target_count != 1:
                raise PlaywrightCapabilityError(
                    "Playwright target locator must resolve to exactly one element: "
                    f"count={target_count}"
                )
            locator.drag_to(target_locator, **timeout_options)
        elif action_name == "wait_for" and locator is not None:
            locator.wait_for(state=str(action.get("state") or "visible"), **timeout_options)
        elif action_name == "wait_for_url":
            target = _target_url(base_url, str(action.get("path") or ""), {})
            self._page.wait_for_url(target, timeout=timeout_ms)
        elif action_name == "wait_for_load_state":
            load_state = cast(
                Literal["load", "domcontentloaded", "networkidle"],
                str(action.get("state") or "load"),
            )
            self._page.wait_for_load_state(state=load_state, timeout=timeout_ms)
        else:
            raise PlaywrightCapabilityError(
                f"Unsupported or incomplete Playwright action: {action_name}"
            )
        _validate_reviewed_frame_origins(self._page, action)
        raw_observations = action.get("observations", [])
        if not isinstance(raw_observations, list) or any(
            not isinstance(value, Mapping) for value in raw_observations
        ):
            raise ValueError("Playwright observations must be an array of objects")
        return self.observe(
            base_url=base_url,
            observations=tuple(cast(Mapping[str, object], value) for value in raw_observations),
            mask_locators=_reviewed_mask_locators(action),
        )

    def observe(
        self,
        *,
        base_url: str,
        observations: tuple[Mapping[str, object], ...],
        mask_locators: tuple[Mapping[str, object], ...],
    ) -> PlaywrightActionResult:
        if _http_origin(self._page.url) != _http_origin(base_url):
            raise OSError("Playwright UI action escaped the approved origin")
        observed: dict[str, object] = {
            "url": self._page.url,
            "title": self._page.title(),
        }
        for observation in observations:
            key = str(observation.get("key") or "")
            kind = str(observation.get("kind") or "")
            observation_locator = observation.get("locator")
            target_locator = (
                _playwright_locator(self._page, observation_locator)
                if isinstance(observation_locator, Mapping)
                else None
            )
            observed[key] = _playwright_observation(
                page=self._page,
                locator=target_locator,
                kind=kind,
                attribute_name=str(observation.get("attribute_name") or ""),
            )
        masks = [
            self._page.locator('input[type="password"]'),
            self._page.locator('input[autocomplete="current-password"]'),
            self._page.locator('input[autocomplete="new-password"]'),
            self._page.locator("[data-operamind-sensitive]"),
            *(_playwright_locator(self._page, value) for value in mask_locators),
        ]
        screenshot = self._page.screenshot(full_page=True, mask=masks)
        return PlaywrightActionResult(observations=observed, screenshot=screenshot)

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._playwright.stop()


def _playwright_locator(page: Any, value: Mapping[str, object]) -> Any:
    frame = value.get("frame")
    scope = _playwright_frame_scope(page, str(frame)) if frame is not None else page
    by = str(value.get("by") or "")
    locator_value = str(value.get("value") or "")
    if by == "role":
        name = value.get("name")
        return scope.get_by_role(
            locator_value,
            name=str(name) if name is not None else None,
            exact=bool(value.get("exact", True)),
        )
    if by == "label":
        return scope.get_by_label(locator_value, exact=bool(value.get("exact", True)))
    if by == "placeholder":
        return scope.get_by_placeholder(locator_value, exact=bool(value.get("exact", True)))
    if by == "text":
        return scope.get_by_text(locator_value, exact=bool(value.get("exact", True)))
    if by == "alt_text":
        return scope.get_by_alt_text(locator_value, exact=bool(value.get("exact", True)))
    if by == "title":
        return scope.get_by_title(locator_value, exact=bool(value.get("exact", True)))
    if by == "test_id":
        return scope.get_by_test_id(locator_value)
    if by == "css":
        return scope.locator(locator_value)
    raise PlaywrightCapabilityError(f"Unsupported Playwright locator strategy: {by}")


def _reviewed_mask_locators(
    action: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    raw = action.get("mask_locators")
    if not isinstance(raw, list) or any(not isinstance(value, Mapping) for value in raw):
        raise ValueError("Playwright action requires reviewed screenshot mask_locators")
    return tuple(cast(Mapping[str, object], value) for value in raw)


def _playwright_frame_scope(page: Any, frame_selector: str) -> Any:
    frame_element = page.locator(frame_selector)
    frame_count = frame_element.count()
    if frame_count != 1:
        raise PlaywrightCapabilityError(
            f"Playwright frame locator must resolve to exactly one frame: count={frame_count}"
        )
    handle = frame_element.element_handle()
    frame = handle.content_frame() if handle is not None else None
    if frame is None:
        raise PlaywrightCapabilityError("Playwright frame element has no active content frame")
    frame_url = str(frame.url or "")
    if frame_url != "about:blank" and _http_origin(frame_url) != _http_origin(page.url):
        raise OSError("Playwright frame escaped the approved origin")
    return frame


def _validate_reviewed_frame_origins(page: Any, action: Mapping[str, object]) -> None:
    specs: list[Mapping[str, object]] = []
    for key in ("locator", "target_locator"):
        value = action.get(key)
        if isinstance(value, Mapping):
            specs.append(value)
    observations = action.get("observations", [])
    if isinstance(observations, list):
        specs.extend(
            cast(Mapping[str, object], observation["locator"])
            for observation in observations
            if isinstance(observation, Mapping) and isinstance(observation.get("locator"), Mapping)
        )
    for spec in specs:
        frame_selector = spec.get("frame")
        if frame_selector is not None:
            _playwright_frame_scope(page, str(frame_selector))


def _playwright_observation(
    *, page: Any, locator: Any | None, kind: str, attribute_name: str = ""
) -> object:
    if kind == "url":
        return page.url
    if kind == "title":
        return page.title()
    if locator is None:
        raise ValueError(f"Playwright observation {kind} requires a locator")
    if kind == "text":
        return locator.inner_text()
    if kind == "count":
        return locator.count()
    if kind == "visible":
        return locator.is_visible()
    if kind == "enabled":
        return locator.is_enabled()
    if kind == "checked":
        return locator.is_checked()
    if kind == "value":
        return locator.input_value()
    if kind == "attribute":
        if not attribute_name:
            raise ValueError("Playwright attribute observation requires attribute_name")
        return locator.get_attribute(attribute_name)
    raise ValueError(f"Unsupported Playwright observation: {kind}")


def _pointer_options(
    action: Mapping[str, object],
    timeout_options: Mapping[str, int],
    *,
    include_button: bool = True,
) -> dict[str, object]:
    options: dict[str, object] = dict(timeout_options)
    button = action.get("mouse_button")
    modifiers = action.get("modifiers")
    position = action.get("position")
    if include_button and button is not None:
        options["button"] = str(button)
    if isinstance(modifiers, list):
        options["modifiers"] = [str(value) for value in modifiers]
    if isinstance(position, Mapping):
        options["position"] = {
            "x": float(position.get("x", 0)),
            "y": float(position.get("y", 0)),
        }
    return options


def _http_target(step: Mapping[str, object], inputs: Mapping[str, object]) -> tuple[str, str]:
    target_parts = str(step.get("target", "")).split(maxsplit=1)
    target_method = target_parts[0].upper() if len(target_parts) == 2 else None
    target_path = target_parts[1] if len(target_parts) == 2 else None
    method = str(inputs.get("method", target_method or "")).upper()
    path = str(inputs.get("path", target_path or ""))
    if method not in _HTTP_METHODS:
        raise ValueError(f"Unsupported HTTP Test data method: {method}")
    if target_method is not None and (method, path) != (target_method, target_path):
        raise ValueError("HTTP Test data inputs differ from the reviewed target")
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("HTTP Test data path must be an origin-relative absolute path")
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError("HTTP Test data path must not change origin or contain a fragment")
    if parsed.query:
        raise ValueError("HTTP Test data query must use the reviewed query object")
    return method, parsed.path


def _target_url(base_url: str, path: str, query: Mapping[object, object]) -> str:
    base = urlsplit(base_url)
    if (
        base.scheme not in {"http", "https"}
        or not base.netloc
        or base.query
        or base.fragment
        or base.username is not None
        or base.password is not None
    ):
        raise ValueError(
            "HTTP Test data base_url must be a credential-free HTTP(S) URL without query"
        )
    _reject_unsafe_url_path(base.path, label="base_url")
    _reject_unsafe_url_path(path, label="path")
    base_path = base.path.rstrip("/")
    target_path = f"{base_path}{path}" if base_path else path
    query_values: list[tuple[str, str]] = []
    for key, value in query.items():
        if isinstance(value, list):
            query_values.extend((str(key), str(item)) for item in value)
        elif value is not None:
            query_values.append((str(key), str(value)))
    return urlunsplit((base.scheme, base.netloc, target_path, urlencode(query_values), ""))


def _reject_unsafe_url_path(value: str, *, label: str) -> None:
    decoded = value
    for _attempt in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if (
        "\\" in decoded
        or "\x00" in decoded
        or any(segment in {".", ".."} for segment in decoded.split("/"))
    ):
        raise ValueError(f"HTTP Test data {label} must not contain dot segments or backslashes")


def _http_origin(value: str) -> tuple[str, str, int] | None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return (
        parsed.scheme.casefold(),
        parsed.hostname.casefold(),
        port if port is not None else (443 if parsed.scheme == "https" else 80),
    )


def _decode_response(body: bytes) -> object:
    if not body:
        return {}
    try:
        return json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode(errors="replace")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next(
        (value for key, value in headers.items() if key.casefold() == name.casefold()),
        None,
    )


def _store_bound_json(
    *,
    evidence_store: LocalEvidenceStore,
    request: TestDataExecutionRequest,
    flow_id: str,
    step_id: str,
    phase: str,
    evidence_type: str,
    payload: object,
) -> TestDataExecutionEvidence:
    evidence_id = _evidence_id(request.run_id, flow_id, step_id, phase, evidence_type)
    stored = evidence_store.store_json(
        project_id=request.project_id,
        run_id=request.run_id,
        evidence_id=evidence_id,
        scenario_id=_flow_component(flow_id),
        evidence_type=evidence_type,
        payload=payload,
    )
    return _execution_evidence(
        stored=stored,
        flow_id=flow_id,
        step_id=step_id,
        phase=phase,
    )


def _execution_evidence(
    *,
    stored: StoredBrowserEvidence,
    flow_id: str,
    step_id: str,
    phase: str,
) -> TestDataExecutionEvidence:
    return TestDataExecutionEvidence(
        evidence_id=stored.evidence_id,
        flow_id=flow_id,
        step_id=step_id,
        phase=phase,
        evidence_type=stored.evidence_type,
        evidence_ref=stored.evidence_ref,
        content_digest=stored.content_digest,
    )


def _evidence_id(
    run_id: str,
    flow_id: str,
    step_id: str,
    phase: str,
    evidence_type: str,
) -> str:
    digest = hashlib.sha256(
        "\x00".join((run_id, flow_id, step_id, phase, evidence_type)).encode()
    ).hexdigest()[:24]
    return f"td-{evidence_type}-{digest}"


def _flow_component(flow_id: str) -> str:
    return f"flow-{hashlib.sha256(flow_id.encode()).hexdigest()[:24]}"
