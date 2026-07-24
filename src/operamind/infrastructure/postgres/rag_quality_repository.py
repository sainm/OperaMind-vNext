"""Canonical Golden RAG quality reports and exact-scope formal-analysis gate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.domain import RagQueryPurpose
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError


class GoldenRagQualityGateBlockedError(ValueError):
    """Raised when the latest exact-scope Golden report does not pass."""


@dataclass(frozen=True, slots=True)
class GoldenRagQualityState:
    report_id: str
    case_id: str
    dataset_id: str
    dataset_version: str
    project_id: str
    document_snapshot_id: str
    embedding_profile_version_id: str
    embedding_profile_binding_key: str
    search_index_build_id: str
    ranking_policy_version: str
    query_plan_version: str
    expectation_digest: str
    status: str
    recall_at_5: float | None
    recall_at_10: float | None
    mrr: float | None
    irrelevant_rate: float | None
    cross_project_leaks: int | None
    threshold_failures: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    report_digest: str
    created_by: str
    created_at: datetime


class GoldenRagQualityRepository:
    """Store immutable reports and resolve the latest exact-scope quality decision."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)

    def publish(
        self, *, artifact: dict[str, Any], created_by: str
    ) -> tuple[bool, GoldenRagQualityState]:
        if not created_by.strip() or len(created_by) > 500:
            raise ValueError("Golden RAG report creator must be non-blank and bounded")
        self._contracts.validate_artifact(artifact)
        _validate_report_semantics(artifact)
        report_id = str(artifact["report_id"])
        report_digest = _digest(_json(artifact))
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT report_digest FROM golden_rag_quality_reports
                WHERE report_id = %s FOR UPDATE
                """,
                (report_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing[0]) != report_digest:
                    raise PersistenceConflictError(
                        f"Golden RAG report identity has different content: {report_id}"
                    )
                created = False
            else:
                self._artifacts.store(
                    artifact_id=report_id,
                    project_id=str(artifact["project_id"]),
                    analysis_case_id=None,
                    artifact=artifact,
                )
                self._insert_report(
                    cursor,
                    artifact=artifact,
                    report_digest=report_digest,
                    created_by=created_by,
                )
                created = True
        state = self.get(report_id)
        if state is None:
            raise RuntimeError("Golden RAG report disappeared after publication")
        return created, state

    def get(self, report_id: str) -> GoldenRagQualityState | None:
        if not report_id.strip():
            raise ValueError("Golden RAG report ID must not be blank")
        with self._connection.cursor() as cursor:
            state = self._load_state(cursor, report_id)
            query_rows = self._load_query_rows(cursor, report_id) if state is not None else ()
        if state is None:
            return None
        artifact = self._artifacts.get(report_id)
        if artifact is None:
            raise PersistenceConflictError(
                f"Golden RAG report normalized row has no Artifact: {report_id}"
            )
        self._validate_integrity(state, artifact, query_rows)
        return state

    def latest_for_scope(
        self,
        *,
        project_id: str,
        document_snapshot_id: str,
        embedding_profile_version_id: str,
        search_index_build_id: str,
    ) -> GoldenRagQualityState | None:
        values = (
            project_id,
            document_snapshot_id,
            embedding_profile_version_id,
            search_index_build_id,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Golden RAG quality scope must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT report_id FROM golden_rag_quality_reports
                WHERE project_id = %s AND document_snapshot_id = %s
                  AND embedding_profile_version_id = %s
                  AND search_index_build_id = %s
                ORDER BY publication_sequence DESC
                LIMIT 1
                """,
                values,
            )
            row = cursor.fetchone()
        return self.get(str(row[0])) if row is not None else None

    def require_passed_gate(
        self,
        *,
        project_id: str,
        document_snapshot_id: str,
        embedding_profile_version_id: str,
        search_index_build_id: str,
    ) -> GoldenRagQualityState:
        state = self.latest_for_scope(
            project_id=project_id,
            document_snapshot_id=document_snapshot_id,
            embedding_profile_version_id=embedding_profile_version_id,
            search_index_build_id=search_index_build_id,
        )
        if state is None:
            raise GoldenRagQualityGateBlockedError(
                "No Golden RAG quality report exists for the current Snapshot/Profile/Index"
            )
        if state.status != "passed":
            reasons = state.failure_reasons or state.threshold_failures
            detail = ", ".join(reasons) if reasons else state.status
            raise GoldenRagQualityGateBlockedError(
                f"Latest Golden RAG quality report blocks formal analysis: {detail}"
            )
        return state

    @staticmethod
    def _insert_report(
        cursor: Cursor[Any],
        *,
        artifact: dict[str, Any],
        report_digest: str,
        created_by: str,
    ) -> None:
        metrics = artifact["metrics"]
        metric = cast(dict[str, object], metrics) if isinstance(metrics, dict) else None
        cursor.execute(
            """
            INSERT INTO golden_rag_quality_reports (
                report_id, case_id, dataset_id, dataset_version, project_id,
                document_snapshot_id, embedding_profile_version_id,
                embedding_profile_binding_key, search_index_build_id,
                ranking_policy_version, query_plan_version, expectation_digest, status,
                recall_at_5, recall_at_10, mrr, irrelevant_rate,
                cross_project_leaks, threshold_failures, failure_reasons,
                report_digest, created_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s
            )
            """,
            (
                artifact["report_id"],
                artifact["case_id"],
                artifact["dataset_id"],
                artifact["dataset_version"],
                artifact["project_id"],
                artifact["document_snapshot_id"],
                artifact["embedding_profile_version_id"],
                artifact["embedding_profile_binding_key"],
                artifact["search_index_build_id"],
                artifact["ranking_policy_version"],
                artifact["query_plan_version"],
                artifact["expectation_digest"],
                artifact["status"],
                metric.get("recall_at_5") if metric else None,
                metric.get("recall_at_10") if metric else None,
                metric.get("mrr") if metric else None,
                metric.get("irrelevant_rate") if metric else None,
                metric.get("cross_project_leaks") if metric else None,
                _json(artifact["threshold_failures"]),
                _json(artifact["failure_reasons"]),
                report_digest,
                created_by,
            ),
        )
        for result in cast(list[dict[str, object]], artifact["query_results"]):
            cursor.execute(
                """
                INSERT INTO golden_rag_query_results (
                    report_id, project_id, query_purpose, query_text_digest,
                    candidates, failure_reasons
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    artifact["report_id"],
                    artifact["project_id"],
                    result["query_purpose"],
                    result["query_text_digest"],
                    _json(result["candidates"]),
                    _json(result["failure_reasons"]),
                ),
            )

    @staticmethod
    def _load_state(cursor: Cursor[Any], report_id: str) -> GoldenRagQualityState | None:
        cursor.execute(
            """
            SELECT report_id, case_id, dataset_id, dataset_version, project_id,
                   document_snapshot_id, embedding_profile_version_id,
                   embedding_profile_binding_key, search_index_build_id,
                   ranking_policy_version, query_plan_version, expectation_digest, status,
                   recall_at_5, recall_at_10, mrr, irrelevant_rate,
                   cross_project_leaks, threshold_failures, failure_reasons,
                   report_digest, created_by, created_at
            FROM golden_rag_quality_reports WHERE report_id = %s
            """,
            (report_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return GoldenRagQualityState(
            report_id=str(row[0]),
            case_id=str(row[1]),
            dataset_id=str(row[2]),
            dataset_version=str(row[3]),
            project_id=str(row[4]),
            document_snapshot_id=str(row[5]),
            embedding_profile_version_id=str(row[6]),
            embedding_profile_binding_key=str(row[7]),
            search_index_build_id=str(row[8]),
            ranking_policy_version=str(row[9]),
            query_plan_version=str(row[10]),
            expectation_digest=str(row[11]),
            status=str(row[12]),
            recall_at_5=float(row[13]) if row[13] is not None else None,
            recall_at_10=float(row[14]) if row[14] is not None else None,
            mrr=float(row[15]) if row[15] is not None else None,
            irrelevant_rate=float(row[16]) if row[16] is not None else None,
            cross_project_leaks=int(row[17]) if row[17] is not None else None,
            threshold_failures=tuple(str(value) for value in row[18]),
            failure_reasons=tuple(str(value) for value in row[19]),
            report_digest=str(row[20]),
            created_by=str(row[21]),
            created_at=cast(datetime, row[22]),
        )

    @staticmethod
    def _load_query_rows(
        cursor: Cursor[Any], report_id: str
    ) -> tuple[tuple[str, str, object, object], ...]:
        cursor.execute(
            """
            SELECT query_purpose, query_text_digest, candidates, failure_reasons
            FROM golden_rag_query_results
            WHERE report_id = %s
            ORDER BY CASE query_purpose
                WHEN 'business_behavior' THEN 1
                WHEN 'precise_anchor' THEN 2
                WHEN 'acceptance_criteria' THEN 3
                ELSE 4
            END
            """,
            (report_id,),
        )
        return tuple((str(row[0]), str(row[1]), row[2], row[3]) for row in cursor.fetchall())

    def _validate_integrity(
        self,
        state: GoldenRagQualityState,
        artifact: dict[str, Any],
        query_rows: tuple[tuple[str, str, object, object], ...],
    ) -> None:
        self._contracts.validate_artifact(artifact)
        _validate_report_semantics(artifact)
        if _digest(_json(artifact)) != state.report_digest:
            raise PersistenceConflictError(
                f"Golden RAG report Artifact digest differs: {state.report_id}"
            )
        expected = (
            state.report_id,
            state.case_id,
            state.dataset_id,
            state.dataset_version,
            state.project_id,
            state.document_snapshot_id,
            state.embedding_profile_version_id,
            state.embedding_profile_binding_key,
            state.search_index_build_id,
            state.ranking_policy_version,
            state.query_plan_version,
            state.expectation_digest,
            state.status,
            state.threshold_failures,
            state.failure_reasons,
        )
        actual = (
            artifact["report_id"],
            artifact["case_id"],
            artifact["dataset_id"],
            artifact["dataset_version"],
            artifact["project_id"],
            artifact["document_snapshot_id"],
            artifact["embedding_profile_version_id"],
            artifact["embedding_profile_binding_key"],
            artifact["search_index_build_id"],
            artifact["ranking_policy_version"],
            artifact["query_plan_version"],
            artifact["expectation_digest"],
            artifact["status"],
            tuple(artifact["threshold_failures"]),
            tuple(artifact["failure_reasons"]),
        )
        if actual != expected:
            raise PersistenceConflictError(
                f"Golden RAG report normalized scope differs: {state.report_id}"
            )
        metrics = cast(dict[str, object] | None, artifact["metrics"])
        normalized_metrics = (
            state.recall_at_5,
            state.recall_at_10,
            state.mrr,
            state.irrelevant_rate,
            state.cross_project_leaks,
        )
        artifact_metrics = (
            (
                float(cast(float | int, metrics["recall_at_5"])),
                float(cast(float | int, metrics["recall_at_10"])),
                float(cast(float | int, metrics["mrr"])),
                float(cast(float | int, metrics["irrelevant_rate"])),
                int(cast(int, metrics["cross_project_leaks"])),
            )
            if metrics is not None
            else (None, None, None, None, None)
        )
        if normalized_metrics != artifact_metrics:
            raise PersistenceConflictError(
                f"Golden RAG report normalized metrics differ: {state.report_id}"
            )
        artifact_query_rows = tuple(
            (
                str(result["query_purpose"]),
                str(result["query_text_digest"]),
                result["candidates"],
                result["failure_reasons"],
            )
            for result in cast(list[dict[str, object]], artifact["query_results"])
        )
        if _json(query_rows) != _json(artifact_query_rows):
            raise PersistenceConflictError(
                f"Golden RAG report normalized query results differ: {state.report_id}"
            )


def _validate_report_semantics(artifact: dict[str, Any]) -> None:
    results = cast(list[dict[str, object]], artifact["query_results"])
    purposes = tuple(str(result["query_purpose"]) for result in results)
    if purposes != tuple(purpose.value for purpose in RagQueryPurpose):
        raise ValueError("Golden RAG report requires three query purposes in canonical order")
    for result in results:
        query_text = str(result["query_text"])
        if _digest(query_text) != result["query_text_digest"]:
            raise ValueError("Golden RAG query text digest does not match query text")
        candidates = cast(list[dict[str, object]], result["candidates"])
        ranks = [int(cast(int, candidate["rank"])) for candidate in candidates]
        if ranks != list(range(1, len(candidates) + 1)):
            raise ValueError("Golden RAG candidates must have contiguous rank order")
        candidate_ids = [str(candidate["target_id"]) for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Golden RAG candidate IDs must be unique per query")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = [
    "GoldenRagQualityGateBlockedError",
    "GoldenRagQualityRepository",
    "GoldenRagQualityState",
]
