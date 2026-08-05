from datetime import UTC, datetime

import pytest

from operamind.application.run_context import RunContext, canonical_digest


def _context(*, run_id: str = "run-001", project_id: str = "project-001") -> RunContext:
    return RunContext(
        project_id=project_id,
        run_id=run_id,
        execution_started_at=datetime(2026, 8, 5, 10, 11, 12, tzinfo=UTC),
        flow_dependencies={"adopt": (), "operate": ("adopt",), "cleanup": ("operate",)},
    )


def _binding(*, run_id: str = "run-001", project_id: str = "project-001") -> dict[str, object]:
    payload: dict[str, object] = {
        "binding_id": "binding-001",
        "project_id": project_id,
        "run_id": run_id,
        "test_data_id": "expense-existing",
        "binding_mode": "adopted",
    }
    return {
        **payload,
        "content_digest": canonical_digest(payload),
        "evidence_ref": "artifact://result/data-bindings/binding-001",
    }


def test_run_context_freezes_system_variables_and_separates_flow_locals() -> None:
    context = _context()

    assert context.execution_order == ("adopt", "operate", "cleanup")
    assert context.runtime_variables == {
        "operamind_run_id": "run-001",
        "test_data_token": "OM-E2E-20260805-BD7130B5",
        "execution_started_at": "2026-08-05T10:11:12Z",
    }
    context.set_local_variable(flow_id="adopt", name="expense_id", value="EXP-001")
    assert context.variables_for_flow("adopt")["expense_id"] == "EXP-001"
    assert "expense_id" not in context.variables_for_flow("operate")

    with pytest.raises(ValueError, match="read-only"):
        context.set_local_variable(flow_id="adopt", name="test_data_token", value="changed")
    with pytest.raises(ValueError, match="already defined"):
        context.set_local_variable(flow_id="adopt", name="expense_id", value="EXP-002")


def test_run_context_rejects_unknown_and_cyclic_flow_dependencies() -> None:
    with pytest.raises(ValueError, match="do not exist"):
        RunContext(
            project_id="project-001",
            run_id="run-001",
            execution_started_at=datetime.now(UTC),
            flow_dependencies={"flow-a": ("missing",)},
        )
    with pytest.raises(ValueError, match="cycle"):
        RunContext(
            project_id="project-001",
            run_id="run-001",
            execution_started_at=datetime.now(UTC),
            flow_dependencies={"flow-a": ("flow-b",), "flow-b": ("flow-a",)},
        )


def test_run_context_blocks_foreign_mutated_and_duplicate_bindings() -> None:
    context = _context()
    context.freeze_binding(_binding())
    assert context.resolve_binding("expense-existing")["binding_id"] == "binding-001"

    with pytest.raises(ValueError, match="already frozen"):
        context.freeze_binding(_binding())
    with pytest.raises(ValueError, match="another Run"):
        context.freeze_binding(_binding(run_id="run-foreign"))
    with pytest.raises(ValueError, match="another Project"):
        context.freeze_binding(_binding(project_id="project-foreign"))
    mutated = _binding()
    mutated["binding_mode"] = "generated"
    with pytest.raises(ValueError, match="digest differs"):
        _context().freeze_binding(mutated)


def test_run_token_is_stable_per_run_and_differs_between_runs() -> None:
    first = _context(run_id="run-001")
    same = _context(run_id="run-001")
    second = _context(run_id="run-002")

    assert first.runtime_variables["test_data_token"] == same.runtime_variables["test_data_token"]
    assert first.runtime_variables["test_data_token"] != second.runtime_variables["test_data_token"]
