import json
from pathlib import Path
from typing import Any, cast

from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import CodeGraphScanner, WorkspaceScanner

ROOT = Path(__file__).parents[2]


def load_profile() -> dict[str, Any]:
    value: object = json.loads(
        (ROOT / "profiles/code-framework-profile.example.json").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def load_polyglot_profile() -> dict[str, Any]:
    value: object = json.loads(
        (ROOT / "profiles/polyglot-code-framework-profile.example.json").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def test_polyglot_tree_sitter_adapters_build_symbols_imports_calls_and_tests(
    tmp_path: Path,
) -> None:
    sources = {
        "src/javascript/repo.js": ("export const loadExpenseJs = (id) => id;\n"),
        "src/javascript/service.js": (
            "import { loadExpenseJs } from './repo.js';\n"
            "export function findExpenseJs(id) { return loadExpenseJs(id); }\n"
        ),
        "tests/javascript/service.test.js": (
            "import { findExpenseJs } from '../../src/javascript/service.js';\n"
            "export function verifyExpenseJs() { return findExpenseJs(1); }\n"
        ),
        "src/typescript/repo.ts": (
            "export interface OrderPort {}\n"
            "export const loadOrderTs = (id: number): number => id;\n"
        ),
        "src/typescript/service.ts": (
            "import { OrderPort, loadOrderTs } from './repo';\n"
            "export class OrderServiceTs implements OrderPort {\n"
            "  findOrderTs(id: number): number { return loadOrderTs(id); }\n"
            "}\n"
        ),
        "src/typescript/badge.tsx": (
            "export const ExpenseBadge = (): JSX.Element => <span>ok</span>;\n"
        ),
        "tests/typescript/service.spec.ts": (
            "import { OrderServiceTs } from '../../src/typescript/service';\n"
            "export function verifyOrderTs() { return new OrderServiceTs().findOrderTs(1); }\n"
        ),
        "src/python/app/repo.py": (
            "class EmployeePort:\n"
            "    pass\n\n"
            "def load_employee_py(employee_id: int) -> int:\n"
            "    return employee_id\n"
        ),
        "src/python/app/service.py": (
            "from app.repo import EmployeePort, load_employee_py\n\n"
            "class EmployeeServicePy(EmployeePort):\n"
            "    def find_employee_py(self, employee_id: int) -> int:\n"
            "        return load_employee_py(employee_id)\n"
        ),
        "tests/python/test_service.py": (
            "from app.service import EmployeeServicePy\n\n"
            "def verify_employee_py() -> int:\n"
            "    return EmployeeServicePy().find_employee_py(1)\n"
        ),
        "src/kotlin/demo/Repo.kt": (
            "package demo\ninterface InvoicePort\nfun loadInvoiceKt(id: Long): Long = id\n"
        ),
        "src/kotlin/demo/Service.kt": (
            "package demo\n"
            "class InvoiceServiceKt : InvoicePort {\n"
            "    fun findInvoiceKt(id: Long): Long {\n"
            "        return loadInvoiceKt(id)\n"
            "    }\n"
            "}\n"
        ),
        "tests/kotlin/ServiceTest.kt": (
            "package demo\nfun verifyInvoiceKt(): Long = InvoiceServiceKt().findInvoiceKt(1)\n"
        ),
    }
    for relative_path, content in sources.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    profile = load_polyglot_profile()
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src", "tests"),
        excluded_globs=tuple(cast(list[str], profile["excluded_globs"])),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-polyglot",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src", "tests"),
        profile=profile,
        files=files,
    )

    ContractCatalog.load(ROOT / "contracts").validate_artifact(result.artifact)
    assert result.diagnostics == ()
    assert result.artifact["scan_status"] == "complete"
    artifact_files = cast(list[dict[str, Any]], result.artifact["files"])
    assert {str(value["language"]) for value in artifact_files} == {
        "javascript",
        "typescript",
        "python",
        "kotlin",
    }
    assert all(cast(list[object], value["symbols"]) for value in artifact_files)
    edges = cast(list[dict[str, Any]], result.artifact["edges"])
    semantic_edges = [
        edge
        for edge in edges
        if edge["extractor"]
        in {
            "javascript_symbol",
            "typescript_symbol",
            "python_symbol",
            "kotlin_symbol",
        }
    ]
    assert {str(edge["extractor"]) for edge in semantic_edges} == {
        "javascript_symbol",
        "typescript_symbol",
        "python_symbol",
        "kotlin_symbol",
    }
    assert (
        sum(
            edge["edge_type"] == "imports" and edge["resolution_status"] == "resolved"
            for edge in semantic_edges
        )
        >= 5
    )
    assert (
        sum(
            edge["edge_type"] == "calls" and edge["resolution_status"] == "resolved"
            for edge in semantic_edges
        )
        >= 4
    )
    assert (
        sum(
            edge["edge_type"] == "tests" and edge["resolution_status"] == "resolved"
            for edge in semantic_edges
        )
        >= 4
    )
    assert (
        sum(
            edge["edge_type"] == "implements" and edge["resolution_status"] == "resolved"
            for edge in semantic_edges
        )
        >= 3
    )
    assert any(
        symbol["name"] == "ExpenseBadge"
        for value in artifact_files
        if value["path"] == "src/typescript/badge.tsx"
        for symbol in cast(list[dict[str, Any]], value["symbols"])
    )


