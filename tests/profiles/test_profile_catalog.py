import copy
import json
from pathlib import Path
from typing import Any

import pytest

from operamind.profiles.catalog import (
    EXPECTED_PROFILE_TYPES,
    ProfileCatalog,
    ProfileValidationError,
)

ROOT = Path(__file__).parents[2]


def load_example(name: str) -> dict[str, Any]:
    raw: object = json.loads((ROOT / "profiles" / name).read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def load_fixture(name: str) -> dict[str, Any]:
    raw: object = json.loads(
        (ROOT / "tests" / "fixtures" / "profiles" / name).read_text(encoding="utf-8")
    )
    assert isinstance(raw, dict)
    return raw


def test_profile_catalog_and_examples_are_valid() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")

    catalog_report = catalog.validate_catalog()
    example_report = catalog.validate_examples()

    assert catalog_report.is_valid, catalog_report.issues
    assert example_report.is_valid, example_report.issues
    assert frozenset(catalog.schema_paths) == EXPECTED_PROFILE_TYPES


def test_visiondemo_target_profile_fixtures_are_valid() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")

    for name in (
        "visiondemo-code-framework-profile.json",
        "visiondemo-command-profile.json",
        "visiondemo-document-relation-profile.json",
    ):
        catalog.validate_profile(load_fixture(name))


def test_springboot15_thymeleaf_gradle_profiles_are_valid_and_use_wrapper() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    code_profile = load_example("springboot15-thymeleaf-gradle-code-framework-profile.example.json")
    command_profile = load_example("springboot15-thymeleaf-gradle-command-profile.example.json")

    catalog.validate_profile(code_profile)
    catalog.validate_profile(command_profile)
    assert code_profile["profile_id"] == "springboot15-thymeleaf-gradle"
    assert "web_ui_route" in code_profile["anchor_extractors"]
    assert {
        template["command_ref"]: template["argv"] for template in command_profile["templates"]
    } == {
        "springboot15-compile": ["./gradlew", "classes", "testClasses", "--no-daemon"],
        "springboot15-test": ["./gradlew", "test", "--no-daemon"],
        "springboot15-build": ["./gradlew", "build", "--no-daemon"],
    }


def test_embedding_dimensions_must_be_positive() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = load_example("embedding-profile.example.json")
    profile["expected_dimensions"] = 0

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert captured.value.report.issues[0].location == "expected_dimensions"


def test_embedding_dimensions_must_fit_pgvector_storage() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = load_example("embedding-profile.example.json")
    profile["expected_dimensions"] = 16_001

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert captured.value.report.issues[0].location == "expected_dimensions"


def test_document_signal_weights_must_sum_to_one() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("document-convention-profile.example.json"))
    profile["variants"][0]["signals"][0]["weight"] = 0.1

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.invalid_signal_weights"
    }


def test_stable_key_fields_must_have_aliases() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("document-convention-profile.example.json"))
    profile["variants"][0]["stable_key_fields"].append("unknown")
    profile["variants"][0]["stable_key_normalizers"]["unknown"] = "preserve"

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.unknown_stable_key_field"
    }


def test_every_stable_key_field_requires_an_explicit_normalizer() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("document-convention-profile.example.json"))
    del profile["variants"][0]["stable_key_normalizers"]["path"]

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.invalid_stable_key_normalizers"
    }


def test_normalized_alias_cannot_map_to_multiple_canonical_fields() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("document-convention-profile.example.json"))
    profile["variants"][0]["field_aliases"]["summary"].append(" uri ")

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.ambiguous_field_alias"
    }


def test_document_relation_rule_field_arity_must_match() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("document-relation-profile.example.json"))
    profile["rules"][0]["target_fields"].append("method")

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.invalid_document_relation_field_arity"
    }


def test_framework_extractors_require_their_declared_language() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("code-framework-profile.example.json"))
    profile["languages"].remove("sql")

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.extractor_language_missing"
    }


@pytest.mark.parametrize(
    ("language", "extractor"),
    [
        ("javascript", "javascript_symbol"),
        ("typescript", "typescript_symbol"),
        ("python", "python_symbol"),
        ("kotlin", "kotlin_symbol"),
    ],
)
def test_polyglot_semantic_extractors_require_their_language(
    language: str,
    extractor: str,
) -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("polyglot-code-framework-profile.example.json"))
    profile["languages"].remove(language)

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.message for issue in captured.value.report.issues} == {
        f"{extractor} requires language {language}"
    }
    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.extractor_language_missing"
    }


@pytest.mark.parametrize("language", ["java", "xml"])
def test_struts1_extractor_requires_java_and_xml(language: str) -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("struts1-code-framework-profile.example.json"))
    profile["languages"].remove(language)

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert any(
        issue.code == "profile.extractor_language_missing"
        and issue.message == f"struts1_mvc requires language {language}"
        for issue in captured.value.report.issues
    )


def test_struts1_extractor_requires_java_symbols() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("struts1-code-framework-profile.example.json"))
    profile["anchor_extractors"].remove("java_symbol")

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert any(
        issue.code == "profile.extractor_dependency_missing"
        and issue.message == "struts1_mvc requires java_symbol"
        for issue in captured.value.report.issues
    )


def test_framework_specialized_java_extractors_require_symbol_extraction() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("code-framework-profile.example.json"))
    profile["anchor_extractors"].remove("java_symbol")

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.extractor_dependency_missing"
    }


@pytest.mark.parametrize(
    ("extractor", "missing_dependency"),
    [
        ("spring_config_binding", "config_key"),
        ("spring_data_access", "sql_table"),
    ],
)
def test_framework_relation_extractors_require_their_anchor_extractors(
    extractor: str,
    missing_dependency: str,
) -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("code-framework-profile.example.json"))
    profile["anchor_extractors"].append(extractor)
    profile["anchor_extractors"].remove(missing_dependency)

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.extractor_dependency_missing"
    }


def test_web_ui_route_extractor_requires_a_supported_ui_language() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("code-framework-profile.example.json"))
    profile["languages"].remove("xml")
    profile["anchor_extractors"].append("web_ui_route")

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.extractor_language_missing"
    }


def test_command_refs_must_be_unique() -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("command-execution-profile.example.json"))
    profile["templates"][1]["command_ref"] = profile["templates"][0]["command_ref"]

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {
        "profile.duplicate_command_ref"
    }


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("working_directory", "../outside", "profile.unsafe_command_working_directory"),
        ("argv", ["../outside/tool"], "profile.unsafe_command_executable"),
    ],
)
def test_command_workspace_paths_cannot_escape(field: str, value: object, code: str) -> None:
    catalog = ProfileCatalog.load(ROOT / "profiles")
    profile = copy.deepcopy(load_example("command-execution-profile.example.json"))
    profile["templates"][0][field] = value

    with pytest.raises(ProfileValidationError) as captured:
        catalog.validate_profile(profile)

    assert {issue.code for issue in captured.value.report.issues} == {code}
