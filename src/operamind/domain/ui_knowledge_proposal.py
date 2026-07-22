"""Deterministically propose reviewable UI Knowledge from Canonical screen facts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from operamind.domain.structured_changes import CanonicalSnapshot, SnapshotFact
from operamind.domain.ui_execution import BrowserLocator, LocatorStrategy
from operamind.domain.ui_knowledge import (
    UiKnowledgeSnapshot,
    UiKnowledgeTarget,
    UiLocatorCandidate,
)

_SAFE_BUSINESS_ATTRIBUTE = re.compile(r"^data-[a-z0-9][a-z0-9_-]*$")
_SAFE_ATTRIBUTE_VALUE = re.compile(r"^[^'\"\r\n]+$")


@dataclass(frozen=True, slots=True)
class UiKnowledgeProposalIssue:
    fact_ref: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"fact_ref": self.fact_ref, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class UiKnowledgeProposal:
    snapshot: UiKnowledgeSnapshot | None
    issues: tuple[UiKnowledgeProposalIssue, ...]


class UiKnowledgeProposalBuilder:
    """Create draft targets only when business-visible identity is available."""

    def build(
        self,
        *,
        source: CanonicalSnapshot,
        snapshot_id: str,
        project_id: str,
        environment_id: str,
        deployment_revision: str,
        snapshot_version: str,
    ) -> UiKnowledgeProposal:
        targets: list[UiKnowledgeTarget] = []
        issues: list[UiKnowledgeProposalIssue] = []
        for snapshot_fact in source.facts:
            if snapshot_fact.fact.fact_type != "screen_element":
                continue
            target, target_issues = _target_from_fact(snapshot_fact)
            issues.extend(target_issues)
            if target is not None:
                targets.append(target)
        if not targets:
            return UiKnowledgeProposal(snapshot=None, issues=tuple(issues))
        return UiKnowledgeProposal(
            snapshot=UiKnowledgeSnapshot(
                snapshot_id=snapshot_id,
                project_id=project_id,
                environment_id=environment_id,
                deployment_revision=deployment_revision,
                snapshot_version=snapshot_version,
                review_status="draft",
                reviewed_by=None,
                targets=tuple(sorted(targets, key=lambda item: item.target_ref)),
            ),
            issues=tuple(sorted(issues, key=lambda item: (item.fact_ref, item.code))),
        )


def _target_from_fact(
    snapshot_fact: SnapshotFact,
) -> tuple[UiKnowledgeTarget | None, tuple[UiKnowledgeProposalIssue, ...]]:
    values = snapshot_fact.fact.values
    business_name = _first(values.get("business_name"), values.get("label"))
    screen_name = _first(values.get("screen_name"))
    issues: list[UiKnowledgeProposalIssue] = []
    if business_name is None:
        issues.append(
            UiKnowledgeProposalIssue(
                snapshot_fact.fact_ref,
                "business_name_missing",
                "screen_element requires business_name or label before UI Knowledge review.",
            )
        )
    if screen_name is None:
        issues.append(
            UiKnowledgeProposalIssue(
                snapshot_fact.fact_ref,
                "screen_name_missing",
                "screen_element requires a business-visible screen_name.",
            )
        )
    if business_name is None or screen_name is None:
        return None, tuple(issues)

    target_ref = _stable_id("ui-target", snapshot_fact.fact.stable_key)
    candidates = _candidates(snapshot_fact, target_ref, business_name, issues)
    if not candidates:
        issues.append(
            UiKnowledgeProposalIssue(
                snapshot_fact.fact_ref,
                "locator_candidate_missing",
                "No safe semantic or stable Locator candidate could be proposed.",
            )
        )
        return None, tuple(issues)
    return (
        UiKnowledgeTarget(
            target_ref=target_ref,
            business_name=business_name,
            screen_name=screen_name,
            trigger_path=_first(values.get("trigger_path")),
            source_fact_refs=(snapshot_fact.fact_ref,),
            candidates=tuple(candidates),
        ),
        tuple(issues),
    )


def _candidates(
    snapshot_fact: SnapshotFact,
    target_ref: str,
    business_name: str,
    issues: list[UiKnowledgeProposalIssue],
) -> list[UiLocatorCandidate]:
    values = snapshot_fact.fact.values
    proposed: list[tuple[LocatorStrategy, str, str | None, float]] = []
    role = _first(values.get("accessible_role"))
    accessible_name = _first(values.get("accessible_name"))
    if role is not None and accessible_name is not None:
        proposed.append((LocatorStrategy.ROLE, role, accessible_name, 0.92))
    label = _first(values.get("label"))
    if label is not None:
        proposed.append((LocatorStrategy.LABEL, label, None, 0.90))
    proposed.append((LocatorStrategy.TEXT, business_name, None, 0.80))
    test_id = _first(values.get("test_id"))
    if test_id is not None:
        proposed.append((LocatorStrategy.TEST_ID, test_id, None, 0.88))
    placeholder = _first(values.get("placeholder"))
    if placeholder is not None:
        proposed.append((LocatorStrategy.PLACEHOLDER, placeholder, None, 0.82))
    attribute_name = _first(values.get("business_attribute_name"))
    attribute_value = _first(values.get("business_attribute_value"))
    if attribute_name is not None or attribute_value is not None:
        if (
            attribute_name is None
            or attribute_value is None
            or _SAFE_BUSINESS_ATTRIBUTE.fullmatch(attribute_name) is None
            or _SAFE_ATTRIBUTE_VALUE.fullmatch(attribute_value) is None
        ):
            issues.append(
                UiKnowledgeProposalIssue(
                    snapshot_fact.fact_ref,
                    "business_attribute_invalid",
                    "Stable business attribute must be a safe data-* name and quoted value.",
                )
            )
        else:
            proposed.append(
                (
                    LocatorStrategy.CSS,
                    f"[{attribute_name}='{attribute_value}']",
                    None,
                    0.86,
                )
            )
    if len(proposed) == 1 and proposed[0][0] is LocatorStrategy.TEXT:
        issues.append(
            UiKnowledgeProposalIssue(
                snapshot_fact.fact_ref,
                "semantic_locator_review_required",
                "Only a text candidate was available; add runtime semantic evidence "
                "before approval.",
            )
        )
    candidates: list[UiLocatorCandidate] = []
    seen: set[tuple[LocatorStrategy, str, str | None]] = set()
    for priority, (strategy, value, name, reliability) in enumerate(proposed, start=1):
        identity = (strategy, value, name)
        if identity in seen:
            continue
        seen.add(identity)
        locator = BrowserLocator(strategy=strategy, value=value, name=name)
        candidates.append(
            UiLocatorCandidate(
                candidate_id=_stable_id(
                    "ui-locator",
                    f"{target_ref}\0{strategy.value}\0{value}\0{name or ''}",
                ),
                locator=locator,
                priority=priority,
                reliability_score=reliability,
                source="canonical_screen_element_proposal",
            )
        )
    return candidates


def _first(*values: str | None) -> str | None:
    return next((value.strip() for value in values if value is not None and value.strip()), None)


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:20]}"
