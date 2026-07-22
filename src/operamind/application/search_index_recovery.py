"""Explicit recovery for interrupted Search Index builds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import Connection

from operamind.infrastructure.postgres import SearchIndexBuildState, SearchIndexRepository


@dataclass(frozen=True, slots=True)
class SearchIndexRecoveryRequest:
    """Audited identity and fixed age boundary for one recovery event."""

    recovery_id: str
    build_id: str
    actor: str
    reason: str
    stale_before: datetime

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.recovery_id, self.build_id, self.actor, self.reason)
        ):
            raise ValueError("Search Index recovery fields must not be blank")
        if self.stale_before.utcoffset() is None:
            raise ValueError("Search Index recovery stale_before must include a timezone")


class SearchIndexRecoveryService:
    """Close a stale interrupted build as failed without allowing it to resume."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._repository = SearchIndexRepository(connection)

    def run(self, request: SearchIndexRecoveryRequest) -> SearchIndexBuildState:
        return self._repository.recover_stale_build(
            recovery_id=request.recovery_id,
            build_id=request.build_id,
            actor=request.actor,
            reason=request.reason,
            stale_before=request.stale_before,
        )