def test_polyglot_adapters_keep_external_and_ambiguous_calls_explicit(
    tmp_path: Path,
) -> None:
    sources = {
        "src/python/first.py": "def normalize(value: int) -> int:\n    return value\n",
        "src/python/second.py": "def normalize(value: int) -> int:\n    return value\n",
        "src/python/caller.py": (
            "from vendor.client import send_external\n\n"
            "def run(value: int) -> int:\n"
            "    print(value)\n"
            "    send_external(value)\n"
            "    return normalize(value)\n"
        ),
    }
    for relative_path, content in sources.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    profile = load_polyglot_profile()
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src",),
        excluded_globs=(),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-polyglot-resolution-status",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src",),
        profile=profile,
        files=files,
    )

    assert result.artifact["scan_status"] == "complete"
    calls = [
        edge
        for edge in cast(list[dict[str, Any]], result.artifact["edges"])
        if edge["edge_type"] == "calls" and edge["extractor"] == "python_symbol"
    ]
    assert any(
        edge["to_ref"] == "external:call:print/1" and edge["resolution_status"] == "external"
        for edge in calls
    )
    assert any(
        edge["to_ref"] == "external:call:send_external/1"
        and edge["resolution_status"] == "external"
        for edge in calls
    )
    assert any(
        edge["to_ref"] == "unresolved:call:normalize/1"
        and edge["resolution_status"] == "unresolved"
        for edge in calls
    )


def test_polyglot_adapters_do_not_resolve_calls_across_incompatible_languages(
    tmp_path: Path,
) -> None:
    sources = {
        "src/javascript/caller.js": (
            "export function runJs(value) { return pythonOnly(value); }\n"
        ),
        "src/python/target.py": "def pythonOnly(value: int) -> int:\n    return value\n",
    }
    for relative_path, content in sources.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    profile = load_polyglot_profile()
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src",),
        excluded_globs=(),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-polyglot-language-boundary",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src",),
        profile=profile,
        files=files,
    )

    calls = [
        edge
        for edge in cast(list[dict[str, Any]], result.artifact["edges"])
        if edge["edge_type"] == "calls" and edge["extractor"] == "javascript_symbol"
    ]
    assert any(
        edge["to_ref"] == "unresolved:call:pythonOnly/1"
        and edge["resolution_status"] == "unresolved"
        for edge in calls
    )


