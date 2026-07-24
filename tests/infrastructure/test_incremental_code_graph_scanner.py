import json
from pathlib import Path
from typing import Any, cast

import pytest

from operamind.infrastructure.code_graph import (
    CodeGraphScanner,
    GitPathChange,
    IncrementalCodeGraphScanner,
    WorkspaceScanner,
)

ROOT = Path(__file__).parents[2]


def _profile() -> dict[str, Any]:
    value = json.loads(
        (ROOT / "profiles/code-framework-profile.example.json").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _polyglot_profile() -> dict[str, Any]:
    value = json.loads(
        (ROOT / "profiles/polyglot-code-framework-profile.example.json").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _write_project(root: Path) -> None:
    main = root / "src/main/java/example"
    test = root / "src/test/java/example"
    main.mkdir(parents=True)
    test.mkdir(parents=True)
    (main / "ExpenseService.java").write_text(
        """package example;
import org.springframework.stereotype.Service;
@Service public class ExpenseService {
  public String search(String status) { return status; }
}
""",
        encoding="utf-8",
    )
    (main / "UnrelatedService.java").write_text(
        """package example;
public class UnrelatedService { public int count() { return 1; } }
""",
        encoding="utf-8",
    )
    (test / "ExpenseServiceTest.java").write_text(
        """package example;
import org.junit.jupiter.api.Test;
class ExpenseServiceTest {
  private final ExpenseService service = new ExpenseService();
  @Test void searches() { service.search("all"); }
}
""",
        encoding="utf-8",
    )


@pytest.mark.parametrize("change_kind", ["body", "signature", "delete", "rename"])
def test_incremental_scan_matches_full_graph_and_limits_parser_scope(
    tmp_path: Path, change_kind: str
) -> None:
    _write_project(tmp_path)
    profile = _profile()
    workspace = WorkspaceScanner()
    scanner = CodeGraphScanner()
    incremental = IncrementalCodeGraphScanner()
    roots = ("src/main", "src/test")
    initial_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=tuple(cast(list[str], profile["excluded_globs"])),
        languages=tuple(cast(list[str], profile["languages"])),
    )
    base = scanner.scan(
        code_graph_snapshot_id="graph-base",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        files=initial_files,
    ).artifact
    service_path = "src/main/java/example/ExpenseService.java"
    service = tmp_path / service_path
    if change_kind == "body":
        service.write_text(service.read_text().replace("return status", "return status.trim()"))
        changes = (GitPathChange("M", (service_path,)),)
    elif change_kind == "signature":
        service.write_text(service.read_text().replace("search", "find"))
        changes = (GitPathChange("M", (service_path,)),)
    elif change_kind == "delete":
        service.unlink()
        changes = (GitPathChange("D", (service_path,)),)
    else:
        renamed_path = "src/main/java/example/RenamedExpenseService.java"
        renamed = tmp_path / renamed_path
        service.rename(renamed)
        renamed.write_text(renamed.read_text().replace("ExpenseService", "RenamedExpenseService"))
        changes = (GitPathChange("R100", (service_path, renamed_path)),)

    current_paths = frozenset(
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()
    )
    changed_current = frozenset(
        path for change in changes for path in change.paths if path in current_paths
    )
    changed_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=tuple(cast(list[str], profile["excluded_globs"])),
        languages=tuple(cast(list[str], profile["languages"])),
        allowed_paths=changed_current,
    )
    plan = incremental.plan(
        previous_artifact=base,
        changes=changes,
        changed_files=changed_files,
        profile=profile,
        current_tracked_paths=current_paths,
    )
    affected_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=tuple(cast(list[str], profile["excluded_globs"])),
        languages=tuple(cast(list[str], profile["languages"])),
        allowed_paths=frozenset(plan.affected_paths),
    )
    actual = incremental.scan(
        code_graph_snapshot_id="graph-incremental",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=affected_files,
        current_tracked_paths=current_paths,
    ).artifact
    current_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=tuple(cast(list[str], profile["excluded_globs"])),
        languages=tuple(cast(list[str], profile["languages"])),
    )
    expected = scanner.scan(
        code_graph_snapshot_id="graph-full",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        files=current_files,
    ).artifact

    assert actual["files"] == expected["files"]
    assert actual["edges"] == expected["edges"]
    assert actual["scanned_file_count"] < len(current_files)
    assert actual["reused_file_count"] >= 1
    assert "src/main/java/example/UnrelatedService.java" not in plan.affected_paths
    if change_kind == "body":
        assert plan.affected_paths == (service_path,)
    else:
        assert "src/test/java/example/ExpenseServiceTest.java" in plan.affected_paths


