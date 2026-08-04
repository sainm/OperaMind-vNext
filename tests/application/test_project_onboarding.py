import subprocess
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openpyxl import Workbook

from operamind.application import project_onboarding as onboarding_module
from operamind.application.project_onboarding import ProjectOnboardingService

ROOT = Path(__file__).parents[2]


def _service() -> ProjectOnboardingService:
    return ProjectOnboardingService(
        connection=cast(Any, object()),
        repository_root=ROOT,
    )


def _write_screen_design(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Screen Items"
    sheet.append(["Screen ID", "Element ID", "Type", "Default Value", "Notes"])
    sheet.append(["customer-list", "status-filter", "select", "all", "filter"])
    workbook.save(path)


def test_preflight_reports_real_workspace_document_embedding_and_browser_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "code"
    documents = tmp_path / "documents"
    workspace.mkdir()
    documents.mkdir()
    subprocess.run(("git", "-C", str(workspace), "init", "--quiet"), check=True)
    _write_screen_design(documents / "customer-ui-specification.xlsx")
    service = _service()
    service._projects = cast(
        Any,
        SimpleNamespace(
            project_configuration=lambda _project_id: {
                "settings_revision": 4,
                "workspace_root": str(workspace),
                "document_roots": [str(documents)],
                "test_base_url": "http://127.0.0.1:8080/app",
            }
        ),
    )
    monkeypatch.setattr(
        onboarding_module.OpenAICompatibleEmbeddingProvider,
        "from_profile",
        lambda _profile: SimpleNamespace(
            probe=lambda: SimpleNamespace(model="local-embedding", dimensions=768)
        ),
    )
    monkeypatch.setattr(onboarding_module, "_browser_capability", lambda: "chrome")
    structure = SimpleNamespace(
        digest="a" * 64,
        sample_count=1,
        payload={"samples": [{"logical_name": "customer-ui-specification.xlsx"}]},
    )
    monkeypatch.setattr(
        onboarding_module,
        "DocumentProfileLearningService",
        lambda **_values: SimpleNamespace(
            extract_structure=lambda **_arguments: structure,
            latest=lambda _project_id: {
                "status": "confirmed",
                "source_structure_digest": structure.digest,
            },
        ),
    )

    result = service.preflight("profile-project")

    assert result["status"] == "ready"
    assert result["blocking_capabilities"] == []
    assert result["settings_revision"] == 4
    assert result["document_discovery"]["document_count"] == 1
    assert [item["status"] for item in result["capabilities"]] == [
        "ready",
        "ready",
        "ready",
        "ready",
    ]


def test_preflight_accepts_git_linked_worktree_marker(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "-C", str(repository), "init", "--quiet"), check=True)
    subprocess.run(
        ("git", "-C", str(repository), "config", "user.email", "tests@example.invalid"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(repository), "config", "user.name", "OperaMind Tests"),
        check=True,
    )
    (repository / "README.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(repository), "add", "README.txt"), check=True)
    subprocess.run(
        ("git", "-C", str(repository), "commit", "--quiet", "-m", "baseline"),
        check=True,
    )
    linked = tmp_path / "linked"
    subprocess.run(
        ("git", "-C", str(repository), "worktree", "add", "--quiet", str(linked)),
        check=True,
    )

    assert (linked / ".git").is_file()
    assert onboarding_module._is_git_workspace(linked)


def test_preflight_fails_closed_without_workspace_documents_or_embedding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    service._projects = cast(
        Any,
        SimpleNamespace(
            project_configuration=lambda _project_id: {
                "settings_revision": 1,
                "workspace_root": str(tmp_path / "missing-code"),
                "document_roots": [str(tmp_path / "missing-documents")],
                "test_base_url": None,
            }
        ),
    )

    def unavailable(_profile: object) -> object:
        raise ValueError("embedding unavailable")

    monkeypatch.setattr(
        onboarding_module.OpenAICompatibleEmbeddingProvider,
        "from_profile",
        unavailable,
    )
    monkeypatch.setattr(onboarding_module, "_browser_capability", lambda: None)

    result = service.preflight("blocked-project")

    assert result["status"] == "blocked"
    assert result["blocking_capabilities"] == [
        "workspace_baseline",
        "document_profiles",
        "embedding_provider",
    ]
    assert result["document_discovery"]["status"] == "blocked"
    assert result["capabilities"][-1]["status"] == "optional"


def test_enqueue_reindex_reuses_latest_ready_snapshot_and_retry_is_bounded() -> None:
    service = _service()
    ready = SimpleNamespace(
        settings_revision=7,
        document_snapshot_id="snapshot-ready",
        document_count=5,
    )
    captured: dict[str, object] = {}

    class Runs:
        def latest_ready(self, _project_id: str) -> SimpleNamespace:
            return ready

        def enqueue(self, **values: object) -> str:
            captured.update(values)
            return "queued"

        def latest(self, _project_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                onboarding_run_id="run-failed",
                settings_revision=7,
                status="failed",
            )

        def retry(self, **values: object) -> SimpleNamespace:
            captured.update(values)
            return SimpleNamespace(public_view=lambda: {"status": "queued"})

    service._projects = cast(
        Any,
        SimpleNamespace(
            project_configuration=lambda _project_id: {"settings_revision": 7}
        ),
    )
    service._runs = cast(Any, Runs())

    assert service.enqueue(project_id="project", action="reindex", actor="operator") == "queued"
    assert captured["settings_revision"] == 7
    assert captured["document_snapshot_id"] == "snapshot-ready"
    assert captured["document_count"] == 5
    assert service.retry(project_id="project", actor="operator") == {"status": "queued"}
    assert captured["onboarding_run_id"] == "run-failed"


def test_enqueue_rejects_unknown_action_before_persistence() -> None:
    service = _service()
    service._projects = cast(
        Any,
        SimpleNamespace(project_configuration=lambda _project_id: {"settings_revision": 1}),
    )
    service._runs = cast(Any, SimpleNamespace())

    with pytest.raises(ValueError, match="Unsupported Project Onboarding action"):
        service.enqueue(project_id="project", action="unknown", actor="operator")


@pytest.mark.parametrize("action", ["initialize", "rescan", "reindex", "relearn"])
def test_enqueue_rejects_every_duplicate_active_run(action: str) -> None:
    service = _service()
    service._projects = cast(
        Any,
        SimpleNamespace(project_configuration=lambda _project_id: {"settings_revision": 7}),
    )
    service._runs = cast(
        Any,
        SimpleNamespace(
            latest=lambda _project_id: SimpleNamespace(
                settings_revision=7,
                status="waiting_for_profile",
            )
        ),
    )

    with pytest.raises(ValueError, match="再実行できません"):
        service.enqueue(project_id="project", action=action, actor="operator")


@pytest.mark.parametrize("ready_revision", [None, 0])
def test_missing_or_stale_ready_snapshot_fails_closed(ready_revision: int | None) -> None:
    service = _service()
    service._projects = cast(
        Any,
        SimpleNamespace(project_configuration=lambda _project_id: {"settings_revision": 1}),
    )
    ready = (
        None
        if ready_revision is None
        else SimpleNamespace(
            settings_revision=ready_revision,
            document_snapshot_id="stale-snapshot",
            document_count=1,
        )
    )
    service._runs = cast(
        Any,
        SimpleNamespace(latest=lambda _project_id: None, latest_ready=lambda _project_id: ready),
    )

    with pytest.raises(ValueError, match="現在の Project 設定で ready"):
        service.enqueue(project_id="project", action="reindex", actor="operator")


def test_missing_run_and_empty_queue_fail_closed() -> None:
    service = _service()
    service._runs = cast(
        Any,
        SimpleNamespace(
            latest=lambda _project_id: None,
            claim_next=lambda **_values: None,
        ),
    )

    with pytest.raises(ValueError, match="run does not exist"):
        service.retry(project_id="project", actor="operator")
    assert service.advance_one(owner="worker").outcome == "idle"


def test_browser_capability_prefers_configuration_then_available_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPERAMIND_PLAYWRIGHT_CHANNEL", "msedge")
    assert onboarding_module._browser_capability() == "msedge"

    monkeypatch.delenv("OPERAMIND_PLAYWRIGHT_CHANNEL")
    monkeypatch.setattr(
        onboarding_module.shutil,
        "which",
        lambda executable: "/browser" if executable == "chrome" else None,
    )
    assert onboarding_module._browser_capability() == "chrome"


def test_coordinator_rejects_invalid_poll_interval() -> None:
    coordinator = onboarding_module.ProjectOnboardingCoordinator(
        database_url="postgresql://example.invalid/database",
        repository_root=ROOT,
    )

    with pytest.raises(ValueError, match="poll_seconds"):
        coordinator.run_forever(stop_event=Event(), poll_seconds=0)
