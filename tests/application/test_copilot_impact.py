from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from operamind.application.copilot_impact import (
    CopilotImpactService,
    _safe_relative_path,
    _validate_code_scope,
    _validate_new_code_path,
)


def _graph() -> dict[str, object]:
    return {
        "files": [
            {
                "path": "src/main/java/example/ExpenseService.java",
                "role": "production",
                "symbols": [
                    {
                        "symbol_id": "example.ExpenseService#search(String)",
                        "name": "search",
                        "signature": "search(String status)",
                    }
                ],
            },
            {
                "path": "src/test/java/example/ExpenseServiceTest.java",
                "role": "test",
                "symbols": [],
            },
        ]
    }


def test_copilot_code_scope_is_graph_validated_and_retains_test_binding() -> None:
    result = _validate_code_scope(
        (
            {
                "target_path": "src/main/java/example/ExpenseService.java",
                "target_symbols": ["search(String status)"],
                "recommended_action": "modify",
                "test_file_refs": ["src/test/java/example/ExpenseServiceTest.java"],
                "rationale": "The changed status rule is implemented by search.",
                "ui_impact": True,
            },
        ),
        graph=_graph(),
        document_change_refs=("change-1",),
    )

    assert result[0]["target_path"] == "src/main/java/example/ExpenseService.java"
    assert result[0]["test_file_refs"] == [
        "src/test/java/example/ExpenseServiceTest.java"
    ]


def test_copilot_code_scope_rejects_a_path_absent_from_graph() -> None:
    with pytest.raises(ValueError, match="absent from the Code Graph"):
        _validate_code_scope(
            (
                {
                    "target_path": "src/main/java/example/Guessed.java",
                    "target_symbols": [],
                    "recommended_action": "modify",
                    "test_file_refs": ["src/test/java/example/ExpenseServiceTest.java"],
                    "rationale": "A guessed file must not be accepted.",
                    "ui_impact": False,
                },
            ),
            graph=_graph(),
            document_change_refs=("change-1",),
        )


def test_copilot_code_scope_allows_a_safe_new_graph_path() -> None:
    result = _validate_code_scope(
        (
            {
                "target_path": "src/main/java/example/ReturnedStatusPolicy.java",
                "target_symbols": [],
                "recommended_action": "add",
                "test_file_refs": ["src/test/java/example/ReturnedStatusPolicyTest.java"],
                "rationale": "The new policy isolates the returned status rule.",
                "ui_impact": False,
            },
        ),
        graph=_graph(),
        document_change_refs=("change-1",),
        scan_roots=("src/main", "src/test"),
        languages=("java",),
    )

    assert result[0]["recommended_action"] == "add"


def test_copilot_code_scope_completes_resolved_graph_impact_closure() -> None:
    graph = _graph()
    files = graph["files"]
    assert isinstance(files, list)
    files[0]["file_id"] = "file-service"
    files[1]["file_id"] = "file-test"
    files.append(
        {
            "file_id": "file-repository",
            "path": "src/main/java/example/ExpenseRepository.java",
            "role": "production",
            "symbols": [{"symbol_id": "repository-search", "name": "search"}],
        }
    )
    graph["edges"] = [
        {
            "edge_id": "edge-call-repository",
            "edge_type": "calls",
            "from_ref": "example.ExpenseService#search(String)",
            "to_ref": "repository-search",
            "resolution_status": "resolved",
            "confidence": "high",
        }
    ]

    result = _validate_code_scope(
        _scope(),
        graph=graph,
        document_change_refs=("change-1",),
    )

    assert result[0]["related_code_paths"] == [
        "src/main/java/example/ExpenseRepository.java"
    ]
    assert result[0]["graph_path_refs"] == ["edge-call-repository"]
    assert result[1] == {
        "target_path": "src/main/java/example/ExpenseRepository.java",
        "target_symbols": [],
        "recommended_action": "review_only",
        "test_file_refs": ["src/test/java/example/ExpenseServiceTest.java"],
        "rationale": (
            "OperaMind added this file as a review-only member of the "
            "resolved two-hop Code Graph impact closure."
        ),
        "ui_impact": False,
        "related_code_paths": [],
        "graph_path_refs": ["edge-call-repository"],
    }