def test_incremental_framework_scan_reconciles_the_approved_tracked_set(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)
    profile = _profile()
    profile["anchor_extractors"].append("spring_data_access")
    workspace = WorkspaceScanner()
    scanner = CodeGraphScanner()
    incremental = IncrementalCodeGraphScanner()
    roots = ("src/main", "src/test")
    exclusions = tuple(cast(list[str], profile["excluded_globs"]))
    languages = tuple(cast(list[str], profile["languages"]))
    initial_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    base = scanner.scan(
        code_graph_snapshot_id="graph-base",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        files=initial_files,
    ).artifact
    service_path = "src/main/java/example/ExpenseService.java"
    service = tmp_path / service_path
    service.write_text(
        service.read_text(encoding="utf-8").replace("return status", "return status.trim()"),
        encoding="utf-8",
    )
    graph_paths = frozenset(file.path for file in initial_files)
    current_paths = graph_paths | {"README.md"}
    changed_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset({service_path}),
    )
    plan = incremental.plan(
        previous_artifact=base,
        changes=(GitPathChange("M", (service_path,)),),
        changed_files=changed_files,
        profile=profile,
        current_tracked_paths=current_paths,
    )
    affected_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset(plan.affected_paths),
    )
    actual = incremental.scan(
        code_graph_snapshot_id="graph-incremental",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=affected_files,
        current_tracked_paths=current_paths,
    ).artifact
    expected = scanner.scan(
        code_graph_snapshot_id="graph-full",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        files=affected_files,
    ).artifact

    assert plan.affected_paths == tuple(sorted(graph_paths))
    assert actual["files"] == expected["files"]
    assert actual["edges"] == expected["edges"]
    assert actual["scanned_file_count"] == len(graph_paths)
    assert actual["reused_file_count"] == 0


def test_incremental_scan_reuses_declared_return_types_for_new_call_chains(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example"
    source.mkdir(parents=True)
    (source / "CustomerRecord.java").write_text(
        """package example;
public class CustomerRecord {
  private String state;
  public String getState() { return state; }
}
""",
        encoding="utf-8",
    )
    (source / "CustomerService.java").write_text(
        """package example;
import org.springframework.stereotype.Service;
@Service public class CustomerService {
  public CustomerRecord load() { return new CustomerRecord(); }
}
""",
        encoding="utf-8",
    )
    controller_path = "src/main/java/example/CustomerController.java"
    controller = tmp_path / controller_path
    controller.write_text(
        """package example;
public class CustomerController {
  private CustomerService service;
  public String state() { return "pending"; }
}
""",
        encoding="utf-8",
    )
    profile = _profile()
    workspace = WorkspaceScanner()
    scanner = CodeGraphScanner()
    incremental = IncrementalCodeGraphScanner()
    roots = ("src/main",)
    exclusions = tuple(cast(list[str], profile["excluded_globs"]))
    languages = tuple(cast(list[str], profile["languages"]))
    initial_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    base = scanner.scan(
        code_graph_snapshot_id="graph-base-return-type",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        files=initial_files,
    ).artifact
    controller.write_text(
        controller.read_text(encoding="utf-8").replace(
            'return "pending"', "return service.load().getState()"
        ),
        encoding="utf-8",
    )
    current_paths = frozenset(file.path for file in initial_files)
    changed_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset({controller_path}),
    )
    plan = incremental.plan(
        previous_artifact=base,
        changes=(GitPathChange("M", (controller_path,)),),
        changed_files=changed_files,
        profile=profile,
        current_tracked_paths=current_paths,
    )
    affected_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset(plan.affected_paths),
    )
    actual = incremental.scan(
        code_graph_snapshot_id="graph-incremental-return-type",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=affected_files,
        current_tracked_paths=current_paths,
    ).artifact
    current_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    expected = scanner.scan(
        code_graph_snapshot_id="graph-full-return-type",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        files=current_files,
    ).artifact

    assert plan.affected_paths == (controller_path,)
    assert actual["files"] == expected["files"]
    assert actual["edges"] == expected["edges"]
    assert not any(
        edge["resolution_status"] == "unresolved"
        for edge in cast(list[dict[str, Any]], actual["edges"])
    )


