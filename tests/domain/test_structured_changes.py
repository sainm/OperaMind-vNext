import json
from pathlib import Path

import pytest

from operamind.contracts import ContractCatalog
from operamind.domain import (
    CanonicalFact,
    CanonicalSnapshot,
    ChangeReviewStatus,
    ChangeType,
    SnapshotFact,
    StructuredChangeBuilder,
)

ROOT = Path(__file__).parents[2]


def snapshot_fact(
    *,
    fact_ref: str,
    stable_key: str,
    values: dict[str, str],
    source_ref: str,
    fact_type: str = "api",
) -> SnapshotFact:
    return SnapshotFact(
        fact_ref=fact_ref,
        fact=CanonicalFact(
            fact_type=fact_type,
            stable_key=stable_key,
            values=values,
            source_refs=(source_ref,),
            field_evidence=(),
        ),
    )


def test_layout_and_evidence_only_changes_do_not_emit_business_change() -> None:
    stable_key = "api:GET/%2Fexpenses"
    source = CanonicalSnapshot(
        snapshot_id="snapshot-before",
        facts=(
            snapshot_fact(
                fact_ref="fact-before",
                stable_key=stable_key,
                values={"method": "GET", "path": "/expenses"},
                source_ref="before.xlsx#API一覧!A5",
            ),
        ),
    )
    target = CanonicalSnapshot(
        snapshot_id="snapshot-after",
        facts=(
            snapshot_fact(
                fact_ref="fact-after",
                stable_key=stable_key,
                values={"path": "/expenses", "method": "GET"},
                source_ref="renamed.xlsx#Renamed Sheet!C8",
            ),
        ),
    )

    changes = StructuredChangeBuilder().diff(
        project_id="project-1", source=source, target=target, domain="api"
    )

    assert changes == ()


def test_modified_fact_serializes_as_valid_deterministic_v1_artifact() -> None:
    stable_key = "api:GET/%2Fexpenses"
    source = CanonicalSnapshot(
        snapshot_id="snapshot-before",
        facts=(
            snapshot_fact(
                fact_ref="fact-before",
                stable_key=stable_key,
                values={
                    "method": "GET",
                    "path": "/expenses",
                    "summary": "申請中の経費一覧",
                },
                source_ref="before.xlsx#API一覧!B5",
            ),
        ),
    )
    target = CanonicalSnapshot(
        snapshot_id="snapshot-after",
        facts=(
            snapshot_fact(
                fact_ref="fact-after",
                stable_key=stable_key,
                values={
                    "method": "GET",
                    "path": "/expenses",
                    "summary": "すべての経費一覧",
                },
                source_ref="after.xlsx#API一覧!D8",
            ),
        ),
    )
    builder = StructuredChangeBuilder()

    first = builder.diff(project_id="project-1", source=source, target=target, domain="api")
    second = builder.diff(project_id="project-1", source=source, target=target, domain="api")
    artifact = first[0].to_artifact()

    assert first == second
    assert first[0].change_type is ChangeType.MODIFIED
    assert first[0].review_status is ChangeReviewStatus.NEEDS_REVIEW
    assert first[0].summary == "Modified api fields: summary."
    assert artifact["before"] == {
        "fact_ref": "fact-before",
        "values": {
            "method": "GET",
            "path": "/expenses",
            "summary": "申請中の経費一覧",
        },
        "source_refs": ["before.xlsx#API一覧!B5"],
    }
    assert artifact["after"] == {
        "fact_ref": "fact-after",
        "values": {
            "method": "GET",
            "path": "/expenses",
            "summary": "すべての経費一覧",
        },
        "source_refs": ["after.xlsx#API一覧!D8"],
    }
    ContractCatalog.load(ROOT / "contracts").validate_artifact(artifact)
    json.dumps(artifact, ensure_ascii=False)


def test_added_and_deleted_facts_have_contract_correct_null_states() -> None:
    source = CanonicalSnapshot(
        snapshot_id="snapshot-before",
        facts=(
            snapshot_fact(
                fact_ref="fact-old",
                stable_key="api:DELETE/%2Flegacy",
                values={"method": "DELETE", "path": "/legacy"},
                source_ref="before#A1",
            ),
        ),
    )
    target = CanonicalSnapshot(
        snapshot_id="snapshot-after",
        facts=(
            snapshot_fact(
                fact_ref="fact-new",
                stable_key="api:POST/%2Fexpenses",
                values={"method": "POST", "path": "/expenses"},
                source_ref="after#A1",
            ),
        ),
    )

    changes = StructuredChangeBuilder().diff(
        project_id="project-1", source=source, target=target, domain="api"
    )
    by_type = {change.change_type: change for change in changes}

    assert set(by_type) == {ChangeType.ADDED, ChangeType.DELETED}
    assert by_type[ChangeType.ADDED].before is None
    assert by_type[ChangeType.ADDED].after is not None
    assert by_type[ChangeType.DELETED].before is not None
    assert by_type[ChangeType.DELETED].after is None
    catalog = ContractCatalog.load(ROOT / "contracts")
    for change in changes:
        catalog.validate_artifact(change.to_artifact())


def test_snapshot_rejects_duplicate_stable_keys() -> None:
    first = snapshot_fact(
        fact_ref="fact-1",
        stable_key="api:GET/%2Fexpenses",
        values={"method": "GET", "path": "/expenses"},
        source_ref="source#A1",
    )
    second = snapshot_fact(
        fact_ref="fact-2",
        stable_key="api:GET/%2Fexpenses",
        values={"method": "GET", "path": "/expenses"},
        source_ref="source#A2",
    )

    with pytest.raises(ValueError, match="duplicate Stable Keys"):
        CanonicalSnapshot(snapshot_id="snapshot", facts=(first, second))


def test_canonical_fact_rejects_stable_key_from_another_fact_type() -> None:
    with pytest.raises(ValueError, match="must start with screen:"):
        snapshot_fact(
            fact_ref="fact-screen",
            stable_key="api:GET/%2Fexpenses",
            values={"screen.name": "expenses"},
            source_ref="source#A1",
            fact_type="screen",
        )
