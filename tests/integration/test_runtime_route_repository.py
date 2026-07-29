import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from operamind.application import RuntimeRouteReconciler, RuntimeRouteReconcileRequest
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    CodeGraphSnapshotRepository,
    MigrationCatalog,
    MigrationRunner,
    PersistenceConflictError,
    ProfileRepository,
    RuntimeRouteEvidenceRepository,
    UnresolvedEvidenceRepository,
)
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_runtime_route_evidence_and_enriched_graph_round_trip() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    repository_id = f"repository-{suffix}"
    revision_id = f"revision-{suffix}"
    profile_version_id = f"profile-version-{suffix}"
    commit_sha = f"commit-{suffix}"
    profile = _profile(suffix)
    profile_ref = f"{profile['profile_id']}@{profile['profile_version']}"
    contracts = ContractCatalog.load(ROOT / "contracts")

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES (%s, %s)",
                (project_id, "Runtime Route integration test"),
            )
            cursor.execute(
                """
                INSERT INTO repositories (repository_id, project_id, remote_url)
                VALUES (%s, %s, %s)
                """,
                (repository_id, project_id, f"https://example.invalid/{suffix}.git"),
            )
            cursor.execute(
                """
                INSERT INTO repository_revisions (
                    repository_revision_id, repository_id, commit_sha
                ) VALUES (%s, %s, %s)
                """,
                (revision_id, repository_id, commit_sha),
            )
        ProfileRepository(connection, ProfileCatalog.load(ROOT / "profiles")).store_version(
            profile_version_id=profile_version_id,
            profile=profile,
        )
        graphs = CodeGraphSnapshotRepository(connection, contracts)
        base = _graph(
            suffix=suffix,
            project_id=project_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            profile_ref=profile_ref,
        )
        graphs.publish(
            artifact=base,
            repository_revision_id=revision_id,
            profile_version_ids={profile_ref: profile_version_id},
        )
        unresolved_reports = UnresolvedEvidenceRepository(connection, contracts)
        base_report = unresolved_reports.get_by_graph(str(base["code_graph_snapshot_id"]))
        assert base_report is not None
        assert base_report["open_count"] == 1
        assert base_report["closed_count"] == 0
        result = RuntimeRouteReconciler(contracts).reconcile(
            request=RuntimeRouteReconcileRequest(
                runtime_route_evidence_id=f"runtime-evidence-{suffix}",
                merged_code_graph_snapshot_id=f"runtime-graph-{suffix}",
                browser_run_id=f"browser-run-{suffix}",
                captured_at=datetime(2026, 7, 20, tzinfo=UTC),
                source_evidence_ref=f"evidence://{project_id}/browser-run/network",
            ),
            base_graph=base,
            capture={
                "route_observations": [
                    {
                        "observation_id": "observation-customer",
                        "scenario_id": "customer-detail",
                        "event_kind": "network_request",
                        "method": "GET",
                        "path": "/api/customers/42",
                        "source_action_id": "open-customer",
                        "source_route_ref": f"route-{suffix}",
                    }
                ]
            },
        )
        evidence_repository = RuntimeRouteEvidenceRepository(connection, contracts)
        evidence = evidence_repository.publish(result.evidence_artifact)
        evidence_replay = evidence_repository.publish(result.evidence_artifact)
        graph = graphs.publish(
            artifact=result.graph_artifact,
            repository_revision_id=revision_id,
            profile_version_ids={profile_ref: profile_version_id},
        )
        graph_replay = graphs.publish(
            artifact=result.graph_artifact,
            repository_revision_id=revision_id,
            profile_version_ids={profile_ref: profile_version_id},
        )

        assert evidence.created and not evidence_replay.created
        assert (evidence.observation_count, evidence.resolved_count) == (1, 1)
        assert evidence_repository.get(evidence.runtime_route_evidence_id) == (
            result.evidence_artifact
        )
        assert graph.created and not graph_replay.created
        assert graph.scan_mode == "runtime_enriched"
        assert graph.base_code_graph_snapshot_id == base["code_graph_snapshot_id"]
        assert graph.unresolved_edge_count == 0
        assert graphs.get(graph.code_graph_snapshot_id) == result.graph_artifact
        runtime_report = unresolved_reports.get_by_graph(graph.code_graph_snapshot_id)
        assert runtime_report is not None
        assert runtime_report["report_status"] == "clear"
        assert runtime_report["open_count"] == 0
        assert runtime_report["closed_count"] == 1
        assert (
            runtime_report["predecessor_report_id"] == base_report["unresolved_evidence_report_id"]
        )
        closed = runtime_report["items"][0]
        assert closed["status"] == "closed"
        assert closed["closure"]["resolved_target_ref"] == f"endpoint-{suffix}"
        assert closed["closure"]["proof_kind"] == "static_runtime_unique"
        management = unresolved_reports.management_view(project_id=project_id)
        assert management["current_report_count"] == 1
        assert management["history_count"] == 2
        assert management["open_count"] == 0
        runtime_edges = [
            edge
            for edge in result.graph_artifact["edges"]
            if edge["edge_type"] == "calls"
        ]
        assert runtime_edges[0]["provenance"] == "static_runtime"
        assert runtime_edges[0]["evidence_refs"] == [
            f"evidence://{project_id}/browser-run/network"
        ]
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT unresolved_integrity_test")
            cursor.execute(
                """
                UPDATE unresolved_evidence_items
                SET missing_evidence = '[]'::jsonb
                WHERE unresolved_evidence_report_id = %s
                """,
                (base_report["unresolved_evidence_report_id"],),
            )
        with pytest.raises(
            PersistenceConflictError,
            match="Unresolved Evidence report items differ",
        ):
            unresolved_reports.get(str(base_report["unresolved_evidence_report_id"]))
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT unresolved_integrity_test")
            cursor.execute("RELEASE SAVEPOINT unresolved_integrity_test")