def test_incremental_scan_resolves_existing_import_when_new_type_is_added(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)
    consumer_path = "src/main/java/example/FutureConsumer.java"
    (tmp_path / consumer_path).write_text(
        """package example;
import example.FutureService;
public class FutureConsumer {
  public String consume() { return new FutureService().run(); }
}
""",
        encoding="utf-8",
    )
    profile = _profile()
    workspace = WorkspaceScanner()
    scanner = CodeGraphScanner()
    incremental = IncrementalCodeGraphScanner()
    roots = ("src/main", "src/test")
    exclusions = tuple(cast(list[str], profile["excluded_globs"]))
    languages = tuple(cast(list[str], profile["languages"]))
    initial_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    base = scanner.scan(
        code_graph_snapshot_id="graph-base",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        files=initial_files,
    ).artifact
    new_path = "src/main/java/example/FutureService.java"
    (tmp_path / new_path).write_text(
        """package example;
public class FutureService { public String run() { return "ready"; } }
""",
        encoding="utf-8",
    )
    current_paths = frozenset(
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()
    )
    changes = (GitPathChange("A", (new_path,)),)
    changed_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset({new_path}),
    )
    plan = incremental.plan(
        previous_artifact=base,
        changes=changes,
        changed_files=changed_files,
        profile=profile,
        current_tracked_paths=current_paths,
    )
    assert plan.affected_paths == (consumer_path, new_path)
    affected_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset(plan.affected_paths),
    )
    actual = incremental.scan(
        code_graph_snapshot_id="graph-incremental",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=affected_files,
        current_tracked_paths=current_paths,
    ).artifact
    expected = scanner.scan(
        code_graph_snapshot_id="graph-full",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        files=workspace.discover(
            workspace_root=tmp_path,
            scan_roots=roots,
            excluded_globs=exclusions,
            languages=languages,
        ),
    ).artifact

    assert actual["files"] == expected["files"]
    assert actual["edges"] == expected["edges"]
    consumer_edges = [
        edge
        for edge in cast(list[dict[str, Any]], actual["edges"])
        if cast(dict[str, Any], edge["source_location"])["path"] == consumer_path
    ]
    assert any(
        edge["edge_type"] == "calls" and edge["resolution_status"] == "resolved"
        for edge in consumer_edges
    )


def test_incremental_scan_with_same_revision_reuses_every_file(tmp_path: Path) -> None:
    _write_project(tmp_path)
    profile = _profile()
    workspace = WorkspaceScanner()
    scanner = CodeGraphScanner()
    incremental = IncrementalCodeGraphScanner()
    roots = ("src/main", "src/test")
    exclusions = tuple(cast(list[str], profile["excluded_globs"]))
    languages = tuple(cast(list[str], profile["languages"]))
    files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    base = scanner.scan(
        code_graph_snapshot_id="graph-base",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        files=files,
    ).artifact
    current_paths = frozenset(file.path for file in files)
    plan = incremental.plan(
        previous_artifact=base,
        changes=(),
        changed_files=(),
        profile=profile,
        current_tracked_paths=current_paths,
    )
    actual = incremental.scan(
        code_graph_snapshot_id="graph-incremental",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=(),
        current_tracked_paths=current_paths,
    ).artifact

    assert plan.affected_paths == ()
    assert actual["files"] == base["files"]
    assert actual["edges"] == base["edges"]
    assert actual["scanned_file_count"] == 0
    assert actual["reused_file_count"] == len(files)


def test_incremental_scan_recomputes_global_diagnostics_after_last_file_deleted(
    tmp_path: Path,
) -> None:
    source_path = "src/main/java/example/OnlyService.java"
    source = tmp_path / source_path
    source.parent.mkdir(parents=True)
    (tmp_path / "src/test").mkdir(parents=True)
    source.write_text(
        """package example;
import org.springframework.stereotype.Service;
@Service public class OnlyService {}
""",
        encoding="utf-8",
    )
    profile = _profile()
    workspace = WorkspaceScanner()
    scanner = CodeGraphScanner()
    incremental = IncrementalCodeGraphScanner()
    roots = ("src/main", "src/test")
    exclusions = tuple(cast(list[str], profile["excluded_globs"]))
    languages = tuple(cast(list[str], profile["languages"]))
    initial_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    base = scanner.scan(
        code_graph_snapshot_id="graph-base",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        files=initial_files,
    ).artifact
    source.unlink()
    plan = incremental.plan(
        previous_artifact=base,
        changes=(GitPathChange("D", (source_path,)),),
        changed_files=(),
        profile=profile,
        current_tracked_paths=frozenset(),
    )
    actual = incremental.scan(
        code_graph_snapshot_id="graph-incremental",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=(),
        current_tracked_paths=frozenset(),
    ).artifact
    expected = scanner.scan(
        code_graph_snapshot_id="graph-full",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        files=(),
    ).artifact

    assert actual["files"] == expected["files"]
    assert actual["edges"] == expected["edges"]
    assert actual["scan_status"] == expected["scan_status"]
    assert actual["diagnostics"] == expected["diagnostics"]