def test_copilot_code_scope_accepts_file_level_symbolless_sources() -> None:
    graph = _graph()
    files = graph["files"]
    assert isinstance(files, list)
    files.append(
        {
            "file_id": "file-template",
            "path": "src/main/resources/templates/expense-list.html",
            "role": "production",
            "symbols": [
                {
                    "symbol_id": "template:expense-list:select-1",
                    "name": "select-1",
                }
            ],
        }
    )

    result = _validate_code_scope(
        (
            {
                "target_path": "src/main/resources/templates/expense-list.html",
                "target_symbols": ["status-filter"],
                "recommended_action": "modify",
                "test_file_refs": ["src/test/java/example/ExpenseServiceTest.java"],
                "rationale": "The status selector is rendered in this template.",
                "ui_impact": True,
            },
        ),
        graph=graph,
        document_change_refs=("change-1",),
    )

    assert result[0]["target_symbols"] == []


class _Contracts:
    def __init__(self) -> None:
        self.validated: list[dict[str, object]] = []

    def validate_artifact(self, artifact: dict[str, object]) -> None:
        self.validated.append(artifact)


class _Requests:
    def __init__(
        self,
        *,
        registration: dict[str, str] | None,
        revision_id: str | None = "revision-1",
    ) -> None:
        self.registration = registration
        self.revision_id = revision_id

    def project_repository_registration(
        self,
        project_id: str,
    ) -> dict[str, str] | None:
        return self.registration

    def repository_revision_id(
        self,
        *,
        repository_id: str,
        commit_sha: str,
    ) -> str | None:
        return self.revision_id

    def get_change_request(self, change_request_id: str) -> dict[str, object]:
        return {
            "artifact": {
                "change_request_id": change_request_id,
                "business_rules": [
                    {"business_rule_id": "rule-all", "text": "すべてを表示する"},
                    {"business_rule_id": "rule-status", "text": "状態検索を維持する"},
                ],
            }
        }


class _Artifacts:
    def __init__(self) -> None:
        self.stored: list[dict[str, object]] = []

    def store(self, **values: object) -> None:
        self.stored.append(values)


class _Impacts:
    def __init__(self) -> None:
        self.published: list[dict[str, object]] = []

    def publish_report(self, **values: object) -> SimpleNamespace:
        self.published.append(values)
        return SimpleNamespace(created=True)


def _service(
    workspace_root: Path,
    *,
    remote_url: str = "git@example.test:operamind/repository.git",
    revision_id: str | None = "revision-1",
) -> CopilotImpactService:
    service = CopilotImpactService.__new__(CopilotImpactService)
    service._contracts = _Contracts()
    service._requests = _Requests(
        registration={
            "workspace_root": str(workspace_root),
            "remote_url": remote_url,
            "repository_id": "repository-1",
        },
        revision_id=revision_id,
    )
    service._artifacts = _Artifacts()
    service._impacts = _Impacts()
    return service


def _scope(*, ui_impact: bool = False) -> tuple[dict[str, Any], ...]:
    return (
        {
            "target_path": "src/main/java/example/ExpenseService.java",
            "target_symbols": ["search(String status)"],
            "recommended_action": "modify",
            "test_file_refs": ["src/test/java/example/ExpenseServiceTest.java"],
            "rationale": "The changed status rule is implemented by search.",
            "ui_impact": ui_impact,
        },
    )


