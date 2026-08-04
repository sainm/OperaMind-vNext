from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from operamind.application.copilot_task_context import (
    CopilotTaskContextRequest,
    CopilotTaskContextService,
)


def _request(root: Path, **changes: object) -> CopilotTaskContextRequest:
    values: dict[str, object] = {
        "project_id": "project-1",
        "analysis_case_id": "case-1",
        "edit_packet_id": "packet-1",
        "approval_grant_id": "grant-1",
        "workspace_root": root,
    }
    values.update(changes)
    return CopilotTaskContextRequest(**values)  # type: ignore[arg-type]


def _service(
    *,
    registered_root: Path,
    requested_root: Path,
    packet: object = ...,
    grant_state: str = "active_editing",
    changed_paths: tuple[str, ...] = (),
    writable_files: tuple[str, ...] = ("src/App.java",),
    same_repository: bool = True,
    remote_url: str = "https://example.invalid/repository.git",
) -> CopilotTaskContextService:
    service = object.__new__(CopilotTaskContextService)
    service._edit_results = SimpleNamespace(
        load_packet_scope=lambda **_values: SimpleNamespace(
            workspace_root=str(registered_root),
            base_repository_revision="a" * 40,
            remote_url="https://example.invalid/repository.git",
            writable_files=writable_files,
        )
    )
    grant = SimpleNamespace(
        grant_id="grant-1",
        project_id="project-1",
        analysis_case_id="case-1",
        edit_packet_id="packet-1",
        state=grant_state,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        allowed_actions=("read", "modify"),
        command_profile_version_id="command-v1",
        allowed_test_command_refs=("test",),
        allowed_ui_scenarios=("scenario-1",),
    )
    service._grants = SimpleNamespace(
        authorize_edit=lambda **_values: grant,
        inspect=lambda _grant_id: grant,
    )
    artifact = (
        {"artifact_type": "CopilotEditPacket", "project_id": "project-1"}
        if packet is ...
        else packet
    )
    service._artifacts = SimpleNamespace(get=lambda _artifact_id: artifact)
    evidence = SimpleNamespace(
        workspace_root=requested_root,
        remote_url=remote_url,
        changed_paths=changed_paths,
        base_sha="a" * 40,
        result_sha=None,
    )
    service._git = SimpleNamespace(
        common_repository_dir=lambda root: (
            Path("/common/repository")
            if same_repository or root == registered_root
            else Path("/different/repository")
        ),
        inspect_current=lambda *_args, **_kwargs: evidence,
    )
    return service


def test_context_request_rejects_blank_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        _request(tmp_path, edit_packet_id=" ")


def test_context_returns_only_approved_workspace_and_artifact_fields(tmp_path: Path) -> None:
    registered = tmp_path / "registered"
    linked = tmp_path / "linked"
    registered.mkdir()
    linked.mkdir()
    service = _service(
        registered_root=registered,
        requested_root=linked,
        changed_paths=("src/App.java",),
    )

    context = service.get(_request(linked))

    assert context["edit_packet"] == {
        "artifact_type": "CopilotEditPacket",
        "project_id": "project-1",
    }
    assert context["approval"]["state"] == "active_editing"
    assert context["workspace"] == {
        "root": str(linked),
        "registered_root": str(registered),
        "isolated_worktree": True,
        "remote_url": "https://example.invalid/repository.git",
        "head_revision": "a" * 40,
        "changed_paths": ["src/App.java"],
        "result_committed": False,
    }
    assert context["context_package_available"] is False


def test_planning_context_accepts_only_matching_successfully_closed_grant(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    service = _service(
        registered_root=root,
        requested_root=root,
        grant_state="completed",
    )
    assert (
        service.get(_request(root, require_active_grant=False))["approval"]["state"] == "completed"
    )

    service._grants.inspect("grant-1").project_id = "other-project"
    with pytest.raises(ValueError, match="planning scope"):
        service.get(_request(root, require_active_grant=False))

    service = _service(
        registered_root=root,
        requested_root=root,
        grant_state="expired",
    )
    with pytest.raises(ValueError, match="valid or successfully closed"):
        service.get(_request(root, require_active_grant=False))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"same_repository": False}, "linked worktree"),
        ({"remote_url": "https://example.invalid/other.git"}, "origin does not match"),
        ({"changed_paths": ("outside.txt",)}, "outside the approved scope"),
        ({"packet": None}, "no immutable"),
        (
            {"packet": {"artifact_type": "CopilotEditPacket", "project_id": "other"}},
            "outside requested Project",
        ),
    ],
)
def test_context_fails_closed_on_workspace_or_artifact_drift(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    registered = tmp_path / "registered"
    requested = tmp_path / "requested"
    registered.mkdir()
    requested.mkdir()
    service = _service(
        registered_root=registered,
        requested_root=requested,
        **changes,  # type: ignore[arg-type]
    )

    with pytest.raises((ValueError, RuntimeError), match=message):
        service.get(_request(requested))
