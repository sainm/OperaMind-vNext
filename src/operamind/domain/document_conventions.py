"""Document Convention Variant matching and Stable Key construction."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote

WHITESPACE = re.compile(r"\s+")


class SignalType(StrEnum):
    """Supported observable signals for dynamic document conventions."""

    FILENAME_TOKEN = "filename_token"
    SHEET_NAME = "sheet_name"
    HEADING = "heading"
    HEADERS = "headers"
    BUSINESS_TERM = "business_term"


class MatchStatus(StrEnum):
    """Whether a Variant can be activated without human review."""

    AUTO_MATCHED = "auto_matched"
    NEEDS_REVIEW = "needs_review"


class StableKeyNormalizer(StrEnum):
    """Field-specific normalization applied before Stable Key encoding."""

    PRESERVE = "preserve"
    CASEFOLD = "casefold"
    UPPERCASE = "uppercase"
    LOWERCASE = "lowercase"


@dataclass(frozen=True, slots=True)
class SignalRule:
    """A weighted rule within one document convention Variant."""

    signal_type: SignalType
    values: tuple[str, ...]
    weight: float


@dataclass(frozen=True, slots=True)
class ConventionVariant:
    """One recognized structural writing style for a document type."""

    variant_id: str
    signals: tuple[SignalRule, ...]
    field_aliases: Mapping[str, tuple[str, ...]]
    stable_key_fields: tuple[str, ...]
    stable_key_normalizers: Mapping[str, StableKeyNormalizer]


@dataclass(frozen=True, slots=True)
class DocumentConvention:
    """Validated runtime representation of a Document Convention Profile."""

    profile_id: str
    profile_version: str
    document_type: str
    minimum_auto_match_score: float
    variants: tuple[ConventionVariant, ...]

    @classmethod
    def from_validated_profile(cls, profile: Mapping[str, Any]) -> DocumentConvention:
        """Map a Profile that has already passed `ProfileCatalog` validation."""

        variants = tuple(
            ConventionVariant(
                variant_id=str(variant["variant_id"]),
                signals=tuple(
                    SignalRule(
                        signal_type=SignalType(signal["type"]),
                        values=tuple(str(value) for value in signal["values"]),
                        weight=float(signal["weight"]),
                    )
                    for signal in variant["signals"]
                ),
                field_aliases={
                    str(field): tuple(str(alias) for alias in aliases)
                    for field, aliases in variant["field_aliases"].items()
                },
                stable_key_fields=tuple(str(field) for field in variant["stable_key_fields"]),
                stable_key_normalizers={
                    str(field): StableKeyNormalizer(normalizer)
                    for field, normalizer in variant["stable_key_normalizers"].items()
                },
            )
            for variant in profile["variants"]
        )
        return cls(
            profile_id=str(profile["profile_id"]),
            profile_version=str(profile["profile_version"]),
            document_type=str(profile["document_type"]),
            minimum_auto_match_score=float(profile["minimum_auto_match_score"]),
            variants=variants,
        )


@dataclass(frozen=True, slots=True)
class DocumentSignals:
    """Normalized observable structure extracted from one source document."""

    filename: str
    sheet_names: frozenset[str]
    headings: frozenset[str]
    headers: frozenset[str]
    business_terms: frozenset[str]

    @classmethod
    def from_raw(
        cls,
        *,
        filename: str,
        sheet_names: tuple[str, ...] = (),
        headings: tuple[str, ...] = (),
        headers: tuple[str, ...] = (),
        business_terms: tuple[str, ...] = (),
    ) -> DocumentSignals:
        """Normalize case, width, and whitespace while discarding source ordering."""

        return cls(
            filename=_normalize(filename),
            sheet_names=_normalize_set(sheet_names),
            headings=_normalize_set(headings),
            headers=_normalize_set(headers),
            business_terms=_normalize_set(business_terms),
        )


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    """Score and evidence for one candidate Variant."""

    variant_id: str
    score: float
    matched_signal_types: tuple[SignalType, ...]


@dataclass(frozen=True, slots=True)
class ConventionMatch:
    """Deterministic match decision with all candidates retained for audit."""

    status: MatchStatus
    selected_variant_id: str | None
    reason: str
    candidates: tuple[MatchCandidate, ...]


class ConventionMatcher:
    """Score document structure against all configured Variants."""

    def match(self, convention: DocumentConvention, observed: DocumentSignals) -> ConventionMatch:
        """Return an automatic match only for one unambiguous candidate above threshold."""

        candidates = tuple(
            sorted(
                (self._score(variant, observed) for variant in convention.variants),
                key=lambda candidate: (-candidate.score, candidate.variant_id),
            )
        )
        top = candidates[0]
        if top.score < convention.minimum_auto_match_score:
            return ConventionMatch(
                status=MatchStatus.NEEDS_REVIEW,
                selected_variant_id=None,
                reason="below_auto_match_threshold",
                candidates=candidates,
            )
        tied = [
            candidate
            for candidate in candidates
            if math.isclose(candidate.score, top.score, abs_tol=1e-9)
        ]
        if len(tied) > 1:
            return ConventionMatch(
                status=MatchStatus.NEEDS_REVIEW,
                selected_variant_id=None,
                reason="ambiguous_top_score",
                candidates=candidates,
            )
        return ConventionMatch(
            status=MatchStatus.AUTO_MATCHED,
            selected_variant_id=top.variant_id,
            reason="unique_candidate_above_threshold",
            candidates=candidates,
        )

    @staticmethod
    def _score(variant: ConventionVariant, observed: DocumentSignals) -> MatchCandidate:
        matched: list[SignalType] = []
        score = 0.0
        for signal in variant.signals:
            if _matches(signal, observed):
                matched.append(signal.signal_type)
                score += signal.weight
        return MatchCandidate(
            variant_id=variant.variant_id,
            score=score,
            matched_signal_types=tuple(matched),
        )


def build_stable_key(
    *,
    fact_type: str,
    stable_key_fields: tuple[str, ...],
    canonical_fields: Mapping[str, str],
    normalizers: Mapping[str, StableKeyNormalizer] | None = None,
) -> str:
    """Build a layout-independent key from ordered canonical business fields."""

    normalized_fact_type = _normalize(fact_type)
    if not normalized_fact_type:
        raise ValueError("fact_type must not be blank")
    parts: list[str] = []
    for field in stable_key_fields:
        raw_value = canonical_fields.get(field)
        if raw_value is None:
            raise ValueError(f"Missing Stable Key field: {field}")
        normalized_value = _normalize_stable_key_value(
            raw_value,
            (normalizers or {}).get(field, StableKeyNormalizer.PRESERVE),
        )
        if not normalized_value:
            raise ValueError(f"Stable Key field must not be blank: {field}")
        parts.append(quote(normalized_value, safe="-._~"))
    if not parts:
        raise ValueError("stable_key_fields must not be empty")
    return f"{normalized_fact_type}:{'/'.join(parts)}"


def _matches(signal: SignalRule, observed: DocumentSignals) -> bool:
    expected = tuple(_normalize(value) for value in signal.values)
    if signal.signal_type is SignalType.FILENAME_TOKEN:
        return any(value in observed.filename for value in expected)
    observed_values = {
        SignalType.SHEET_NAME: observed.sheet_names,
        SignalType.HEADING: observed.headings,
        SignalType.HEADERS: observed.headers,
        SignalType.BUSINESS_TERM: observed.business_terms,
    }[signal.signal_type]
    if signal.signal_type is SignalType.HEADERS:
        return all(value in observed_values for value in expected)
    return any(value in observed_values for value in expected)


def _normalize_set(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(normalized for value in values if (normalized := _normalize(value)))


def _normalize(value: str) -> str:
    return WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).strip()).casefold()


def _normalize_value(value: str) -> str:
    return WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).strip())


def _normalize_stable_key_value(value: str, normalizer: StableKeyNormalizer) -> str:
    normalized = _normalize_value(value)
    if normalizer is StableKeyNormalizer.CASEFOLD:
        return normalized.casefold()
    if normalizer is StableKeyNormalizer.UPPERCASE:
        return normalized.upper()
    if normalizer is StableKeyNormalizer.LOWERCASE:
        return normalized.lower()
    return normalized
