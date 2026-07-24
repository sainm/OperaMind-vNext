"""Evidence-producing executors for reviewed TestDataPlan channel bindings."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import IO, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
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
        evidence_id = _evidence_id(flow_id, step_id, phase, evidence_type)
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
            evidence_id = _evidence_id(flow_id, step_id, phase, "screenshot")
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
        or base.path not in {"", "/"}
        or base.query
        or base.fragment
        or base.username is not None
        or base.password is not None
    ):
        raise ValueError("HTTP Test data base_url must be a credential-free HTTP(S) origin")
    query_values: list[tuple[str, str]] = []
    for key, value in query.items():
        if isinstance(value, list):
            query_values.extend((str(key), str(item)) for item in value)
        elif value is not None:
            query_values.append((str(key), str(value)))
    return urlunsplit((base.scheme, base.netloc, path, urlencode(query_values), ""))


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
    evidence_id = _evidence_id(flow_id, step_id, phase, evidence_type)
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


def _evidence_id(flow_id: str, step_id: str, phase: str, evidence_type: str) -> str:
    digest = hashlib.sha256(
        "\x00".join((flow_id, step_id, phase, evidence_type)).encode()
    ).hexdigest()[:24]
    return f"td-{evidence_type}-{digest}"


def _flow_component(flow_id: str) -> str:
    return f"flow-{hashlib.sha256(flow_id.encode()).hexdigest()[:24]}"
