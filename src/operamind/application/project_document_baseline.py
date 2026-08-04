"""Automatic Canonical document and local Embedding baseline for a new Project."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

from operamind.application.document_diff import DocumentDiffService
from operamind.application.search_index_build import (
    SearchIndexBuildRequest,
    SearchIndexBuildService,
)
from operamind.contracts import ContractCatalog
from operamind.domain import CanonicalDocumentNodeBuilder
from operamind.domain.document_conventions import (
    ConventionMatcher,
    DocumentConvention,
    MatchStatus,
)
from operamind.infrastructure.documents import DocumentSignalExtractorRegistry
from operamind.infrastructure.embeddings import OpenAICompatibleEmbeddingProvider
from operamind.infrastructure.postgres import (
    CanonicalRepository,
    DocumentNodeRepository,
    DocumentSnapshotWrite,
    ProfileRepository,
    SnapshotStatus,
)
from operamind.infrastructure.postgres.document_profile_learning_repository import (
    DocumentProfileLearningRepository,
)
from operamind.profiles import ProfileCatalog

_MAX_DOCUMENTS = 500


@dataclass(frozen=True, slots=True)
class ProjectDocumentBaselineResult:
    """Committed baseline identity returned to the Project initialization screen."""

    snapshot_id: str
    document_count: int
    index_build_id: str
    generated_vector_count: int
    embedding_profile_binding_key: str


@dataclass(frozen=True, slots=True)
class _DocumentCandidate:
    path: Path
    profile_source: str
    profile_id: str
    document_type: str
    fact_type: str
    score: float


@dataclass(frozen=True, slots=True)
class ProjectDocumentDiscoveryResult:
    """Profile-backed discovery result safe to expose in Project preflight."""

    candidates: tuple[_DocumentCandidate, ...]
    ignored_documents: tuple[str, ...]
    review_required: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return bool(self.candidates) and not self.review_required

    def public_summary(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "blocked",
            "document_count": len(self.candidates),
            "documents": [
                {
                    "path": str(candidate.path),
                    "profile_id": candidate.profile_id,
                    "document_type": candidate.document_type,
                    "fact_type": candidate.fact_type,
                    "match_score": round(candidate.score, 6),
                }
                for candidate in self.candidates
            ],
            "ignored_documents": list(self.ignored_documents),
            "review_required": list(self.review_required),
        }


@dataclass(frozen=True, slots=True)
class ProjectDocumentSnapshotResult:
    snapshot_id: str
    document_count: int


class ProjectDocumentBaselineService:
    """Discover supported design documents and publish one ready Search Index."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        repository_root: Path,
    ) -> None:
        self._connection = connection
        self._root = repository_root.resolve()
        self._contracts = ContractCatalog.load(self._root / "contracts")
        self._profiles = ProfileCatalog.load(self._root / "profiles")
        self._profile_repository = ProfileRepository(connection, self._profiles)
        self._canonical = CanonicalRepository(connection, self._contracts)
        self._nodes = DocumentNodeRepository(connection)
        self._extractors = DocumentSignalExtractorRegistry.default()
        self._document_diff = DocumentDiffService(
            extractors=self._extractors,
            contracts=self._contracts,
        )
        self._node_builder = CanonicalDocumentNodeBuilder()

    def ensure(
        self,
        *,
        project_id: str,
        document_roots: tuple[Path, ...],
        actor: str,
    ) -> ProjectDocumentBaselineResult:
        snapshot = self.store_documents(
            project_id=project_id,
            document_roots=document_roots,
            actor=actor,
        )
        return self.build_index(
            project_id=project_id,
            snapshot_id=snapshot.snapshot_id,
            document_count=snapshot.document_count,
            actor=actor,
        )

    def discover(
        self, *, document_roots: tuple[Path, ...], project_id: str | None = None
    ) -> ProjectDocumentDiscoveryResult:
        """Identify supported Office documents by validated Profile signals."""

        profiles = (
            self._load_confirmed_project_profiles(project_id)
            if project_id is not None
            else _load_document_profiles(self._root / "profiles", self._profiles)
        )
        return _discover_documents(
            document_roots,
            extractors=self._extractors,
            profiles=profiles,
        )

    def store_documents(
        self,
        *,
        project_id: str,
        document_roots: tuple[Path, ...],
        actor: str,
    ) -> ProjectDocumentSnapshotResult:
        """Persist one deterministic Canonical snapshot without calling Embedding."""

        discovery = self.discover(document_roots=document_roots, project_id=project_id)
        if not discovery.candidates:
            raise ValueError("設計書の場所に Profile で識別できる XLSX/DOCX がありません")
        if discovery.review_required:
            raise ValueError(
                "Document Convention の確認が必要です: "
                + "; ".join(discovery.review_required)
            )
        candidates = discovery.candidates
        digests = tuple(_file_digest(candidate.path) for candidate in candidates)
        snapshot_id = _id(
            "document-baseline",
            project_id,
            *(f"{candidate.path.as_uri()}:{digest}" for candidate, digest in zip(
                candidates, digests, strict=True
            )),
        )
        profile_cache: dict[str, tuple[dict[str, Any], str]] = {}
        for candidate, digest in zip(candidates, digests, strict=True):
            cached_profile = profile_cache.get(candidate.profile_source)
            if cached_profile is None:
                cached_profile = self._resolve_document_profile(
                    project_id=project_id,
                    source=candidate.profile_source,
                    actor=actor,
                )
            profile, profile_version_id = cached_profile
            profile_cache[candidate.profile_source] = (profile, profile_version_id)
            convention = DocumentConvention.from_validated_profile(profile)
            if (
                convention.profile_id != candidate.profile_id
                or convention.document_type != candidate.document_type
                or convention.fact_type != candidate.fact_type
            ):
                raise ValueError("Document Profile changed after discovery; rescan is required")
            document_id = _id("document", project_id, candidate.path.as_uri())
            document_version_id = _id("document-version", document_id, digest)
            built = self._document_diff.build_snapshot(
                path=candidate.path,
                snapshot_id=snapshot_id,
                fact_type=candidate.fact_type,
                convention=convention,
                stable_key_namespace=document_id,
            )
            self._canonical.store_snapshot(
                DocumentSnapshotWrite(
                    project_id=project_id,
                    document_id=document_id,
                    document_version_id=document_version_id,
                    logical_name=candidate.path.name,
                    source_ref=candidate.path.as_uri(),
                    content_digest=digest,
                    extractor_ref=self._extractors.extractor_ref(candidate.path),
                    profile_version_id=profile_version_id,
                    selected_variant_id=built.selected_variant_id,
                    status=SnapshotStatus.COMMITTED,
                    snapshot=built.snapshot,
                    selected_variant_ids=built.selected_variant_ids,
                    fact_variant_ids=built.fact_variant_ids,
                )
            )
            self._nodes.store_nodes(
                project_id=project_id,
                snapshot_id=snapshot_id,
                nodes=self._node_builder.build(
                    snapshot=built.snapshot,
                    document_version_id=document_version_id,
                    logical_name=candidate.path.name,
                    document_type=convention.document_type,
                ),
            )

        return ProjectDocumentSnapshotResult(
            snapshot_id=snapshot_id,
            document_count=len(candidates),
        )

    def build_index(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        document_count: int,
        actor: str,
        build_nonce: str | None = None,
    ) -> ProjectDocumentBaselineResult:
        """Build and publish RAG for an already committed Canonical snapshot."""

        embedding_profile = _load_profile(
            self._root / "profiles" / "embedding-profile.example.json"
        )
        embedding_profile_version_id = _profile_version_id(embedding_profile)
        embedding_binding_key = "embedding:document_search"
        index_build_id = _id(
            "search-index",
            project_id,
            snapshot_id,
            embedding_profile_version_id,
            build_nonce or "stable",
        )
        index = SearchIndexBuildService(
            connection=self._connection,
            profiles=self._profiles,
        ).run(
            SearchIndexBuildRequest(
                build_id=index_build_id,
                project_id=project_id,
                snapshot_id=snapshot_id,
                profile_version_id=embedding_profile_version_id,
                profile_binding_key=embedding_binding_key,
                profile_activation_event_id=_id(
                    "profile-activation",
                    project_id,
                    embedding_binding_key,
                    embedding_profile_version_id,
                ),
                activated_by=actor,
                activation_reason="Project 初期化時のローカル RAG 基線作成",
            ),
            profile=embedding_profile,
            provider=OpenAICompatibleEmbeddingProvider.from_profile(embedding_profile),
        )
        return ProjectDocumentBaselineResult(
            snapshot_id=snapshot_id,
            document_count=document_count,
            index_build_id=index.state.spec.build_id,
            generated_vector_count=index.generated_vector_count,
            embedding_profile_binding_key=embedding_binding_key,
        )

    def _resolve_document_profile(
        self,
        *,
        project_id: str,
        source: str,
        actor: str,
    ) -> tuple[dict[str, Any], str]:
        if source.startswith("version:"):
            profile_version_id = source.removeprefix("version:")
            profile = self._profile_repository.get_version(profile_version_id)
            if profile is None:
                raise ValueError("Confirmed Project Document Profile does not exist")
            return profile, profile_version_id
        profile = _load_profile(self._root / "profiles" / source)
        self._profiles.validate_profile(profile)
        profile_version_id = _profile_version_id(profile)
        binding_key = f"document:{profile['document_type']}"
        self._profile_repository.store_version(
            profile_version_id=profile_version_id,
            profile=profile,
        )
        self._profile_repository.activate(
            activation_event_id=_id(
                "profile-activation",
                project_id,
                binding_key,
                profile_version_id,
            ),
            project_id=project_id,
            binding_key=binding_key,
            profile_version_id=profile_version_id,
            activated_by=actor,
            reason="Project 初期化時の設計書 Convention 自動選択",
        )
        return profile, profile_version_id

    def _load_confirmed_project_profiles(
        self, project_id: str
    ) -> tuple[tuple[str, DocumentConvention], ...]:
        runs = DocumentProfileLearningRepository(self._connection)
        confirmed = runs.latest_confirmed(project_id)
        if confirmed is None:
            raise ValueError("Project の確認済み設計書 Profile がありません")
        loaded: list[tuple[str, DocumentConvention]] = []
        for version_id in runs.profile_version_ids(confirmed.learning_run_id):
            profile = self._profile_repository.get_version(version_id)
            if profile is None:
                raise ValueError("確認済み設計書 Profile Version が見つかりません")
            loaded.append(
                (
                    f"version:{version_id}",
                    DocumentConvention.from_validated_profile(profile),
                )
            )
        if not loaded:
            raise ValueError("Project の確認済み設計書 Profile Set が空です")
        return tuple(loaded)


