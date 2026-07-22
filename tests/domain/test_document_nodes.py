from operamind.domain import (
    CanonicalDocumentNodeBuilder,
    CanonicalFact,
    CanonicalSnapshot,
    DocumentEmbeddingInputBuilder,
    DocumentNodeType,
    SnapshotFact,
)


def make_snapshot(source_ref: str = "screen.xlsx#items!A2") -> CanonicalSnapshot:
    return CanonicalSnapshot(
        snapshot_id="snapshot-1",
        facts=(
            SnapshotFact(
                fact_ref="fact-1",
                fact=CanonicalFact(
                    fact_type="screen_element",
                    stable_key="screen_element:screen-a/status",
                    values={
                        "element_id": "status",
                        "screen_id": "SCREEN-A",
                        "description": "Status selector",
                    },
                    source_refs=(source_ref,),
                    field_evidence=(),
                ),
            ),
        ),
    )


def test_node_builder_creates_non_indexed_section_and_indexed_slice() -> None:
    nodes = CanonicalDocumentNodeBuilder().build(
        snapshot=make_snapshot(),
        document_version_id="document-version-1",
        logical_name="screen-design.xlsx",
        document_type="screen_design",
    )

    assert len(nodes) == 2
    section, slice_node = nodes
    assert section.node_type is DocumentNodeType.SECTION
    assert not section.index_eligible
    assert slice_node.node_type is DocumentNodeType.SLICE
    assert slice_node.index_eligible
    assert slice_node.parent_node_id == section.node_id
    assert slice_node.business_keys == ("screen_element:screen-a/status",)
    assert slice_node.content.splitlines() == [
        "description: Status selector",
        "element_id: status",
        "screen_id: SCREEN-A",
    ]


def test_node_identity_and_embedding_content_ignore_source_layout() -> None:
    builder = CanonicalDocumentNodeBuilder()
    first = builder.build(
        snapshot=make_snapshot("before.xlsx#items!A2"),
        document_version_id="document-version-1",
        logical_name="screen-design.xlsx",
        document_type="screen_design",
    )
    moved = builder.build(
        snapshot=make_snapshot("after.xlsx#renamed!H9"),
        document_version_id="document-version-1",
        logical_name="screen-design.xlsx",
        document_type="screen_design",
    )

    assert [node.node_id for node in first] == [node.node_id for node in moved]
    assert [node.content_digest for node in first] == [node.content_digest for node in moved]
    assert first[1].source_refs != moved[1].source_refs


def test_embedding_input_is_preprocessing_bound_and_layout_independent() -> None:
    node = CanonicalDocumentNodeBuilder().build(
        snapshot=make_snapshot("screen.xlsx#items!A2"),
        document_version_id="document-version-1",
        logical_name="screen-design.xlsx",
        document_type="screen_design",
    )[1]
    builder = DocumentEmbeddingInputBuilder()
    first = builder.build(
        node=node,
        document_type="screen_design",
        relation_labels=("acceptance", "parent", "acceptance"),
        preprocessing_version="canonical-slice-v1",
    )
    repeated = builder.build(
        node=node,
        document_type="screen_design",
        relation_labels=("parent", "acceptance"),
        preprocessing_version="canonical-slice-v1",
    )
    changed_version = builder.build(
        node=node,
        document_type="screen_design",
        relation_labels=("acceptance", "parent"),
        preprocessing_version="canonical-slice-v2",
    )

    assert first == repeated
    assert first.target_node_id == node.node_id
    assert '"document_type":"screen_design"' in first.text
    assert '"relation_labels":["acceptance","parent"]' in first.text
    assert first.input_digest != changed_version.input_digest
