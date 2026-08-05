"""Derive UI verification from a passed, bounded TestDataPlan execution."""

from __future__ import annotations

import hashlib
from typing import Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.run_context_values import canonical_digest


class TestDataUiVerificationService:
    """Publish UI Evidence only after data generation and UI assertions pass."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)

    def publish(
        self,
        *,
        orchestration_id: str,
        execution_result: dict[str, Any],
    ) -> dict[str, object] | None:
        orchestration = self._required(orchestration_id, "ChangeOrchestrationPlan")
        refs = cast(dict[str, str], orchestration["artifact_refs"])
        test_plan = self._required(refs["test_plan_id"], "TestPlan")
        test_data_plan = self._required(refs["test_data_plan_id"], "TestDataPlan")
        if execution_result.get("test_data_plan_id") != test_data_plan["test_data_plan_id"]:
            raise ValueError("Test data UI verification plan identity differs")
        ui_cases = [
            case
            for case in cast(list[dict[str, Any]], test_plan["test_cases"])
            if case.get("level") == "ui"
        ]
        if not ui_cases or execution_result.get("status") != "passed":
            return None
        data_coverage = cast(dict[str, object], execution_result.get("data_coverage") or {})
        if ui_cases and (
            data_coverage.get("status") != "passed"
            or data_coverage.get("coverage_percent") != 100
        ):
            raise ValueError(
                "Executable Test Data Coverage must be 100 before UI verification"
            )
        scenario_evidence = _ui_scenario_evidence(
            ui_cases=ui_cases,
            test_data_plan=test_data_plan,
            execution_result=execution_result,
        )
        schema_version = (
            "v3" if execution_result.get("schema_version") == "v3" else "v2"
        )
        scenario_bindings = (
            _ui_scenario_binding_refs(
                ui_cases=ui_cases,
                test_data_plan=test_data_plan,
                execution_result=execution_result,
            )
            if schema_version == "v3"
            else {}
        )
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT result.edit_packet_id, result.result_repository_revision
                FROM edit_results AS result
                JOIN edit_packets AS packet
                  ON packet.edit_packet_id = result.edit_packet_id
                 AND packet.project_id = result.project_id
                JOIN change_orchestrations AS orchestration
                  ON orchestration.impact_report_id = packet.impact_report_id
                 AND orchestration.project_id = packet.project_id
                 AND orchestration.analysis_case_id = packet.analysis_case_id
                WHERE orchestration.orchestration_id = %s
                  AND result.validation_mode = 'committed'
                  AND result.status IN ('in_scope', 'no_changes')
                  AND result.result_repository_revision IS NOT NULL
                ORDER BY result.recorded_at DESC, result.edit_result_id DESC
                LIMIT 1
                """,
                (orchestration_id,),
            )
            edit_row = cursor.fetchone()
        if edit_row is None:
            raise ValueError(
                "UI verification requires a committed in-scope or no-changes Edit Result"
            )
        edit_packet_id = str(edit_row[0])
        revision = str(edit_row[1])
        case_id = str(orchestration["analysis_case_id"])
        project_id = str(orchestration["project_id"])
        impact_item_ids = [
            str(item["impact_item_id"])
            for item in cast(list[dict[str, Any]], orchestration["code_scope"])
        ]
        result_id = _id(
            "test-data-ui-verification",
            project_id,
            orchestration_id,
            str(execution_result["execution_result_id"]),
        )
        artifact = {
            "artifact_type": "UiVerificationResult",
            "schema_version": schema_version,
            "verification_result_id": result_id,
            "orchestration_id": orchestration_id,
            "test_data_execution_result_id": execution_result["execution_result_id"],
            "analysis_case_id": case_id,
            "edit_packet_id": edit_packet_id,
            "repository_revision": revision,
            "deployment_revision": revision,
            "environment_id": "operamind-test-data-ui",
            "status": "passed",
            "scenario_results": [
                {
                    "scenario_id": str(case["test_case_id"]),
                    "status": "passed",
                    "impact_item_refs": impact_item_ids,
                    "evidence_refs": scenario_evidence[str(case["test_case_id"])],
                    "failure_category": "none",
                    "summary": (
                        "TestDataPlan のデータ生成後に UI 操作、"
                        "UI 断言、スクリーンショット保存が完了しました。"
                    ),
                    **(
                        {
                            "test_data_binding_refs": scenario_bindings[
                                str(case["test_case_id"])
                            ]
                        }
                        if schema_version == "v3"
                        else {}
                    ),
                }
                for case in ui_cases
            ],
            "unresolved_impact_item_ids": [],
            "out_of_scope_files": [],
            "failure_reasons": [],
        }
        self._contracts.validate_artifact(artifact)
        self._artifacts.store(
            artifact_id=result_id,
            project_id=project_id,
            analysis_case_id=case_id,
            artifact=artifact,
        )
        return {
            "verification_result_id": result_id,
            "status": "passed",
            "scenario_count": len(ui_cases),
        }

    def _required(self, artifact_id: str, artifact_type: str) -> dict[str, Any]:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None or artifact.get("artifact_type") != artifact_type:
            raise ValueError(f"Required {artifact_type} Artifact is missing: {artifact_id}")
        return artifact


