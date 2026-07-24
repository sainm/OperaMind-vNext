"""Compute auditable RAG retrieval metrics against frozen Golden expectations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from operamind.domain import RagQueryPurpose


@dataclass(frozen=True, slots=True)
class RagQualityMetrics:
    """Macro query metrics and an absolute isolation defect count."""

    recall_at_5: float
    recall_at_10: float
    mrr: float
    irrelevant_rate: float
    cross_project_leaks: int


@dataclass(frozen=True, slots=True)
class RagQualityEvaluation:
    """Metric result plus explicit failed threshold names."""

    metrics: RagQualityMetrics
    passed: bool
    failures: tuple[str, ...]
    queries: tuple[RagQueryQualityEvaluation, ...]


@dataclass(frozen=True, slots=True)
class RagQueryQualityEvaluation:
    """Per-query hits and explicit defects retained in the formal report."""

    purpose: RagQueryPurpose
    required_hits_at_5: tuple[str, ...]
    required_hits_at_10: tuple[str, ...]
    missing_required_refs: tuple[str, ...]
    irrelevant_hits: tuple[str, ...]
    cross_project_leaks: tuple[str, ...]
    reciprocal_rank: float
    failure_reasons: tuple[str, ...]


class RagQualityEvaluator:
    """Evaluate three planned-query rankings without changing expectations."""

    def evaluate(
        self,
        *,
        expected: dict[str, Any],
        observed: dict[str, Any],
    ) -> RagQualityEvaluation:
        """Compute macro Recall@K/MRR, irrelevant rate, and Project leaks."""

        case_id = _required_string(expected, "case_id")
        project_id = _required_string(expected, "project_id")
        if _required_string(observed, "case_id") != case_id:
            raise ValueError("Observed RAG result case_id does not match expectation")
        if _required_string(observed, "project_id") != project_id:
            raise ValueError("Observed RAG result project_id does not match expectation")
        expectations = _by_purpose(expected.get("query_expectations"), candidates=False)
        results = _by_purpose(observed.get("query_results"), candidates=True)
        if set(expectations) != set(RagQueryPurpose) or set(results) != set(RagQueryPurpose):
            raise ValueError("RAG quality evaluation requires all three query purposes")

        recalls_at_5: list[float] = []
        recalls_at_10: list[float] = []
        reciprocal_ranks: list[float] = []
        irrelevant_hits = 0
        evaluated_hits = 0
        cross_project_leaks = 0
        query_evaluations: list[RagQueryQualityEvaluation] = []
        for purpose in RagQueryPurpose:
            expectation = expectations[purpose]
            result = results[purpose]
            required = _string_set(expectation, "required_candidate_refs", non_empty=True)
            irrelevant = _string_set(expectation, "irrelevant_candidate_refs", non_empty=False)
            candidates = _candidates(result)
            refs_at_5 = _ranked_refs(candidates[:5])
            refs_at_10 = _ranked_refs(candidates[:10])
            recalls_at_5.append(len(required.intersection(refs_at_5)) / len(required))
            recalls_at_10.append(len(required.intersection(refs_at_10)) / len(required))
            first_rank = next(
                (
                    rank
                    for rank, (_, _, candidate_refs) in enumerate(candidates, start=1)
                    if required.intersection(candidate_refs)
                ),
                None,
            )
            reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)
            top_ten = candidates[:10]
            required_at_5 = required.intersection(refs_at_5)
            required_at_10 = required.intersection(refs_at_10)
            missing_required = required.difference(refs_at_10)
            query_irrelevant = irrelevant.intersection(_ranked_refs(top_ten))
            query_leaks = {
                candidate_id
                for candidate_id, candidate_project_id, _ in top_ten
                if candidate_project_id != project_id
            }
            evaluated_hits += len(top_ten)
            irrelevant_hits += len(query_irrelevant)
            cross_project_leaks += len(query_leaks)
            query_failures = tuple(
                reason
                for reason, failed in (
                    ("required_candidate_missing_at_5", len(required_at_5) < len(required)),
                    ("required_candidate_missing_at_10", bool(missing_required)),
                    ("irrelevant_candidate_retrieved", bool(query_irrelevant)),
                    ("cross_project_candidate_retrieved", bool(query_leaks)),
                )
                if failed
            )
            query_evaluations.append(
                RagQueryQualityEvaluation(
                    purpose=purpose,
                    required_hits_at_5=tuple(sorted(required_at_5)),
                    required_hits_at_10=tuple(sorted(required_at_10)),
                    missing_required_refs=tuple(sorted(missing_required)),
                    irrelevant_hits=tuple(sorted(query_irrelevant)),
                    cross_project_leaks=tuple(sorted(query_leaks)),
                    reciprocal_rank=0.0 if first_rank is None else 1.0 / first_rank,
                    failure_reasons=query_failures,
                )
            )

        metrics = RagQualityMetrics(
            recall_at_5=sum(recalls_at_5) / len(recalls_at_5),
            recall_at_10=sum(recalls_at_10) / len(recalls_at_10),
            mrr=sum(reciprocal_ranks) / len(reciprocal_ranks),
            irrelevant_rate=(irrelevant_hits / evaluated_hits if evaluated_hits else 0.0),
            cross_project_leaks=cross_project_leaks,
        )
        thresholds = expected.get("quality_thresholds")
        if not isinstance(thresholds, dict):
            raise ValueError("Golden RAG expectation requires quality_thresholds")
        failures = tuple(
            name
            for name, passed in (
                ("recall_at_5", metrics.recall_at_5 >= _number(thresholds, "min_recall_at_5")),
                (
                    "recall_at_10",
                    metrics.recall_at_10 >= _number(thresholds, "min_recall_at_10"),
                ),
                ("mrr", metrics.mrr >= _number(thresholds, "min_mrr")),
                (
                    "irrelevant_rate",
                    metrics.irrelevant_rate <= _number(thresholds, "max_irrelevant_rate"),
                ),
                (
                    "cross_project_leaks",
                    metrics.cross_project_leaks <= _integer(thresholds, "max_cross_project_leaks"),
                ),
            )
            if not passed
        )
        return RagQualityEvaluation(
            metrics=metrics,
            passed=not failures,
            failures=failures,
            queries=tuple(query_evaluations),
        )


def _by_purpose(value: object, *, candidates: bool) -> dict[RagQueryPurpose, dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        label = "query_results" if candidates else "query_expectations"
        raise ValueError(f"Golden RAG {label} must be a list of objects")
    result: dict[RagQueryPurpose, dict[str, Any]] = {}
    for raw in value:
        item = cast(dict[str, Any], raw)
        purpose = RagQueryPurpose(_required_string(item, "query_purpose"))
        if purpose in result:
            raise ValueError(f"Duplicate RAG query purpose: {purpose.value}")
        result[purpose] = item
    return result


def _candidates(value: dict[str, Any]) -> list[tuple[str, str, frozenset[str]]]:
    raw = value.get("candidates")
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("Observed RAG candidates must be a list of objects")
    candidates: list[tuple[str, str, frozenset[str]]] = []
    for raw_item in raw:
        item = cast(dict[str, Any], raw_item)
        target_id = _required_string(item, "target_id")
        raw_semantic_refs = item.get("semantic_refs", [])
        if not isinstance(raw_semantic_refs, list) or not all(
            isinstance(ref, str) and ref.strip() for ref in raw_semantic_refs
        ):
            raise ValueError("Observed RAG semantic_refs must be non-blank strings")
        semantic_refs = set(raw_semantic_refs)
        if len(semantic_refs) != len(raw_semantic_refs):
            raise ValueError("Observed RAG semantic_refs must be unique")
        candidates.append(
            (
                target_id,
                _required_string(item, "project_id"),
                frozenset({target_id, *semantic_refs}),
            )
        )
    if len({candidate_id for candidate_id, _, _ in candidates}) != len(candidates):
        raise ValueError("Observed RAG candidate IDs must be unique per query")
    return candidates


def _ranked_refs(candidates: list[tuple[str, str, frozenset[str]]]) -> set[str]:
    return {ref for _, _, refs in candidates for ref in refs}


def _required_string(value: dict[str, Any], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return raw


def _string_set(value: dict[str, Any], key: str, *, non_empty: bool) -> set[str]:
    raw = value.get(key)
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise ValueError(f"{key} must be a list of non-blank strings")
    result = set(raw)
    if len(result) != len(raw) or (non_empty and not result):
        raise ValueError(f"{key} must contain unique values and satisfy minimum size")
    return result


def _number(value: dict[str, Any], key: str) -> float:
    raw = value.get(key)
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise ValueError(f"{key} must be numeric")
    converted = float(raw)
    if not 0 <= converted <= 1:
        raise ValueError(f"{key} must be between 0 and 1")
    return converted


def _integer(value: dict[str, Any], key: str) -> int:
    raw = value.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return raw
