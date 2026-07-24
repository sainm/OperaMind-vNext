import json
import subprocess
from pathlib import Path

import pytest

from operamind.application import CopilotCheckpointRequest, CopilotCheckpointService
from operamind.commands.copilot_checkpoint import build_parser


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def initialized_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "remote", "add", "origin", "https://example.invalid/example.git")
    source = repository / "src/main/java/example"
    source.mkdir(parents=True)
    (source / "App.java").write_text("class App {}\n", encoding="utf-8")
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=OperaMind Test",
        "-c",
        "user.email=operamind@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    return repository, git(repository, "rev-parse", "HEAD")


def test_checkpoint_cli_exposes_codex_rehearsal_attach_and_show() -> None:
    parser = build_parser()

    attached = parser.parse_args(
        [
            "attach-rehearsal",
            "--checkpoint-root",
            "checkpoint",
            "--proposal-file",
            "proposal.json",
        ]
    )
    shown = parser.parse_args(["show-rehearsal", "--checkpoint-root", "checkpoint"])

    assert attached.proposal_file == Path("proposal.json")
    assert shown.command == "show-rehearsal"


def test_code_edit_checkpoint_pauses_and_resumes_in_same_linked_worktree(
    tmp_path: Path,
) -> None:
    repository, revision = initialized_repository(tmp_path)
    worktree = tmp_path / "copilot-worktree"
    git(repository, "worktree", "add", "--detach", str(worktree), revision)
    checkpoint = tmp_path / "checkpoint"
    service = CopilotCheckpointService()
    service.initialize(
        checkpoint_root=checkpoint,
        request=CopilotCheckpointRequest(
            session_id="session-1",
            phase="code_edit",
            project_id="project-1",
            analysis_case_id="case-1",
            registered_repository_root=repository,
            workspace_root=worktree,
            base_revision=revision,
            expected_outputs=("src/main/java/example/App.java",),
            approval_grant_id="grant-1",
            edit_packet_id="packet-1",
        ),
    )

    paused = service.pause(checkpoint_root=checkpoint, reason="free_quota_exhausted")
    rebound = service.rebind_grant(
        checkpoint_root=checkpoint,
        expected_previous_grant_id="grant-1",
        approval_grant_id="grant-2",
    )
    resumed = service.resume(checkpoint_root=checkpoint)

    assert paused["status"] == "paused"
    assert paused["pause_reason"] == "free_quota_exhausted"
    assert rebound["approval_grant_id"] == "grant-2"
    assert resumed["status"] == "active"
    assert resumed["resume_count"] == 1


def test_checkpoint_grant_rebind_requires_paused_state_and_matching_previous_id(
    tmp_path: Path,
) -> None:
    repository, revision = initialized_repository(tmp_path)
    worktree = tmp_path / "copilot-worktree"
    git(repository, "worktree", "add", "--detach", str(worktree), revision)
    checkpoint = tmp_path / "checkpoint"
    service = CopilotCheckpointService()
    service.initialize(
        checkpoint_root=checkpoint,
        request=CopilotCheckpointRequest(
            session_id="session-1",
            phase="code_edit",
            project_id="project-1",
            analysis_case_id="case-1",
            registered_repository_root=repository,
            workspace_root=worktree,
            base_revision=revision,
            expected_outputs=("src/main/java/example/App.java",),
            approval_grant_id="grant-1",
            edit_packet_id="packet-1",
        ),
    )

    with pytest.raises(ValueError, match="paused code-edit"):
        service.rebind_grant(
            checkpoint_root=checkpoint,
            expected_previous_grant_id="grant-1",
            approval_grant_id="grant-2",
        )
    service.pause(checkpoint_root=checkpoint, reason="free_quota_exhausted")
    with pytest.raises(ValueError, match="previous Grant ID"):
        service.rebind_grant(
            checkpoint_root=checkpoint,
            expected_previous_grant_id="grant-wrong",
            approval_grant_id="grant-2",
        )


def test_code_edit_checkpoint_rejects_original_or_unrelated_workspace(tmp_path: Path) -> None:
    repository, revision = initialized_repository(tmp_path)
    service = CopilotCheckpointService()
    request = CopilotCheckpointRequest(
        session_id="session-1",
        phase="code_edit",
        project_id="project-1",
        analysis_case_id="case-1",
        registered_repository_root=repository,
        workspace_root=repository,
        base_revision=revision,
        expected_outputs=("src/main/java/example/App.java",),
        approval_grant_id="grant-1",
        edit_packet_id="packet-1",
    )

    with pytest.raises(ValueError, match="isolated linked worktree"):
        service.initialize(checkpoint_root=tmp_path / "checkpoint", request=request)


def test_code_edit_checkpoint_rejects_process_files_inside_original_repository(
    tmp_path: Path,
) -> None:
    repository, revision = initialized_repository(tmp_path)
    worktree = tmp_path / "copilot-worktree"
    git(repository, "worktree", "add", "--detach", str(worktree), revision)
    request = CopilotCheckpointRequest(
        session_id="session-1",
        phase="code_edit",
        project_id="project-1",
        analysis_case_id="case-1",
        registered_repository_root=repository,
        workspace_root=worktree,
        base_revision=revision,
        expected_outputs=("src/main/java/example/App.java",),
        approval_grant_id="grant-1",
        edit_packet_id="packet-1",
    )

    with pytest.raises(ValueError, match="outside target repositories"):
        CopilotCheckpointService().initialize(
            checkpoint_root=repository / ".operamind-checkpoint",
            request=request,
        )

    assert git(repository, "status", "--short") == ""


