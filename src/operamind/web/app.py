"""Application factory, trace propagation, and safe API error boundary."""

from __future__ import annotations

import base64
import binascii
import logging
import secrets
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from operamind.application.local_environment_diagnostics import (
    LocalEnvironmentDiagnosticsService,
)
from operamind.application.orchestration_task import OrchestrationSchedulingPolicy
from operamind.application.web_test_data_execution import (
    TestDataExecutorFactory,
    default_test_data_executor_factory,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.web.routers import (
    bridge,
    change_requests,
    commands,
    local_environment,
    orchestration_tasks,
    projects,
    readiness,
)

LOGGER = logging.getLogger(__name__)


def create_app(
    *,
    repository_root: Path,
    database_url: str,
    bridge_token: str | None = None,
    web_token: str | None = None,
    orchestration_scheduling_policy: OrchestrationSchedulingPolicy | None = None,
    test_data_executor_factory: TestDataExecutorFactory = default_test_data_executor_factory,
) -> FastAPI:
    if not database_url.strip():
        raise ValueError("database_url must not be blank")
    root = repository_root.resolve()
    static_root = Path(__file__).with_name("static")
    app = FastAPI(title="OperaMind Control Plane", version="1.0.0")
    app.state.repository_root = root
    app.state.database_url = database_url
    app.state.test_data_executor_factory = test_data_executor_factory
    app.state.bridge_token = bridge_token
    app.state.web_token = web_token
    app.state.orchestration_scheduling_policy = (
        orchestration_scheduling_policy or OrchestrationSchedulingPolicy()
    )
    app.state.local_environment_diagnostics = LocalEnvironmentDiagnosticsService(
        repository_root=root,
        database_url=database_url,
        bridge_enabled=bool(bridge_token),
    )

    @app.middleware("http")
    async def authenticate_local_web(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            web_token
            and request.url.path != "/health"
            and not request.url.path.startswith("/api/v1/local-bridge")
            and not _web_token_matches(request, web_token)
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "web_authentication_required",
                    "message": "OperaMind Web の認証が必要です",
                    "details": None,
                    "trace_id": getattr(request.state, "trace_id", "unavailable"),
                },
                headers={"WWW-Authenticate": 'Basic realm="OperaMind Web", charset="UTF-8"'},
            )
        return await call_next(request)

    @app.middleware("http")
    async def trace_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = request.headers.get("X-Trace-ID") or uuid.uuid4().hex
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return _error(
            request, 422, "request_validation_failed", "入力内容を確認してください", error.errors()
        )

    @app.exception_handler(PersistenceConflictError)
    async def conflict_error(request: Request, error: PersistenceConflictError) -> JSONResponse:
        return _error(
            request,
            409,
            "persistence_conflict",
            "同じ識別子に異なる内容を登録することはできません",
            {"reason": str(error)},
        )

    @app.exception_handler(ValueError)
    async def value_error(request: Request, error: ValueError) -> JSONResponse:
        reason = str(error)
        status = 404 if "does not exist" in reason else 409
        code = "not_found" if status == 404 else "command_rejected"
        message = "対象が見つかりません" if status == 404 else "処理を続行できません"
        return _error(request, status, code, message, {"reason": reason})

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, error: psycopg.Error) -> JSONResponse:
        LOGGER.exception("PostgreSQL request failed", exc_info=error)
        return _error(request, 503, "database_unavailable", "データベースを利用できません")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled Web request failure", exc_info=error)
        return _error(request, 500, "internal_error", "処理中にエラーが発生しました")

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(projects.router)
    app.include_router(change_requests.router)
    app.include_router(commands.router)
    app.include_router(readiness.router)
    app.include_router(bridge.router)
    app.include_router(local_environment.router)
    app.include_router(local_environment.bridge_router)
    app.include_router(orchestration_tasks.router)
    app.mount("/", StaticFiles(directory=static_root, html=True), name="web")
    return app


def _web_token_matches(request: Request, expected: str) -> bool:
    authorization = request.headers.get("Authorization", "")
    bearer_prefix = "Bearer "
    if authorization.startswith(bearer_prefix):
        supplied = authorization[len(bearer_prefix) :]
        return bool(supplied) and secrets.compare_digest(supplied, expected)
    basic_prefix = "Basic "
    if not authorization.startswith(basic_prefix):
        return False
    try:
        decoded = base64.b64decode(authorization[len(basic_prefix) :], validate=True).decode(
            "utf-8"
        )
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, separator, supplied = decoded.partition(":")
    return (
        separator == ":"
        and secrets.compare_digest(username, "operamind")
        and secrets.compare_digest(supplied, expected)
    )


def _error(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: object | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "details": jsonable_encoder(
                details,
                custom_encoder={Exception: str, ValueError: str},
            ),
            "trace_id": getattr(request.state, "trace_id", "unavailable"),
        },
    )
