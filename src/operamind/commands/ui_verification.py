"""CLI for approved UI Scenarios, execution gates, Runs, and closure Results."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psycopg

from operamind.application import (
    BrowserExecutionRequest,
    BrowserExecutionService,
    BrowserPreflightRequest,
    BrowserPreflightService,
    UiKnowledgeProposalRequest,
    UiKnowledgeProposalService,
    UiKnowledgeReviewRequest,
    UiKnowledgeReviewService,
    UiRunRecovery,
    UiRuntimeObservationRequest,
    UiRuntimeObservationService,
    UiVerificationService,
)
from operamind.contracts import ContractCatalog
from operamind.domain import BrowserExecutionManifest, UiKnowledgeSnapshot
from operamind.infrastructure.browser import (
    LocalEvidenceStore,
    PlaywrightBrowserExecutor,
    PlaywrightBrowserPreflightProbe,
    PlaywrightUiKnowledgeRuntimeObserver,
)
from operamind.infrastructure.postgres import (
    UiDeploymentWrite,
    UiExecutionEvidenceWrite,
    UiExecutionPlanRecord,
    UiExecutionPlanWrite,
    UiPreflightCheckWrite,
    UiScenarioResultWrite,
    VerificationScenarioWrite,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the evidence-bound UI verification flow")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="operation", required=True)

    scenario = subparsers.add_parser("register-scenario")
    scenario.add_argument("--scenario", type=Path, required=True)
    scenario.add_argument("--scenario-version-id", required=True)
    scenario.add_argument("--scenario-version", required=True)
    scenario.add_argument("--project-id", required=True)
    scenario.add_argument("--trigger-path", required=True)
    scenario.add_argument("--data-recipe-ref")
    scenario.add_argument(
        "--review-status", choices=("draft", "approved", "rejected"), required=True
    )
    scenario.add_argument("--activate", action="store_true")

    plan = subparsers.add_parser("build-plan")
    _add_scope_arguments(plan)
    plan.add_argument("--plan-id", required=True)
    plan.add_argument("--edit-packet-id", required=True)
    plan.add_argument("--edit-result-id", required=True)
    plan.add_argument("--environment-id", required=True)
    plan.add_argument("--base-url", required=True)
    plan.add_argument("--deployment-revision", required=True)
    plan.add_argument("--repository-revision", required=True)

    preflight = subparsers.add_parser("record-preflight")
    preflight.add_argument("--project-id", required=True)
    preflight.add_argument("--plan-id", required=True)
    preflight.add_argument("--attempt-id", required=True)
    preflight.add_argument("--checks", type=Path, required=True)

    start = subparsers.add_parser("start-run")
    start.add_argument("--project-id", required=True)
    start.add_argument("--plan-id", required=True)
    start.add_argument("--run-id", required=True)
    start.add_argument("--approval-grant-id", required=True)

    complete = subparsers.add_parser("complete-run")
    complete.add_argument("--verification-result-id", required=True)
    complete.add_argument("--project-id", required=True)
    complete.add_argument("--run-id", required=True)
    complete.add_argument("--scenario-results", type=Path, required=True)
    complete.add_argument("--evidence", type=Path, required=True)
    complete.add_argument("--out-of-scope-file", action="append", default=[])

    recover = subparsers.add_parser("recover-run")
    recover.add_argument("--verification-result-id", required=True)
    recover.add_argument("--project-id", required=True)
    recover.add_argument("--run-id", required=True)
    recover.add_argument("--recovery-id", required=True)
    recover.add_argument("--actor", required=True)
    recover.add_argument("--reason", required=True)
    recover.add_argument("--stale-before", type=_timestamp, required=True)

    manifest = subparsers.add_parser("register-browser-manifest")
    manifest.add_argument("--manifest", type=Path, required=True)

    knowledge = subparsers.add_parser("register-ui-knowledge")
    knowledge.add_argument("--snapshot", type=Path, required=True)

    proposal = subparsers.add_parser("propose-ui-knowledge")
    proposal.add_argument("--project-id", required=True)
    proposal.add_argument("--document-snapshot-id", required=True)
    proposal.add_argument("--environment-id", required=True)
    proposal.add_argument("--deployment-revision", required=True)
    proposal.add_argument("--snapshot-id", required=True)
    proposal.add_argument("--snapshot-version", required=True)

    observation = subparsers.add_parser("observe-ui-knowledge")
    observation.add_argument("--project-id", required=True)
    observation.add_argument("--source-snapshot-id", required=True)
    observation.add_argument("--observation-run-id", required=True)
    observation.add_argument("--result-snapshot-id", required=True)
    observation.add_argument("--result-snapshot-version", required=True)
    observation.add_argument("--storage-state", type=Path)
    observation.add_argument(
        "--browser-name", choices=("chromium", "firefox", "webkit"), default="chromium"
    )
    observation.add_argument("--browser-channel", choices=("chrome", "msedge"), default="msedge")
    observation.add_argument("--headed", action="store_true")
    observation.add_argument("--viewport-width", type=int, default=1280)
    observation.add_argument("--viewport-height", type=int, default=720)
    observation.add_argument("--timeout-ms", type=int, default=5_000)
    observation.add_argument("--navigation-timeout-ms", type=int, default=10_000)

    review = subparsers.add_parser("review-ui-knowledge")
    review.add_argument("--project-id", required=True)
    review.add_argument("--source-snapshot-id", required=True)
    review.add_argument("--review-event-id", required=True)
    review.add_argument("--result-snapshot-id", required=True)
    review.add_argument("--result-snapshot-version", required=True)
    review.add_argument("--decision", choices=("approved", "rejected"), required=True)
    review.add_argument("--reviewed-by", required=True)
    review.add_argument("--reason")
    review.add_argument("--activate", action="store_true")

    browser_preflight = subparsers.add_parser("preflight-browser")
    browser_preflight.add_argument("--project-id", required=True)
    browser_preflight.add_argument("--plan-id", required=True)
    browser_preflight.add_argument("--manifest-id", required=True)
    browser_preflight.add_argument("--attempt-id", required=True)
    browser_preflight.add_argument("--storage-state", type=Path)
    browser_preflight.add_argument("--timeout-ms", type=int, default=5_000)
    browser_preflight.add_argument("--navigation-timeout-ms", type=int, default=10_000)

    execute = subparsers.add_parser("execute-browser")
    execute.add_argument("--project-id", required=True)
    execute.add_argument("--plan-id", required=True)
    execute.add_argument("--manifest-id", required=True)
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--verification-result-id", required=True)
    execute.add_argument("--approval-grant-id", required=True)
    execute.add_argument("--evidence-root", type=Path, required=True)
    execute.add_argument("--storage-state", type=Path)
    execute.add_argument("--timeout-ms", type=int, default=10_000)
    execute.add_argument("--navigation-timeout-ms", type=int, default=20_000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        contracts = ContractCatalog.load(root / "contracts")
        with psycopg.connect(database_url) as connection:
            if args.operation in {
                "register-browser-manifest",
                "register-ui-knowledge",
                "propose-ui-knowledge",
                "observe-ui-knowledge",
                "review-ui-knowledge",
                "preflight-browser",
                "execute-browser",
            }:
                output, successful = _dispatch_browser(
                    connection=connection,
                    contracts=contracts,
                    args=args,
                    root=root,
                )
            else:
                service = UiVerificationService(connection=connection, contracts=contracts)
                output, successful = _dispatch(service, args, root)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if successful else 1
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        psycopg.Error,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _dispatch(
    service: UiVerificationService,
    args: argparse.Namespace,
    root: Path,
) -> tuple[dict[str, Any], bool]:
    if args.operation == "register-scenario":
        raw = _load_object(root, args.scenario)
        scenario_id = _string(raw, "scenario_id")
        created = service.register_scenario(
            VerificationScenarioWrite(
                scenario_version_id=args.scenario_version_id,
                project_id=args.project_id,
                scenario_id=scenario_id,
                scenario_version=args.scenario_version,
                title=_string(raw, "title"),
                preconditions=_strings(raw, "preconditions", allow_empty=True),
                steps=_strings(raw, "steps"),
                expected_visible_results=_strings(raw, "expected_visible_results"),
                evidence_requirements=_strings(
                    raw,
                    "evidence_requirements" if "evidence_requirements" in raw else "evidence",
                ),
                trigger_path=args.trigger_path,
                data_recipe_ref=args.data_recipe_ref,
                review_status=args.review_status,
                activate=args.activate,
                test_case_refs=(
                    _strings(raw, "test_case_refs") if "test_case_refs" in raw else (scenario_id,)
                ),
            )
        )
        return {"scenario_version_id": args.scenario_version_id, "created": created}, True
    if args.operation == "build-plan":
        plan_result = service.build_plan(
            deployment=UiDeploymentWrite(
                project_id=args.project_id,
                environment_id=args.environment_id,
                base_url=args.base_url,
                deployment_revision=args.deployment_revision,
                repository_revision=args.repository_revision,
            ),
            plan=UiExecutionPlanWrite(
                plan_id=args.plan_id,
                project_id=args.project_id,
                analysis_case_id=args.analysis_case_id,
                edit_packet_id=args.edit_packet_id,
                edit_result_id=args.edit_result_id,
                environment_id=args.environment_id,
                deployment_revision=args.deployment_revision,
            ),
        )
        return _plan_payload(plan_result), True
    if args.operation == "record-preflight":
        preflight_result = service.record_preflight(
            project_id=args.project_id,
            plan_id=args.plan_id,
            attempt_id=args.attempt_id,
            checks=_preflight_checks(_load_array(root, args.checks)),
        )
        return _plan_payload(preflight_result), preflight_result.status == "ready"
    if args.operation == "start-run":
        run_result = service.start_run(
            project_id=args.project_id,
            plan_id=args.plan_id,
            run_id=args.run_id,
            approval_grant_id=args.approval_grant_id,
        )
        return {
            "run_id": run_result.run_id,
            "created": run_result.created,
            "status": run_result.status,
        }, run_result.status == "running"
    if args.operation == "complete-run":
        completion = service.complete_run(
            verification_result_id=args.verification_result_id,
            project_id=args.project_id,
            run_id=args.run_id,
            scenario_results=_scenario_results(_load_array(root, args.scenario_results)),
            evidence=_evidence(_load_array(root, args.evidence)),
            out_of_scope_files=tuple(args.out_of_scope_file),
        )
        return completion.artifact, completion.record.status == "passed"
    if args.operation == "recover-run":
        recovery = service.recover_run(
            verification_result_id=args.verification_result_id,
            project_id=args.project_id,
            run_id=args.run_id,
            recovery=UiRunRecovery(
                recovery_id=args.recovery_id,
                actor=args.actor,
                reason=args.reason,
                stale_before=args.stale_before,
            ),
        )
        return recovery.artifact, True
    raise ValueError(f"Unsupported UI operation: {args.operation}")


def _dispatch_browser(
    *,
    connection: psycopg.Connection[Any],
    contracts: ContractCatalog,
    args: argparse.Namespace,
    root: Path,
) -> tuple[dict[str, Any], bool]:
    if args.operation == "register-browser-manifest":
        service = BrowserExecutionService(connection=connection, contracts=contracts)
        manifest = BrowserExecutionManifest.from_dict(_load_object(root, args.manifest))
        registration = service.register_manifest(manifest)
        return {
            "manifest_id": registration.manifest_id,
            "created": registration.created,
            "review_status": registration.review_status,
        }, True
    if args.operation == "register-ui-knowledge":
        probe = PlaywrightBrowserPreflightProbe()
        preflight_service = BrowserPreflightService(connection=connection, probe=probe)
        snapshot = UiKnowledgeSnapshot.from_dict(_load_object(root, args.snapshot))
        knowledge_registration = preflight_service.register_knowledge(snapshot)
        return {
            "snapshot_id": knowledge_registration.snapshot_id,
            "created": knowledge_registration.created,
            "review_status": knowledge_registration.review_status,
            "active": knowledge_registration.active,
        }, True
    if args.operation == "propose-ui-knowledge":
        proposal_service = UiKnowledgeProposalService(
            connection=connection,
            contracts=contracts,
        )
        proposal = proposal_service.propose(
            UiKnowledgeProposalRequest(
                project_id=args.project_id,
                document_snapshot_id=args.document_snapshot_id,
                environment_id=args.environment_id,
                deployment_revision=args.deployment_revision,
                snapshot_id=args.snapshot_id,
                snapshot_version=args.snapshot_version,
            )
        )
        payload: dict[str, Any] = {
            "snapshot": proposal.snapshot.to_dict() if proposal.snapshot is not None else None,
            "issues": [item.to_dict() for item in proposal.issues],
            "ready_for_review": proposal.snapshot is not None,
        }
        return payload, proposal.snapshot is not None
    if args.operation == "observe-ui-knowledge":
        storage_state = _resolve(root, args.storage_state) if args.storage_state else None
        observer = PlaywrightUiKnowledgeRuntimeObserver(
            browser_name=args.browser_name,
            browser_channel=args.browser_channel,
            headless=not args.headed,
            viewport_width=args.viewport_width,
            viewport_height=args.viewport_height,
            timeout_ms=args.timeout_ms,
            navigation_timeout_ms=args.navigation_timeout_ms,
            evidence_store=LocalEvidenceStore(root / "readiness" / "evidence" / "test-data"),
        )
        observation_service = UiRuntimeObservationService(
            connection=connection,
            observer=observer,
        )
        observed = observation_service.observe(
            UiRuntimeObservationRequest(
                project_id=args.project_id,
                source_snapshot_id=args.source_snapshot_id,
                observation_run_id=args.observation_run_id,
                result_snapshot_id=args.result_snapshot_id,
                result_snapshot_version=args.result_snapshot_version,
                storage_state=storage_state,
            )
        )
        observation_result = observed.observation
        return {
            "observation_run_id": observed.record.run_id,
            "created": observed.record.created,
            "status": observation_result.status,
            "snapshot": observation_result.snapshot.to_dict()
            if observation_result.snapshot is not None
            else None,
            "observations": [item.to_dict() for item in observation_result.observations],
            "evidence": [item.to_dict() for item in observation_result.evidence],
            "issues": [item.to_dict() for item in observation_result.issues],
        }, observation_result.status == "completed"
    if args.operation == "review-ui-knowledge":
        review_service = UiKnowledgeReviewService(connection=connection)
        reviewed = review_service.review(
            UiKnowledgeReviewRequest(
                project_id=args.project_id,
                source_snapshot_id=args.source_snapshot_id,
                review_event_id=args.review_event_id,
                result_snapshot_id=args.result_snapshot_id,
                result_snapshot_version=args.result_snapshot_version,
                decision=args.decision,
                reviewed_by=args.reviewed_by,
                reason=args.reason,
                activate=args.activate,
            )
        )
        return {
            "review_event_id": reviewed.record.review_event_id,
            "created": reviewed.record.created,
            "source_snapshot_id": reviewed.record.source_snapshot_id,
            "result_snapshot_id": reviewed.record.result_snapshot_id,
            "decision": reviewed.record.decision,
            "active": reviewed.record.active,
            "snapshot": reviewed.snapshot.to_dict(),
        }, True
    if args.operation == "preflight-browser":
        storage_state = _resolve(root, args.storage_state) if args.storage_state else None
        probe = PlaywrightBrowserPreflightProbe(
            timeout_ms=args.timeout_ms,
            navigation_timeout_ms=args.navigation_timeout_ms,
        )
        preflight_service = BrowserPreflightService(connection=connection, probe=probe)
        result = preflight_service.inspect(
            BrowserPreflightRequest(
                project_id=args.project_id,
                plan_id=args.plan_id,
                manifest_id=args.manifest_id,
                attempt_id=args.attempt_id,
                storage_state=storage_state,
            )
        )
        return _plan_payload(result), result.status == "ready"
    if args.operation == "execute-browser":
        evidence_root = _resolve(root, args.evidence_root)
        storage_state = _resolve(root, args.storage_state) if args.storage_state else None
        executor = PlaywrightBrowserExecutor(
            evidence_store=LocalEvidenceStore(evidence_root),
            timeout_ms=args.timeout_ms,
            navigation_timeout_ms=args.navigation_timeout_ms,
        )
        service = BrowserExecutionService(
            connection=connection,
            contracts=contracts,
            executor=executor,
        )
        execution = service.execute(
            BrowserExecutionRequest(
                project_id=args.project_id,
                plan_id=args.plan_id,
                manifest_id=args.manifest_id,
                run_id=args.run_id,
                verification_result_id=args.verification_result_id,
                approval_grant_id=args.approval_grant_id,
                storage_state=storage_state,
            )
        )
        artifact = execution.verification.artifact
        return artifact, execution.verification.record.status == "passed"
    raise ValueError(f"Unsupported Browser operation: {args.operation}")


def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)


def _plan_payload(result: UiExecutionPlanRecord) -> dict[str, Any]:
    return {
        "plan_id": result.plan_id,
        "created": result.created,
        "status": result.status,
        "scenario_refs": list(result.scenario_refs),
        "blocking_reasons": list(result.blocking_reasons),
    }


def _preflight_checks(values: list[object]) -> tuple[UiPreflightCheckWrite, ...]:
    return tuple(
        UiPreflightCheckWrite(
            check_id=_string(item, "check_id"),
            check_type=_string(item, "check_type"),
            status=_string(item, "status"),
            evidence_ref=_optional_string(item, "evidence_ref"),
            reason=_optional_string(item, "reason"),
        )
        for item in _objects(values)
    )


def _scenario_results(values: list[object]) -> tuple[UiScenarioResultWrite, ...]:
    return tuple(
        UiScenarioResultWrite(
            scenario_id=_string(item, "scenario_id"),
            status=_string(item, "status"),
            impact_item_refs=_strings(item, "impact_item_refs", allow_empty=True),
            evidence_refs=_strings(item, "evidence_refs", allow_empty=True),
            failure_category=_string(item, "failure_category"),
            summary=_optional_string(item, "summary"),
        )
        for item in _objects(values)
    )


def _evidence(values: list[object]) -> tuple[UiExecutionEvidenceWrite, ...]:
    return tuple(
        UiExecutionEvidenceWrite(
            evidence_id=_string(item, "evidence_id"),
            scenario_id=_string(item, "scenario_id"),
            evidence_type=_string(item, "evidence_type"),
            evidence_ref=_string(item, "evidence_ref"),
            content_digest=_string(item, "content_digest"),
            sanitized=_boolean(item, "sanitized"),
        )
        for item in _objects(values)
    )


def _load_object(root: Path, path: Path) -> dict[str, object]:
    raw = _load_json(root, path)
    if not isinstance(raw, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return cast(dict[str, object], raw)


def _load_array(root: Path, path: Path) -> list[object]:
    raw = _load_json(root, path)
    if not isinstance(raw, list):
        raise ValueError(f"JSON input must be an array: {path}")
    return cast(list[object], raw)


def _load_json(root: Path, path: Path) -> object:
    resolved = _resolve(root, path)
    return json.loads(resolved.read_text(encoding="utf-8"))


def _resolve(root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _objects(values: list[object]) -> tuple[dict[str, object], ...]:
    if not all(isinstance(value, dict) for value in values):
        raise ValueError("JSON array entries must be objects")
    return tuple(cast(dict[str, object], value) for value in values)


def _string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return item


def _optional_string(value: dict[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be null or a non-blank string")
    return item


def _strings(
    value: dict[str, object],
    key: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    item = value.get(key)
    if not isinstance(item, list) or (not allow_empty and not item):
        raise ValueError(f"{key} must be a JSON string array")
    if not all(isinstance(entry, str) and entry.strip() for entry in item):
        raise ValueError(f"{key} entries must be non-blank strings")
    return tuple(cast(list[str], item))


def _boolean(value: dict[str, object], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ValueError(f"{key} must be a boolean")
    return item


def _timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from error
    if result.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return result.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
