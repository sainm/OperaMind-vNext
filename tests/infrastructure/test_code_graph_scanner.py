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


def test_scanner_blocks_silent_semantic_fallback_for_unimplemented_code_language(
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
    assert result.diagnostics == ("language_extractor_not_implemented:typescript",)
