from pathlib import Path

import pytest

from operamind.infrastructure.postgres.migrations import MigrationCatalog, MigrationError

ROOT = Path(__file__).parents[3]


def test_repository_migrations_are_sequential_and_transaction_free() -> None:
    catalog = MigrationCatalog.load(ROOT / "migrations")

    assert [migration.version for migration in catalog.migrations] == [
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0010",
        "0011",
        "0012",
        "0013",
        "0014",
        "0015",
        "0016",
        "0017",
        "0018",
        "0019",
        "0020",
        "0021",
        "0022",
        "0023",
        "0024",
        "0025",
        "0026",
        "0027",
        "0028",
        "0029",
        "0030",
        "0031",
        "0032",
        "0033",
        "0034",
        "0035",
        "0036",
        "0037",
        "0038",
        "0039",
        "0040",
        "0041",
        "0042",
        "0043",
        "0044",
        "0045",
        "0046",
        "0047",
        "0048",
        "0049",
        "0050",
        "0051",
        "0052",
        "0053",
        "0054",
        "0055",
        "0056",
        "0057",
        "0058",
        "0059",
        "0060",
        "0061",
        "0062",
        "0063",
        "0064",
        "0065",
        "0066",
        "0067",
        "0068",
        "0069",
        "0070",
        "0071",
        "0072",
        "0073",
        "0074",
        "0075",
        "0076",
        "0077",
        "0078",
    ]
    assert all(len(migration.checksum) == 64 for migration in catalog.migrations)


def test_verification_only_scope_allows_empty_edit_authority() -> None:
    migration = (ROOT / "migrations/0069_verification_only_execution_scope.sql").read_text(
        encoding="utf-8"
    )

    assert "jsonb_array_length(editable_files) = 0" in migration
    assert "jsonb_array_length(allowed_items) = 0" in migration
    assert "DROP CONSTRAINT approval_grants_arrays_valid" in migration


def test_main_flow_coordinator_candidates_have_bounded_query_indexes() -> None:
    migration = (ROOT / "migrations/0070_main_flow_coordinator_candidates.sql").read_text(
        encoding="utf-8"
    )

    assert "change_automation_runs_coordinator_candidates_idx" in migration
    assert "WHERE status IN ('running', 'waiting')" in migration
    assert "orchestration_task_claims_active_expiry_idx" in migration
    assert "WHERE status = 'active'" in migration


def test_test_data_execution_has_a_persisted_lease() -> None:
    migration = (ROOT / "migrations/0071_test_data_execution_leases.sql").read_text(
        encoding="utf-8"
    )

    assert "execution_owner text" in migration
    assert "lease_expires_at timestamptz" in migration
    assert "test_data_execution_runs_running_lease_idx" in migration


def test_project_onboarding_is_staged_and_recoverable() -> None:
    migration = (ROOT / "migrations/0072_project_onboarding.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE project_onboarding_runs" in migration
    assert "settings_revision integer NOT NULL" in migration
    assert "lease_expires_at timestamptz" in migration
    assert "'discover', 'documents', 'index', 'complete'" in migration
    assert "project_onboarding_runs_claimable_idx" in migration


def test_project_document_profiles_are_learned_and_version_bound() -> None:
    migration = (ROOT / "migrations/0073_project_document_profile_learning.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE project_document_learning_runs" in migration
    assert "CREATE TABLE project_document_learning_profiles" in migration
    assert "waiting_for_profile" in migration
    assert "requested_action IN ('initialize', 'rescan', 'reindex', 'relearn')" in migration


def test_ui_verification_closure_binding_uses_current_artifact_store() -> None:
    migration = (ROOT / "migrations/0060_ui_verification_artifact_binding.sql").read_text(
        encoding="utf-8"
    )

    assert "DROP CONSTRAINT change_closure_results_ui_fk" in migration
    assert "FOREIGN KEY (\n        project_id, ui_verification_result_id\n    )" in migration
    assert "REFERENCES artifact_records(project_id, artifact_id)" in migration
    assert "change_validations" not in migration


def test_project_local_sources_allow_version_control_optional_paths() -> None:
    migration = (ROOT / "migrations/0061_project_local_sources.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE project_workspaces" in migration
    assert "'git', 'local_files'" in migration
    assert "CREATE TABLE project_document_roots" in migration
    assert "UNIQUE (project_id, root_path)" in migration
    assert "migration:0061" in migration


def test_project_source_git_baselines_bind_code_and_documents_to_commits() -> None:
    migration = (ROOT / "migrations/0066_project_source_git_baselines.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE project_source_git_baselines" in migration
    assert "source_kind IN ('code', 'document')" in migration
    assert "management_kind IN ('existing_git', 'operamind_local_git')" in migration
    assert "baseline_revision text NOT NULL" in migration


def test_change_automation_recovery_persists_rag_and_supersedes_old_runs() -> None:
    migration = (ROOT / "migrations/0063_change_automation_recovery.sql").read_text(
        encoding="utf-8"
    )

    assert "'superseded'" in migration
    assert "row_number() OVER" in migration
    assert "CREATE TABLE change_automation_rag_discoveries" in migration
    assert "discovery ->> 'status' = 'ready'" in migration


def test_ui_test_plan_revision_task_adds_a_bounded_copilot_stage() -> None:
    migration = (ROOT / "migrations/0064_ui_test_plan_revision_task.sql").read_text(
        encoding="utf-8"
    )

    assert "'ui_test_revision'" in migration
    assert "copilot_coding_tasks_current_stage_valid" in migration


def test_project_test_environment_and_post_commit_planning_are_persisted() -> None:
    migration = (ROOT / "migrations/0065_project_test_environment.sql").read_text(encoding="utf-8")

    assert "ADD COLUMN test_base_url" in migration
    assert "^https?://" in migration
    assert "'test_planning'" in migration


def test_migration_cannot_control_its_own_transaction(tmp_path: Path) -> None:
    (tmp_path / "0001_invalid.sql").write_text("BEGIN;\nSELECT 1;\nCOMMIT;\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="must not manage transactions"):
        MigrationCatalog.load(tmp_path)
