from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from operamind.application.project_stack import (
    SPRINGBOOT15_THYMELEAF_GRADLE,
    ProjectProfileBootstrapper,
    _gradle_wrapper,
    detect_project_stack,
)
from operamind.application.web_control_plane import _safe_project_stack_context
from operamind.infrastructure.postgres import ActiveProfileBinding
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]


class _ProfileRepository:
    def __init__(self) -> None:
        self.versions: dict[str, dict[str, Any]] = {}
        self.bindings: dict[tuple[str, str], ActiveProfileBinding] = {}
        self.activation_calls: list[tuple[str, str]] = []

    def get_active(
        self, *, project_id: str, binding_key: str
    ) -> ActiveProfileBinding | None:
        return self.bindings.get((project_id, binding_key))

    def store_version(self, *, profile_version_id: str, profile: dict[str, Any]) -> str:
        self.versions[profile_version_id] = profile
        return "digest"

    def activate(
        self,
        *,
        activation_event_id: str,
        project_id: str,
        binding_key: str,
        profile_version_id: str,
        activated_by: str,
        reason: str,
    ) -> bool:
        del activation_event_id, reason
        profile = self.versions[profile_version_id]
        self.bindings[(project_id, binding_key)] = ActiveProfileBinding(
            project_id=project_id,
            binding_key=binding_key,
            profile_version_id=profile_version_id,
            activated_by=activated_by,
            activated_at=datetime.now(UTC),
            profile=profile,
        )
        self.activation_calls.append((binding_key, profile_version_id))
        return True


def test_detects_supported_spring_boot_15_thymeleaf_gradle_project(
    tmp_path: Path,
) -> None:
    _write_supported_project(tmp_path)

    detected = detect_project_stack(tmp_path)

    assert detected.supported
    assert detected.stack_id == SPRINGBOOT15_THYMELEAF_GRADLE
    assert detected.missing_signals == ()
    assert "build.gradle:Spring Boot 1.5" in detected.evidence
    assert "app/src/main/resources/templates/expense/list.html" in detected.evidence
    assert all(not Path(item).is_absolute() for item in detected.evidence)
    assert detected.copilot_context()["compile_command"] == [
        "./gradlew",
        "classes",
        "testClasses",
        "--no-daemon",
    ]
    assert detected.copilot_context()["change_constraints"] == [
        "Spring Boot 1.5、Thymeleaf、Gradle Wrapper を維持する",
        "変更要件に含まれない Framework / Build Tool の更新を行わない",
        "JAVA_HOME に設定された対象工程互換 JDK を使用する",
    ]


def test_does_not_guess_from_gradle_or_template_files_without_spring_boot_15(
    tmp_path: Path,
) -> None:
    _write_supported_project(tmp_path)
    (tmp_path / "build.gradle").write_text(
        """
plugins {
    id 'org.springframework.boot' version '3.4.5'
}
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-thymeleaf'
}
""",
        encoding="utf-8",
    )

    detected = detect_project_stack(tmp_path)

    assert not detected.supported
    assert detected.stack_id is None
    assert detected.status == "unrecognized"
    assert "Spring Boot 1.5 の Gradle 設定" in detected.missing_signals


def test_bootstrap_activates_missing_profiles_once_and_preserves_existing_bindings(
    tmp_path: Path,
) -> None:
    _write_supported_project(tmp_path)
    repository = _ProfileRepository()
    bootstrapper = ProjectProfileBootstrapper(
        profiles=ProfileCatalog.load(ROOT / "profiles"),
        repository=repository,
    )

    first = bootstrapper.ensure(
        project_id="project-1",
        repository_id="repository-1",
        workspace_root=tmp_path,
    )
    replay = bootstrapper.ensure(
        project_id="project-1",
        repository_id="repository-1",
        workspace_root=tmp_path,
    )

    assert first.detection.supported
    assert first.activated_binding_keys == (
        "code-framework:repository-1",
        "command-execution:repository-1",
    )
    assert len(first.active_bindings) == 2
    assert replay.activated_binding_keys == ()
    assert len(replay.active_bindings) == 2
    assert len(repository.activation_calls) == 2