def test_publish_creates_graph_validated_context_and_impact_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        "operamind.application.copilot_impact.GitWorkspaceInspector",
        lambda: SimpleNamespace(
            inspect=lambda root: SimpleNamespace(
                remote_url="git@example.test:operamind/repository.git",
                head_sha="abc123",
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "_build_graph",
        lambda **values: (_graph(), ("src/main", "src/test"), ("java",)),
    )

    result = service.publish(
        project_id="project-1",
        analysis_case_id="analysis-1",
        change_request_id="change-request-1",
        coding_task_id="coding-task-1",
        workspace_root=tmp_path,
        source_document_snapshot_id="document-before",
        target_document_snapshot_id="document-after",
        search_index_build_id="index-1",
        document_change_refs=("structured-change-1",),
        code_scope=_scope(ui_impact=True),
    )

    assert result["created"] is True
    assert result["code_scope"] == [
        {
            "target_path": "src/main/java/example/ExpenseService.java",
            "target_symbols": ["search(String status)"],
            "recommended_action": "modify",
            "test_file_refs": ["src/test/java/example/ExpenseServiceTest.java"],
            "rationale": "The changed status rule is implemented by search.",
                "ui_impact": True,
                "related_code_paths": [],
                "graph_path_refs": [],
            }
    ]
    assert len(service._contracts.validated) == 2
    context, report = service._contracts.validated
    assert context["artifact_type"] == "CopilotImpactContext"
    assert report["artifact_type"] == "ImpactReport"
    assert report["ui_impact_status"] == "impacted"
    assert len(report["required_ui_scenario_refs"]) == 2
    assert all(
        str(value).startswith("ui-scenario-")
        for value in report["required_ui_scenario_refs"]
    )
    assert report["repository_revision"] == "abc123"
    assert report["items"][0]["structured_change_refs"] == ["structured-change-1"]
    assert len(service._artifacts.stored) == 1
    assert len(service._impacts.published) == 1


def test_revised_scope_gets_new_artifact_ids_on_the_same_coding_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        "operamind.application.copilot_impact.GitWorkspaceInspector",
        lambda: SimpleNamespace(
            inspect=lambda root: SimpleNamespace(
                remote_url="git@example.test:operamind/repository.git",
                head_sha="abc123",
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "_build_graph",
        lambda **values: (_graph(), ("src/main", "src/test"), ("java",)),
    )

    first = service.publish(
        project_id="project-1",
        analysis_case_id="analysis-1",
        change_request_id="change-request-1",
        coding_task_id="coding-task-1",
        workspace_root=tmp_path,
        source_document_snapshot_id="document-before",
        target_document_snapshot_id="document-after",
        search_index_build_id="index-1",
        document_change_refs=("structured-change-1",),
        code_scope=_scope(),
    )
    revised_scope = (
        {
            **_scope()[0],
            "recommended_action": "review_only",
            "rationale": "The current implementation already satisfies the design change.",
        },
    )
    revised = service.publish(
        project_id="project-1",
        analysis_case_id="analysis-1",
        change_request_id="change-request-1",
        coding_task_id="coding-task-1",
        workspace_root=tmp_path,
        source_document_snapshot_id="document-before",
        target_document_snapshot_id="document-after",
        search_index_build_id="index-1",
        document_change_refs=("structured-change-1",),
        code_scope=revised_scope,
    )

    assert revised["context_id"] != first["context_id"]
    assert revised["impact_report_id"] != first["impact_report_id"]


def test_impact_identity_and_context_record_revision_and_fallback_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    head = {"sha": "revision-before"}
    monkeypatch.setattr(
        "operamind.application.copilot_impact.GitWorkspaceInspector",
        lambda: SimpleNamespace(
            inspect=lambda root: SimpleNamespace(
                remote_url="git@example.test:operamind/repository.git",
                head_sha=head["sha"],
            )
        ),
    )
    service._requests.revision_id = "revision-1"
    monkeypatch.setattr(
        service,
        "_build_graph",
        lambda **values: (_graph(), ("src/main", "src/test"), ("java",)),
    )

    first = service.publish(
        project_id="project-1",
        analysis_case_id="analysis-1",
        change_request_id="change-request-1",
        coding_task_id="coding-task-1",
        workspace_root=tmp_path,
        source_document_snapshot_id="document-before",
        target_document_snapshot_id="document-after",
        search_index_build_id="index-1",
        document_change_refs=("structured-change-1",),
        code_scope=_scope(),
        actor="codex:fallback",
        provider_id="codex_fallback",
    )
    context = service._contracts.validated[-2]
    assert context["generated_by"] == "codex:fallback"
    assert context["generator_provider"] == "codex_fallback"

    head["sha"] = "revision-after"
    revised = service.publish(
        project_id="project-1",
        analysis_case_id="analysis-1",
        change_request_id="change-request-1",
        coding_task_id="coding-task-1",
        workspace_root=tmp_path,
        source_document_snapshot_id="document-before",
        target_document_snapshot_id="document-after",
        search_index_build_id="index-1",
        document_change_refs=("structured-change-1",),
        code_scope=_scope(),
        actor="codex:fallback",
        provider_id="codex_fallback",
    )

    assert revised["impact_report_id"] != first["impact_report_id"]


def test_publish_rejects_missing_scope_or_repository_registration(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="must not be empty"):
        service.publish(
            project_id="project-1",
            analysis_case_id="analysis-1",
            change_request_id="change-request-1",
            coding_task_id="coding-task-1",
            workspace_root=tmp_path,
            source_document_snapshot_id="document-before",
            target_document_snapshot_id="document-after",
            search_index_build_id="index-1",
            document_change_refs=("structured-change-1",),
            code_scope=(),
        )

    service._requests.registration = None
    with pytest.raises(ValueError, match="no unambiguous Repository"):
        service.publish(
            project_id="project-1",
            analysis_case_id="analysis-1",
            change_request_id="change-request-1",
            coding_task_id="coding-task-1",
            workspace_root=tmp_path,
            source_document_snapshot_id="document-before",
            target_document_snapshot_id="document-after",
            search_index_build_id="index-1",
            document_change_refs=("structured-change-1",),
            code_scope=_scope(),
        )


@pytest.mark.parametrize(
    ("actor", "provider_id"),
    [(" ", "vscode_github_copilot"), ("mcp:github-copilot", " ")],
)
def test_publish_rejects_blank_impact_provenance(
    tmp_path: Path,
    actor: str,
    provider_id: str,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="provenance must not be blank"):
        service.publish(
            project_id="project-1",
            analysis_case_id="analysis-1",
            change_request_id="change-request-1",
            coding_task_id="coding-task-1",
            workspace_root=tmp_path,
            source_document_snapshot_id="document-before",
            target_document_snapshot_id="document-after",
            search_index_build_id="index-1",
            document_change_refs=("structured-change-1",),
            code_scope=_scope(),
            actor=actor,
            provider_id=provider_id,
        )


def test_build_graph_requires_exactly_one_active_framework_profile() -> None:
    service: Any = CopilotImpactService.__new__(CopilotImpactService)
    service._profile_repository = SimpleNamespace(
        list_active_by_type=lambda **values: ()
    )

    with pytest.raises(ValueError, match="exactly one active CodeFrameworkProfile"):
        service._build_graph(
            project_id="project-1",
            coding_task_id="coding-task-1",
            graph_id="graph-1",
            repository_id="repository-1",
            repository_revision_id="revision-1",
            workspace_root=Path("/workspace"),
        )


def test_build_graph_rejects_incomplete_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {
        "default_scan_roots": ["src/main", "src/test"],
        "languages": ["java"],
    }
    service: Any = CopilotImpactService.__new__(CopilotImpactService)
    service._connection = object()
    service._contracts = object()
    service._profiles = object()
    service._profile_repository = SimpleNamespace(
        list_active_by_type=lambda **values: (
            SimpleNamespace(
                profile=profile,
                profile_version_id="profile-version-1",
                binding_key="binding-1",
            ),
        )
    )
    captured: dict[str, object] = {}

    def build_service(**values: object) -> SimpleNamespace:
        captured["dependencies"] = values

        def run(request: object, *, profile: object) -> SimpleNamespace:
            captured["request"] = request
            captured["profile"] = profile
            return SimpleNamespace(
                scan=SimpleNamespace(artifact={"scan_status": "partial"})
            )

        return SimpleNamespace(run=run)

    monkeypatch.setattr(
        "operamind.application.copilot_impact.CodeGraphBuildService",
        build_service,
    )

    with pytest.raises(ValueError, match="Code Graph is not complete"):
        service._build_graph(
            project_id="project-1",
            coding_task_id="coding-task-1",
            graph_id="graph-1",
            repository_id="repository-1",
            repository_revision_id="revision-1",
            workspace_root=Path("/workspace"),
        )

    assert captured["profile"] is profile
    assert captured["dependencies"] == {
        "connection": service._connection,
        "contracts": service._contracts,
        "profiles": service._profiles,
    }


@pytest.mark.parametrize(
    ("remote_url", "revision_id", "expected"),
    [
        ("git@example.test:other/repository.git", "revision-1", "remote differs"),
        ("git@example.test:operamind/repository.git", None, "revision is not registered"),
    ],
)
def test_publish_rejects_repository_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_url: str,
    revision_id: str | None,
    expected: str,
) -> None:
    service = _service(tmp_path, revision_id=revision_id)
    monkeypatch.setattr(
        "operamind.application.copilot_impact.GitWorkspaceInspector",
        lambda: SimpleNamespace(
            inspect=lambda root: SimpleNamespace(
                remote_url=remote_url,
                head_sha="abc123",
            )
        ),
    )

    with pytest.raises(ValueError, match=expected):
        service.publish(
            project_id="project-1",
            analysis_case_id="analysis-1",
            change_request_id="change-request-1",
            coding_task_id="coding-task-1",
            workspace_root=tmp_path,
            source_document_snapshot_id="document-before",
            target_document_snapshot_id="document-after",
            search_index_build_id="index-1",
            document_change_refs=("structured-change-1",),
            code_scope=_scope(),
        )


def test_publish_rejects_workspace_outside_registration(tmp_path: Path) -> None:
    registered = tmp_path / "registered"
    proposed = tmp_path / "proposed"
    registered.mkdir()
    proposed.mkdir()
    service = _service(registered)

    with pytest.raises(ValueError, match="Workspace differs"):
        service.publish(
            project_id="project-1",
            analysis_case_id="analysis-1",
            change_request_id="change-request-1",
            coding_task_id="coding-task-1",
            workspace_root=proposed,
            source_document_snapshot_id="document-before",
            target_document_snapshot_id="document-after",
            search_index_build_id="index-1",
            document_change_refs=("structured-change-1",),
            code_scope=_scope(),
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", "unsafe"),
        ("/src/file.py", "unsafe"),
        ("src/../file.py", "unsafe"),
        (r"src\file.py", "unsafe"),
    ],
)
def test_safe_relative_path_rejects_unsafe_values(
    value: str,
    expected: str,
) -> None:
    with pytest.raises(ValueError, match=expected):
        _safe_relative_path(value)


