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
