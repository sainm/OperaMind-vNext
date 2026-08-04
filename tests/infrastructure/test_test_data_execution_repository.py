from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionEventWrite as EventWrite,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionRecoveryWrite as RecoveryWrite,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionRepository as DataRepository,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionRunWrite as RunWrite,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    _event_id,
    _flow_steps,
    _strings,
    _timestamp,
    _validate_coverage_evidence,
    _validate_evidence_bindings,
)

NOW = datetime(2026, 7, 25, 8, 0, tzinfo=UTC)


class Context:
    def __init__(self, value: object) -> None:
        self.value = value

    def __enter__(self) -> object:
        return self.value

    def __exit__(self, *args: object) -> None:
        return None


class Cursor:
    def __init__(
        self,
        *,
        one: list[object] | None = None,
        all_rows: list[list[tuple[object, ...]]] | None = None,
        rowcount: int = 1,
    ) -> None:
        self.one = list(one or [])
        self.all_rows = list(all_rows or [])
        self.rowcount = rowcount
        self.executions: list[tuple[str, object]] = []

    def execute(self, query: str, parameters: object = None) -> None:
        self.executions.append((query, parameters))

    def fetchone(self) -> object:
        return self.one.pop(0) if self.one else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.all_rows.pop(0) if self.all_rows else []


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self.cursor_value = cursor

    def cursor(self) -> Context:
        return Context(self.cursor_value)

    def transaction(self) -> Context:
        return Context(None)


def test_run_and_recovery_writes_reject_invalid_identity_and_time() -> None:
    valid = _run_write()
    assert valid.started_at == NOW

    with pytest.raises(ValueError, match="must not be blank"):
        _run_write(run_id=" ")
    with pytest.raises(ValueError, match="include a timezone"):
        _run_write(started_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="replay Run ID must not be blank"):
        _run_write(replay_of_run_id=" ")
    with pytest.raises(ValueError, match="cannot replay itself"):
        _run_write(replay_of_run_id="run-1")

    with pytest.raises(ValueError, match="recovery fields must not be blank"):
        RecoveryWrite(
            recovery_id=" ",
            run_id="run-1",
            project_id="project-1",
            actor="operator",
            reason="stale",
            stale_before=NOW,
        )
    with pytest.raises(ValueError, match="include a timezone"):
        RecoveryWrite(
            recovery_id="recovery-1",
            run_id="run-1",
            project_id="project-1",
            actor="operator",
            reason="stale",
            stale_before=NOW.replace(tzinfo=None),
        )


def test_coverage_validation_rejects_self_declared_success_without_real_proofs() -> None:
    artifact = _result_artifact()
    artifact["schema_version"] = "v2"
    artifact["data_coverage"] = {
        "required_criterion_count": 1,
        "covered_criterion_count": 1,
        "coverage_percent": 100,
        "condition_count": 1,
        "passed_condition_count": 0,
        "status": "passed",
        "proofs": [],
    }

    with pytest.raises(ValueError, match="no passed proof"):
        _validate_coverage_evidence(artifact)


def test_v2_passed_execution_rejects_not_applicable_data_coverage() -> None:
    artifact = _result_artifact()
    artifact["schema_version"] = "v2"

    with pytest.raises(ValueError, match="requires 100% data coverage"):
        _validate_coverage_evidence(artifact)


def test_coverage_validation_recomputes_summary_from_reserved_plan() -> None:
    artifact = _result_artifact()
    artifact["data_coverage"] = {
        "required_criterion_count": 0,
        "covered_criterion_count": 0,
        "coverage_percent": 0,
        "condition_count": 0,
        "passed_condition_count": 0,
        "status": "not_applicable",
        "proofs": [],
    }
    plan = {
        "data_sets": [
            {
                "coverage_conditions": [
                    {
                        "condition_id": "condition-1",
                        "criterion_ref": "criterion-1",
                    }
                ]
            }
        ]
    }

    with pytest.raises(ValueError, match="condition count differs from reserved plan"):
        _validate_coverage_evidence(artifact, plan=plan)


