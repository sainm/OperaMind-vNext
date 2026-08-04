from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_windows_extension_step_creates_its_own_junit_directory() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")
    extension = workflow.split("- name: VS Code Extension regression", 1)[1].split("- name:", 1)[0]

    assert "if: always()" in extension
    assert "New-Item -ItemType Directory -Force quality-results" in extension
    assert "npm run test:junit" in extension
