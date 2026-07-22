import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from operamind.domain.document_conventions import (
    ConventionMatcher,
    DocumentConvention,
    DocumentSignals,
    MatchStatus,
    build_stable_key,
)
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]


def load_convention() -> DocumentConvention:
    profile: dict[str, Any] = json.loads(
        (ROOT / "profiles/document-convention-profile.example.json").read_text(encoding="utf-8")
    )
    ProfileCatalog.load(ROOT / "profiles").validate_profile(profile)
    return DocumentConvention.from_validated_profile(profile)


def test_api_list_variant_matches_all_structural_signals() -> None:
    observed = DocumentSignals.from_raw(
        filename="顧客_API_設計書.xlsx",
        sheet_names=("API一覧",),
        headers=("処理概要", "HTTPメソッド", "URI"),
    )

    result = ConventionMatcher().match(load_convention(), observed)

    assert result.status is MatchStatus.AUTO_MATCHED
    assert result.selected_variant_id == "api-list"
    assert result.candidates[0].score == pytest.approx(1.0)


def test_header_order_does_not_change_variant_result() -> None:
    first = DocumentSignals.from_raw(filename="IF.xlsx", headers=("URI", "HTTPメソッド"))
    second = DocumentSignals.from_raw(filename="IF.xlsx", headers=("HTTPメソッド", "URI"))
    matcher = ConventionMatcher()
    convention = load_convention()

    first_result = matcher.match(convention, first)
    second_result = matcher.match(convention, second)

    assert first_result == second_result


def test_url_based_api_template_matches_its_own_variant() -> None:
    observed = DocumentSignals.from_raw(
        filename="04_API詳細設計書.xlsx",
        sheet_names=("API一覧",),
        headers=("URL", "HTTPメソッド", "API名"),
    )

    result = ConventionMatcher().match(load_convention(), observed)

    assert result.status is MatchStatus.AUTO_MATCHED
    assert result.selected_variant_id == "api-list-url"
    assert result.candidates[0].score == pytest.approx(1.0)


def test_low_confidence_match_requires_review() -> None:
    observed = DocumentSignals.from_raw(filename="unknown.xlsx", sheet_names=("API一覧",))

    result = ConventionMatcher().match(load_convention(), observed)

    assert result.status is MatchStatus.NEEDS_REVIEW
    assert result.selected_variant_id is None
    assert result.reason == "below_auto_match_threshold"


def test_equal_top_scores_require_review_instead_of_guessing() -> None:
    convention = load_convention()
    first = convention.variants[0]
    ambiguous = replace(
        convention,
        variants=(first, replace(first, variant_id="api-list-copy")),
    )
    observed = DocumentSignals.from_raw(
        filename="API.xlsx",
        sheet_names=("API一覧",),
        headers=("URI", "HTTPメソッド"),
    )

    result = ConventionMatcher().match(ambiguous, observed)

    assert result.status is MatchStatus.NEEDS_REVIEW
    assert result.selected_variant_id is None
    assert result.reason == "ambiguous_top_score"


def test_stable_key_is_independent_of_field_mapping_order_and_whitespace() -> None:
    first = build_stable_key(
        fact_type="API",
        stable_key_fields=("method", "path"),
        canonical_fields={"method": " GET ", "path": "/expenses"},
    )
    second = build_stable_key(
        fact_type="api",
        stable_key_fields=("method", "path"),
        canonical_fields={"path": "/expenses", "method": "GET"},
    )

    assert first == second == "api:GET/%2Fexpenses"


def test_stable_key_rejects_missing_business_field() -> None:
    with pytest.raises(ValueError, match="path"):
        build_stable_key(
            fact_type="api",
            stable_key_fields=("method", "path"),
            canonical_fields={"method": "GET"},
        )
