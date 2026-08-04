"""Project-specific Office structure learning through VS Code GitHub Copilot."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from psycopg import Connection

from operamind.application.document_diff import DocumentDiffService
from operamind.contracts import ContractCatalog
from operamind.domain.document_conventions import (
    ConventionMatcher,
    DocumentConvention,
    DocumentSignals,
    MatchStatus,
)
from operamind.infrastructure.documents import DocumentSignalExtractorRegistry
from operamind.infrastructure.postgres.document_profile_learning_repository import (
    DocumentProfileLearningRecord,
    DocumentProfileLearningRepository,
)
from operamind.infrastructure.postgres.profile_repository import ProfileRepository
from operamind.infrastructure.postgres.project_onboarding_repository import (
    ProjectOnboardingRepository,
)
from operamind.profiles import ProfileCatalog

_MAX_SIGNAL_VALUES = 120
_MAX_DOCUMENTS = 500
_SAFE_ID = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class DocumentLearningStructure:
    payload: dict[str, Any]
    digest: str
    sample_count: int


class DocumentProfileLearningService:
    """Extract bounded structure, validate Copilot drafts, and activate Profiles."""

    def __init__(self, *, connection: Connection[Any], repository_root: Path) -> None:
        self._connection = connection
        self._root = repository_root.resolve()
        self._contracts = ContractCatalog.load(self._root / "contracts")
        self._profiles = ProfileCatalog.load(self._root / "profiles")
        self._profile_repository = ProfileRepository(connection, self._profiles)
        self._runs = DocumentProfileLearningRepository(connection)
        self._onboarding = ProjectOnboardingRepository(connection)
        self._extractors = DocumentSignalExtractorRegistry.default()
        self._diff = DocumentDiffService(
            extractors=self._extractors,
            contracts=self._contracts,
        )

    def extract_structure(
        self, *, project_id: str, document_roots: tuple[Path, ...]
    ) -> DocumentLearningStructure:
        samples: list[dict[str, object]] = []
        seen: set[Path] = set()
        for root in document_roots:
            resolved_root = root.resolve(strict=True)
            for directory, names, filenames in os.walk(resolved_root, followlinks=False):
                names[:] = sorted(name for name in names if not name.startswith("."))
                for filename in sorted(filenames):
                    path = Path(directory) / filename
                    if (
                        filename.startswith("~$")
                        or path.suffix.casefold() not in self._extractors.supported_suffixes
                    ):
                        continue
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(resolved_root) or resolved in seen:
                        continue
                    seen.add(resolved)
                    signals = self._extractors.extract(resolved)
                    sections = self._extractors.extract_learning_structure(resolved)
                    sample_id = _id("document-sample", project_id, resolved.as_uri())
                    samples.append(
                        {
                            "sample_id": sample_id,
                            "source_ref": resolved.as_uri(),
                            "logical_name": resolved.name,
                            "format": resolved.suffix.casefold().lstrip("."),
                            "signals": _signals_view(signals),
                            "sections": [
                                {"name": name, "signals": _signals_view(section_signals)}
                                for name, section_signals in sections
                            ],
                        }
                    )
                    if len(samples) > _MAX_DOCUMENTS:
                        raise ValueError(f"設計書は {_MAX_DOCUMENTS} 件以内で登録してください")
        if not samples:
            raise ValueError("学習対象の XLSX/DOCX 設計書がありません")
        payload: dict[str, Any] = {
            "schema_version": "v1",
            "project_id": project_id,
            "samples": samples,
        }
        payload["structure_identity"] = _structure_identity(samples)
        canonical = _canonical_json(cast(dict[str, Any], payload["structure_identity"]))
        return DocumentLearningStructure(
            payload=payload,
            digest=hashlib.sha256(canonical.encode()).hexdigest(),
            sample_count=len(samples),
        )

    def ensure_task(
        self,
        *,
        project_id: str,
        onboarding_run_id: str,
        settings_revision: int,
        document_roots: tuple[Path, ...],
        actor: str,
        instruction: str | None = None,
        force: bool = False,
    ) -> tuple[DocumentProfileLearningRecord, bool]:
        structure = self.extract_structure(project_id=project_id, document_roots=document_roots)
        if not force:
            confirmed = self._runs.confirmed_for_structure(
                project_id=project_id,
                structure_digest=structure.digest,
            )
            if confirmed is not None:
                return confirmed, True
        else:
            self._runs.supersede_active(
                project_id=project_id,
                settings_revision=settings_revision,
                reason="利用者が設計書の再学習を要求しました",
            )
        latest = self._runs.latest(project_id)
        if (
            latest is not None
            and latest.settings_revision == settings_revision
            and latest.source_structure_digest == structure.digest
            and latest.status in {"pending", "claimed", "in_progress", "draft_ready"}
        ):
            return latest, False
        previous = self._runs.latest_confirmed(project_id)
        previous_ids = (
            self._runs.profile_version_ids(previous.learning_run_id)
            if previous is not None
            else ()
        )
        run = self._runs.create(
            learning_run_id=f"document-learning-{uuid4().hex}",
            project_id=project_id,
            onboarding_run_id=onboarding_run_id,
            settings_revision=settings_revision,
            requested_by=actor,
            instruction=instruction,
            source_structure=structure.payload,
            source_structure_digest=structure.digest,
            sample_count=structure.sample_count,
            previous_profile_version_ids=previous_ids,
        )
        return run, False

    def latest(self, project_id: str) -> dict[str, object] | None:
        record = self._runs.latest(project_id)
        return record.public_view() if record is not None else None

    def claim_next(
        self, *, workspace_root: Path, consumer_id: str
    ) -> dict[str, object] | None:
        claim = self._runs.claim_next(
            workspace_root=str(workspace_root.resolve(strict=True)),
            consumer_id=consumer_id,
        )
        return (
            self._bridge_view(claim.record, claim_token=claim.claim_token)
            if claim is not None
            else None
        )

    def accept(
        self,
        *,
        learning_run_id: str,
        workspace_root: Path,
        consumer_id: str,
        claim_token: str,
        actor: str,
    ) -> dict[str, object]:
        record = self._runs.accept(
            learning_run_id=learning_run_id,
            workspace_root=str(workspace_root.resolve(strict=True)),
            consumer_id=consumer_id,
            claim_token=claim_token,
            actor=actor,
        )
        return self._bridge_view(record, claim_token=claim_token)

    def resume(
        self,
        *,
        learning_run_id: str,
        workspace_root: Path,
        consumer_id: str,
        claim_token: str,
    ) -> dict[str, object]:
        record = self._runs.resume(
            learning_run_id=learning_run_id,
            workspace_root=str(workspace_root.resolve(strict=True)),
            consumer_id=consumer_id,
            claim_token=claim_token,
        )
        return self._bridge_view(
            record,
            claim_token=claim_token
            if record.status in {"claimed", "in_progress"}
            else None,
        )

    def cancel(
        self,
        *,
        learning_run_id: str,
        workspace_root: Path,
        consumer_id: str,
        claim_token: str,
        reason: str,
    ) -> dict[str, object]:
        return self._bridge_view(
            self._runs.cancel(
                learning_run_id=learning_run_id,
                workspace_root=str(workspace_root.resolve(strict=True)),
                consumer_id=consumer_id,
                claim_token=claim_token,
                reason=reason,
            )
        )

    def mcp_context(
        self,
        *,
        learning_run_id: str,
        workspace_root: Path,
        consumer_id: str,
        claim_token: str,
    ) -> dict[str, object]:
        record = self._runs.resume(
            learning_run_id=learning_run_id,
            workspace_root=str(workspace_root.resolve(strict=True)),
            consumer_id=consumer_id,
            claim_token=claim_token,
        )
        seed_profiles: list[dict[str, Any]] = []
        for path in sorted((self._root / "profiles").glob("*.json")):
            loaded = _load_object(path)
            if loaded.get("profile_type") == "DocumentConventionProfile":
                seed_profiles.append(loaded)
        previous_profiles = [
            profile
            for version_id in record.previous_profile_version_ids
            if (profile := self._profile_repository.get_version(version_id)) is not None
        ]
        previous = self._runs.latest_confirmed(record.project_id)
        profile_contract = _load_object(
            self._root / "profiles" / "schemas" / "document-convention-profile.schema.json"
        )
        return {
            "task": self._bridge_view(record)["task"],
            "inputs": {
                "source_structure": record.source_structure,
                "previous_profile_version_ids": list(record.previous_profile_version_ids),
                "previous_profiles": previous_profiles,
                "structure_diff": _structure_diff(
                    previous.source_structure if previous is not None else None,
                    record.source_structure,
                ),
                "seed_profiles": seed_profiles,
                "profile_contract": profile_contract,
                "instruction": record.instruction,
            },
            "constraints": {
                "profile_id_prefix": _project_profile_prefix(record.project_id),
                "coverage_required_percent": 100,
                "no_source_file_edits": True,
                "profile_schema": "DocumentConventionProfile",
            },
            "stage_contract": {
                "label": "設計書学習",
                "output_stage": "document_profile_learning",
                "tool": "copilot_record_change_outputs",
                "stop_after_recording": True,
            },
            "stage_status": {
                "task_stage": "document_profile_learning",
                "flow_stage": "project_onboarding",
                "task_state": record.status,
                "outcome": "ready",
                "requires_confirmation": False,
                "next_action": "continue_current_stage",
                "message": "Project 専用の設計書 Profile 草案を作成してください。",
                "blocking_reasons": [],
            },
        }

    def record_draft(
        self,
        *,
        learning_run_id: str,
        workspace_root: Path,
        consumer_id: str,
        claim_token: str,
        draft: dict[str, Any],
    ) -> dict[str, object]:
        record = self._runs.get(learning_run_id)
        if record is None:
            raise ValueError("Document Profile learning run does not exist")
        self._contracts.validate_artifact(draft)
        self._validate_draft_identity(record, draft)
        profiles = self._validated_profiles(record, draft)
        covered, generated_ambiguities = self._assess_coverage(record, draft, profiles)
        submitted_ambiguities = cast(list[dict[str, object]], draft["ambiguities"])
        normalized = dict(draft)
        normalized_ambiguities: list[dict[str, object]] = list(submitted_ambiguities)
        normalized_ambiguities.extend(
            cast(dict[str, object], dict(item)) for item in generated_ambiguities
        )
        normalized["ambiguities"] = normalized_ambiguities
        ambiguity_count = len(normalized_ambiguities)
        coverage_percent = round(covered * 100 / record.sample_count, 2)
        stored = self._runs.record_draft(
            learning_run_id=learning_run_id,
            workspace_root=str(workspace_root.resolve(strict=True)),
            consumer_id=consumer_id,
            claim_token=claim_token,
            draft=normalized,
            covered_sample_count=covered,
            coverage_percent=coverage_percent,
            ambiguity_count=ambiguity_count,
        )
        ready = coverage_percent == 100 and ambiguity_count == 0
        return {
            "learning": stored.public_view(),
            "stage_status": {
                "task_stage": "document_profile_learning",
                "flow_stage": "project_onboarding",
                "task_state": stored.status,
                "outcome": "accepted" if ready else "blocked",
                "requires_confirmation": ready,
                "next_action": "wait_for_confirmation"
                if ready
                else "resolve_blocker",
                "message": (
                    "設計書 Profile 草案を受け付けました。Web の確認を待ってください。"
                    if ready
                    else "未カバーまたは曖昧な設計書があります。草案を再生成してください。"
                ),
                "blocking_reasons": [
                    str(item["description"]) for item in normalized_ambiguities
                ],
            },
        }

    def confirm(self, *, project_id: str, learning_run_id: str, actor: str) -> dict[str, object]:
        record = self._runs.get(learning_run_id)
        if record is None or record.project_id != project_id:
            raise ValueError("Document Profile learning run does not exist")
        if record.draft_payload is None:
            raise ValueError("Document Profile draft does not exist")
        profiles = self._validated_profiles(record, record.draft_payload)
        with self._connection.transaction():
            version_ids: list[str] = []
            for profile in profiles.values():
                version_id = _profile_version_id(profile)
                version_ids.append(version_id)
                self._profile_repository.store_version(
                    profile_version_id=version_id,
                    profile=profile,
                )
                self._profile_repository.activate(
                    activation_event_id=_id(
                        "profile-activation",
                        project_id,
                        learning_run_id,
                        version_id,
                    ),
                    project_id=project_id,
                    binding_key=f"document:{profile['document_type']}",
                    profile_version_id=version_id,
                    activated_by=actor,
                    reason=f"設計書学習 {learning_run_id} の確認済み Profile",
                )
            self._runs.bind_confirmed_profiles(
                learning_run_id=learning_run_id,
                project_id=project_id,
                profile_version_ids=tuple(version_ids),
            )
            confirmed = self._runs.confirm(learning_run_id=learning_run_id, actor=actor)
            self._onboarding.resume_after_learning(
                onboarding_run_id=confirmed.onboarding_run_id,
                learning_run_id=learning_run_id,
                settings_revision=confirmed.settings_revision,
            )
        return confirmed.public_view()

    def _validated_profiles(
        self, record: DocumentProfileLearningRecord, draft: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        raw_profiles = cast(list[object], draft["profiles"])
        profiles: dict[str, dict[str, Any]] = {}
        prefix = _project_profile_prefix(record.project_id)
        active: dict[str, dict[str, Any]] = {}
        for version_id in record.previous_profile_version_ids:
            previous = self._profile_repository.get_version(version_id)
            if previous is not None:
                active[str(previous["profile_id"])] = previous
        for raw in raw_profiles:
            if not isinstance(raw, dict):
                raise ValueError("Document Profile draft profiles must be objects")
            profile = cast(dict[str, Any], raw)
            self._profiles.validate_profile(profile)
            profile_id = str(profile["profile_id"])
            if not profile_id.startswith(prefix):
                raise ValueError(f"Project Profile ID must start with {prefix}")
            if profile_id in profiles:
                raise ValueError("Document Profile draft contains duplicate Profile IDs")
            current_profile = active.get(profile_id)
            current_version = (
                str(current_profile["profile_version"])
                if current_profile is not None
                else None
            )
            proposed_version = str(profile["profile_version"])
            if current_version is None and proposed_version != "1.0.0":
                raise ValueError("A new Project Profile must start at version 1.0.0")
            if current_version is not None:
                proposed = _semver(proposed_version)
                current = _semver(current_version)
                if proposed < current:
                    raise ValueError("A relearned Project Profile version cannot decrease")
                if proposed == current and profile != current_profile:
                    raise ValueError(
                        "A changed Project Profile must increase its semantic version"
                    )
            profiles[profile_id] = profile
        return profiles

    def _assess_coverage(
        self,
        record: DocumentProfileLearningRecord,
        draft: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
    ) -> tuple[int, list[dict[str, str]]]:
        samples = {
            str(sample["sample_id"]): cast(dict[str, Any], sample)
            for sample in cast(list[dict[str, object]], record.source_structure["samples"])
        }
        assignments: dict[str, dict[str, object]] = {}
        ambiguities: list[dict[str, str]] = []
        for raw in cast(list[dict[str, object]], draft["document_assignments"]):
            sample_id = str(raw["sample_id"])
            if sample_id in assignments:
                raise ValueError("A document sample can only have one Profile assignment")
            assignments[sample_id] = raw
        covered = 0
        matcher = ConventionMatcher()
        for sample_id, sample in samples.items():
            assignment = assignments.get(sample_id)
            reason: str | None = None
            if assignment is None:
                reason = "Profile assignment is missing"
            else:
                profile = profiles.get(str(assignment["profile_id"]))
                if profile is None:
                    reason = "Assigned Profile does not exist in this draft"
                else:
                    convention = DocumentConvention.from_validated_profile(profile)
                    observed = _signals_from_view(cast(dict[str, object], sample["signals"]))
                    match = matcher.match(convention, observed)
                    if (
                        match.status is not MatchStatus.AUTO_MATCHED
                        or match.selected_variant_id != assignment["variant_id"]
                    ):
                        reason = "Assigned Profile/Variant does not uniquely match the sample"
                    else:
                        try:
                            built = self._diff.build_snapshot(
                                path=_path_from_file_uri(str(sample["source_ref"])),
                                snapshot_id=f"learning-{record.learning_run_id}",
                                fact_type=convention.fact_type,
                                convention=convention,
                                stable_key_namespace=sample_id,
                            )
                            if not built.snapshot.facts:
                                reason = "Profile produced no Canonical facts"
                        except (OSError, ValueError) as error:
                            reason = str(error)
            if reason is None:
                covered += 1
            else:
                ambiguities.append(
                    {
                        "ambiguity_id": _id(
                            "learning-ambiguity", record.learning_run_id, sample_id
                        ),
                        "sample_id": sample_id,
                        "description": reason,
                        "suggested_action": (
                            "Profile の Signal、Field Alias、Stable Key を見直してください"
                        ),
                    }
                )
        unknown = sorted(set(assignments) - set(samples))
        if unknown:
            raise ValueError("Document Profile assignments contain unknown sample IDs")
        return covered, ambiguities

    @staticmethod
    def _validate_draft_identity(
        record: DocumentProfileLearningRecord, draft: dict[str, Any]
    ) -> None:
        if (
            draft.get("learning_run_id") != record.learning_run_id
            or draft.get("project_id") != record.project_id
            or draft.get("source_structure_digest") != record.source_structure_digest
        ):
            raise ValueError("Document Profile draft identity differs from the learning task")

    @staticmethod
    def _bridge_view(
        record: DocumentProfileLearningRecord, *, claim_token: str | None = None
    ) -> dict[str, object]:
        view: dict[str, object] = {
            "task": {
                "coding_task_id": record.learning_run_id,
                "change_request_id": f"project-learning:{record.project_id}",
                "project_id": record.project_id,
                "execution_mode": "copilot_change_task",
                "task_kind": "document_profile_learning",
                "initial_stage": "document_profile_learning",
                "task_summary": "Project 設計書の構造を学習し、専用 Profile 草案を作成する",
            },
            "state": record.status,
            "current_stage": "document_profile_learning",
        }
        if claim_token is not None:
            view["claim_token"] = claim_token
        return view


def _signals_view(signals: DocumentSignals) -> dict[str, object]:
    return {
        "filename": signals.filename,
        "sheet_names": sorted(signals.sheet_names)[:_MAX_SIGNAL_VALUES],
        "headings": sorted(signals.headings)[:_MAX_SIGNAL_VALUES],
        "headers": sorted(signals.headers)[:_MAX_SIGNAL_VALUES],
        "business_terms": sorted(signals.business_terms)[:_MAX_SIGNAL_VALUES],
    }


def _signals_from_view(value: dict[str, object]) -> DocumentSignals:
    return DocumentSignals.from_raw(
        filename=str(value["filename"]),
        sheet_names=tuple(str(item) for item in cast(list[object], value["sheet_names"])),
        headings=tuple(str(item) for item in cast(list[object], value["headings"])),
        headers=tuple(str(item) for item in cast(list[object], value["headers"])),
        business_terms=tuple(
            str(item) for item in cast(list[object], value["business_terms"])
        ),
    )


def _path_from_file_uri(value: str) -> Path:
    from urllib.parse import urlsplit
    from urllib.request import url2pathname

    parsed = urlsplit(value)
    if parsed.scheme != "file":
        raise ValueError("Document learning source must be a local file URI")
    if parsed.netloc not in {"", "localhost"}:
        raw_path = f"//{parsed.netloc}{parsed.path}"
    else:
        raw_path = parsed.path
    converted = url2pathname(raw_path)
    if os.name == "nt" and re.match(r"^/[A-Za-z]:", converted):
        converted = converted[1:]
    return Path(converted).resolve(strict=True)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return value


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _profile_version_id(profile: dict[str, Any]) -> str:
    return f"{profile['profile_id']}-{profile['profile_version']}"


def _semver(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _structure_diff(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, object]:
    previous_source = (
        cast(dict[str, Any], previous.get("structure_identity", previous))
        if previous is not None
        else {}
    )
    current_source = cast(dict[str, Any], current.get("structure_identity", current))
    previous_samples = {
        str(item["source_ref"]): item
        for item in cast(list[dict[str, object]], previous_source.get("samples", []))
    }
    current_samples = {
        str(item["source_ref"]): item
        for item in cast(list[dict[str, object]], current_source.get("samples", []))
    }
    added = sorted(set(current_samples) - set(previous_samples))
    removed = sorted(set(previous_samples) - set(current_samples))
    changed = sorted(
        source_ref
        for source_ref in set(previous_samples) & set(current_samples)
        if previous_samples[source_ref] != current_samples[source_ref]
    )
    return {
        "has_previous_version": previous is not None,
        "added_sources": added,
        "removed_sources": removed,
        "changed_sources": changed,
    }


def _structure_identity(samples: list[dict[str, object]]) -> dict[str, object]:
    """Exclude changing business values while retaining parsable Office structure."""

    def structural_signals(value: object) -> dict[str, object]:
        signals = cast(dict[str, object], value)
        return {
            "sheet_names": signals.get("sheet_names", []),
            "headings": signals.get("headings", []),
            "headers": signals.get("headers", []),
        }

    return {
        "samples": [
            {
                "sample_id": sample["sample_id"],
                "source_ref": sample["source_ref"],
                "format": sample["format"],
                "signals": {
                    "sheet_names": sorted(
                        {
                            str(value)
                            for section in cast(
                                list[dict[str, object]], sample["sections"]
                            )
                            for value in cast(
                                list[object],
                                cast(dict[str, object], section["signals"])["sheet_names"],
                            )
                        }
                    ),
                    "headings": sorted(
                        {
                            str(value)
                            for section in cast(
                                list[dict[str, object]], sample["sections"]
                            )
                            for value in cast(
                                list[object],
                                cast(dict[str, object], section["signals"])["headings"],
                            )
                        }
                    ),
                    "headers": sorted(
                        {
                            str(value)
                            for section in cast(
                                list[dict[str, object]], sample["sections"]
                            )
                            for value in cast(
                                list[object],
                                cast(dict[str, object], section["signals"])["headers"],
                            )
                        }
                    ),
                },
                "sections": [
                    {
                        "name": section["name"],
                        "signals": structural_signals(section["signals"]),
                    }
                    for section in cast(list[dict[str, object]], sample["sections"])
                ],
            }
            for sample in samples
        ]
    }


def _slug(value: str) -> str:
    normalized = _SAFE_ID.sub("-", value.casefold()).strip("-")
    return normalized or "project"


def _project_profile_prefix(project_id: str) -> str:
    identity = hashlib.sha256(project_id.encode()).hexdigest()[:8]
    return f"project-{_slug(project_id)}-{identity}-"


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"
