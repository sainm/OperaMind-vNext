from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from operamind.application import change_orchestration_service as service_module
from operamind.application.change_orchestration import ChangeOrchestrationBlockedError
from operamind.application.change_orchestration_service import (
    ChangeOrchestrationService,
    _select_reviewed_case,
)

ROOT = Path(__file__).parents[2]
REVISION = "ad23d0a7a54ce196c0ea6c41445e5f5492ae1ea6"
STABLE_KEYS = {"screen_element:screen_expense_list/expense-search-status"}


def test_orchestrate_uses_one_evidence_basis_and_persists_the_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, Any] = {}
    reviewed_case = object()
    evidence = SimpleNamespace(
        change_request={"project_id": "visiondemo"},
        analysis_case_id="case-1",
        structured_changes=(
            {"change_id": "change-1", "stable_key": "stable-1"},
            {"change_id": "change-2", "stable_key": "stable-2"},
        ),
        accepted_structured_change_refs=frozenset({"change-1", "change-2"}),
        impact_report={"repository_revision": REVISION},
        impact_report_state="confirmed",
        impact_confirmation={"impact_report_id": "impact-1"},
    )

    class Repository:
        def __init__(self, connection: object, contracts: object) -> None:
            observed["repository_init"] = (connection, contracts)

        def load_evidence(self, change_request_id: str) -> object:
            observed["loaded"] = change_request_id
            return evidence

        def persist(self, *, result: object, created_by: str) -> object:
            observed["persisted"] = (result, created_by)
            return SimpleNamespace(created=True)

    planned = SimpleNamespace(
        orchestration={"orchestration_id": "orchestration-1"},
        artifacts=({"artifact_type": "TestPlan"},),
    )

    class Planner:
        def __init__(self, *, repository_root: Path) -> None:
            observed["planner_root"] = repository_root

        def plan(self, value: object) -> object:
            observed["plan_input"] = value
            return planned

    def select_case(**kwargs: object) -> object:
        observed["selection"] = kwargs
        return reviewed_case

    contracts = object()
    connection = object()
    monkeypatch.setattr(service_module.ContractCatalog, "load", lambda _path: contracts)
    monkeypatch.setattr(service_module, "ChangeOrchestrationRepository", Repository)
    monkeypatch.setattr(service_module, "ChangeOrchestrationPlanner", Planner)
    monkeypatch.setattr(service_module, "_select_reviewed_case", select_case)

    result = ChangeOrchestrationService(
        connection=connection, repository_root=tmp_path
    ).orchestrate(change_request_id="request-1", actor="worker-1")

    assert result.created is True
    assert result.orchestration == planned.orchestration
    assert result.artifacts == planned.artifacts
    assert observed["repository_init"] == (connection, contracts)
    assert observed["planner_root"] == tmp_path.resolve()
    assert observed["loaded"] == "request-1"
    assert observed["selection"] == {
        "repository_root": tmp_path.resolve(),
        "project_id": "visiondemo",
        "repository_revision": REVISION,
        "stable_keys": {"stable-1", "stable-2"},
    }
    plan_input = observed["plan_input"]
    assert plan_input.change_request is evidence.change_request
    assert plan_input.reviewed_case is reviewed_case
    assert observed["persisted"] == (planned, "worker-1")


def test_select_reviewed_case_loads_the_unique_frozen_match(tmp_path: Path) -> None:
    repository_root = _copy_dataset(tmp_path)
    manifest_path = repository_root / "golden-dataset/manifest.golden.json"
    _update_json(
        manifest_path,
        lambda payload: payload["cases"].insert(
            0,
            {
                "case_id": "other-project-case",
                "project_id": "other-project",
                "expected_changes": "does-not-need-to-exist.json",
            },
        ),
    )

    case = _select_reviewed_case(
        repository_root=repository_root,
        project_id="visiondemo",
        repository_revision=REVISION,
        stable_keys=STABLE_KEYS,
    )

    assert case.case_id == "visiondemo-expense-status-filter-golden"
    assert case.repository["base_revision"] == REVISION


