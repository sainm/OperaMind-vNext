"""Normalized immutable Impact Report publication and append-only confirmation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.code_graph_repository import (
    CodeGraphSnapshotRepository,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError

type ImpactItemLedgerRow = tuple[
    str,
    str,
    tuple[str, ...],
    str,
    tuple[str, ...],
    str,
    float | None,
    str,
    str | None,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    bool,
    tuple[str, ...],
]


@dataclass(frozen=True, slots=True)
class ImpactReportState:
    """Current normalized state for one immutable ImpactReport Artifact."""

    impact_report_id: str
    project_id: str
    analysis_case_id: str
    repository_id: str
    repository_revision_id: str
    code_graph_snapshot_id: str
    status: str
    item_count: int
    blocking_unknowns: tuple[str, ...]
    confirmed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ImpactReportPublishResult:
    """Publication outcome including replay-safe normalized state."""

    created: bool
    state: ImpactReportState


@dataclass(frozen=True, slots=True)
class ImpactConfirmationResult:
    """Append-only confirmation outcome."""

    created: bool
    confirmation_id: str
    impact_report_id: str
    report_status: str


class ImpactRepository:
    """Persist Impact artifacts without allowing cross-scope or stale confirmation."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)
        self._graphs = CodeGraphSnapshotRepository(connection, contracts)

    def publish_report(
        self,
        *,
        artifact: dict[str, Any],
        repository_id: str,
        repository_revision_id: str,
    ) -> ImpactReportPublishResult:
        """Publish one current report and supersede an older report for the Case."""

        self._contracts.validate_artifact(artifact)
        if artifact.get("artifact_type") != "ImpactReport":
            raise ValueError("Impact report publication requires an ImpactReport Artifact")
        if not repository_id.strip() or not repository_revision_id.strip():
            raise ValueError("Impact report Repository scope must not be blank")
        _validate_report_semantics(artifact)
        report_id = str(artifact["impact_report_id"])
        project_id = str(artifact["project_id"])
        case_id = str(artifact["analysis_case_id"])
        with self._connection.transaction(), self._connection.cursor() as cursor:
            existing = self._load_state(cursor, report_id)
            if existing is not None:
                stored_artifact = self._artifacts.get(report_id)
                if stored_artifact != artifact:
                    raise PersistenceConflictError(
                        f"Impact Report identity has different content: {report_id}"
                    )
                self._validate_report_integrity(
                    cursor,
                    state=existing,
                    artifact=artifact,
                )
                expected_identity = (
                    project_id,
                    case_id,
                    repository_id,
                    repository_revision_id,
                    str(artifact["code_graph_snapshot_id"]),
                    len(cast(list[object], artifact["items"])),
                    tuple(
                        str(value) for value in cast(list[object], artifact["blocking_unknowns"])
                    ),
                )
                actual_identity = (
                    existing.project_id,
                    existing.analysis_case_id,
                    existing.repository_id,
                    existing.repository_revision_id,
                    existing.code_graph_snapshot_id,
                    existing.item_count,
                    existing.blocking_unknowns,
                )
                if actual_identity != expected_identity:
                    raise PersistenceConflictError(
                        f"Impact Report normalized identity differs: {report_id}"
                    )
                if existing.status != "superseded":
                    self._supersede_packets_from_other_reports(
                        cursor,
                        project_id=project_id,
                        analysis_case_id=case_id,
                        current_report_id=report_id,
                    )
                return ImpactReportPublishResult(created=False, state=existing)
            self._validate_report_scope(
                cursor,
                artifact=artifact,
                repository_id=repository_id,
                repository_revision_id=repository_revision_id,
            )
            self._artifacts.store(
                artifact_id=report_id,
                project_id=project_id,
                analysis_case_id=case_id,
                artifact=artifact,
            )
            cursor.execute(
                """
                SELECT impact_report_id
                FROM impact_reports
                WHERE project_id = %s
                  AND analysis_case_id = %s
                  AND status IN ('awaiting_confirmation', 'confirmed', 'blocked')
                ORDER BY impact_report_id
                FOR UPDATE
                """,
                (project_id, case_id),
            )
            for previous_id_row in cursor.fetchall():
                previous_id = str(previous_id_row[0])
                previous_state = self._load_state(cursor, previous_id)
                previous_artifact = self._artifacts.get(previous_id)
                if previous_state is None or previous_artifact is None:
                    raise PersistenceConflictError(
                        f"Previous Impact Report ledger is incomplete: {previous_id}"
                    )
                self._validate_report_integrity(
                    cursor,
                    state=previous_state,
                    artifact=previous_artifact,
                )
            cursor.execute(
                """
                UPDATE impact_reports
                SET status = 'superseded'
                WHERE project_id = %s
                  AND analysis_case_id = %s
                  AND status IN ('awaiting_confirmation', 'confirmed', 'blocked')
                """,
                (project_id, case_id),
            )
            status = str(artifact["status"])
            cursor.execute(
                """
                INSERT INTO impact_reports (
                    impact_report_id,
                    project_id,
                    analysis_case_id,
                    document_snapshot_id,
                    context_package_id,
                    code_graph_snapshot_id,
                    repository_id,
                    repository_revision_id,
                    repository_revision,
                    analysis_policy_version,
                    status,
                    summary,
                    blocking_unknowns
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    report_id,
                    project_id,
                    case_id,
                    artifact["document_snapshot_id"],
                    artifact["context_package_id"],
                    artifact["code_graph_snapshot_id"],
                    repository_id,
                    repository_revision_id,
                    artifact["repository_revision"],
                    artifact.get("analysis_policy_version", "scope-impact-v1"),
                    status,
                    artifact["summary"],
                    _canonical_json(artifact["blocking_unknowns"]),
                ),
            )
            for item in cast(list[dict[str, Any]], artifact["items"]):
                self._insert_item(cursor, artifact=artifact, item=item)
            self._supersede_packets_from_other_reports(
                cursor,
                project_id=project_id,
                analysis_case_id=case_id,
                current_report_id=report_id,
            )
            case_status = "reanalysis_required" if status == "blocked" else "awaiting_confirmation"
            cursor.execute(
                """
                UPDATE analysis_cases
                SET status = %s, updated_at = now()
                WHERE analysis_case_id = %s AND project_id = %s
                """,
                (case_status, case_id, project_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Impact Report Analysis Case does not exist in the Project")
            state = self._load_state(cursor, report_id)
            if state is None:
                raise RuntimeError("Impact Report disappeared during publication")
            self._validate_report_integrity(cursor, state=state, artifact=artifact)
        return ImpactReportPublishResult(created=True, state=state)

    @staticmethod
    def _supersede_packets_from_other_reports(
        cursor: Cursor[Any],
        *,
        project_id: str,
        analysis_case_id: str,
        current_report_id: str,
    ) -> None:
        """Make mutable Packet state follow replacement of its immutable Report source."""

        cursor.execute(
            """
            UPDATE edit_packets
            SET status = 'superseded'
            WHERE project_id = %s
              AND analysis_case_id = %s
              AND status = 'active'
              AND impact_report_id <> %s
            """,
            (project_id, analysis_case_id, current_report_id),
        )

    def confirm(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        artifact: dict[str, Any],
    ) -> ImpactConfirmationResult:
        """Append one complete item decision and reject stale Graph/report state."""

        self._contracts.validate_artifact(artifact)
        if artifact.get("artifact_type") != "ImpactConfirmation":
            raise ValueError("Impact confirmation requires an ImpactConfirmation Artifact")
        confirmation_id = str(artifact["confirmation_id"])
        report_id = str(artifact["impact_report_id"])
        confirmed_at = datetime.fromisoformat(str(artifact["confirmed_at"]).replace("Z", "+00:00"))
        if confirmed_at.tzinfo is None:
            raise ValueError("Impact Confirmation confirmed_at must include a timezone")
        approved = tuple(str(value) for value in cast(list[object], artifact["approved_item_ids"]))
        rejected = tuple(str(value) for value in cast(list[object], artifact["rejected_item_ids"]))
        if set(approved) & set(rejected):
            raise ValueError("Impact confirmation item decisions must not overlap")

        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT impact_report_id, project_id, analysis_case_id, status,
                       code_graph_snapshot_id
                FROM impact_reports
                WHERE impact_report_id = %s
                FOR UPDATE
                """,
                (report_id,),
            )
            report = cursor.fetchone()
            if report is None:
                raise ValueError(f"Impact Report does not exist: {report_id}")
            if (str(report[1]), str(report[2])) != (project_id, analysis_case_id):
                raise ValueError("Impact Report is outside Confirmation scope")
            state = self._load_state(cursor, report_id)
            report_artifact = self._artifacts.get(report_id)
            if state is None or report_artifact is None:
                raise PersistenceConflictError(
                    f"Impact Report normalized/Artifact pair is incomplete: {report_id}"
                )
            self._validate_report_integrity(
                cursor,
                state=state,
                artifact=report_artifact,
            )
            existing = self._load_confirmation(cursor, confirmation_id)
            if existing is not None:
                if self._artifacts.get(confirmation_id) != artifact:
                    raise PersistenceConflictError(
                        f"Impact Confirmation identity has different content: {confirmation_id}"
                    )
                expected_confirmation = (
                    confirmation_id,
                    project_id,
                    analysis_case_id,
                    report_id,
                    str(artifact["confirmed_by"]),
                    list(approved),
                    list(rejected),
                    artifact.get("user_note"),
                    confirmed_at,
                )
                if existing != expected_confirmation:
                    raise PersistenceConflictError(
                        f"Impact Confirmation normalized identity differs: {confirmation_id}"
                    )
                self._validate_report_integrity(
                    cursor,
                    state=state,
                    artifact=report_artifact,
                )
                return ImpactConfirmationResult(False, confirmation_id, report_id, str(report[3]))
            if str(report[3]) != "awaiting_confirmation":
                raise ValueError("Impact Report is not awaiting confirmation")
            context_artifact = self._artifacts.get(str(report_artifact["context_package_id"]))
            if (
                context_artifact is None
                or context_artifact.get("artifact_type")
                not in {"ContextPackage", "CopilotImpactContext"}
            ):
                raise PersistenceConflictError(
                    f"Impact Report Context Package is unavailable: {report_id}"
                )
            cursor.execute(
                """
                SELECT reason FROM profile_drift_impacts
                WHERE project_id = %s
                  AND artifact_type = 'ImpactReport'
                  AND artifact_id = %s
                  AND resolved_at IS NULL
                ORDER BY profile_drift_event_id
                LIMIT 1
                """,
                (project_id, report_id),
            )
            drift = cursor.fetchone()
            if drift is not None:
                raise ValueError(f"Impact Report is blocked by Profile drift: {drift[0]}")
            cursor.execute(
                """
                SELECT is_current, status, %s <= clock_timestamp()
                FROM code_graph_snapshots
                WHERE code_graph_snapshot_id = %s AND project_id = %s
                FOR SHARE
                """,
                (confirmed_at, report[4], project_id),
            )
            graph = cursor.fetchone()
            if graph is None or not bool(graph[0]) or str(graph[1]) != "complete":
                raise ValueError("Impact Report Code Graph is no longer current and complete")
            if not bool(graph[2]):
                raise ValueError("Impact Confirmation confirmed_at must not be in the future")
            cursor.execute(
                """
                SELECT impact_item_id, recommended_action
                FROM impact_items
                WHERE impact_report_id = %s AND project_id = %s
                ORDER BY impact_item_id
                """,
                (report_id, project_id),
            )
            items = {str(item_id): str(action) for item_id, action in cursor.fetchall()}
            if set(approved) | set(rejected) != set(items):
                raise ValueError("Impact confirmation must decide every report item exactly once")
            actionable = {
                item_id
                for item_id, action in items.items()
                if action in {"modify", "add", "delete"}
            }
            if not set(approved) & actionable:
                raise ValueError("Impact confirmation must approve at least one actionable item")
            self._artifacts.store(
                artifact_id=confirmation_id,
                project_id=project_id,
                analysis_case_id=analysis_case_id,
                artifact=artifact,
            )
            cursor.execute(
                """
                INSERT INTO impact_confirmations (
                    confirmation_id,
                    project_id,
                    analysis_case_id,
                    impact_report_id,
                    confirmed_by,
                    approved_item_ids,
                    rejected_item_ids,
                    user_note,
                    confirmed_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                """,
                (
                    confirmation_id,
                    project_id,
                    analysis_case_id,
                    report_id,
                    artifact["confirmed_by"],
                    _canonical_json(list(approved)),
                    _canonical_json(list(rejected)),
                    artifact.get("user_note"),
                    confirmed_at,
                ),
            )
            cursor.execute(
                """
                UPDATE impact_reports
                SET status = 'confirmed', confirmed_at = %s
                WHERE impact_report_id = %s
                """,
                (confirmed_at, report_id),
            )
            confirmed_state = self._load_state(cursor, report_id)
            if confirmed_state is None:
                raise RuntimeError("Confirmed Impact Report disappeared")
            self._validate_report_integrity(
                cursor,
                state=confirmed_state,
                artifact=report_artifact,
            )
        return ImpactConfirmationResult(True, confirmation_id, report_id, "confirmed")

    def get_state(self, impact_report_id: str) -> ImpactReportState | None:
        """Return the current normalized state without rewriting the Artifact."""

        if not impact_report_id.strip():
            raise ValueError("impact_report_id must not be blank")
        with self._connection.cursor() as cursor:
            state = self._load_state(cursor, impact_report_id)
        if state is None:
            return None
        artifact = self._artifacts.get(impact_report_id)
        if artifact is None:
            raise PersistenceConflictError(
                f"Impact Report normalized row has no immutable Artifact: {impact_report_id}"
            )
        with self._connection.cursor() as cursor:
            self._validate_report_integrity(cursor, state=state, artifact=artifact)
        return state

    def _validate_report_integrity(
        self,
        cursor: Cursor[Any],
        *,
        state: ImpactReportState,
        artifact: dict[str, Any],
    ) -> None:
        if artifact.get("artifact_type") != "ImpactReport":
            raise PersistenceConflictError(
                f"Impact Report Artifact type differs: {state.impact_report_id}"
            )
        self._contracts.validate_artifact(artifact)
        _validate_report_semantics(artifact)
        context = self._artifacts.get(str(artifact["context_package_id"]))
        expected_context = (
            artifact["project_id"],
            artifact["analysis_case_id"],
            artifact["document_snapshot_id"],
        )
        actual_context = _impact_context_scope(context)
        if actual_context is None:
            raise PersistenceConflictError(
                f"Impact Report Context Package is missing: {state.impact_report_id}"
            )
        if actual_context != expected_context:
            raise PersistenceConflictError(
                f"Impact Report Context Package scope differs: {state.impact_report_id}"
            )
        graph = self._graphs.get(str(artifact["code_graph_snapshot_id"]))
        expected_graph = (
            artifact["project_id"],
            artifact["code_graph_snapshot_id"],
            artifact["repository_revision"],
        )
        actual_graph = (
            graph.get("project_id") if graph is not None else None,
            graph.get("code_graph_snapshot_id") if graph is not None else None,
            graph.get("repository_revision") if graph is not None else None,
        )
        if actual_graph != expected_graph:
            raise PersistenceConflictError(
                f"Impact Report Code Graph scope differs: {state.impact_report_id}"
            )
        cursor.execute(
            """
            SELECT impact_report_id, project_id, analysis_case_id,
                   document_snapshot_id, context_package_id, code_graph_snapshot_id,
                   repository_id, repository_revision_id, repository_revision,
                   analysis_policy_version, status, summary, blocking_unknowns,
                   confirmed_at
            FROM impact_reports
            WHERE impact_report_id = %s
            """,
            (state.impact_report_id,),
        )
        header = cursor.fetchone()
        if header is None:
            raise PersistenceConflictError(
                f"Impact Report normalized header disappeared: {state.impact_report_id}"
            )
        expected_immutable = (
            str(artifact["impact_report_id"]),
            str(artifact["project_id"]),
            str(artifact["analysis_case_id"]),
            str(artifact["document_snapshot_id"]),
            str(artifact["context_package_id"]),
            str(artifact["code_graph_snapshot_id"]),
            state.repository_id,
            state.repository_revision_id,
            str(artifact["repository_revision"]),
            str(artifact.get("analysis_policy_version", "scope-impact-v1")),
            str(artifact["summary"]),
            tuple(str(value) for value in cast(list[object], artifact["blocking_unknowns"])),
        )
        actual_immutable = (
            str(header[0]),
            str(header[1]),
            str(header[2]),
            str(header[3]),
            str(header[4]),
            str(header[5]),
            str(header[6]),
            str(header[7]),
            str(header[8]),
            str(header[9]),
            str(header[11]),
            tuple(str(value) for value in cast(list[object], header[12])),
        )
        if actual_immutable != expected_immutable:
            raise PersistenceConflictError(
                f"Impact Report normalized header differs: {state.impact_report_id}"
            )
        status = str(header[10])
        confirmed_at = cast(datetime | None, header[13])
        artifact_status = str(artifact["status"])
        allowed_statuses = (
            {"blocked", "superseded"}
            if artifact_status == "blocked"
            else {"awaiting_confirmation", "confirmed", "superseded"}
        )
        if status not in allowed_statuses:
            raise PersistenceConflictError(
                f"Impact Report lifecycle state differs: {state.impact_report_id}"
            )
        actual_state = (
            str(header[0]),
            str(header[1]),
            str(header[2]),
            str(header[6]),
            str(header[7]),
            str(header[5]),
            status,
            tuple(str(value) for value in cast(list[object], header[12])),
            confirmed_at,
        )
        expected_state = (
            state.impact_report_id,
            state.project_id,
            state.analysis_case_id,
            state.repository_id,
            state.repository_revision_id,
            state.code_graph_snapshot_id,
            state.status,
            state.blocking_unknowns,
            state.confirmed_at,
        )
        if actual_state != expected_state:
            raise PersistenceConflictError(
                f"Impact Report state read drifted: {state.impact_report_id}"
            )

        expected_items = _impact_item_rows(artifact)
        actual_items = self._load_item_rows(cursor, state.impact_report_id)
        if actual_items != expected_items or state.item_count != len(expected_items):
            raise PersistenceConflictError(
                f"Impact Report Item ledger differs: {state.impact_report_id}"
            )
        self._validate_confirmation_integrity(
            cursor,
            state=state,
            item_rows=actual_items,
        )

    def _validate_confirmation_integrity(
        self,
        cursor: Cursor[Any],
        *,
        state: ImpactReportState,
        item_rows: tuple[ImpactItemLedgerRow, ...],
    ) -> None:
        cursor.execute(
            """
            SELECT confirmation_id, project_id, analysis_case_id, impact_report_id,
                   confirmed_by, approved_item_ids, rejected_item_ids, user_note,
                   confirmed_at
            FROM impact_confirmations
            WHERE impact_report_id = %s
            """,
            (state.impact_report_id,),
        )
        row = cursor.fetchone()
        if row is None:
            if state.status == "confirmed" or state.confirmed_at is not None:
                raise PersistenceConflictError(
                    f"Impact Report confirmation ledger is missing: {state.impact_report_id}"
                )
            return
        confirmation_id = str(row[0])
        confirmation_artifact = self._artifacts.get(confirmation_id)
        if confirmation_artifact is None:
            raise PersistenceConflictError(
                f"Impact Confirmation normalized row has no Artifact: {confirmation_id}"
            )
        self._contracts.validate_artifact(confirmation_artifact)
        if confirmation_artifact.get("artifact_type") != "ImpactConfirmation":
            raise PersistenceConflictError(
                f"Impact Confirmation Artifact type differs: {confirmation_id}"
            )
        confirmed_at = datetime.fromisoformat(
            str(confirmation_artifact["confirmed_at"]).replace("Z", "+00:00")
        )
        expected = (
            str(confirmation_artifact["confirmation_id"]),
            state.project_id,
            state.analysis_case_id,
            str(confirmation_artifact["impact_report_id"]),
            str(confirmation_artifact["confirmed_by"]),
            tuple(
                str(value)
                for value in cast(list[object], confirmation_artifact["approved_item_ids"])
            ),
            tuple(
                str(value)
                for value in cast(list[object], confirmation_artifact["rejected_item_ids"])
            ),
            confirmation_artifact.get("user_note"),
            confirmed_at,
        )
        actual = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            tuple(str(value) for value in cast(list[object], row[5])),
            tuple(str(value) for value in cast(list[object], row[6])),
            row[7],
            cast(datetime, row[8]),
        )
        if actual != expected:
            raise PersistenceConflictError(
                f"Impact Confirmation normalized identity differs: {confirmation_id}"
            )
        approved = set(expected[5])
        rejected = set(expected[6])
        item_ids = {item[1] for item in item_rows}
        actionable = {item[1] for item in item_rows if item[7] in {"modify", "add", "delete"}}
        if (
            approved & rejected
            or approved | rejected != item_ids
            or not approved & actionable
            or state.confirmed_at != confirmed_at
            or state.status not in {"confirmed", "superseded"}
        ):
            raise PersistenceConflictError(
                f"Impact Confirmation decision ledger differs: {confirmation_id}"
            )

    @staticmethod
    def _load_item_rows(
        cursor: Cursor[Any],
        report_id: str,
    ) -> tuple[ImpactItemLedgerRow, ...]:
        cursor.execute(
            """
            SELECT project_id, impact_item_id, structured_change_refs, target_path,
                   target_symbols, impact_level, impact_score, recommended_action,
                   rationale, evidence_refs, graph_path_refs, test_file_refs,
                   requires_confirmation, unknowns
            FROM impact_items
            WHERE impact_report_id = %s
            ORDER BY impact_item_id
            """,
            (report_id,),
        )
        return tuple(_impact_item_row(tuple(row)) for row in cursor.fetchall())

    def _validate_report_scope(
        self,
        cursor: Cursor[Any],
        *,
        artifact: dict[str, Any],
        repository_id: str,
        repository_revision_id: str,
    ) -> None:
        project_id = str(artifact["project_id"])
        case_id = str(artifact["analysis_case_id"])
        context = self._artifacts.get(str(artifact["context_package_id"]))
        actual_context = _impact_context_scope(context)
        if actual_context is None:
            raise ValueError("Impact Report Context Package does not exist")
        expected_context = (
            project_id,
            case_id,
            artifact["document_snapshot_id"],
        )
        if actual_context != expected_context:
            raise ValueError("Impact Report Context Package is outside report scope")
        cursor.execute(
            """
            SELECT graph.repository_id, graph.repository_revision_id,
                   revision.commit_sha, graph.is_current, graph.status
            FROM code_graph_snapshots AS graph
            JOIN repository_revisions AS revision
              ON revision.repository_revision_id = graph.repository_revision_id
             AND revision.repository_id = graph.repository_id
            WHERE graph.code_graph_snapshot_id = %s AND graph.project_id = %s
            FOR SHARE OF graph, revision
            """,
            (artifact["code_graph_snapshot_id"], project_id),
        )
        graph = cursor.fetchone()
        expected_graph = (
            repository_id,
            repository_revision_id,
            artifact["repository_revision"],
        )
        if graph is None or tuple(graph[:3]) != expected_graph:
            raise ValueError("Impact Report Graph/Repository Revision scope does not match")
        if not bool(graph[3]) or str(graph[4]) not in {"complete", "truncated"}:
            raise ValueError("Impact Report Code Graph is not current and queryable")
        if str(artifact["status"]) == "awaiting_confirmation" and str(graph[4]) != "complete":
            raise ValueError("Only a complete Code Graph can produce a confirmable report")
        cursor.execute(
            """
            SELECT artifact_type, artifact_id, reason
            FROM profile_drift_impacts
            WHERE project_id = %s
              AND resolved_at IS NULL
              AND (
                  (artifact_type = 'DocumentSnapshot' AND artifact_id = %s)
                  OR (artifact_type = 'CodeGraphSnapshot' AND artifact_id = %s)
              )
            ORDER BY artifact_type, artifact_id
            LIMIT 1
            """,
            (
                project_id,
                artifact["document_snapshot_id"],
                artifact["code_graph_snapshot_id"],
            ),
        )
        drift = cursor.fetchone()
        if drift is not None:
            raise ValueError(
                f"Impact Report source is stale by Profile drift: {drift[0]} {drift[1]}"
            )

    @staticmethod
    def _insert_item(
        cursor: Cursor[Any], *, artifact: dict[str, Any], item: dict[str, Any]
    ) -> None:
        cursor.execute(
            """
            INSERT INTO impact_items (
                impact_report_id, project_id, impact_item_id, structured_change_refs,
                target_path, target_symbols, impact_level, impact_score,
                recommended_action, rationale, evidence_refs, graph_path_refs,
                test_file_refs, requires_confirmation, unknowns
            ) VALUES (
                %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb
            )
            """,
            (
                artifact["impact_report_id"],
                artifact["project_id"],
                item["impact_item_id"],
                _canonical_json(item["structured_change_refs"]),
                item["target_path"],
                _canonical_json(item["target_symbols"]),
                item["impact_level"],
                item.get("impact_score"),
                item["recommended_action"],
                item.get("rationale"),
                _canonical_json(item["evidence_refs"]),
                _canonical_json(item["graph_path_refs"]),
                _canonical_json(item.get("test_file_refs", [])),
                item["requires_confirmation"],
                _canonical_json(item.get("unknowns", [])),
            ),
        )

    @staticmethod
    def _load_state(cursor: Cursor[Any], report_id: str) -> ImpactReportState | None:
        cursor.execute(
            """
            SELECT report.impact_report_id, report.project_id, report.analysis_case_id,
                   report.repository_id, report.repository_revision_id,
                   report.code_graph_snapshot_id, report.status,
                   count(item.impact_item_id), report.blocking_unknowns,
                   report.confirmed_at
            FROM impact_reports AS report
            LEFT JOIN impact_items AS item
              ON item.impact_report_id = report.impact_report_id
             AND item.project_id = report.project_id
            WHERE report.impact_report_id = %s
            GROUP BY report.impact_report_id
            """,
            (report_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ImpactReportState(
            impact_report_id=str(row[0]),
            project_id=str(row[1]),
            analysis_case_id=str(row[2]),
            repository_id=str(row[3]),
            repository_revision_id=str(row[4]),
            code_graph_snapshot_id=str(row[5]),
            status=str(row[6]),
            item_count=int(row[7]),
            blocking_unknowns=tuple(str(value) for value in cast(list[object], row[8])),
            confirmed_at=cast(datetime | None, row[9]),
        )

    @staticmethod
    def _load_confirmation(cursor: Cursor[Any], confirmation_id: str) -> tuple[object, ...] | None:
        cursor.execute(
            """
            SELECT confirmation_id, project_id, analysis_case_id, impact_report_id,
                   confirmed_by, approved_item_ids, rejected_item_ids, user_note,
                   confirmed_at
            FROM impact_confirmations
            WHERE confirmation_id = %s
            """,
            (confirmation_id,),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None


def _impact_context_scope(
    context: dict[str, Any] | None,
) -> tuple[object, object, object] | None:
    if context is None:
        return None
    artifact_type = context.get("artifact_type")
    if artifact_type == "ContextPackage":
        snapshot_id = context.get("document_snapshot_id")
    elif artifact_type == "CopilotImpactContext":
        snapshot_id = context.get("target_document_snapshot_id")
    else:
        return None
    return (
        context.get("project_id"),
        context.get("analysis_case_id"),
        snapshot_id,
    )


def _validate_report_semantics(artifact: dict[str, Any]) -> None:
    status = str(artifact["status"])
    if status not in {"awaiting_confirmation", "blocked"}:
        raise ValueError("A new Impact Report must be awaiting_confirmation or blocked")
    unknowns = cast(list[object], artifact["blocking_unknowns"])
    if (status == "blocked") != bool(unknowns):
        raise ValueError("Impact Report status must agree with blocking_unknowns")
    items = cast(list[dict[str, Any]], artifact["items"])
    item_ids = [str(item["impact_item_id"]) for item in items]
    paths = [str(item["target_path"]) for item in items]
    if len(item_ids) != len(set(item_ids)) or len(paths) != len(set(paths)):
        raise ValueError("Impact Report item IDs and target paths must be unique")


def _impact_item_rows(artifact: dict[str, Any]) -> tuple[ImpactItemLedgerRow, ...]:
    project_id = str(artifact["project_id"])
    return tuple(
        sorted(
            _impact_item_row(
                (
                    project_id,
                    item["impact_item_id"],
                    item["structured_change_refs"],
                    item["target_path"],
                    item["target_symbols"],
                    item["impact_level"],
                    item.get("impact_score"),
                    item["recommended_action"],
                    item.get("rationale"),
                    item["evidence_refs"],
                    item["graph_path_refs"],
                    item.get("test_file_refs", []),
                    item["requires_confirmation"],
                    item.get("unknowns", []),
                )
            )
            for item in cast(list[dict[str, Any]], artifact["items"])
        )
    )


def _impact_item_row(row: tuple[object, ...]) -> ImpactItemLedgerRow:
    score = float(cast(float | int, row[6])) if row[6] is not None else None
    return (
        str(row[0]),
        str(row[1]),
        tuple(str(value) for value in cast(list[object], row[2])),
        str(row[3]),
        tuple(str(value) for value in cast(list[object], row[4])),
        str(row[5]),
        score,
        str(row[7]),
        str(row[8]) if row[8] is not None else None,
        tuple(str(value) for value in cast(list[object], row[9])),
        tuple(str(value) for value in cast(list[object], row[10])),
        tuple(str(value) for value in cast(list[object], row[11])),
        bool(row[12]),
        tuple(str(value) for value in cast(list[object], row[13])),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