def test_reserve_is_idempotent_only_for_the_same_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = Cursor()
    repository = _repository(cursor)
    record = DataRepository._load(Cursor(one=[_record_row()]), "run-1", for_update=False)
    assert record is not None
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: record)
    monkeypatch.setattr(repository, "_created_by", lambda *_args: "operator")

    reservation = repository.reserve(_run_write())

    assert reservation.created is False
    assert reservation.record is record

    with pytest.raises(PersistenceConflictError, match="different content"):
        repository.reserve(_run_write(project_id="other-project"))


@pytest.mark.parametrize(
    ("index", "value", "message"),
    [
        (1, "blocked", "requires a ready"),
        (2, "other-case", "does not match"),
        (3, NOW, "expired"),
        (5, False, "revoked"),
        (6, "closed", "does not permit"),
        (4, ["run_test"], "must allow"),
    ],
)
def test_reserve_rejects_invalid_approval_scope(
    monkeypatch: pytest.MonkeyPatch, index: int, value: object, message: str
) -> None:
    scope: list[object] = [
        "case-1",
        "ready",
        "case-1",
        NOW + timedelta(hours=1),
        ["run_test", "record_evidence"],
        True,
        "editing",
        "test-plan-1",
    ]
    scope[index] = value
    repository = _repository(Cursor(one=[tuple(scope), None]))
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match=message):
        repository.reserve(_run_write())


def test_reserve_rejects_profile_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = (
        "case-1",
        "ready",
        "case-1",
        NOW + timedelta(hours=1),
        ["run_test", "record_evidence"],
        True,
        "editing",
        "test-plan-1",
    )
    repository = _repository(Cursor(one=[scope, ("Profile changed",)]))
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="Profile changed"):
        repository.reserve(_run_write())


def test_load_plan_requires_ready_matching_artifact() -> None:
    for row, artifact, error in (
        (None, None, "Ready Test data Orchestration"),
        (("plan-1", "blocked"), None, "Ready Test data Orchestration"),
        (("plan-1", "ready"), None, "Artifact is missing"),
        (
            ("plan-1", "ready"),
            {"artifact_type": "TestPlan", "project_id": "project-1"},
            "Artifact is missing",
        ),
        (
            ("plan-1", "ready"),
            {"artifact_type": "TestDataPlan", "project_id": "other-project"},
            "scope differs",
        ),
    ):
        repository = _repository(Cursor(one=[row]))
        repository._artifacts = SimpleNamespace(get=lambda _artifact_id, value=artifact: value)
        with pytest.raises((ValueError, PersistenceConflictError), match=error):
            repository.load_plan(orchestration_id="orchestration-1", project_id="project-1")

    expected = {"artifact_type": "TestDataPlan", "project_id": "project-1"}
    repository = _repository(Cursor(one=[("plan-1", "ready")]))
    repository._artifacts = SimpleNamespace(get=lambda _artifact_id: expected)
    assert (
        repository.load_plan(orchestration_id="orchestration-1", project_id="project-1") is expected
    )


def test_append_event_validates_scope_state_and_persists_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(Cursor())
    invalid = (
        (EventWrite("", "project-1", "started"), "must not be blank"),
        (
            EventWrite("run-1", "project-1", "started", phase="verify"),
            "phase is invalid",
        ),
        (
            EventWrite("run-1", "project-1", "started", step_id="step-1"),
            "requires flow and phase",
        ),
    )
    for write, message in invalid:
        with pytest.raises(ValueError, match=message):
            repository.append_event(write)

    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="Run does not exist"):
        repository.append_event(EventWrite("run-1", "project-1", "started"))

    completed = _record(status="passed")
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: completed)
    with pytest.raises(ValueError, match="does not accept"):
        repository.append_event(EventWrite("run-1", "project-1", "step_started"))

    cursor = Cursor(one=[(3,), (NOW,)])
    repository = _repository(cursor)
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: _record(status="running"))
    write = EventWrite(
        run_id="run-1",
        project_id="project-1",
        event_type="step_completed",
        flow_id="flow-1",
        phase="setup",
        step_id="step-1",
        status="passed",
        message="done",
    )

    event = repository.append_event(write)

    assert event == {
        "event_id": _event_id("run-1", 3, "step_completed"),
        "sequence": 3,
        "event_type": "step_completed",
        "flow_id": "flow-1",
        "phase": "setup",
        "step_id": "step-1",
        "status": "passed",
        "message": "done",
        "created_at": NOW.isoformat(),
    }