def test_incremental_python_adapter_matches_full_graph_after_call_change(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/python/app"
    tests = tmp_path / "tests/python"
    source.mkdir(parents=True)
    tests.mkdir(parents=True)
    (source / "repo.py").write_text(
        "def normalize_original(value: str) -> str:\n"
        "    return value.strip()\n\n"
        "def normalize_updated(value: str) -> str:\n"
        "    return value.casefold()\n",
        encoding="utf-8",
    )
    service_path = "src/python/app/service.py"
    service = tmp_path / service_path
    service.write_text(
        "from app.repo import normalize_original\n\n"
        "def search_employee(value: str) -> str:\n"
        "    return normalize_original(value)\n",
        encoding="utf-8",
    )
    (tests / "test_service.py").write_text(
        "from app.service import search_employee\n\n"
        "def test_search_employee() -> None:\n"
        "    assert search_employee(' A ') == 'A'\n",
        encoding="utf-8",
    )
    profile = _polyglot_profile()
    workspace = WorkspaceScanner()
    scanner = CodeGraphScanner()
    incremental = IncrementalCodeGraphScanner()
    roots = ("src", "tests")
    exclusions = tuple(cast(list[str], profile["excluded_globs"]))
    languages = tuple(cast(list[str], profile["languages"]))
    initial_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    base = scanner.scan(
        code_graph_snapshot_id="graph-polyglot-base",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        files=initial_files,
    ).artifact
    service.write_text(
        "from app.repo import normalize_updated\n\n"
        "def search_employee(value: str) -> str:\n"
        "    return normalize_updated(value)\n",
        encoding="utf-8",
    )
    current_paths = frozenset(file.path for file in initial_files)
    changed_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset({service_path}),
    )
    plan = incremental.plan(
        previous_artifact=base,
        changes=(GitPathChange("M", (service_path,)),),
        changed_files=changed_files,
        profile=profile,
        current_tracked_paths=current_paths,
    )
    affected_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset(plan.affected_paths),
    )
    actual = incremental.scan(
        code_graph_snapshot_id="graph-polyglot-incremental",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=affected_files,
        current_tracked_paths=current_paths,
    ).artifact
    expected = scanner.scan(
        code_graph_snapshot_id="graph-polyglot-full",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        files=workspace.discover(
            workspace_root=tmp_path,
            scan_roots=roots,
            excluded_globs=exclusions,
            languages=languages,
        ),
    ).artifact

    assert plan.affected_paths == tuple(sorted(current_paths))
    assert actual["files"] == expected["files"]
    assert actual["edges"] == expected["edges"]
    assert actual["diagnostics"] == expected["diagnostics"] == []


