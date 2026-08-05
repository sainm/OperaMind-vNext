"""Background execution of internally reserved TestDataPlan Runs."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread, get_ident
from typing import Literal
from uuid import uuid4

import psycopg

from operamind.application.change_closure_service import ChangeClosureService
from operamind.application.copilot_coding_task import (
    CopilotCodingTaskPublishRequest,
    CopilotCodingTaskService,
)
from operamind.application.data_identity import (
    DataIdentityProvider,
    is_sensitive_data_identity_name,
)
from operamind.application.test_case_revision_service import TestCaseRevisionService
from operamind.application.test_data_execution import (
    TestDataChannelExecutor,
    TestDataExecutionProgress,
)
from operamind.application.test_data_execution_service import (
    TestDataExecutionRecoveryRequest,
    TestDataExecutionService,
    TestDataExecutionServiceRequest,
)
from operamind.application.test_data_ui_verification import (
    TestDataUiVerificationService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.postgres.change_orchestration_repository import (
    ChangeOrchestrationRepository,
)
from operamind.infrastructure.postgres.copilot_coding_task_repository import (
    CopilotCodingTaskRepository,
)
from operamind.infrastructure.postgres.existing_test_data_repository import (
    ExistingTestDataRepository,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionEventWrite,
    TestDataExecutionRecord,
    TestDataExecutionRepository,
)
from operamind.infrastructure.postgres.web_control_plane_repository import (
    WebControlPlaneRepository,
)
from operamind.infrastructure.test_data import (
    PlaywrightUiTestDataExecutor,
    SafeHttpTestDataExecutor,
    configured_data_identity_providers,
    default_data_identity_providers,
)
from operamind.infrastructure.test_data.target_data import ProjectSqlTestDataExecutor

LOGGER = logging.getLogger(__name__)
TestDataExecutorFactory = Callable[[Path], Mapping[str, TestDataChannelExecutor]]
MainFlowExecutionOutcome = Literal["executed", "published", "recovered", "busy", "failed"]
_EXECUTION_LEASE_SECONDS = 300
_EXECUTION_HEARTBEAT_SECONDS = 60


def default_test_data_executor_factory(
    repository_root: Path,
    *,
    control_database_url: str | None = None,
) -> Mapping[str, TestDataChannelExecutor]:
    """Expose restricted adapters; SQL resolves only reviewed Project bindings."""
    store = LocalEvidenceStore(repository_root / "readiness" / "evidence" / "test-data")
    executors: dict[str, TestDataChannelExecutor] = {
        "http": SafeHttpTestDataExecutor(evidence_store=store),
        "ui": PlaywrightUiTestDataExecutor(evidence_store=store),
    }
    if control_database_url is not None:
        executors["sql"] = ProjectSqlTestDataExecutor(
            control_database_url=control_database_url,
            evidence_store=store,
        )
    return executors


def execute_reserved_test_data_run(
    *,
    database_url: str,
    repository_root: Path,
    run_id: str,
    executor_factory: TestDataExecutorFactory = default_test_data_executor_factory,
) -> MainFlowExecutionOutcome:
    """Execute one persisted reservation and evaluate Closure after completion."""
    executor_id = f"main-flow:{uuid4().hex}:{get_ident()}"
    stop_heartbeat = Event()
    lease_lost = Event()
    heartbeat_thread: Thread | None = None
    try:
        execution_artifact: dict[str, object]
        with psycopg.connect(database_url) as connection:
            contracts = ContractCatalog.load(repository_root / "contracts")
            repository = TestDataExecutionRepository(connection, contracts)
            claim = repository.claim_execution(
                run_id=run_id,
                executor_id=executor_id,
                at=datetime.now(UTC),
                lease_seconds=_EXECUTION_LEASE_SECONDS,
            )
            record = claim.record
            if claim.outcome == "busy":
                return "busy"

            def progress(value: TestDataExecutionProgress) -> None:
                if lease_lost.is_set():
                    raise RuntimeError("TestDataPlan execution lease was lost")
                repository.append_event(
                    TestDataExecutionEventWrite(
                        run_id=record.run_id,
                        project_id=record.project_id,
                        event_type=value.event_type,
                        flow_id=value.flow_id,
                        phase=value.phase,
                        step_id=value.step_id,
                        status=value.status,
                        message=value.message,
                    )
                )

            executors: Mapping[str, TestDataChannelExecutor]
            if claim.outcome != "claimed":
                executors = {}
            elif executor_factory is default_test_data_executor_factory:
                executors = default_test_data_executor_factory(
                    repository_root,
                    control_database_url=database_url,
                )
            else:
                executors = executor_factory(repository_root)
            service = TestDataExecutionService(
                connection=connection,
                contracts=contracts,
                executors=executors,
                identity_providers=_identity_providers_for_project(
                    connection,
                    record.project_id,
                ),
                progress_sink=progress,
            )
            if claim.outcome in {"stale", "exhausted"}:
                stale_before = record.lease_expires_at or datetime.now(UTC)
                execution = service.recover(
                    TestDataExecutionRecoveryRequest(
                        recovery_id=_recovery_id(record.run_id, stale_before),
                        run_id=record.run_id,
                        project_id=record.project_id,
                        actor="main-flow-worker",
                        reason="Persisted execution lease expired before completion",
                        stale_before=stale_before,
                    )
                )
                outcome: MainFlowExecutionOutcome = "recovered"
            else:
                if claim.outcome == "claimed":
                    heartbeat_thread = Thread(
                        target=_heartbeat_execution_lease,
                        kwargs={
                            "database_url": database_url,
                            "repository_root": repository_root,
                            "run_id": run_id,
                            "executor_id": executor_id,
                            "stop_event": stop_heartbeat,
                            "lease_lost_event": lease_lost,
                        },
                        name=f"operamind-test-data-heartbeat-{run_id[:16]}",
                        daemon=True,
                    )
                    heartbeat_thread.start()
                execution = service.execute_reserved(
                    TestDataExecutionServiceRequest(
                        execution_result_id=record.execution_result_id,
                        run_id=record.run_id,
                        orchestration_id=record.orchestration_id,
                        test_data_plan_id=record.test_data_plan_id,
                        approval_grant_id=record.approval_grant_id,
                        project_id=record.project_id,
                        actor="main-flow-worker",
                        base_url=WebControlPlaneRepository(
                            connection, contracts
                        ).project_test_base_url(record.project_id),
                        started_at=record.started_at,
                        replay_of_run_id=record.replay_of_run_id,
                        execution_owner=(executor_id if claim.outcome == "claimed" else None),
                    )
                )
                outcome = "executed" if claim.outcome == "claimed" else "published"
            execution_artifact = execution.artifact
            _publish_locator_failure_feedback(
                connection=connection,
                contracts=contracts,
                repository_root=repository_root,
                record=record,
                artifact=execution_artifact,
            )

        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=5)

        # Commit the immutable execution result before publishing downstream UI
        # verification. A publication or Closure failure must not roll a passed
        # browser run back and replace it with a synthetic not-run result.
        with psycopg.connect(database_url) as connection:
            contracts = ContractCatalog.load(repository_root / "contracts")
            repository = TestDataExecutionRepository(connection, contracts)
            TestDataUiVerificationService(connection, contracts).publish(
                orchestration_id=record.orchestration_id,
                execution_result=execution_artifact,
            )
            closure = ChangeClosureService(connection, contracts).close(
                orchestration_id=record.orchestration_id,
                actor="main-flow-worker",
            )
            repository.append_event(
                TestDataExecutionEventWrite(
                    run_id=record.run_id,
                    project_id=record.project_id,
                    event_type="closure_generated",
                    message=f"ChangeClosureResult: {closure.record.closure_result_id}",
                )
            )
        return outcome
    except Exception as error:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=5)
        LOGGER.exception("Main-flow TestDataPlan background Run failed: %s", run_id)
        _publish_background_failure(
            database_url=database_url,
            repository_root=repository_root,
            run_id=run_id,
            executor_id=executor_id,
            error=error,
        )
        return "failed"


def _heartbeat_execution_lease(
    *,
    database_url: str,
    repository_root: Path,
    run_id: str,
    executor_id: str,
    stop_event: Event,
    lease_lost_event: Event,
) -> None:
    """Renew a live execution Claim until completion or ownership loss."""

    while not stop_event.wait(_EXECUTION_HEARTBEAT_SECONDS):
        try:
            with psycopg.connect(database_url) as connection:
                contracts = ContractCatalog.load(repository_root / "contracts")
                renewed = TestDataExecutionRepository(connection, contracts).heartbeat_execution(
                    run_id=run_id,
                    executor_id=executor_id,
                    at=datetime.now(UTC),
                    lease_seconds=_EXECUTION_LEASE_SECONDS,
                )
            if not renewed:
                LOGGER.error("TestDataPlan execution lease was lost for Run: %s", run_id)
                lease_lost_event.set()
                return
        except (OSError, RuntimeError, ValueError, psycopg.Error):
            LOGGER.exception("Could not renew TestDataPlan execution lease for Run: %s", run_id)
            lease_lost_event.set()
            return


def _recovery_id(run_id: str, stale_before: datetime) -> str:
    stamp = stale_before.astimezone(UTC).isoformat()
    digest = hashlib.sha256(f"{run_id}\0{stamp}".encode()).hexdigest()[:32]
    return f"test-data-recovery-{digest}"


def _publish_background_failure(
    *,
    database_url: str,
    repository_root: Path,
    run_id: str,
    error: Exception,
    executor_id: str | None = None,
) -> None:
    """Make an outer-worker failure visible without persisting exception details."""
    reason = f"Background TestDataPlan worker failed before completion ({type(error).__name__})"
    try:
        with psycopg.connect(database_url) as connection:
            contracts = ContractCatalog.load(repository_root / "contracts")
            repository = TestDataExecutionRepository(connection, contracts)
            record = repository.get_record(run_id)
            if record is None:
                return
            if (
                record.status == "running"
                and executor_id is not None
                and record.execution_owner != executor_id
            ):
                LOGGER.warning(
                    "Ignoring failure from a TestDataPlan executor that no longer owns Run: %s",
                    run_id,
                )
                return
            if record.status != "running":
                downstream_reason = (
                    "Downstream UI Evidence or Change Closure publication failed "
                    f"({type(error).__name__})"
                )
                repository.append_event(
                    TestDataExecutionEventWrite(
                        run_id=record.run_id,
                        project_id=record.project_id,
                        event_type="downstream_publication_failed",
                        status=record.status,
                        message=downstream_reason,
                    )
                )
                return
            service = TestDataExecutionService(
                connection=connection,
                contracts=contracts,
                executors={},
                identity_providers=_identity_providers_for_project(
                    connection,
                    record.project_id,
                ),
            )
            repository.append_event(
                TestDataExecutionEventWrite(
                    run_id=record.run_id,
                    project_id=record.project_id,
                    event_type="background_failed",
                    status="failed",
                    message=reason,
                )
            )
            service.fail_reserved(
                TestDataExecutionServiceRequest(
                    execution_result_id=record.execution_result_id,
                    run_id=record.run_id,
                    orchestration_id=record.orchestration_id,
                    test_data_plan_id=record.test_data_plan_id,
                    approval_grant_id=record.approval_grant_id,
                    project_id=record.project_id,
                    actor="main-flow-worker",
                    started_at=record.started_at,
                    execution_owner=executor_id,
                ),
                reason=reason,
            )
            closure = ChangeClosureService(connection, contracts).close(
                orchestration_id=record.orchestration_id,
                actor="main-flow-worker",
            )
            repository.append_event(
                TestDataExecutionEventWrite(
                    run_id=record.run_id,
                    project_id=record.project_id,
                    event_type="closure_generated",
                    message=f"ChangeClosureResult: {closure.record.closure_result_id}",
                )
            )
    except Exception:
        LOGGER.exception("Could not publish background failure for Run: %s", run_id)


def _publish_locator_failure_feedback(
    *,
    connection: psycopg.Connection,
    contracts: ContractCatalog,
    repository_root: Path,
    record: TestDataExecutionRecord,
    artifact: Mapping[str, object],
) -> None:
    """Feed real Playwright locator blocks into the matching Copilot Task ledger."""

    failures: list[dict[str, object]] = []
    raw_flows = artifact.get("flow_results", [])
    if not isinstance(raw_flows, list):
        return
    for flow in raw_flows:
        if not isinstance(flow, Mapping):
            continue
        flow_id = str(flow.get("flow_id") or "")
        for collection in ("step_results", "cleanup_results"):
            raw_steps = flow.get(collection, [])
            if not isinstance(raw_steps, list):
                continue
            for step in raw_steps:
                if not isinstance(step, Mapping) or step.get("channel") != "ui":
                    continue
                reason = str(step.get("failure_reason") or "")
                failure_stage = str(step.get("failure_stage") or "")
                is_pre_action_block = failure_stage.startswith("pre_action_")
                if step.get("status") != "blocked" or not (
                    is_pre_action_block
                    or any(
                        token in reason.lower()
                        for token in (
                            "locator",
                            "record scope",
                            "dom",
                            "origin",
                            "pre-action",
                        )
                    )
                ):
                    continue
                failures.append(
                    {
                        "flow_id": flow_id,
                        "step_id": str(step.get("step_id") or ""),
                        "phase": str(step.get("phase") or collection),
                        "failure_stage": failure_stage or "pre_action_validation",
                        "failure_reason": reason,
                        "evidence_refs": [
                            str(value)
                            for value in step.get("evidence_refs", [])
                            if isinstance(value, str)
                        ],
                        "test_data_binding_refs": [
                            str(value)
                            for value in step.get("test_data_binding_refs", [])
                            if isinstance(value, str)
                        ],
                        **_public_locator_failure_facts(step),
                    }
                )
    if not failures:
        return
    project_id = record.project_id
    run_id = record.run_id
    orchestration_id = record.orchestration_id
    feedback: dict[str, object] = {
        "failure_stage": "formal_ui_run_pre_action_validation",
        "failures": failures,
        "next_action": "create_new_ui_test_plan_revision_and_require_confirmation",
    }
    source_task_id = CopilotCodingTaskRepository(
        connection, contracts
    ).record_ui_locator_blocked_feedback(
        orchestration_id=orchestration_id,
        project_id=project_id,
        run_id=run_id,
        feedback=feedback,
        actor="main-flow-worker",
    )
    if source_task_id is None:
        return
    try:
        _publish_automatic_locator_revision(
            connection=connection,
            contracts=contracts,
            repository_root=repository_root,
            record=record,
            feedback={
                **feedback,
                "run_id": run_id,
                "orchestration_id": orchestration_id,
                "source_coding_task_id": source_task_id,
            },
        )
    except Exception as error:
        LOGGER.exception("Could not queue automatic Locator revision for Run: %s", run_id)
        TestDataExecutionRepository(connection, contracts).append_event(
            TestDataExecutionEventWrite(
                run_id=run_id,
                project_id=project_id,
                event_type="locator_revision_publish_failed",
                status="blocked",
                message=(
                    "Automatic UI TestPlan revision could not be queued "
                    f"({type(error).__name__})"
                ),
            )
        )


def _publish_automatic_locator_revision(
    *,
    connection: psycopg.Connection,
    contracts: ContractCatalog,
    repository_root: Path,
    record: TestDataExecutionRecord,
    feedback: Mapping[str, object],
) -> None:
    """Queue a new immutable plan revision from real blocked Evidence."""

    orchestration = ChangeOrchestrationRepository(connection, contracts).get(
        record.orchestration_id
    )
    if orchestration is None:
        raise ValueError("Locator failure Orchestration does not exist")
    change_request_id = str(orchestration["change_request_id"])
    instruction = (
        "実ブラウザの Locator 失敗 Evidence に基づき、業務期待値を変更せず、"
        "UI TestPlan と TestDataPlan の Locator、Observation、Scope だけを再生成する。"
    )
    revisions = TestCaseRevisionService(
        connection=connection,
        repository_root=repository_root,
    )
    proposal_result = revisions.propose(
        change_request_id=change_request_id,
        instruction=instruction,
        actor="main-flow-worker",
    )
    proposal = proposal_result["proposal"]
    if not isinstance(proposal, dict) or proposal.get("analysis_status") != "deterministic":
        raise ValueError("Locator failure did not produce a deterministic Plan revision")
    proposal_id = str(proposal["proposal_id"])
    prepared = revisions.prepare_ai_regeneration(
        change_request_id=change_request_id,
        proposal_id=proposal_id,
        selections={},
    )
    workspace_root = WebControlPlaneRepository(
        connection, contracts
    ).project_workspace_root(record.project_id)
    if workspace_root is None:
        raise ValueError("Project has no Workspace for automatic Locator revision")
    context: dict[str, object] = {
        "proposal_id": proposal_id,
        "source_orchestration_id": str(proposal["source_orchestration_id"]),
        "source_test_plan_id": str(proposal["source_test_plan_id"]),
        "instruction": instruction,
        "confirmed_operations_json": _canonical_json(prepared["operations"]),
        "selections_json": _canonical_json(prepared["selections"]),
        "locator_failure_evidence_json": _canonical_json(feedback),
    }
    task_digest = hashlib.sha256(
        f"{record.project_id}\0{record.run_id}\0{proposal_id}".encode()
    ).hexdigest()[:32]
    CopilotCodingTaskService(
        connection=connection,
        repository_root=repository_root,
    ).publish(
        CopilotCodingTaskPublishRequest(
            coding_task_id=f"copilot-ui-locator-revision-{task_digest}",
            change_request_id=change_request_id,
            project_id=record.project_id,
            workspace_root=Path(workspace_root),
            task_summary="実ブラウザ Evidence に基づく UI TestPlan Locator 修正",
            actor="main-flow-worker",
            idempotency_key=f"ui-locator-revision:{record.run_id}",
            task_kind="ui_test_plan_revision",
            initial_stage="ui_test_revision",
            plan_revision_context=context,
        )
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _public_locator_failure_facts(step: Mapping[str, object]) -> dict[str, object]:
    """Retain only bounded, non-secret facts needed to revise one Locator."""

    facts: dict[str, object] = {}
    locator_type = step.get("locator_type")
    if isinstance(locator_type, str) and locator_type.strip():
        facts["locator_type"] = locator_type[:100]
    for key in ("record_scope_match_count", "action_locator_match_count"):
        value = step.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            facts[key] = value
    raw_values = step.get("observed_screen_identity_values")
    if isinstance(raw_values, list):
        public_values: list[dict[str, object]] = []
        for raw in raw_values[:20]:
            if not isinstance(raw, Mapping):
                continue
            name = raw.get("name")
            value = raw.get("value")
            if (
                not isinstance(name, str)
                or not name.strip()
                or is_sensitive_data_identity_name(name)
                or not isinstance(value, str | int | float | bool)
            ):
                continue
            public_values.append({"name": name[:200], "value": value})
        if public_values:
            facts["observed_screen_identity_values"] = public_values
    return facts


def _identity_providers_for_project(
    connection: psycopg.Connection,
    project_id: str,
) -> Mapping[str, DataIdentityProvider]:
    """Resolve reviewed Project refs while retaining legacy generic refs for old Artifacts."""

    profiles = ExistingTestDataRepository(connection).profiles(project_id)
    return {
        **default_data_identity_providers(),
        **configured_data_identity_providers(
            {profile.provider_ref: profile.provider_type for profile in profiles}
        ),
    }
