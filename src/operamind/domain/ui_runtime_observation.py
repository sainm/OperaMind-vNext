"""Append-only runtime Locator observations and draft UI Knowledge enrichment."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from operamind.domain.ui_execution import BrowserLocator, LocatorStrategy
from operamind.domain.ui_knowledge import (
    UiKnowledgeSnapshot,
    UiKnowledgeTarget,
    UiLocatorCandidate,
)


class UiLocatorObservationStatus(StrEnum):
    UNIQUE_VISIBLE = "unique_visible"
    NOT_FOUND = "not_found"
    HIDDEN = "hidden"
    AMBIGUOUS = "ambiguous"
    NAVIGATION_FAILED = "navigation_failed"


@dataclass(frozen=True, slots=True)
class UiRuntimeLocatorObservation:
    observation_id: str
    target_ref: str
    candidate_id: str
    locator: BrowserLocator
    status: UiLocatorObservationStatus
    match_count: int
    visible_count: int
    discovered: bool

    def __post_init__(self) -> None:
        if any(
            not value.strip() for value in (self.observation_id, self.target_ref, self.candidate_id)
        ):
            raise ValueError("Runtime Locator Observation identity must not be blank")
        if self.locator.target_ref is not None:
            raise ValueError("Runtime Observation requires a concrete Locator")
        if self.match_count < 0 or not 0 <= self.visible_count <= self.match_count:
            raise ValueError("Runtime Observation match counts are invalid")
        expected = {
            UiLocatorObservationStatus.UNIQUE_VISIBLE: self.match_count == 1
            and self.visible_count == 1,
            UiLocatorObservationStatus.NOT_FOUND: self.match_count == 0 and self.visible_count == 0,
            UiLocatorObservationStatus.HIDDEN: self.match_count == 1 and self.visible_count == 0,
            UiLocatorObservationStatus.AMBIGUOUS: self.match_count > 1,
            UiLocatorObservationStatus.NAVIGATION_FAILED: self.match_count == 0
            and self.visible_count == 0,
        }[self.status]
        if not expected:
            raise ValueError("Runtime Observation status is inconsistent with match counts")

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "target_ref": self.target_ref,
            "candidate_id": self.candidate_id,
            "locator": self.locator.to_dict(),
            "status": self.status.value,
            "match_count": self.match_count,
            "visible_count": self.visible_count,
            "discovered": self.discovered,
        }


@dataclass(frozen=True, slots=True)
class UiRuntimeObservationEvidence:
    evidence_id: str
    observation_id: str
    target_ref: str
    evidence_ref: str
    content_digest: str
    sanitized: bool = True

    def __post_init__(self) -> None:
        values = (
            self.evidence_id,
            self.observation_id,
            self.target_ref,
            self.evidence_ref,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Runtime Observation Evidence fields must not be blank")
        if len(self.content_digest) != 64 or any(
            value not in "0123456789abcdef" for value in self.content_digest
        ):
            raise ValueError("Runtime Observation Evidence digest must be SHA-256")

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "observation_id": self.observation_id,
            "target_ref": self.target_ref,
            "evidence_ref": self.evidence_ref,
            "content_digest": self.content_digest,
            "sanitized": self.sanitized,
        }


@dataclass(frozen=True, slots=True)
class UiRuntimeObservationIssue:
    target_ref: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.target_ref, self.code, self.message)):
            raise ValueError("Runtime Observation issue fields must not be blank")

    def to_dict(self) -> dict[str, str]:
        return {"target_ref": self.target_ref, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class UiRuntimeObservationResult:
    status: str
    snapshot: UiKnowledgeSnapshot | None
    observations: tuple[UiRuntimeLocatorObservation, ...]
    issues: tuple[UiRuntimeObservationIssue, ...]
    evidence: tuple[UiRuntimeObservationEvidence, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"completed", "partial", "blocked"}:
            raise ValueError("Runtime Observation result status is invalid")
        if self.status == "blocked" and self.snapshot is not None:
            raise ValueError("Blocked Runtime Observation must not publish a draft Snapshot")
        if self.status != "blocked" and self.snapshot is None:
            raise ValueError("Completed Runtime Observation requires a draft Snapshot")


class UiRuntimeObservationMerger:
    """Create a new draft Snapshot; never mutate or approve the observed source."""

    def merge(
        self,
        *,
        source: UiKnowledgeSnapshot,
        observations: tuple[UiRuntimeLocatorObservation, ...],
        result_snapshot_id: str,
        result_snapshot_version: str,
    ) -> UiKnowledgeSnapshot:
        if any(not value.strip() for value in (result_snapshot_id, result_snapshot_version)):
            raise ValueError("Runtime Observation result Snapshot identity must not be blank")
        if result_snapshot_id == source.snapshot_id:
            raise ValueError("Runtime Observation must create a new UI Knowledge Snapshot")
        known_targets = {target.target_ref for target in source.targets}
        if any(item.target_ref not in known_targets for item in observations):
            raise ValueError("Runtime Observation contains a target outside source UI Knowledge")
        by_target: dict[str, list[UiRuntimeLocatorObservation]] = {}
        for observation in observations:
            by_target.setdefault(observation.target_ref, []).append(observation)
        targets = tuple(
            _merge_target(target, tuple(by_target.get(target.target_ref, ())))
            for target in source.targets
        )
        return UiKnowledgeSnapshot(
            snapshot_id=result_snapshot_id,
            project_id=source.project_id,
            environment_id=source.environment_id,
            deployment_revision=source.deployment_revision,
            snapshot_version=result_snapshot_version,
            review_status="draft",
            reviewed_by=None,
            targets=targets,
        )


def runtime_candidate_id(target_ref: str, locator: BrowserLocator) -> str:
    if locator.target_ref is not None or locator.strategy is None or locator.value is None:
        raise ValueError("Runtime candidate ID requires a concrete Locator")
    identity = (
        f"{target_ref}\0{locator.strategy.value}\0{locator.value}\0"
        f"{locator.name or ''}\0{locator.exact}"
    )
    return f"ui-locator-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def runtime_observation_id(
    run_id: str,
    target_ref: str,
    candidate_id: str,
) -> str:
    identity = f"{run_id}\0{target_ref}\0{candidate_id}"
    return f"ui-observation-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def _merge_target(
    target: UiKnowledgeTarget,
    observations: tuple[UiRuntimeLocatorObservation, ...],
) -> UiKnowledgeTarget:
    candidates = {_locator_key(item.locator): item for item in target.candidates}
    for observation in observations:
        if observation.status is not UiLocatorObservationStatus.UNIQUE_VISIBLE:
            continue
        key = _locator_key(observation.locator)
        existing = candidates.get(key)
        reliability = _runtime_reliability(observation.locator.strategy)
        candidates[key] = UiLocatorCandidate(
            candidate_id=observation.candidate_id,
            locator=observation.locator,
            priority=existing.priority if existing is not None else 999,
            reliability_score=max(
                reliability,
                existing.reliability_score if existing is not None else 0.0,
            ),
            source=_runtime_source(existing.source if existing is not None else None),
        )
    ordered = sorted(
        candidates.values(),
        key=lambda item: (
            _strategy_rank(item.locator.strategy),
            item.priority,
            item.locator.value or "",
            item.locator.name or "",
        ),
    )
    reranked = tuple(
        UiLocatorCandidate(
            candidate_id=item.candidate_id,
            locator=item.locator,
            priority=index,
            reliability_score=item.reliability_score,
            source=item.source,
        )
        for index, item in enumerate(ordered, start=1)
    )
    return UiKnowledgeTarget(
        target_ref=target.target_ref,
        business_name=target.business_name,
        screen_name=target.screen_name,
        trigger_path=target.trigger_path,
        source_fact_refs=target.source_fact_refs,
        candidates=reranked,
    )


def _locator_key(
    locator: BrowserLocator,
) -> tuple[LocatorStrategy | None, str | None, str | None, bool]:
    return (locator.strategy, locator.value, locator.name, locator.exact)


def _strategy_rank(strategy: LocatorStrategy | None) -> int:
    return {
        LocatorStrategy.ROLE: 1,
        LocatorStrategy.LABEL: 2,
        LocatorStrategy.TEXT: 3,
        LocatorStrategy.TEST_ID: 4,
        LocatorStrategy.PLACEHOLDER: 5,
        LocatorStrategy.CSS: 6,
        None: 99,
    }[strategy]


def _runtime_reliability(strategy: LocatorStrategy | None) -> float:
    return {
        LocatorStrategy.ROLE: 0.99,
        LocatorStrategy.LABEL: 0.98,
        LocatorStrategy.TEXT: 0.90,
        LocatorStrategy.TEST_ID: 0.97,
        LocatorStrategy.PLACEHOLDER: 0.95,
        LocatorStrategy.CSS: 0.85,
        None: 0.0,
    }[strategy]


def _runtime_source(existing: str | None) -> str:
    if existing is None:
        return "runtime_observation"
    if "runtime_verified" in existing:
        return existing
    return f"{existing}+runtime_verified"