def test_bootstrap_leaves_unrecognized_project_unchanged(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    repository = _ProfileRepository()

    result = ProjectProfileBootstrapper(
        profiles=ProfileCatalog.load(ROOT / "profiles"),
        repository=repository,
    ).ensure(
        project_id="project-1",
        repository_id="repository-1",
        workspace_root=tmp_path,
    )

    assert not result.detection.supported
    assert result.active_bindings == ()
    assert repository.versions == {}
    assert repository.activation_calls == []


def test_bootstrap_blocks_supported_stack_without_coverage_prerequisites(
    tmp_path: Path,
) -> None:
    _write_supported_project(tmp_path, with_coverage=False)

    with pytest.raises(ValueError, match="JaCoCo plugin"):
        ProjectProfileBootstrapper(
            profiles=ProfileCatalog.load(ROOT / "profiles"),
            repository=_ProfileRepository(),
        ).ensure(
            project_id="project-1",
            repository_id="repository-1",
            workspace_root=tmp_path,
        )


def test_bootstrap_upgrades_only_an_older_binding_of_the_same_builtin_profile(
    tmp_path: Path,
) -> None:
    _write_supported_project(tmp_path)
    repository = _ProfileRepository()
    profile_value: object = json.loads(
        (ROOT / "profiles/springboot15-thymeleaf-gradle-code-framework-profile.example.json")
        .read_text(encoding="utf-8")
    )
    assert isinstance(profile_value, dict)
    old_profile: dict[str, Any] = {
        **profile_value,
        "profile_version": "1.0.0",
    }
    old_profile["anchor_extractors"] = [
        value
        for value in old_profile["anchor_extractors"]
        if value != "javascript_symbol"
    ]
    repository.bindings[("project-1", "code-framework:repository-1")] = (
        ActiveProfileBinding(
            project_id="project-1",
            binding_key="code-framework:repository-1",
            profile_version_id="springboot15-thymeleaf-gradle-code-framework-1.0.0",
            activated_by="automation:operamind",
            activated_at=datetime.now(UTC),
            profile=old_profile,
        )
    )

    result = ProjectProfileBootstrapper(
        profiles=ProfileCatalog.load(ROOT / "profiles"),
        repository=repository,
    ).ensure(
        project_id="project-1",
        repository_id="repository-1",
        workspace_root=tmp_path,
    )

    code_binding = next(
        binding
        for binding in result.active_bindings
        if binding.binding_key == "code-framework:repository-1"
    )
    assert code_binding.profile["profile_version"] == "1.0.4"
    assert "javascript_symbol" in code_binding.profile["anchor_extractors"]
    assert result.activated_binding_keys == (
        "code-framework:repository-1",
        "command-execution:repository-1",
    )


def test_gradle_wrapper_detection_accepts_windows_batch_wrapper(tmp_path: Path) -> None:
    batch = tmp_path / "gradlew.bat"
    batch.write_text("@echo off\r\n", encoding="utf-8")

    assert _gradle_wrapper(tmp_path, platform_name="nt") == batch


def test_project_list_stack_context_reports_a_missing_local_workspace(tmp_path: Path) -> None:
    context = _safe_project_stack_context(tmp_path / "missing-workspace")

    assert context["detection_status"] == "unavailable"
    assert context["quality_readiness"] == "unavailable"
    assert context["missing_signals"] == ["コード Workspace を読み取れません"]


def _write_supported_project(root: Path, *, with_coverage: bool = True) -> None:
    (root / "gradle" / "wrapper").mkdir(parents=True)
    (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
        "distributionUrl=https://services.gradle.org/distributions/gradle-4.10.3-bin.zip\n",
        encoding="utf-8",
    )
    coverage_configuration = (
        "apply plugin: 'jacoco'\n"
        "jacocoTestReport { reports { xml.enabled true } }"
        if with_coverage
        else ""
    )
    build_text = """
buildscript {
    ext {
        springBootVersion = '1.5.22.RELEASE'
    }
    dependencies {
        classpath("org.springframework.boot:spring-boot-gradle-plugin:${springBootVersion}")
    }
}
apply plugin: 'org.springframework.boot'
__COVERAGE_CONFIGURATION__
dependencies {
    compile 'org.springframework.boot:spring-boot-starter-thymeleaf'
}
""".replace("__COVERAGE_CONFIGURATION__", coverage_configuration)
    (root / "build.gradle").write_text(
        build_text,
        encoding="utf-8",
    )
    template = root / "app" / "src" / "main" / "resources" / "templates" / "expense"
    template.mkdir(parents=True)
    (template / "list.html").write_text(
        '<html xmlns:th="http://www.thymeleaf.org"></html>',
        encoding="utf-8",
    )
    if with_coverage:
        test = root / "src" / "test" / "java" / "example" / "SmokeTest.java"
        test.parent.mkdir(parents=True)
        test.write_text("class SmokeTest {}\n", encoding="utf-8")
