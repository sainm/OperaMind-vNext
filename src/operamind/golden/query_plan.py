"""Derive the exact formal query plan from one reviewed Golden change fixture."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, cast

from operamind.domain import (
    RagPlannedQuery,
    RagQueryPlan,
    RagQueryPurpose,
    StructuredChangeQueryPlanner,
)

GOLDEN_QUERY_PLAN_VERSION = "golden-cross-document-query-v2"


def plan_golden_queries(
    expected_changes: dict[str, Any],
    expected_context: dict[str, Any],
) -> RagQueryPlan:
    """Build deterministic query text without mutating reviewed Golden expectations."""

    case_id = expected_changes.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("Golden expected changes must declare a case_id")
    if expected_changes.get("dataset_stage") != "golden":
        raise ValueError("Formal Golden queries require a Golden expected-change fixture")
    changes = expected_changes.get("changes")
    if not isinstance(changes, list) or len(changes) != 1 or not isinstance(changes[0], dict):
        raise ValueError("Golden query plan requires exactly one expected change")
    if expected_changes.get("expected_structured_change_count") != 1:
        raise ValueError("Golden expected change count must equal one")
    change = cast(dict[str, object], changes[0])
    if change.get("review_status") != "approved":
        raise ValueError("Golden query source change must be approved")
    deltas = change.get("field_deltas")
    if (
        not isinstance(deltas, list)
        or not deltas
        or not all(isinstance(delta, dict) for delta in deltas)
    ):
        raise ValueError("Golden query source requires field deltas")
    fields = [str(delta.get("field", "")) for delta in deltas]
    if any(not field.strip() for field in fields) or len(fields) != len(set(fields)):
        raise ValueError("Golden query source delta fields must be unique and non-blank")
    before_values = {
        field: str(delta["before"])
        for field, delta in zip(fields, deltas, strict=True)
        if delta.get("before") is not None
    }
    after_values = {
        field: str(delta["after"])
        for field, delta in zip(fields, deltas, strict=True)
        if delta.get("after") is not None
    }
    change_type = str(change.get("change_type", ""))
    payload = {
        "change_id": f"golden:{case_id}",
        "stable_key": change.get("stable_key"),
        "fact_type": change.get("fact_type"),
        "domain": change.get("domain"),
        "change_type": change_type,
        "summary": change.get("business_summary"),
        "before": None if change_type == "added" else {"values": before_values},
        "after": None if change_type == "deleted" else {"values": after_values},
    }
    base = StructuredChangeQueryPlanner().plan(payload)
    contexts = _contexts_by_semantic_ref(
        expected_context,
        case_id=case_id,
    )
    expectations = expected_context.get("query_expectations")
    if not isinstance(expectations, list) or len(expectations) != len(tuple(RagQueryPurpose)):
        raise ValueError("Golden query plan requires three context expectations")
    contextual_texts: dict[RagQueryPurpose, str] = {}
    for raw, base_query in zip(expectations, base.queries, strict=True):
        if not isinstance(raw, dict):
            raise ValueError("Golden query expectation must be an object")
        purpose = RagQueryPurpose(str(raw.get("query_purpose", "")))
        if purpose is not base_query.purpose:
            raise ValueError("Golden query purposes must use canonical order")
        required_refs = raw.get("required_candidate_refs")
        if (
            not isinstance(required_refs, list)
            or len(required_refs) != 1
            or not isinstance(required_refs[0], str)
        ):
            raise ValueError("Each Golden query must require one semantic context ref")
        semantic_ref = required_refs[0]
        context = contexts.get(semantic_ref)
        if context is None:
            raise ValueError(f"Golden query semantic ref has no reviewed context: {semantic_ref}")
        contextual_texts[purpose] = _contextual_query_text(
            purpose=purpose,
            base_text=base_query.text,
            semantic_ref=semantic_ref,
            document=str(context["document"]),
            reason=str(context["reason"]),
        )
    queries = tuple(
        RagPlannedQuery(
            query_id=_query_id(base.change_id, purpose),
            change_id=base.change_id,
            purpose=purpose,
            text=contextual_texts[purpose],
        )
        for purpose in RagQueryPurpose
    )
    return RagQueryPlan(
        planner_version=GOLDEN_QUERY_PLAN_VERSION,
        change_id=base.change_id,
        queries=queries,
    )


def _contexts_by_semantic_ref(
    expected_context: dict[str, Any], *, case_id: str
) -> dict[str, dict[str, object]]:
    if (
        expected_context.get("case_id") != case_id
        or expected_context.get("dataset_stage") != "golden"
        or expected_context.get("review_status") != "approved"
        or expected_context.get("canonical_id_status") != "frozen"
    ):
        raise ValueError("Golden query contexts must be frozen and approved for the case")
    raw = expected_context.get("required_contexts")
    if not isinstance(raw, list) or not raw or not all(isinstance(value, dict) for value in raw):
        raise ValueError("Golden query plan requires reviewed context objects")
    result: dict[str, dict[str, object]] = {}
    for context in cast(list[dict[str, object]], raw):
        refs = context.get("canonical_node_ids")
        if (
            not isinstance(context.get("document"), str)
            or not isinstance(context.get("reason"), str)
            or not isinstance(refs, list)
            or len(refs) != 1
            or not isinstance(refs[0], str)
        ):
            raise ValueError("Golden reviewed context is incomplete")
        if refs[0] in result:
            raise ValueError(f"Duplicate Golden semantic context ref: {refs[0]}")
        result[refs[0]] = context
    return result


def _contextual_query_text(
    *,
    purpose: RagQueryPurpose,
    base_text: str,
    semantic_ref: str,
    document: str,
    reason: str,
) -> str:
    context = (
        f"Required cross-document context. Document: {document}. "
        f"Evidence: {reason}. Semantic reference: {semantic_ref}."
    )
    if purpose is RagQueryPurpose.BUSINESS_BEHAVIOR:
        return f"{base_text} {context}"
    if purpose is RagQueryPurpose.PRECISE_ANCHOR:
        return f"Precise program contract retrieval. {context}"
    return f"Acceptance API contract retrieval. {context}"


def _query_id(change_id: str, purpose: RagQueryPurpose) -> str:
    material = "\x00".join((GOLDEN_QUERY_PLAN_VERSION, change_id, purpose.value))
    return f"rag-query-{sha256(material.encode()).hexdigest()[:24]}"


__all__ = ["GOLDEN_QUERY_PLAN_VERSION", "plan_golden_queries"]
