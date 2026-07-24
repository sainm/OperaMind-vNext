"""Transactional idempotency receipts for human Web commands."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, cast

from psycopg import Connection

from operamind.infrastructure.postgres.errors import PersistenceConflictError


class WebCommandRepository:
    """Execute one DB-backed command once for a scope and idempotency key."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def execute(
        self,
        *,
        command_scope: str,
        idempotency_key: str,
        actor: str,
        payload: dict[str, object],
        operation: Callable[[], dict[str, object]],
    ) -> dict[str, object]:
        scope = command_scope.strip()
        key = idempotency_key.strip()
        command_actor = actor.strip()
        if not scope or len(scope) > 1000:
            raise ValueError("Web command scope must be non-blank and bounded")
        if not key or len(key) > 500:
            raise ValueError("Web command idempotency key must be non-blank and bounded")
        if not command_actor or len(command_actor) > 200:
            raise ValueError("Web command actor must be non-blank and bounded")
        request_text = _json({"actor": command_actor, "payload": payload})
        request_digest = _digest(request_text)
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO web_command_receipts (
                        command_scope, idempotency_key, actor, request_digest
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (scope, key, command_actor, request_digest),
                )
                created = cursor.rowcount == 1
                if not created:
                    cursor.execute(
                        """
                        SELECT actor, request_digest, response_payload, response_digest
                        FROM web_command_receipts
                        WHERE command_scope = %s AND idempotency_key = %s
                        FOR UPDATE
                        """,
                        (scope, key),
                    )
                    existing = cursor.fetchone()
                    if existing is None:
                        raise RuntimeError("Web command receipt disappeared during replay")
                    if (str(existing[0]), str(existing[1])) != (
                        command_actor,
                        request_digest,
                    ):
                        raise PersistenceConflictError(
                            "Web command idempotency key has different request content"
                        )
                    response = existing[2]
                    response_digest = existing[3]
                    if not isinstance(response, dict) or response_digest is None:
                        raise RuntimeError("Web command receipt is incomplete")
                    response_text = _json(cast(dict[str, object], response))
                    if _digest(response_text) != str(response_digest):
                        raise PersistenceConflictError(
                            "Web command receipt response digest differs"
                        )
                    return cast(dict[str, object], response)
            result = operation()
            response_text = _json(result)
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE web_command_receipts
                    SET response_payload = %s::jsonb, response_digest = %s,
                        completed_at = now()
                    WHERE command_scope = %s AND idempotency_key = %s
                      AND response_payload IS NULL
                    """,
                    (response_text, _digest(response_text), scope, key),
                )
                if cursor.rowcount != 1:
                    raise PersistenceConflictError("Web command receipt completion lost its lock")
            return result


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
