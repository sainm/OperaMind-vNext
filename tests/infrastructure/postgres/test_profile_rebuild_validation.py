from __future__ import annotations

from typing import Any, cast

import pytest
from psycopg import Cursor

from operamind.infrastructure.postgres.profile_rebuild_validation import (
    ProfileReplacementValidator,
)


class ImpactBindingCursor:
    def __init__(self, document_snapshot_id: str, code_graph_snapshot_id: str) -> None:
        self._row = (document_snapshot_id, code_graph_snapshot_id)

    def execute(self, query: object, params: object = None) -> None:
        del params
        assert "FROM impact_reports" in str(query)

    def fetchone(self) -> tuple[str, str]:
        return self._row


def test_impact_replacement_must_reference_validated_snapshot_replacements() -> None:
    cursor = cast(Cursor[Any], ImpactBindingCursor("snapshot-new", "graph-new"))

    ProfileReplacementValidator._require_dependency_bindings(
        cursor,
        artifact_type="ImpactReport",
        artifact_id="impact-new",
        project_id="project-1",
        dependencies={
            "DocumentSnapshot": {"snapshot-new"},
            "CodeGraphSnapshot": {"graph-new"},
        },
    )

    with pytest.raises(ValueError, match="does not reference a validated replacement"):
        ProfileReplacementValidator._require_dependency_bindings(
            cursor,
            artifact_type="ImpactReport",
            artifact_id="impact-new",
            project_id="project-1",
            dependencies={
                "DocumentSnapshot": {"snapshot-other"},
                "CodeGraphSnapshot": {"graph-new"},
            },
        )
