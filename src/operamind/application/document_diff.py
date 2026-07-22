"""Executable before/after document Diff use case."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from operamind.contracts import ContractCatalog
from operamind.domain import (
    CanonicalFactMapper,
    CanonicalSnapshot,
    SnapshotFact,
    StructuredChange,
    StructuredChangeBuilder,
)
from operamind.domain.document_conventions import (
    ConventionMatcher,
    DocumentConvention,
    MatchStatus,
)
from operamind.infrastructure.documents import DocumentSignalExtractorRegistry


class DocumentDiffBlockedError(ValueError):
    """Raised when review or missing canonical records prevents a safe Diff."""


@dataclass(frozen=True, slots=True)
class DocumentDiffRequest:
    """Inputs whose IDs become StructuredChange provenance."""

    project_id: str
    domain: str
    fact_type: str
    source_snapshot_id: str
    target_snapshot_id: str
    before_path: Path
    after_path: Path


@dataclass(frozen=True, slots=True)
class DocumentDiffResult:
    """Validated changes and the exact Canonical inputs that produced them."""

    source_snapshot: CanonicalSnapshot
    target_snapshot: CanonicalSnapshot
    source_variant_id: str
    target_variant_id: str
    source_extractor_ref: str
    target_extractor_ref: str
    source_content_digest: str
    target_content_digest: str
    changes: tuple[StructuredChange, ...]

    @property
    def source_fact_count(self) -> int:
        """Return the number of Canonical Facts in the source snapshot."""

        return len(self.source_snapshot.facts)

    @property
    def target_fact_count(self) -> int:
        """Return the number of Canonical Facts in the target snapshot."""

        return len(self.target_snapshot.facts)

    def to_payload(self) -> dict[str, Any]:
        """Return a CLI-friendly envelope containing Contract v1 artifacts."""

        return {
            "source_fact_count": self.source_fact_count,
            "target_fact_count": self.target_fact_count,
            "structured_change_count": len(self.changes),
            "source_extractor_ref": self.source_extractor_ref,
            "target_extractor_ref": self.target_extractor_ref,
            "source_content_digest": self.source_content_digest,
            "target_content_digest": self.target_content_digest,
            "changes": [change.to_artifact() for change in self.changes],
        }


class DocumentDiffService:
    """Extract, map, align, validate, and return one document-pair Diff."""

    def __init__(
        self,
        *,
        extractors: DocumentSignalExtractorRegistry,
        contracts: ContractCatalog,
    ) -> None:
        self._extractors = extractors
        self._contracts = contracts
        self._matcher = ConventionMatcher()
        self._mapper = CanonicalFactMapper()
        self._change_builder = StructuredChangeBuilder()

    def run(
        self, request: DocumentDiffRequest, convention: DocumentConvention
    ) -> DocumentDiffResult:
        """Block unsafe snapshots and validate every emitted StructuredChange."""

        self._validate_request(request)
        source_digest = _file_digest(request.before_path)
        target_digest = _file_digest(request.after_path)
        source = self._build_snapshot(
            path=request.before_path,
            snapshot_id=request.source_snapshot_id,
            fact_type=request.fact_type,
            convention=convention,
        )
        target = self._build_snapshot(
            path=request.after_path,
            snapshot_id=request.target_snapshot_id,
            fact_type=request.fact_type,
            convention=convention,
        )
        if (
            _file_digest(request.before_path) != source_digest
            or _file_digest(request.after_path) != target_digest
        ):
            raise DocumentDiffBlockedError(
                "Source document changed while Canonical extraction was running"
            )
        changes = self._change_builder.diff(
            project_id=request.project_id,
            source=source.snapshot,
            target=target.snapshot,
            domain=request.domain,
        )
        for change in changes:
            self._contracts.validate_artifact(change.to_artifact())
        return DocumentDiffResult(
            source_snapshot=source.snapshot,
            target_snapshot=target.snapshot,
            source_variant_id=source.selected_variant_id,
            target_variant_id=target.selected_variant_id,
            source_extractor_ref=self._extractors.extractor_ref(request.before_path),
            target_extractor_ref=self._extractors.extractor_ref(request.after_path),
            source_content_digest=source_digest,
            target_content_digest=target_digest,
            changes=changes,
        )

    def _build_snapshot(
        self,
        *,
        path: Path,
        snapshot_id: str,
        fact_type: str,
        convention: DocumentConvention,
    ) -> _BuiltDocumentSnapshot:
        signals = self._extractors.extract(path)
        match = self._matcher.match(convention, signals)
        if match.status is not MatchStatus.AUTO_MATCHED or match.selected_variant_id is None:
            raise DocumentDiffBlockedError(
                f"Document Convention requires review for {path}: {match.reason}"
            )
        variant = next(
            (item for item in convention.variants if item.variant_id == match.selected_variant_id),
            None,
        )
        if variant is None:
            raise DocumentDiffBlockedError(
                f"Selected Variant is not present in the Convention: {match.selected_variant_id}"
            )
        records = self._extractors.extract_records(path, variant)
        if not records:
            raise DocumentDiffBlockedError(f"No canonical records extracted from {path}")

        facts: list[SnapshotFact] = []
        blocked_records: list[str] = []
        for record in records:
            result = self._mapper.map_record(
                convention=convention,
                match=match,
                fact_type=fact_type,
                record=record,
            )
            if result.fact is None:
                blocked_records.append(f"{record.record_ref}:{result.reason.value}")
                continue
            facts.append(
                SnapshotFact(
                    fact_ref=_fact_ref(snapshot_id, result.fact.stable_key),
                    fact=result.fact,
                )
            )
        if blocked_records:
            raise DocumentDiffBlockedError(
                "Canonical mapping requires review: " + ", ".join(blocked_records)
            )
        return _BuiltDocumentSnapshot(
            snapshot=CanonicalSnapshot(snapshot_id=snapshot_id, facts=tuple(facts)),
            selected_variant_id=match.selected_variant_id,
        )

    @staticmethod
    def _validate_request(request: DocumentDiffRequest) -> None:
        required = (
            request.project_id,
            request.domain,
            request.fact_type,
            request.source_snapshot_id,
            request.target_snapshot_id,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Document Diff identity fields must not be blank")
        if request.source_snapshot_id == request.target_snapshot_id:
            raise ValueError("Source and target snapshot IDs must differ")
        if request.before_path.resolve() == request.after_path.resolve():
            raise ValueError("Source and target document paths must differ")


def _fact_ref(snapshot_id: str, stable_key: str) -> str:
    material = f"{snapshot_id}\x00{stable_key}".encode()
    return f"fact-{sha256(material).hexdigest()[:24]}"


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _BuiltDocumentSnapshot:
    """Canonical snapshot together with its matched Convention Variant."""

    snapshot: CanonicalSnapshot
    selected_variant_id: str