def test_polyglot_adapters_report_each_language_parse_error(
    tmp_path: Path,
) -> None:
    sources = {
        "src/javascript/broken.js": "export function broken( {\n",
        "src/typescript/broken.ts": "export function broken(value: number {\n",
        "src/python/broken.py": "def broken(:\n",
        "src/kotlin/Broken.kt": "fun broken( {\n",
    }
    for relative_path, content in sources.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    profile = load_polyglot_profile()
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src",),
        excluded_globs=(),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-polyglot-broken",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src",),
        profile=profile,
        files=files,
    )

    assert result.artifact["scan_status"] == "truncated"
    assert set(result.diagnostics) == {
        "tree_sitter_parse_error:javascript:src/javascript/broken.js",
        "tree_sitter_parse_error:kotlin:src/kotlin/Broken.kt",
        "tree_sitter_parse_error:python:src/python/broken.py",
        "tree_sitter_parse_error:typescript:src/typescript/broken.ts",
    }


def test_tree_sitter_scanner_builds_resolved_java_test_and_lexical_edges(
    tmp_path: Path,
) -> None:
    main_java = tmp_path / "src/main/java/example"
    test_java = tmp_path / "src/test/java/example"
    resources = tmp_path / "src/main/resources"
    main_java.mkdir(parents=True)
    test_java.mkdir(parents=True)
    resources.mkdir(parents=True)
    (main_java / "SearchService.java").write_text(
        """package example;
public interface SearchService { String search(String status); }
""",
        encoding="utf-8",
    )
    (main_java / "ExpenseService.java").write_text(
        """package example;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
@RequestMapping("/api")
public class ExpenseService implements SearchService {
  @GetMapping("/expenses")
  public String search(String status) { return status; }
}
""",
        encoding="utf-8",
    )
    (test_java / "ExpenseServiceTest.java").write_text(
        """package example;
import org.junit.jupiter.api.Test;
class ExpenseServiceTest {
  private final ExpenseService service = new ExpenseService();
  @Test void returnsAll() { service.search(null); }
}
""",
        encoding="utf-8",
    )
    (resources / "application.properties").write_text(
        "feature.expense-search=true\n", encoding="utf-8"
    )
    (resources / "schema.sql").write_text(
        "CREATE TABLE expenses(id bigint);\nSELECT * FROM expenses;\nUPDATE expenses SET id=1;\n",
        encoding="utf-8",
    )
    profile = load_profile()
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main", "src/test"),
        excluded_globs=tuple(cast(list[str], profile["excluded_globs"])),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-1",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src/main", "src/test"),
        profile=profile,
        files=files,
    )

    ContractCatalog.load(ROOT / "contracts").validate_artifact(result.artifact)
    assert result.diagnostics == ()
    assert "org.springframework.web.bind.annotation" in result.framework_markers_found
    assert result.artifact["scan_status"] == "complete"
    artifact_files = cast(list[dict[str, Any]], result.artifact["files"])
    assert len(artifact_files) == 5
    symbols = [
        symbol
        for artifact_file in artifact_files
        for symbol in cast(list[dict[str, Any]], artifact_file["symbols"])
    ]
    assert {str(symbol["symbol_type"]) for symbol in symbols} >= {
        "class",
        "config_key",
        "db_table",
        "field",
        "interface",
        "method",
    }
    edges = cast(list[dict[str, Any]], result.artifact["edges"])
    edge_types = {str(edge["edge_type"]) for edge in edges}
    assert edge_types >= {
        "calls",
        "contains",
        "exposes",
        "implements",
        "imports",
        "reads",
        "tests",
        "writes",
    }
    endpoint_edges = [edge for edge in edges if edge["edge_type"] == "exposes"]
    assert [edge["to_ref"] for edge in endpoint_edges] == ["http:GET:/api/expenses"]
    test_edges = [edge for edge in edges if edge["edge_type"] == "tests"]
    assert len(test_edges) == 1
    assert test_edges[0]["resolution_status"] == "resolved"


