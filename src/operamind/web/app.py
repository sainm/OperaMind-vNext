"""Application factory, trace propagation, and safe API error boundary."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
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
from operamind.application.main_flow_coordinator import MainFlowCoordinator
from operamind.application.main_flow_execution import (
    TestDataExecutorFactory,
    default_test_data_executor_factory,
)
from operamind.application.orchestration_task import OrchestrationSchedulingPolicy
from operamind.application.project_onboarding import ProjectOnboardingCoordinator
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.local_installation import PRODUCT_ID, application_version
from operamind.web.routers import (
    bridge,
    change_requests,
    local_environment,
    projects,
)

LOGGER = logging.getLogger(__name__)


def create_app(
    *,
    repository_root: Path,
    database_url: str,
    bridge_token: str | None = None,
    orchestration_scheduling_policy: OrchestrationSchedulingPolicy | None = None,
    test_data_executor_factory: TestDataExecutorFactory = default_test_data_executor_factory,
    enable_internal_coordinator: bool = True,
    coordinator_poll_seconds: float = 2.0,
) -> FastAPI:
    if not database_url.strip():
        raise ValueError("database_url must not be blank")
    root = repository_root.resolve()
    static_root = Path(__file__).with_name("static")
    scheduling_policy = orchestration_scheduling_policy or OrchestrationSchedulingPolicy()
    if not 0.1 <= coordinator_poll_seconds <= 60:
        raise ValueError("coordinator_poll_seconds must be between 0.1 and 60")
    coordinator = MainFlowCoordinator(
        database_url=database_url,
        repository_root=root,
        executor_factory=test_data_executor_factory,
        scheduling_policy=scheduling_policy,
    )
    onboarding_coordinator = ProjectOnboardingCoordinator(
        database_url=database_url,
        repository_root=root,
    )
    stop_event = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        threads: list[threading.Thread] = []
        if enable_internal_coordinator:
            stop_event.clear()
            threads = [
                threading.Thread(
                    target=coordinator.run_forever,
                    kwargs={
                        "stop_event": stop_event,
                        "poll_seconds": coordinator_poll_seconds,
                    },
                    name="operamind-main-flow",
                    daemon=True,
                ),
                threading.Thread(
                    target=onboarding_coordinator.run_forever,
                    kwargs={
                        "stop_event": stop_event,
                        "poll_seconds": coordinator_poll_seconds,
                    },
                    name="operamind-project-onboarding",
                    daemon=True,
                ),
            ]
            for thread in threads:
                thread.start()
        try:
            yield
        finally:
            stop_event.set()
            for thread in threads:
                thread.join(timeout=5)
                if thread.is_alive():
                    LOGGER.error(
                        "Internal coordinator %s did not stop within 5 seconds", thread.name
                    )

    app = FastAPI(title="OperaMind Control Plane", version="1.0.0", lifespan=lifespan)
    app.state.repository_root = root
    app.state.database_url = database_url
    app.state.bridge_token = bridge_token
    app.state.orchestration_scheduling_policy = scheduling_policy
    app.state.test_data_executor_factory = test_data_executor_factory
    app.state.local_environment_diagnostics = LocalEnvironmentDiagnosticsService(
        repository_root=root,
        database_url=database_url,
        bridge_enabled=bool(bridge_token),
    )

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
        return {
            "status": "ok",
            "product": PRODUCT_ID,
            "version": application_version(),
        }

    app.include_router(projects.router)
    app.include_router(change_requests.router)
    app.include_router(bridge.router)
    app.include_router(local_environment.bridge_router)
    app.mount("/", StaticFiles(directory=static_root, html=True), name="web")
    return app


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
