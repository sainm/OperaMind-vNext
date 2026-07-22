"""Versioned UI Knowledge expressed through business-visible target names."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit

from operamind.domain.ui_execution import BrowserLocator


@dataclass(frozen=True, slots=True)
class UiLocatorCandidate:
    """One reviewed Locator candidate for a business-visible UI target."""

    candidate_id: str
    locator: BrowserLocator
    priority: int
    reliability_score: float
    source: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.candidate_id, self.source)):
            raise ValueError("UI Locator candidate identity and source must not be blank")
        if self.locator.target_ref is not None:
            raise ValueError("UI Locator candidate must contain a concrete Locator")
        if self.priority < 1:
            raise ValueError("UI Locator candidate priority must be at least 1")
        if not 0.0 <= self.reliability_score <= 1.0:
            raise ValueError("UI Locator reliability_score must be between 0 and 1")

    @classmethod
    def from_dict(cls, raw: object) -> UiLocatorCandidate:
        value = _object(raw, "UI Locator candidate")
        _exact_keys(
            value,
            {"candidate_id", "locator", "priority", "reliability_score", "source"},
            "UI Locator candidate",
        )
        score = value.get("reliability_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError("reliability_score must be a number")
        priority = value.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ValueError("priority must be an integer")
        return cls(
            candidate_id=_string(value, "candidate_id"),
            locator=BrowserLocator.from_dict(value.get("locator")),
            priority=priority,
            reliability_score=float(score),
            source=_string(value, "source"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "locator": self.locator.to_dict(),
            "priority": self.priority,
            "reliability_score": self.reliability_score,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class UiKnowledgeTarget:
    """A UI element named in business language, with internal source provenance."""

    target_ref: str
    business_name: str
    screen_name: str
    trigger_path: str | None
    source_fact_refs: tuple[str, ...]
    candidates: tuple[UiLocatorCandidate, ...]

    def __post_init__(self) -> None:
        names = (self.target_ref, self.business_name, self.screen_name)
        if any(not value.strip() for value in names):
            raise ValueError("UI Knowledge target identity and names must not be blank")
        if self.trigger_path is not None:
            _validate_trigger_path(self.trigger_path)
        if not self.source_fact_refs or any(not value.strip() for value in self.source_fact_refs):
            raise ValueError("UI Knowledge target requires source Fact provenance")
        if len(self.source_fact_refs) != len(set(self.source_fact_refs)):
            raise ValueError("UI Knowledge source Fact refs must be unique")
        if not self.candidates:
            raise ValueError("UI Knowledge target requires Locator candidates")
        if len({item.candidate_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("UI Locator candidate IDs must be unique per target")
        if len({item.priority for item in self.candidates}) != len(self.candidates):
            raise ValueError("UI Locator candidate priorities must be unique per target")

    @classmethod
    def from_dict(cls, raw: object) -> UiKnowledgeTarget:
        value = _object(raw, "UI Knowledge target")
        _exact_keys(
            value,
            {
                "target_ref",
                "business_name",
                "screen_name",
                "trigger_path",
                "source_fact_refs",
                "candidates",
            },
            "UI Knowledge target",
        )
        trigger_path = value.get("trigger_path")
        if trigger_path is not None and (
            not isinstance(trigger_path, str) or not trigger_path.strip()
        ):
            raise ValueError("trigger_path must be null or a non-blank string")
        return cls(
            target_ref=_string(value, "target_ref"),
            business_name=_string(value, "business_name"),
            screen_name=_string(value, "screen_name"),
            trigger_path=trigger_path,
            source_fact_refs=_strings(value, "source_fact_refs"),
            candidates=tuple(
                UiLocatorCandidate.from_dict(item) for item in _array(value, "candidates")
            ),
        )

    def preferred_locator(self, *, minimum_reliability: float = 0.8) -> BrowserLocator:
        """Select the strongest reviewed candidate deterministically."""

        eligible = [
            item for item in self.candidates if item.reliability_score >= minimum_reliability
        ]
        if not eligible:
            raise ValueError(
                f"UI target has no Locator meeting reliability threshold: {self.target_ref}"
            )
        return min(eligible, key=lambda item: (item.priority, -item.reliability_score)).locator

    def to_dict(self) -> dict[str, object]:
        return {
            "target_ref": self.target_ref,
            "business_name": self.business_name,
            "screen_name": self.screen_name,
            "trigger_path": self.trigger_path,
            "source_fact_refs": list(self.source_fact_refs),
            "candidates": [item.to_dict() for item in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class UiKnowledgeSnapshot:
    """Immutable Locator knowledge for one exact deployed revision."""

    snapshot_id: str
    project_id: str
    environment_id: str
    deployment_revision: str
    snapshot_version: str
    review_status: str
    reviewed_by: str | None
    targets: tuple[UiKnowledgeTarget, ...]
    activate: bool = False

    def __post_init__(self) -> None:
        required = (
            self.snapshot_id,
            self.project_id,
            self.environment_id,
            self.deployment_revision,
            self.snapshot_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("UI Knowledge Snapshot identity must not be blank")
        if self.review_status not in {"draft", "approved", "rejected"}:
            raise ValueError("UI Knowledge review_status is invalid")
        if (self.review_status == "draft") != (self.reviewed_by is None):
            raise ValueError("Reviewed UI Knowledge requires reviewed_by; draft forbids it")
        if self.activate and self.review_status != "approved":
            raise ValueError("Only approved UI Knowledge may become active")
        if not self.targets:
            raise ValueError("UI Knowledge Snapshot requires targets")
        if len({item.target_ref for item in self.targets}) != len(self.targets):
            raise ValueError("UI Knowledge target refs must be unique")

    @classmethod
    def from_dict(cls, raw: object) -> UiKnowledgeSnapshot:
        value = _object(raw, "UI Knowledge Snapshot")
        _exact_keys(
            value,
            {
                "snapshot_id",
                "project_id",
                "environment_id",
                "deployment_revision",
                "snapshot_version",
                "review_status",
                "reviewed_by",
                "targets",
                "activate",
            },
            "UI Knowledge Snapshot",
        )
        reviewed_by = value.get("reviewed_by")
        if reviewed_by is not None and (
            not isinstance(reviewed_by, str) or not reviewed_by.strip()
        ):
            raise ValueError("reviewed_by must be null or a non-blank string")
        activate = value.get("activate", False)
        if not isinstance(activate, bool):
            raise ValueError("activate must be a boolean")
        return cls(
            snapshot_id=_string(value, "snapshot_id"),
            project_id=_string(value, "project_id"),
            environment_id=_string(value, "environment_id"),
            deployment_revision=_string(value, "deployment_revision"),
            snapshot_version=_string(value, "snapshot_version"),
            review_status=_string(value, "review_status"),
            reviewed_by=reviewed_by,
            targets=tuple(UiKnowledgeTarget.from_dict(item) for item in _array(value, "targets")),
            activate=activate,
        )

    def resolve(self, target_ref: str, *, minimum_reliability: float = 0.8) -> BrowserLocator:
        matches = [item for item in self.targets if item.target_ref == target_ref]
        if len(matches) != 1:
            raise ValueError(f"UI target is not uniquely defined: {target_ref}")
        return matches[0].preferred_locator(minimum_reliability=minimum_reliability)

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "project_id": self.project_id,
            "environment_id": self.environment_id,
            "deployment_revision": self.deployment_revision,
            "snapshot_version": self.snapshot_version,
            "review_status": self.review_status,
            "reviewed_by": self.reviewed_by,
            "activate": self.activate,
            "targets": [item.to_dict() for item in self.targets],
        }


def _validate_trigger_path(value: str) -> None:
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError("UI Knowledge trigger_path must be origin-relative")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError("UI Knowledge trigger_path cannot change origin or contain a fragment")


def _object(raw: object, label: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], raw)


def _exact_keys(value: dict[str, object], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} has unknown fields: {unknown}")


def _string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return item


def _array(value: dict[str, object], key: str) -> list[object]:
    item = value.get(key)
    if not isinstance(item, list) or not item:
        raise ValueError(f"{key} must be a non-empty array")
    return item


def _strings(value: dict[str, object], key: str) -> tuple[str, ...]:
    items = _array(value, key)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise ValueError(f"{key} entries must be non-blank strings")
    return tuple(cast(list[str], items))
