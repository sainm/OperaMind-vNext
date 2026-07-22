"""Align Canonical Snapshots and emit deterministic StructuredChange v1 artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from operamind.domain.canonical_facts import CanonicalFact


class ChangeType(StrEnum):
    """Stable-key alignment outcome."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


class ChangeConfidence(StrEnum):
    """Contract v1 confidence values."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ChangeReviewStatus(StrEnum):
    """Contract v1 review states."""

    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SnapshotFact:
    """A Canonical Fact with identity inside one committed snapshot."""

    fact_ref: str
    fact: CanonicalFact

    def __post_init__(self) -> None:
        if not self.fact_ref.strip():
            raise ValueError("Snapshot fact_ref must not be blank")


@dataclass(frozen=True, slots=True)
class CanonicalSnapshot:
    """A snapshot that requires unique Stable Keys and fact references."""

    snapshot_id: str
    facts: tuple[SnapshotFact, ...]

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip():
            raise ValueError("Canonical snapshot_id must not be blank")
        stable_keys = [item.fact.stable_key for item in self.facts]
        if len(stable_keys) != len(set(stable_keys)):
            raise ValueError("Canonical Snapshot contains duplicate Stable Keys")
        fact_refs = [item.fact_ref for item in self.facts]
        if len(fact_refs) != len(set(fact_refs)):
            raise ValueError("Canonical Snapshot contains duplicate fact_refs")


@dataclass(frozen=True, slots=True)
class FactState:
    """StructuredChange fact state backed by Canonical source references."""

    fact_ref: str
    values: Mapping[str, str]
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.fact_ref.strip():
            raise ValueError("Fact state fact_ref must not be blank")
        if not self.source_refs:
            raise ValueError("Fact state must retain at least one source_ref")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("Fact state source_refs must be unique")
        object.__setattr__(self, "values", MappingProxyType(dict(sorted(self.values.items()))))

    def to_artifact(self) -> dict[str, Any]:
        """Return the Contract v1 fact_state representation."""

        return {
            "fact_ref": self.fact_ref,
            "values": dict(self.values),
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True, slots=True)
class StructuredChange:
    """Domain representation of one Contract v1 StructuredChange."""

    change_id: str
    project_id: str
    source_snapshot_id: str
    target_snapshot_id: str
    stable_key: str
    fact_type: str
    domain: str
    change_type: ChangeType
    before: FactState | None
    after: FactState | None
    summary: str
    source_refs: tuple[str, ...]
    confidence: ChangeConfidence
    review_status: ChangeReviewStatus
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required_strings = (
            self.change_id,
            self.project_id,
            self.source_snapshot_id,
            self.target_snapshot_id,
            self.stable_key,
            self.fact_type,
            self.domain,
            self.summary,
        )
        if any(not value.strip() for value in required_strings):
            raise ValueError("StructuredChange identity and summary fields must not be blank")
        if self.source_snapshot_id == self.target_snapshot_id:
            raise ValueError("StructuredChange requires different source and target snapshots")
        if not self.source_refs or len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("StructuredChange source_refs must be non-empty and unique")
        if len(self.unknowns) != len(set(self.unknowns)):
            raise ValueError("StructuredChange unknowns must be unique")
        if self.change_type is ChangeType.ADDED and (self.before is not None or self.after is None):
            raise ValueError("Added change requires only an after state")
        if self.change_type is ChangeType.MODIFIED and (self.before is None or self.after is None):
            raise ValueError("Modified change requires before and after states")
        if self.change_type is ChangeType.DELETED and (
            self.before is None or self.after is not None
        ):
            raise ValueError("Deleted change requires only a before state")

    def to_artifact(self) -> dict[str, Any]:
        """Serialize without leaking internal dataclasses across the v1 boundary."""

        return {
            "artifact_type": "StructuredChange",
            "schema_version": "v1",
            "change_id": self.change_id,
            "project_id": self.project_id,
            "source_snapshot_id": self.source_snapshot_id,
            "target_snapshot_id": self.target_snapshot_id,
            "stable_key": self.stable_key,
            "fact_type": self.fact_type,
            "domain": self.domain,
            "change_type": self.change_type.value,
            "before": self.before.to_artifact() if self.before is not None else None,
            "after": self.after.to_artifact() if self.after is not None else None,
            "summary": self.summary,
            "source_refs": list(self.source_refs),
            "confidence": self.confidence.value,
            "review_status": self.review_status.value,
            "unknowns": list(self.unknowns),
        }


class StructuredChangeBuilder:
    """Produce semantic changes by aligning facts on Stable Key."""

    def diff(
        self,
        *,
        project_id: str,
        source: CanonicalSnapshot,
        target: CanonicalSnapshot,
        domain: str,
        confidence: ChangeConfidence = ChangeConfidence.HIGH,
        review_status: ChangeReviewStatus = ChangeReviewStatus.NEEDS_REVIEW,
    ) -> tuple[StructuredChange, ...]:
        """Ignore evidence/layout-only changes and emit added, modified, and deleted facts."""

        if not project_id.strip():
            raise ValueError("project_id must not be blank")
        if not domain.strip():
            raise ValueError("domain must not be blank")
        if source.snapshot_id == target.snapshot_id:
            raise ValueError("Source and target snapshot IDs must differ")

        before_by_key = {item.fact.stable_key: item for item in source.facts}
        after_by_key = {item.fact.stable_key: item for item in target.facts}
        changes: list[StructuredChange] = []
        for stable_key in sorted(before_by_key.keys() | after_by_key.keys()):
            before = before_by_key.get(stable_key)
            after = after_by_key.get(stable_key)
            if before is not None and after is not None:
                if before.fact.fact_type != after.fact.fact_type:
                    raise ValueError(f"Stable Key changed fact_type: {stable_key}")
                if dict(before.fact.values) == dict(after.fact.values):
                    continue
                change_type = ChangeType.MODIFIED
            elif before is None:
                change_type = ChangeType.ADDED
            else:
                change_type = ChangeType.DELETED
            changes.append(
                _build_change(
                    project_id=project_id,
                    source=source,
                    target=target,
                    domain=domain,
                    stable_key=stable_key,
                    change_type=change_type,
                    before=before,
                    after=after,
                    confidence=confidence,
                    review_status=review_status,
                )
            )
        return tuple(changes)


def _build_change(
    *,
    project_id: str,
    source: CanonicalSnapshot,
    target: CanonicalSnapshot,
    domain: str,
    stable_key: str,
    change_type: ChangeType,
    before: SnapshotFact | None,
    after: SnapshotFact | None,
    confidence: ChangeConfidence,
    review_status: ChangeReviewStatus,
) -> StructuredChange:
    representative = after if after is not None else before
    if representative is None:
        raise ValueError("A change must contain at least one fact state")
    before_state = _fact_state(before)
    after_state = _fact_state(after)
    source_refs = tuple(
        sorted(
            {
                source_ref
                for state in (before_state, after_state)
                if state is not None
                for source_ref in state.source_refs
            }
        )
    )
    return StructuredChange(
        change_id=_change_id(project_id, source.snapshot_id, target.snapshot_id, stable_key),
        project_id=project_id,
        source_snapshot_id=source.snapshot_id,
        target_snapshot_id=target.snapshot_id,
        stable_key=stable_key,
        fact_type=representative.fact.fact_type,
        domain=domain,
        change_type=change_type,
        before=before_state,
        after=after_state,
        summary=_summary(change_type, representative.fact.fact_type, before, after),
        source_refs=source_refs,
        confidence=confidence,
        review_status=review_status,
    )


def _fact_state(snapshot_fact: SnapshotFact | None) -> FactState | None:
    if snapshot_fact is None:
        return None
    return FactState(
        fact_ref=snapshot_fact.fact_ref,
        values=snapshot_fact.fact.values,
        source_refs=tuple(sorted(snapshot_fact.fact.source_refs)),
    )


def _summary(
    change_type: ChangeType,
    fact_type: str,
    before: SnapshotFact | None,
    after: SnapshotFact | None,
) -> str:
    if change_type is ChangeType.ADDED:
        return f"Added {fact_type} fact."
    if change_type is ChangeType.DELETED:
        return f"Deleted {fact_type} fact."
    if before is None or after is None:
        raise ValueError("Modified summary requires before and after facts")
    changed_fields = sorted(
        field
        for field in before.fact.values.keys() | after.fact.values.keys()
        if before.fact.values.get(field) != after.fact.values.get(field)
    )
    return f"Modified {fact_type} fields: {', '.join(changed_fields)}."


def _change_id(
    project_id: str, source_snapshot_id: str, target_snapshot_id: str, stable_key: str
) -> str:
    material = "\x00".join((project_id, source_snapshot_id, target_snapshot_id, stable_key))
    return f"change-{sha256(material.encode()).hexdigest()[:24]}"