def test_execution_claim_distinguishes_claimed_busy_stale_and_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claimed_record = _record(status="running")
    persisted_record = _record(status="running")
    cursor = Cursor()
    repository = _repository(cursor)
    records = iter((claimed_record, persisted_record))
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: next(records))

    claim = repository.claim_execution(
        run_id="run-1",
        executor_id="worker-1",
        at=NOW,
        lease_seconds=300,
    )

    assert claim.outcome == "claimed"
    assert claim.record is persisted_record
    assert "attempt_count = attempt_count + 1" in cursor.executions[0][0]

    for outcome, record in (
        (
            "busy",
            _record(
                status="running",
                execution_owner="worker-1",
                lease_expires_at=NOW + timedelta(seconds=1),
            ),
        ),
        (
            "terminal",
            _record(status="passed", completed_at=NOW),
        ),
    ):
        repository = _repository(Cursor())
        monkeypatch.setattr(repository, "_load", lambda *_args, value=record, **_kwargs: value)
        result = repository.claim_execution(
            run_id="run-1",
            executor_id="worker-2",
            at=NOW,
            lease_seconds=300,
        )
        assert result.outcome == outcome

    stale_cursor = Cursor()
    repository = _repository(stale_cursor)
    stale = _record(
        status="running",
        execution_owner="dead-worker",
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: stale)
    result = repository.claim_execution(
        run_id="run-1",
        executor_id="recovery-worker",
        at=NOW,
        lease_seconds=300,
    )
    assert result.outcome == "stale"
    assert "UPDATE test_data_execution_runs" in stale_cursor.executions[0][0]


