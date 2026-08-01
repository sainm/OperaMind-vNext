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
from operamind.domain.document_conventions import DocumentConvention
from operamind.infrastructure.documents import DocumentSignalExtractorRegistry
from operamind.infrastructure.embeddings import OpenAICompatibleEmbeddingProvider
from operamind.infrastructure.postgres import (
    CanonicalRepository,
    DocumentNodeRepository,
    DocumentSnapshotWrite,
    ProfileRepository,
    SnapshotStatus,
)
from operamind.profiles import ProfileCatalog

_MAX_DOCUMENTS = 500
_DOCUMENT_TYPES = (
    (
        "画面設計書",
        "screen-design-convention-profile.example.json",
        "screen_element",
    ),
    (
        "プログラム設計書",
        "program-design-convention-profile.example.json",
        "program_method",
    ),
)


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
    profile_filename: str
    fact_type: str


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
        candidates = _discover_documents(document_roots)
        if not candidates:
            raise ValueError(
                "設計書の場所に対応する画面設計書またはプログラム設計書がありません"
            )
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
            cached_profile = profile_cache.get(candidate.profile_filename)
            if cached_profile is None:
                cached_profile = self._activate_document_profile(
                    project_id=project_id,
                    filename=candidate.profile_filename,
                    actor=actor,
                )
            profile, profile_version_id = cached_profile
            profile_cache[candidate.profile_filename] = (profile, profile_version_id)
            convention = DocumentConvention.from_validated_profile(profile)
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
            document_count=len(candidates),
            index_build_id=index.state.spec.build_id,
            generated_vector_count=index.generated_vector_count,
            embedding_profile_binding_key=embedding_binding_key,
        )

    def _activate_document_profile(
        self,
        *,
        project_id: str,
        filename: str,
        actor: str,
    ) -> tuple[dict[str, Any], str]:
        profile = _load_profile(self._root / "profiles" / filename)
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


def _discover_documents(document_roots: tuple[Path, ...]) -> tuple[_DocumentCandidate, ...]:
    found: dict[Path, _DocumentCandidate] = {}
    for root in document_roots:
        resolved_root = root.resolve(strict=True)
        for directory, names, filenames in os.walk(resolved_root, followlinks=False):
            names[:] = sorted(name for name in names if not name.startswith("."))
            current = Path(directory)
            for filename in sorted(filenames):
                if not filename.lower().endswith(".xlsx") or filename.startswith("~$"):
                    continue
                match = next(
                    (
                        (profile_filename, fact_type)
                        for token, profile_filename, fact_type in _DOCUMENT_TYPES
                        if token in filename
                    ),
                    None,
                )
                if match is None:
                    continue
                path = (current / filename).resolve(strict=True)
                if not path.is_file() or not path.is_relative_to(resolved_root):
                    continue
                found[path] = _DocumentCandidate(path, match[0], match[1])
                if len(found) > _MAX_DOCUMENTS:
                    raise ValueError(f"設計書は {_MAX_DOCUMENTS} 件以内で登録してください")
    return tuple(found[path] for path in sorted(found, key=lambda item: item.as_posix()))


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
