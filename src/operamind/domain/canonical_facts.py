"""Map source-document records into auditable Canonical Facts."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from operamind.domain.document_conventions import (
    ConventionMatch,
    ConventionVariant,
    DocumentConvention,
    MatchStatus,
    build_stable_key,
)

WHITESPACE = re.compile(r"\s+")


class CanonicalMappingStatus(StrEnum):
    """Whether an observed record can enter a Canonical Snapshot."""

    MAPPED = "mapped"
    NEEDS_REVIEW = "needs_review"


class CanonicalMappingReason(StrEnum):
    """Deterministic reason for a mapping decision."""

    MAPPED = "mapped"
    VARIANT_NOT_AUTO_MATCHED = "variant_not_auto_matched"
    MISSING_STABLE_KEY_FIELD = "missing_stable_key_field"
    CONFLICTING_FIELD_VALUES = "conflicting_field_values"


@dataclass(frozen=True, slots=True)
class ObservedField:
    """One named source value with a precise evidence reference."""

    name: str
    value: str
    source_ref: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Observed field name must not be blank")
        if not self.source_ref.strip():
            raise ValueError("Observed field source_ref must not be blank")


@dataclass(frozen=True, slots=True)
class ObservedRecord:
    """One source table row whose fields may use Convention aliases."""

    record_ref: str
    fields: tuple[ObservedField, ...]

    def __post_init__(self) -> None:
        if not self.record_ref.strip():
            raise ValueError("Observed record_ref must not be blank")
        if not self.fields:
            raise ValueError("Observed record must contain at least one field")


@dataclass(frozen=True, slots=True)
class CanonicalFieldEvidence:
    """Source aliases and locations that produced one canonical field."""

    canonical_field: str
    source_aliases: tuple[str, ...]
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalFact:
    """Layout-independent business fact ready for snapshot identity assignment."""

    fact_type: str
    stable_key: str
    values: Mapping[str, str]
    source_refs: tuple[str, ...]
    field_evidence: tuple[CanonicalFieldEvidence, ...]

    def __post_init__(self) -> None:
        if not self.fact_type.strip():
            raise ValueError("Canonical fact_type must not be blank")
        if not self.stable_key.strip():
            raise ValueError("Canonical stable_key must not be blank")
        expected_prefix = f"{normalize_field_name(self.fact_type)}:"
        if not self.stable_key.startswith(expected_prefix):
            raise ValueError(f"Canonical stable_key must start with {expected_prefix}")
        if not self.values or any(
            not field.strip() or not value.strip() for field, value in self.values.items()
        ):
            raise ValueError("Canonical Fact values must be non-empty names and values")
        if (
            not self.source_refs
            or any(not source_ref.strip() for source_ref in self.source_refs)
            or len(self.source_refs) != len(set(self.source_refs))
        ):
            raise ValueError("Canonical Fact source_refs must be non-empty and unique")
        object.__setattr__(self, "values", MappingProxyType(dict(sorted(self.values.items()))))


@dataclass(frozen=True, slots=True)
class CanonicalMappingResult:
    """Mapping outcome that never discards ambiguity or missing identity fields."""

    status: CanonicalMappingStatus
    reason: CanonicalMappingReason
    record_ref: str
    fact: CanonicalFact | None
    missing_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()
    unmapped_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is CanonicalMappingStatus.MAPPED and self.fact is None:
            raise ValueError("Mapped result must contain a Canonical Fact")
        if self.status is CanonicalMappingStatus.NEEDS_REVIEW and self.fact is not None:
            raise ValueError("needs_review result must not contain a Canonical Fact")


class CanonicalFactMapper:
    """Apply the uniquely auto-matched Variant's alias rules to one record."""

    def map_record(
        self,
        *,
        convention: DocumentConvention,
        match: ConventionMatch,
        fact_type: str,
        record: ObservedRecord,
    ) -> CanonicalMappingResult:
        """Return a fact only when Variant selection and Stable Key fields are safe."""

        if match.status is not MatchStatus.AUTO_MATCHED or match.selected_variant_id is None:
            return CanonicalMappingResult(
                status=CanonicalMappingStatus.NEEDS_REVIEW,
                reason=CanonicalMappingReason.VARIANT_NOT_AUTO_MATCHED,
                record_ref=record.record_ref,
                fact=None,
            )
        variant = _selected_variant(convention, match.selected_variant_id)
        alias_lookup = _alias_lookup(variant)

        candidates: dict[str, list[ObservedField]] = {}
        unmapped_fields: list[str] = []
        for field in record.fields:
            canonical_field = alias_lookup.get(normalize_field_name(field.name))
            if canonical_field is None:
                unmapped_fields.append(field.name)
                continue
            if not normalize_business_value(field.value):
                continue
            candidates.setdefault(canonical_field, []).append(field)

        values: dict[str, str] = {}
        evidence: list[CanonicalFieldEvidence] = []
        conflicting_fields: list[str] = []
        for canonical_field, fields in candidates.items():
            distinct_values = {normalize_business_value(field.value) for field in fields}
            if len(distinct_values) > 1:
                conflicting_fields.append(canonical_field)
                continue
            values[canonical_field] = normalize_business_value(fields[0].value)
            evidence.append(
                CanonicalFieldEvidence(
                    canonical_field=canonical_field,
                    source_aliases=tuple(sorted({field.name for field in fields})),
                    source_refs=tuple(sorted({field.source_ref for field in fields})),
                )
            )

        unmapped = tuple(sorted(set(unmapped_fields)))
        if conflicting_fields:
            return CanonicalMappingResult(
                status=CanonicalMappingStatus.NEEDS_REVIEW,
                reason=CanonicalMappingReason.CONFLICTING_FIELD_VALUES,
                record_ref=record.record_ref,
                fact=None,
                conflicting_fields=tuple(sorted(conflicting_fields)),
                unmapped_fields=unmapped,
            )

        missing_fields = tuple(
            field for field in variant.stable_key_fields if not values.get(field, "").strip()
        )
        if missing_fields:
            return CanonicalMappingResult(
                status=CanonicalMappingStatus.NEEDS_REVIEW,
                reason=CanonicalMappingReason.MISSING_STABLE_KEY_FIELD,
                record_ref=record.record_ref,
                fact=None,
                missing_fields=missing_fields,
                unmapped_fields=unmapped,
            )

        source_refs = tuple(
            sorted({source_ref for item in evidence for source_ref in item.source_refs})
        )
        fact = CanonicalFact(
            fact_type=fact_type,
            stable_key=build_stable_key(
                fact_type=fact_type,
                stable_key_fields=variant.stable_key_fields,
                canonical_fields=values,
                normalizers=variant.stable_key_normalizers,
            ),
            values=values,
            source_refs=source_refs,
            field_evidence=tuple(sorted(evidence, key=lambda item: item.canonical_field)),
        )
        return CanonicalMappingResult(
            status=CanonicalMappingStatus.MAPPED,
            reason=CanonicalMappingReason.MAPPED,
            record_ref=record.record_ref,
            fact=fact,
            unmapped_fields=unmapped,
        )


def normalize_field_name(value: str) -> str:
    """Normalize a source header for deterministic alias lookup."""

    return unicodedata.normalize("NFKC", normalize_business_value(value)).casefold()


def normalize_business_value(value: str) -> str:
    """Normalize whitespace without rewriting business-significant punctuation or case."""

    return WHITESPACE.sub(" ", value.strip())


def _selected_variant(
    convention: DocumentConvention, selected_variant_id: str
) -> ConventionVariant:
    variants = [
        variant for variant in convention.variants if variant.variant_id == selected_variant_id
    ]
    if len(variants) != 1:
        raise ValueError(f"Selected Variant does not exist uniquely: {selected_variant_id}")
    return variants[0]


def _alias_lookup(variant: ConventionVariant) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical_field, aliases in variant.field_aliases.items():
        for alias in aliases:
            normalized_alias = normalize_field_name(alias)
            existing = lookup.get(normalized_alias)
            if existing is not None and existing != canonical_field:
                raise ValueError(
                    "Alias maps to multiple canonical fields: "
                    f"{alias} ({existing}, {canonical_field})"
                )
            lookup[normalized_alias] = canonical_field
    return lookup
