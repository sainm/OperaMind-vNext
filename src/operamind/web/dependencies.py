"""Request-scoped service construction and trusted command headers."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import Header, HTTPException, Request

from operamind.application.web_control_plane import WebControlPlaneService


def get_service(request: Request) -> Iterator[WebControlPlaneService]:
    database_url = str(request.app.state.database_url)
    root = Path(request.app.state.repository_root)
    # BackgroundTasks run before yield-dependency cleanup. Autocommit makes each
    # explicit repository transaction visible to the worker before that cleanup.
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield WebControlPlaneService(
            connection=connection,
            repository_root=root,
            control_database_url=database_url,
            orchestration_scheduling_policy=request.app.state.orchestration_scheduling_policy,
        )


def command_actor(
    actor: Annotated[str, Header(alias="X-OperaMind-Actor", min_length=1, max_length=200)],
) -> str:
    return actor.strip()


def idempotency_key(
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=500)],
) -> str:
    return key.strip()


def local_bridge_auth(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    token = getattr(request.app.state, "bridge_token", None)
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=503, detail="OperaMind local Bridge is disabled")
    prefix = "Bearer "
    supplied = (
        authorization[len(prefix) :] if authorization and authorization.startswith(prefix) else ""
    )
    if not supplied or not secrets.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Invalid OperaMind local Bridge token")
