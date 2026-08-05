"""Immutable Run-level variables and frozen TestDataBinding coordination."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from operamind.run_context_values import build_test_data_token, canonical_digest

SYSTEM_RUNTIME_VARIABLE_NAMES = frozenset(
    {"operamind_run_id", "test_data_token", "execution_started_at"}
)


class RunContext:
    """Own Run-scoped state while keeping Flow outputs explicitly local.

    Frozen bindings can only be added once. Every read revalidates their Project,
    Run, test-data identity and content digest so a stale or foreign mapping cannot
    be smuggled into a later Flow.
    """

    def __init__(
        self,
        *,
        project_id: str,
        run_id: str,
        execution_started_at: datetime,
        flow_dependencies: Mapping[str, Sequence[str]],
    ) -> None:
        if not project_id.strip() or not run_id.strip():
            raise ValueError("RunContext scope must not be blank")
        if execution_started_at.utcoffset() is None:
            raise ValueError("RunContext execution_started_at must include a timezone")
        self.project_id = project_id
        self.run_id = run_id
        started_at = execution_started_at.astimezone(UTC)
        self.runtime_variables: Mapping[str, object] = MappingProxyType(
            {
                "operamind_run_id": run_id,
                "test_data_token": build_test_data_token(
                    project_id=project_id,
                    run_id=run_id,
                    started_at=started_at,
                ),
                "execution_started_at": started_at.isoformat().replace("+00:00", "Z"),
            }
        )
        dependencies = {
            str(flow_id): tuple(str(value) for value in values)
            for flow_id, values in flow_dependencies.items()
        }
        self.execution_order = _topological_flow_order(dependencies)
        self.flow_dependencies: Mapping[str, tuple[str, ...]] = MappingProxyType(
            dependencies
        )
        self._local_variables: dict[str, dict[str, object]] = {
            flow_id: {} for flow_id in dependencies
        }
        self._frozen_data_bindings: dict[str, dict[str, Any]] = {}
        self._evidence_refs: set[str] = set()

    @property
    def frozen_data_bindings(self) -> Mapping[str, Mapping[str, object]]:
        return MappingProxyType(
            {
                test_data_id: MappingProxyType(dict(binding))
                for test_data_id, binding in self._frozen_data_bindings.items()
            }
        )

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(sorted(self._evidence_refs))

    def variables_for_flow(self, flow_id: str) -> Mapping[str, object]:
        local = self._local_variables.get(flow_id)
        if local is None:
            raise ValueError(f"RunContext Flow is not registered: {flow_id}")
        return MappingProxyType({**self.runtime_variables, **local})

    def local_variables_for_flow(self, flow_id: str) -> dict[str, object]:
        local = self._local_variables.get(flow_id)
        if local is None:
            raise ValueError(f"RunContext Flow is not registered: {flow_id}")
        return local

    def set_local_variable(self, *, flow_id: str, name: str, value: object) -> None:
        if name in SYSTEM_RUNTIME_VARIABLE_NAMES:
            raise ValueError(f"System Run variable is read-only: {name}")
        local = self.local_variables_for_flow(flow_id)
        if name in local:
            raise ValueError(f"Flow-local variable is already defined: {flow_id}/{name}")
        local[name] = value

    def freeze_binding(self, binding: Mapping[str, object]) -> None:
        test_data_id = self._validate_binding(binding)
        if test_data_id in self._frozen_data_bindings:
            raise ValueError(f"Data binding is already frozen: {test_data_id}")
        self._frozen_data_bindings[test_data_id] = dict(binding)

    def resolve_binding(self, test_data_id: str) -> Mapping[str, object]:
        binding = self._frozen_data_bindings.get(test_data_id)
        if binding is None:
            raise ValueError(f"Frozen Data binding does not exist: {test_data_id}")
        self._validate_binding(binding, expected_test_data_id=test_data_id)
        return MappingProxyType(dict(binding))

    def add_evidence_refs(self, values: Sequence[str]) -> None:
        for value in values:
            if not value.strip():
                raise ValueError("RunContext Evidence ref must not be blank")
            self._evidence_refs.add(value)

    def to_artifact(self) -> dict[str, object]:
        return {
            "runtime_variables": dict(self.runtime_variables),
            "frozen_data_bindings": [
                dict(self._frozen_data_bindings[key])
                for key in sorted(self._frozen_data_bindings)
            ],
            "flow_dependencies": {
                key: list(self.flow_dependencies[key])
                for key in sorted(self.flow_dependencies)
            },
            "evidence_refs": list(self.evidence_refs),
        }

    def _validate_binding(
        self,
        binding: Mapping[str, object],
        *,
        expected_test_data_id: str | None = None,
    ) -> str:
        project_id = str(binding.get("project_id", ""))
        run_id = str(binding.get("run_id", ""))
        test_data_id = str(binding.get("test_data_id", ""))
        if project_id != self.project_id:
            raise ValueError("Frozen Data binding belongs to another Project")
        if run_id != self.run_id:
            raise ValueError("Frozen Data binding belongs to another Run")
        if not test_data_id or (
            expected_test_data_id is not None and test_data_id != expected_test_data_id
        ):
            raise ValueError("Frozen Data binding Test Data identity differs")
        expected_digest = str(binding.get("content_digest", ""))
        payload = {
            key: value
            for key, value in binding.items()
            if key not in {"content_digest", "evidence_ref"}
        }
        if expected_digest != canonical_digest(payload):
            raise ValueError("Frozen Data binding content digest differs")
        return test_data_id


def flow_dependencies_from_plan(
    flows: Sequence[Mapping[str, object]],
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for flow in flows:
        raw = flow.get("depends_on_flows", [])
        values = raw if isinstance(raw, list | tuple) else []
        result[str(flow.get("flow_id", ""))] = tuple(str(value) for value in values)
    return result


def _topological_flow_order(
    dependencies: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    known = set(dependencies)
    for flow_id, values in dependencies.items():
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"{flow_id}: Flow dependencies do not exist: {sorted(unknown)}")
        if flow_id in values:
            raise ValueError(f"{flow_id}: Flow cannot depend on itself")
    remaining: dict[str, set[str]] = {
        key: set(values) for key, values in dependencies.items()
    }
    ordered: list[str] = []
    while remaining:
        ready = sorted(key for key, values in remaining.items() if not values)
        if not ready:
            raise ValueError(f"Flow dependency cycle exists: {sorted(remaining)}")
        ordered.extend(ready)
        for key in ready:
            remaining.pop(key)
        for pending_dependencies in remaining.values():
            pending_dependencies.difference_update(ready)
    return tuple(ordered)
