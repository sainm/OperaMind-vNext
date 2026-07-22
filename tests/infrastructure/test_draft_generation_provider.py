import json
from pathlib import Path

from operamind.infrastructure.draft_generation import FileDraftGenerationProvider

ROOT = Path(__file__).parents[2]


def test_file_draft_provider_imports_copilot_response_through_schema_gate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "copilot-response.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "case": {},
                "document_operations": [],
                "confidence": {
                    "document_change": "high",
                    "code_scope": "high",
                    "edit_plan": "high",
                    "verification_plan": "high",
                },
                "questions": [],
            }
        ),
        encoding="utf-8",
    )

    result = FileDraftGenerationProvider(
        repository_root=ROOT,
        response_path=source,
    ).generate(
        prompt='{"task":"bounded Copilot import"}',
        workspace_root=workspace,
        output_root=tmp_path / "output",
    )

    assert result.provider_id == "github-copilot-vscode"
    assert result.payload["schema_version"] == "v1"
    assert result.response_path != source
    assert result.response_path.read_text(encoding="utf-8").endswith("\n")
    assert "VS Code GitHub Copilot" in result.stdout_path.read_text(encoding="utf-8")
