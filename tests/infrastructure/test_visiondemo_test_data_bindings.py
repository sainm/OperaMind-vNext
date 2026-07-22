import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from operamind.application.test_data_execution import (
    TestDataExecutionRequest as ExecutionRequest,
)
from operamind.application.test_data_execution import (
    TestDataStepExecution as StepExecution,
)
from operamind.application.visiondemo_target_e2e import (
    build_visiondemo_cross_screen_plan,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.test_data.executors import HttpResponse
from operamind.infrastructure.test_data.visiondemo import (
    VisionDemoCanonicalHttpExecutor,
    VisionDemoDeploymentConfig,
    VisionDemoReviewedHttpTransport,
)

ROOT = Path(__file__).parents[2]


class _RecordingTransport:
    def __init__(self) -> None:
        self.body: bytes | None = None

    def send(
        self,
        *,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        del method, url, headers, timeout_seconds
        self.body = body
        return HttpResponse(status_code=200, headers={}, body=b"{}")


class _RecordingExecutor:
    def __init__(self) -> None:
        self.resolved_inputs: Mapping[str, object] = {}

    def execute(
        self,
        *,
        request: ExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> StepExecution:
        del request, flow_id, step, variables, phase
        self.resolved_inputs = resolved_inputs
        return StepExecution(source_values={}, evidence=())


def test_reviewed_transport_adapts_only_returned_expense_recipe() -> None:
    delegate = _RecordingTransport()
    transport = VisionDemoReviewedHttpTransport(delegate)

    transport.send(
        method="POST",
        url="http://127.0.0.1:18082/expense/api/save",
        body=json.dumps({"expenseNo": "EXP-OM-REVIEWED", "status": "差戻し"}).encode(),
        headers={"Content-Type": "application/json"},
        timeout_seconds=1,
    )

    assert delegate.body is not None
    payload = json.loads(delegate.body)
    assert payload["expense"]["expenseNo"] == "EXP-OM-REVIEWED"
    assert payload["expense"]["employee"] == {"id": 2}
    assert payload["details"][0]["amount"] == 4321


def test_reviewed_transport_rejects_unapproved_logical_fields() -> None:
    transport = VisionDemoReviewedHttpTransport(_RecordingTransport())

    with pytest.raises(ValueError, match="unapproved fields"):
        transport.send(
            method="POST",
            url="http://127.0.0.1:18082/expense/api/save",
            body=json.dumps(
                {"expenseNo": "EXP-OM-REVIEWED", "status": "差戻し", "amount": 1}
            ).encode(),
            headers={"Content-Type": "application/json"},
            timeout_seconds=1,
        )


def test_canonical_http_executor_normalizes_legacy_direct_inputs() -> None:
    delegate = _RecordingExecutor()
    executor = VisionDemoCanonicalHttpExecutor(delegate)  # type: ignore[arg-type]

    executor.execute(
        request=ExecutionRequest(
            execution_result_id="result-1",
            run_id="run-1",
            project_id="visiondemo",
        ),
        flow_id="flow-1",
        step={"target": "POST /expense/api/save"},
        resolved_inputs={"expenseNo": "EXP-OM-REVIEWED", "status": "差戻し"},
        variables={},
        phase="setup",
    )

    assert delegate.resolved_inputs == {
        "method": "POST",
        "path": "/expense/api/save",
        "json": {"expenseNo": "EXP-OM-REVIEWED", "status": "差戻し"},
    }


def test_deployment_config_rejects_non_tmp_h2_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    h2_jar = tmp_path / "h2.jar"
    java = tmp_path / "java"
    h2_jar.touch()
    java.touch()
    monkeypatch.setenv("OPERAMIND_VISIONDEMO_BASE_URL", "http://127.0.0.1:18082")
    monkeypatch.setenv(
        "OPERAMIND_VISIONDEMO_JDBC_URL",
        "jdbc:h2:file:/Users/example/visiondemo;AUTO_SERVER=TRUE",
    )
    monkeypatch.setenv("OPERAMIND_VISIONDEMO_H2_JAR", str(h2_jar))
    monkeypatch.setenv("OPERAMIND_VISIONDEMO_JAVA", str(java))

    with pytest.raises(ValueError, match="local /tmp H2"):
        VisionDemoDeploymentConfig.from_environment()


def test_deployment_config_rejects_credentials_in_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    h2_jar = tmp_path / "h2.jar"
    java = tmp_path / "java"
    h2_jar.touch()
    java.touch()
    monkeypatch.setenv(
        "OPERAMIND_VISIONDEMO_BASE_URL", "http://operator:secret@127.0.0.1:18082"
    )
    monkeypatch.setenv(
        "OPERAMIND_VISIONDEMO_JDBC_URL",
        "jdbc:h2:file:/tmp/visiondemo;AUTO_SERVER=TRUE",
    )
    monkeypatch.setenv("OPERAMIND_VISIONDEMO_H2_JAR", str(h2_jar))
    monkeypatch.setenv("OPERAMIND_VISIONDEMO_JAVA", str(java))

    with pytest.raises(ValueError, match="must be an origin"):
        VisionDemoDeploymentConfig.from_environment()


@pytest.mark.parametrize("force_business_failure", [False, True])
def test_cross_screen_plan_matches_contract_and_always_defines_cleanup(
    force_business_failure: bool,
) -> None:
    plan = build_visiondemo_cross_screen_plan(
        force_business_failure=force_business_failure
    )

    ContractCatalog.load(ROOT / "contracts").validate_artifact(plan)
    flow = plan["generation_flows"][0]
    assert [step["channel"] for step in flow["steps"]] == [
        "fixture",
        "http",
        "http",
        "ui",
        "ui",
        "sql",
    ]
    assert [step["channel"] for step in flow["cleanup_steps"]] == [
        "http",
        "http",
        "sql",
    ]
    assert plan["template_instances"][0]["entity_order"] == ["employee", "expense"]
    assert plan["template_instances"][0]["cleanup_entity_order"] == [
        "expense",
        "employee",
    ]
    assert [step.get("entity_ref") for step in flow["cleanup_steps"][:2]] == [
        "expense",
        "employee",
    ]
