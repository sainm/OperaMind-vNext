"""Profile-driven exact relations between Canonical document Slice nodes."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

WHITESPACE = re.compile(r"\s+")


class RelationValueNormalizer(StrEnum):
    """Explicit normalizers allowed in a DocumentRelationProfile rule."""

    NFKC_CASEFOLD = "nfkc_casefold"
    PRESERVE = "preserve"
    URL_PATH = "url_path"


class RelationUnresolvedReason(StrEnum):
    """Reasons an eligible source Slice did not produce a safe edge."""

    MISSING_SOURCE_VALUE = "missing_source_value"
    NO_TARGET = "no_target"
    AMBIGUOUS_TARGET = "ambiguous_target"
    SELF_TARGET = "self_target"


@dataclass(frozen=True, slots=True)
class DocumentRelationFact:
    """Structured Fact values attached to one rehydratable Slice node."""

    node_id: str
    document_id: str
    document_type: str
    fact_type: str
    values: Mapping[str, str]

    def __post_init__(self) -> None:
        required = (self.node_id, self.document_id, self.document_type, self.fact_type)
        if any(not value.strip() for value in required):
            raise ValueError("Document relation Fact identity must not be blank")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


@dataclass(frozen=True, slots=True)
class DocumentRelationRule:
    """One exact tuple-join rule parsed from a validated Profile."""

    rule_id: str
    relation_label: str
    source_document_types: tuple[str, ...]
    source_fact_types: tuple[str, ...]
    source_fields: tuple[str, ...]
    target_document_types: tuple[str, ...]
    target_fact_types: tuple[str, ...]
    target_fields: tuple[str, ...]
    value_normalizers: tuple[RelationValueNormalizer, ...]

    def __post_init__(self) -> None:
        if not self.rule_id.strip() or not self.relation_label.strip():
            raise ValueError("Document relation rule identity must not be blank")
        collections = (
            self.source_document_types,
            self.source_fact_types,
            self.source_fields,
            self.target_document_types,
            self.target_fact_types,
            self.target_fields,
            self.value_normalizers,
        )
        if any(not values for values in collections):
            raise ValueError("Document relation rule collections must not be empty")
        if not (len(self.source_fields) == len(self.target_fields) == len(self.value_normalizers)):
            raise ValueError("Document relation rule field arity must match")


@dataclass(frozen=True, slots=True)
class PlannedDocumentRelation:
    """One safe derived edge with no raw match value persisted."""

    rule_id: str
    relation_label: str
    source_node_id: str
    target_node_id: str
    match_key_digest: str


@dataclass(frozen=True, slots=True)
class UnresolvedDocumentRelation:
    """One source anchor that could not resolve uniquely."""

    rule_id: str
    source_node_id: str
    match_key_digest: str | None
    candidate_target_count: int
    reason: RelationUnresolvedReason


@dataclass(frozen=True, slots=True)
class DocumentRelationPlan:
    """Deterministic edges and complete unresolved-source ledger."""

    relations: tuple[PlannedDocumentRelation, ...]
    unresolved: tuple[UnresolvedDocumentRelation, ...]


class DocumentRelationPlanner:
    """Join Canonical fields exactly according to reviewed Profile rules."""

    def __init__(self, rules: tuple[DocumentRelationRule, ...]) -> None:
        if not rules:
            raise ValueError("Document relation planner requires at least one rule")
        if len({rule.rule_id for rule in rules}) != len(rules):
            raise ValueError("Document relation rule IDs must be unique")
        self._rules = rules

    @classmethod
    def from_validated_profile(cls, profile: dict[str, Any]) -> DocumentRelationPlanner:
        """Parse a Profile after ProfileCatalog validation."""

        if profile.get("profile_type") != "DocumentRelationProfile":
            raise ValueError("DocumentRelationPlanner requires a DocumentRelationProfile")
        return cls(
            tuple(
                DocumentRelationRule(
                    rule_id=str(rule["rule_id"]),
                    relation_label=str(rule["relation_label"]),
                    source_document_types=tuple(
                        str(value) for value in rule["source_document_types"]
                    ),
                    source_fact_types=tuple(str(value) for value in rule["source_fact_types"]),
                    source_fields=tuple(str(value) for value in rule["source_fields"]),
                    target_document_types=tuple(
                        str(value) for value in rule["target_document_types"]
                    ),
                    target_fact_types=tuple(str(value) for value in rule["target_fact_types"]),
                    target_fields=tuple(str(value) for value in rule["target_fields"]),
                    value_normalizers=tuple(
                        RelationValueNormalizer(str(value)) for value in rule["value_normalizers"]
                    ),
                )
                for rule in profile["rules"]
            )
        )

    def plan(self, facts: tuple[DocumentRelationFact, ...]) -> DocumentRelationPlan:
        """Resolve every eligible source to exactly one target or record why not."""

        relations: list[PlannedDocumentRelation] = []
        unresolved: list[UnresolvedDocumentRelation] = []
        for rule in self._rules:
            targets: dict[tuple[str, ...], list[DocumentRelationFact]] = {}
            for fact in facts:
                if not _matches_target(rule, fact):
                    continue
                key = _match_key(fact.values, rule.target_fields, rule.value_normalizers)
                if key is not None:
                    targets.setdefault(key, []).append(fact)

            for fact in facts:
                if not _matches_source(rule, fact):
                    continue
                key = _match_key(fact.values, rule.source_fields, rule.value_normalizers)
                if key is None:
                    unresolved.append(
                        UnresolvedDocumentRelation(
                            rule_id=rule.rule_id,
                            source_node_id=fact.node_id,
                            match_key_digest=None,
                            candidate_target_count=0,
                            reason=RelationUnresolvedReason.MISSING_SOURCE_VALUE,
                        )
                    )
                    continue
                digest = _match_digest(key)
                candidates = tuple(sorted(targets.get(key, []), key=lambda target: target.node_id))
                if not candidates:
                    reason = RelationUnresolvedReason.NO_TARGET
                elif len(candidates) > 1:
                    reason = RelationUnresolvedReason.AMBIGUOUS_TARGET
                elif candidates[0].node_id == fact.node_id:
                    reason = RelationUnresolvedReason.SELF_TARGET
                else:
                    target = candidates[0]
                    relations.append(
                        PlannedDocumentRelation(
                            rule_id=rule.rule_id,
                            relation_label=rule.relation_label,
                            source_node_id=fact.node_id,
                            target_node_id=target.node_id,
                            match_key_digest=digest,
                        )
                    )
                    continue
                unresolved.append(
                    UnresolvedDocumentRelation(
                        rule_id=rule.rule_id,
                        source_node_id=fact.node_id,
                        match_key_digest=digest,
                        candidate_target_count=len(candidates),
                        reason=reason,
                    )
                )
        return DocumentRelationPlan(
            relations=tuple(
                sorted(
                    relations,
                    key=lambda value: (
                        value.rule_id,
                        value.source_node_id,
                        value.target_node_id,
                    ),
                )
            ),
            unresolved=tuple(
                sorted(
                    unresolved,
                    key=lambda value: (value.rule_id, value.source_node_id),
                )
            ),
        )


def _matches_source(rule: DocumentRelationRule, fact: DocumentRelationFact) -> bool:
    return (
        fact.document_type in rule.source_document_types
        and fact.fact_type in rule.source_fact_types
    )


def _matches_target(rule: DocumentRelationRule, fact: DocumentRelationFact) -> bool:
    return (
        fact.document_type in rule.target_document_types
        and fact.fact_type in rule.target_fact_types
    )


def _match_key(
    values: Mapping[str, str],
    fields: tuple[str, ...],
    normalizers: tuple[RelationValueNormalizer, ...],
) -> tuple[str, ...] | None:
    normalized: list[str] = []
    for field, normalizer in zip(fields, normalizers, strict=True):
        raw = values.get(field)
        if raw is None or not raw.strip():
            return None
        value = _normalize(raw, normalizer)
        if not value:
            return None
        normalized.append(value)
    return tuple(normalized)


def _normalize(value: str, normalizer: RelationValueNormalizer) -> str:
    normalized = WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).strip())
    if normalizer is RelationValueNormalizer.NFKC_CASEFOLD:
        return normalized.casefold()
    if normalizer is RelationValueNormalizer.URL_PATH:
        parsed = urlsplit(normalized)
        path = parsed.path or normalized.split("?", 1)[0].split("#", 1)[0]
        if not path.startswith("/"):
            path = f"/{path}"
        return path.rstrip("/") or "/"
    return normalized


def _match_digest(key: tuple[str, ...]) -> str:
    return sha256("\x00".join(key).encode()).hexdigest()