def _ui_scenario_evidence(
    *,
    ui_cases: list[dict[str, Any]],
    test_data_plan: dict[str, Any],
    execution_result: dict[str, Any],
) -> dict[str, list[str]]:
    flow_definitions = {
        str(flow["flow_id"]): flow
        for flow in cast(list[dict[str, Any]], test_data_plan["generation_flows"])
    }
    flow_results = {
        str(flow["flow_id"]): flow
        for flow in cast(list[dict[str, Any]], execution_result["flow_results"])
    }
    evidence = cast(list[dict[str, Any]], execution_result["evidence"])
    collected: dict[str, list[str]] = {}
    for case in ui_cases:
        case_id = str(case["test_case_id"])
        matching = [
            flow
            for flow in flow_definitions.values()
            if case_id in {str(value) for value in cast(list[object], flow["test_case_refs"])}
        ]
        refs: set[str] = set()
        for flow in matching:
            flow_id = str(flow["flow_id"])
            result = flow_results.get(flow_id)
            if result is None or result.get("status") != "passed":
                continue
            ui_step_ids = {
                str(step["step_id"])
                for step in cast(list[dict[str, Any]], flow["steps"])
                if step.get("channel") == "ui"
            }
            passed_ui_steps = {
                str(step["step_id"])
                for step in cast(list[dict[str, Any]], result["step_results"])
                if step.get("channel") == "ui" and step.get("status") == "passed"
            }
            if not ui_step_ids or not ui_step_ids.issubset(passed_ui_steps):
                continue
            refs.update(
                str(item["evidence_ref"])
                for item in evidence
                if item.get("flow_id") == flow_id
                and item.get("step_id") in ui_step_ids
                and item.get("phase") == "setup"
                and item.get("evidence_type") == "screenshot"
                and item.get("sanitized") is True
            )
        if not refs:
            raise ValueError(
                f"UI test case has no passed UI step with screenshot Evidence: {case_id}"
            )
        collected[case_id] = sorted(refs)
    return collected


def _ui_scenario_binding_refs(
    *,
    ui_cases: list[dict[str, Any]],
    test_data_plan: dict[str, Any],
    execution_result: dict[str, Any],
) -> dict[str, list[str]]:
    project_id = str(execution_result.get("project_id", ""))
    run_id = str(execution_result.get("run_id", ""))
    if not project_id or not run_id:
        raise ValueError("v3 UI verification requires current Project and Run scope")
    bindings: dict[str, dict[str, Any]] = {}
    for value in cast(list[dict[str, Any]], execution_result.get("data_bindings", [])):
        binding_id = str(value.get("binding_id", ""))
        if (
            not binding_id
            or value.get("project_id") != project_id
            or value.get("run_id") != run_id
        ):
            raise ValueError("UI verification Binding belongs to another Project or Run")
        content = {
            key: item
            for key, item in value.items()
            if key not in {"content_digest", "evidence_ref"}
        }
        if value.get("content_digest") != canonical_digest(content):
            raise ValueError("UI verification Binding content digest differs")
        identity = {
            "business_unique_keys": value.get("business_unique_keys"),
            "screen_identity_values": value.get("screen_identity_values"),
        }
        if value.get("identity_digest") != canonical_digest(identity):
            raise ValueError("UI verification Binding identity digest differs")
        if binding_id in bindings:
            raise ValueError("UI verification Binding ID is duplicated")
        bindings[binding_id] = value

    flow_definitions = {
        str(flow["flow_id"]): flow
        for flow in cast(list[dict[str, Any]], test_data_plan["generation_flows"])
    }
    flow_results = {
        str(flow["flow_id"]): flow
        for flow in cast(list[dict[str, Any]], execution_result["flow_results"])
    }
    screenshot_evidence = [
        item
        for item in cast(list[dict[str, Any]], execution_result["evidence"])
        if item.get("evidence_type") == "screenshot"
        and item.get("sanitized") is True
    ]
    collected: dict[str, list[str]] = {}
    for case in ui_cases:
        case_id = str(case["test_case_id"])
        case_refs: set[str] = set()
        matching_flow_ids = {
            flow_id
            for flow_id, flow in flow_definitions.items()
            if case_id
            in {str(item) for item in cast(list[object], flow["test_case_refs"])}
        }
        for flow_id in matching_flow_ids:
            result = flow_results.get(flow_id)
            if result is None or result.get("status") != "passed":
                continue
            for step in cast(list[dict[str, Any]], result["step_results"]):
                if step.get("channel") != "ui" or step.get("status") != "passed":
                    continue
                step_id = str(step["step_id"])
                step_refs = {
                    str(item)
                    for item in cast(list[object], step.get("test_data_binding_refs", []))
                }
                matching_screenshots = [
                    item
                    for item in screenshot_evidence
                    if item.get("flow_id") == flow_id
                    and item.get("step_id") == step_id
                    and item.get("phase") == "setup"
                ]
                for screenshot in matching_screenshots:
                    ref = str(screenshot.get("test_data_binding_ref", ""))
                    if not ref or ref not in bindings or ref not in step_refs:
                        raise ValueError(
                            "Screenshot Evidence does not resolve to the UI Step Binding"
                        )
                    case_refs.add(ref)
                if matching_screenshots and not step_refs:
                    raise ValueError("UI Step has no frozen TestDataBinding reference")
        if not case_refs:
            raise ValueError(
                f"UI test case has no screenshot-bound TestDataBinding: {case_id}"
            )
        collected[case_id] = sorted(case_refs)
    return collected


def _id(prefix: str, *values: str) -> str:
    material = "\0".join(values).encode()
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"