def test_new_code_path_must_match_scan_root_and_language() -> None:
    with pytest.raises(ValueError, match="outside configured scan roots"):
        _validate_new_code_path(
            "docs/example.py",
            scan_roots=("src",),
            languages=("python",),
        )
    with pytest.raises(ValueError, match="unsupported language suffix"):
        _validate_new_code_path(
            "src/example.txt",
            scan_roots=("src",),
            languages=("python",),
        )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ((), "no document change evidence"),
        (
            (
                {
                    "target_path": "src/main/java/example/ExpenseService.java",
                    "target_symbols": [],
                    "recommended_action": "execute",
                    "test_file_refs": ["src/test/java/example/ExpenseServiceTest.java"],
                    "rationale": "Reason",
                    "ui_impact": False,
                },
            ),
            "action is not allowed",
        ),
        (
            (
                {
                    "target_path": "src/main/java/example/ExpenseService.java",
                    "target_symbols": [],
                    "recommended_action": "modify",
                    "test_file_refs": [],
                    "rationale": "Reason",
                    "ui_impact": False,
                },
            ),
            "has no test files",
        ),
    ],
)
def test_scope_rejects_missing_evidence_action_or_tests(
    changes: tuple[dict[str, Any], ...],
    expected: str,
) -> None:
    document_refs = () if not changes else ("change-1",)
    with pytest.raises(ValueError, match=expected):
        _validate_code_scope(
            changes or _scope(),
            graph=_graph(),
            document_change_refs=document_refs,
        )


