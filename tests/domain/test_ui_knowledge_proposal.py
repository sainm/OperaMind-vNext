from operamind.domain import (
    CanonicalFact,
    CanonicalSnapshot,
    SnapshotFact,
    UiKnowledgeProposalBuilder,
)


def _screen_fact(*, values: dict[str, str]) -> SnapshotFact:
    return SnapshotFact(
        fact_ref="fact-status-filter",
        fact=CanonicalFact(
            fact_type="screen_element",
            stable_key="screen_element:expense-list/status-filter",
            values=values,
            source_refs=("source://screen-design/G5",),
            field_evidence=(),
        ),
    )


def test_ui_knowledge_proposal_uses_business_names_and_safe_locator_priority() -> None:
    source = CanonicalSnapshot(
        snapshot_id="document-snapshot-001",
        facts=(
            _screen_fact(
                values={
                    "screen_id": "SCREEN_EXPENSE_LIST",
                    "element_id": "expense-search-status",
                    "screen_name": "経費一覧",
                    "business_name": "ステータス絞り込み",
                    "label": "ステータス",
                    "accessible_role": "combobox",
                    "accessible_name": "ステータス",
                    "test_id": "status-filter",
                    "trigger_path": "/expenses",
                }
            ),
        ),
    )

    proposal = UiKnowledgeProposalBuilder().build(
        source=source,
        snapshot_id="ui-knowledge-proposal-001",
        project_id="visiondemo",
        environment_id="staging",
        deployment_revision="deploy-001",
        snapshot_version="1.0.0",
    )

    assert proposal.snapshot is not None
    assert proposal.snapshot.review_status == "draft"
    target = proposal.snapshot.targets[0]
    assert target.business_name == "ステータス絞り込み"
    assert target.screen_name == "経費一覧"
    assert "SCREEN_EXPENSE_LIST" not in target.to_dict().values()
    assert [item.locator.strategy.value for item in target.candidates] == [
        "role",
        "label",
        "text",
        "test_id",
    ]
    assert target.preferred_locator().strategy.value == "role"
    assert proposal.issues == ()


def test_ui_knowledge_proposal_keeps_missing_business_identity_as_review_issue() -> None:
    source = CanonicalSnapshot(
        snapshot_id="document-snapshot-001",
        facts=(
            _screen_fact(
                values={
                    "screen_id": "SCREEN_EXPENSE_LIST",
                    "element_id": "expense-search-status",
                    "description": "Filter expenses by status",
                }
            ),
        ),
    )

    proposal = UiKnowledgeProposalBuilder().build(
        source=source,
        snapshot_id="ui-knowledge-proposal-001",
        project_id="visiondemo",
        environment_id="staging",
        deployment_revision="deploy-001",
        snapshot_version="1.0.0",
    )

    assert proposal.snapshot is None
    assert {item.code for item in proposal.issues} == {
        "business_name_missing",
        "screen_name_missing",
    }


def test_ui_knowledge_proposal_flags_text_only_locator_for_runtime_review() -> None:
    source = CanonicalSnapshot(
        snapshot_id="document-snapshot-001",
        facts=(
            _screen_fact(
                values={
                    "screen_name": "経費一覧",
                    "business_name": "ステータス絞り込み",
                    "element_id": "expense-search-status",
                    "screen_id": "SCREEN_EXPENSE_LIST",
                }
            ),
        ),
    )

    proposal = UiKnowledgeProposalBuilder().build(
        source=source,
        snapshot_id="ui-knowledge-proposal-001",
        project_id="visiondemo",
        environment_id="staging",
        deployment_revision="deploy-001",
        snapshot_version="1.0.0",
    )

    assert proposal.snapshot is not None
    assert proposal.snapshot.targets[0].candidates[0].locator.strategy.value == "text"
    assert [item.code for item in proposal.issues] == ["semantic_locator_review_required"]