@pytest.mark.parametrize(
    (
        "language",
        "extension",
        "repository_content",
        "service_before",
        "service_after",
        "test_content",
    ),
    [
        pytest.param(
            "javascript",
            "js",
            (
                "export function normalizeOriginal(value) { return value.trim(); }\n"
                "export function normalizeUpdated(value) { return value.toLowerCase(); }\n"
            ),
            (
                "import { normalizeOriginal } from './repo.js';\n"
                "export function searchEmployee(value) { return normalizeOriginal(value); }\n"
            ),
            (
                "import { normalizeUpdated } from './repo.js';\n"
                "export function searchEmployee(value) { return normalizeUpdated(value); }\n"
            ),
            (
                "import { searchEmployee } from '../../src/javascript/app/service.js';\n"
                "export function verifyEmployee() { return searchEmployee(' A '); }\n"
            ),
            id="javascript",
        ),
        pytest.param(
            "typescript",
            "ts",
            (
                "export function normalizeOriginal(value: string): string "
                "{ return value.trim(); }\n"
                "export function normalizeUpdated(value: string): string "
                "{ return value.toLowerCase(); }\n"
            ),
            (
                "import { normalizeOriginal } from './repo';\n"
                "export function searchEmployee(value: string): string "
                "{ return normalizeOriginal(value); }\n"
            ),
            (
                "import { normalizeUpdated } from './repo';\n"
                "export function searchEmployee(value: string): string "
                "{ return normalizeUpdated(value); }\n"
            ),
            (
                "import { searchEmployee } from '../../src/typescript/app/service';\n"
                "export function verifyEmployee(): string { return searchEmployee(' A '); }\n"
            ),
            id="typescript",
        ),
        pytest.param(
            "kotlin",
            "kt",
            (
                "package demo\n"
                "fun normalizeOriginal(value: String): String = value.trim()\n"
                "fun normalizeUpdated(value: String): String = value.lowercase()\n"
            ),
            (
                "package demo\n"
                "fun searchEmployee(value: String): String = normalizeOriginal(value)\n"
            ),
            ("package demo\nfun searchEmployee(value: String): String = normalizeUpdated(value)\n"),
            ('package demo\nfun verifyEmployee(): String = searchEmployee(" A ")\n'),
            id="kotlin",
        ),
    ],
)
def test_incremental_semantic_adapters_match_full_graph_after_call_change(
    tmp_path: Path,
    language: str,
    extension: str,
    repository_content: str,
    service_before: str,
    service_after: str,
    test_content: str,
) -> None:
    source = tmp_path / "src" / language / "app"
    tests = tmp_path / "tests" / language
    source.mkdir(parents=True)
    tests.mkdir(parents=True)
    (source / f"repo.{extension}").write_text(repository_content, encoding="utf-8")
    service_path = f"src/{language}/app/service.{extension}"
    service = tmp_path / service_path
    service.write_text(service_before, encoding="utf-8")
    (tests / f"service.test.{extension}").write_text(test_content, encoding="utf-8")
    profile = _polyglot_profile()
    workspace = WorkspaceScanner()
    scanner = CodeGraphScanner()
    incremental = IncrementalCodeGraphScanner()
    roots = ("src", "tests")
    exclusions = tuple(cast(list[str], profile["excluded_globs"]))
    languages = tuple(cast(list[str], profile["languages"]))
    initial_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    base = scanner.scan(
        code_graph_snapshot_id=f"graph-{language}-base",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        files=initial_files,
    ).artifact
    service.write_text(service_after, encoding="utf-8")
    current_paths = frozenset(file.path for file in initial_files)
    changed_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset({service_path}),
    )
    plan = incremental.plan(
        previous_artifact=base,
        changes=(GitPathChange("M", (service_path,)),),
        changed_files=changed_files,
        profile=profile,
        current_tracked_paths=current_paths,
    )
    affected_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset(plan.affected_paths),
    )
    actual = incremental.scan(
        code_graph_snapshot_id=f"graph-{language}-incremental",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=affected_files,
        current_tracked_paths=current_paths,
    ).artifact
    expected = scanner.scan(
        code_graph_snapshot_id=f"graph-{language}-full",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        files=workspace.discover(
            workspace_root=tmp_path,
            scan_roots=roots,
            excluded_globs=exclusions,
            languages=languages,
        ),
    ).artifact

    assert plan.affected_paths == tuple(sorted(current_paths))
    assert actual["files"] == expected["files"]
    assert actual["edges"] == expected["edges"]
    assert actual["diagnostics"] == expected["diagnostics"] == []


def test_incremental_body_change_reparses_one_file_in_large_fixture(tmp_path: Path) -> None:
    _write_project(tmp_path)
    main = tmp_path / "src/main/java/example"
    for index in range(100):
        (main / f"Catalog{index}.java").write_text(
            f"package example; public class Catalog{index} "
            f"{{ public int value() {{ return {index}; }} }}\n",
            encoding="utf-8",
        )
    profile = _profile()
    workspace = WorkspaceScanner()
    scanner = CodeGraphScanner()
    incremental = IncrementalCodeGraphScanner()
    roots = ("src/main", "src/test")
    exclusions = tuple(cast(list[str], profile["excluded_globs"]))
    languages = tuple(cast(list[str], profile["languages"]))
    initial_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    base = scanner.scan(
        code_graph_snapshot_id="graph-base",
        project_id="project",
        repository_id="repository",
        repository_revision="a" * 40,
        scan_roots=roots,
        profile=profile,
        files=initial_files,
    ).artifact
    service_path = "src/main/java/example/ExpenseService.java"
    service = tmp_path / service_path
    service.write_text(
        service.read_text(encoding="utf-8").replace("return status", "return status.trim()"),
        encoding="utf-8",
    )
    current_paths = frozenset(file.path for file in initial_files)
    changed_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset({service_path}),
    )
    plan = incremental.plan(
        previous_artifact=base,
        changes=(GitPathChange("M", (service_path,)),),
        changed_files=changed_files,
        profile=profile,
        current_tracked_paths=current_paths,
    )
    actual = incremental.scan(
        code_graph_snapshot_id="graph-incremental",
        project_id="project",
        repository_id="repository",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=changed_files,
        current_tracked_paths=current_paths,
    ).artifact

    assert plan.affected_paths == (service_path,)
    assert actual["scanned_file_count"] == 1
    assert actual["reused_file_count"] == 102
    assert len(cast(list[object], actual["files"])) == 103