def test_framework_extractors_link_config_ui_routes_and_spring_data_access(
    tmp_path: Path,
) -> None:
    java_root = tmp_path / "src/main/java/example"
    resources = tmp_path / "src/main/resources"
    webapp = tmp_path / "src/main/webapp"
    java_root.mkdir(parents=True)
    resources.mkdir(parents=True)
    webapp.mkdir(parents=True)
    (java_root / "Expense.java").write_text(
        """package example;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
@Entity @Table(name = "expenses")
public class Expense { Long id; }
""",
        encoding="utf-8",
    )
    (java_root / "ExpenseRepository.java").write_text(
        """package example;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
public interface ExpenseRepository extends JpaRepository<Expense, Long> {
  Optional<Expense> findByStatus(String status);
}
""",
        encoding="utf-8",
    )
    (java_root / "ExpenseService.java").write_text(
        """package example;
import java.util.Optional;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Service;
@Service public class ExpenseService {
  private final ExpenseRepository repository;
  private final Environment environment;
  @Value("${feature.expense-search:false}") private boolean enabled;
  ExpenseService(ExpenseRepository repository, Environment environment) {
    this.repository = repository; this.environment = environment;
  }
  Optional<Expense> load(Long id) {
    environment.getProperty("feature.page-size");
    return repository.findById(id);
  }
  Expense store(Expense expense) { return repository.save(expense); }
}
""",
        encoding="utf-8",
    )
    (java_root / "ExpenseController.java").write_text(
        """package example;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
@RestController @RequestMapping("/expense")
public class ExpenseController {
  @GetMapping("/page") String page() { return "expense"; }
  @GetMapping("/api/{id}") String detail() { return "detail"; }
}
""",
        encoding="utf-8",
    )
    (resources / "application.properties").write_text(
        "feature.expense-search=true\nfeature.page-size=20\n", encoding="utf-8"
    )
    (resources / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS expenses(id bigint);\n", encoding="utf-8"
    )
    (resources / "data.sql").write_text("INSERT INTO expenses(id) VALUES (1);\n", encoding="utf-8")
    (webapp / "expense.jsp").write_text(
        """<a href="/expense/page?view=list">一覧</a>
<script>$.ajax({url: '/expense/api/' + id});</script>
""",
        encoding="utf-8",
    )
    profile = load_profile()
    profile["languages"].append("javascript")
    profile["anchor_extractors"].extend(
        ["spring_config_binding", "spring_data_access", "web_ui_route"]
    )
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main",),
        excluded_globs=(),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-framework",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src/main",),
        profile=profile,
        files=files,
    )

    ContractCatalog.load(ROOT / "contracts").validate_artifact(result.artifact)
    assert result.diagnostics == ()
    artifact_files = cast(list[dict[str, Any]], result.artifact["files"])
    symbols = [
        symbol
        for artifact_file in artifact_files
        for symbol in cast(list[dict[str, Any]], artifact_file["symbols"])
    ]
    assert any(symbol["signature"] == "table:expenses" for symbol in symbols)
    assert not any(symbol["signature"] == "table:if" for symbol in symbols)
    assert {symbol["name"] for symbol in symbols if symbol["symbol_type"] == "ui_route"} == {
        "/expense/api/{*}",
        "/expense/page?view=list",
    }
    edges = cast(list[dict[str, Any]], result.artifact["edges"])
    framework_edges = [
        edge
        for edge in edges
        if edge["extractor"] in {"spring_config_binding", "spring_data_access", "web_ui_route"}
    ]
    assert all(edge["resolution_status"] != "unresolved" for edge in framework_edges)
    assert sum(edge["edge_type"] == "maps_to" for edge in framework_edges) == 2
    assert sum(edge["edge_type"] == "reads" for edge in framework_edges) == 4
    assert sum(edge["edge_type"] == "writes" for edge in framework_edges) == 1
    assert sum(edge["edge_type"] == "calls" for edge in framework_edges) == 2
    assert not any(
        edge["resolution_status"] == "unresolved"
        and str(edge["to_ref"]).startswith(
            ("unresolved:call:repository.findById", "unresolved:call:repository.save")
        )
        for edge in edges
    )


