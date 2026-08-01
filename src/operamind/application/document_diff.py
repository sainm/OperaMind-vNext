"""Executable before/after document Diff use case."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from operamind.contracts import ContractCatalog
from operamind.domain import (
    CanonicalFact,
    CanonicalFactMapper,
    CanonicalSnapshot,
    SnapshotFact,
    StructuredChange,
    StructuredChangeBuilder,
)
from operamind.domain.document_conventions import (
    ConventionMatch,
    ConventionMatcher,
    DocumentConvention,
    MatchStatus,
    SignalType,
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
class DocumentSnapshotBuildResult:
    """One Canonical Snapshot with complete section and Fact Variant provenance."""

    snapshot: CanonicalSnapshot
    selected_variant_ids: tuple[str, ...]
    fact_variant_ids: tuple[tuple[str, str], ...]
    ignored_sections: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.selected_variant_ids:
            raise ValueError("Canonical Snapshot must retain at least one selected Variant")
        if len(self.selected_variant_ids) != len(set(self.selected_variant_ids)):
            raise ValueError("Canonical Snapshot selected Variant IDs must be unique")
        expected_refs = {item.fact_ref for item in self.snapshot.facts}
        actual_refs = [fact_ref for fact_ref, _variant_id in self.fact_variant_ids]
        if set(actual_refs) != expected_refs or len(actual_refs) != len(set(actual_refs)):
            raise ValueError(
                "Canonical Snapshot Fact Variant provenance must be complete and unique"
            )
        if any(
            not variant_id.strip() or variant_id not in self.selected_variant_ids
            for _fact_ref, variant_id in self.fact_variant_ids
        ):
            raise ValueError("Canonical Snapshot Fact Variant provenance is invalid")
        if len(self.ignored_sections) != len(set(self.ignored_sections)):
            raise ValueError("Canonical Snapshot ignored sections must be unique")

    @property
    def selected_variant_id(self) -> str:
        """Return the primary Variant for compatibility with v1 callers."""

        return self.selected_variant_ids[0]


@dataclass(frozen=True, slots=True)
class DocumentDiffResult:
    """Validated changes and the exact Canonical inputs that produced them."""

    source_snapshot: CanonicalSnapshot
    target_snapshot: CanonicalSnapshot
    source_variant_id: str
    target_variant_id: str
    source_snapshot_variant_ids: tuple[str, ...]
    target_snapshot_variant_ids: tuple[str, ...]
    source_fact_variant_ids: tuple[tuple[str, str], ...]
    target_fact_variant_ids: tuple[tuple[str, str], ...]
    source_ignored_sections: tuple[str, ...]
    target_ignored_sections: tuple[str, ...]
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
            "source_variant_ids": list(self.source_snapshot_variant_ids),
            "target_variant_ids": list(self.target_snapshot_variant_ids),
            "source_fact_variant_ids": dict(self.source_fact_variant_ids),
            "target_fact_variant_ids": dict(self.target_fact_variant_ids),
            "source_ignored_sections": list(self.source_ignored_sections),
            "target_ignored_sections": list(self.target_ignored_sections),
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
        source = self.build_snapshot(
            path=request.before_path,
            snapshot_id=request.source_snapshot_id,
            fact_type=request.fact_type,
            convention=convention,
        )
        target = self.build_snapshot(
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
            source_snapshot_variant_ids=source.selected_variant_ids,
            target_snapshot_variant_ids=target.selected_variant_ids,
            source_fact_variant_ids=source.fact_variant_ids,
            target_fact_variant_ids=target.fact_variant_ids,
            source_ignored_sections=source.ignored_sections,
            target_ignored_sections=target.ignored_sections,
            source_extractor_ref=self._extractors.extractor_ref(request.before_path),
            target_extractor_ref=self._extractors.extractor_ref(request.after_path),
            source_content_digest=source_digest,
            target_content_digest=target_digest,
            changes=changes,
        )

    def build_snapshot(
        self,
        *,
        path: Path,
        snapshot_id: str,
        fact_type: str,
        convention: DocumentConvention,
        stable_key_namespace: str | None = None,
    ) -> DocumentSnapshotBuildResult:
        """Build a Canonical Snapshot using the same rules as the persisted Diff path."""

        signals = self._extractors.extract(path)
        sheet_signals = self._extractors.extract_sheet_signals(path)
        if sheet_signals:
            built = self._build_multi_sheet_snapshot(
                path=path,
                snapshot_id=snapshot_id,
                fact_type=fact_type,
                convention=convention,
                sheet_signals=sheet_signals,
            )
            return _with_stable_key_namespace(built, stable_key_namespace)
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
        fact_variant_ids: list[tuple[str, str]] = []
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
            snapshot_fact = SnapshotFact(
                fact_ref=_fact_ref(snapshot_id, result.fact.stable_key),
                fact=result.fact,
            )
            facts.append(snapshot_fact)
            fact_variant_ids.append((snapshot_fact.fact_ref, match.selected_variant_id))
        if blocked_records:
            raise DocumentDiffBlockedError(
                "Canonical mapping requires review: " + ", ".join(blocked_records)
            )
        built = DocumentSnapshotBuildResult(
            snapshot=CanonicalSnapshot(snapshot_id=snapshot_id, facts=tuple(facts)),
            selected_variant_ids=(match.selected_variant_id,),
            fact_variant_ids=tuple(fact_variant_ids),
        )
        return _with_stable_key_namespace(built, stable_key_namespace)

    def _build_multi_sheet_snapshot(
        self,
        *,
        path: Path,
        snapshot_id: str,
        fact_type: str,
        convention: DocumentConvention,
        sheet_signals: tuple[tuple[str, Any], ...],
    ) -> DocumentSnapshotBuildResult:
        """Match and map each worksheet independently, preserving all source locations."""

        facts: list[SnapshotFact] = []
        blocked_records: list[str] = []
        selected_variant_ids: list[str] = []
        fact_variant_ids: list[tuple[str, str]] = []
        ignored_sections: list[str] = []
        for sheet_name, signals in sheet_signals:
            match = self._matcher.match(convention, signals)
            if match.status is not MatchStatus.AUTO_MATCHED or match.selected_variant_id is None:
                if _requires_section_review(match):
                    raise DocumentDiffBlockedError(
                        "Document Convention requires review for "
                        f"{path}#{sheet_name}: {match.reason}"
                    )
                ignored_sections.append(f"{sheet_name}:{match.reason}")
                continue
            variant = next(
                (
                    item
                    for item in convention.variants
                    if item.variant_id == match.selected_variant_id
                ),
                None,
            )
            if variant is None:
                raise DocumentDiffBlockedError(
                    f"Selected Variant is not present in the Convention: "
                    f"{match.selected_variant_id}"
                )
            records = self._extractors.extract_records_for_sheet(
                path,
                variant,
                sheet_name=sheet_name,
            )
            if not records:
                raise DocumentDiffBlockedError(
                    f"No canonical records extracted from {path}#{sheet_name} "
                    f"with {match.selected_variant_id}"
                )
            selected_variant_ids.append(match.selected_variant_id)
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
                snapshot_fact = SnapshotFact(
                    fact_ref=_fact_ref(snapshot_id, result.fact.stable_key),
                    fact=result.fact,
                )
                facts.append(snapshot_fact)
                fact_variant_ids.append((snapshot_fact.fact_ref, match.selected_variant_id))
        if blocked_records:
            raise DocumentDiffBlockedError(
                "Canonical mapping requires review: " + ", ".join(blocked_records)
            )
        if not facts:
            raise DocumentDiffBlockedError(f"No canonical records extracted from {path}")
        unique_variant_ids = tuple(dict.fromkeys(selected_variant_ids))
        return DocumentSnapshotBuildResult(
            snapshot=CanonicalSnapshot(snapshot_id=snapshot_id, facts=tuple(facts)),
            selected_variant_ids=unique_variant_ids,
            fact_variant_ids=tuple(fact_variant_ids),
            ignored_sections=tuple(ignored_sections),
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


def _with_stable_key_namespace(
    result: DocumentSnapshotBuildResult,
    namespace: str | None,
) -> DocumentSnapshotBuildResult:
    """Keep Stable Keys unique when one Snapshot contains several documents."""

    if namespace is None:
        return result
    normalized_namespace = namespace.strip()
    if not normalized_namespace or any(char in normalized_namespace for char in (":", "/")):
        raise ValueError("Canonical Stable Key namespace must be a non-blank path segment")
    variants = dict(result.fact_variant_ids)
    namespaced_facts: list[SnapshotFact] = []
    namespaced_variants: list[tuple[str, str]] = []
    for snapshot_fact in result.snapshot.facts:
        fact = snapshot_fact.fact
        _prefix, separator, local_key = fact.stable_key.partition(":")
        if not separator or not local_key:
            raise ValueError("Canonical Stable Key has no fact type prefix")
        stable_key = f"{fact.fact_type}:{normalized_namespace}/{local_key}"
        fact_ref = _fact_ref(result.snapshot.snapshot_id, stable_key)
        namespaced_facts.append(
            SnapshotFact(
                fact_ref=fact_ref,
                fact=CanonicalFact(
                    fact_type=fact.fact_type,
                    stable_key=stable_key,
                    values=fact.values,
                    source_refs=fact.source_refs,
                    field_evidence=fact.field_evidence,
                ),
            )
        )
        namespaced_variants.append((fact_ref, variants[snapshot_fact.fact_ref]))
    return DocumentSnapshotBuildResult(
        snapshot=CanonicalSnapshot(
            snapshot_id=result.snapshot.snapshot_id,
            facts=tuple(namespaced_facts),
        ),
        selected_variant_ids=result.selected_variant_ids,
        fact_variant_ids=tuple(namespaced_variants),
        ignored_sections=result.ignored_sections,
    )


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _requires_section_review(match: ConventionMatch) -> bool:
    """Treat structurally related but incomplete worksheets as review blockers."""

    structural_types = {SignalType.HEADERS, SignalType.HEADING}
    return any(
        structural_types.intersection(candidate.matched_signal_types)
        for candidate in match.candidates
    )
