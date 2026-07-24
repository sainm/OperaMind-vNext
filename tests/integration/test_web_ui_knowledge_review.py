from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from operamind.application.ui_knowledge_review import (
    UiKnowledgeReviewRequest,
    UiKnowledgeReviewService,
)
from operamind.domain import (
    BrowserLocator,
    LocatorStrategy,
    UiKnowledgeSnapshot,
    UiKnowledgeTarget,
    UiLocatorCandidate,
    UiLocatorObservationStatus,
    UiRuntimeLocatorObservation,
    UiRuntimeObservationEvidence,
    UiRuntimeObservationResult,
    runtime_observation_id,
)
from operamind.infrastructure.postgres import (
    MigrationCatalog,
    MigrationRunner,
    ProfileRepository,
    UiKnowledgeRepository,
    UiKnowledgeReviewQueryRepository,
    UiLocatorObservationRepository,
)
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_ui_knowledge_review_queue_preserves_observation_evidence_and_versions() -> None:
    assert DATABASE_URL is not None
    schema_name = f"ui_knowledge_web_review_{uuid4().hex}"
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
            cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        _seed_deployment(connection)
        knowledge = UiKnowledgeRepository(connection)
        source = _snapshot("knowledge-source", "1.0.0", "approved", "qa-source")
        assert knowledge.store(source).created
        observation_repository = UiLocatorObservationRepository(connection)
        for suffix, status, count in (
            ("approved", UiLocatorObservationStatus.UNIQUE_VISIBLE, 1),
            ("rejected", UiLocatorObservationStatus.AMBIGUOUS, 2),
        ):
            draft = _snapshot(f"knowledge-draft-{suffix}", f"1.{count}.0-draft", "draft")
            assert knowledge.store(draft).created
            run_id = f"knowledge-observation-{suffix}"
            observation_id = runtime_observation_id(
                run_id, "expense.status-filter", "status-filter-candidate"
            )
            result = UiRuntimeObservationResult(
                status="completed",
                snapshot=draft,
                observations=(
                    UiRuntimeLocatorObservation(
                        observation_id=observation_id,
                        target_ref="expense.status-filter",
                        candidate_id="status-filter-candidate",
                        locator=BrowserLocator(
                            strategy=LocatorStrategy.TEST_ID,
                            value="status-filter",
                        ),
                        status=status,
                        match_count=count,
                        visible_count=count,
                        discovered=False,
                    ),
                ),
                issues=(),
                evidence=(
                    UiRuntimeObservationEvidence(
                        evidence_id=f"knowledge-evidence-{suffix}",
                        observation_id=observation_id,
                        target_ref="expense.status-filter",
                        evidence_ref=(
                            f"evidence://visiondemo/{run_id}/knowledge-evidence-{suffix}"
                        ),
                        content_digest=("a" if suffix == "approved" else "b") * 64,
                    ),
                ),
            )
            assert observation_repository.store(
                run_id=run_id,
                source=source,
                result=result,
            ).created

        query = UiKnowledgeReviewQueryRepository(connection)
        queue = query.review_queue(project_id="visiondemo")
        assert queue["draft_count"] == 2
        drafts = {
            str(item["snapshot_id"]): item for item in cast(list[dict[str, Any]], queue["drafts"])
        }
        approved_target = drafts["knowledge-draft-approved"]["targets"][0]
        assert approved_target["business_name"] == "ステータス絞り込み"
        assert approved_target["candidates"][0]["observation"] == {
            "status": "unique_visible",
            "match_count": 1,
            "visible_count": 1,
            "discovered": False,
            "observed_at": approved_target["candidates"][0]["observation"]["observed_at"],
        }
        assert approved_target["evidence"]["content_digest"] == "a" * 64

        profile_catalog = ProfileCatalog.load(ROOT / "profiles")
        reviewer = UiKnowledgeReviewService(
            connection=connection,
            profiles=profile_catalog,
        )
        approved = reviewer.review(
            UiKnowledgeReviewRequest(
                project_id="visiondemo",
                source_snapshot_id="knowledge-draft-approved",
                result_snapshot_id="knowledge-reviewed-approved",
                result_snapshot_version="1.1.0",
                review_event_id="knowledge-review-approved",
                decision="approved",
                reviewed_by="qa-user",
                activate=True,
                reason="一意性、表示状態、証跡を確認しました",
            )
        )
        rejected = reviewer.review(
            UiKnowledgeReviewRequest(
                project_id="visiondemo",
                source_snapshot_id="knowledge-draft-rejected",
                result_snapshot_id="knowledge-reviewed-rejected",
                result_snapshot_version="1.2.0-rejected",
                review_event_id="knowledge-review-rejected",
                decision="rejected",
                reviewed_by="qa-user",
                reason="候補が複数要素に一致します",
            )
        )
        assert approved.snapshot.activate
        assert rejected.snapshot.review_status == "rejected"
        after = query.review_queue(project_id="visiondemo")
        assert after["draft_count"] == 0
        versions = {
            str(item["snapshot_id"]): item for item in cast(list[dict[str, Any]], after["versions"])
        }
        assert versions["knowledge-reviewed-approved"]["reason"] == (
            "一意性、表示状態、証跡を確認しました"
        )
        assert versions["knowledge-reviewed-rejected"]["review_status"] == "rejected"
        active_locator = ProfileRepository(connection, profile_catalog).get_active(
            project_id="visiondemo",
            binding_key="ui-locator:visiondemo-local:visiondemo-revision",
        )
        assert active_locator is not None
        assert active_locator.profile["profile_type"] == "UiLocatorProfile"
        assert active_locator.profile["ui_knowledge_snapshot_id"] == "knowledge-reviewed-approved"
        assert active_locator.profile["target_refs"] == ["expense.status-filter"]

        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
        connection.commit()


def _seed_deployment(connection: psycopg.Connection[Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES ('visiondemo', 'VisionDemo')"
        )
        cursor.execute(
            """
            INSERT INTO ui_environments (
                environment_id, project_id, base_url, status
            ) VALUES (
                'visiondemo-local', 'visiondemo', 'http://127.0.0.1:8080', 'active'
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO ui_deployments (
                deployment_revision, environment_id, project_id,
                repository_revision, status
            ) VALUES (
                'visiondemo-revision', 'visiondemo-local', 'visiondemo',
                'repository-revision', 'ready'
            )
            """
        )


def _snapshot(
    snapshot_id: str,
    version: str,
    status: str,
    reviewed_by: str | None = None,
) -> UiKnowledgeSnapshot:
    return UiKnowledgeSnapshot(
        snapshot_id=snapshot_id,
        project_id="visiondemo",
        environment_id="visiondemo-local",
        deployment_revision="visiondemo-revision",
        snapshot_version=version,
        review_status=status,
        reviewed_by=reviewed_by,
        targets=(
            UiKnowledgeTarget(
                target_ref="expense.status-filter",
                business_name="ステータス絞り込み",
                screen_name="経費一覧",
                trigger_path="/expenses",
                source_fact_refs=("fact-status-filter",),
                candidates=(
                    UiLocatorCandidate(
                        candidate_id="status-filter-candidate",
                        locator=BrowserLocator(
                            strategy=LocatorStrategy.TEST_ID,
                            value="status-filter",
                        ),
                        priority=1,
                        reliability_score=0.98,
                        source="runtime_observation",
                    ),
                ),
            ),
        ),
    )
