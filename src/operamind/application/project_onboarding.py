"""Recoverable, staged Project Onboarding and capability preflight."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast
from uuid import uuid4

import psycopg
from psycopg import Connection

from operamind.application.document_profile_learning import DocumentProfileLearningService
from operamind.application.project_document_baseline import ProjectDocumentBaselineService
from operamind.contracts import ContractCatalog
from operamind.infrastructure.embeddings import OpenAICompatibleEmbeddingProvider
from operamind.infrastructure.postgres import (
    ProjectOnboardingClaim,
    ProjectOnboardingRecord,
    ProjectOnboardingRepository,
    WebControlPlaneRepository,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.profiles import ProfileCatalog

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectOnboardingIteration:
    outcome: str
    onboarding_run_id: str | None = None
    stage: str | None = None


class ProjectOnboardingService:
    """Queue, inspect, and advance one Project Onboarding stage."""

    def __init__(self, *, connection: Connection[Any], repository_root: Path) -> None:
        self._connection = connection
        self._root = repository_root.resolve()
        self._contracts = ContractCatalog.load(self._root / "contracts")
        self._projects = WebControlPlaneRepository(connection, self._contracts)
        self._runs = ProjectOnboardingRepository(connection)

    def enqueue(
        self,
        *,
        project_id: str,
        action: str,
        actor: str,
    ) -> ProjectOnboardingRecord:
        configuration = self._projects.project_configuration(project_id)
        settings_revision = cast(int, configuration["settings_revision"])
        if action not in {"initialize", "rescan", "reindex", "relearn"}:
            raise ValueError("Unsupported Project Onboarding action")
        latest = self._runs.latest(project_id)
        if (
            latest is not None
            and latest.settings_revision == settings_revision
            and latest.status in {"queued", "running", "waiting_for_profile"}
        ):
            raise ValueError("現在の Project Onboarding が完了するまで再実行できません")
        snapshot_id: str | None = None
        document_count: int | None = None
        if action == "reindex":
            ready = self._runs.latest_ready(project_id)
            if ready is None or ready.settings_revision != settings_revision:
                raise ValueError(
                    "RAG 再構築には現在の Project 設定で ready の "
                    "Document Snapshot が必要です"
                )
            snapshot_id = ready.document_snapshot_id
            document_count = ready.document_count
        return self._runs.enqueue(
            onboarding_run_id=f"project-onboarding-{uuid4().hex}",
            project_id=project_id,
            settings_revision=settings_revision,
            requested_action=action,  # type: ignore[arg-type]
            requested_by=actor,
            document_snapshot_id=snapshot_id,
            document_count=document_count,
        )

    def latest(self, project_id: str) -> dict[str, object] | None:
        record = self._runs.latest(project_id)
        return record.public_view() if record is not None else None

    def retry(self, *, project_id: str, actor: str) -> dict[str, object]:
        record = self._runs.latest(project_id)
        if record is None:
            raise ValueError("Project Onboarding run does not exist")
        return self._runs.retry(
            onboarding_run_id=record.onboarding_run_id,
            actor=actor,
        ).public_view()

    def preflight(self, project_id: str) -> dict[str, object]:
        configuration = self._projects.project_configuration(project_id)
        workspace = Path(str(configuration["workspace_root"]))
        document_roots = tuple(
            Path(str(root)) for root in cast(list[object], configuration["document_roots"])
        )
        capabilities: list[dict[str, object]] = []

        workspace_ready = _is_git_workspace(workspace)
        capabilities.append(
            {
                "capability": "workspace_baseline",
                "status": "ready" if workspace_ready else "blocked",
                "detail": str(workspace),
            }
        )
        try:
            learning_service = DocumentProfileLearningService(
                connection=self._connection,
                repository_root=self._root,
            )
            structure = learning_service.extract_structure(
                project_id=project_id,
                document_roots=document_roots,
            )
            learning = learning_service.latest(project_id)
            learning_ready = bool(
                learning
                and learning.get("status") == "confirmed"
                and learning.get("source_structure_digest") == structure.digest
            )
            discovery_summary = {
                "status": "ready" if learning_ready else "learning_required",
                "document_count": structure.sample_count,
                "documents": cast(list[object], structure.payload["samples"]),
                "ignored_documents": [],
                "review_required": (
                    [] if learning_ready else ["Project 専用の設計書学習が必要です"]
                ),
            }
        except (OSError, ValueError) as error:
            discovery_summary = {
                "status": "blocked",
                "document_count": 0,
                "documents": [],
                "ignored_documents": [],
                "review_required": [str(error)],
            }
        capabilities.append(
            {
                "capability": "document_profiles",
                "status": (
                    "ready" if discovery_summary["status"] == "ready" else "blocked"
                ),
                "detail": f"{discovery_summary['document_count']} XLSX/DOCX",
            }
        )

        embedding_detail: str
        try:
            profile = _load_object(self._root / "profiles" / "embedding-profile.example.json")
            ProfileCatalog.load(self._root / "profiles").validate_profile(profile)
            probe = OpenAICompatibleEmbeddingProvider.from_profile(profile).probe()
            embedding_status = "ready"
            embedding_detail = f"{probe.model} / {probe.dimensions} dimensions"
        except (OSError, ValueError) as error:
            embedding_status = "blocked"
            embedding_detail = str(error)
        capabilities.append(
            {
                "capability": "embedding_provider",
                "status": embedding_status,
                "detail": embedding_detail,
            }
        )

        test_base_url = configuration.get("test_base_url")
        browser = _browser_capability()
        capabilities.append(
            {
                "capability": "ui_test_target",
                "status": "ready" if test_base_url and browser else "optional",
                "detail": (
                    f"{test_base_url} / {browser}"
                    if test_base_url and browser
                    else "UI URL または Chrome/Edge は後から設定できます"
                ),
            }
        )
        blocking = [str(item["capability"]) for item in capabilities if item["status"] == "blocked"]
        return {
            "project_id": project_id,
            "settings_revision": configuration["settings_revision"],
            "status": "ready" if not blocking else "blocked",
            "blocking_capabilities": blocking,
            "capabilities": capabilities,
            "document_discovery": discovery_summary,
        }

    def advance_one(self, *, owner: str) -> ProjectOnboardingIteration:
        claim = self._runs.claim_next(owner=owner)
        if claim is None:
            return ProjectOnboardingIteration(outcome="idle")
        record = claim.record
        stop_heartbeat = Event()
        lease_lost = Event()
        heartbeat = Thread(
            target=_heartbeat_onboarding,
            args=(self._connection.info.dsn, claim, stop_heartbeat, lease_lost),
            name=f"operamind-project-onboarding-heartbeat-{record.onboarding_run_id[-12:]}",
            daemon=True,
        )
        heartbeat.start()
        failure: Exception | None = None
        waiting_for_profile = False
        try:
            configuration = self._projects.project_configuration(record.project_id)
            document_roots = tuple(
                Path(str(root))
                for root in cast(list[object], configuration["document_roots"])
            )
            baseline = ProjectDocumentBaselineService(
                connection=self._connection,
                repository_root=self._root,
            )
            if record.current_stage == "discover":
                learning_service = DocumentProfileLearningService(
                    connection=self._connection,
                    repository_root=self._root,
                )
                learning, already_confirmed = learning_service.ensure_task(
                    project_id=record.project_id,
                    onboarding_run_id=record.onboarding_run_id,
                    settings_revision=record.settings_revision,
                    document_roots=document_roots,
                    actor=record.requested_by,
                    instruction=(
                        "現在の設計書構造を再学習してください"
                        if record.requested_action == "relearn"
                        else None
                    ),
                    force=record.requested_action == "relearn",
                )
                if already_confirmed:
                    self._runs.advance(claim=claim, next_stage="documents")
                else:
                    self._runs.wait_for_learning(
                        claim=claim,
                        learning_run_id=learning.learning_run_id,
                    )
                    waiting_for_profile = True
            elif record.current_stage == "documents":
                snapshot = baseline.store_documents(
                    project_id=record.project_id,
                    document_roots=document_roots,
                    actor=record.requested_by,
                )
                self._runs.advance(
                    claim=claim,
                    next_stage="index",
                    document_snapshot_id=snapshot.snapshot_id,
                    document_count=snapshot.document_count,
                )
            elif record.current_stage == "index":
                if record.document_snapshot_id is None or record.document_count is None:
                    raise ValueError("Project Onboarding index stage has no Document Snapshot")
                result = baseline.build_index(
                    project_id=record.project_id,
                    snapshot_id=record.document_snapshot_id,
                    document_count=record.document_count,
                    actor=record.requested_by,
                    build_nonce=f"{record.onboarding_run_id}:{record.attempt_count}",
                )
                self._runs.advance(
                    claim=claim,
                    next_stage="complete",
                    search_index_build_id=result.index_build_id,
                    generated_vector_count=result.generated_vector_count,
                )
            else:
                raise ValueError(f"Unsupported Project Onboarding stage: {record.current_stage}")
        except Exception as error:
            failure = error
            LOGGER.exception(
                "Project Onboarding stage failed: %s/%s",
                record.onboarding_run_id,
                record.current_stage,
            )
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=5)
        if failure is not None:
            try:
                self._runs.fail(claim=claim, reason=str(failure))
            except PersistenceConflictError:
                lease_lost.set()
                LOGGER.info(
                    "Project Onboarding stage lease was superseded: %s",
                    record.onboarding_run_id,
                )
            return ProjectOnboardingIteration(
                outcome="superseded" if lease_lost.is_set() else "failed",
                onboarding_run_id=record.onboarding_run_id,
                stage=record.current_stage,
            )
        if waiting_for_profile:
            return ProjectOnboardingIteration(
                outcome="waiting_for_profile",
                onboarding_run_id=record.onboarding_run_id,
                stage="learn",
            )
        return ProjectOnboardingIteration(
            outcome="advanced",
            onboarding_run_id=record.onboarding_run_id,
            stage=record.current_stage,
        )


@dataclass(frozen=True, slots=True)
class ProjectOnboardingCoordinator:
    database_url: str
    repository_root: Path

    def run_once(self) -> ProjectOnboardingIteration:
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            return ProjectOnboardingService(
                connection=connection,
                repository_root=self.repository_root,
            ).advance_one(owner=f"project-onboarding:{uuid4().hex}")

    def run_forever(self, *, stop_event: Event, poll_seconds: float) -> None:
        if not 0.1 <= poll_seconds <= 60:
            raise ValueError("Project Onboarding poll_seconds must be between 0.1 and 60")
        while not stop_event.is_set():
            try:
                self.run_once()
            except (OSError, RuntimeError, psycopg.Error, ValueError):
                LOGGER.exception("Project Onboarding coordinator iteration failed")
            stop_event.wait(poll_seconds)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return value


def _browser_capability() -> str | None:
    configured = os.getenv("OPERAMIND_PLAYWRIGHT_CHANNEL", "").strip()
    if configured:
        return configured
    for executable in ("msedge", "chrome", "google-chrome", "chromium"):
        if shutil.which(executable):
            return executable
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    mac_edge = Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    if mac_chrome.is_file():
        return "chrome"
    if mac_edge.is_file():
        return "msedge"
    return None


def _is_git_workspace(workspace: Path) -> bool:
    """Accept normal repositories and linked worktrees during preflight."""

    if not workspace.is_dir():
        return False
    try:
        result = subprocess.run(
            ("git", "-C", str(workspace), "rev-parse", "--show-toplevel"),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0 or not result.stdout.strip():
        return False
    try:
        return Path(result.stdout.strip()).resolve(strict=True) == workspace.resolve(strict=True)
    except OSError:
        return False


def _heartbeat_onboarding(
    database_url: str,
    claim: ProjectOnboardingClaim,
    stop_event: Event,
    lease_lost: Event,
) -> None:
    while not stop_event.wait(60):
        try:
            with psycopg.connect(database_url, autocommit=True) as connection:
                if not ProjectOnboardingRepository(connection).heartbeat(claim=claim):
                    lease_lost.set()
                    return
        except psycopg.Error:
            LOGGER.exception(
                "Project Onboarding heartbeat failed: %s",
                claim.record.onboarding_run_id,
            )
