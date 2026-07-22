"""Deterministic execution-scope and result comparison for revised Test Cases."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

_DIMENSIONS = ("test_data", "ui_scenarios", "execution_scope")


@dataclass(frozen=True, slots=True)
class TestCaseExecutionScopeComparison:
    source_scope_digest: str
    target_scope_digest: str
    changed_dimensions: tuple[str, ...]
    dimensions: tuple[dict[str, Any], ...]

    @property
    def changed(self) -> bool:
        return bool(self.changed_dimensions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "changed" if self.changed else "unchanged",
            "requires_confirmation": self.changed,
            "source_scope_digest": self.source_scope_digest,
            "target_scope_digest": self.target_scope_digest,
            "changed_dimensions": list(self.changed_dimensions),
            "dimensions": [dict(value) for value in self.dimensions],
        }


def compare_test_case_execution_scope(
    source_bundle: dict[str, Any], target_bundle: dict[str, Any]
) -> TestCaseExecutionScopeComparison:
    """Compare only values that can change execution or its business verdict."""

    source = _scope(source_bundle)
    target = _scope(target_bundle)
    dimensions: list[dict[str, Any]] = []
    changed: list[str] = []
    source_digests: dict[str, str] = {}
    target_digests: dict[str, str] = {}
    for name in _DIMENSIONS:
        source_digest = _digest(source[name])
        target_digest = _digest(target[name])
        source_digests[name] = source_digest
        target_digests[name] = target_digest
        is_changed = source_digest != target_digest
        if is_changed:
            changed.append(name)
        dimensions.append(
            {
                "dimension": name,
                "status": "changed" if is_changed else "unchanged",
                "source_digest": source_digest,
                "target_digest": target_digest,
            }
        )
    return TestCaseExecutionScopeComparison(
        source_scope_digest=_digest(source_digests),
        target_scope_digest=_digest(target_digests),
        changed_dimensions=tuple(changed),
        dimensions=tuple(dimensions),
    )


def compare_test_case_version_results(
    *,
    source_orchestration_id: str,
    target_orchestration_id: str,
    source_run: dict[str, Any] | None,
    target_run: dict[str, Any] | None,
    source_closure: dict[str, Any] | None,
    target_closure: dict[str, Any] | None,
    source_coverage: dict[str, Any],
    target_coverage: dict[str, Any],
) -> dict[str, Any]:
    source = _result_summary(
        orchestration_id=source_orchestration_id,
        run=source_run,
        closure=source_closure,
        coverage=source_coverage,
    )
    target = _result_summary(
        orchestration_id=target_orchestration_id,
        run=target_run,
        closure=target_closure,
        coverage=target_coverage,
    )
    fields = (
        "run_status",
        "cleanup_status",
        "evidence_count",
        "closure_status",
        "ui_status",
        "coverage_percent",
        "passed_test_count",
        "test_count",
    )
    deltas = [
        {"field": field, "before": source[field], "after": target[field]}
        for field in fields
        if source[field] != target[field]
    ]
    return {"source": source, "target": target, "deltas": deltas}


def _scope(bundle: dict[str, Any]) -> dict[str, Any]:
    orchestration = cast(dict[str, Any], bundle["orchestration"])
    test_plan = cast(dict[str, Any], bundle["test_plan"])
    data_plan = cast(dict[str, Any], bundle["test_data_plan"])
    return {
        "test_data": {
            "status": data_plan["status"],
            "data_sets": data_plan["data_sets"],
            "generation_flows": data_plan["generation_flows"],
            "blocking_reasons": data_plan["blocking_reasons"],
        },
        "ui_scenarios": orchestration["ui_scenarios"],
        "execution_scope": {
            "repository_revision": orchestration["repository_revision"],
            "code_scope": orchestration["code_scope"],
            "test_cases": [
                {
                    "test_case_id": value["test_case_id"],
                    "level": value.get("level"),
                    "execution_mode": value.get("execution_mode"),
                    "test_data_refs": value.get("test_data_refs", []),
                }
                for value in cast(list[dict[str, Any]], test_plan["test_cases"])
            ],
        },
    }


def _result_summary(
    *,
    orchestration_id: str,
    run: dict[str, Any] | None,
    closure: dict[str, Any] | None,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    result = cast(dict[str, Any] | None, run.get("result") if run is not None else None)
    evidence = cast(list[object], result.get("evidence", [])) if result else []
    tests = cast(list[dict[str, Any]], closure.get("test_results", [])) if closure else []
    return {
        "orchestration_id": orchestration_id,
        "run_id": run.get("run_id") if run else None,
        "run_status": run.get("status") if run else None,
        "cleanup_status": result.get("cleanup_status") if result else None,
        "evidence_count": len(evidence),
        "closure_result_id": closure.get("closure_result_id") if closure else None,
        "closure_status": closure.get("status") if closure else None,
        "ui_status": closure.get("ui_status") if closure else None,
        "coverage_percent": coverage["coverage_percent"],
        "passed_test_count": sum(test.get("status") == "passed" for test in tests),
        "test_count": len(tests),
    }


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
