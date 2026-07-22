"""Canonical Section/Slice nodes derived from normalized document facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from operamind.domain.structured_changes import CanonicalSnapshot, SnapshotFact


class DocumentNodeType(StrEnum):
    """Canonical structural node kinds used by RAG rehydration."""

    SECTION = "section"
    SLICE = "slice"


@dataclass(frozen=True, slots=True)
class DocumentNode:
    """A bounded Canonical node whose content can be rehydrated by ID."""

    node_id: str
    snapshot_id: str
    document_version_id: str
    parent_node_id: str | None
    node_type: DocumentNodeType
    ordinal: int
    heading_path: tuple[str, ...]
    business_keys: tuple[str, ...]
    summary: str
    content: str
    source_refs: tuple[str, ...]
    index_eligible: bool

    def __post_init__(self) -> None:
        required = (
            self.node_id,
            self.snapshot_id,
            self.document_version_id,
            self.summary,
            self.content,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Document node identity and content must not be blank")
        if self.ordinal < 0:
            raise ValueError("Document node ordinal must not be negative")
        if not self.heading_path or any(not item.strip() for item in self.heading_path):
            raise ValueError("Document node heading_path must be non-empty")
        if not self.source_refs or any(not item.strip() for item in self.source_refs):
            raise ValueError("Document node source_refs must be non-empty")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("Document node source_refs must be unique")
        if len(self.business_keys) != len(set(self.business_keys)):
            raise ValueError("Document node business_keys must be unique")
        if self.node_type is DocumentNodeType.SECTION:
            if self.parent_node_id is not None or self.index_eligible:
                raise ValueError("Section nodes must be non-indexable roots")
        elif self.parent_node_id is None or not self.index_eligible:
            raise ValueError("Slice nodes must be indexable children")

    @property
    def content_digest(self) -> str:
        """Return the digest of rehydratable Canonical node fields."""

        material = "\x00".join(
            (
                self.node_type.value,
                "\x1f".join(self.heading_path),
                "\x1f".join(self.business_keys),
                self.summary,
                self.content,
            )
        )
        return sha256(material.encode()).hexdigest()


class CanonicalDocumentNodeBuilder:
    """Build deterministic Section/Slice structure from one Canonical Snapshot member."""

    def build(
        self,
        *,
        snapshot: CanonicalSnapshot,
        document_version_id: str,
        logical_name: str,
        document_type: str,
    ) -> tuple[DocumentNode, ...]:
        """Create one Section per Fact Type and one indexable Slice per Fact."""

        required = (document_version_id, logical_name, document_type)
        if any(not value.strip() for value in required):
            raise ValueError("Document node build fields must not be blank")
        facts_by_type: dict[str, list[SnapshotFact]] = {}
        for snapshot_fact in snapshot.facts:
            facts_by_type.setdefault(snapshot_fact.fact.fact_type, []).append(snapshot_fact)

        nodes: list[DocumentNode] = []
        for section_ordinal, fact_type in enumerate(sorted(facts_by_type)):
            facts = sorted(
                facts_by_type[fact_type],
                key=lambda item: item.fact.stable_key,
            )
            section_id = _node_id(
                snapshot.snapshot_id,
                document_version_id,
                "section",
                fact_type,
            )
            section_source_refs = tuple(
                sorted(
                    {
                        source_ref
                        for snapshot_fact in facts
                        for source_ref in snapshot_fact.fact.source_refs
                    }
                )
            )
            nodes.append(
                DocumentNode(
                    node_id=section_id,
                    snapshot_id=snapshot.snapshot_id,
                    document_version_id=document_version_id,
                    parent_node_id=None,
                    node_type=DocumentNodeType.SECTION,
                    ordinal=section_ordinal,
                    heading_path=(logical_name, fact_type),
                    business_keys=(),
                    summary=f"{document_type} {fact_type} facts",
                    content=f"fact_count: {len(facts)}",
                    source_refs=section_source_refs,
                    index_eligible=False,
                )
            )
            for slice_ordinal, snapshot_fact in enumerate(facts):
                fact = snapshot_fact.fact
                nodes.append(
                    DocumentNode(
                        node_id=_node_id(
                            snapshot.snapshot_id,
                            document_version_id,
                            "slice",
                            fact.stable_key,
                        ),
                        snapshot_id=snapshot.snapshot_id,
                        document_version_id=document_version_id,
                        parent_node_id=section_id,
                        node_type=DocumentNodeType.SLICE,
                        ordinal=slice_ordinal,
                        heading_path=(logical_name, fact_type),
                        business_keys=(fact.stable_key,),
                        summary=f"{fact_type} {fact.stable_key}",
                        content="\n".join(
                            f"{field}: {value}" for field, value in fact.values.items()
                        ),
                        source_refs=tuple(sorted(fact.source_refs)),
                        index_eligible=True,
                    )
                )
        return tuple(nodes)


def _node_id(snapshot_id: str, document_version_id: str, node_type: str, key: str) -> str:
    material = "\x00".join((snapshot_id, document_version_id, node_type, key))
    return f"node-{sha256(material.encode()).hexdigest()[:24]}"