def _profile(suffix: str) -> dict[str, Any]:
    raw: object = json.loads(
        (ROOT / "profiles/code-framework-profile.example.json").read_text(encoding="utf-8")
    )
    assert isinstance(raw, dict)
    value = dict(raw)
    value["profile_id"] = f"generic-runtime-web-{suffix}"
    return value


def _graph(
    *,
    suffix: str,
    project_id: str,
    repository_id: str,
    commit_sha: str,
    profile_ref: str,
) -> dict[str, Any]:
    file_id = f"file-{suffix}"
    route_id = f"route-{suffix}"
    endpoint_id = f"endpoint-{suffix}"
    path = "src/main/webapp/customer.js"

    def edge(
        edge_id: str,
        edge_type: str,
        from_ref: str,
        to_ref: str,
        status: str,
        line: int,
    ) -> dict[str, Any]:
        return {
            "edge_id": f"{edge_id}-{suffix}",
            "edge_type": edge_type,
            "from_ref": from_ref,
            "to_ref": to_ref,
            "resolution_status": status,
            "confidence": "low" if status == "unresolved" else "high",
            "extractor": "web_ui_route",
            "profile_version": profile_ref,
            "provenance": "static",
            "evidence_refs": [],
            "source_location": {"path": path, "start_line": line, "end_line": line},
        }

    return {
        "artifact_type": "CodeGraphSnapshot",
        "schema_version": "v1",
        "code_graph_snapshot_id": f"base-graph-{suffix}",
        "project_id": project_id,
        "repository_id": repository_id,
        "repository_revision": commit_sha,
        "framework_profile_refs": [profile_ref],
        "scan_roots": ["src/main"],
        "scan_status": "complete",
        "scan_mode": "full",
        "framework_markers_found": ["generic.web.Controller"],
        "diagnostics": [],
        "files": [
            {
                "file_id": file_id,
                "path": path,
                "language": "javascript",
                "role": "production",
                "content_hash": f"sha256:{suffix}",
                "symbols": [
                    {
                        "symbol_id": route_id,
                        "symbol_type": "ui_route",
                        "name": "dynamic:options.url",
                        "signature": "route:GET:dynamic:options.url",
                        "start_line": 4,
                        "end_line": 4,
                    },
                    {
                        "symbol_id": endpoint_id,
                        "symbol_type": "method",
                        "name": "readCustomer",
                        "signature": "example.CustomerController.readCustomer/1",
                        "start_line": 10,
                        "end_line": 12,
                    },
                ],
            }
        ],
        "edges": [
            edge("contains-route", "contains", file_id, route_id, "resolved", 4),
            edge("contains-endpoint", "contains", file_id, endpoint_id, "resolved", 10),
            edge(
                "exposes-endpoint",
                "exposes",
                endpoint_id,
                "http:GET:/api/customers/{id}",
                "external",
                10,
            ),
            edge(
                "calls-route",
                "calls",
                route_id,
                "unresolved:endpoint:GET:dynamic:options.url",
                "unresolved",
                4,
            ),
        ],
    }
