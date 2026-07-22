import json
import shutil
from hashlib import sha256
from pathlib import Path

from operamind.golden import GoldenDatasetValidator

ROOT = Path(__file__).parents[2]


def test_silver_manifest_is_structurally_valid() -> None:
    validator = GoldenDatasetValidator(ROOT / "golden-dataset")

    report = validator.validate(ROOT / "golden-dataset/manifest.silver.json")

    assert report.is_valid, report.issues


def test_silver_manifest_is_not_mvp_ready() -> None:
    validator = GoldenDatasetValidator(ROOT / "golden-dataset")

    report = validator.validate(ROOT / "golden-dataset/manifest.silver.json", require_ready=True)

    assert not report.is_valid
    issue_codes = {issue.code for issue in report.issues}
    assert "golden.not_ready" in issue_codes
    assert "golden.local_only_source" in issue_codes
    assert "golden.pending_review" in issue_codes
    assert "golden.rag_not_ready" in issue_codes
    assert "golden.ui_not_ready" in issue_codes


def test_reviewed_golden_dataset_is_structurally_valid_and_mvp_ready() -> None:
    validator = GoldenDatasetValidator(ROOT / "golden-dataset")

    structural = validator.validate(ROOT / "golden-dataset/manifest.golden.json")
    report = validator.validate(ROOT / "golden-dataset/manifest.golden.json", require_ready=True)

    assert structural.is_valid, structural.issues
    assert report.is_valid, report.issues


def test_rejected_human_judgment_step_prevents_readiness(tmp_path: Path) -> None:
    dataset_root = tmp_path / "golden-dataset"
    shutil.copytree(ROOT / "golden-dataset", dataset_root)
    manifest_path = dataset_root / "manifest.golden.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review_path = dataset_root / manifest["cases"][0]["review"]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["dataset_stage"] = "golden_candidate"
    review["review_status"] = "pending"
    review.pop("reviewed_by")
    review.pop("reviewed_at")
    review["steps"][2]["decision"] = "pending"
    review_path.write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = GoldenDatasetValidator(dataset_root).validate(manifest_path, require_ready=True)

    assert not report.is_valid
    assert "golden.pending_review" in {issue.code for issue in report.issues}


def test_review_steps_must_appear_exactly_once(tmp_path: Path) -> None:
    dataset_root = tmp_path / "golden-dataset"
    shutil.copytree(ROOT / "golden-dataset", dataset_root)
    manifest_path = dataset_root / "manifest.golden.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review_path = dataset_root / manifest["cases"][0]["review"]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["steps"][3]["step_id"] = review["steps"][2]["step_id"]
    review_path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")

    report = GoldenDatasetValidator(dataset_root).validate(manifest_path)

    issue_codes = {issue.code for issue in report.issues}
    assert "golden.review_step_set_mismatch" in issue_codes


def test_dataset_digest_binds_every_referenced_case_file(tmp_path: Path) -> None:
    dataset_root = tmp_path / "golden-dataset"
    shutil.copytree(ROOT / "golden-dataset", dataset_root)
    manifest_path = dataset_root / "manifest.golden.json"
    validator = GoldenDatasetValidator(dataset_root)
    manifest_digest = sha256(manifest_path.read_bytes()).hexdigest()
    before = validator.dataset_digest(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review_path = dataset_root / manifest["cases"][0]["review"]
    review_path.write_text(
        review_path.read_text(encoding="utf-8") + "\nUnreviewed note.\n",
        encoding="utf-8",
    )

    after = validator.dataset_digest(manifest_path)

    assert before != after
    assert sha256(manifest_path.read_bytes()).hexdigest() == manifest_digest


def test_ui_expectation_schema_and_cross_references_are_enforced(tmp_path: Path) -> None:
    dataset_root = tmp_path / "golden-dataset"
    shutil.copytree(ROOT / "golden-dataset", dataset_root)
    expectation_path = (
        dataset_root / "cases/visiondemo-expense-status-filter/expected-ui-scenarios.silver.json"
    )
    payload = json.loads(expectation_path.read_text(encoding="utf-8"))
    payload["project_id"] = "another-project"
    payload["scenarios"][0]["steps"] = []
    payload["scenarios"][1]["scenario_id"] = payload["scenarios"][0]["scenario_id"]
    expectation_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = GoldenDatasetValidator(dataset_root).validate(dataset_root / "manifest.silver.json")

    assert not report.is_valid
    issue_codes = {issue.code for issue in report.issues}
    assert "golden.ui_expectation_schema_violation" in issue_codes
    assert "golden.ui_project_id_mismatch" in issue_codes
    assert "golden.duplicate_ui_scenario_id" in issue_codes
    assert "golden.ui_outcome_coverage" in issue_codes


def test_silver_test_data_plan_schema_and_execution_semantics_are_enforced(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "golden-dataset"
    shutil.copytree(ROOT / "golden-dataset", dataset_root)
    plan_path = dataset_root / (
        "cases/visiondemo-expense-employee-cross-screen/test-data-plan.silver.json"
    )
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["generation_flows"][0]["cleanup_steps"] = []
    plan_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = GoldenDatasetValidator(dataset_root).validate(
        dataset_root / "manifest.silver.json"
    )

    assert not report.is_valid
    assert "golden.test_data_plan_semantic_violation" in {
        issue.code for issue in report.issues
    }
