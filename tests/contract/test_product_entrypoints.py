import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_product_entrypoints_do_not_restore_manual_main_flow_clis() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = config["project"]["scripts"]

    assert scripts["operamind-web"] == "operamind.commands.web:main"
    assert scripts["operamind-mcp"] == "operamind.commands.mcp_server:main"
    assert {
        "operamind-change-cases",
        "operamind-start-analysis",
        "operamind-review-change",
        "operamind-resolve-code-scope",
        "operamind-approval-grant",
        "operamind-build-edit-packet",
        "operamind-orchestration-tasks",
        "operamind-ui-verification",
    }.isdisjoint(scripts)


def test_product_source_does_not_query_retired_ui_pipeline_tables() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((ROOT / "src/operamind").rglob("*.py"))
    )

    retired_tables = {
        "change_validations",
        "ui_browser_manifests",
        "ui_execution_evidence",
        "ui_execution_plans",
        "ui_execution_runs",
        "ui_knowledge_snapshots",
        "ui_preflight_checks",
    }
    assert all(table not in source for table in retired_tables)
