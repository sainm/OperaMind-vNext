import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from psycopg import Cursor

from operamind.infrastructure.postgres import (
    DocumentIngestionResultRepository,
    PersistenceConflictError,
)

ROOT = Path(__file__).parents[3]


def _artifact() -> dict[str, Any]:
    value: object = json.loads(
        (ROOT / "contracts/examples/document-ingestion-result.v1.example.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    return value


def test_event_identity_must_match_its_artifact() -> None:
    artifact = _artifact()

    DocumentIngestionResultRepository._validate_event_artifact_binding(
        artifact,
        event_id="ingestion-ready-001",
        search_index_build_id="search-index-build-001",
    )

    with pytest.raises(PersistenceConflictError, match="identities differ"):
        DocumentIngestionResultRepository._validate_event_artifact_binding(
            artifact,
            event_id="different-event",
            search_index_build_id="search-index-build-001",
        )


def test_document_profiles_must_match_snapshot_membership_and_activation() -> None:
    artifact = _artifact()
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [("screen-design-profile-version-001", "screen-design", "1.0.0")],
        [(["screen-item-table-ja"],)],
        [("fact-screen-status-before-001", "screen-item-table-ja")],
        [(["screen-item-table-ja"],)],
        [("fact-screen-status-after-001", "screen-item-table-ja")],
        [
            (
                "screen-design-activation-001",
                "document:screen_design",
                "screen-design-profile-version-001",
            )
        ],
    ]

    DocumentIngestionResultRepository._validate_document_profile_bindings(
        cast(Cursor[Any], cursor),
        event_project_id="project-001",
        artifact=artifact,
    )

    drifted = dict(artifact)
    drifted["document_profile_refs"] = ["different@1.0.0"]
    with pytest.raises(PersistenceConflictError, match="refs differ"):
        DocumentIngestionResultRepository._validate_document_profile_bindings(
            cast(Cursor[Any], MagicMock()),
            event_project_id="project-001",
            artifact=drifted,
        )


def test_embedding_profile_must_match_search_build_and_activation() -> None:
    artifact = _artifact()
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        (
            "project-001",
            "document-snapshot-after-001",
            "embedding-profile-version-001",
            "openai-compatible",
            "1.0.0",
        ),
        (1,),
    ]

    DocumentIngestionResultRepository._validate_build_artifact_binding(
        cast(Cursor[Any], cursor),
        event_project_id="project-001",
        artifact=artifact,
        search_index_build_id="search-index-build-001",
    )

    drifted = dict(artifact)
    drifted["embedding_profile_version_id"] = "different-profile-version"
    cursor = MagicMock()
    cursor.fetchone.return_value = (
        "project-001",
        "document-snapshot-after-001",
        "embedding-profile-version-001",
        "openai-compatible",
        "1.0.0",
    )
    with pytest.raises(PersistenceConflictError, match="drifted from Search Index Build"):
        DocumentIngestionResultRepository._validate_build_artifact_binding(
            cast(Cursor[Any], cursor),
            event_project_id="project-001",
            artifact=drifted,
            search_index_build_id="search-index-build-001",
        )
