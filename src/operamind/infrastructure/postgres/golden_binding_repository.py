"""Resolve reviewed Golden semantic references to exact indexed Canonical nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries
from psycopg import Connection

GOLDEN_SEMANTIC_BINDING_VERSION = "golden-semantic-binding-v1"


@dataclass(frozen=True, slots=True)
class GoldenSemanticBinding:
    """Auditable resolution of one reviewed semantic reference in one fixed scope."""

    semantic_ref: str
    canonical_node_id: str
    document: str
    business_keys: tuple[str, ...]
    matched_locations: tuple[str, ...]
    unmatched_locations: tuple[str, ...]
    matched_source_refs: tuple[str, ...]
    resolution_method: str

    def to_artifact(self) -> dict[str, object]:
        return {
            "semantic_ref": self.semantic_ref,
            "canonical_node_id": self.canonical_node_id,
            "document": self.document,
            "business_keys": list(self.business_keys),
            "matched_locations": list(self.matched_locations),
            "unmatched_locations": list(self.unmatched_locations),
            "matched_source_refs": list(self.matched_source_refs),
            "resolution_method": self.resolution_method,
        }


@dataclass(frozen=True, slots=True)
class _IndexedNode:
    node_id: str
    document: str
    business_keys: tuple[str, ...]
    source_refs: tuple[str, ...]


class GoldenSemanticBindingRepository:
    """Bind approved Golden contexts without changing physical Canonical identities."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def resolve(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        search_index_build_id: str,
        expected: dict[str, Any],
    ) -> tuple[GoldenSemanticBinding, ...]:
        required = (project_id, snapshot_id, search_index_build_id)
        if any(not value.strip() for value in required):
            raise ValueError("Golden semantic binding scope must not be blank")
        contexts = _required_contexts(expected)
        required_refs = _required_query_refs(expected)
        context_refs = {
            semantic_ref
            for context in contexts
            for semantic_ref in cast(list[str], context["canonical_node_ids"])
        }
        if context_refs != required_refs:
            raise ValueError(
                "Golden required query refs must exactly match reviewed required contexts"
            )
        nodes = self._indexed_nodes(
            project_id=project_id,
            snapshot_id=snapshot_id,
            search_index_build_id=search_index_build_id,
        )
        bindings: list[GoldenSemanticBinding] = []
        for context in contexts:
            semantic_refs = cast(list[str], context["canonical_node_ids"])
            if len(semantic_refs) != 1:
                raise ValueError(
                    "Each reviewed Golden context must declare exactly one semantic reference"
                )
            bindings.append(
                _resolve_context(
                    semantic_ref=semantic_refs[0],
                    document=str(context["document"]),
                    locations=tuple(cast(list[str], context["locations"])),
                    nodes=nodes,
                )
            )
        if len({binding.canonical_node_id for binding in bindings}) != len(bindings):
            raise ValueError("Golden semantic refs must resolve to unique Canonical nodes")
        return tuple(sorted(bindings, key=lambda value: value.semantic_ref))

    def _indexed_nodes(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        search_index_build_id: str,
    ) -> tuple[_IndexedNode, ...]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT node.document_node_id,
                       document.logical_name,
                       node.business_keys,
                       node.source_refs
                FROM search_index_entries AS entry
                JOIN document_nodes AS node
                  ON node.project_id = entry.project_id
                 AND node.document_snapshot_id = entry.document_snapshot_id
                 AND node.document_node_id = entry.target_node_id
                JOIN document_versions AS version
                  ON version.project_id = node.project_id
                 AND version.document_version_id = node.document_version_id
                JOIN documents AS document
                  ON document.project_id = version.project_id
                 AND document.document_id = version.document_id
                WHERE entry.search_index_build_id = %s
                  AND entry.project_id = %s
                  AND entry.document_snapshot_id = %s
                  AND node.index_eligible
                ORDER BY node.document_node_id
                """,
                (search_index_build_id, project_id, snapshot_id),
            )
            rows = cursor.fetchall()
        return tuple(
            _IndexedNode(
                node_id=str(row[0]),
                document=str(row[1]),
                business_keys=tuple(str(value) for value in cast(list[object], row[2])),
                source_refs=tuple(str(value) for value in cast(list[object], row[3])),
            )
            for row in rows
        )


def _required_contexts(expected: dict[str, Any]) -> list[dict[str, object]]:
    raw = expected.get("required_contexts")
    if not isinstance(raw, list) or not raw or not all(isinstance(value, dict) for value in raw):
        raise ValueError("Golden semantic binding requires reviewed context objects")
    contexts = cast(list[dict[str, object]], raw)
    for context in contexts:
        refs = context.get("canonical_node_ids")
        locations = context.get("locations")
        if (
            not isinstance(context.get("document"), str)
            or not isinstance(refs, list)
            or not refs
            or not all(isinstance(value, str) and value.strip() for value in refs)
            or not isinstance(locations, list)
            or not locations
            or not all(isinstance(value, str) and value.strip() for value in locations)
        ):
            raise ValueError("Golden reviewed context binding fields are invalid")
    return contexts


def _required_query_refs(expected: dict[str, Any]) -> set[str]:
    raw = expected.get("query_expectations")
    if not isinstance(raw, list) or not all(isinstance(value, dict) for value in raw):
        raise ValueError("Golden query expectations are invalid")
    refs = {
        str(value)
        for expectation in cast(list[dict[str, object]], raw)
        for value in cast(list[object], expectation.get("required_candidate_refs", []))
    }
    if not refs or any(not value.strip() for value in refs):
        raise ValueError("Golden required query refs are invalid")
    return refs


def _resolve_context(
    *,
    semantic_ref: str,
    document: str,
    locations: tuple[str, ...],
    nodes: tuple[_IndexedNode, ...],
) -> GoldenSemanticBinding:
    direct = tuple(node for node in nodes if node.node_id == semantic_ref)
    if direct:
        if len(direct) != 1:
            raise ValueError(f"Golden direct node binding is ambiguous: {semantic_ref}")
        node = direct[0]
        return GoldenSemanticBinding(
            semantic_ref=semantic_ref,
            canonical_node_id=node.node_id,
            document=node.document,
            business_keys=node.business_keys,
            matched_locations=(),
            unmatched_locations=locations,
            matched_source_refs=(),
            resolution_method="direct_node_id",
        )

    matched: list[tuple[_IndexedNode, tuple[str, ...], tuple[str, ...]]] = []
    for node in nodes:
        if node.document != document:
            continue
        source_matches = tuple(
            source_ref
            for source_ref in node.source_refs
            if any(
                _source_ref_in_location(source_ref, document, location) for location in locations
            )
        )
        if not source_matches:
            continue
        location_matches = tuple(
            location
            for location in locations
            if any(
                _source_ref_in_location(source_ref, document, location)
                for source_ref in source_matches
            )
        )
        matched.append((node, location_matches, source_matches))
    if len(matched) != 1:
        raise ValueError(
            "Golden semantic ref must resolve to exactly one indexed Canonical node: "
            f"{semantic_ref} resolved={len(matched)}"
        )
    node, matched_locations, matched_source_refs = matched[0]
    return GoldenSemanticBinding(
        semantic_ref=semantic_ref,
        canonical_node_id=node.node_id,
        document=document,
        business_keys=node.business_keys,
        matched_locations=matched_locations,
        unmatched_locations=tuple(
            location for location in locations if location not in matched_locations
        ),
        matched_source_refs=matched_source_refs,
        resolution_method="reviewed_source_location",
    )


def _source_ref_in_location(source_ref: str, document: str, location: str) -> bool:
    if "#" not in source_ref or "!" not in location:
        return False
    source_document, source_location = source_ref.rsplit("#", 1)
    if source_document != document or "!" not in source_location:
        return False
    source_sheet, source_cell = source_location.rsplit("!", 1)
    expected_sheet, expected_range = location.rsplit("!", 1)
    if source_sheet != expected_sheet:
        return False
    try:
        row, column = coordinate_to_tuple(source_cell.replace("$", ""))
        boundaries = range_boundaries(expected_range.replace("$", ""))
    except ValueError:
        return False
    if any(value is None for value in boundaries):
        return False
    min_column, min_row, max_column, max_row = cast(tuple[int, int, int, int], boundaries)
    return min_row <= row <= max_row and min_column <= column <= max_column


__all__ = [
    "GOLDEN_SEMANTIC_BINDING_VERSION",
    "GoldenSemanticBinding",
    "GoldenSemanticBindingRepository",
]
