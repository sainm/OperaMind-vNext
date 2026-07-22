import json
from pathlib import Path
from typing import Any

from operamind.domain import (
    CanonicalFactMapper,
    CanonicalMappingReason,
    CanonicalMappingStatus,
    ObservedField,
    ObservedRecord,
)
from operamind.domain.document_conventions import (
    ConventionMatch,
    ConventionMatcher,
    DocumentConvention,
    DocumentSignals,
)
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]


def load_convention() -> DocumentConvention:
    profile: dict[str, Any] = json.loads(
        (ROOT / "profiles/document-convention-profile.example.json").read_text(encoding="utf-8")
    )
    ProfileCatalog.load(ROOT / "profiles").validate_profile(profile)
    return DocumentConvention.from_validated_profile(profile)


def auto_match(convention: DocumentConvention) -> ConventionMatch:
    return ConventionMatcher().match(
        convention,
        DocumentSignals.from_raw(
            filename="API.xlsx",
            sheet_names=("API一覧",),
            headers=("URI", "HTTPメソッド"),
        ),
    )


def test_alias_and_column_order_do_not_change_canonical_identity() -> None:
    convention = load_convention()
    match = auto_match(convention)
    first = ObservedRecord(
        record_ref="before#row=5",
        fields=(
            ObservedField("HTTPメソッド", " GET ", "before#C5"),
            ObservedField("URI", "/expenses", "before#D5"),
            ObservedField("処理概要", "経費一覧", "before#B5"),
        ),
    )
    second = ObservedRecord(
        record_ref="after#row=8",
        fields=(
            ObservedField("Description", "経費一覧", "after#A8"),
            ObservedField("Path", "/expenses", "after#B8"),
            ObservedField("Method", "GET", "after#C8"),
        ),
    )
    mapper = CanonicalFactMapper()

    first_result = mapper.map_record(
        convention=convention, match=match, fact_type="api", record=first
    )
    second_result = mapper.map_record(
        convention=convention, match=match, fact_type="api", record=second
    )

    assert first_result.status is CanonicalMappingStatus.MAPPED
    assert second_result.status is CanonicalMappingStatus.MAPPED
    assert first_result.fact is not None
    assert second_result.fact is not None
    assert first_result.fact.stable_key == second_result.fact.stable_key
    assert first_result.fact.values == second_result.fact.values
    assert first_result.fact.stable_key == "api:GET/%2Fexpenses"


def test_missing_stable_key_field_requires_review() -> None:
    convention = load_convention()
    record = ObservedRecord(
        record_ref="source#row=1",
        fields=(ObservedField("HTTPメソッド", "GET", "source#A1"),),
    )

    result = CanonicalFactMapper().map_record(
        convention=convention,
        match=auto_match(convention),
        fact_type="api",
        record=record,
    )

    assert result.status is CanonicalMappingStatus.NEEDS_REVIEW
    assert result.reason is CanonicalMappingReason.MISSING_STABLE_KEY_FIELD
    assert result.missing_fields == ("path",)
    assert result.fact is None


def test_conflicting_alias_values_require_review() -> None:
    convention = load_convention()
    record = ObservedRecord(
        record_ref="source#row=1",
        fields=(
            ObservedField("HTTPメソッド", "GET", "source#A1"),
            ObservedField("URI", "/expenses", "source#B1"),
            ObservedField("Path", "/customers", "source#C1"),
        ),
    )

    result = CanonicalFactMapper().map_record(
        convention=convention,
        match=auto_match(convention),
        fact_type="api",
        record=record,
    )

    assert result.status is CanonicalMappingStatus.NEEDS_REVIEW
    assert result.reason is CanonicalMappingReason.CONFLICTING_FIELD_VALUES
    assert result.conflicting_fields == ("path",)
    assert result.fact is None


def test_low_confidence_variant_blocks_canonical_mapping() -> None:
    convention = load_convention()
    match = ConventionMatcher().match(convention, DocumentSignals.from_raw(filename="unknown.xlsx"))
    record = ObservedRecord(
        record_ref="source#row=1",
        fields=(
            ObservedField("HTTPメソッド", "GET", "source#A1"),
            ObservedField("URI", "/expenses", "source#B1"),
        ),
    )

    result = CanonicalFactMapper().map_record(
        convention=convention,
        match=match,
        fact_type="api",
        record=record,
    )

    assert result.status is CanonicalMappingStatus.NEEDS_REVIEW
    assert result.reason is CanonicalMappingReason.VARIANT_NOT_AUTO_MATCHED
    assert result.fact is None


def test_unmapped_source_fields_are_retained_for_audit() -> None:
    convention = load_convention()
    record = ObservedRecord(
        record_ref="source#row=1",
        fields=(
            ObservedField("HTTPメソッド", "GET", "source#A1"),
            ObservedField("URI", "/expenses", "source#B1"),
            ObservedField("Owner", "Accounting", "source#C1"),
        ),
    )

    result = CanonicalFactMapper().map_record(
        convention=convention,
        match=auto_match(convention),
        fact_type="api",
        record=record,
    )

    assert result.status is CanonicalMappingStatus.MAPPED
    assert result.unmapped_fields == ("Owner",)
