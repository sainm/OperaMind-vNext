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

from operamind.application.data_identity import redact_secret_evidence
from operamind.application.test_data_execution import (
    TestDataExecutionEvidence,
    TestDataExecutionRequest,
    TestDataStepBlockedError,
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
            payload=redact_secret_evidence(payload),
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
        frozen_binding = _validated_frozen_binding(step=step, request=request)
        result = binding(request, resolved_inputs, variables)
        observed = dict(result.observations)
        if frozen_binding is not None:
            observed.update(
                _verified_observed_binding_identity(
                    frozen_binding=frozen_binding,
                    raw_observation=observed,
                )
            )
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
                    "observed": observed,
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
            source_values={"ui": observed},
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


class PlaywrightPreActionBlockedError(TestDataStepBlockedError, OSError):
    """A real page-state check failed before the target action was executed.

    ``_SyncPlaywrightSession`` historically exposed origin/frame failures as
    ``OSError``.  Keep that compatibility for direct callers while exposing
    the structured ``TestDataStepBlockedError`` used by the execution layer.
    """

    def __init__(self, message: str, *, details: Mapping[str, object]) -> None:
        super().__init__(message)
        self.details = dict(details)


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

    def observe_binding_identity(
        self,
        *,
        base_url: str,
        frozen_binding: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def observe(
        self,
        *,
        base_url: str,
        observations: tuple[Mapping[str, object], ...],
        mask_locators: tuple[Mapping[str, object], ...],
        binding_scope_locator: Mapping[str, object] | None = None,
    ) -> PlaywrightActionResult: ...

    def capture_failure(
        self,
        *,
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
        frozen_binding = _validated_frozen_binding(step=step, request=request)
        runtime_action = dict(action)
        if frozen_binding is not None:
            runtime_action["_operamind_binding_scope"] = frozen_binding["record_scope_locator"]
            if phase == "cleanup":
                runtime_action["_operamind_allow_binding_absent_after_action"] = True
        if self._session is None:
            self._session = self._session_factory()
        binding_verification: dict[str, object] = {}
        try:
            if frozen_binding is not None:
                raw_identity = self._session.observe_binding_identity(
                    base_url=request.base_url,
                    frozen_binding=frozen_binding,
                )
                binding_verification = _verified_observed_binding_identity(
                    frozen_binding=frozen_binding,
                    raw_observation=raw_identity,
                )
        except TestDataStepBlockedError as error:
            blocked_binding = cast(Mapping[str, object], frozen_binding)
            details = {
                "failure_stage": "pre_action_identity_validation",
                "locator_type": _locator_type(
                    cast(Mapping[str, object], blocked_binding["record_scope_locator"])
                ),
                **(_public_failure_observation(raw_identity) if "raw_identity" in locals() else {}),
            }
            raise self._blocked_with_evidence(
                error=error,
                details=details,
                request=request,
                flow_id=flow_id,
                step=step,
                phase=phase,
                frozen_binding=frozen_binding,
                action=action,
            ) from error
        driver = "playwright"
        fallback_reason: str | None = None
        action_kinds: tuple[str, ...] = ()
        try:
            result = self._session.execute(base_url=request.base_url, action=runtime_action)
        except PlaywrightPreActionBlockedError as error:
            raise self._blocked_with_evidence(
                error=error,
                details=error.details,
                request=request,
                flow_id=flow_id,
                step=step,
                phase=phase,
                frozen_binding=frozen_binding,
                action=action,
            ) from error
        except PlaywrightCapabilityError as error:
            if frozen_binding is not None:
                blocked = self._blocked_with_evidence(
                    error=TestDataStepBlockedError(
                        "Frozen Binding を持つ UI 操作では AI fallback を使用できません"
                    ),
                    details={
                        "failure_stage": "pre_action_capability_validation",
                        "locator_type": (
                            _locator_type(cast(Mapping[str, object], action["locator"]))
                            if isinstance(action.get("locator"), Mapping)
                            else "unknown"
                        ),
                    },
                    request=request,
                    flow_id=flow_id,
                    step=step,
                    phase=phase,
                    frozen_binding=frozen_binding,
                    action=action,
                )
                raise blocked from error
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
            try:
                result = self._session.observe(
                    base_url=request.base_url,
                    observations=tuple(
                        cast(Mapping[str, object], value) for value in raw_observations
                    ),
                    mask_locators=_reviewed_mask_locators(action),
                )
            except PlaywrightPreActionBlockedError as observation_error:
                raise self._blocked_with_evidence(
                    error=observation_error,
                    details=observation_error.details,
                    request=request,
                    flow_id=flow_id,
                    step=step,
                    phase=phase,
                    frozen_binding=frozen_binding,
                    action=action,
                ) from observation_error
            driver = "computer_use"
            fallback_reason = str(fallback.get("reason") or "")
            action_kinds = fallback_result.action_kinds
        step_id = str(step["step_id"])
        observed = dict(result.observations)
        if frozen_binding is not None:
            observed.update(binding_verification)
        trace = {
            "driver": driver,
            **{
                key: observed[key]
                for key in (
                    "locator_type",
                    "record_scope_match_count",
                    "action_locator_match_count",
                    "observed_screen_identity_values",
                    "observed_identity_digest",
                )
                if key in observed
            },
        }
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
                "observed": observed,
                "trace": trace,
            },
            test_data_binding_ref=(
                str(frozen_binding["binding_id"]) if frozen_binding is not None else None
            ),
        )
        if step.get("_suppress_unbound_screenshot") is True:
            return TestDataStepExecution(
                source_values={"ui": observed},
                evidence=(log,),
                trace=trace,
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
            source_values={"ui": observed},
            evidence=(
                log,
                _execution_evidence(
                    stored=screenshot,
                    flow_id=flow_id,
                    step_id=step_id,
                    phase=phase,
                    test_data_binding_ref=(
                        str(frozen_binding["binding_id"]) if frozen_binding is not None else None
                    ),
                ),
            ),
            trace=trace,
        )

    def _blocked_with_evidence(
        self,
        *,
        error: Exception,
        details: Mapping[str, object],
        request: TestDataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        phase: str,
        frozen_binding: Mapping[str, object] | None,
        action: Mapping[str, object],
    ) -> TestDataStepBlockedError:
        if self._session is None:
            return TestDataStepBlockedError(str(error))
        capture = getattr(self._session, "capture_failure", None)
        if not callable(capture):
            return TestDataStepBlockedError(str(error), trace=details)
        try:
            result = capture(mask_locators=_reviewed_mask_locators(action))
        except Exception as capture_error:
            # A broken reviewed mask must not suppress the original blocked
            # action.  Retry with mandatory built-in masks only and record the
            # capture issue in the sanitized step log.
            try:
                result = capture(mask_locators=())
            except Exception:
                return TestDataStepBlockedError(
                    str(error),
                    trace={
                        **dict(details),
                        "failure_capture_warning": type(capture_error).__name__,
                    },
                )
            details = {
                **dict(details),
                "failure_capture_warning": type(capture_error).__name__,
            }
        step_id = str(step["step_id"])
        binding_ref = str(frozen_binding["binding_id"]) if frozen_binding is not None else None
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
                "driver": "playwright",
                "status": "blocked",
                "blocking_reason": str(error),
                **dict(details),
                "observed": dict(result.observations),
            },
            test_data_binding_ref=binding_ref,
        )
        evidence_id = _evidence_id(request.run_id, flow_id, step_id, phase, "screenshot")
        stored = self._evidence_store.store_screenshot(
            project_id=request.project_id,
            run_id=request.run_id,
            evidence_id=evidence_id,
            scenario_id=_flow_component(flow_id),
            content=result.screenshot,
        )
        screenshot = _execution_evidence(
            stored=stored,
            flow_id=flow_id,
            step_id=step_id,
            phase=phase,
            test_data_binding_ref=binding_ref,
        )
        trace = {
            **dict(details),
            "observed": dict(result.observations),
            "driver": "playwright",
            "locator_type": details.get("locator_type"),
        }
        return TestDataStepBlockedError(str(error), (log, screenshot), trace=trace)

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
        current_origin = _http_origin(self._page.url)
        if current_origin != _http_origin(base_url) and not (
            action_name == "goto" and current_origin is None
        ):
            raise PlaywrightPreActionBlockedError(
                "Playwright action left the configured Project origin",
                details={
                    "failure_stage": "pre_action_origin_validation",
                    "actual_origin": current_origin,
                },
            )
        raw_binding_scope = action.get("_operamind_binding_scope")
        binding_scope = None
        binding_count: int | None = None
        if isinstance(raw_binding_scope, Mapping):
            binding_scope = _playwright_locator(self._page, raw_binding_scope)
            binding_count = binding_scope.count()
            if binding_count != 1:
                raise PlaywrightPreActionBlockedError(
                    "Frozen Binding の record scope は操作前に 1 件である必要があります: "
                    f"count={binding_count}",
                    details={
                        "failure_stage": "pre_action_record_scope_validation",
                        "record_scope_match_count": binding_count,
                        "locator_type": _locator_type(raw_binding_scope),
                    },
                )
        locator_scope = binding_scope if binding_scope is not None else self._page
        locator = None
        locator_count: int | None = None
        locator_spec = action.get("locator")
        if isinstance(locator_spec, Mapping):
            locator = _playwright_locator(locator_scope, locator_spec)
            if action_name != "wait_for":
                locator_count = locator.count()
                if locator_count != 1:
                    raise PlaywrightPreActionBlockedError(
                        "Playwright locator must resolve to exactly one element: "
                        f"count={locator_count}",
                        details={
                            "failure_stage": "pre_action_locator_validation",
                            "record_scope_match_count": binding_count,
                            "action_locator_match_count": locator_count,
                            "locator_type": _locator_type(locator_spec),
                        },
                    )
        pre_action_observations = _read_pre_action_observations(
            page=self._page,
            scope=locator_scope,
            action=action,
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
            target_locator = _playwright_locator(locator_scope, target_spec)
            target_count = target_locator.count()
            if target_count != 1:
                raise PlaywrightPreActionBlockedError(
                    "Playwright target locator must resolve to exactly one element: "
                    f"count={target_count}",
                    details={
                        "failure_stage": "pre_action_target_locator_validation",
                        "record_scope_match_count": binding_count,
                        "action_locator_match_count": target_count,
                        "locator_type": _locator_type(target_spec),
                    },
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
        cleanup_scope_count: int | None = None
        if (
            binding_scope is not None
            and action.get("_operamind_allow_binding_absent_after_action") is True
        ):
            try:
                binding_scope.wait_for(state="detached", **timeout_options)
            except Exception as wait_error:
                cleanup_scope_count = binding_scope.count()
                if cleanup_scope_count != 0:
                    raise PlaywrightPreActionBlockedError(
                        "Frozen Binding の record scope が cleanup 後も残っています: "
                        f"count={cleanup_scope_count}",
                        details={
                            "failure_stage": "post_action_cleanup_scope_validation",
                            "record_scope_match_count": cleanup_scope_count,
                            "locator_type": _locator_type(
                                cast(Mapping[str, object], raw_binding_scope)
                            ),
                        },
                    ) from wait_error
            else:
                cleanup_scope_count = binding_scope.count()
            if cleanup_scope_count != 0:
                raise PlaywrightPreActionBlockedError(
                    "Frozen Binding の record scope cleanup verification failed",
                    details={
                        "failure_stage": "post_action_cleanup_scope_validation",
                        "record_scope_match_count": cleanup_scope_count,
                    },
                )
        raw_observations = action.get("observations", [])
        if not isinstance(raw_observations, list) or any(
            not isinstance(value, Mapping) for value in raw_observations
        ):
            raise ValueError("Playwright observations must be an array of objects")
        result = self.observe(
            base_url=base_url,
            observations=tuple(cast(Mapping[str, object], value) for value in raw_observations),
            mask_locators=_reviewed_mask_locators(action),
            binding_scope_locator=(
                cast(Mapping[str, object], raw_binding_scope)
                if isinstance(raw_binding_scope, Mapping)
                and action.get("_operamind_allow_binding_absent_after_action") is not True
                else None
            ),
        )
        metadata: dict[str, object] = {
            "locator_type": (
                _locator_type(locator_spec) if isinstance(locator_spec, Mapping) else "navigation"
            ),
            "record_scope_match_count": binding_count,
            "action_locator_match_count": locator_count,
        }
        if pre_action_observations:
            metadata["pre_action_observations"] = pre_action_observations
        if cleanup_scope_count is not None:
            metadata["cleanup_record_scope_match_count"] = cleanup_scope_count
        return PlaywrightActionResult(
            observations={**metadata, **dict(result.observations)},
            screenshot=result.screenshot,
        )

    def observe_binding_identity(
        self,
        *,
        base_url: str,
        frozen_binding: Mapping[str, object],
    ) -> Mapping[str, object]:
        if _http_origin(self._page.url) != _http_origin(base_url):
            raise TestDataStepBlockedError(
                "Frozen Binding の DOM 身元確認前に approved origin から外れました"
            )
        binding_scope = _playwright_locator(
            self._page,
            cast(Mapping[str, object], frozen_binding["record_scope_locator"]),
        )
        match_count = binding_scope.count()
        if match_count != 1:
            return {"binding_match_count": match_count}
        observations = cast(Mapping[str, object], frozen_binding["identity_observations"])
        business_specs = cast(list[Mapping[str, object]], observations["business_unique_keys"])
        raw_screen_specs = observations.get("screen_identity_values")
        screen_specs = (
            cast(list[Mapping[str, object]], raw_screen_specs)
            if isinstance(raw_screen_specs, list) and raw_screen_specs
            else [cast(Mapping[str, object], observations["screen_key"])]
        )
        observed_screen_values = [
            {
                "name": str(spec["name"]),
                "value": _playwright_dom_identity_value(binding_scope, spec),
            }
            for spec in screen_specs
        ]
        return {
            "binding_match_count": match_count,
            "observed_business_unique_keys": [
                {
                    "name": str(spec["name"]),
                    "value": _playwright_dom_identity_value(binding_scope, spec),
                }
                for spec in business_specs
            ],
            "observed_screen_key": observed_screen_values[0],
            "observed_screen_identity_values": observed_screen_values,
        }

    def observe(
        self,
        *,
        base_url: str,
        observations: tuple[Mapping[str, object], ...],
        mask_locators: tuple[Mapping[str, object], ...],
        binding_scope_locator: Mapping[str, object] | None = None,
    ) -> PlaywrightActionResult:
        if _http_origin(self._page.url) != _http_origin(base_url):
            raise PlaywrightPreActionBlockedError(
                "Playwright UI action escaped the approved origin",
                details={
                    "failure_stage": "post_action_origin_validation",
                    "locator_type": "origin",
                    "expected_origin": _origin_label(base_url),
                    "observed_origin": _origin_label(self._page.url),
                },
            )
        binding_scope = None
        if binding_scope_locator is not None:
            binding_scope = _playwright_locator(self._page, binding_scope_locator)
            binding_count = binding_scope.count()
            if binding_count != 1:
                raise TestDataStepBlockedError(
                    "Frozen Binding の record scope が操作後の観測時に変化しました: "
                    f"count={binding_count}"
                )
        locator_scope = binding_scope if binding_scope is not None else self._page
        observed: dict[str, object] = {
            "url": self._page.url,
            "title": self._page.title(),
        }
        for observation in observations:
            key = str(observation.get("key") or "")
            kind = str(observation.get("kind") or "")
            observation_locator = observation.get("locator")
            target_locator = (
                _playwright_locator(locator_scope, observation_locator)
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

    def capture_failure(
        self,
        *,
        mask_locators: tuple[Mapping[str, object], ...],
    ) -> PlaywrightActionResult:
        masks = [
            self._page.locator('input[type="password"]'),
            self._page.locator('input[autocomplete="current-password"]'),
            self._page.locator('input[autocomplete="new-password"]'),
            self._page.locator("[data-operamind-sensitive]"),
            *(_playwright_locator(self._page, value) for value in mask_locators),
        ]
        return PlaywrightActionResult(
            observations={
                "url": self._page.url,
                "title": self._page.title(),
            },
            screenshot=self._page.screenshot(full_page=True, mask=masks),
        )

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
        locator = scope.get_by_role(
            locator_value,
            name=str(name) if name is not None else None,
            exact=bool(value.get("exact", True)),
        )
    elif by == "label":
        locator = scope.get_by_label(locator_value, exact=bool(value.get("exact", True)))
    elif by == "placeholder":
        locator = scope.get_by_placeholder(locator_value, exact=bool(value.get("exact", True)))
    elif by == "text":
        locator = scope.get_by_text(locator_value, exact=bool(value.get("exact", True)))
    elif by == "alt_text":
        locator = scope.get_by_alt_text(locator_value, exact=bool(value.get("exact", True)))
    elif by == "title":
        locator = scope.get_by_title(locator_value, exact=bool(value.get("exact", True)))
    elif by == "test_id":
        locator = scope.get_by_test_id(locator_value)
    elif by == "css":
        locator = scope.locator(locator_value)
    else:
        raise PlaywrightPreActionBlockedError(
            f"Unsupported Playwright locator strategy: {by}",
            details={
                "failure_stage": "pre_action_locator_validation",
                "locator_type": by or "unknown",
            },
        )
    filters = value.get("all")
    if filters is not None:
        if (
            not isinstance(filters, list)
            or not filters
            or any(not isinstance(item, Mapping) for item in filters)
        ):
            raise PlaywrightPreActionBlockedError(
                "Composite Playwright locator is invalid",
                details={
                    "failure_stage": "pre_action_locator_validation",
                    "locator_type": _locator_type(value),
                },
            )
        for item in filters:
            locator = locator.filter(has=_playwright_locator(scope, item))
    return locator


def _locator_type(locator: Mapping[str, object]) -> str:
    by = str(locator.get("by") or "unknown")
    if by == "role" and locator.get("name") is not None:
        by = "role+name"
    filters = locator.get("all")
    if isinstance(filters, list) and filters:
        return (
            "composite("
            + ",".join(
                [by, *(_locator_type(item) for item in filters if isinstance(item, Mapping))]
            )
            + ")"
        )
    return by


def _public_failure_observation(value: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "binding_match_count",
        "observed_business_unique_keys",
        "observed_screen_key",
        "observed_screen_identity_values",
    }
    return {key: item for key, item in value.items() if key in allowed}


def _validated_frozen_binding(
    *,
    step: Mapping[str, object],
    request: TestDataExecutionRequest,
) -> Mapping[str, object] | None:
    binding_ref = str(step.get("data_binding_ref", ""))
    raw = step.get("_frozen_data_binding")
    if not binding_ref:
        if raw is not None:
            raise TestDataStepBlockedError("参照のない Frozen Binding が注入されました")
        return None
    if not isinstance(raw, Mapping):
        raise TestDataStepBlockedError(f"Frozen Binding がありません: {binding_ref}")
    if raw.get("test_data_id") != binding_ref or raw.get("run_id") != request.run_id:
        raise TestDataStepBlockedError("Frozen Binding の実行 Scope が一致しません")
    digest = str(raw.get("content_digest", ""))
    payload = {
        key: value for key, value in raw.items() if key not in {"content_digest", "evidence_ref"}
    }
    actual = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if digest != actual:
        raise TestDataStepBlockedError("Frozen Binding の content digest が一致しません")
    locator = raw.get("record_scope_locator")
    if not isinstance(locator, Mapping) or locator.get("exact") is not True:
        raise TestDataStepBlockedError("Frozen Binding に exact screen locator がありません")
    expected_identity = {
        "business_unique_keys": raw.get("business_unique_keys"),
        "screen_identity_values": raw.get("screen_identity_values"),
    }
    identity_digest = _canonical_identity_digest(expected_identity)
    if raw.get("identity_digest") != identity_digest:
        raise TestDataStepBlockedError("Frozen Binding の identity digest が一致しません")
    _validated_identity_observation_specs(raw)
    return raw


def _validated_identity_observation_specs(
    frozen_binding: Mapping[str, object],
) -> Mapping[str, object]:
    raw = frozen_binding.get("identity_observations")
    if not isinstance(raw, Mapping):
        raise TestDataStepBlockedError("Frozen Binding に DOM 身元観測定義がありません")
    business = raw.get("business_unique_keys")
    screen = raw.get("screen_key")
    raw_screen_specs = raw.get("screen_identity_values")
    screen_specs = (
        cast(list[Mapping[str, object]], raw_screen_specs)
        if isinstance(raw_screen_specs, list) and raw_screen_specs
        else ([screen] if isinstance(screen, Mapping) else [])
    )
    if not isinstance(business, list) or not business or not screen_specs:
        raise TestDataStepBlockedError("Frozen Binding の DOM 身元観測定義が不完全です")
    expected_business = frozen_binding.get("business_unique_keys")
    expected_screen_values = frozen_binding.get("screen_identity_values")
    if (
        not isinstance(expected_business, list)
        or not isinstance(expected_screen_values, list)
        or not expected_screen_values
    ):
        raise TestDataStepBlockedError("Frozen Binding の身元値が不完全です")
    expected_names = [
        str(value.get("name", "")) for value in expected_business if isinstance(value, Mapping)
    ]
    observed_names = [
        str(value.get("name", "")) for value in business if isinstance(value, Mapping)
    ]
    if len(observed_names) != len(business) or observed_names != expected_names:
        raise TestDataStepBlockedError("Frozen Binding の業務キー観測定義が一致しません")
    if any(not name for name in observed_names) or len(observed_names) != len(set(observed_names)):
        raise TestDataStepBlockedError("Frozen Binding の業務キー観測名が不正です")
    screen_names = [str(value.get("name", "")) for value in screen_specs]
    expected_screen_names = [
        str(value.get("name", "")) for value in expected_screen_values if isinstance(value, Mapping)
    ]
    if len(expected_screen_names) != len(expected_screen_values) or (
        screen_names != expected_screen_names
    ):
        raise TestDataStepBlockedError("Frozen Binding の画面キー観測定義が一致しません")
    for spec in [*cast(list[Mapping[str, object]], business), *screen_specs]:
        kind = str(spec.get("kind", ""))
        if kind not in {"text", "input_value", "attribute"}:
            raise TestDataStepBlockedError("Frozen Binding の DOM 身元観測種別が不正です")
        if kind == "attribute" and not str(spec.get("attribute_name", "")).strip():
            raise TestDataStepBlockedError("Frozen Binding の DOM 属性名がありません")
        relative_locator = spec.get("locator")
        if relative_locator is not None and (
            not isinstance(relative_locator, Mapping)
            or relative_locator.get("exact") is not True
            or relative_locator.get("frame") is not None
        ):
            raise TestDataStepBlockedError(
                "Frozen Binding の DOM 身元 Locator は同一 container 内の exact 指定が必須です"
            )
    return raw


def _verified_observed_binding_identity(
    *,
    frozen_binding: Mapping[str, object],
    raw_observation: Mapping[str, object],
) -> dict[str, object]:
    if any(
        key in raw_observation
        for key in ("observed_identity_digest", "binding_identity_digest", "binding_content_digest")
    ):
        raise TestDataStepBlockedError(
            "Frozen Binding の digest を DOM 観測結果として入力することは禁止されています"
        )
    match_count = raw_observation.get("binding_match_count")
    if isinstance(match_count, bool) or not isinstance(match_count, int) or match_count != 1:
        raise TestDataStepBlockedError(
            "Frozen Binding の record scope は操作前に 1 件である必要があります: "
            f"count={match_count!r}"
        )
    expected_business = frozen_binding.get("business_unique_keys")
    expected_screen_values = frozen_binding.get("screen_identity_values")
    raw_business = raw_observation.get("observed_business_unique_keys")
    raw_screen_values = raw_observation.get("observed_screen_identity_values")
    if raw_screen_values is None:
        legacy_screen = raw_observation.get("observed_screen_key")
        raw_screen_values = [legacy_screen] if isinstance(legacy_screen, Mapping) else None
    if (
        not isinstance(expected_business, list)
        or not isinstance(expected_screen_values, list)
        or not expected_screen_values
        or not isinstance(raw_business, list)
        or not isinstance(raw_screen_values, list)
    ):
        raise TestDataStepBlockedError("Frozen Binding の DOM 身元観測値がありません")
    observed_business = _normalized_observed_identity_values(
        expected=expected_business,
        observed=raw_business,
        label="業務キー",
    )
    observed_screen_values = _normalized_observed_identity_values(
        expected=expected_screen_values,
        observed=raw_screen_values,
        label="画面キー",
    )
    observed_identity = {
        "business_unique_keys": observed_business,
        "screen_identity_values": observed_screen_values,
    }
    observed_digest = _canonical_identity_digest(observed_identity)
    expected_digest = str(frozen_binding.get("identity_digest", ""))
    if observed_digest != expected_digest:
        raise TestDataStepBlockedError(
            "Frozen Binding と DOM の身元が一致しません: observed_identity_digest が一致しません"
        )
    return {
        "binding_match_count": match_count,
        "binding_id": frozen_binding["binding_id"],
        "test_data_id": frozen_binding["test_data_id"],
        "observed_business_unique_keys": observed_business,
        "observed_screen_key": observed_screen_values[0],
        "observed_screen_identity_values": observed_screen_values,
        "binding_identity_digest": expected_digest,
        "observed_identity_digest": observed_digest,
    }


def _normalized_observed_identity_values(
    *,
    expected: list[object],
    observed: list[object],
    label: str,
) -> list[dict[str, object]]:
    if len(observed) != len(expected):
        raise TestDataStepBlockedError(f"Frozen Binding の DOM {label}観測値が不足しています")
    if any(
        not isinstance(expected_value, Mapping) or not isinstance(observed_value, Mapping)
        for expected_value, observed_value in zip(expected, observed, strict=True)
    ):
        raise TestDataStepBlockedError(f"Frozen Binding の DOM {label}観測値が不正です")
    return [
        _normalized_observed_identity_value(
            expected=cast(Mapping[str, object], expected_value),
            observed=cast(Mapping[str, object], observed_value),
            label=label,
        )
        for expected_value, observed_value in zip(expected, observed, strict=True)
    ]


def _normalized_observed_identity_value(
    *,
    expected: Mapping[str, object],
    observed: Mapping[str, object],
    label: str,
) -> dict[str, object]:
    name = str(expected.get("name", ""))
    if str(observed.get("name", "")) != name or "value" not in observed:
        raise TestDataStepBlockedError(f"Frozen Binding の DOM {label}観測値がありません: {name}")
    actual = observed["value"]
    if actual is None or (isinstance(actual, str) and not actual.strip()):
        raise TestDataStepBlockedError(f"Frozen Binding の DOM {label}観測値がありません: {name}")
    return {
        "name": name,
        "value": _coerce_observed_identity_value(actual, expected.get("value"), name=name),
    }


def _coerce_observed_identity_value(actual: object, expected: object, *, name: str) -> object:
    if isinstance(expected, bool):
        if isinstance(actual, bool):
            return actual
        normalized = str(actual).strip().casefold()
        if normalized in {"true", "false"}:
            return normalized == "true"
    elif isinstance(expected, int) and not isinstance(expected, bool):
        try:
            return int(str(actual).strip())
        except ValueError:
            pass
    elif isinstance(expected, float):
        try:
            return float(str(actual).strip())
        except ValueError:
            pass
    elif isinstance(expected, str):
        return str(actual)
    raise TestDataStepBlockedError(f"Frozen Binding の DOM 身元値を変換できません: {name}")


def _canonical_identity_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


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
        raise PlaywrightPreActionBlockedError(
            f"Playwright frame locator must resolve to exactly one frame: count={frame_count}",
            details={
                "failure_stage": "pre_action_locator_validation",
                "action_locator_match_count": frame_count,
                "locator_type": "frame",
            },
        )
    handle = frame_element.element_handle()
    frame = handle.content_frame() if handle is not None else None
    if frame is None:
        raise PlaywrightPreActionBlockedError(
            "Playwright frame element has no active content frame",
            details={
                "failure_stage": "pre_action_locator_validation",
                "locator_type": "frame",
            },
        )
    frame_url = str(frame.url or "")
    if frame_url != "about:blank" and _http_origin(frame_url) != _http_origin(page.url):
        raise PlaywrightPreActionBlockedError(
            "Playwright frame escaped the approved origin",
            details={
                "failure_stage": "pre_action_origin_validation",
                "actual_origin": _http_origin(frame_url),
            },
        )
    return frame


def _validate_reviewed_frame_origins(page: Any, action: Mapping[str, object]) -> None:
    specs: list[Mapping[str, object]] = []
    for key in ("locator", "target_locator"):
        value = action.get(key)
        if isinstance(value, Mapping):
            specs.append(value)
    for observation_key in ("observations", "pre_action_observations"):
        observations = action.get(observation_key, [])
        if isinstance(observations, list):
            specs.extend(
                cast(Mapping[str, object], observation["locator"])
                for observation in observations
                if isinstance(observation, Mapping)
                and isinstance(observation.get("locator"), Mapping)
            )
    for spec in specs:
        frame_selector = spec.get("frame")
        if frame_selector is not None:
            _playwright_frame_scope(page, str(frame_selector))


def _read_pre_action_observations(
    *,
    page: Any,
    scope: Any,
    action: Mapping[str, object],
) -> dict[str, object]:
    """Read reviewed page state immediately before a Playwright action."""

    raw = action.get("pre_action_observations", [])
    if not isinstance(raw, list) or any(not isinstance(value, Mapping) for value in raw):
        raise ValueError("Playwright pre_action_observations must be an array of objects")
    observed: dict[str, object] = {}
    for item in raw:
        observation = cast(Mapping[str, object], item)
        key = str(observation.get("key") or "")
        kind = str(observation.get("kind") or "")
        if not key or "expected" not in observation:
            raise PlaywrightPreActionBlockedError(
                "Playwright pre-action observation requires key and expected",
                details={
                    "failure_stage": "pre_action_state_validation",
                    "observation_kind": kind or "unknown",
                },
            )
        locator_spec = observation.get("locator")
        locator = (
            _playwright_locator(scope, cast(Mapping[str, object], locator_spec))
            if isinstance(locator_spec, Mapping)
            else None
        )
        if kind not in {"url", "title"}:
            if locator is None:
                raise PlaywrightPreActionBlockedError(
                    f"Playwright pre-action observation requires a locator: {key}",
                    details={
                        "failure_stage": "pre_action_state_validation",
                        "observation_key": key,
                        "observation_kind": kind,
                    },
                )
            match_count = locator.count()
            if kind != "count" and match_count != 1:
                raise PlaywrightPreActionBlockedError(
                    f"Playwright pre-action observation must resolve to exactly one element: "
                    f"{key} count={match_count}",
                    details={
                        "failure_stage": "pre_action_state_validation",
                        "observation_key": key,
                        "observation_kind": kind,
                        "observation_match_count": match_count,
                    },
                )
        actual = _playwright_observation(
            page=page,
            locator=locator,
            kind=kind,
            attribute_name=str(observation.get("attribute_name") or ""),
        )
        expected = observation.get("expected")
        if actual != expected:
            raise PlaywrightPreActionBlockedError(
                f"Playwright pre-action observation did not match: {key}",
                details={
                    "failure_stage": "pre_action_state_validation",
                    "observation_key": key,
                    "observation_kind": kind,
                    "observed": _sanitize_public_observation_value(actual, key),
                    "expected": _sanitize_public_observation_value(expected, key),
                },
            )
        observed[key] = _sanitize_public_observation_value(actual, key)
    return observed


def _sanitize_public_observation_value(value: object, key: str) -> object:
    """Keep pre-action Evidence useful without copying secrets into the ledger."""

    redacted = redact_secret_evidence(value, field_name=key)
    if redacted == "[REDACTED]":
        return redacted
    value = redacted
    if isinstance(value, str):
        return value[:500] + ("…" if len(value) > 500 else "")
    if isinstance(value, list):
        return [_sanitize_public_observation_value(item, key) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(name): _sanitize_public_observation_value(item, str(name))
            for name, item in list(value.items())[:20]
        }
    return value


def _playwright_dom_identity_value(
    binding_scope: Any,
    observation: Mapping[str, object],
) -> object:
    target = binding_scope
    locator = observation.get("locator")
    if isinstance(locator, Mapping):
        target = _playwright_locator(binding_scope, locator)
        count = target.count()
        if count != 1:
            raise TestDataStepBlockedError(
                "Frozen Binding の DOM 身元 Locator は container 内で 1 件である必要があります: "
                f"{observation.get('name', '<unknown>')} count={count}"
            )
    kind = str(observation.get("kind", ""))
    playwright_kind = "value" if kind == "input_value" else kind
    return _playwright_observation(
        page=None,
        locator=target,
        kind=playwright_kind,
        attribute_name=str(observation.get("attribute_name", "")),
    )


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
    if include_button and button is not None:
        options["button"] = str(button)
    if isinstance(modifiers, list):
        options["modifiers"] = [str(value) for value in modifiers]
    if action.get("position") is not None:
        raise PlaywrightPreActionBlockedError(
            "Coordinate-based Playwright actions are forbidden",
            details={
                "failure_stage": "pre_action_locator_validation",
                "locator_type": "coordinate",
            },
        )
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


def _origin_label(value: str) -> str:
    origin = _http_origin(value)
    if origin is None:
        return "invalid-origin"
    scheme, hostname, port = origin
    default_port = 443 if scheme == "https" else 80
    suffix = "" if port == default_port else f":{port}"
    return f"{scheme}://{hostname}{suffix}"


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
    test_data_binding_ref: str | None = None,
) -> TestDataExecutionEvidence:
    evidence_id = _evidence_id(request.run_id, flow_id, step_id, phase, evidence_type)
    stored = evidence_store.store_json(
        project_id=request.project_id,
        run_id=request.run_id,
        evidence_id=evidence_id,
        scenario_id=_flow_component(flow_id),
        evidence_type=evidence_type,
        payload=redact_secret_evidence(payload),
    )
    return _execution_evidence(
        stored=stored,
        flow_id=flow_id,
        step_id=step_id,
        phase=phase,
        test_data_binding_ref=test_data_binding_ref,
    )


def _execution_evidence(
    *,
    stored: StoredBrowserEvidence,
    flow_id: str,
    step_id: str,
    phase: str,
    test_data_binding_ref: str | None = None,
) -> TestDataExecutionEvidence:
    return TestDataExecutionEvidence(
        evidence_id=stored.evidence_id,
        flow_id=flow_id,
        step_id=step_id,
        phase=phase,
        evidence_type=stored.evidence_type,
        evidence_ref=stored.evidence_ref,
        content_digest=stored.content_digest,
        test_data_binding_ref=test_data_binding_ref,
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