def test_framework_extractors_keep_ambiguous_targets_unresolved(tmp_path: Path) -> None:
    java_root = tmp_path / "src/main/java/example"
    resources = tmp_path / "src/main/resources"
    webapp = tmp_path / "src/main/webapp"
    java_root.mkdir(parents=True)
    resources.mkdir(parents=True)
    webapp.mkdir(parents=True)
    (java_root / "Expense.java").write_text(
        """package example;
import jakarta.persistence.Table;
@Table(name = "expenses") public class Expense {}
""",
        encoding="utf-8",
    )
    (java_root / "ExpenseRepository.java").write_text(
        """package example;
import org.springframework.data.jpa.repository.JpaRepository;
public interface ExpenseRepository extends JpaRepository<Expense, Long> {}
""",
        encoding="utf-8",
    )
    (java_root / "ConfigConsumer.java").write_text(
        """package example;
import org.springframework.beans.factory.annotation.Value;
public class ConfigConsumer {
  @Value("${feature.Flag}") private boolean enabled;
}
""",
        encoding="utf-8",
    )
    for name in ("FirstController", "SecondController"):
        (java_root / f"{name}.java").write_text(
            f"""package example;
import org.springframework.web.bind.annotation.GetMapping;
public class {name} {{ @GetMapping("/expense/page") String page() {{ return "x"; }} }}
""",
            encoding="utf-8",
        )
    (resources / "application.properties").write_text("feature.Flag=true\n", encoding="utf-8")
    (resources / "override.properties").write_text("feature.Flag=false\n", encoding="utf-8")
    (resources / "schema.sql").write_text("CREATE TABLE expenses(id bigint);\n", encoding="utf-8")
    (resources / "duplicate.sql").write_text(
        "CREATE TABLE expenses(id bigint);\n", encoding="utf-8"
    )
    (webapp / "expense.jsp").write_text('<a href="/expense/page">一覧</a>\n', encoding="utf-8")
    profile = load_profile()
    profile["anchor_extractors"].extend(
        ["spring_config_binding", "spring_data_access", "web_ui_route"]
    )
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main",),
        excluded_globs=(),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-framework-ambiguous",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src/main",),
        profile=profile,
        files=files,
    )

    ContractCatalog.load(ROOT / "contracts").validate_artifact(result.artifact)
    edges = cast(list[dict[str, Any]], result.artifact["edges"])
    unresolved_targets = {
        str(edge["to_ref"])
        for edge in edges
        if edge["resolution_status"] == "unresolved"
        and edge["extractor"] in {"spring_config_binding", "spring_data_access", "web_ui_route"}
    }
    assert unresolved_targets == {
        "unresolved:config_key:feature.Flag",
        "unresolved:endpoint:http:GET:/expense/page",
        "unresolved:table:expenses",
    }


