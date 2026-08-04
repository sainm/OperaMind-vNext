import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from operamind.application.orchestration_worker import (
    OrchestrationTaskExecutionContext,
    OrchestrationTaskExecutionResult,
    OrchestrationTaskWorker,
    OrchestrationWorkerConfiguration,
)
from operamind.application.web_control_plane import (
    BusinessRuleInput,
    ChangeRequestInput,
    WebControlPlaneService,
)
from operamind.infrastructure.postgres import (
    MigrationCatalog,
    MigrationRunner,
    OrchestrationTaskRepository,
    PersistenceConflictError,
)

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_task_claim_lease_result_and_single_agent_policy() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"task-project-{suffix}"
    request_id = f"task-request-{suffix}"

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES (%s, 'Task protocol test')",
                (project_id,),
            )
        service = WebControlPlaneService(connection=connection, repository_root=ROOT)
        task_repository = OrchestrationTaskRepository(connection)
        worker_one_registration = task_repository.register_worker(
            executor_kind="subagent",
            executor_id="worker-1",
            capabilities=("requirement_review",),
            project_id=project_id,
            max_concurrent_tasks=1,
            lease_seconds=30,
        )
        worker_two_registration = task_repository.register_worker(
            executor_kind="agent",
            executor_id="worker-2",
            capabilities=("requirement_review",),
            project_id=project_id,
            max_concurrent_tasks=1,
            lease_seconds=30,
        )
        service.submit_change_request(
            ChangeRequestInput(
                change_request_id=request_id,
                project_id=project_id,
                analysis_case_id=None,
                input_mode="natural_language",
                requirement_text="确认跨执行者任务协议",
                source_document_ref=None,
                target_document_ref=None,
                business_rules=(BusinessRuleInput("rule-1", "需要人工确认", ()),),
                ambiguity_status="needs_confirmation",
                ambiguities=("确认验收边界",),
                submitted_by="owner",
            )
        )
        started = service.start_change_automation(
            request_id=request_id,
            idempotency_key="task-protocol",
            actor="owner",
        )
        run = started["run"]
        task = run["current_task"]
        assert task["action"] == "confirm_requirement"
        assert task["eligible_executor_kinds"] == ["agent", "subagent", "human"]

        ready = task_repository.list_ready(
            executor_kind="subagent",
            capabilities=("requirement_review",),
            project_id=project_id,
        )
        assert [value["orchestration_task_id"] for value in ready] == [
            task["orchestration_task_id"]
        ]

        claimed = task_repository.claim(
            task_id=task["orchestration_task_id"],
            executor_kind="subagent",
            executor_id="worker-1",
            capabilities=("requirement_review",),
            project_id=project_id,
            worker_token=str(worker_one_registration["worker_token"]),
        )
        assert claimed is not None
        lease_token = claimed["lease_token"]
        task_id = claimed["orchestration_task_id"]
        assert "lease_token" not in claimed["claims"][0]
        assert (
            task_repository.claim_next(
                executor_kind="agent",
                executor_id="worker-2",
                capabilities=("requirement_review",),
                project_id=project_id,
                worker_token=str(worker_two_registration["worker_token"]),
            )
            is None
        )

        running = task_repository.heartbeat(
            task_id=task_id,
            executor_id="worker-1",
            lease_token=lease_token,
        )
        assert running["state"] == "running"
        with pytest.raises(ValueError, match="does not belong"):
            task_repository.record_result(
                task_id=task_id,
                executor_id="worker-1",
                lease_token="wrong-token",
                outcome="completed",
                summary="invalid",
                artifact_refs=("confirmation-1",),
                evidence={"human_confirmation": True},
            )
        completed = task_repository.record_result(
            task_id=task_id,
            executor_id="worker-1",
            lease_token=lease_token,
            outcome="completed",
            summary="人工确认记录已写入",
            artifact_refs=("confirmation-1",),
            evidence={"human_confirmation": True},
        )
        assert completed["state"] == "submitted"
        assert completed["results"][0]["artifact_refs"] == ["confirmation-1"]
        assert completed["claims"][0]["status"] == "completed"
        assert completed["events"][-1]["event_type"] == "result_submitted"

        replayed = task_repository.record_result(
            task_id=task_id,
            executor_id="worker-1",
            lease_token=lease_token,
            outcome="completed",
            summary="人工确认记录已写入",
            artifact_refs=("confirmation-1",),
            evidence={"human_confirmation": True},
        )
        assert len(replayed["results"]) == 1
        assert [event["event_type"] for event in replayed["events"]].count("result_submitted") == 1
        with pytest.raises(PersistenceConflictError, match="different content"):
            task_repository.record_result(
                task_id=task_id,
                executor_id="worker-1",
                lease_token=lease_token,
                outcome="completed",
                summary="異なる結果で再送",
                artifact_refs=("confirmation-1",),
                evidence={"human_confirmation": True},
            )

        expiring_request_id = f"task-expiry-request-{suffix}"
        service.submit_change_request(
            ChangeRequestInput(
                change_request_id=expiring_request_id,
                project_id=project_id,
                analysis_case_id=None,
                input_mode="natural_language",
                requirement_text="验证任务租约恢复",
                source_document_ref=None,
                target_document_ref=None,
                business_rules=(BusinessRuleInput("rule-expiry", "租约可恢复", ()),),
                ambiguity_status="needs_confirmation",
                ambiguities=("确认恢复边界",),
                submitted_by="owner",
            )
        )
        service.start_change_automation(
            request_id=expiring_request_id,
            idempotency_key="lease-expiry",
            actor="owner",
        )
        expiring = task_repository.claim_next(
            executor_kind="human",
            executor_id="reviewer-1",
            capabilities=("requirement_review",),
            project_id=project_id,
        )
        assert expiring is not None
        expiring_task_id = expiring["orchestration_task_id"]
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE orchestration_task_claims
                SET lease_expires_at = claimed_at + interval '1 microsecond'
                WHERE orchestration_task_id = %s AND status = 'active'
                """,
                (expiring_task_id,),
            )
            cursor.execute(
                """
                UPDATE orchestration_tasks
                SET attempt_count = max_attempts
                WHERE orchestration_task_id = %s
                """,
                (expiring_task_id,),
            )
        assert task_repository.view(str(expiring_task_id))["effective_state"] == "failed"
        exhausted_ready = task_repository.list_management(
            project_id=project_id,
            states=("ready",),
            capability="requirement_review",
            blocking_reason=None,
            limit=50,
        )
        assert expiring_task_id not in {
            value["orchestration_task_id"] for value in exhausted_ready
        }
        exhausted_failed = task_repository.list_management(
            project_id=project_id,
            states=("failed",),
            capability="requirement_review",
            blocking_reason=None,
            limit=50,
        )
        assert expiring_task_id in {
            value["orchestration_task_id"] for value in exhausted_failed
        }
        exhausted_graph = task_repository.dependency_graph(
            project_id=project_id,
            automation_run_id=None,
            limit=50,
        )
        exhausted_graph_task = next(
            value
            for value in exhausted_graph["tasks"]
            if value["orchestration_task_id"] == expiring_task_id
        )
        assert exhausted_graph_task["effective_state"] == "failed"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE orchestration_tasks
                SET attempt_count = 1
                WHERE orchestration_task_id = %s
                """,
                (expiring_task_id,),
            )
        recovered = task_repository.list_ready(
            executor_kind="agent",
            capabilities=("requirement_review",),
            project_id=project_id,
        )
        recovered_task = next(
            value for value in recovered if value["orchestration_task_id"] == expiring_task_id
        )
        assert recovered_task["state"] == "claimed"
        assert recovered_task["effective_state"] == "ready"
        assert recovered_task["lease_expired"] is True
        assert recovered_task["claims"][0]["status"] == "active"
        management_ready = task_repository.list_management(
            project_id=project_id,
            states=("ready",),
            capability="requirement_review",
            blocking_reason=None,
            limit=50,
        )
        assert expiring_task_id in {value["orchestration_task_id"] for value in management_ready}

        reclaimed = task_repository.claim_next(
            executor_kind="agent",
            executor_id="worker-2",
            capabilities=("requirement_review",),
            project_id=project_id,
            worker_token=str(worker_two_registration["worker_token"]),
        )
        assert reclaimed is not None
        assert reclaimed["claims"][0]["status"] == "expired"
        assert reclaimed["events"][-1]["event_type"] == "claimed"
        blocked = task_repository.record_result(
            task_id=reclaimed["orchestration_task_id"],
            executor_id="worker-2",
            lease_token=reclaimed["lease_token"],
            outcome="blocked",
            summary="外部確認待ち",
            artifact_refs=(),
            evidence={"blocking_reason": "owner unavailable"},
        )
        assert blocked["state"] == "blocked"
        assert blocked["blocking_reason"] == "owner unavailable"
        management = task_repository.list_management(
            project_id=project_id,
            states=("blocked",),
            capability="requirement_review",
            blocking_reason="owner",
            limit=50,
        )
        assert len(management) == 1
        assert management[0]["orchestration_task_id"] == expiring_task_id
        assert management[0]["automation_run_id"] != task["automation_run_id"]
        requeued = task_repository.requeue(
            task_id=reclaimed["orchestration_task_id"],
            actor="operator-1",
            reason="owner is available",
        )
        assert requeued["state"] == "ready"
        assert requeued["events"][-1]["event_type"] == "requeued"

        worker_repository = OrchestrationTaskRepository(connection)
        worker_registration = worker_repository.register_worker(
            executor_kind="agent",
            executor_id="worker-3",
            capabilities=("requirement_review",),
            project_id=project_id,
            max_concurrent_tasks=1,
            lease_seconds=30,
        )
        worker = OrchestrationTaskWorker(
            queue=worker_repository,
            handlers={"confirm_requirement": BlockingRequirementHandler()},
            configuration=OrchestrationWorkerConfiguration(
                executor_kind="agent",
                executor_id="worker-3",
                capabilities=("requirement_review",),
                worker_token=str(worker_registration["worker_token"]),
                project_id=project_id,
                heartbeat_interval_seconds=0.01,
                idle_poll_seconds=0.01,
            ),
        )
        worker_result = worker.run_once()

        assert worker_result.status == "submitted"
        assert worker_result.task_id == expiring_task_id
        assert worker_result.outcome == "blocked"
        assert worker_result.recovered_expired_lease is True
        worker_view = task_repository.view(expiring_task_id)
        assert worker_view["state"] == "blocked"
        assert worker_view["claims"][-1]["executor_id"] == "worker-3"
        assert worker_view["results"][-1]["evidence"] == {
            "blocking_reason": "human confirmation is still required"
        }
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO orchestration_task_dependencies (
                    orchestration_task_id, depends_on_task_id, project_id
                ) VALUES (%s, %s, %s)
                """,
                (expiring_task_id, task["orchestration_task_id"], project_id),
            )
        graph = task_repository.dependency_graph(
            project_id=project_id,
            automation_run_id=None,
            limit=50,
        )
        graph_task = next(
            value for value in graph["tasks"] if value["orchestration_task_id"] == expiring_task_id
        )
        assert graph_task["dependencies"] == [task["orchestration_task_id"]]
        assert graph_task["blocking_reason"] == "human confirmation is still required"
        assert graph["truncated"] is False


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_registered_capabilities_schedule_multiple_workers_and_report_runtime_metrics() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"worker-project-{suffix}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES (%s, 'Worker registry test')",
                (project_id,),
            )
        service = WebControlPlaneService(connection=connection, repository_root=ROOT)
        first_task = _create_confirmation_task(
            service=service,
            project_id=project_id,
            request_id=f"worker-request-a-{suffix}",
        )
        second_task = _create_confirmation_task(
            service=service,
            project_id=project_id,
            request_id=f"worker-request-b-{suffix}",
        )
        repository = OrchestrationTaskRepository(connection)
        first_worker = repository.register_worker(
            executor_kind="agent",
            executor_id=f"worker-a-{suffix}",
            capabilities=("requirement_review",),
            project_id=project_id,
            max_concurrent_tasks=1,
            lease_seconds=30,
        )
        second_worker = repository.register_worker(
            executor_kind="subagent",
            executor_id=f"worker-b-{suffix}",
            capabilities=("requirement_review",),
            project_id=project_id,
            max_concurrent_tasks=1,
            lease_seconds=30,
        )
        assert first_worker["live"] is True
        assert second_worker["live"] is True
        assert "credential_digest" not in first_worker
        with pytest.raises(ValueError, match="registered Worker credential is required"):
            repository.claim(
                task_id=first_task["orchestration_task_id"],
                executor_kind="agent",
                executor_id=f"unregistered-{suffix}",
                capabilities=("requirement_review",),
                project_id=project_id,
            )
        with pytest.raises(ValueError, match="credential is invalid"):
            repository.claim(
                task_id=first_task["orchestration_task_id"],
                executor_kind="agent",
                executor_id=first_worker["executor_id"],
                capabilities=("requirement_review",),
                project_id=project_id,
                worker_token="spoofed-worker-token",
            )
        prioritized = repository.update_priority(
            task_id=second_task["orchestration_task_id"],
            priority=900,
            actor="operator-1",
        )
        assert prioritized["priority"] == 900
        assert prioritized["events"][-1]["event_type"] == "priority_updated"
        ready_order = repository.list_ready(
            executor_kind="agent",
            capabilities=("requirement_review",),
            project_id=project_id,
        )
        assert ready_order[0]["orchestration_task_id"] == second_task["orchestration_task_id"]

        first_claim = repository.claim(
            task_id=first_task["orchestration_task_id"],
            executor_kind="agent",
            executor_id=first_worker["executor_id"],
            capabilities=("requirement_review",),
            project_id=project_id,
            worker_token=str(first_worker["worker_token"]),
        )
        with pytest.raises(ValueError, match="concurrency limit"):
            repository.claim(
                task_id=second_task["orchestration_task_id"],
                executor_kind="agent",
                executor_id=first_worker["executor_id"],
                capabilities=("requirement_review",),
                project_id=project_id,
                worker_token=str(first_worker["worker_token"]),
            )
        with pytest.raises(ValueError, match="differ from Worker registration"):
            repository.claim(
                task_id=second_task["orchestration_task_id"],
                executor_kind="subagent",
                executor_id=second_worker["executor_id"],
                capabilities=("impact_review",),
                project_id=project_id,
                worker_token=str(second_worker["worker_token"]),
            )
        second_claim = repository.claim(
            task_id=second_task["orchestration_task_id"],
            executor_kind="subagent",
            executor_id=second_worker["executor_id"],
            capabilities=("requirement_review",),
            project_id=project_id,
            worker_token=str(second_worker["worker_token"]),
        )

        draining = repository.set_worker_status(
            executor_kind="agent",
            executor_id=str(first_worker["executor_id"]),
            status="draining",
            actor="operator-1",
        )
        assert draining["status"] == "draining"
        assert draining["present"] is True
        assert draining["live"] is False
        heartbeat = repository.heartbeat_worker(
            executor_kind="agent",
            executor_id=str(first_worker["executor_id"]),
            worker_token=str(first_worker["worker_token"]),
            lease_seconds=30,
        )
        assert heartbeat["status"] == "draining"
        repository.release(
            task_id=first_task["orchestration_task_id"],
            executor_id=first_worker["executor_id"],
            lease_token=first_claim["lease_token"],
            reason="registry test complete",
        )
        with pytest.raises(ValueError, match="not accepting new Tasks"):
            repository.claim(
                task_id=first_task["orchestration_task_id"],
                executor_kind="agent",
                executor_id=first_worker["executor_id"],
                capabilities=("requirement_review",),
                project_id=project_id,
                worker_token=str(first_worker["worker_token"]),
            )
        configured = repository.update_worker_configuration(
            executor_kind="agent",
            executor_id=str(first_worker["executor_id"]),
            capabilities=("requirement_review", "impact_review"),
            max_concurrent_tasks=2,
            actor="operator-1",
        )
        assert configured["capabilities"] == ["requirement_review", "impact_review"]
        assert configured["max_concurrent_tasks"] == 2
        enabled = repository.set_worker_status(
            executor_kind="agent",
            executor_id=str(first_worker["executor_id"]),
            status="online",
            actor="operator-1",
        )
        assert enabled["live"] is True
        resumed_claim = repository.claim(
            task_id=first_task["orchestration_task_id"],
            executor_kind="agent",
            executor_id=first_worker["executor_id"],
            capabilities=("requirement_review", "impact_review"),
            project_id=project_id,
            worker_token=str(first_worker["worker_token"]),
        )
        repository.release(
            task_id=first_task["orchestration_task_id"],
            executor_id=first_worker["executor_id"],
            lease_token=resumed_claim["lease_token"],
            reason="worker operation test complete",
        )
        disabled = repository.set_worker_status(
            executor_kind="agent",
            executor_id=str(first_worker["executor_id"]),
            status="offline",
            actor="operator-1",
        )
        assert disabled["live"] is False
        assert (
            repository.heartbeat_worker(
                executor_kind="agent",
                executor_id=str(first_worker["executor_id"]),
                worker_token=str(first_worker["worker_token"]),
                lease_seconds=30,
            )["status"]
            == "offline"
        )
        with pytest.raises(ValueError, match="not accepting new Tasks"):
            repository.claim(
                task_id=first_task["orchestration_task_id"],
                executor_kind="agent",
                executor_id=first_worker["executor_id"],
                capabilities=("requirement_review", "impact_review"),
                project_id=project_id,
                worker_token=str(first_worker["worker_token"]),
            )
        repository.record_result(
            task_id=second_task["orchestration_task_id"],
            executor_id=second_worker["executor_id"],
            lease_token=second_claim["lease_token"],
            outcome="blocked",
            summary="業務担当者の確認待ち",
            artifact_refs=(),
            evidence={"blocking_reason": "business owner unavailable"},
        )
        monitoring = repository.runtime_monitoring(project_id=project_id, window_hours=24)

        assert monitoring["task_count"] == 2
        assert monitoring["claim_count"] == 3
        assert monitoring["retry_count"] == 1
        assert monitoring["retried_task_count"] == 1
        alert_monitoring = repository.runtime_monitoring(
            project_id=project_id,
            window_hours=24,
            backlog_alert_threshold=1,
        )
        assert alert_monitoring["oldest_ready_wait_seconds"] is not None
        assert any(alert["alert_type"] == "queue_backlog" for alert in alert_monitoring["alerts"])
        assert monitoring["result_count"] == 1
        assert monitoring["success_rate"] == 0.0
        assert monitoring["blocker_reasons"][0]["reason"] == "business owner unavailable"
        assert {worker["executor_id"] for worker in monitoring["workers"]} == {
            first_worker["executor_id"],
            second_worker["executor_id"],
        }
        first_worker_view = next(
            worker
            for worker in monitoring["workers"]
            if worker["executor_id"] == first_worker["executor_id"]
        )
        assert "credential_digest" not in first_worker_view
        assert {
            "registered",
            "drain_requested",
            "configuration_updated",
            "enabled",
            "disabled",
        }.issubset({event["event_type"] for event in first_worker_view["events"]})
        assert (
            repository.unregister_worker(
                executor_kind="agent",
                executor_id=first_worker["executor_id"],
                worker_token=str(first_worker["worker_token"]),
            )["live"]
            is False
        )


def _create_confirmation_task(
    *, service: WebControlPlaneService, project_id: str, request_id: str
) -> dict[str, object]:
    service.submit_change_request(
        ChangeRequestInput(
            change_request_id=request_id,
            project_id=project_id,
            analysis_case_id=None,
            input_mode="natural_language",
            requirement_text="登録済み Worker の並行実行を確認する",
            source_document_ref=None,
            target_document_ref=None,
            business_rules=(BusinessRuleInput(f"rule-{request_id}", "確認が必要", ()),),
            ambiguity_status="needs_confirmation",
            ambiguities=("確認境界",),
            submitted_by="owner",
        )
    )
    run = service.start_change_automation(
        request_id=request_id,
        idempotency_key=f"start-{request_id}",
        actor="owner",
    )["run"]
    task = run["current_task"]
    assert isinstance(task, dict)
    return task


class BlockingRequirementHandler:
    def execute(
        self,
        *,
        task: dict[str, object],
        context: OrchestrationTaskExecutionContext,
    ) -> OrchestrationTaskExecutionResult:
        context.raise_if_cancelled()
        return OrchestrationTaskExecutionResult(
            outcome="blocked",
            summary="Automatic worker cannot replace human confirmation.",
            artifact_refs=(),
            evidence={"blocking_reason": "human confirmation is still required"},
        )