@pytest.mark.parametrize(
    ("field", "value"),
    [("dataset_stage", "silver"), ("status", "draft")],
)
def test_select_reviewed_case_requires_a_frozen_golden_manifest(
    tmp_path: Path, field: str, value: str
) -> None:
    repository_root = _copy_dataset(tmp_path)
    _update_json(
        repository_root / "golden-dataset/manifest.golden.json",
        lambda payload: payload.update({field: value}),
    )

    with pytest.raises(ChangeOrchestrationBlockedError, match="not frozen"):
        _select_reviewed_case(
            repository_root=repository_root,
            project_id="visiondemo",
            repository_revision=REVISION,
            stable_keys=STABLE_KEYS,
        )


@pytest.mark.parametrize(
    ("project_id", "revision"),
    [("missing-project", REVISION), ("visiondemo", "different-revision")],
)
def test_select_reviewed_case_requires_project_revision_binding(
    tmp_path: Path, project_id: str, revision: str
) -> None:
    repository_root = _copy_dataset(tmp_path)

    with pytest.raises(ChangeOrchestrationBlockedError, match="does not bind"):
        _select_reviewed_case(
            repository_root=repository_root,
            project_id=project_id,
            repository_revision=revision,
            stable_keys=STABLE_KEYS,
        )


def test_select_reviewed_case_rejects_an_escaping_expected_path(tmp_path: Path) -> None:
    repository_root = _copy_dataset(tmp_path)
    manifest_path = repository_root / "golden-dataset/manifest.golden.json"
    _update_json(
        manifest_path,
        lambda payload: payload["cases"][0].update(
            {"expected_changes": "../outside.json"}
        ),
    )

    with pytest.raises(ChangeOrchestrationBlockedError, match="escapes dataset root"):
        _select_reviewed_case(
            repository_root=repository_root,
            project_id="visiondemo",
            repository_revision=REVISION,
            stable_keys=STABLE_KEYS,
        )


def test_select_reviewed_case_rejects_missing_or_duplicate_matches(
    tmp_path: Path,
) -> None:
    repository_root = _copy_dataset(tmp_path)
    with pytest.raises(ChangeOrchestrationBlockedError, match="found 0"):
        _select_reviewed_case(
            repository_root=repository_root,
            project_id="visiondemo",
            repository_revision=REVISION,
            stable_keys={"different-key"},
        )

    manifest_path = repository_root / "golden-dataset/manifest.golden.json"
    _update_json(
        manifest_path,
        lambda payload: payload["cases"].append(dict(payload["cases"][0])),
    )
    with pytest.raises(ChangeOrchestrationBlockedError, match="found 2"):
        _select_reviewed_case(
            repository_root=repository_root,
            project_id="visiondemo",
            repository_revision=REVISION,
            stable_keys=STABLE_KEYS,
        )


def test_select_reviewed_case_ignores_case_with_different_base_revision(
    tmp_path: Path,
) -> None:
    repository_root = _copy_dataset(tmp_path)
    case_path = (
        repository_root
        / "golden-dataset/cases/visiondemo-expense-status-filter-golden/change-loop-case.json"
    )
    _update_json(
        case_path,
        lambda payload: payload["repository"].update(
            {"base_revision": "0000000000000000000000000000000000000000"}
        ),
    )

    with pytest.raises(ChangeOrchestrationBlockedError, match="found 0"):
        _select_reviewed_case(
            repository_root=repository_root,
            project_id="visiondemo",
            repository_revision=REVISION,
            stable_keys=STABLE_KEYS,
        )


def _copy_dataset(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "golden-dataset", tmp_path / "golden-dataset")
    return tmp_path


def _update_json(path: Path, update: Any) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    update(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
