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