def test_scope_rejects_duplicate_symbols_and_untrusted_test_references() -> None:
    duplicate = _scope()[0]
    with pytest.raises(ValueError, match="duplicate path"):
        _validate_code_scope(
            (duplicate, duplicate),
            graph=_graph(),
            document_change_refs=("change-1",),
        )

    invalid_symbol = {**duplicate, "target_symbols": ["guessedSymbol"]}
    with pytest.raises(ValueError, match="symbols are absent"):
        _validate_code_scope(
            (invalid_symbol,),
            graph=_graph(),
            document_change_refs=("change-1",),
        )

    invalid_test = {
        **duplicate,
        "test_file_refs": ["src/main/java/example/ExpenseService.java"],
    }
    with pytest.raises(ValueError, match="not a Graph test file"):
        _validate_code_scope(
            (invalid_test,),
            graph=_graph(),
            document_change_refs=("change-1",),
        )


def test_scope_rejects_blank_rationale_and_non_boolean_ui_impact() -> None:
    scope = _scope()[0]
    with pytest.raises(ValueError, match="rationale must not be blank"):
        _validate_code_scope(
            ({**scope, "rationale": "  "},),
            graph=_graph(),
            document_change_refs=("change-1",),
        )
    with pytest.raises(ValueError, match="ui_impact must be boolean"):
        _validate_code_scope(
            ({**scope, "ui_impact": "yes"},),
            graph=_graph(),
            document_change_refs=("change-1",),
        )
