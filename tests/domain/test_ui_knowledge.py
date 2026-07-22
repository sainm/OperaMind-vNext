import pytest

from operamind.domain import BrowserExecutionManifest, UiKnowledgeSnapshot


def _snapshot() -> dict[str, object]:
    return {
        "snapshot_id": "ui-knowledge-001",
        "project_id": "visiondemo",
        "environment_id": "staging",
        "deployment_revision": "deploy-001",
        "snapshot_version": "1.0.0",
        "review_status": "approved",
        "reviewed_by": "qa@example.com",
        "activate": True,
        "targets": [
            {
                "target_ref": "expense.status-filter",
                "business_name": "ステータス絞り込み",
                "screen_name": "経費一覧",
                "trigger_path": "/expenses",
                "source_fact_refs": ["fact-screen-status"],
                "candidates": [
                    {
                        "candidate_id": "locator-status-label",
                        "locator": {"strategy": "label", "value": "Status"},
                        "priority": 1,
                        "reliability_score": 0.95,
                        "source": "screen_design_and_runtime",
                    },
                    {
                        "candidate_id": "locator-status-testid",
                        "locator": {"strategy": "test_id", "value": "status-filter"},
                        "priority": 2,
                        "reliability_score": 0.99,
                        "source": "runtime_observation",
                    },
                ],
            }
        ],
    }


def test_ui_knowledge_prefers_reviewed_business_locator_priority() -> None:
    snapshot = UiKnowledgeSnapshot.from_dict(_snapshot())

    locator = snapshot.resolve("expense.status-filter")

    assert locator.to_dict() == {"strategy": "label", "value": "Status", "exact": True}
    assert UiKnowledgeSnapshot.from_dict(snapshot.to_dict()) == snapshot


def test_ui_knowledge_rejects_candidates_below_reliability_threshold() -> None:
    raw = _snapshot()
    raw["targets"][0]["candidates"][0]["reliability_score"] = 0.79  # type: ignore[index]
    raw["targets"][0]["candidates"][1]["reliability_score"] = 0.78  # type: ignore[index]
    snapshot = UiKnowledgeSnapshot.from_dict(raw)

    with pytest.raises(ValueError, match="reliability threshold"):
        snapshot.resolve("expense.status-filter")


def test_business_target_locator_requires_frozen_ui_knowledge_snapshot() -> None:
    manifest: dict[str, object] = {
        "manifest_id": "manifest-001",
        "plan_id": "plan-001",
        "project_id": "visiondemo",
        "browser": {
            "name": "chromium",
            "channel": None,
            "headless": True,
            "viewport": {"width": 1280, "height": 720},
        },
        "review_status": "approved",
        "reviewed_by": "qa@example.com",
        "scenarios": [
            {
                "scenario_id": "expense-filter",
                "trigger_path": "/expenses",
                "impact_item_refs": ["impact-001"],
                "actions": [],
                "assertions": [
                    {
                        "assertion_id": "status-visible",
                        "kind": "visible",
                        "locator": {"target_ref": "expense.status-filter"},
                        "failure_category": "business_assertion",
                    }
                ],
                "redaction_locators": [],
            }
        ],
    }

    with pytest.raises(ValueError, match="ui_knowledge_snapshot_id"):
        BrowserExecutionManifest.from_dict(manifest)

    manifest["ui_knowledge_snapshot_id"] = "ui-knowledge-001"
    parsed = BrowserExecutionManifest.from_dict(manifest)
    assert parsed.scenarios[0].assertions[0].locator.target_ref == "expense.status-filter"
