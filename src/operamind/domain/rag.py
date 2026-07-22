"""Deterministic embedding inputs and search result identities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from operamind.domain.document_nodes import DocumentNode


@dataclass(frozen=True, slots=True)
class DocumentEmbeddingInput:
    """Versioned text and digest for one indexable Canonical node."""

    target_node_id: str
    text: str
    keyword_text: str
    input_digest: str


class DocumentEmbeddingInputBuilder:
    """Compose the exact design-specified fields without source layout metadata."""

    def build(
        self,
        *,
        node: DocumentNode,
        document_type: str,
        relation_labels: tuple[str, ...],
        preprocessing_version: str,
    ) -> DocumentEmbeddingInput:
        """Return deterministic JSON text and a preprocessing-bound digest."""

        if not node.index_eligible:
            raise ValueError("Only index-eligible Document Nodes can produce embedding input")
        if not document_type.strip() or not preprocessing_version.strip():
            raise ValueError("Embedding input profile fields must not be blank")
        if any(not label.strip() for label in relation_labels):
            raise ValueError("Embedding relation labels must not be blank")
        labels = tuple(sorted(set(relation_labels)))
        payload = {
            "business_keys": list(node.business_keys),
            "document_type": document_type,
            "heading_path": list(node.heading_path),
            "relation_labels": list(labels),
            "section_summary": node.summary,
            "slice_content": node.content,
        }
        text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        keyword_text = "\n".join(
            (
                document_type,
                *node.heading_path,
                *node.business_keys,
                node.summary,
                node.content,
                *labels,
            )
        )
        digest = sha256(f"{preprocessing_version}\x00{text}".encode()).hexdigest()
        return DocumentEmbeddingInput(
            target_node_id=node.node_id,
            text=text,
            keyword_text=keyword_text,
            input_digest=digest,
        )


class SearchChannel(StrEnum):
    """Retrieval channels combined by the ranking policy."""

    VECTOR = "vector"
    KEYWORD = "keyword"


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    """Content-free candidate identity returned by formal retrieval."""

    target_type: str
    target_id: str
    score: float
    channels: tuple[SearchChannel, ...]
    source_query_id: str

    def __post_init__(self) -> None:
        required = (self.target_type, self.target_id, self.source_query_id)
        if any(not value.strip() for value in required):
            raise ValueError("Search candidate identity fields must not be blank")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("Search candidate score must be between 0 and 1")
        if not self.channels or len(self.channels) != len(set(self.channels)):
            raise ValueError("Search candidate channels must be non-empty and unique")
