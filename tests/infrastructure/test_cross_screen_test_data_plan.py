from pathlib import Path

import pytest

from operamind.contracts import ContractCatalog
from tests.fixtures.visiondemo_target_e2e import (
    build_visiondemo_cross_screen_plan,
)

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("force_business_failure", [False, True])
def test_cross_screen_plan_matches_contract_and_always_defines_cleanup(
    force_business_failure: bool,
) -> None:
    plan = build_visiondemo_cross_screen_plan(force_business_failure=force_business_failure)

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
    assert [step.get("entity_ref") for step in flow["steps"] if step.get("entity_ref")] == [
        "employee",
        "expense",
    ]
    assert [step.get("entity_ref") for step in flow["cleanup_steps"][:2]] == [
        "expense",
        "employee",
    ]