def test_codex_rehearsal_is_attached_outside_clean_target_as_non_executable(
    tmp_path: Path,
) -> None:
    repository, revision = initialized_repository(tmp_path)
    worktree = tmp_path / "copilot-worktree"
    git(repository, "worktree", "add", "--detach", str(worktree), revision)
    checkpoint = tmp_path / "checkpoint"
    service = CopilotCheckpointService()
    service.initialize(
        checkpoint_root=checkpoint,
        request=CopilotCheckpointRequest(
            session_id="session-1",
            phase="code_edit",
            project_id="project-1",
            analysis_case_id="case-1",
            registered_repository_root=repository,
            workspace_root=worktree,
            base_revision=revision,
            expected_outputs=(
                "src/main/java/example/App.java",
                "src/test/scripts/app-search.sh",
            ),
            approval_grant_id="grant-1",
            edit_packet_id="packet-1",
        ),
    )
    service.pause(checkpoint_root=checkpoint, reason="free_quota_exhausted")
    proposal = tmp_path / "proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "proposal_id": "proposal-1",
                "producer": "openai_codex",
                "generated_at": "2026-07-18T12:00:00Z",
                "status": "ready_for_copilot_review",
                "bindings": {
                    "session_id": "session-1",
                    "project_id": "project-1",
                    "analysis_case_id": "case-1",
                    "edit_packet_id": "packet-1",
                    "base_revision": revision,
                },
                "execution_policy": {
                    "executable": False,
                    "automatic_apply_allowed": False,
                    "required_final_editor": "github_copilot_vscode",
                    "copilot_must_revalidate": True,
                },
                "changes": [
                    {
                        "path": "src/main/java/example/App.java",
                        "operation": "modify",
                        "candidate_patch": "-class App {}\n+class App { int value; }\n",
                        "candidate_content": None,
                        "rationale": "Exercise the approved behavior.",
                    },
                    {
                        "path": "src/test/scripts/app-search.sh",
                        "operation": "add",
                        "candidate_patch": None,
                        "candidate_content": "#!/usr/bin/env bash\nset -euo pipefail\n",
                        "rationale": "Verify the approved behavior against the real API.",
                    },
                ],
                "findings": [],
                "validation_steps": ["Run the Grant-approved API command."],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    attached = service.attach_rehearsal(
        checkpoint_root=checkpoint,
        proposal_file=proposal,
    )
    loaded = service.load_rehearsal(checkpoint)

    assert attached["executable"] is False
    assert attached["required_final_editor"] == "github_copilot_vscode"
    assert loaded["proposal"]["proposal_id"] == "proposal-1"
    assert "Do not apply it as a patch" in (
        checkpoint / "COPILOT-REHEARSAL-INSTRUCTIONS.md"
    ).read_text(encoding="utf-8")
    assert git(worktree, "status", "--short") == ""


def test_codex_rehearsal_can_block_for_reanalysis_without_candidate_changes(
    tmp_path: Path,
) -> None:
    repository, revision = initialized_repository(tmp_path)
    worktree = tmp_path / "copilot-worktree"
    git(repository, "worktree", "add", "--detach", str(worktree), revision)
    checkpoint = tmp_path / "checkpoint"
    service = CopilotCheckpointService()
    service.initialize(
        checkpoint_root=checkpoint,
        request=CopilotCheckpointRequest(
            session_id="session-1",
            phase="code_edit",
            project_id="project-1",
            analysis_case_id="case-1",
            registered_repository_root=repository,
            workspace_root=worktree,
            base_revision=revision,
            expected_outputs=("src/main/java/example/App.java",),
            approval_grant_id="grant-1",
            edit_packet_id="packet-1",
        ),
    )
    service.pause(checkpoint_root=checkpoint, reason="free_quota_exhausted")
    proposal = tmp_path / "proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "proposal_id": "proposal-conflict",
                "producer": "openai_codex",
                "generated_at": "2026-07-18T12:00:00+00:00",
                "status": "needs_reanalysis",
                "bindings": {
                    "session_id": "session-1",
                    "project_id": "project-1",
                    "analysis_case_id": "case-1",
                    "edit_packet_id": "packet-1",
                    "base_revision": revision,
                },
                "execution_policy": {
                    "executable": False,
                    "automatic_apply_allowed": False,
                    "required_final_editor": "github_copilot_vscode",
                    "copilot_must_revalidate": True,
                },
                "changes": [],
                "findings": ["Approved expected value conflicts with the bound fixture."],
                "validation_steps": ["Replace the Impact Report before editing."],
            }
        ),
        encoding="utf-8",
    )

    attached = service.attach_rehearsal(
        checkpoint_root=checkpoint,
        proposal_file=proposal,
    )

    assert attached["status"] == "needs_reanalysis"
    with pytest.raises(ValueError, match="requires reanalysis"):
        service.resume(checkpoint_root=checkpoint)
    assert git(worktree, "status", "--short") == ""
