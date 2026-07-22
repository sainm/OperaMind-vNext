"""Immutable normalized persistence for Contract-validated Code Graph Snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.profile_repository import (
    validate_profile_payload_identity,
)
from operamind.infrastructure.postgres.unresolved_evidence_repository import (
    UnresolvedEvidenceRepository,
)

type CodeGraphFileLedgerRow = tuple[str, str, str, str, str, str]
type CodeGraphSymbolLedgerRow = tuple[str, str, str, str, str, str, int, int]
type CodeGraphEdgeLedgerRow = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    int,
    int,
    str,
    tuple[str, ...],
    str | None,
]
type CodeGraphTestBindingLedgerRow = tuple[str, str, str, str, str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class CodeGraphPublishResult:
    """Publication outcome and derived Test Binding count."""

    created: bool
    code_graph_snapshot_id: str
    status: str
    is_current: bool
    file_count: int
    symbol_count: int
    edge_count: int
    unresolved_edge_count: int
    test_binding_count: int
    scan_mode: str
    base_code_graph_snapshot_id: str | None
    scanned_file_count: int
    reused_file_count: int


@dataclass(frozen=True, slots=True)
class CodeGraphRepositoryScope:
    """Registered Repository Revision inputs required before local scanning."""

    project_id: str
    repository_id: str
    repository_revision_id: str
    commit_sha: str
    remote_url: str
    workspace_root: str | None


class CodeGraphSnapshotRepository:
    """Publish graph artifacts without persisting source code or full file content."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)

    def publish(
        self,
        *,
        artifact: dict[str, Any],
        repository_revision_id: str,
        profile_version_ids: dict[str, str],
        failure_reason: str | None = None,
    ) -> CodeGraphPublishResult:
        """Validate and publish one immutable graph; stale replay stays stale."""

        if not repository_revision_id.strip():
            raise ValueError("repository_revision_id must not be blank")
        self._contracts.validate_artifact(artifact)
        graph = _validate_graph_artifact(artifact)
        if set(profile_version_ids) != set(graph.profile_refs):
            raise ValueError("Profile version mapping must exactly cover framework_profile_refs")
        if any(not value.strip() for value in profile_version_ids.values()):
            raise ValueError("Profile version IDs must not be blank")
        if graph.status == "stale":
            raise ValueError("A new Code Graph Snapshot cannot be published as stale")
        if graph.status == "failed":
            if failure_reason is None or not failure_reason.strip():
                raise ValueError("A failed Code Graph Snapshot requires failure_reason")
        elif failure_reason is not None:
            raise ValueError("failure_reason is only valid for a failed Code Graph Snapshot")

        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._validate_scope(
                cursor,
                graph=graph,
                repository_revision_id=repository_revision_id,
                profile_version_ids=profile_version_ids,
            )
            if graph.base_snapshot_id is not None:
                base_graph = self._artifacts.get_for_share(graph.base_snapshot_id)
                if base_graph is None:
                    raise ValueError("Derived Code Graph base Snapshot does not exist")
                UnresolvedEvidenceRepository(
                    self._connection, self._contracts
                ).ensure_for_graph(base_graph)
            existing = self._load_result(cursor, graph.snapshot_id)
            if existing is not None:
                stored = self._artifacts.get(graph.snapshot_id)
                if stored != artifact:
                    raise PersistenceConflictError(
                        f"Code Graph Snapshot identity has different content: {graph.snapshot_id}"
                    )
                self._validate_existing_identity(
                    cursor,
                    graph=graph,
                    existing=existing,
                    repository_revision_id=repository_revision_id,
                    profile_version_ids=profile_version_ids,
                    failure_reason=failure_reason,
                )
                self._validate_normalized_integrity(cursor, graph=graph)
                if graph.status in {"complete", "truncated"}:
                    UnresolvedEvidenceRepository(
                        self._connection, self._contracts
                    ).ensure_for_graph(artifact)
                return existing

            self._artifacts.store(
                artifact_id=graph.snapshot_id,
                project_id=graph.project_id,
                analysis_case_id=None,
                artifact=artifact,
            )
            cursor.execute(
                """
                INSERT INTO code_graph_snapshots (
                    code_graph_snapshot_id,
                    project_id,
                    repository_id,
                    repository_revision_id,
                    status,
                    scan_roots,
                    file_count,
                    symbol_count,
                    edge_count,
                    unresolved_edge_count,
                    is_current,
                    failure_reason
                ) VALUES (
                    %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, false, %s
                )
                """,
                (
                    graph.snapshot_id,
                    graph.project_id,
                    graph.repository_id,
                    repository_revision_id,
                    graph.status,
                    _canonical_json(list(graph.scan_roots)),
                    len(graph.files),
                    graph.symbol_count,
                    len(graph.edges),
                    graph.unresolved_edge_count,
                    failure_reason,
                ),
            )
            self._store_scan_lineage(cursor, graph=graph, artifact=artifact)
            for profile_ref in graph.profile_refs:
                cursor.execute(
                    """
                    INSERT INTO code_graph_snapshot_profiles (
                        code_graph_snapshot_id,
                        project_id,
                        profile_version_id,
                        profile_ref
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (
                        graph.snapshot_id,
                        graph.project_id,
                        profile_version_ids[profile_ref],
                        profile_ref,
                    ),
                )
            for file in graph.files:
                self._store_file(cursor, graph=graph, file=file)
            for file in graph.files:
                for symbol in cast(list[dict[str, Any]], file["symbols"]):
                    self._store_symbol(cursor, graph=graph, file=file, symbol=symbol)
            for edge in graph.edges:
                self._store_edge(cursor, graph=graph, edge=edge)
            test_binding_count = self._store_test_bindings(cursor, graph=graph)

            if graph.status in {"complete", "truncated"}:
                cursor.execute(
                    """
                    UPDATE code_graph_snapshots
                    SET status = 'stale', is_current = false
                    WHERE project_id = %s
                      AND repository_id = %s
                      AND is_current
                    """,
                    (graph.project_id, graph.repository_id),
                )
                cursor.execute(
                    """
                    UPDATE code_graph_snapshots
                    SET is_current = true
                    WHERE code_graph_snapshot_id = %s
                    """,
                    (graph.snapshot_id,),
                )
            result = self._load_result(cursor, graph.snapshot_id)
            if result is None:
                raise RuntimeError("Code Graph Snapshot disappeared during publication")
            if result.test_binding_count != test_binding_count:
                raise RuntimeError("Code Graph Test Binding count drifted during publication")
            self._validate_normalized_integrity(cursor, graph=graph)
            if graph.status in {"complete", "truncated"}:
                UnresolvedEvidenceRepository(
                    self._connection, self._contracts
                ).ensure_for_graph(artifact)
            return replace(result, created=True)

    @staticmethod
    def _store_scan_lineage(
        cursor: Cursor[Any],
        *,
        graph: _ValidatedCodeGraph,
        artifact: dict[str, Any],
    ) -> None:
        scan_mode = str(artifact.get("scan_mode", "full"))
        base_id = artifact.get("base_code_graph_snapshot_id")
        changed_paths = tuple(
            str(value) for value in cast(list[object], artifact.get("changed_paths", []))
        )
        affected_paths = tuple(
            str(value)
            for value in cast(
                list[object], artifact.get("affected_paths", [file["path"] for file in graph.files])
            )
        )
        scanned_count = int(artifact.get("scanned_file_count", len(graph.files)))
        reused_count = int(artifact.get("reused_file_count", 0))
        runtime_evidence_refs = tuple(
            str(value) for value in cast(list[object], artifact.get("runtime_evidence_refs", []))
        )
        if scan_mode not in {"full", "incremental", "runtime_enriched"}:
            raise ValueError("Code Graph scan_mode is invalid")
        if len(changed_paths) != len(set(changed_paths)) or len(affected_paths) != len(
            set(affected_paths)
        ):
            raise ValueError("Code Graph incremental paths must be unique")
        if scanned_count + reused_count != len(graph.files):
            raise ValueError("Code Graph scanned and reused counts must cover all files")
        if scan_mode in {"incremental", "runtime_enriched"}:
            if not isinstance(base_id, str) or not base_id.strip() or base_id == graph.snapshot_id:
                raise ValueError("Derived Code Graph requires a different base Snapshot")
            cursor.execute(
                """
                SELECT repository_id FROM code_graph_snapshots
                WHERE code_graph_snapshot_id = %s AND project_id = %s
                FOR SHARE
                """,
                (base_id, graph.project_id),
            )
            base = cursor.fetchone()
            if base is None or str(base[0]) != graph.repository_id:
                raise ValueError("Derived Code Graph base Snapshot scope differs")
        elif base_id is not None or reused_count != 0:
            raise ValueError("Full Code Graph cannot reuse a base Snapshot")
        if scan_mode == "runtime_enriched":
            if not runtime_evidence_refs or scanned_count != 0 or changed_paths or affected_paths:
                raise ValueError("Runtime-enriched Code Graph lineage is invalid")
            for evidence_ref in runtime_evidence_refs:
                cursor.execute(
                    """
                    SELECT code_graph_snapshot_id, project_id
                    FROM runtime_route_evidence
                    WHERE runtime_route_evidence_id = %s
                    FOR SHARE
                    """,
                    (evidence_ref,),
                )
                evidence = cursor.fetchone()
                if evidence is None or tuple(evidence) != (base_id, graph.project_id):
                    raise ValueError("Runtime Route Evidence does not bind the base Code Graph")
        elif runtime_evidence_refs:
            raise ValueError("Only runtime-enriched Code Graphs accept Runtime Route Evidence")
        cursor.execute(
            """
            INSERT INTO code_graph_scan_lineage (
                code_graph_snapshot_id, project_id, scan_mode,
                base_code_graph_snapshot_id, changed_paths, affected_paths,
                scanned_file_count, reused_file_count, runtime_evidence_refs
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb)
            """,
            (
                graph.snapshot_id,
                graph.project_id,
                scan_mode,
                base_id,
                _canonical_json(list(changed_paths)),
                _canonical_json(list(affected_paths)),
                scanned_count,
                reused_count,
                _canonical_json(list(runtime_evidence_refs)),
            ),
        )

    def get_repository_scope(
        self,
        *,
        project_id: str,
        repository_id: str,
        repository_revision_id: str,
    ) -> CodeGraphRepositoryScope | None:
        """Load one Project-bound Repository Revision registration."""

        required = (project_id, repository_id, repository_revision_id)
        if any(not value.strip() for value in required):
            raise ValueError("Repository scope fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT repository.project_id,
                       repository.repository_id,
                       revision.repository_revision_id,
                       revision.commit_sha,
                       repository.remote_url,
                       repository.workspace_root
                FROM repositories AS repository
                JOIN repository_revisions AS revision
                  ON revision.repository_id = repository.repository_id
                WHERE repository.project_id = %s
                  AND repository.repository_id = %s
                  AND revision.repository_revision_id = %s
                """,
                (project_id, repository_id, repository_revision_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return CodeGraphRepositoryScope(
            project_id=str(row[0]),
            repository_id=str(row[1]),
            repository_revision_id=str(row[2]),
            commit_sha=str(row[3]),
            remote_url=str(row[4]),
            workspace_root=str(row[5]) if row[5] is not None else None,
        )

    def get(self, code_graph_snapshot_id: str) -> dict[str, Any] | None:
        """Return the immutable Contract Artifact for one persisted graph."""

        if not code_graph_snapshot_id.strip():
            raise ValueError("code_graph_snapshot_id must not be blank")
        with self._connection.cursor() as cursor:
            result = self._load_result(cursor, code_graph_snapshot_id)
        if result is None:
            return None
        artifact = self._artifacts.get(code_graph_snapshot_id)
        if artifact is None:
            raise PersistenceConflictError(
                f"Code Graph normalized row has no immutable Artifact: {code_graph_snapshot_id}"
            )
        graph = _validate_graph_artifact(artifact)
        with self._connection.cursor() as cursor:
            self._validate_normalized_integrity(cursor, graph=graph)
        return artifact

    def get_current(
        self,
        *,
        project_id: str,
        repository_id: str,
    ) -> CodeGraphPublishResult | None:
        """Load the latest complete or truncated graph for one repository."""

        if not project_id.strip() or not repository_id.strip():
            raise ValueError("Current Code Graph scope fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT code_graph_snapshot_id
                FROM code_graph_snapshots
                WHERE project_id = %s
                  AND repository_id = %s
                  AND is_current
                """,
                (project_id, repository_id),
            )
            row = cursor.fetchone()
            result = self._load_result(cursor, str(row[0])) if row is not None else None
        if result is not None:
            self.get(result.code_graph_snapshot_id)
        return result

    def get_publication_binding(
        self, code_graph_snapshot_id: str
    ) -> tuple[str, dict[str, str]] | None:
        """Return the immutable Repository Revision and Profile IDs of one graph."""

        if not code_graph_snapshot_id.strip():
            raise ValueError("code_graph_snapshot_id must not be blank")
        self.get(code_graph_snapshot_id)
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT repository_revision_id
                FROM code_graph_snapshots
                WHERE code_graph_snapshot_id = %s
                """,
                (code_graph_snapshot_id,),
            )
            revision = cursor.fetchone()
            if revision is None:
                return None
            cursor.execute(
                """
                SELECT profile_ref, profile_version_id
                FROM code_graph_snapshot_profiles
                WHERE code_graph_snapshot_id = %s
                ORDER BY profile_ref
                """,
                (code_graph_snapshot_id,),
            )
            profiles = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
        return str(revision[0]), profiles

    @staticmethod
    def _validate_scope(
        cursor: Cursor[Any],
        *,
        graph: _ValidatedCodeGraph,
        repository_revision_id: str,
        profile_version_ids: dict[str, str],
    ) -> None:
        cursor.execute(
            """
            SELECT repository.project_id, revision.commit_sha
            FROM repositories AS repository
            JOIN repository_revisions AS revision
              ON revision.repository_id = repository.repository_id
            WHERE repository.repository_id = %s
              AND revision.repository_revision_id = %s
            FOR SHARE OF repository, revision
            """,
            (graph.repository_id, repository_revision_id),
        )
        repository = cursor.fetchone()
        if repository is None:
            raise ValueError("Repository Revision does not exist")
        if tuple(repository) != (graph.project_id, graph.repository_revision):
            raise ValueError("Code Graph repository scope or commit SHA does not match")
        for profile_ref, profile_version_id in profile_version_ids.items():
            cursor.execute(
                """
                SELECT profile_type, profile_id, semantic_version
                FROM profile_versions
                WHERE profile_version_id = %s
                FOR SHARE
                """,
                (profile_version_id,),
            )
            profile = cursor.fetchone()
            if profile is None or str(profile[0]) != "CodeFrameworkProfile":
                raise ValueError("Code Graph requires persisted CodeFrameworkProfile versions")
            if profile_ref != f"{profile[1]}@{profile[2]}":
                raise ValueError("Code Graph Profile ref does not match persisted Profile")

    @staticmethod
    def _validate_existing_identity(
        cursor: Cursor[Any],
        *,
        graph: _ValidatedCodeGraph,
        existing: CodeGraphPublishResult,
        repository_revision_id: str,
        profile_version_ids: dict[str, str],
        failure_reason: str | None,
    ) -> None:
        """Reject replay when normalized publication identity differs from the Artifact."""

        cursor.execute(
            """
            SELECT repository_revision_id, failure_reason
            FROM code_graph_snapshots
            WHERE code_graph_snapshot_id = %s
            FOR SHARE
            """,
            (graph.snapshot_id,),
        )
        snapshot = cursor.fetchone()
        if snapshot is None or tuple(snapshot) != (repository_revision_id, failure_reason):
            raise PersistenceConflictError(
                f"Code Graph Snapshot publication identity differs: {graph.snapshot_id}"
            )
        cursor.execute(
            """
            SELECT profile_ref, profile_version_id
            FROM code_graph_snapshot_profiles
            WHERE code_graph_snapshot_id = %s
            ORDER BY profile_ref
            """,
            (graph.snapshot_id,),
        )
        stored_profiles = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
        if stored_profiles != profile_version_ids:
            raise PersistenceConflictError(
                f"Code Graph Snapshot Profile mapping differs: {graph.snapshot_id}"
            )
        expected_counts = (
            len(graph.files),
            graph.symbol_count,
            len(graph.edges),
            graph.unresolved_edge_count,
        )
        stored_counts = (
            existing.file_count,
            existing.symbol_count,
            existing.edge_count,
            existing.unresolved_edge_count,
        )
        if stored_counts != expected_counts:
            raise PersistenceConflictError(
                f"Code Graph Snapshot normalized counts differ: {graph.snapshot_id}"
            )

    @staticmethod
    def _store_file(
        cursor: Cursor[Any],
        *,
        graph: _ValidatedCodeGraph,
        file: dict[str, Any],
    ) -> None:
        cursor.execute(
            """
            INSERT INTO code_files (
                code_graph_snapshot_id,
                project_id,
                code_file_id,
                path,
                language,
                role,
                content_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                graph.snapshot_id,
                graph.project_id,
                file["file_id"],
                file["path"],
                file["language"],
                file["role"],
                file["content_hash"],
            ),
        )

    @staticmethod
    def _store_symbol(
        cursor: Cursor[Any],
        *,
        graph: _ValidatedCodeGraph,
        file: dict[str, Any],
        symbol: dict[str, Any],
    ) -> None:
        cursor.execute(
            """
            INSERT INTO code_symbols (
                code_graph_snapshot_id,
                project_id,
                code_symbol_id,
                code_file_id,
                symbol_type,
                name,
                signature,
                start_line,
                end_line
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                graph.snapshot_id,
                graph.project_id,
                symbol["symbol_id"],
                file["file_id"],
                symbol["symbol_type"],
                symbol["name"],
                symbol["signature"],
                symbol["start_line"],
                symbol["end_line"],
            ),
        )

    @staticmethod
    def _store_edge(
        cursor: Cursor[Any],
        *,
        graph: _ValidatedCodeGraph,
        edge: dict[str, Any],
    ) -> None:
        location = cast(dict[str, Any], edge["source_location"])
        cursor.execute(
            """
            INSERT INTO code_edges (
                code_graph_snapshot_id,
                project_id,
                code_edge_id,
                edge_type,
                from_ref,
                to_ref,
                resolution_status,
                confidence,
                extractor,
                profile_version_ref,
                source_path,
                source_start_line,
                source_end_line,
                provenance,
                evidence_refs,
                static_edge_ref
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s::jsonb, %s
            )
            """,
            (
                graph.snapshot_id,
                graph.project_id,
                edge["edge_id"],
                edge["edge_type"],
                edge["from_ref"],
                edge["to_ref"],
                edge["resolution_status"],
                edge["confidence"],
                edge["extractor"],
                edge["profile_version"],
                location["path"],
                location["start_line"],
                location["end_line"],
                edge.get("provenance", "static"),
                _canonical_json(cast(list[object], edge.get("evidence_refs", []))),
                edge.get("static_edge_ref"),
            ),
        )

    @staticmethod
    def _store_test_bindings(cursor: Cursor[Any], *, graph: _ValidatedCodeGraph) -> int:
        rows = _expected_test_binding_rows(graph)
        for row in rows:
            cursor.execute(
                """
                INSERT INTO code_test_bindings (
                    code_test_binding_id,
                    code_graph_snapshot_id,
                    project_id,
                    production_file_id,
                    test_file_id,
                    source_edge_id,
                    confidence,
                    extractor,
                    profile_version_ref
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                row,
            )
        return len(rows)

    @staticmethod
    def _validate_normalized_integrity(
        cursor: Cursor[Any],
        *,
        graph: _ValidatedCodeGraph,
    ) -> None:
        cursor.execute(
            """
            SELECT snapshot.project_id,
                   snapshot.repository_id,
                   revision.commit_sha,
                   snapshot.status,
                   snapshot.scan_roots,
                   snapshot.file_count,
                   snapshot.symbol_count,
                   snapshot.edge_count,
                   snapshot.unresolved_edge_count,
                   snapshot.is_current,
                   snapshot.failure_reason
            FROM code_graph_snapshots AS snapshot
            JOIN repository_revisions AS revision
              ON revision.repository_revision_id = snapshot.repository_revision_id
             AND revision.repository_id = snapshot.repository_id
            WHERE snapshot.code_graph_snapshot_id = %s
            """,
            (graph.snapshot_id,),
        )
        header = cursor.fetchone()
        if header is None:
            raise PersistenceConflictError(
                f"Code Graph Snapshot normalized header disappeared: {graph.snapshot_id}"
            )
        status = str(header[3])
        status_matches = status == graph.status or (
            status == "stale" and graph.status in {"complete", "truncated"}
        )
        expected_header = (
            graph.project_id,
            graph.repository_id,
            graph.repository_revision,
            list(graph.scan_roots),
            len(graph.files),
            graph.symbol_count,
            len(graph.edges),
            graph.unresolved_edge_count,
        )
        actual_header = (
            str(header[0]),
            str(header[1]),
            str(header[2]),
            list(cast(list[object], header[4])),
            int(cast(int, header[5])),
            int(cast(int, header[6])),
            int(cast(int, header[7])),
            int(cast(int, header[8])),
        )
        if not status_matches or actual_header != expected_header:
            raise PersistenceConflictError(
                f"Code Graph Snapshot normalized header differs: {graph.snapshot_id}"
            )
        is_current = bool(header[9])
        failure_reason = str(header[10]) if header[10] is not None else None
        if (
            (status in {"complete", "truncated"}) != is_current
            or (status == "stale" and is_current)
            or ((status == "failed") != (failure_reason is not None))
        ):
            raise PersistenceConflictError(
                f"Code Graph Snapshot lifecycle state differs: {graph.snapshot_id}"
            )

        actual_profiles = CodeGraphSnapshotRepository._load_profile_refs(
            cursor,
            graph=graph,
        )
        expected_profiles = tuple(sorted(graph.profile_refs))
        if actual_profiles != expected_profiles:
            raise PersistenceConflictError(
                f"Code Graph Snapshot Profile ledger differs: {graph.snapshot_id}"
            )

        expected_files, expected_symbols, expected_edges = _expected_graph_rows(graph)
        actual_files = CodeGraphSnapshotRepository._load_file_rows(cursor, graph.snapshot_id)
        actual_symbols = CodeGraphSnapshotRepository._load_symbol_rows(cursor, graph.snapshot_id)
        actual_edges = CodeGraphSnapshotRepository._load_edge_rows(cursor, graph.snapshot_id)
        actual_bindings = CodeGraphSnapshotRepository._load_test_binding_rows(
            cursor,
            graph.snapshot_id,
        )
        expected_bindings = _expected_test_binding_rows(graph)
        CodeGraphSnapshotRepository._validate_scan_lineage(cursor, graph=graph)
        if actual_files != expected_files:
            raise PersistenceConflictError(
                f"Code Graph Snapshot File ledger differs: {graph.snapshot_id}"
            )
        if actual_symbols != expected_symbols:
            raise PersistenceConflictError(
                f"Code Graph Snapshot Symbol ledger differs: {graph.snapshot_id}"
            )
        if actual_edges != expected_edges:
            raise PersistenceConflictError(
                f"Code Graph Snapshot Edge ledger differs: {graph.snapshot_id}"
            )
        if actual_bindings != expected_bindings:
            raise PersistenceConflictError(
                f"Code Graph Snapshot Test Binding ledger differs: {graph.snapshot_id}"
            )

    @staticmethod
    def _validate_scan_lineage(
        cursor: Cursor[Any],
        *,
        graph: _ValidatedCodeGraph,
    ) -> None:
        cursor.execute(
            """
            SELECT scan_mode, base_code_graph_snapshot_id, changed_paths,
                   affected_paths, scanned_file_count, reused_file_count,
                   runtime_evidence_refs
            FROM code_graph_scan_lineage
            WHERE code_graph_snapshot_id = %s AND project_id = %s
            """,
            (graph.snapshot_id, graph.project_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise PersistenceConflictError(
                f"Code Graph Snapshot scan lineage disappeared: {graph.snapshot_id}"
            )
        actual = (
            str(row[0]),
            str(row[1]) if row[1] is not None else None,
            tuple(str(value) for value in cast(list[object], row[2])),
            tuple(str(value) for value in cast(list[object], row[3])),
            int(row[4]),
            int(row[5]),
            tuple(str(value) for value in cast(list[object], row[6])),
        )
        expected = (
            graph.scan_mode,
            graph.base_snapshot_id,
            graph.changed_paths,
            graph.affected_paths,
            graph.scanned_file_count,
            graph.reused_file_count,
            graph.runtime_evidence_refs,
        )
        if actual != expected:
            raise PersistenceConflictError(
                f"Code Graph Snapshot scan lineage differs: {graph.snapshot_id}"
            )

    @staticmethod
    def _load_profile_refs(
        cursor: Cursor[Any],
        *,
        graph: _ValidatedCodeGraph,
    ) -> tuple[str, ...]:
        cursor.execute(
            """
            SELECT mapping.project_id,
                   mapping.profile_ref,
                   mapping.profile_version_id,
                   profile.profile_type,
                   profile.profile_id,
                   profile.semantic_version,
                   profile.payload,
                   profile.payload_digest
            FROM code_graph_snapshot_profiles AS mapping
            JOIN profile_versions AS profile
              ON profile.profile_version_id = mapping.profile_version_id
            WHERE mapping.code_graph_snapshot_id = %s
            ORDER BY mapping.profile_ref
            """,
            (graph.snapshot_id,),
        )
        refs: list[str] = []
        for row in cursor.fetchall():
            if str(row[0]) != graph.project_id:
                raise PersistenceConflictError(
                    f"Code Graph Snapshot Profile scope differs: {graph.snapshot_id}"
                )
            profile = validate_profile_payload_identity(
                profile_version_id=str(row[2]),
                row=tuple(row[3:8]),
                expected_profile_type="CodeFrameworkProfile",
            )
            expected_ref = f"{profile['profile_id']}@{profile['profile_version']}"
            if str(row[1]) != expected_ref:
                raise PersistenceConflictError(
                    f"Code Graph Snapshot Profile identity differs: {row[1]}"
                )
            refs.append(str(row[1]))
        return tuple(refs)

    @staticmethod
    def _load_file_rows(
        cursor: Cursor[Any],
        snapshot_id: str,
    ) -> tuple[CodeGraphFileLedgerRow, ...]:
        cursor.execute(
            """
            SELECT project_id, code_file_id, path, language, role, content_hash
            FROM code_files
            WHERE code_graph_snapshot_id = %s
            ORDER BY code_file_id
            """,
            (snapshot_id,),
        )
        return tuple(
            cast(CodeGraphFileLedgerRow, tuple(str(value) for value in row))
            for row in cursor.fetchall()
        )

    @staticmethod
    def _load_symbol_rows(
        cursor: Cursor[Any],
        snapshot_id: str,
    ) -> tuple[CodeGraphSymbolLedgerRow, ...]:
        cursor.execute(
            """
            SELECT project_id, code_symbol_id, code_file_id, symbol_type,
                   name, signature, start_line, end_line
            FROM code_symbols
            WHERE code_graph_snapshot_id = %s
            ORDER BY code_symbol_id
            """,
            (snapshot_id,),
        )
        return tuple(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                int(cast(int, row[6])),
                int(cast(int, row[7])),
            )
            for row in cursor.fetchall()
        )

    @staticmethod
    def _load_edge_rows(
        cursor: Cursor[Any],
        snapshot_id: str,
    ) -> tuple[CodeGraphEdgeLedgerRow, ...]:
        cursor.execute(
            """
            SELECT project_id, code_edge_id, edge_type, from_ref, to_ref,
                   resolution_status, confidence, extractor, profile_version_ref,
                   source_path, source_start_line, source_end_line,
                   provenance, evidence_refs, static_edge_ref
            FROM code_edges
            WHERE code_graph_snapshot_id = %s
            ORDER BY code_edge_id
            """,
            (snapshot_id,),
        )
        return tuple(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
                str(row[8]),
                str(row[9]),
                int(cast(int, row[10])),
                int(cast(int, row[11])),
                str(row[12]),
                tuple(str(value) for value in cast(list[object], row[13])),
                str(row[14]) if row[14] is not None else None,
            )
            for row in cursor.fetchall()
        )

    @staticmethod
    def _load_test_binding_rows(
        cursor: Cursor[Any],
        snapshot_id: str,
    ) -> tuple[CodeGraphTestBindingLedgerRow, ...]:
        cursor.execute(
            """
            SELECT code_test_binding_id, code_graph_snapshot_id, project_id,
                   production_file_id, test_file_id, source_edge_id,
                   confidence, extractor, profile_version_ref
            FROM code_test_bindings
            WHERE code_graph_snapshot_id = %s
            ORDER BY production_file_id, test_file_id
            """,
            (snapshot_id,),
        )
        return tuple(
            cast(CodeGraphTestBindingLedgerRow, tuple(str(value) for value in row))
            for row in cursor.fetchall()
        )

    @staticmethod
    def _load_result(
        cursor: Cursor[Any], code_graph_snapshot_id: str
    ) -> CodeGraphPublishResult | None:
        cursor.execute(
            """
            SELECT snapshot.code_graph_snapshot_id,
                   snapshot.status,
                   snapshot.is_current,
                   snapshot.file_count,
                   snapshot.symbol_count,
                   snapshot.edge_count,
                   snapshot.unresolved_edge_count,
                   count(binding.code_test_binding_id),
                   lineage.scan_mode,
                   lineage.base_code_graph_snapshot_id,
                   lineage.scanned_file_count,
                   lineage.reused_file_count
            FROM code_graph_snapshots AS snapshot
            LEFT JOIN code_test_bindings AS binding
              ON binding.code_graph_snapshot_id = snapshot.code_graph_snapshot_id
             AND binding.project_id = snapshot.project_id
            JOIN code_graph_scan_lineage AS lineage
              ON lineage.code_graph_snapshot_id = snapshot.code_graph_snapshot_id
             AND lineage.project_id = snapshot.project_id
            WHERE snapshot.code_graph_snapshot_id = %s
            GROUP BY snapshot.code_graph_snapshot_id,
                     lineage.code_graph_snapshot_id,
                     lineage.project_id
            """,
            (code_graph_snapshot_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return CodeGraphPublishResult(
            created=False,
            code_graph_snapshot_id=str(row[0]),
            status=str(row[1]),
            is_current=bool(row[2]),
            file_count=int(row[3]),
            symbol_count=int(row[4]),
            edge_count=int(row[5]),
            unresolved_edge_count=int(row[6]),
            test_binding_count=int(row[7]),
            scan_mode=str(row[8]),
            base_code_graph_snapshot_id=str(row[9]) if row[9] is not None else None,
            scanned_file_count=int(row[10]),
            reused_file_count=int(row[11]),
        )


@dataclass(frozen=True, slots=True)
class _ValidatedCodeGraph:
    snapshot_id: str
    project_id: str
    repository_id: str
    repository_revision: str
    profile_refs: tuple[str, ...]
    scan_roots: tuple[str, ...]
    status: str
    files: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    symbol_count: int
    unresolved_edge_count: int
    owner_by_ref: dict[str, str]
    role_by_file: dict[str, str]
    scan_mode: str
    base_snapshot_id: str | None
    changed_paths: tuple[str, ...]
    affected_paths: tuple[str, ...]
    scanned_file_count: int
    reused_file_count: int
    runtime_evidence_refs: tuple[str, ...]


def _validate_graph_artifact(artifact: dict[str, Any]) -> _ValidatedCodeGraph:
    if artifact.get("artifact_type") != "CodeGraphSnapshot":
        raise ValueError("Expected a CodeGraphSnapshot Artifact")
    files = tuple(cast(list[dict[str, Any]], artifact["files"]))
    edges = tuple(cast(list[dict[str, Any]], artifact["edges"]))
    scan_mode = str(artifact.get("scan_mode", "full"))
    base_snapshot_id = (
        str(artifact["base_code_graph_snapshot_id"])
        if artifact.get("base_code_graph_snapshot_id") is not None
        else None
    )
    changed_paths = tuple(
        str(value) for value in cast(list[object], artifact.get("changed_paths", []))
    )
    affected_paths = tuple(
        str(value)
        for value in cast(
            list[object], artifact.get("affected_paths", [file["path"] for file in files])
        )
    )
    scanned_file_count = int(artifact.get("scanned_file_count", len(files)))
    reused_file_count = int(artifact.get("reused_file_count", 0))
    runtime_evidence_refs = tuple(
        str(value) for value in cast(list[object], artifact.get("runtime_evidence_refs", []))
    )
    if scanned_file_count + reused_file_count != len(files):
        raise ValueError("Code Graph scan lineage counts must cover all files")
    file_ids = [str(file["file_id"]) for file in files]
    paths = [str(file["path"]) for file in files]
    edge_ids = [str(edge["edge_id"]) for edge in edges]
    if len(file_ids) != len(set(file_ids)) or len(paths) != len(set(paths)):
        raise ValueError("Code Graph file IDs and paths must be unique")
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("Code Graph edge IDs must be unique")

    owner_by_ref = {file_id: file_id for file_id in file_ids}
    role_by_file = {str(file["file_id"]): str(file["role"]) for file in files}
    symbol_count = 0
    for file in files:
        file_id = str(file["file_id"])
        for symbol in cast(list[dict[str, Any]], file["symbols"]):
            symbol_id = str(symbol["symbol_id"])
            if symbol_id in owner_by_ref:
                raise ValueError("Code Graph file and symbol IDs must be globally unique")
            if int(symbol["end_line"]) < int(symbol["start_line"]):
                raise ValueError("Code Graph symbol end_line must not precede start_line")
            owner_by_ref[symbol_id] = file_id
            symbol_count += 1

    profile_refs = tuple(
        str(value) for value in cast(list[object], artifact["framework_profile_refs"])
    )
    known_paths = set(paths)
    for edge in edges:
        from_ref = str(edge["from_ref"])
        to_ref = str(edge["to_ref"])
        location = cast(dict[str, Any], edge["source_location"])
        if from_ref not in owner_by_ref:
            raise ValueError("Every Code Graph edge source must resolve to a local file or symbol")
        if edge["resolution_status"] == "resolved" and to_ref not in owner_by_ref:
            raise ValueError("Resolved Code Graph edge target must exist in the Snapshot")
        if str(location["path"]) not in known_paths:
            raise ValueError("Code Graph edge source path must exist in files")
        if int(location["end_line"]) < int(location["start_line"]):
            raise ValueError("Code Graph edge source end_line must not precede start_line")
        if str(edge["profile_version"]) not in profile_refs:
            raise ValueError("Code Graph edge Profile ref must be bound to the Snapshot")

    return _ValidatedCodeGraph(
        snapshot_id=str(artifact["code_graph_snapshot_id"]),
        project_id=str(artifact["project_id"]),
        repository_id=str(artifact["repository_id"]),
        repository_revision=str(artifact["repository_revision"]),
        profile_refs=profile_refs,
        scan_roots=tuple(str(value) for value in cast(list[object], artifact["scan_roots"])),
        status=str(artifact["scan_status"]),
        files=files,
        edges=edges,
        symbol_count=symbol_count,
        unresolved_edge_count=sum(edge["resolution_status"] == "unresolved" for edge in edges),
        owner_by_ref=owner_by_ref,
        role_by_file=role_by_file,
        scan_mode=scan_mode,
        base_snapshot_id=base_snapshot_id,
        changed_paths=changed_paths,
        affected_paths=affected_paths,
        scanned_file_count=scanned_file_count,
        reused_file_count=reused_file_count,
        runtime_evidence_refs=runtime_evidence_refs,
    )


def _expected_graph_rows(
    graph: _ValidatedCodeGraph,
) -> tuple[
    tuple[CodeGraphFileLedgerRow, ...],
    tuple[CodeGraphSymbolLedgerRow, ...],
    tuple[CodeGraphEdgeLedgerRow, ...],
]:
    files: list[CodeGraphFileLedgerRow] = []
    symbols: list[CodeGraphSymbolLedgerRow] = []
    for file in graph.files:
        file_id = str(file["file_id"])
        files.append(
            (
                graph.project_id,
                file_id,
                str(file["path"]),
                str(file["language"]),
                str(file["role"]),
                str(file["content_hash"]),
            )
        )
        symbols.extend(
            (
                graph.project_id,
                str(symbol["symbol_id"]),
                file_id,
                str(symbol["symbol_type"]),
                str(symbol["name"]),
                str(symbol["signature"]),
                int(symbol["start_line"]),
                int(symbol["end_line"]),
            )
            for symbol in cast(list[dict[str, Any]], file["symbols"])
        )
    edges = tuple(
        sorted(
            (
                graph.project_id,
                str(edge["edge_id"]),
                str(edge["edge_type"]),
                str(edge["from_ref"]),
                str(edge["to_ref"]),
                str(edge["resolution_status"]),
                str(edge["confidence"]),
                str(edge["extractor"]),
                str(edge["profile_version"]),
                str(cast(dict[str, Any], edge["source_location"])["path"]),
                int(cast(dict[str, Any], edge["source_location"])["start_line"]),
                int(cast(dict[str, Any], edge["source_location"])["end_line"]),
                str(edge.get("provenance", "static")),
                tuple(str(value) for value in cast(list[object], edge.get("evidence_refs", []))),
                str(edge["static_edge_ref"]) if edge.get("static_edge_ref") is not None else None,
            )
            for edge in graph.edges
        )
    )
    return tuple(sorted(files)), tuple(sorted(symbols)), edges


def _expected_test_binding_rows(
    graph: _ValidatedCodeGraph,
) -> tuple[CodeGraphTestBindingLedgerRow, ...]:
    bindings: dict[tuple[str, str], dict[str, Any]] = {}
    confidence_priority = {"high": 0, "medium": 1, "low": 2}
    for edge in graph.edges:
        if edge["edge_type"] != "tests" or edge["resolution_status"] != "resolved":
            continue
        from_file = graph.owner_by_ref[str(edge["from_ref"])]
        to_file = graph.owner_by_ref[str(edge["to_ref"])]
        from_role = graph.role_by_file[from_file]
        to_role = graph.role_by_file[to_file]
        if from_role == "test" and to_role != "test":
            test_file_id, production_file_id = from_file, to_file
        elif to_role == "test" and from_role != "test":
            test_file_id, production_file_id = to_file, from_file
        else:
            continue
        pair = (production_file_id, test_file_id)
        current = bindings.get(pair)
        if current is None or (
            confidence_priority[str(edge["confidence"])],
            str(edge["edge_id"]),
        ) < (
            confidence_priority[str(current["confidence"])],
            str(current["edge_id"]),
        ):
            bindings[pair] = edge

    rows: list[CodeGraphTestBindingLedgerRow] = []
    for (production_file_id, test_file_id), edge in sorted(bindings.items()):
        material = "\x00".join(
            (graph.snapshot_id, production_file_id, test_file_id, str(edge["edge_id"]))
        )
        rows.append(
            (
                f"test-binding-{sha256(material.encode()).hexdigest()[:24]}",
                graph.snapshot_id,
                graph.project_id,
                production_file_id,
                test_file_id,
                str(edge["edge_id"]),
                str(edge["confidence"]),
                str(edge["extractor"]),
                str(edge["profile_version"]),
            )
        )
    return tuple(rows)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
