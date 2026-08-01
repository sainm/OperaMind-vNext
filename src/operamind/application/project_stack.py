"""Deterministic target-project stack detection and internal Profile bootstrap."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from operamind.infrastructure.postgres import ActiveProfileBinding
from operamind.profiles import ProfileCatalog

SPRINGBOOT15_THYMELEAF_GRADLE = "springboot15-thymeleaf-gradle"
_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".gradle",
        ".idea",
        ".vscode",
        "build",
        "node_modules",
        "out",
        "target",
    }
)
_SPRING_BOOT_15_PATTERNS = (
    re.compile(r"springBootVersion\s*=\s*['\"]1\.5\.[^'\"]+['\"]"),
    re.compile(r"spring-boot-gradle-plugin:1\.5\.[^'\"\s)]+"),
    re.compile(
        r"id\s*(?:\(\s*)?['\"]org\.springframework\.boot['\"]"
        r"(?:\s*\))?\s*version\s*['\"]1\.5\.[^'\"]+['\"]"
    ),
)
_THYMELEAF_PATTERNS = (
    re.compile(r"spring-boot-starter-thymeleaf"),
    re.compile(r"(?:org\.)?thymeleaf"),
)
_PROFILE_FILES = {
    "CodeFrameworkProfile": (
        "springboot15-thymeleaf-gradle-code-framework-profile.example.json",
        "code-framework",
    ),
    "CommandExecutionProfile": (
        "springboot15-thymeleaf-gradle-command-profile.example.json",
        "command-execution",
    ),
}


@dataclass(frozen=True, slots=True)
class ProjectStackDetection:
    """Evidence-backed classification used by the internal change task."""

    stack_id: str | None
    status: str
    evidence: tuple[str, ...]
    missing_signals: tuple[str, ...]
    gradle_wrapper_command: str | None = None

    @property
    def supported(self) -> bool:
        return self.status == "supported" and self.stack_id is not None

    def copilot_context(self) -> dict[str, object]:
        """Return bounded, business-relevant constraints for VS Code Copilot."""

        if not self.supported:
            return {
                "detection_status": self.status,
                "stack_id": None,
                "evidence": list(self.evidence),
                "missing_signals": list(self.missing_signals),
            }
        wrapper = self.gradle_wrapper_command or (
            "./gradlew.bat" if os.name == "nt" else "./gradlew"
        )
        return {
            "detection_status": "supported",
            "stack_id": self.stack_id,
            "framework": "Spring Boot 1.5",
            "template_engine": "Thymeleaf",
            "build_system": "Gradle Wrapper",
            "source_kinds": ["Java", "Thymeleaf HTML", "Gradle"],
            "change_constraints": [
                "Spring Boot 1.5、Thymeleaf、Gradle Wrapper を維持する",
                "変更要件に含まれない Framework / Build Tool の更新を行わない",
                "JAVA_HOME に設定された対象工程互換 JDK を使用する",
            ],
            "compile_command": [wrapper, "classes", "testClasses", "--no-daemon"],
            "test_command": [wrapper, "test", "--no-daemon"],
            "build_command": [wrapper, "build", "--no-daemon"],
            "evidence": list(self.evidence),
            "missing_signals": [],
        }


@dataclass(frozen=True, slots=True)
class ProjectProfileBootstrapResult:
    """Internal result; never exposed as an extra Web workflow stage."""

    detection: ProjectStackDetection
    active_bindings: tuple[ActiveProfileBinding, ...]
    activated_binding_keys: tuple[str, ...]


class ProjectProfileRepository(Protocol):
    """Minimal persistence boundary needed by automatic target-stack setup."""

    def get_active(
        self, *, project_id: str, binding_key: str
    ) -> ActiveProfileBinding | None: ...

    def store_version(self, *, profile_version_id: str, profile: dict[str, Any]) -> str: ...

    def activate(
        self,
        *,
        activation_event_id: str,
        project_id: str,
        binding_key: str,
        profile_version_id: str,
        activated_by: str,
        reason: str,
    ) -> bool: ...


def detect_project_stack(workspace_root: Path) -> ProjectStackDetection:
    """Recognize the supported legacy stack without guessing from a project name."""

    root = workspace_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Workspace root must be a directory")

    build_files = _build_files(root)
    build_text = "\n".join(_read_bounded(path) for path in build_files)
    evidence: list[str] = []
    missing: list[str] = []

    gradle_wrapper = _gradle_wrapper(root)
    wrapper_properties = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if gradle_wrapper is not None and wrapper_properties.is_file() and build_files:
        evidence.extend(
            [
                _relative(root, gradle_wrapper),
                _relative(root, wrapper_properties),
                *(_relative(root, path) for path in build_files),
            ]
        )
    else:
        missing.append("Gradle Wrapper と build.gradle")

    spring_boot_15 = any(pattern.search(build_text) for pattern in _SPRING_BOOT_15_PATTERNS)
    if spring_boot_15:
        evidence.append("build.gradle:Spring Boot 1.5")
    else:
        missing.append("Spring Boot 1.5 の Gradle 設定")

    thymeleaf_dependency = any(pattern.search(build_text) for pattern in _THYMELEAF_PATTERNS)
    template_files = _thymeleaf_templates(root)
    if thymeleaf_dependency and template_files:
        evidence.extend(_relative(root, path) for path in template_files)
    else:
        missing.append("Thymeleaf 依存関係と src/main/resources/templates/*.html")

    unique_evidence = tuple(dict.fromkeys(evidence))
    if missing:
        return ProjectStackDetection(
            stack_id=None,
            status="unrecognized",
            evidence=unique_evidence,
            missing_signals=tuple(missing),
        )
    if gradle_wrapper is None:
        raise AssertionError("Supported stack requires a Gradle wrapper")
    return ProjectStackDetection(
        stack_id=SPRINGBOOT15_THYMELEAF_GRADLE,
        status="supported",
        evidence=unique_evidence,
        missing_signals=(),
        gradle_wrapper_command=f"./{gradle_wrapper.name}",
    )


def _gradle_wrapper(root: Path, *, platform_name: str | None = None) -> Path | None:
    """Find the checked-in Gradle wrapper for either POSIX or Windows."""

    platform = os.name if platform_name is None else platform_name
    names = ("gradlew.bat", "gradlew") if platform == "nt" else ("gradlew", "gradlew.bat")
    return next((root / name for name in names if (root / name).is_file()), None)


class ProjectProfileBootstrapper:
    """Activate built-in Profiles only when strong stack evidence and no binding exist."""

    def __init__(
        self,
        *,
        profiles: ProfileCatalog,
        repository: ProjectProfileRepository,
    ) -> None:
        self._profiles = profiles
        self._repository = repository

    def ensure(
        self,
        *,
        project_id: str,
        repository_id: str,
        workspace_root: Path,
        actor: str = "automation:operamind",
    ) -> ProjectProfileBootstrapResult:
        if not project_id.strip() or not repository_id.strip() or not actor.strip():
            raise ValueError("Project Profile bootstrap identity must not be blank")
        detection = detect_project_stack(workspace_root)
        if not detection.supported:
            return ProjectProfileBootstrapResult(detection, (), ())

        active: list[ActiveProfileBinding] = []
        activated: list[str] = []
        for profile_type, (filename, binding_prefix) in _PROFILE_FILES.items():
            binding_key = f"{binding_prefix}:{repository_id}"
            current = self._repository.get_active(
                project_id=project_id,
                binding_key=binding_key,
            )
            if current is not None:
                if current.profile.get("profile_type") != profile_type:
                    raise ValueError(
                        f"Active Profile binding has unexpected type: {binding_key}"
                    )
                active.append(current)
                continue

            profile = _load_profile(self._profiles.root / filename)
            if profile.get("profile_type") != profile_type:
                raise ValueError(f"Built-in Profile has unexpected type: {filename}")
            profile_version_id = (
                f"{profile['profile_id']}-{binding_prefix}-{profile['profile_version']}"
            )
            self._repository.store_version(
                profile_version_id=profile_version_id,
                profile=profile,
            )
            activation_event_id = _activation_event_id(
                project_id=project_id,
                repository_id=repository_id,
                binding_key=binding_key,
                profile_version_id=profile_version_id,
            )
            created = self._repository.activate(
                activation_event_id=activation_event_id,
                project_id=project_id,
                binding_key=binding_key,
                profile_version_id=profile_version_id,
                activated_by=actor,
                reason=(
                    "Spring Boot 1.5、Thymeleaf、Gradle Wrapper の"
                    "確定的な工程証跡から自動選択"
                ),
            )
            if created:
                activated.append(binding_key)
            bound = self._repository.get_active(
                project_id=project_id,
                binding_key=binding_key,
            )
            if bound is None:
                raise RuntimeError(f"Automatic Profile activation was not persisted: {binding_key}")
            active.append(bound)
        return ProjectProfileBootstrapResult(
            detection=detection,
            active_bindings=tuple(active),
            activated_binding_keys=tuple(activated),
        )


def _build_files(root: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in _IGNORED_DIRECTORIES)
        current = Path(directory)
        for filename in ("build.gradle", "build.gradle.kts"):
            if filename in filenames:
                path = (current / filename).resolve()
                if path.is_relative_to(root):
                    found.append(path)
        if len(found) >= 100:
            break
    return tuple(sorted(found))


def _thymeleaf_templates(root: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in _IGNORED_DIRECTORIES)
        current = Path(directory)
        relative_parts = current.relative_to(root).parts
        if _contains_parts(
            relative_parts,
            ("src", "main", "resources", "templates"),
        ):
            for filename in sorted(filenames):
                if filename.endswith(".html"):
                    path = (current / filename).resolve()
                    if path.is_relative_to(root):
                        found.append(path)
                        if len(found) >= 20:
                            return tuple(found)
    return tuple(found)


def _read_bounded(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(1_048_576)


def _load_profile(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Built-in Profile is not a JSON object: {path.name}")
    return cast(dict[str, Any], value)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _activation_event_id(
    *,
    project_id: str,
    repository_id: str,
    binding_key: str,
    profile_version_id: str,
) -> str:
    digest = hashlib.sha256(
        "\0".join((project_id, repository_id, binding_key, profile_version_id)).encode()
    ).hexdigest()
    return f"automatic-profile-activation-{digest[:32]}"


def _contains_parts(value: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    return any(
        value[index : index + len(expected)] == expected
        for index in range(len(value) - len(expected) + 1)
    )