def test_java_type_flow_and_field_access_are_not_project_specific(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/example"
    source.mkdir(parents=True)
    (source / "CustomerRecord.java").write_text(
        """package example;
public class CustomerRecord {
  private String state;
  public String getState() { return state; }
  public void setState(String state) { this.state = state; }
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
    (source / "CustomerController.java").write_text(
        """package example;
import java.util.List;
import java.util.Map;
public class CustomerController {
  private final CustomerService service = new CustomerService();
  public String state(Map<String, Object> body, List<CustomerRecord> records) {
    Long.valueOf(body.get("id").toString());
    for (CustomerRecord record : records) { record.getState(); }
    return service.load().getState();
  }
}
""",
        encoding="utf-8",
    )
    profile = load_profile()
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main",),
        excluded_globs=(),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-generic-java-flow",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src/main",),
        profile=profile,
        files=files,
    )

    ContractCatalog.load(ROOT / "contracts").validate_artifact(result.artifact)
    artifact_files = cast(list[dict[str, Any]], result.artifact["files"])
    symbols = [
        symbol
        for artifact_file in artifact_files
        for symbol in cast(list[dict[str, Any]], artifact_file["symbols"])
    ]
    by_signature = {str(symbol["signature"]): symbol for symbol in symbols}
    getter = by_signature["example.CustomerRecord#getState()"]
    setter = by_signature["example.CustomerRecord#setState(String)"]
    field = by_signature["example.CustomerRecord#state:String"]
    controller = by_signature[
        "example.CustomerController#state(Map<String,Object>,List<CustomerRecord>)"
    ]
    assert getter["declared_type"] == "String"
    assert field["declared_type"] == "String"
    edges = cast(list[dict[str, Any]], result.artifact["edges"])
    assert any(
        edge["from_ref"] == controller["symbol_id"]
        and edge["to_ref"] == getter["symbol_id"]
        and edge["edge_type"] == "calls"
        for edge in edges
    )
    assert any(
        edge["from_ref"] == getter["symbol_id"]
        and edge["to_ref"] == field["symbol_id"]
        and edge["edge_type"] == "reads"
        and edge["extractor"] == "java_field_access"
        for edge in edges
    )
    assert any(
        edge["from_ref"] == setter["symbol_id"]
        and edge["to_ref"] == field["symbol_id"]
        and edge["edge_type"] == "writes"
        and edge["extractor"] == "java_field_access"
        for edge in edges
    )
    assert not any(edge["resolution_status"] == "unresolved" for edge in edges)


def test_spring_data_optional_lambda_uses_repository_generic_type(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/example"
    resources = tmp_path / "src/main/resources"
    source.mkdir(parents=True)
    resources.mkdir(parents=True)
    (source / "CustomerRecord.java").write_text(
        """package example;
import jakarta.persistence.Table;
@Table(name = "customer_records") public class CustomerRecord {
  private String state;
  public void setState(String state) { this.state = state; }
}
""",
        encoding="utf-8",
    )
    (source / "CustomerRecordRepository.java").write_text(
        """package example;
import org.springframework.data.jpa.repository.JpaRepository;
public interface CustomerRecordRepository extends JpaRepository<CustomerRecord, Long> {}
""",
        encoding="utf-8",
    )
    (source / "CustomerRecordService.java").write_text(
        """package example;
import org.springframework.stereotype.Service;
@Service public class CustomerRecordService {
  private CustomerRecordRepository repository;
  public void approve(Long id) {
    repository.findById(id).ifPresent(record -> record.setState("approved"));
  }
}
""",
        encoding="utf-8",
    )
    (resources / "schema.sql").write_text(
        "CREATE TABLE customer_records(id bigint);\n", encoding="utf-8"
    )
    profile = load_profile()
    profile["anchor_extractors"].append("spring_data_access")
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main",),
        excluded_globs=(),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-generic-spring-data",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src/main",),
        profile=profile,
        files=files,
    )

    ContractCatalog.load(ROOT / "contracts").validate_artifact(result.artifact)
    edges = cast(list[dict[str, Any]], result.artifact["edges"])
    assert any(
        edge["extractor"] == "spring_data_access"
        and edge["resolution_status"] == "external"
        and edge["to_ref"] == "external:call:java.util.Optional.ifPresent/1"
        for edge in edges
    )
    assert any(
        edge["extractor"] == "spring_data_access"
        and edge["resolution_status"] == "resolved"
        and edge["edge_type"] == "calls"
        for edge in edges
    )
    assert not any(edge["resolution_status"] == "unresolved" for edge in edges)


def test_dynamic_route_sink_propagates_static_calls_and_keeps_runtime_input(
    tmp_path: Path,
) -> None:
    java_root = tmp_path / "src/main/java/example"
    webapp = tmp_path / "src/main/webapp"
    java_root.mkdir(parents=True)
    webapp.mkdir(parents=True)
    (java_root / "CustomerController.java").write_text(
        """package example;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
public class CustomerController {
  @GetMapping("/customers/one") String one() { return "one"; }
  @PostMapping("/customers/save") String save() { return "save"; }
}
""",
        encoding="utf-8",
    )
    (webapp / "transport.js").write_text(
        """function loadRoute(route, callback) {
  $.ajax({url: route, type: 'GET', success: callback});
}
function saveRoute(endpoint, payload) {
  $.ajax({url: endpoint, type: 'POST', data: payload});
}
""",
        encoding="utf-8",
    )
    (webapp / "view.jsp").write_text(
        """<script>
const saveEndpoint = '/customers/save';
loadRoute('/customers/one', onLoaded);
saveRoute(saveEndpoint, payload);
loadRoute(runtimeRoute, onLoaded);
</script>
""",
        encoding="utf-8",
    )
    profile = load_profile()
    profile["languages"].append("javascript")
    profile["anchor_extractors"].append("web_ui_route")
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main",),
        excluded_globs=(),
        languages=tuple(cast(list[str], profile["languages"])),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-generic-dynamic-route",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src/main",),
        profile=profile,
        files=files,
    )

    ContractCatalog.load(ROOT / "contracts").validate_artifact(result.artifact)
    artifact_files = cast(list[dict[str, Any]], result.artifact["files"])
    route_names = {
        str(symbol["name"])
        for artifact_file in artifact_files
        for symbol in cast(list[dict[str, Any]], artifact_file["symbols"])
        if symbol["symbol_type"] == "ui_route"
    }
    assert route_names == {
        "/customers/one",
        "/customers/save",
        "dynamic:runtimeRoute",
    }
    edges = cast(list[dict[str, Any]], result.artifact["edges"])
    route_calls = [
        edge
        for edge in edges
        if edge["extractor"] == "web_ui_route" and edge["edge_type"] == "calls"
    ]
    assert sum(edge["resolution_status"] == "resolved" for edge in route_calls) == 2
    assert [
        edge["to_ref"] for edge in route_calls if edge["resolution_status"] == "unresolved"
    ] == ["unresolved:endpoint:GET:dynamic:runtimeRoute"]


def test_scanner_marks_parse_errors_and_unknown_framework_as_truncated(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example"
    source.mkdir(parents=True)
    (source / "Broken.java").write_text("class Broken { void x( }", encoding="utf-8")
    profile = load_profile()
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main",),
        excluded_globs=(),
        languages=("java",),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-broken",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src/main",),
        profile=profile,
        files=files,
    )

    assert result.artifact["scan_status"] == "truncated"
    assert "framework_marker_not_found" in result.diagnostics
    assert any(value.startswith("tree_sitter_parse_error:") for value in result.diagnostics)


def test_scanner_distinguishes_explicit_external_receivers_from_unknown_calls(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example"
    source.mkdir(parents=True)
    (source / "SearchController.java").write_text(
        """package example;
import java.util.HashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
public class SearchController {
  @GetMapping("/search")
  public Map<String, Object> search() {
    Map<String, Object> result = new HashMap<>();
    result.put("status", "ok");
    unknown.refresh();
    return result;
  }
}
""",
        encoding="utf-8",
    )
    profile = load_profile()
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main",),
        excluded_globs=(),
        languages=("java",),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-external-call",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src/main",),
        profile=profile,
        files=files,
    )

    calls = [
        edge
        for edge in cast(list[dict[str, Any]], result.artifact["edges"])
        if edge["edge_type"] == "calls"
    ]
    assert any(
        edge["to_ref"] == "external:call:result.put/2" and edge["resolution_status"] == "external"
        for edge in calls
    )
    assert any(
        edge["to_ref"] == "unresolved:call:unknown.refresh/0"
        and edge["resolution_status"] == "unresolved"
        for edge in calls
    )


def test_scanner_requires_the_registered_typescript_extractor(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/typescript"
    source.mkdir(parents=True)
    (source / "ExpensePage.ts").write_text(
        "// org.springframework.stereotype.Service\nexport const status = 'all';\n",
        encoding="utf-8",
    )
    profile = load_profile()
    profile["languages"] = ["typescript"]
    profile["anchor_extractors"] = ["java_symbol"]
    files = WorkspaceScanner().discover(
        workspace_root=tmp_path,
        scan_roots=("src/main",),
        excluded_globs=(),
        languages=("typescript",),
    )

    result = CodeGraphScanner().scan(
        code_graph_snapshot_id="graph-typescript",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="abc123",
        scan_roots=("src/main",),
        profile=profile,
        files=files,
    )

    assert result.artifact["scan_status"] == "truncated"
    assert result.diagnostics == ("required_extractor_missing:typescript:typescript_symbol",)