def _load_document_profiles(
    profiles_root: Path,
    catalog: ProfileCatalog,
) -> tuple[tuple[str, DocumentConvention], ...]:
    loaded: list[tuple[str, DocumentConvention]] = []
    for path in sorted(profiles_root.glob("*.json")):
        profile = _load_profile(path)
        if profile.get("profile_type") != "DocumentConventionProfile":
            continue
        catalog.validate_profile(profile)
        loaded.append((path.name, DocumentConvention.from_validated_profile(profile)))
    if not loaded:
        raise ValueError("DocumentConventionProfile が登録されていません")
    identities = [
        (convention.document_type, convention.profile_id)
        for _filename, convention in loaded
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("DocumentConventionProfile の document_type/profile_id が重複しています")
    return tuple(loaded)


def _discover_documents(
    document_roots: tuple[Path, ...],
    *,
    extractors: DocumentSignalExtractorRegistry,
    profiles: tuple[tuple[str, DocumentConvention], ...],
) -> ProjectDocumentDiscoveryResult:
    found: dict[Path, _DocumentCandidate] = {}
    ignored: list[str] = []
    review_required: list[str] = []
    matcher = ConventionMatcher()
    for root in document_roots:
        resolved_root = root.resolve(strict=True)
        for directory, names, filenames in os.walk(resolved_root, followlinks=False):
            names[:] = sorted(name for name in names if not name.startswith("."))
            current = Path(directory)
            for filename in sorted(filenames):
                unresolved_path = current / filename
                if (
                    filename.startswith("~$")
                    or unresolved_path.suffix.casefold() not in extractors.supported_suffixes
                ):
                    continue
                path = unresolved_path.resolve(strict=True)
                if not path.is_file() or not path.is_relative_to(resolved_root):
                    continue
                signals = extractors.extract(path)
                scored = []
                for profile_source, convention in profiles:
                    match = matcher.match(convention, signals)
                    scored.append(
                        (match.candidates[0].score, profile_source, convention, match.status)
                    )
                scored.sort(key=lambda item: (-item[0], item[2].profile_id))
                automatic = [item for item in scored if item[3] is MatchStatus.AUTO_MATCHED]
                if not automatic:
                    top_score = scored[0][0]
                    if top_score > 0:
                        review_required.append(
                            f"{path}: no unique Profile reached its auto-match threshold "
                            f"(best={scored[0][2].profile_id}, score={top_score:.3f})"
                        )
                    else:
                        ignored.append(str(path))
                    continue
                best_score = automatic[0][0]
                tied = [item for item in automatic if abs(item[0] - best_score) <= 1e-9]
                if len(tied) != 1:
                    review_required.append(
                        f"{path}: multiple Document Profiles matched at score {best_score:.3f}: "
                        + ", ".join(item[2].profile_id for item in tied)
                    )
                    continue
                score, profile_source, convention, _status = tied[0]
                found[path] = _DocumentCandidate(
                    path=path,
                    profile_source=profile_source,
                    profile_id=convention.profile_id,
                    document_type=convention.document_type,
                    fact_type=convention.fact_type,
                    score=score,
                )
                if len(found) > _MAX_DOCUMENTS:
                    raise ValueError(f"設計書は {_MAX_DOCUMENTS} 件以内で登録してください")
    return ProjectDocumentDiscoveryResult(
        candidates=tuple(found[path] for path in sorted(found, key=lambda item: item.as_posix())),
        ignored_documents=tuple(sorted(set(ignored))),
        review_required=tuple(sorted(set(review_required))),
    )


def _load_profile(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Profile must be a JSON object: {path.name}")
    return value


def _profile_version_id(profile: dict[str, Any]) -> str:
    return f"{profile['profile_id']}-{profile['profile_version']}"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _id(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode()
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"
