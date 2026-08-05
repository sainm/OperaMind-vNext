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


def test_configuration_guide_covers_every_product_runtime_setting() -> None:
    guide = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    required_runtime_settings = {
        "OPERAMIND_DATABASE_URL",
        "OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN",
        "OPERAMIND_PLAYWRIGHT_CHANNEL",
        "EMBED_API_URL",
        "EMBED_API_KEY",
        "EMBED_MODEL",
    }
    assert all(name in guide for name in required_runtime_settings)
    assert all(name in example for name in required_runtime_settings)
    assert "OPERAMIND_TEST_TARGET_BASE_URL" not in example


def test_configuration_guide_does_not_claim_oracle_target_execution_is_ready() -> None:
    guide = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")

    assert "現在、production で実行できる SQL Target Data 方言は PostgreSQL のみ" in guide
    assert "未登録方言は Plan 確認前に blocked" in guide
    assert "PostgreSQL へ fallback しない" in guide
