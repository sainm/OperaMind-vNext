"""Immutable report persistence and bounded management queries for unresolved evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.unresolved_evidence import (
    UnresolvedEvidenceReportBuilder,
    unresolved_evidence_report_id,
)


@dataclass(frozen=True, slots=True)
class UnresolvedEvidencePublishResult:
    unresolved_evidence_report_id: str
    code_graph_snapshot_id: str
    created: bool
    open_count: int
    closed_count: int


class UnresolvedEvidenceRepository:
    """Persist one deterministic report per graph and retain all predecessor reports."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)
        self._builder = UnresolvedEvidenceReportBuilder(contracts)

    def ensure_for_graph(self, graph: dict[str, Any]) -> UnresolvedEvidencePublishResult:
        self._contracts.validate_artifact(graph)
        if graph.get("artifact_type") != "CodeGraphSnapshot":
            raise ValueError("Unresolved Evidence requires a CodeGraphSnapshot")
        if graph.get("scan_status") not in {"complete", "truncated"}:
            raise ValueError("Unresolved Evidence requires a usable Code Graph")
        snapshot_id = str(graph["code_graph_snapshot_id"])
        report_id = unresolved_evidence_report_id(snapshot_id)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            existing = self._load_result(cursor, report_id)
            if existing is not None:
                artifact = self._artifacts.get_for_share(report_id)
                if artifact is None:
                    raise PersistenceConflictError(
                        "Unresolved Evidence normalized report has no immutable Artifact"
                    )
                self._validate_rows(cursor, artifact)
                return existing
            self._validate_graph_scope(cursor, graph)
            predecessor = self._predecessor(cursor, graph)
            result = self._builder.build(graph=graph, predecessor=predecessor)
            artifact = result.artifact
            self._artifacts.store(
                artifact_id=report_id,
                project_id=str(graph["project_id"]),
                analysis_case_id=None,
                artifact=artifact,
            )
            trigger = cast(dict[str, Any], artifact["trigger"])
            cursor.execute(
                """
                INSERT INTO unresolved_evidence_reports (
                    unresolved_evidence_report_id, project_id, repository_id,
                    repository_revision, code_graph_snapshot_id,
                    predecessor_report_id, report_status, trigger_type,
                    trigger_evidence_refs, open_count, closed_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    report_id,
                    artifact["project_id"],
                    artifact["repository_id"],
                    artifact["repository_revision"],
                    snapshot_id,
                    artifact.get("predecessor_report_id"),
                    artifact["report_status"],
                    trigger["trigger_type"],
                    _json(trigger["evidence_refs"]),
                    result.open_count,
                    result.closed_count,
                ),
            )
            for item in cast(list[dict[str, Any]], artifact["items"]):
                self._store_item(cursor, report_id, str(graph["project_id"]), item)
            self._validate_rows(cursor, artifact)
            return UnresolvedEvidencePublishResult(
                unresolved_evidence_report_id=report_id,
                code_graph_snapshot_id=snapshot_id,
                created=True,
                open_count=result.open_count,
                closed_count=result.closed_count,
            )

    def get(self, report_id: str) -> dict[str, Any] | None:
        if not report_id.strip():
            raise ValueError("unresolved_evidence_report_id must not be blank")
        with self._connection.cursor() as cursor:
            if self._load_result(cursor, report_id) is None:
                return None
        artifact = self._artifacts.get(report_id)
        if artifact is None:
            raise PersistenceConflictError(
                "Unresolved Evidence normalized report has no immutable Artifact"
            )
        with self._connection.cursor() as cursor:
            self._validate_rows(cursor, artifact)
        return artifact

    def get_by_graph(self, code_graph_snapshot_id: str) -> dict[str, Any] | None:
        if not code_graph_snapshot_id.strip():
            raise ValueError("code_graph_snapshot_id must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT unresolved_evidence_report_id
                FROM unresolved_evidence_reports
                WHERE code_graph_snapshot_id = %s
                """,
                (code_graph_snapshot_id,),
            )
            row = cursor.fetchone()
        return self.get(str(row[0])) if row is not None else None

    def management_view(self, *, project_id: str, history_limit: int = 50) -> dict[str, object]:
        if not project_id.strip():
            raise ValueError("project_id must not be blank")
        if not 1 <= history_limit <= 200:
            raise ValueError("history_limit must be between 1 and 200")
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM projects WHERE project_id = %s", (project_id,))
            if cursor.fetchone() is None:
                raise ValueError("Project does not exist")
            cursor.execute(
                """
                SELECT report.unresolved_evidence_report_id,
                       report.repository_id, report.code_graph_snapshot_id,
                       report.report_status, report.trigger_type,
                       report.open_count, report.closed_count, report.created_at,
                       graph.is_current
                FROM unresolved_evidence_reports AS report
                JOIN code_graph_snapshots AS graph
                  ON graph.code_graph_snapshot_id = report.code_graph_snapshot_id
                 AND graph.project_id = report.project_id
                WHERE report.project_id = %s
                ORDER BY graph.is_current DESC,
                         report.created_at DESC,
                         report.unresolved_evidence_report_id DESC
                LIMIT %s
                """,
                (project_id, history_limit),
            )
            rows = cursor.fetchall()
        artifacts = {str(row[0]): self.get(str(row[0])) for row in rows}
        current_reports = [
            cast(dict[str, Any], artifacts[str(row[0])]) for row in rows if bool(row[8])
        ]
        history = [
            {
                "unresolved_evidence_report_id": str(row[0]),
                "repository_id": str(row[1]),
                "code_graph_snapshot_id": str(row[2]),
                "report_status": str(row[3]),
                "trigger_type": str(row[4]),
                "open_count": int(row[5]),
                "closed_count": int(row[6]),
                "created_at": row[7].isoformat(),
                "is_current": bool(row[8]),
            }
            for row in rows
        ]
        return {
            "project_id": project_id,
            "current_reports": current_reports,
            "history": history,
            "current_report_count": len(current_reports),
            "history_count": len(history),
            "open_count": sum(int(report["open_count"]) for report in current_reports),
            "closed_in_current_count": sum(
                int(report["closed_count"]) for report in current_reports
            ),
        }

    @staticmethod
    def _validate_graph_scope(cursor: Cursor[Any], graph: dict[str, Any]) -> None:
        cursor.execute(
            """
            SELECT snapshot.project_id, snapshot.repository_id, revision.commit_sha
            FROM code_graph_snapshots AS snapshot
            JOIN repository_revisions AS revision
              ON revision.repository_revision_id = snapshot.repository_revision_id
             AND revision.repository_id = snapshot.repository_id
            WHERE snapshot.code_graph_snapshot_id = %s
            FOR SHARE OF snapshot, revision
            """,
            (graph["code_graph_snapshot_id"],),
        )
        row = cursor.fetchone()
        expected = (
            graph["project_id"],
            graph["repository_id"],
            graph["repository_revision"],
        )
        if row is None or tuple(row) != expected:
            raise ValueError("Unresolved Evidence Code Graph scope differs")

    def _predecessor(self, cursor: Cursor[Any], graph: dict[str, Any]) -> dict[str, Any] | None:
        base_id = graph.get("base_code_graph_snapshot_id")
        if isinstance(base_id, str):
            cursor.execute(
                """
                SELECT unresolved_evidence_report_id
                FROM unresolved_evidence_reports
                WHERE code_graph_snapshot_id = %s AND project_id = %s
                """,
                (base_id, graph["project_id"]),
            )
        else:
            cursor.execute(
                """
                SELECT prior.unresolved_evidence_report_id
                FROM code_graph_snapshots AS current_graph
                JOIN unresolved_evidence_reports AS prior
                  ON prior.project_id = current_graph.project_id
                 AND prior.repository_id = current_graph.repository_id
                 AND prior.code_graph_snapshot_id <> current_graph.code_graph_snapshot_id
                WHERE current_graph.code_graph_snapshot_id = %s
                  AND current_graph.is_current
                ORDER BY prior.created_at DESC,
                         prior.unresolved_evidence_report_id DESC
                LIMIT 1
                """,
                (graph["code_graph_snapshot_id"],),
            )
        row = cursor.fetchone()
        if row is None:
            return None
        artifact = self._artifacts.get_for_share(str(row[0]))
        if artifact is None:
            raise PersistenceConflictError("Unresolved Evidence predecessor Artifact disappeared")
        return artifact

    @staticmethod
    def _store_item(
        cursor: Cursor[Any], report_id: str, project_id: str, item: dict[str, Any]
    ) -> None:
        location = cast(dict[str, Any], item["source_location"])
        closure = cast(dict[str, Any] | None, item.get("closure"))
        cursor.execute(
            """
            INSERT INTO unresolved_evidence_items (
                unresolved_evidence_report_id, project_id, item_id, finding_key,
                edge_ref, status, category, reason, edge_type, source_ref,
                unresolved_target_ref, source_path, source_start_line,
                source_end_line, candidate_targets, missing_evidence,
                resolution_suggestions, provenance, evidence_refs,
                resolved_target_ref, resolved_edge_ref, proof_kind,
                closure_evidence_refs
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb,
                %s, %s, %s, %s::jsonb
            )
            """,
            (
                report_id,
                project_id,
                item["item_id"],
                item["finding_key"],
                item["edge_ref"],
                item["status"],
                item["category"],
                item["reason"],
                item["edge_type"],
                item["source_ref"],
                item["unresolved_target_ref"],
                location["path"],
                location["start_line"],
                location["end_line"],
                _json(item["candidate_targets"]),
                _json(item["missing_evidence"]),
                _json(item["resolution_suggestions"]),
                item["provenance"],
                _json(item["evidence_refs"]),
                closure.get("resolved_target_ref") if closure else None,
                closure.get("resolved_edge_ref") if closure else None,
                closure.get("proof_kind") if closure else None,
                _json(closure["evidence_refs"]) if closure else None,
            ),
        )

    @staticmethod
    def _load_result(cursor: Cursor[Any], report_id: str) -> UnresolvedEvidencePublishResult | None:
        cursor.execute(
            """
            SELECT unresolved_evidence_report_id, code_graph_snapshot_id,
                   open_count, closed_count
            FROM unresolved_evidence_reports
            WHERE unresolved_evidence_report_id = %s
            """,
            (report_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return UnresolvedEvidencePublishResult(
            unresolved_evidence_report_id=str(row[0]),
            code_graph_snapshot_id=str(row[1]),
            created=False,
            open_count=int(row[2]),
            closed_count=int(row[3]),
        )

    @staticmethod
    def _validate_rows(cursor: Cursor[Any], artifact: dict[str, Any]) -> None:
        report_id = str(artifact["unresolved_evidence_report_id"])
        trigger = cast(dict[str, Any], artifact["trigger"])
        items = cast(list[dict[str, Any]], artifact["items"])
        cursor.execute(
            """
            SELECT project_id, repository_id, repository_revision,
                   code_graph_snapshot_id, predecessor_report_id, report_status,
                   trigger_type, trigger_evidence_refs, open_count, closed_count
            FROM unresolved_evidence_reports
            WHERE unresolved_evidence_report_id = %s
            """,
            (report_id,),
        )
        row = cursor.fetchone()
        expected_header = (
            artifact["project_id"],
            artifact["repository_id"],
            artifact["repository_revision"],
            artifact["code_graph_snapshot_id"],
            artifact.get("predecessor_report_id"),
            artifact["report_status"],
            trigger["trigger_type"],
            tuple(trigger["evidence_refs"]),
            int(artifact["open_count"]),
            int(artifact["closed_count"]),
        )
        actual_header = (
            (
                *tuple(row[:7]),
                tuple(cast(list[object], row[7])),
                int(row[8]),
                int(row[9]),
            )
            if row is not None
            else None
        )
        if actual_header != expected_header:
            raise PersistenceConflictError("Unresolved Evidence report header differs")
        cursor.execute(
            """
            SELECT item_id, finding_key, edge_ref, status, category, reason,
                   edge_type, source_ref, unresolved_target_ref, source_path,
                   source_start_line, source_end_line, candidate_targets,
                   missing_evidence, resolution_suggestions, provenance,
                   evidence_refs, resolved_target_ref, resolved_edge_ref,
                   proof_kind, closure_evidence_refs
            FROM unresolved_evidence_items
            WHERE unresolved_evidence_report_id = %s
            ORDER BY item_id
            """,
            (report_id,),
        )
        actual_items = tuple(_row_item(row) for row in cursor.fetchall())
        expected_items = tuple(sorted(_artifact_item(item) for item in items))
        if actual_items != expected_items:
            raise PersistenceConflictError("Unresolved Evidence report items differ")


def _artifact_item(item: dict[str, Any]) -> tuple[object, ...]:
    location = cast(dict[str, Any], item["source_location"])
    closure = cast(dict[str, Any] | None, item.get("closure"))
    return (
        item["item_id"],
        item["finding_key"],
        item["edge_ref"],
        item["status"],
        item["category"],
        item["reason"],
        item["edge_type"],
        item["source_ref"],
        item["unresolved_target_ref"],
        location["path"],
        location["start_line"],
        location["end_line"],
        _json(item["candidate_targets"]),
        tuple(item["missing_evidence"]),
        tuple(item["resolution_suggestions"]),
        item["provenance"],
        tuple(item["evidence_refs"]),
        closure.get("resolved_target_ref") if closure else None,
        closure.get("resolved_edge_ref") if closure else None,
        closure.get("proof_kind") if closure else None,
        tuple(closure["evidence_refs"]) if closure else None,
    )


def _row_item(row: tuple[object, ...]) -> tuple[object, ...]:
    return (
        *row[:12],
        _json(row[12]),
        tuple(cast(list[object], row[13])),
        tuple(cast(list[object], row[14])),
        row[15],
        tuple(cast(list[object], row[16])),
        *row[17:20],
        tuple(cast(list[object], row[20])) if row[20] is not None else None,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
