from __future__ import annotations

import copy
from pathlib import Path

import pytest

from operamind.application.business_data_template import (
    BusinessDataTemplateInstantiator,
    BusinessDataTemplateRequest,
    validate_business_data_template,
)
from operamind.application.test_data_execution import (
    TestDataExecutionEngine as DataExecutionEngine,
)
from operamind.application.test_data_execution import (
    TestDataExecutionRequest as DataExecutionRequest,
)
from operamind.contracts import ContractCatalog
from tests.fixtures.visiondemo_target_e2e import (
    build_visiondemo_cross_screen_template,
)

ROOT = Path(__file__).resolve().parents[2]


def _request(instance_id: str, *, status: str = "差戻し") -> BusinessDataTemplateRequest:
    return BusinessDataTemplateRequest(
        instance_id=instance_id,
        test_data_plan_id=f"plan-{instance_id}",
        test_plan_id=f"test-plan-{instance_id}",
        project_id="visiondemo",
        test_case_refs=(f"case-{instance_id}",),
        parameters={
            "department_id": 7,
            "business_date": "2026-07-19",
            "expense_status": status,
            "expense_amount": 4321,
            "expected_employee_name": "{{employee_name}}",
        },
    )


def test_template_is_reused_without_persisting_parameter_values() -> None:
    instantiator = BusinessDataTemplateInstantiator(ContractCatalog.load(ROOT / "contracts"))
    template = build_visiondemo_cross_screen_template()

    first = instantiator.instantiate(template=template, request=_request("first"))
    second = instantiator.instantiate(template=template, request=_request("second"))

    assert first["test_data_plan_id"] != second["test_data_plan_id"]
    assert first["generation_flows"][0]["flow_id"] == "first-flow"
    assert second["generation_flows"][0]["flow_id"] == "second-flow"
    assert first["template_instances"][0]["entity_order"] == ["employee", "expense"]
    assert first["template_instances"][0]["cleanup_entity_order"] == [
        "expense",
        "employee",
    ]
    provenance = first["template_instances"][0]
    assert "parameter_values" not in provenance
    assert sorted(provenance["parameter_names"]) == [
        "business_date",
        "department_id",
        "expected_employee_name",
        "expense_amount",
        "expense_status",
    ]


def test_failed_precondition_builds_a_blocked_non_executable_plan() -> None:
    instantiator = BusinessDataTemplateInstantiator(ContractCatalog.load(ROOT / "contracts"))

    plan = instantiator.instantiate(
        template=build_visiondemo_cross_screen_template(),
        request=_request("unsupported", status="未知"),
    )

    assert plan["status"] == "blocked"
    assert plan["blocking_reasons"] == [
        "expense-status-supported: 経費ステータスが対象システムで利用できる"
    ]
    assert plan["template_instances"][0]["precondition_results"][2]["status"] == "blocked"
    execution = DataExecutionEngine(
        contracts=ContractCatalog.load(ROOT / "contracts"),
        executors={},
    ).execute(
        plan=plan,
        request=DataExecutionRequest(
            execution_result_id="result-unsupported",
            run_id="run-unsupported",
            project_id="visiondemo",
        ),
    )
    assert execution["status"] == "blocked"
    assert execution["evidence"] == []
    assert "expense-status-supported" in execution["failure_reasons"][0]


def test_template_rejects_detail_cleanup_after_master() -> None:
    template = copy.deepcopy(build_visiondemo_cross_screen_template())
    cleanup = template["generation_flow"]["cleanup_steps"]
    cleanup[0]["sequence"] = 2
    cleanup[1]["sequence"] = 1

    reasons = validate_business_data_template(template)

    assert "expense: detail must be cleaned before its master" in reasons


def test_template_rejects_unknown_or_missing_parameters() -> None:
    instantiator = BusinessDataTemplateInstantiator(ContractCatalog.load(ROOT / "contracts"))
    request = _request("invalid")
    with pytest.raises(ValueError, match="Unknown business data template parameters"):
        instantiator.instantiate(
            template=build_visiondemo_cross_screen_template(),
            request=BusinessDataTemplateRequest(
                instance_id=request.instance_id,
                test_data_plan_id=request.test_data_plan_id,
                test_plan_id=request.test_plan_id,
                project_id=request.project_id,
                test_case_refs=request.test_case_refs,
                parameters={**request.parameters, "secret": "must-not-be-accepted"},
            ),
        )
