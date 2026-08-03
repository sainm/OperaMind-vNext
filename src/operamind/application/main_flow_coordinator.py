"""Internal coordinator that advances the six-stage flow without Web commands."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import cast

import psycopg

from operamind.application.main_flow_execution import (
    TestDataExecutorFactory,
    execute_reserved_test_data_run,
)
from operamind.application.orchestration_task import OrchestrationSchedulingPolicy
from operamind.application.web_control_plane import WebControlPlaneService

LOGGER = logging.getLogger(__name__)
_AUTOMATION_ACTOR = "automation:operamind"
_AUTOMATION_IDEMPOTENCY_KEY = "automatic-main-flow"


@dataclass(frozen=True, slots=True)
class MainFlowCoordinatorIteration:
    observed_requests: int
    reserved_runs: int
    executed_runs: int
    failed_requests: int


@dataclass(frozen=True, slots=True)
class MainFlowCoordinator:
    """Poll Canonical state and execute only deterministic internal transitions."""

    database_url: str
    repository_root: Path
    executor_factory: TestDataExecutorFactory
    scheduling_policy: OrchestrationSchedulingPolicy

    def run_once(self) -> MainFlowCoordinatorIteration:
        scheduled: list[tuple[str, str]] = []
        observed = 0
        reserved = 0
        failures = 0
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            service = WebControlPlaneService(
                connection=connection,
                repository_root=self.repository_root,
                orchestration_scheduling_policy=self.scheduling_policy,
            )
            projects = cast(list[dict[str, object]], service.list_projects()["projects"])
            for project in projects:
                project_id = project.get("project_id")
                if not isinstance(project_id, str):
                    continue
                requests = cast(
                    list[dict[str, object]],
                    service.list_change_requests(project_id=project_id)["change_requests"],
                )
                for request in requests:
                    request_id = request.get("change_request_id")
                    if not isinstance(request_id, str):
                        continue
                    observed += 1
                    try:
                        automation = service.resume_pending_change_automation(
                            request_id=request_id,
                            actor=_AUTOMATION_ACTOR,
                        )
                        if _test_data_execution_is_ready(automation):
                            result = service.start_test_data_run(
                                request_id=request_id,
                                idempotency_key=_AUTOMATION_IDEMPOTENCY_KEY,
                                actor=_AUTOMATION_ACTOR,
                            )
                            if result.get("background_required") is True:
                                reserved += 1
                                scheduled.append((request_id, str(result["run_id"])))
                        elif _ui_publication_is_ready(automation):
                            management = service.execution_management(request_id)
                            execution = management.get("test_data_execution")
                            if (
                                isinstance(execution, dict)
                                and execution.get("status") != "running"
                                and management.get("change_closure") is None
                            ):
                                scheduled.append((request_id, str(execution["run_id"])))
                    except (ValueError, psycopg.Error):
                        failures += 1
                        LOGGER.exception(
                            "Internal main-flow transition failed for request %s",
                            request_id,
                        )

        executed = 0
        for request_id, run_id in scheduled:
            execute_reserved_test_data_run(
                database_url=self.database_url,
                repository_root=self.repository_root,
                run_id=run_id,
                executor_factory=self.executor_factory,
            )
            executed += 1
            try:
                with psycopg.connect(self.database_url, autocommit=True) as connection:
                    WebControlPlaneService(
                        connection=connection,
                        repository_root=self.repository_root,
                        orchestration_scheduling_policy=self.scheduling_policy,
                    ).resume_pending_change_automation(
                        request_id=request_id,
                        actor=_AUTOMATION_ACTOR,
                    )
            except (ValueError, psycopg.Error):
                failures += 1
                LOGGER.exception(
                    "Internal main-flow completion refresh failed for request %s",
                    request_id,
                )
        return MainFlowCoordinatorIteration(
            observed_requests=observed,
            reserved_runs=reserved,
            executed_runs=executed,
            failed_requests=failures,
        )

    def run_forever(self, *, stop_event: Event, poll_seconds: float) -> None:
        if not 0.1 <= poll_seconds <= 60:
            raise ValueError("main-flow poll_seconds must be between 0.1 and 60")
        while not stop_event.is_set():
            try:
                self.run_once()
            except (OSError, RuntimeError, psycopg.Error, ValueError):
                LOGGER.exception("Internal main-flow coordinator iteration failed")
            stop_event.wait(poll_seconds)


def _test_data_execution_is_ready(automation: dict[str, object] | None) -> bool:
    if automation is not None and isinstance(automation.get("run"), dict):
        automation = cast(dict[str, object], automation["run"])
    return (
        automation is not None
        and automation.get("status") == "waiting"
        and automation.get("next_action") == "start_test_data_execution"
    )


def _ui_publication_is_ready(automation: dict[str, object] | None) -> bool:
    if automation is not None and isinstance(automation.get("run"), dict):
        automation = cast(dict[str, object], automation["run"])
    return (
        automation is not None
        and automation.get("status") == "waiting"
        and automation.get("next_action") == "run_ui_verification"
    )