def test_execution_claim_and_heartbeat_validate_identity_and_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(Cursor())
    for values, message in (
        ({"run_id": " ", "executor_id": "worker", "at": NOW, "lease_seconds": 300}, "blank"),
        (
            {
                "run_id": "run-1",
                "executor_id": "worker",
                "at": NOW.replace(tzinfo=None),
                "lease_seconds": 300,
            },
            "timezone",
        ),
        (
            {"run_id": "run-1", "executor_id": "worker", "at": NOW, "lease_seconds": 10},
            "lease_seconds",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            repository.claim_execution(**values)  # type: ignore[arg-type]

    missing = _repository(Cursor())
    monkeypatch.setattr(missing, "_load", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="does not exist"):
        missing.claim_execution(run_id="run-1", executor_id="worker", at=NOW, lease_seconds=300)

    heartbeat_cursor = Cursor(rowcount=1)
    repository = _repository(heartbeat_cursor)
    assert repository.heartbeat_execution(
        run_id="run-1", executor_id="worker", at=NOW, lease_seconds=300
    )
    heartbeat_cursor.rowcount = 0
    assert not repository.heartbeat_execution(
        run_id="run-1", executor_id="worker", at=NOW, lease_seconds=300
    )


def test_events_and_recovery_are_normalized_for_web_output() -> None:
    event_row = (
        "event-1",
        1,
        "started",
        None,
        None,
        None,
        None,
        None,
        NOW,
    )
    repository = _repository(Cursor(all_rows=[[event_row]]))
    assert repository.events("run-1") == [
        {
            "event_id": "event-1",
            "sequence": 1,
            "event_type": "started",
            "flow_id": None,
            "phase": None,
            "step_id": None,
            "status": None,
            "message": None,
            "created_at": NOW.isoformat(),
        }
    ]

    repository = _repository(Cursor(one=[None]))
    assert repository.recovery("run-1") is None
    repository = _repository(Cursor(one=[("recovery-1", "operator", "stale", NOW, NOW)]))
    assert repository.recovery("run-1") == {
        "recovery_id": "recovery-1",
        "actor": "operator",
        "reason": "stale",
        "stale_before": NOW.isoformat(),
        "created_at": NOW.isoformat(),
    }


def test_latest_active_scope_fails_closed_and_returns_authorization() -> None:
    repository = _repository(Cursor())
    with pytest.raises(ValueError, match="include a timezone"):
        repository.latest_active_scope(
            orchestration_id="orchestration-1",
            project_id="project-1",
            at=NOW.replace(tzinfo=None),
        )

    repository = _repository(Cursor(one=[None]))
    with pytest.raises(ValueError, match="No active Approval"):
        repository.latest_active_scope(
            orchestration_id="orchestration-1", project_id="project-1", at=NOW
        )

    authorization = {
        "project_id": "project-1",
        "authorized": True,
        "approval_grant_id": "grant-1",
        "status": "active",
        "authorization_id": "authorization-1",
        "blocking_reason": None,
    }
    repository = _repository(Cursor(one=[(1,)]))
    repository._case_execution_authorizations = SimpleNamespace(
        state=lambda **_kwargs: authorization
    )
    assert repository.latest_active_scope(
        orchestration_id="orchestration-1", project_id="project-1", at=NOW
    ) == {
        "approval_grant_id": "grant-1",
        "authorization_status": "active",
        "authorization_id": "authorization-1",
    }

    authorization["project_id"] = "other-project"
    repository = _repository(Cursor(one=[(1,)]))
    repository._case_execution_authorizations = SimpleNamespace(
        state=lambda **_kwargs: authorization
    )
    with pytest.raises(ValueError, match="project differs"):
        repository.latest_active_scope(
            orchestration_id="orchestration-1", project_id="project-1", at=NOW
        )

    authorization.update(project_id="project-1", authorized=False, blocking_reason="revoked")
    repository = _repository(Cursor(one=[(1,)]))
    repository._case_execution_authorizations = SimpleNamespace(
        state=lambda **_kwargs: authorization
    )
    with pytest.raises(ValueError, match="revoked"):
        repository.latest_active_scope(
            orchestration_id="orchestration-1", project_id="project-1", at=NOW
        )


def test_latest_run_views(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository(Cursor())
    with pytest.raises(ValueError, match="must not be blank"):
        repository.latest_for_orchestration(" ")
    repository = _repository(Cursor(one=[None]))
    assert repository.latest_for_orchestration("orchestration-1") is None

    repository = _repository(Cursor(one=[("run-1",)]))
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeError, match="disappeared"):
        repository.latest_for_orchestration("orchestration-1")

    record = _record(status="running")
    repository = _repository(Cursor(one=[("run-1",)]))
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: record)
    monkeypatch.setattr(repository, "events", lambda _run_id: [{"event_id": "event-1"}])
    monkeypatch.setattr(repository, "recovery", lambda _run_id: None)
    latest = repository.latest_for_orchestration("orchestration-1")
    assert latest is not None
    assert latest["status"] == "running"
    assert latest["result"] is None
    assert latest["completed_at"] is None

    record = _record(status="passed", completed_at=NOW)
    repository = _repository(Cursor(one=[("run-1",)]))
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: record)
    monkeypatch.setattr(repository, "events", lambda _run_id: [])
    monkeypatch.setattr(repository, "recovery", lambda _run_id: {"recovery_id": "r-1"})
    monkeypatch.setattr(
        repository, "get_result", lambda _run_id: {"artifact_type": "TestDataExecutionResult"}
    )
    latest = repository.latest_for_orchestration("orchestration-1")
    assert latest is not None
    assert latest["completed_at"] == NOW.isoformat()
    assert latest["result"] == {"artifact_type": "TestDataExecutionResult"}


def test_load_created_by_and_normalized_comparison_helpers() -> None:
    cursor = Cursor(one=[None])
    assert DataRepository._load(cursor, "missing", for_update=True) is None
    assert "FOR UPDATE" in cursor.executions[0][0]

    loaded = DataRepository._load(
        Cursor(one=[_record_row(replay_of_run_id="old-run")]),
        "run-1",
        for_update=False,
    )
    assert loaded is not None
    assert loaded.replay_of_run_id == "old-run"

    with pytest.raises(RuntimeError, match="disappeared"):
        DataRepository._created_by(Cursor(one=[None]), "run-1")
    assert DataRepository._created_by(Cursor(one=[("operator",)]), "run-1") == "operator"

    artifact = _result_artifact()
    flows, steps, bindings, evidence = _normalized_rows(artifact)
    assert DataRepository._normalized_matches(
        Cursor(all_rows=[flows, steps, bindings, evidence]), "run-1", artifact
    )
    assert not DataRepository._normalized_matches(Cursor(all_rows=[[]]), "run-1", artifact)
    assert not DataRepository._normalized_matches(Cursor(all_rows=[flows, []]), "run-1", artifact)
    assert not DataRepository._normalized_matches(
        Cursor(all_rows=[flows, steps, []]), "run-1", artifact
    )

    first = artifact["flow_results"][0]["step_results"][0]
    second = dict(first, step_id="a-later-step", sequence=2)
    first["step_id"] = "z-first-step"
    artifact["flow_results"][0]["step_results"].append(second)
    flows, steps, bindings, evidence = _normalized_rows(artifact)
    assert DataRepository._normalized_matches(
        Cursor(all_rows=[flows, steps, bindings, evidence]), "run-1", artifact
    )


