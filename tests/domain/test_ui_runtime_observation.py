import pytest

from operamind.domain import (
    BrowserLocator,
    LocatorStrategy,
    UiKnowledgeSnapshot,
    UiKnowledgeTarget,
    UiLocatorCandidate,
    UiLocatorObservationStatus,
    UiRuntimeLocatorObservation,
    UiRuntimeObservationMerger,
    runtime_candidate_id,
    runtime_observation_id,
)


def _source() -> UiKnowledgeSnapshot:
    locator = BrowserLocator(strategy=LocatorStrategy.LABEL, value="ステータス")
    return UiKnowledgeSnapshot(
        snapshot_id="ui-knowledge-source",
        project_id="visiondemo",
        environment_id="staging",
        deployment_revision="deploy-001",
        snapshot_version="1.0.0",
        review_status="approved",
        reviewed_by="qa@example.com",
        activate=True,
        targets=(
            UiKnowledgeTarget(
                target_ref="expense.status-filter",
                business_name="ステータス絞り込み",
                screen_name="経費一覧",
                trigger_path="/expenses",
                source_fact_refs=("fact-status",),
                candidates=(
                    UiLocatorCandidate(
                        candidate_id=runtime_candidate_id("expense.status-filter", locator),
                        locator=locator,
                        priority=1,
                        reliability_score=0.90,
                        source="canonical_screen_element_proposal",
                    ),
                ),
            ),
        ),
    )


def test_runtime_observation_creates_new_draft_and_enriches_candidates() -> None:
    source = _source()
    label = source.targets[0].candidates[0]
    test_id_locator = BrowserLocator(
        strategy=LocatorStrategy.TEST_ID,
        value="status-filter",
    )
    test_id = runtime_candidate_id("expense.status-filter", test_id_locator)
    observations = (
        UiRuntimeLocatorObservation(
            observation_id=runtime_observation_id(
                "observation-run-001", "expense.status-filter", label.candidate_id
            ),
            target_ref="expense.status-filter",
            candidate_id=label.candidate_id,
            locator=label.locator,
            status=UiLocatorObservationStatus.UNIQUE_VISIBLE,
            match_count=1,
            visible_count=1,
            discovered=False,
        ),
        UiRuntimeLocatorObservation(
            observation_id=runtime_observation_id(
                "observation-run-001", "expense.status-filter", test_id
            ),
            target_ref="expense.status-filter",
            candidate_id=test_id,
            locator=test_id_locator,
            status=UiLocatorObservationStatus.UNIQUE_VISIBLE,
            match_count=1,
            visible_count=1,
            discovered=True,
        ),
    )

    result = UiRuntimeObservationMerger().merge(
        source=source,
        observations=observations,
        result_snapshot_id="ui-knowledge-observed",
        result_snapshot_version="1.1.0-draft",
    )

    assert result.review_status == "draft"
    assert result.reviewed_by is None
    assert not result.activate
    assert source.review_status == "approved"
    candidates = result.targets[0].candidates
    assert [item.locator.strategy for item in candidates] == [
        LocatorStrategy.LABEL,
        LocatorStrategy.TEST_ID,
    ]
    assert candidates[0].reliability_score == 0.98
    assert candidates[0].source.endswith("+runtime_verified")
    assert candidates[1].reliability_score == 0.97
    assert candidates[1].source == "runtime_observation"


def test_runtime_observation_rejects_inconsistent_match_status() -> None:
    locator = BrowserLocator(strategy=LocatorStrategy.TEXT, value="Status")

    with pytest.raises(ValueError, match="inconsistent"):
        UiRuntimeLocatorObservation(
            observation_id="observation-invalid",
            target_ref="expense.status-filter",
            candidate_id="candidate-invalid",
            locator=locator,
            status=UiLocatorObservationStatus.UNIQUE_VISIBLE,
            match_count=2,
            visible_count=2,
            discovered=False,
        )


def test_runtime_observation_cannot_mutate_source_snapshot_identity() -> None:
    with pytest.raises(ValueError, match="new UI Knowledge Snapshot"):
        UiRuntimeObservationMerger().merge(
            source=_source(),
            observations=(),
            result_snapshot_id="ui-knowledge-source",
            result_snapshot_version="1.1.0",
        )
