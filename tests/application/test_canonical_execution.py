from types import SimpleNamespace
from typing import Any, cast

import pytest

import operamind.application.canonical_execution as canonical_module
from operamind.application import (
    ChangeLoopBlockedError,
    ChangeLoopPlan,
    PostgresCanonicalExecutionAuthorizer,
)
from operamind.infrastructure.code_graph import TextReplacement


def _artifacts() -> dict[str, dict[str, Any]]:
    revision = "a" * 40
    return {
        "packet-1": {
            "artifact_type": "CopilotEditPacket",
            "edit_packet_id": "packet-1",
            "base_repository_revision": revision,
            "editable_files": ["src/App.java"],
            "forbidden_globs": ["secrets.txt"],
        },
        "impact-1": {
            "artifact_type": "ImpactReport",
            "impact_report_id": "impact-1",
            "context_package_id": "context-1",
            "code_graph_snapshot_id": "graph-1",
        },
        "confirmation-1": {
            "artifact_type": "ImpactConfirmation",
            "confirmation_id": "confirmation-1",
        },
        "context-1": {
            "artifact_type": "ContextPackage",
            "project_id": "project-1",
            "analysis_case_id": "case-1",
            "context_items": [{"section_id": "section-1"}],
            "retrieval_trace": [{"retrieval_mode": "hybrid"}],
            "unknowns": [],
        },
        "graph-1": {
            "artifact_type": "CodeGraphSnapshot",
            "code_graph_snapshot_id": "graph-1",
            "scan_status": "complete",
            "repository_revision": revision,
        },
    }


def _plan(artifacts: dict[str, dict[str, Any]]) -> ChangeLoopPlan:
    return ChangeLoopPlan(  # type: ignore[arg-type]
        request=SimpleNamespace(project_id="project-1", change_request_id="case-1"),
        case=None,
        git=None,
        document_diff=None,
        artifacts=tuple(
            artifacts[value] for value in ("packet-1", "impact-1", "confirmation-1", "graph-1")
        ),
        replacements=(TextReplacement("src/App.java", "before", "after"),),
        allowed_edit_paths=frozenset({"src/App.java"}),
        forbidden_paths=frozenset({"secrets.txt"}),
    )


def _authorizer(
    monkeypatch: pytest.MonkeyPatch,
    artifacts: dict[str, dict[str, Any]],
) -> PostgresCanonicalExecutionAuthorizer:
    grant = SimpleNamespace(
        grant_id="grant-1",
        project_id="project-1",
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        impact_report_id="impact-1",
        confirmation_id="confirmation-1",
        base_repository_revision="a" * 40,
    )
    artifact_repository = SimpleNamespace(get=lambda artifact_id: artifacts.get(artifact_id))
    grant_repository = SimpleNamespace(authorize_edit=lambda **_: grant)
    packet_repository = SimpleNamespace(
        get=lambda _: SimpleNamespace(status="active", impact_report_status="confirmed")
    )
    impact_repository = SimpleNamespace(get_state=lambda _: SimpleNamespace(status="confirmed"))
    monkeypatch.setattr(canonical_module, "ArtifactRepository", lambda *_: artifact_repository)
    monkeypatch.setattr(canonical_module, "ApprovalGrantRepository", lambda *_: grant_repository)
    monkeypatch.setattr(canonical_module, "EditPacketRepository", lambda *_: packet_repository)
    monkeypatch.setattr(canonical_module, "ImpactRepository", lambda *_: impact_repository)
    return PostgresCanonicalExecutionAuthorizer(
        connection=cast(Any, object()),
        contracts=cast(Any, object()),
        approval_grant_id="grant-1",
        edit_packet_id="packet-1",
    )


def test_postgres_authorizer_binds_exact_persisted_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _artifacts()

    binding = _authorizer(monkeypatch, artifacts).authorize(plan=_plan(artifacts))

    assert binding.context_package_id == "context-1"
    assert binding.code_graph_snapshot_id == "graph-1"
    assert binding.approval_grant_id == "grant-1"


def test_postgres_authorizer_rejects_synthetic_plan_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _artifacts()
    plan_artifacts = _artifacts()
    plan_artifacts["impact-1"] = {
        **plan_artifacts["impact-1"],
        "status": "synthetic-confirmed",
    }

    with pytest.raises(ChangeLoopBlockedError, match="differs from persisted ImpactReport"):
        _authorizer(monkeypatch, artifacts).authorize(plan=_plan(plan_artifacts))


def test_postgres_authorizer_hydrates_synthetic_copies_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _artifacts()
    plan_artifacts = _artifacts()
    plan_artifacts["impact-1"] = {
        **plan_artifacts["impact-1"],
        "status": "synthetic-confirmed",
    }
    authorizer = _authorizer(monkeypatch, artifacts)

    hydrated = authorizer.hydrate(plan=_plan(plan_artifacts))

    assert hydrated.artifact("ImpactReport") == artifacts["impact-1"]
    assert authorizer.authorize(plan=hydrated).impact_report_id == "impact-1"