def test_evidence_binding_and_scalar_helpers_reject_invalid_content() -> None:
    artifact = _result_artifact()
    _validate_evidence_bindings(artifact)
    assert len(_flow_steps(artifact["flow_results"][0])) == 2
    assert _strings(["run_test", "record_evidence"]) == frozenset({"run_test", "record_evidence"})
    assert _timestamp("2026-07-25T08:00:00Z") == NOW

    with pytest.raises(PersistenceConflictError, match="allowed_actions"):
        _strings("run_test")
    with pytest.raises(PersistenceConflictError, match="allowed_actions"):
        _strings(["run_test", 1])
    with pytest.raises(ValueError, match="include a timezone"):
        _timestamp("2026-07-25T08:00:00")

    duplicate = _result_artifact()
    duplicate["evidence"].append(dict(duplicate["evidence"][0]))
    with pytest.raises(ValueError, match="must be unique"):
        _validate_evidence_bindings(duplicate)

    missing = _result_artifact()
    missing["flow_results"][0]["step_results"][0]["evidence_refs"] = ["missing"]
    with pytest.raises(ValueError, match="ref is missing"):
        _validate_evidence_bindings(missing)

    wrong_scope = _result_artifact()
    wrong_scope["evidence"][0]["step_id"] = "other-step"
    with pytest.raises(ValueError, match="different scope"):
        _validate_evidence_bindings(wrong_scope)

    unreferenced = _result_artifact()
    unreferenced["flow_results"][0]["step_results"][0]["evidence_refs"] = []
    with pytest.raises(ValueError, match="must be referenced"):
        _validate_evidence_bindings(unreferenced)


def test_completion_and_recovery_require_the_canonical_result_type() -> None:
    repository = _repository(Cursor())
    with pytest.raises(ValueError, match="requires TestDataExecutionResult"):
        repository.complete({"artifact_type": "TestPlan"})
    with pytest.raises(ValueError, match="must be interrupted"):
        repository.recover(
            artifact={"status": "passed"},
            recovery=RecoveryWrite(
                recovery_id="recovery-1",
                run_id="run-1",
                project_id="project-1",
                actor="operator",
                reason="stale",
                stale_before=NOW,
            ),
        )
    with pytest.raises(ValueError, match="completion owner"):
        repository.complete(_result_artifact(), execution_owner=" ")


