import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from operamind.application import DocumentRelationBuildRequest, DocumentRelationBuildService
from operamind.contracts import ContractCatalog
from operamind.domain import (
    CanonicalDocumentNodeBuilder,
    CanonicalFact,
    CanonicalFieldEvidence,
    CanonicalSnapshot,
    SnapshotFact,
    StructuredChangeBuilder,
)
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    CanonicalRepository,
    DocumentExpansionReason,
    DocumentNodeRepository,
    DocumentRelationBuildStatus,
    DocumentRelationRepository,
    DocumentSnapshotWrite,
    MigrationCatalog,
    MigrationRunner,
    PersistenceConflictError,
    ProfileRepository,
    SearchIndexRepository,
    SnapshotStatus,
)
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


def load_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def load_profile() -> dict[str, Any]:
    return load_object(ROOT / "profiles/screen-design-convention-profile.example.json")


def insert_project(connection: psycopg.Connection[Any], project_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, %s)",
            (project_id, "P1 integration test"),
        )


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_profile_version_activation_and_audit_round_trip() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    first_version_id = f"profile-screen-v1-{suffix}"
    second_version_id = f"profile-screen-v2-{suffix}"
    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        insert_project(connection, project_id)
        repository = ProfileRepository(connection, ProfileCatalog.load(ROOT / "profiles"))
        first = load_profile()
        first_digest = repository.store_version(
            profile_version_id=first_version_id,
            profile=first,
        )

        assert (
            repository.store_version(
                profile_version_id=first_version_id,
                profile=first,
            )
            == first_digest
        )
        assert repository.get_version(first_version_id) == first
        assert repository.activate(
            activation_event_id=f"activation-1-{suffix}",
            project_id=project_id,
            binding_key="document:screen_design",
            profile_version_id=first_version_id,
            activated_by="tester@example.invalid",
            reason="Initial reviewed Profile",
        )
        assert not repository.activate(
            activation_event_id=f"activation-1-{suffix}",
            project_id=project_id,
            binding_key="document:screen_design",
            profile_version_id=first_version_id,
            activated_by="tester@example.invalid",
            reason="Initial reviewed Profile",
        )

        second = copy.deepcopy(first)
        second["profile_version"] = "1.0.1"
        repository.store_version(profile_version_id=second_version_id, profile=second)
        assert repository.activate(
            activation_event_id=f"activation-2-{suffix}",
            project_id=project_id,
            binding_key="document:screen_design",
            profile_version_id=second_version_id,
            activated_by="reviewer@example.invalid",
            reason="Reviewed Profile update",
        )
        active = repository.get_active(
            project_id=project_id,
            binding_key="document:screen_design",
        )
        assert active is not None
        assert active.profile_version_id == second_version_id
        assert active.profile == second
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT profile_digest_drift_probe")
            cursor.execute(
                """
                UPDATE profile_versions
                SET payload = jsonb_set(
                    payload,
                    '{minimum_auto_match_score}',
                    '0.7'::jsonb
                )
                WHERE profile_version_id = %s
                """,
                (second_version_id,),
            )
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            repository.get_version(second_version_id)
        with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
            repository.get_active(
                project_id=project_id,
                binding_key="document:screen_design",
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT profile_digest_drift_probe")
            cursor.execute("RELEASE SAVEPOINT profile_digest_drift_probe")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT previous_profile_version_id, activated_profile_version_id
                FROM profile_activation_events
                WHERE project_id = %s AND binding_key = %s
                ORDER BY activated_at, activation_event_id
                """,
                (project_id, "document:screen_design"),
            )
            audit = cursor.fetchall()
        assert audit == [(None, first_version_id), (first_version_id, second_version_id)]
        connection.rollback()


def make_fact(
    *, fact_ref: str, source_ref: str, default_value: str, description: str
) -> SnapshotFact:
    return SnapshotFact(
        fact_ref=fact_ref,
        fact=CanonicalFact(
            fact_type="screen_element",
            stable_key="screen_element:screen_expense_list/expense-search-status",
            values={
                "screen_id": "SCREEN_EXPENSE_LIST",
                "element_id": "expense-search-status",
                "default_value": default_value,
                "description": description,
            },
            source_refs=(source_ref,),
            field_evidence=(
                CanonicalFieldEvidence(
                    canonical_field="default_value",
                    source_aliases=("初期値",),
                    source_refs=(source_ref,),
                ),
            ),
        ),
    )


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_canonical_snapshot_and_structured_change_round_trip() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    document_id = f"document-{suffix}"
    profile_version_id = f"profile-{suffix}"
    before = CanonicalSnapshot(
        snapshot_id=f"snapshot-before-{suffix}",
        facts=(
            make_fact(
                fact_ref=f"fact-before-{suffix}",
                source_ref="screen-before.xlsx#画面項目一覧!G5",
                default_value="申請中",
                description="ステータスフィルタ(下書き/申請中/承認済)",
            ),
        ),
    )
    after = CanonicalSnapshot(
        snapshot_id=f"snapshot-after-{suffix}",
        facts=(
            make_fact(
                fact_ref=f"fact-after-{suffix}",
                source_ref="screen-after.xlsx#画面項目一覧!G5",
                default_value="すべて",
                description="ステータスフィルタ(下書き/申請中/承認済/差戻し)",
            ),
        ),
    )
    before_write = DocumentSnapshotWrite(
        project_id=project_id,
        document_id=document_id,
        document_version_id=f"version-before-{suffix}",
        logical_name="02_画面設計書_経費精算申請一覧.xlsx",
        source_ref="immutable://design-docs/before.xlsx",
        content_digest=hashlib.sha256(b"before").hexdigest(),
        extractor_ref="manual-canonical@1",
        profile_version_id=profile_version_id,
        selected_variant_id="screen-item-table-ja",
        status=SnapshotStatus.COMMITTED,
        snapshot=before,
    )
    after_write = DocumentSnapshotWrite(
        project_id=project_id,
        document_id=document_id,
        document_version_id=f"version-after-{suffix}",
        logical_name="02_画面設計書_経費精算申請一覧.xlsx",
        source_ref="immutable://design-docs/after.xlsx",
        content_digest=hashlib.sha256(b"after").hexdigest(),
        extractor_ref="manual-canonical@1",
        profile_version_id=profile_version_id,
        selected_variant_id="screen-item-table-ja",
        status=SnapshotStatus.COMMITTED,
        snapshot=after,
    )
    changes = StructuredChangeBuilder().diff(
        project_id=project_id,
        source=before,
        target=after,
        domain="ui",
    )

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        insert_project(connection, project_id)
        profiles = ProfileRepository(connection, ProfileCatalog.load(ROOT / "profiles"))
        profiles.store_version(profile_version_id=profile_version_id, profile=load_profile())
        repository = CanonicalRepository(
            connection,
            ContractCatalog.load(ROOT / "contracts"),
        )

        repository.store_snapshot(before_write)
        repository.store_snapshot(after_write)
        repository.store_snapshot(before_write)
        repository.store_snapshot(after_write)
        node_builder = CanonicalDocumentNodeBuilder()
        node_repository = DocumentNodeRepository(connection)
        node_repository.store_nodes(
            project_id=project_id,
            snapshot_id=before.snapshot_id,
            nodes=node_builder.build(
                snapshot=before,
                document_version_id=before_write.document_version_id,
                logical_name=before_write.logical_name,
                document_type="screen_design",
            ),
        )
        node_repository.store_nodes(
            project_id=project_id,
            snapshot_id=after.snapshot_id,
            nodes=node_builder.build(
                snapshot=after,
                document_version_id=after_write.document_version_id,
                logical_name=after_write.logical_name,
                document_type="screen_design",
            ),
        )
        assert repository.get_snapshot(project_id=project_id, snapshot_id=before.snapshot_id) == (
            before
        )
        assert (
            repository.get_snapshot(project_id=project_id, snapshot_id=after.snapshot_id) == after
        )
        assert repository.store_changes(changes) == (changes[0].change_id,)
        assert repository.store_changes(changes) == (changes[0].change_id,)
        ArtifactRepository(connection, ContractCatalog.load(ROOT / "contracts")).store(
            artifact_id=changes[0].change_id,
            project_id=project_id,
            analysis_case_id=None,
            artifact=changes[0].to_artifact(),
        )
        assert repository.get_change_artifact(changes[0].change_id) == changes[0].to_artifact()
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT structured_change_normalized_drift_probe")
            cursor.execute(
                """
                UPDATE structured_changes
                SET summary = 'Tampered but schema-valid summary'
                WHERE structured_change_id = %s
                """,
                (changes[0].change_id,),
            )
        with pytest.raises(PersistenceConflictError, match="differ from Artifact"):
            repository.get_change_artifact(changes[0].change_id)
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT structured_change_normalized_drift_probe")
            cursor.execute("RELEASE SAVEPOINT structured_change_normalized_drift_probe")

        conflicting = copy.deepcopy(load_profile())
        conflicting["profile_version"] = "9.9.9"
        with pytest.raises(PersistenceConflictError):
            profiles.store_version(profile_version_id=profile_version_id, profile=conflicting)
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_document_node_neighborhood_is_bounded_and_cross_document_explicit() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"project-{suffix}"
    snapshot_id = f"snapshot-{suffix}"
    profile_version_id = f"profile-{suffix}"
    api_profile_version_id = f"api-profile-{suffix}"

    def snapshot_fact(label: str, fact_type: str = "screen_element") -> SnapshotFact:
        stable_key = f"{fact_type}:screen-a/{label}"
        return SnapshotFact(
            fact_ref=f"fact-{fact_type}-{label}-{suffix}",
            fact=CanonicalFact(
                fact_type=fact_type,
                stable_key=stable_key,
                values={"name": label, "value": "d" if label == "b" else f"value-{label}"},
                source_refs=(f"design.xlsx#{label}",),
                field_evidence=(),
            ),
        )

    screen_snapshot = CanonicalSnapshot(
        snapshot_id=snapshot_id,
        facts=tuple(snapshot_fact(label) for label in ("a", "b", "c", "d")),
    )
    api_snapshot = CanonicalSnapshot(
        snapshot_id=snapshot_id,
        facts=(snapshot_fact("b", "api_endpoint"),),
    )
    screen_version_id = f"screen-version-{suffix}"
    api_version_id = f"api-version-{suffix}"

    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        insert_project(connection, project_id)
        profile_catalog = ProfileCatalog.load(ROOT / "profiles")
        profile_repository = ProfileRepository(connection, profile_catalog)
        profile_repository.store_version(
            profile_version_id=profile_version_id,
            profile=load_profile(),
        )
        profile_repository.store_version(
            profile_version_id=api_profile_version_id,
            profile=load_object(ROOT / "profiles/document-convention-profile.example.json"),
        )
        canonical = CanonicalRepository(connection, ContractCatalog.load(ROOT / "contracts"))
        canonical.store_snapshot(
            DocumentSnapshotWrite(
                project_id=project_id,
                document_id=f"screen-document-{suffix}",
                document_version_id=screen_version_id,
                logical_name="screen-design.xlsx",
                source_ref="immutable://screen-design.xlsx",
                content_digest=hashlib.sha256(b"screen").hexdigest(),
                extractor_ref="manual-canonical@1",
                profile_version_id=profile_version_id,
                selected_variant_id="screen-item-table-ja",
                status=SnapshotStatus.COMMITTED,
                snapshot=screen_snapshot,
            )
        )
        canonical.store_snapshot(
            DocumentSnapshotWrite(
                project_id=project_id,
                document_id=f"api-document-{suffix}",
                document_version_id=api_version_id,
                logical_name="api-design.xlsx",
                source_ref="immutable://api-design.xlsx",
                content_digest=hashlib.sha256(b"api").hexdigest(),
                extractor_ref="manual-canonical@1",
                profile_version_id=api_profile_version_id,
                selected_variant_id="api-list",
                status=SnapshotStatus.COMMITTED,
                snapshot=api_snapshot,
            )
        )
        builder = CanonicalDocumentNodeBuilder()
        screen_nodes = builder.build(
            snapshot=screen_snapshot,
            document_version_id=screen_version_id,
            logical_name="screen-design.xlsx",
            document_type="screen_design",
        )
        api_nodes = builder.build(
            snapshot=api_snapshot,
            document_version_id=api_version_id,
            logical_name="api-design.xlsx",
            document_type="api_design",
        )
        nodes = DocumentNodeRepository(connection)
        nodes.store_nodes(
            project_id=project_id,
            snapshot_id=snapshot_id,
            nodes=screen_nodes,
        )
        nodes.store_nodes(
            project_id=project_id,
            snapshot_id=snapshot_id,
            nodes=api_nodes,
        )
        screen_slices = screen_nodes[1:]
        seed = screen_slices[1]
        related = screen_slices[3]
        api_slice = api_nodes[1]
        relation_profile: dict[str, Any] = {
            "profile_type": "DocumentRelationProfile",
            "profile_id": "integration-relations",
            "profile_version": "1.0.0",
            "rules": [
                {
                    "rule_id": "screen-to-api",
                    "relation_label": "calls_api",
                    "source_document_types": ["screen_design"],
                    "source_fact_types": ["screen_element"],
                    "source_fields": ["name"],
                    "target_document_types": ["api_design"],
                    "target_fact_types": ["api_endpoint"],
                    "target_fields": ["name"],
                    "value_normalizers": ["nfkc_casefold"],
                    "ambiguity_policy": "require_unique_target",
                },
                {
                    "rule_id": "screen-value-to-name",
                    "relation_label": "same_business_rule",
                    "source_document_types": ["screen_design"],
                    "source_fact_types": ["screen_element"],
                    "source_fields": ["value"],
                    "target_document_types": ["screen_design"],
                    "target_fact_types": ["screen_element"],
                    "target_fields": ["name"],
                    "value_normalizers": ["nfkc_casefold"],
                    "ambiguity_policy": "require_unique_target",
                },
            ],
            "unresolved_policy": "record_and_continue",
        }
        relation_service = DocumentRelationBuildService(
            connection=connection,
            profiles=profile_catalog,
        )
        first_request = DocumentRelationBuildRequest(
            build_id=f"relation-build-v1-{suffix}",
            project_id=project_id,
            snapshot_id=snapshot_id,
            profile_version_id=f"relation-profile-v1-{suffix}",
            profile_binding_key="relation:document_graph",
            profile_activation_event_id=f"relation-activation-v1-{suffix}",
            activated_by="reviewer@example.invalid",
            activation_reason="Reviewed exact relation rules",
        )
        first = relation_service.run(first_request, profile=relation_profile)
        replay = relation_service.run(first_request, profile=relation_profile)
        relation_profile_v2 = copy.deepcopy(relation_profile)
        relation_profile_v2["profile_version"] = "1.0.1"
        second_request = DocumentRelationBuildRequest(
            build_id=f"relation-build-v2-{suffix}",
            project_id=project_id,
            snapshot_id=snapshot_id,
            profile_version_id=f"relation-profile-v2-{suffix}",
            profile_binding_key="relation:document_graph",
            profile_activation_event_id=f"relation-activation-v2-{suffix}",
            activated_by="reviewer@example.invalid",
            activation_reason="Reviewed relation Profile revision",
        )
        second = relation_service.run(second_request, profile=relation_profile_v2)
        stale_replay = relation_service.run(first_request, profile=relation_profile)

        exact = nodes.find_by_business_key(
            project_id=project_id,
            snapshot_id=snapshot_id,
            business_key=seed.business_keys[0],
        )
        expanded = nodes.expand_neighborhood(
            project_id=project_id,
            snapshot_id=snapshot_id,
            seed_node_ids=(seed.node_id,),
            adjacent_distance=1,
        )

        assert tuple(record.node.node_id for record in exact) == (seed.node_id,)
        assert first.publication.created
        assert first.publication.state.relation_count == 2
        assert first.publication.state.unresolved_count == 6
        assert not replay.publication.created
        assert second.publication.created
        assert second.publication.state.is_current
        assert not stale_replay.publication.created
        assert stale_replay.publication.state.status is DocumentRelationBuildStatus.STALE
        relation_repository = DocumentRelationRepository(connection)
        assert (
            relation_repository.get_current_build(
                project_id=project_id,
                snapshot_id=snapshot_id,
            )
            == second.publication.state
        )
        active_relation_profile = profile_repository.get_active(
            project_id=project_id,
            binding_key="relation:document_graph",
        )
        assert active_relation_profile is not None
        assert active_relation_profile.profile_version_id == second_request.profile_version_id
        expansion_pairs = {(item.record.node.node_id, item.reason) for item in expanded}
        assert expansion_pairs == {
            (screen_slices[0].node_id, DocumentExpansionReason.ADJACENT),
            (screen_slices[2].node_id, DocumentExpansionReason.ADJACENT),
            (related.node_id, DocumentExpansionReason.RELATED),
            (api_slice.node_id, DocumentExpansionReason.CROSS_DOCUMENT),
        }
        assert (
            nodes.find_by_business_key(
                project_id=f"other-{project_id}",
                snapshot_id=snapshot_id,
                business_key=seed.business_keys[0],
            )
            == ()
        )
        assert nodes.list_document_profile_refs(
            project_id=project_id,
            snapshot_id=snapshot_id,
        ) == (
            "api-design-conventions-example@1.0.0",
            "screen-design-conventions-example@1.0.0",
        )
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT document_node_digest_drift_probe")
            cursor.execute(
                """
                UPDATE document_nodes
                SET content = content || ' tampered'
                WHERE document_node_id = %s
                """,
                (seed.node_id,),
            )
        with pytest.raises(PersistenceConflictError, match="content digest differs"):
            nodes.get_node(
                project_id=project_id,
                snapshot_id=snapshot_id,
                node_id=seed.node_id,
            )
        with pytest.raises(PersistenceConflictError, match="target node content digest differs"):
            SearchIndexRepository(connection).load_targets(
                project_id=project_id,
                snapshot_id=snapshot_id,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT document_node_digest_drift_probe")
            cursor.execute("RELEASE SAVEPOINT document_node_digest_drift_probe")
            cursor.execute("SAVEPOINT relation_plan_digest_drift_probe")
            cursor.execute(
                """
                UPDATE document_relation_entries
                SET rule_id = rule_id || '-tampered'
                WHERE document_relation_build_id = %s
                """,
                (second_request.build_id,),
            )
        with pytest.raises(PersistenceConflictError, match="plan digest differs"):
            relation_repository.get_current_build(
                project_id=project_id,
                snapshot_id=snapshot_id,
            )
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT relation_plan_digest_drift_probe")
            cursor.execute("RELEASE SAVEPOINT relation_plan_digest_drift_probe")
        connection.rollback()
