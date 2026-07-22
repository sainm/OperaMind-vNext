"""Bounded read model for exact-anchor Code Graph scope resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.domain import CodeAnchor, CodeAnchorKind, CodeAnchorMatch, CodeScopeEdge
from operamind.infrastructure.postgres.code_graph_repository import (
    CodeGraphSnapshotRepository,
)


@dataclass(frozen=True, slots=True)
class CodeGraphQueryScope:
    """Current Graph identity and immutable diagnostic/Profile provenance."""

    code_graph_snapshot_id: str
    project_id: str
    repository_id: str
    repository_revision_id: str
    commit_sha: str
    status: str
    is_current: bool
    unresolved_edge_count: int
    profile_versions: tuple[tuple[str, str], ...]
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodeAnchorMatchLoad:
    """Direct matches plus anchors that exceeded their explicit candidate ceiling."""

    matches: tuple[CodeAnchorMatch, ...]
    overflow_anchor_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodeEdgeLoad:
    """Resolved allowed edges plus a hard-limit overflow signal."""

    edges: tuple[CodeScopeEdge, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class CodeUnresolvedEdgeLoad:
    """Relevant unresolved Edge IDs plus a hard-limit overflow signal."""

    edge_ids: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class CodeNodeLocation:
    """File ownership for one file or Symbol graph ref."""

    node_ref: str
    file_id: str
    path: str
    role: str
    symbol_id: str | None
    symbol_signature: str | None


@dataclass(frozen=True, slots=True)
class CodeTestFileBinding:
    """Production-to-test file mapping with source Edge provenance."""

    production_file_id: str
    test_file_id: str
    test_path: str
    source_edge_id: str


class CodeGraphQueryRepository:
    """Query one Graph Snapshot without loading source content or crossing Project scope."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._snapshots = CodeGraphSnapshotRepository(connection, contracts)

    def get_scope(
        self,
        *,
        project_id: str,
        code_graph_snapshot_id: str,
    ) -> CodeGraphQueryScope | None:
        """Load graph/revision state and its immutable diagnostics."""

        if not project_id.strip() or not code_graph_snapshot_id.strip():
            raise ValueError("Code Graph query scope fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT graph.code_graph_snapshot_id,
                       graph.project_id,
                       graph.repository_id,
                       graph.repository_revision_id,
                       revision.commit_sha,
                       graph.status,
                       graph.is_current,
                       graph.unresolved_edge_count
                FROM code_graph_snapshots AS graph
                JOIN repository_revisions AS revision
                  ON revision.repository_revision_id = graph.repository_revision_id
                 AND revision.repository_id = graph.repository_id
                WHERE graph.project_id = %s
                  AND graph.code_graph_snapshot_id = %s
                """,
                (project_id, code_graph_snapshot_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                """
                SELECT profile_ref, profile_version_id
                FROM code_graph_snapshot_profiles
                WHERE project_id = %s
                  AND code_graph_snapshot_id = %s
                ORDER BY profile_ref
                """,
                (project_id, code_graph_snapshot_id),
            )
            profile_versions = tuple(
                (str(profile_ref), str(profile_version_id))
                for profile_ref, profile_version_id in cursor.fetchall()
            )
        artifact = self._snapshots.get(code_graph_snapshot_id)
        if artifact is None:
            raise RuntimeError("Code Graph normalized row has no immutable Artifact")
        diagnostics = tuple(str(value) for value in cast(list[object], artifact["diagnostics"]))
        return CodeGraphQueryScope(
            code_graph_snapshot_id=str(row[0]),
            project_id=str(row[1]),
            repository_id=str(row[2]),
            repository_revision_id=str(row[3]),
            commit_sha=str(row[4]),
            status=str(row[5]),
            is_current=bool(row[6]),
            unresolved_edge_count=int(row[7]),
            profile_versions=profile_versions,
            diagnostics=diagnostics,
        )

    def match_anchors(
        self,
        *,
        scope: CodeGraphQueryScope,
        anchors: tuple[CodeAnchor, ...],
        max_matches_per_anchor: int,
    ) -> CodeAnchorMatchLoad:
        """Run namespace-specific exact queries and preserve every bounded direct match."""

        if not 1 <= max_matches_per_anchor <= 10_000:
            raise ValueError("max_matches_per_anchor must be between 1 and 10000")
        matches: list[CodeAnchorMatch] = []
        overflow: list[str] = []
        with self._connection.cursor() as cursor:
            for anchor in anchors:
                rows = self._match_anchor_rows(
                    cursor,
                    scope=scope,
                    anchor=anchor,
                    limit=max_matches_per_anchor + 1,
                )
                if len(rows) > max_matches_per_anchor:
                    overflow.append(anchor.anchor_id)
                    rows = rows[:max_matches_per_anchor]
                grouped: dict[str, set[str]] = {}
                for node_ref, via_edge_id in rows:
                    grouped.setdefault(str(node_ref), set())
                    if via_edge_id is not None:
                        grouped[str(node_ref)].add(str(via_edge_id))
                matches.extend(
                    CodeAnchorMatch(
                        anchor_id=anchor.anchor_id,
                        node_ref=node_ref,
                        via_edge_ids=tuple(sorted(edge_ids)),
                    )
                    for node_ref, edge_ids in sorted(grouped.items())
                )
        return CodeAnchorMatchLoad(
            matches=tuple(matches),
            overflow_anchor_ids=tuple(sorted(overflow)),
        )

    def load_resolved_edges(
        self,
        *,
        scope: CodeGraphQueryScope,
        edge_types: tuple[str, ...],
        max_edges: int,
    ) -> CodeEdgeLoad:
        """Load only Profile-allowed resolved edges under a hard row ceiling."""

        if not edge_types or len(edge_types) != len(set(edge_types)):
            raise ValueError("edge_types must be non-empty and unique")
        if not 1 <= max_edges <= 2_000_000:
            raise ValueError("max_edges must be between 1 and 2000000")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT code_edge_id, edge_type, from_ref, to_ref,
                       provenance, evidence_refs
                FROM code_edges
                WHERE project_id = %s
                  AND code_graph_snapshot_id = %s
                  AND edge_type = ANY(%s)
                  AND resolution_status = 'resolved'
                ORDER BY code_edge_id
                LIMIT %s
                """,
                (
                    scope.project_id,
                    scope.code_graph_snapshot_id,
                    list(edge_types),
                    max_edges + 1,
                ),
            )
            rows = cursor.fetchall()
        truncated = len(rows) > max_edges
        return CodeEdgeLoad(
            edges=tuple(
                CodeScopeEdge(
                    edge_id=str(row[0]),
                    edge_type=str(row[1]),
                    from_ref=str(row[2]),
                    to_ref=str(row[3]),
                    provenance=str(row[4]),
                    evidence_refs=tuple(str(value) for value in cast(list[object], row[5])),
                )
                for row in rows[:max_edges]
            ),
            truncated=truncated,
        )

    def hydrate_refs(
        self,
        *,
        scope: CodeGraphQueryScope,
        node_refs: tuple[str, ...],
    ) -> tuple[CodeNodeLocation, ...]:
        """Map bounded file/Symbol refs to repository-relative file ownership."""

        if len(node_refs) != len(set(node_refs)):
            raise ValueError("node_refs must be unique")
        if not node_refs:
            return ()
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT file.code_file_id AS node_ref,
                       file.code_file_id,
                       file.path,
                       file.role,
                       NULL::text AS symbol_id,
                       NULL::text AS signature
                FROM code_files AS file
                WHERE file.project_id = %s
                  AND file.code_graph_snapshot_id = %s
                  AND file.code_file_id = ANY(%s)
                UNION ALL
                SELECT symbol.code_symbol_id AS node_ref,
                       file.code_file_id,
                       file.path,
                       file.role,
                       symbol.code_symbol_id,
                       symbol.signature
                FROM code_symbols AS symbol
                JOIN code_files AS file
                  ON file.code_graph_snapshot_id = symbol.code_graph_snapshot_id
                 AND file.project_id = symbol.project_id
                 AND file.code_file_id = symbol.code_file_id
                WHERE symbol.project_id = %s
                  AND symbol.code_graph_snapshot_id = %s
                  AND symbol.code_symbol_id = ANY(%s)
                ORDER BY node_ref
                """,
                (
                    scope.project_id,
                    scope.code_graph_snapshot_id,
                    list(node_refs),
                    scope.project_id,
                    scope.code_graph_snapshot_id,
                    list(node_refs),
                ),
            )
            rows = cursor.fetchall()
        return tuple(
            CodeNodeLocation(
                node_ref=str(row[0]),
                file_id=str(row[1]),
                path=str(row[2]),
                role=str(row[3]),
                symbol_id=str(row[4]) if row[4] is not None else None,
                symbol_signature=str(row[5]) if row[5] is not None else None,
            )
            for row in rows
        )

    def load_test_bindings(
        self,
        *,
        scope: CodeGraphQueryScope,
        production_file_ids: tuple[str, ...],
    ) -> tuple[CodeTestFileBinding, ...]:
        """Load explicit Test Binding rows only for candidate production files."""

        if len(production_file_ids) != len(set(production_file_ids)):
            raise ValueError("production_file_ids must be unique")
        if not production_file_ids:
            return ()
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT binding.production_file_id,
                       binding.test_file_id,
                       test_file.path,
                       binding.source_edge_id
                FROM code_test_bindings AS binding
                JOIN code_files AS test_file
                  ON test_file.code_graph_snapshot_id = binding.code_graph_snapshot_id
                 AND test_file.project_id = binding.project_id
                 AND test_file.code_file_id = binding.test_file_id
                WHERE binding.project_id = %s
                  AND binding.code_graph_snapshot_id = %s
                  AND binding.production_file_id = ANY(%s)
                ORDER BY binding.production_file_id, test_file.path
                """,
                (
                    scope.project_id,
                    scope.code_graph_snapshot_id,
                    list(production_file_ids),
                ),
            )
            rows = cursor.fetchall()
        return tuple(
            CodeTestFileBinding(
                production_file_id=str(row[0]),
                test_file_id=str(row[1]),
                test_path=str(row[2]),
                source_edge_id=str(row[3]),
            )
            for row in rows
        )

    def load_incident_unresolved_edge_ids(
        self,
        *,
        scope: CodeGraphQueryScope,
        node_refs: tuple[str, ...],
        max_edges: int,
    ) -> CodeUnresolvedEdgeLoad:
        """Return unresolved Edge IDs incident to the traversed node set."""

        if not node_refs:
            return CodeUnresolvedEdgeLoad(edge_ids=(), truncated=False)
        if not 1 <= max_edges <= 100_000:
            raise ValueError("max_edges must be between 1 and 100000")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT code_edge_id
                FROM code_edges
                WHERE project_id = %s
                  AND code_graph_snapshot_id = %s
                  AND resolution_status = 'unresolved'
                  AND (from_ref = ANY(%s) OR to_ref = ANY(%s))
                ORDER BY code_edge_id
                LIMIT %s
                """,
                (
                    scope.project_id,
                    scope.code_graph_snapshot_id,
                    list(node_refs),
                    list(node_refs),
                    max_edges + 1,
                ),
            )
            rows = cursor.fetchall()
        return CodeUnresolvedEdgeLoad(
            edge_ids=tuple(str(row[0]) for row in rows[:max_edges]),
            truncated=len(rows) > max_edges,
        )

    @staticmethod
    def _match_anchor_rows(
        cursor: Any,
        *,
        scope: CodeGraphQueryScope,
        anchor: CodeAnchor,
        limit: int,
    ) -> list[tuple[object, object | None]]:
        value = anchor.normalized_value
        parameters: tuple[object, ...]
        if anchor.kind is CodeAnchorKind.PATH:
            query = """
                SELECT code_file_id, NULL::text
                FROM code_files
                WHERE project_id = %s AND code_graph_snapshot_id = %s AND path = %s
                ORDER BY code_file_id LIMIT %s
            """
            parameters = (scope.project_id, scope.code_graph_snapshot_id, value, limit)
        elif anchor.kind is CodeAnchorKind.SYMBOL:
            query = """
                SELECT code_symbol_id, NULL::text
                FROM code_symbols
                WHERE project_id = %s
                  AND code_graph_snapshot_id = %s
                  AND (
                      lower(name) = %s
                      OR lower(regexp_replace(signature, '\\s+', '', 'g')) = %s
                  )
                ORDER BY code_symbol_id LIMIT %s
            """
            parameters = (
                scope.project_id,
                scope.code_graph_snapshot_id,
                value,
                value,
                limit,
            )
        elif anchor.kind in {CodeAnchorKind.TABLE, CodeAnchorKind.CONFIG_KEY}:
            symbol_type = "db_table" if anchor.kind is CodeAnchorKind.TABLE else "config_key"
            signature = f"table:{value}" if symbol_type == "db_table" else f"config:{value}"
            query = """
                SELECT code_symbol_id, NULL::text
                FROM code_symbols
                WHERE project_id = %s
                  AND code_graph_snapshot_id = %s
                  AND symbol_type = %s
                  AND (lower(name) = %s OR lower(signature) = %s)
                ORDER BY code_symbol_id LIMIT %s
            """
            parameters = (
                scope.project_id,
                scope.code_graph_snapshot_id,
                symbol_type,
                value.casefold(),
                signature.casefold(),
                limit,
            )
        else:
            edge_type = "exposes" if anchor.kind is CodeAnchorKind.ENDPOINT else "navigates_to"
            if anchor.kind is CodeAnchorKind.ENDPOINT and value.startswith("http:*:"):
                edge_target_clause = "to_ref LIKE %s ESCAPE '!'"
                path = value.removeprefix("http:*:")
                target_value = f"http:%:{_escape_like(path)}"
            else:
                edge_target_clause = "to_ref = %s"
                target_value = value
            query = f"""
                SELECT from_ref, code_edge_id
                FROM code_edges
                WHERE project_id = %s
                  AND code_graph_snapshot_id = %s
                  AND edge_type = %s
                  AND {edge_target_clause}
                ORDER BY from_ref, code_edge_id LIMIT %s
            """
            parameters = (
                scope.project_id,
                scope.code_graph_snapshot_id,
                edge_type,
                target_value,
                limit,
            )
        cursor.execute(query, parameters)
        return cast(list[tuple[object, object | None]], cursor.fetchall())


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
