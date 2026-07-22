"""Confirmed Impact source loading and immutable Edit Packet persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.impact_repository import ImpactRepository


@dataclass(frozen=True, slots=True)
class ConfirmedImpactItem:
    impact_item_id: str
    target_path: str
    target_symbols: tuple[str, ...]
    recommended_action: str
    test_file_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EditPacketSource:
    project_id: str
    analysis_case_id: str
    impact_report_id: str
    confirmation_id: str
    repository_id: str
    repository_revision_id: str
    commit_sha: str
    remote_url: str
    workspace_root: str
    business_summary: str
    required_ui_scenario_refs: tuple[str, ...]
    approved_item_ids: tuple[str, ...]
    items: tuple[ConfirmedImpactItem, ...]


@dataclass(frozen=True, slots=True)
class EditPacketPublishResult:
    created: bool
    edit_packet_id: str
    status: str


@dataclass(frozen=True, slots=True)
class EditPacketRecord:
    """Integrity-checked Packet Artifact and its normalized lifecycle state."""

    artifact: dict[str, Any]
    project_id: str
    analysis_case_id: str
    repository_revision_id: str
    status: str
    impact_report_status: str


class EditPacketRepository:
    """Require a current confirmed report and persist one active Packet per Case."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)
        self._impacts = ImpactRepository(connection, contracts)

    def load_source(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        impact_report_id: str,
        confirmation_id: str,
    ) -> EditPacketSource:
        report_state = self._impacts.get_state(impact_report_id)
        if report_state is None:
            raise ValueError("Confirmed Impact source does not exist in Packet scope")
        if (
            report_state.project_id != project_id
            or report_state.analysis_case_id != analysis_case_id
            or report_state.status != "confirmed"
        ):
            raise ValueError("Impact Report is not currently confirmed in Packet scope")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT report.repository_id, report.repository_revision_id,
                       report.repository_revision, repository.remote_url,
                       repository.workspace_root, report.code_graph_snapshot_id,
                       graph.is_current, graph.status, report.status,
                       confirmation.approved_item_ids
                FROM impact_reports AS report
                JOIN impact_confirmations AS confirmation
                  ON confirmation.impact_report_id = report.impact_report_id
                 AND confirmation.project_id = report.project_id
                JOIN repositories AS repository
                  ON repository.repository_id = report.repository_id
                 AND repository.project_id = report.project_id
                JOIN code_graph_snapshots AS graph
                  ON graph.code_graph_snapshot_id = report.code_graph_snapshot_id
                 AND graph.project_id = report.project_id
                WHERE report.project_id = %s
                  AND report.analysis_case_id = %s
                  AND report.impact_report_id = %s
                  AND confirmation.confirmation_id = %s
                """,
                (project_id, analysis_case_id, impact_report_id, confirmation_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Confirmed Impact source does not exist in Packet scope")
            if str(row[8]) != "confirmed":
                raise ValueError("Impact Report is not currently confirmed")
            if not bool(row[6]) or str(row[7]) != "complete":
                raise ValueError("Edit Packet requires a current complete Code Graph")
            if row[4] is None:
                raise ValueError("Edit Packet Repository workspace_root is not registered")
            cursor.execute(
                """
                SELECT impact_item_id, target_path, target_symbols,
                       recommended_action, test_file_refs
                FROM impact_items
                WHERE impact_report_id = %s AND project_id = %s
                ORDER BY impact_item_id
                """,
                (impact_report_id, project_id),
            )
            items = tuple(
                ConfirmedImpactItem(
                    impact_item_id=str(item[0]),
                    target_path=str(item[1]),
                    target_symbols=tuple(str(value) for value in cast(list[object], item[2])),
                    recommended_action=str(item[3]),
                    test_file_refs=tuple(str(value) for value in cast(list[object], item[4])),
                )
                for item in cursor.fetchall()
            )
        report_artifact = self._artifacts.get(impact_report_id)
        if report_artifact is None:
            raise RuntimeError("Normalized Impact Report has no immutable Artifact")
        return EditPacketSource(
            project_id=project_id,
            analysis_case_id=analysis_case_id,
            impact_report_id=impact_report_id,
            confirmation_id=confirmation_id,
            repository_id=str(row[0]),
            repository_revision_id=str(row[1]),
            commit_sha=str(row[2]),
            remote_url=str(row[3]),
            workspace_root=str(row[4]),
            business_summary=str(report_artifact["summary"]),
            required_ui_scenario_refs=tuple(
                str(value)
                for value in cast(list[object], report_artifact["required_ui_scenario_refs"])
            ),
            approved_item_ids=tuple(str(value) for value in cast(list[object], row[9])),
            items=items,
        )

    def get(self, edit_packet_id: str) -> EditPacketRecord | None:
        """Load one Packet only after validating its complete immutable provenance."""

        if not edit_packet_id.strip():
            raise ValueError("edit_packet_id must not be blank")
        artifact = self._artifacts.get(edit_packet_id)
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT packet.edit_packet_id, packet.project_id,
                       packet.analysis_case_id, packet.impact_report_id,
                       packet.confirmation_id, packet.repository_id,
                       packet.repository_revision_id,
                       packet.base_repository_revision, packet.status,
                       packet.editable_files, packet.read_only_files,
                       packet.test_files, packet.forbidden_globs,
                       packet.allowed_items, packet.required_ui_scenario_refs,
                       revision.commit_sha, confirmation.impact_report_id
                FROM edit_packets AS packet
                JOIN repository_revisions AS revision
                  ON revision.repository_revision_id = packet.repository_revision_id
                 AND revision.repository_id = packet.repository_id
                JOIN impact_confirmations AS confirmation
                  ON confirmation.confirmation_id = packet.confirmation_id
                 AND confirmation.project_id = packet.project_id
                WHERE packet.edit_packet_id = %s
                """,
                (edit_packet_id,),
            )
            row = cursor.fetchone()
        if row is None:
            if artifact is not None:
                raise PersistenceConflictError(
                    f"Edit Packet Artifact has no normalized identity: {edit_packet_id}"
                )
            return None
        if artifact is None:
            raise PersistenceConflictError(
                f"Edit Packet normalized identity has no immutable Artifact: {edit_packet_id}"
            )
        report_status = self._validate_packet_integrity(artifact=artifact, row=tuple(row))
        return EditPacketRecord(
            artifact=artifact,
            project_id=str(row[1]),
            analysis_case_id=str(row[2]),
            repository_revision_id=str(row[6]),
            status=str(row[8]),
            impact_report_status=report_status,
        )

    def publish(
        self, *, artifact: dict[str, Any], source: EditPacketSource
    ) -> EditPacketPublishResult:
        self._contracts.validate_artifact(artifact)
        if artifact.get("artifact_type") != "CopilotEditPacket":
            raise ValueError("Edit Packet publication requires CopilotEditPacket")
        _validate_packet_semantics(artifact=artifact, source=source)
        expected = (
            source.project_id,
            source.impact_report_id,
            source.confirmation_id,
            source.repository_id,
            source.commit_sha,
        )
        actual = (
            artifact["project_id"],
            artifact["impact_report_id"],
            artifact["confirmation_id"],
            artifact["repository_id"],
            artifact["base_repository_revision"],
        )
        if actual != expected:
            raise ValueError("Edit Packet Artifact is outside confirmed Impact scope")
        packet_id = str(artifact["edit_packet_id"])
        with self._connection.transaction(), self._connection.cursor() as cursor:
            report_state = self._impacts.get_state(source.impact_report_id)
            if (
                report_state is None
                or report_state.project_id != source.project_id
                or report_state.analysis_case_id != source.analysis_case_id
            ):
                raise ValueError("Edit Packet Impact source integrity changed")
            existing = self._artifacts.get(packet_id)
            if existing is not None:
                if existing != artifact:
                    raise PersistenceConflictError(
                        f"Edit Packet identity has different content: {packet_id}"
                    )
                packet = self.get(packet_id)
                if packet is None:
                    raise PersistenceConflictError(
                        f"Edit Packet normalized identity is missing: {packet_id}"
                    )
                _validate_record_source(packet=packet, source=source)
                return EditPacketPublishResult(False, packet_id, packet.status)
            cursor.execute(
                """
                SELECT report.status, graph.is_current, graph.status,
                       case_record.status, report.analysis_case_id,
                       report.repository_id, report.repository_revision_id,
                       report.repository_revision, confirmation.confirmation_id,
                       confirmation.approved_item_ids, repository.remote_url,
                       repository.workspace_root
                FROM impact_reports AS report
                JOIN analysis_cases AS case_record
                  ON case_record.analysis_case_id = report.analysis_case_id
                 AND case_record.project_id = report.project_id
                JOIN impact_confirmations AS confirmation
                  ON confirmation.impact_report_id = report.impact_report_id
                 AND confirmation.project_id = report.project_id
                JOIN repositories AS repository
                  ON repository.repository_id = report.repository_id
                 AND repository.project_id = report.project_id
                JOIN code_graph_snapshots AS graph
                  ON graph.code_graph_snapshot_id = report.code_graph_snapshot_id
                 AND graph.project_id = report.project_id
                WHERE report.impact_report_id = %s
                  AND report.project_id = %s
                  AND confirmation.confirmation_id = %s
                FOR UPDATE OF report, case_record
                FOR SHARE OF confirmation, repository, graph
                """,
                (source.impact_report_id, source.project_id, source.confirmation_id),
            )
            current = cursor.fetchone()
            expected_current = (
                "confirmed",
                True,
                "complete",
                "awaiting_confirmation",
                source.analysis_case_id,
                source.repository_id,
                source.repository_revision_id,
                source.commit_sha,
                source.confirmation_id,
                list(source.approved_item_ids),
                source.remote_url,
                source.workspace_root,
            )
            if current is None or tuple(current) != expected_current:
                raise ValueError("Edit Packet source became stale before publication")
            cursor.execute(
                """
                SELECT impact_item_id, target_path, target_symbols,
                       recommended_action, test_file_refs
                FROM impact_items
                WHERE impact_report_id = %s AND project_id = %s
                ORDER BY impact_item_id
                FOR SHARE
                """,
                (source.impact_report_id, source.project_id),
            )
            current_items = tuple(
                ConfirmedImpactItem(
                    impact_item_id=str(item[0]),
                    target_path=str(item[1]),
                    target_symbols=tuple(str(value) for value in cast(list[object], item[2])),
                    recommended_action=str(item[3]),
                    test_file_refs=tuple(str(value) for value in cast(list[object], item[4])),
                )
                for item in cursor.fetchall()
            )
            if current_items != source.items:
                raise ValueError("Edit Packet Impact Item source became stale before publication")
            cursor.execute(
                """
                SELECT edit_packet_id
                FROM edit_packets
                WHERE project_id = %s AND analysis_case_id = %s AND status = 'active'
                FOR UPDATE
                """,
                (source.project_id, source.analysis_case_id),
            )
            previous_packet_ids = tuple(str(row[0]) for row in cursor.fetchall())
            for previous_packet_id in previous_packet_ids:
                if self.get(previous_packet_id) is None:
                    raise PersistenceConflictError(
                        f"Active Edit Packet disappeared: {previous_packet_id}"
                    )
            self._artifacts.store(
                artifact_id=packet_id,
                project_id=source.project_id,
                analysis_case_id=source.analysis_case_id,
                artifact=artifact,
            )
            cursor.execute(
                """
                UPDATE edit_packets SET status = 'superseded'
                WHERE project_id = %s AND analysis_case_id = %s AND status = 'active'
                """,
                (source.project_id, source.analysis_case_id),
            )
            cursor.execute(
                """
                INSERT INTO edit_packets (
                    edit_packet_id, project_id, analysis_case_id, impact_report_id,
                    confirmation_id, repository_id, repository_revision_id,
                    base_repository_revision, status, editable_files, read_only_files,
                    test_files, forbidden_globs, allowed_items, required_ui_scenario_refs
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
                )
                """,
                (
                    packet_id,
                    source.project_id,
                    source.analysis_case_id,
                    source.impact_report_id,
                    source.confirmation_id,
                    source.repository_id,
                    source.repository_revision_id,
                    source.commit_sha,
                    _json(artifact["editable_files"]),
                    _json(artifact["read_only_files"]),
                    _json(artifact["test_files"]),
                    _json(artifact["forbidden_globs"]),
                    _json(artifact["allowed_items"]),
                    _json(artifact["required_ui_scenario_refs"]),
                ),
            )
            cursor.execute(
                """
                UPDATE analysis_cases SET status = 'editing', updated_at = now()
                WHERE analysis_case_id = %s AND project_id = %s
                """,
                (source.analysis_case_id, source.project_id),
            )
            published = self.get(packet_id)
            if published is None or published.status != "active":
                raise PersistenceConflictError(
                    f"Published Edit Packet failed integrity validation: {packet_id}"
                )
        return EditPacketPublishResult(True, packet_id, "active")

    def _validate_packet_integrity(
        self,
        *,
        artifact: dict[str, Any],
        row: tuple[object, ...],
    ) -> str:
        packet_id = str(row[0])
        if artifact.get("artifact_type") != "CopilotEditPacket":
            raise PersistenceConflictError(f"Edit Packet Artifact type differs: {packet_id}")
        self._contracts.validate_artifact(artifact)
        report_state = self._impacts.get_state(str(artifact["impact_report_id"]))
        if report_state is None:
            raise PersistenceConflictError(f"Edit Packet Impact Report is missing: {packet_id}")
        status = str(row[8])
        if status not in {"active", "superseded"} or report_state.status not in {
            "confirmed",
            "superseded",
        }:
            raise PersistenceConflictError(f"Edit Packet lifecycle provenance differs: {packet_id}")
        expected_identity = (
            str(artifact["edit_packet_id"]),
            str(artifact["project_id"]),
            report_state.analysis_case_id,
            str(artifact["impact_report_id"]),
            str(artifact["confirmation_id"]),
            str(artifact["repository_id"]),
            report_state.repository_revision_id,
            str(artifact["base_repository_revision"]),
            list(artifact["editable_files"]),
            list(artifact["read_only_files"]),
            list(artifact["test_files"]),
            list(artifact["forbidden_globs"]),
            list(artifact["allowed_items"]),
            list(artifact["required_ui_scenario_refs"]),
            str(artifact["base_repository_revision"]),
            str(artifact["impact_report_id"]),
        )
        actual_identity = (*tuple(row[:8]), *tuple(row[9:]))
        if actual_identity != expected_identity:
            raise PersistenceConflictError(f"Edit Packet normalized identity differs: {packet_id}")
        if report_state.project_id != str(
            artifact["project_id"]
        ) or report_state.repository_id != str(artifact["repository_id"]):
            raise PersistenceConflictError(f"Edit Packet Impact scope differs: {packet_id}")
        source = self._load_integrity_source(
            project_id=report_state.project_id,
            analysis_case_id=report_state.analysis_case_id,
            impact_report_id=report_state.impact_report_id,
            confirmation_id=str(artifact["confirmation_id"]),
        )
        try:
            _validate_packet_semantics(artifact=artifact, source=source)
        except ValueError as error:
            raise PersistenceConflictError(
                f"Edit Packet derived scope differs: {packet_id}"
            ) from error
        return report_state.status

    def _load_integrity_source(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        impact_report_id: str,
        confirmation_id: str,
    ) -> EditPacketSource:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT report.repository_id, report.repository_revision_id,
                       report.repository_revision, repository.remote_url,
                       repository.workspace_root, confirmation.approved_item_ids
                FROM impact_reports AS report
                JOIN impact_confirmations AS confirmation
                  ON confirmation.impact_report_id = report.impact_report_id
                 AND confirmation.project_id = report.project_id
                JOIN repositories AS repository
                  ON repository.repository_id = report.repository_id
                 AND repository.project_id = report.project_id
                WHERE report.project_id = %s
                  AND report.analysis_case_id = %s
                  AND report.impact_report_id = %s
                  AND confirmation.confirmation_id = %s
                """,
                (project_id, analysis_case_id, impact_report_id, confirmation_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise PersistenceConflictError(
                    f"Edit Packet confirmed Impact provenance is missing: {impact_report_id}"
                )
            cursor.execute(
                """
                SELECT impact_item_id, target_path, target_symbols,
                       recommended_action, test_file_refs
                FROM impact_items
                WHERE impact_report_id = %s AND project_id = %s
                ORDER BY impact_item_id
                """,
                (impact_report_id, project_id),
            )
            items = tuple(
                ConfirmedImpactItem(
                    impact_item_id=str(item[0]),
                    target_path=str(item[1]),
                    target_symbols=_strings(item[2]),
                    recommended_action=str(item[3]),
                    test_file_refs=_strings(item[4]),
                )
                for item in cursor.fetchall()
            )
        report_artifact = self._artifacts.get(impact_report_id)
        if report_artifact is None:
            raise PersistenceConflictError(
                f"Edit Packet Impact Artifact is missing: {impact_report_id}"
            )
        return EditPacketSource(
            project_id=project_id,
            analysis_case_id=analysis_case_id,
            impact_report_id=impact_report_id,
            confirmation_id=confirmation_id,
            repository_id=str(row[0]),
            repository_revision_id=str(row[1]),
            commit_sha=str(row[2]),
            remote_url=str(row[3]),
            workspace_root=str(row[4]),
            business_summary=str(report_artifact["summary"]),
            required_ui_scenario_refs=_strings(report_artifact["required_ui_scenario_refs"]),
            approved_item_ids=_strings(row[5]),
            items=items,
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in cast(list[object], value))


def _validate_record_source(*, packet: EditPacketRecord, source: EditPacketSource) -> None:
    artifact = packet.artifact
    expected = (
        source.project_id,
        source.analysis_case_id,
        source.impact_report_id,
        source.confirmation_id,
        source.repository_id,
        source.repository_revision_id,
        source.commit_sha,
    )
    actual = (
        packet.project_id,
        packet.analysis_case_id,
        artifact["impact_report_id"],
        artifact["confirmation_id"],
        artifact["repository_id"],
        packet.repository_revision_id,
        artifact["base_repository_revision"],
    )
    if actual != expected:
        raise PersistenceConflictError(
            f"Edit Packet replay source differs: {artifact['edit_packet_id']}"
        )


def _validate_packet_semantics(*, artifact: dict[str, Any], source: EditPacketSource) -> None:
    approved_ids = set(source.approved_item_ids)
    approved_items = tuple(item for item in source.items if item.impact_item_id in approved_ids)
    actionable = tuple(
        item for item in approved_items if item.recommended_action in {"modify", "add", "delete"}
    )
    test_files = sorted({path for item in actionable for path in item.test_file_refs})
    expected_files = (
        sorted({item.target_path for item in actionable if item.target_path not in test_files}),
        sorted(
            {
                item.target_path
                for item in approved_items
                if item.recommended_action == "review_only"
            }
        ),
        test_files,
    )
    actual_files = (
        artifact["editable_files"],
        artifact["read_only_files"],
        artifact["test_files"],
    )
    if actual_files != expected_files:
        raise ValueError("Edit Packet file scope is not derived from approved Impact Items")
    flattened_paths = [path for group in expected_files for path in group]
    if len(flattened_paths) != len(set(flattened_paths)):
        raise ValueError("Edit Packet derived file classifications overlap")

    allowed_items = cast(list[dict[str, Any]], artifact["allowed_items"])
    actual_item_scope = tuple(
        (
            item["impact_item_id"],
            item["target_path"],
            item["target_symbols"],
            item["allowed_actions"],
            item["business_summary"],
        )
        for item in allowed_items
    )
    expected_item_scope = tuple(
        (
            item.impact_item_id,
            item.target_path,
            list(item.target_symbols),
            [item.recommended_action],
            source.business_summary,
        )
        for item in actionable
    )
    if actual_item_scope != expected_item_scope:
        raise ValueError("Edit Packet allowed Items are not derived from approved Impact Items")
    if artifact["required_ui_scenario_refs"] != list(source.required_ui_scenario_refs):
        raise ValueError("Edit Packet UI Scenario scope differs from the confirmed Impact Report")
    if artifact.get("must_not_fetch_context_package") is not True:
        raise ValueError("Edit Packet must prohibit fetching the Context Package")
