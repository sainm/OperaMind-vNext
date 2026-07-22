"""Deterministic three-purpose RAG queries for one StructuredChange."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any

from operamind.domain.structured_changes import ChangeType


class RagQueryPurpose(StrEnum):
    """Required retrieval perspectives for formal impact analysis."""

    BUSINESS_BEHAVIOR = "business_behavior"
    PRECISE_ANCHOR = "precise_anchor"
    ACCEPTANCE_CRITERIA = "acceptance_criteria"


@dataclass(frozen=True, slots=True)
class RagPlannedQuery:
    """One immutable query with a traceable StructuredChange origin."""

    query_id: str
    change_id: str
    purpose: RagQueryPurpose
    text: str

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.change_id.strip() or not self.text.strip():
            raise ValueError("RAG planned query fields must not be blank")


@dataclass(frozen=True, slots=True)
class RagQueryPlan:
    """Complete, versioned query set for one StructuredChange."""

    planner_version: str
    change_id: str
    queries: tuple[RagPlannedQuery, ...]

    def __post_init__(self) -> None:
        if not self.planner_version.strip() or not self.change_id.strip():
            raise ValueError("RAG Query Plan identity must not be blank")
        purposes = tuple(query.purpose for query in self.queries)
        if purposes != tuple(RagQueryPurpose):
            raise ValueError("RAG Query Plan must contain the three purposes in order")
        if any(query.change_id != self.change_id for query in self.queries):
            raise ValueError("RAG Query Plan queries must belong to one change")


class StructuredChangeQueryPlanner:
    """Build content-only queries without invoking an AI or copying source refs."""

    VERSION = "structured-change-query-v1"

    def plan(self, change: dict[str, Any]) -> RagQueryPlan:
        """Create business, anchor, and acceptance queries deterministically."""

        required = ("change_id", "stable_key", "fact_type", "domain", "summary")
        values = tuple(str(change.get(field, "")) for field in required)
        if any(not value.strip() for value in values):
            raise ValueError("StructuredChange query fields must not be blank")
        change_id, stable_key, fact_type, domain, summary = values
        change_type = ChangeType(str(change.get("change_type", "")))
        before = _state_values(change.get("before"))
        after = _state_values(change.get("after"))
        changed_fields = tuple(
            field
            for field in sorted(before.keys() | after.keys())
            if before.get(field) != after.get(field)
        )
        if not changed_fields:
            raise ValueError("StructuredChange Query Plan requires changed fields")

        texts = {
            RagQueryPurpose.BUSINESS_BEHAVIOR: _business_query(
                domain=domain,
                fact_type=fact_type,
                change_type=change_type,
                summary=summary,
                before=before,
                after=after,
                changed_fields=changed_fields,
            ),
            RagQueryPurpose.PRECISE_ANCHOR: _anchor_query(
                stable_key=stable_key,
                fact_type=fact_type,
                before=before,
                after=after,
                changed_fields=changed_fields,
            ),
            RagQueryPurpose.ACCEPTANCE_CRITERIA: _acceptance_query(
                stable_key=stable_key,
                change_type=change_type,
                before=before,
                after=after,
                changed_fields=changed_fields,
            ),
        }
        queries = tuple(
            RagPlannedQuery(
                query_id=_query_id(change_id, purpose),
                change_id=change_id,
                purpose=purpose,
                text=texts[purpose],
            )
            for purpose in RagQueryPurpose
        )
        return RagQueryPlan(
            planner_version=self.VERSION,
            change_id=change_id,
            queries=queries,
        )


def _state_values(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not isinstance(value.get("values"), dict):
        raise ValueError("StructuredChange fact state must contain values")
    raw_values = value["values"]
    return {str(field): str(field_value) for field, field_value in raw_values.items()}


def _business_query(
    *,
    domain: str,
    fact_type: str,
    change_type: ChangeType,
    summary: str,
    before: dict[str, str],
    after: dict[str, str],
    changed_fields: tuple[str, ...],
) -> str:
    transitions = "; ".join(
        f"{field}: {_display(before.get(field))} -> {_display(after.get(field))}"
        for field in changed_fields
    )
    return (
        f"Business behavior change. Domain: {domain}. Fact type: {fact_type}. "
        f"Operation: {change_type.value}. Summary: {summary}. Transitions: {transitions}."
    )


def _anchor_query(
    *,
    stable_key: str,
    fact_type: str,
    before: dict[str, str],
    after: dict[str, str],
    changed_fields: tuple[str, ...],
) -> str:
    anchors = "; ".join(
        f"{field}={_display(after.get(field, before.get(field)))}" for field in changed_fields
    )
    return (
        f"Exact canonical anchors. Stable key: {stable_key}. Fact type: {fact_type}. "
        f"Changed fields: {', '.join(changed_fields)}. Target anchors: {anchors}."
    )


def _acceptance_query(
    *,
    stable_key: str,
    change_type: ChangeType,
    before: dict[str, str],
    after: dict[str, str],
    changed_fields: tuple[str, ...],
) -> str:
    if change_type is ChangeType.DELETED:
        criterion = f"{stable_key} must be absent"
    else:
        expected = "; ".join(
            f"{field} must equal {_display(after.get(field))}" for field in changed_fields
        )
        criterion = f"{stable_key}: {expected}"
    previous = "; ".join(
        f"{field} was {_display(before.get(field))}" for field in changed_fields if field in before
    )
    suffix = f" Previous state: {previous}." if previous else ""
    return f"Acceptance criteria. {criterion}.{suffix}"


def _display(value: str | None) -> str:
    return "<absent>" if value is None else value


def _query_id(change_id: str, purpose: RagQueryPurpose) -> str:
    material = "\x00".join((StructuredChangeQueryPlanner.VERSION, change_id, purpose.value))
    return f"rag-query-{sha256(material.encode()).hexdigest()[:24]}"