def test_completion_update_requires_the_live_claim_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = Cursor()
    repository = _repository(cursor)
    repository._artifacts = SimpleNamespace(
        get=lambda _artifact_id: {
            "artifact_type": "TestDataPlan",
            "project_id": "project-1",
            "data_sets": [],
        },
        store=lambda **_values: None,
    )
    running = _record(
        status="running",
        execution_owner="worker-1",
        heartbeat_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    completed = _record(status="passed")
    records = iter((running, completed))
    monkeypatch.setattr(repository, "_load", lambda *_args, **_kwargs: next(records))

    result = repository.complete(_result_artifact(), execution_owner="worker-1")

    update = next(
        (query, parameters)
        for query, parameters in cursor.executions
        if "UPDATE test_data_execution_runs" in query
    )
    assert "execution_owner = %s" in update[0]
    assert "lease_expires_at > now()" in update[0]
    assert update[1][-2:] == ("worker-1", "worker-1")
    assert result.status == "passed"


def _repository(cursor: Cursor) -> DataRepository:
    repository = object.__new__(DataRepository)
    repository._connection = Connection(cursor)
    repository._contracts = SimpleNamespace(validate_artifact=lambda _artifact: None)
    repository._artifacts = SimpleNamespace()
    repository._case_execution_authorizations = SimpleNamespace()
    return repository


def _run_write(**changes: Any) -> RunWrite:
    values = {
        "run_id": "run-1",
        "execution_result_id": "result-1",
        "orchestration_id": "orchestration-1",
        "test_data_plan_id": "plan-1",
        "approval_grant_id": "grant-1",
        "project_id": "project-1",
        "created_by": "operator",
        "started_at": NOW,
        "replay_of_run_id": None,
    }
    values.update(changes)
    return RunWrite(**values)


def _record(*, status: str, completed_at: datetime | None = None, **changes: object) -> object:
    values: dict[str, object] = {
        "run_id": "run-1",
        "execution_result_id": "result-1",
        "orchestration_id": "orchestration-1",
        "test_data_plan_id": "plan-1",
        "approval_grant_id": "grant-1",
        "project_id": "project-1",
        "analysis_case_id": "case-1",
        "status": status,
        "started_at": NOW,
        "completed_at": completed_at,
        "replay_of_run_id": None,
        "execution_owner": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "attempt_count": 0,
        "max_attempts": 3,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _record_row(*, replay_of_run_id: str | None = None) -> tuple[object, ...]:
    return (
        "result-1",
        "orchestration-1",
        "plan-1",
        "grant-1",
        "project-1",
        "case-1",
        "running",
        NOW,
        None,
        replay_of_run_id,
        None,
        None,
        None,
        0,
        3,
    )


def _result_artifact() -> dict[str, Any]:
    step = {
        "phase": "setup",
        "step_id": "step-1",
        "sequence": 1,
        "channel": "db",
        "status": "passed",
        "output_variables": {"employee_id": 10},
        "evidence_refs": ["evidence://setup/1"],
    }
    cleanup = {
        "phase": "cleanup",
        "step_id": "cleanup-1",
        "sequence": 1,
        "channel": "db",
        "status": "passed",
        "output_variables": {},
        "evidence_refs": [],
    }
    return {
        "artifact_type": "TestDataExecutionResult",
        "execution_result_id": "result-1",
        "run_id": "run-1",
        "test_data_plan_id": "plan-1",
        "project_id": "project-1",
        "started_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
        "status": "passed",
        "flow_results": [
            {
                "flow_id": "flow-1",
                "status": "passed",
                "deferred_assertion_ids": [],
                "step_results": [step],
                "cleanup_results": [cleanup],
            }
        ],
        "data_bindings": [],
        "data_coverage": {
            "required_criterion_count": 0,
            "covered_criterion_count": 0,
            "coverage_percent": 0,
            "condition_count": 0,
            "passed_condition_count": 0,
            "status": "not_applicable",
            "proofs": [],
        },
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "flow_id": "flow-1",
                "phase": "setup",
                "step_id": "step-1",
                "evidence_type": "database",
                "evidence_ref": "evidence://setup/1",
                "content_digest": "a" * 64,
                "sanitized": True,
            }
        ],
    }


def _normalized_rows(
    artifact: dict[str, Any],
) -> tuple[
    list[tuple[object, ...]],
    list[tuple[object, ...]],
    list[tuple[object, ...]],
    list[tuple[object, ...]],
]:
    flow = artifact["flow_results"][0]
    flows = [
        (
            flow["flow_id"],
            1,
            flow["status"],
            flow["deferred_assertion_ids"],
        )
    ]
    steps = sorted(
        (
            (
                flow["flow_id"],
                step["phase"],
                step["step_id"],
                step["sequence"],
                step["channel"],
                step["status"],
                step["output_variables"],
                step["evidence_refs"],
                step.get("failure_reason"),
            )
            for step in _flow_steps(flow)
        ),
        key=lambda value: (value[0], value[1], value[3]),
    )
    evidence = [
        (
            "evidence-1",
            "flow-1",
            "setup",
            "step-1",
            "database",
            "evidence://setup/1",
            "a" * 64,
            True,
        )
    ]
    return flows, steps, [], evidence
