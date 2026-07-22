import json
from pathlib import Path

import pytest

from operamind.domain import BrowserExecutionManifest

ROOT = Path(__file__).parents[2]


def _manifest() -> dict[str, object]:
    return {
        "manifest_id": "browser-manifest-001",
        "plan_id": "ui-plan-001",
        "project_id": "visiondemo",
        "browser": {
            "name": "chromium",
            "channel": "chrome",
            "headless": True,
            "viewport": {"width": 1280, "height": 720},
        },
        "review_status": "approved",
        "reviewed_by": "qa@example.com",
        "scenarios": [
            {
                "scenario_id": "expense-filter-default-all",
                "trigger_path": "/expenses",
                "impact_item_refs": ["impact-item-001"],
                "actions": [
                    {
                        "action_id": "select-all",
                        "kind": "select_option",
                        "locator": {"strategy": "label", "value": "Status"},
                        "value": {"source": "literal", "value": "all"},
                    }
                ],
                "assertions": [
                    {
                        "assertion_id": "all-visible",
                        "kind": "count_equals",
                        "locator": {"strategy": "test_id", "value": "expense-row"},
                        "expected": {"source": "literal", "value": "4"},
                        "failure_category": "business_assertion",
                    }
                ],
                "redaction_locators": [{"strategy": "css", "value": "[data-sensitive='true']"}],
            }
        ],
    }


def test_browser_manifest_round_trips_strict_declarative_dsl() -> None:
    raw = _manifest()

    manifest = BrowserExecutionManifest.from_dict(raw)

    assert BrowserExecutionManifest.from_dict(manifest.to_dict()) == manifest


def test_documented_browser_manifest_example_is_executable() -> None:
    raw: object = json.loads(
        (ROOT / "docs/examples/ui-browser-manifest.v1.json").read_text(encoding="utf-8")
    )

    manifest = BrowserExecutionManifest.from_dict(raw)

    assert manifest.review_status == "approved"
    assert manifest.scenarios[0].impact_item_refs == ("impact-item-001",)


def test_browser_manifest_rejects_arbitrary_script_action() -> None:
    raw = _manifest()
    scenario = raw["scenarios"][0]  # type: ignore[index]
    scenario["actions"][0]["kind"] = "evaluate_javascript"  # type: ignore[index]

    with pytest.raises(ValueError, match="not a valid BrowserActionKind"):
        BrowserExecutionManifest.from_dict(raw)


def test_browser_manifest_rejects_fragile_deep_css() -> None:
    raw = _manifest()
    scenario = raw["scenarios"][0]  # type: ignore[index]
    scenario["redaction_locators"][0]["value"] = "main div:nth-child(2) input"  # type: ignore[index]

    with pytest.raises(ValueError, match="single stable"):
        BrowserExecutionManifest.from_dict(raw)


def test_browser_manifest_rejects_unscoped_secret_environment_variable() -> None:
    raw = _manifest()
    scenario = raw["scenarios"][0]  # type: ignore[index]
    scenario["actions"][0]["value"] = {"source": "env", "value": "PASSWORD"}  # type: ignore[index]

    with pytest.raises(ValueError, match="OPERAMIND_UI_"):
        BrowserExecutionManifest.from_dict(raw)


def test_browser_manifest_requires_origin_relative_trigger_path() -> None:
    raw = _manifest()
    scenario = raw["scenarios"][0]  # type: ignore[index]
    scenario["trigger_path"] = "https://attacker.invalid/expenses"  # type: ignore[index]

    with pytest.raises(ValueError, match="origin-relative"):
        BrowserExecutionManifest.from_dict(raw)


def test_browser_manifest_allows_blank_literal_for_default_select_value() -> None:
    raw = _manifest()
    scenario = raw["scenarios"][0]  # type: ignore[index]
    scenario["assertions"][0] = {  # type: ignore[index]
        "assertion_id": "default-all",
        "kind": "value_equals",
        "locator": {"strategy": "css", "value": "#expense-search-status"},
        "expected": {"source": "literal", "value": ""},
        "failure_category": "business_assertion",
    }

    manifest = BrowserExecutionManifest.from_dict(raw)

    assert manifest.scenarios[0].assertions[0].expected is not None
    assert manifest.scenarios[0].assertions[0].expected.value == ""
