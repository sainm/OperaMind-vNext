import json
from pathlib import Path
from typing import Any, cast

from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import (
    CodeGraphScanner,
    GitPathChange,
    IncrementalCodeGraphScanner,
    WorkspaceScanner,
)
from operamind.infrastructure.postgres.code_graph_repository import (
    _validate_graph_artifact,
)

ROOT = Path(__file__).parents[2]


def _profile() -> dict[str, Any]:
    value: object = json.loads(
        (ROOT / "profiles/struts1-code-framework-profile.example.json").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _write_project(root: Path, *, success_target: str = "expense.list") -> None:
    java = root / "src/main/java/example"
    web_inf = root / "src/main/webapp/WEB-INF"
    jsp = web_inf / "jsp/expense"
    layouts = web_inf / "layouts"
    java.mkdir(parents=True)
    jsp.mkdir(parents=True)
    layouts.mkdir(parents=True)
    (java / "ExpenseForm.java").write_text(
        """package example;
import org.apache.struts.action.ActionForm;
public class ExpenseForm extends ActionForm {
  private String status;
  public String getStatus() { return status; }
}
""",
        encoding="utf-8",
    )
    (java / "ExpenseAction.java").write_text(
        """package example;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.apache.struts.action.Action;
import org.apache.struts.action.ActionForm;
import org.apache.struts.action.ActionForward;
import org.apache.struts.action.ActionMapping;
public class ExpenseAction extends Action {
  public ActionForward execute(
      ActionMapping mapping,
      ActionForm form,
      HttpServletRequest request,
      HttpServletResponse response) {
    return mapping.findForward("success");
  }
  public ActionForward cancel(
      ActionMapping mapping,
      ActionForm form,
      HttpServletRequest request,
      HttpServletResponse response) {
    return new ActionForward("/WEB-INF/jsp/expense/detail.jsp");
  }
}
""",
        encoding="utf-8",
    )
    (web_inf / "web.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<web-app>
  <servlet>
    <servlet-name>action</servlet-name>
    <servlet-class>org.apache.struts.action.ActionServlet</servlet-class>
  </servlet>
  <servlet-mapping>
    <servlet-name>action</servlet-name>
    <url-pattern>*.do</url-pattern>
  </servlet-mapping>
</web-app>
""",
        encoding="utf-8",
    )
    (web_inf / "struts-config.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE struts-config PUBLIC
  "-//Apache Software Foundation//DTD Struts Configuration 1.3//EN"
  "http://struts.apache.org/dtds/struts-config_1_3.dtd">
<struts-config>
  <form-beans>
    <form-bean name="expenseForm" type="example.ExpenseForm"/>
  </form-beans>
  <global-forwards>
    <forward name="home" path="/expense/search.do"/>
  </global-forwards>
  <action-mappings>
    <action path="/expense/search"
            type="example.ExpenseAction"
            name="expenseForm"
            input="/WEB-INF/jsp/expense/search.jsp">
      <forward name="success" path="{success_target}"/>
    </action>
  </action-mappings>
</struts-config>
""",
        encoding="utf-8",
    )
    (web_inf / "tiles-defs.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE tiles-definitions PUBLIC
  "-//Apache Software Foundation//DTD Tiles Configuration 1.3//EN"
  "http://struts.apache.org/dtds/tiles-config_1_3.dtd">
<tiles-definitions>
  <definition name="layout.base" path="/WEB-INF/layouts/base.jsp"/>
  <definition name="expense.list" extends="layout.base">
    <put name="body" value="/WEB-INF/jsp/expense/list.jsp"/>
  </definition>
  <definition name="expense.detail" extends="layout.base">
    <put name="body" value="/WEB-INF/jsp/expense/detail.jsp"/>
  </definition>
</tiles-definitions>
""",
        encoding="utf-8",
    )
    (jsp / "search.jsp").write_text(
        """<%@ taglib uri="/WEB-INF/struts-html.tld" prefix="html" %>
<%@ taglib uri="/WEB-INF/struts-logic.tld" prefix="logic" %>
<%@ taglib uri="/WEB-INF/struts-tiles.tld" prefix="tiles" %>
<html:form action="/expense/search">
  <html:submit value="検索"/>
</html:form>
<html:link forward="home">ホーム</html:link>
<html:link page="/WEB-INF/jsp/expense/detail.jsp">詳細</html:link>
<tiles:insert definition="expense.list"/>
""",
        encoding="utf-8",
    )
    (jsp / "list.jsp").write_text("<p>一覧</p>\n", encoding="utf-8")
    (jsp / "detail.jsp").write_text("<p>詳細</p>\n", encoding="utf-8")
    (layouts / "base.jsp").write_text(
        '<html><body><tiles:insert attribute="body"/></body></html>\n',
        encoding="utf-8",
    )


def _scan(root: Path, *, graph_id: str, revision: str) -> dict[str, Any]:
    profile = _profile()
    files = WorkspaceScanner().discover(
        workspace_root=root,
        scan_roots=("src/main",),
        excluded_globs=tuple(cast(list[str], profile["excluded_globs"])),
        languages=tuple(cast(list[str], profile["languages"])),
    )
    artifact = (
        CodeGraphScanner()
        .scan(
            code_graph_snapshot_id=graph_id,
            project_id="project-1",
            repository_id="repository-1",
            repository_revision=revision,
            scan_roots=("src/main",),
            profile=profile,
            files=files,
        )
        .artifact
    )
    _validate_graph_artifact(artifact)
    return artifact


def test_struts1_adapter_builds_traceable_action_form_forward_tiles_and_jsp_graph(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)

    artifact = _scan(tmp_path, graph_id="graph-struts1", revision="a" * 40)

    ContractCatalog.load(ROOT / "contracts").validate_artifact(artifact)
    assert artifact["scan_status"] == "complete"
    assert artifact["diagnostics"] == []
    files = cast(list[dict[str, Any]], artifact["files"])
    symbols = [symbol for file in files for symbol in cast(list[dict[str, Any]], file["symbols"])]
    symbols_by_type_name = {
        (str(symbol["symbol_type"]), str(symbol["name"])): str(symbol["symbol_id"])
        for symbol in symbols
    }
    action = symbols_by_type_name[("struts_action_mapping", "/expense/search")]
    form = symbols_by_type_name[("struts_action_form", "expenseForm")]
    success = symbols_by_type_name[("struts_action_forward", "success")]
    home = symbols_by_type_name[("struts_action_forward", "home")]
    tile = symbols_by_type_name[("tiles_definition", "expense.list")]
    base_tile = symbols_by_type_name[("tiles_definition", "layout.base")]
    action_class = symbols_by_type_name[("class", "ExpenseAction")]
    form_class = symbols_by_type_name[("class", "ExpenseForm")]
    execute = symbols_by_type_name[("method", "execute")]
    cancel = symbols_by_type_name[("method", "cancel")]
    jsp_routes = {
        str(symbol["signature"]): str(symbol["symbol_id"])
        for symbol in symbols
        if symbol["symbol_type"] == "struts_jsp_route"
    }
    form_route = jsp_routes["struts:jsp-route:form_action:/expense/search"]
    home_route = jsp_routes["struts:jsp-route:forward:home"]
    tile_route = jsp_routes["struts:jsp-route:tiles:expense.list"]
    detail_route = jsp_routes["struts:jsp-route:jsp:/WEB-INF/jsp/expense/detail.jsp"]
    file_id_by_path = {str(file["path"]): str(file["file_id"]) for file in files}
    search_jsp = file_id_by_path["src/main/webapp/WEB-INF/jsp/expense/search.jsp"]
    list_jsp = file_id_by_path["src/main/webapp/WEB-INF/jsp/expense/list.jsp"]
    detail_jsp = file_id_by_path["src/main/webapp/WEB-INF/jsp/expense/detail.jsp"]
    base_jsp = file_id_by_path["src/main/webapp/WEB-INF/layouts/base.jsp"]
    edges = [
        edge
        for edge in cast(list[dict[str, Any]], artifact["edges"])
        if edge["extractor"] == "struts1_mvc"
    ]

    assert _has_edge(edges, "exposes", action, "http:ANY:/expense/search.do", "external")
    assert _has_edge(edges, "maps_to", action, action_class)
    assert _has_edge(edges, "maps_to", action, form)
    assert _has_edge(edges, "maps_to", form, form_class)
    assert _has_edge(edges, "calls", action, execute)
    assert _has_edge(edges, "navigates_to", action, search_jsp)
    assert _has_edge(edges, "navigates_to", action, success)
    assert _has_edge(edges, "navigates_to", execute, success)
    assert _has_edge(edges, "navigates_to", cancel, detail_jsp)
    assert _has_edge(edges, "navigates_to", success, tile)
    assert _has_edge(edges, "navigates_to", home, action)
    assert _has_edge(edges, "maps_to", tile, base_tile)
    assert _has_edge(edges, "navigates_to", tile, list_jsp)
    assert _has_edge(edges, "navigates_to", base_tile, base_jsp)
    assert _has_edge(edges, "calls", form_route, action)
    assert _has_edge(edges, "navigates_to", home_route, home)
    assert _has_edge(edges, "navigates_to", tile_route, tile)
    assert _has_edge(edges, "navigates_to", detail_route, detail_jsp)


def test_struts1_incremental_config_change_matches_full_graph(tmp_path: Path) -> None:
    _write_project(tmp_path)
    profile = _profile()
    workspace = WorkspaceScanner()
    roots = ("src/main",)
    exclusions = tuple(cast(list[str], profile["excluded_globs"]))
    languages = tuple(cast(list[str], profile["languages"]))
    initial_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
    )
    base = _scan(tmp_path, graph_id="graph-struts1-base", revision="a" * 40)
    config_path = "src/main/webapp/WEB-INF/struts-config.xml"
    config = tmp_path / config_path
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'path="expense.list"',
            'path="expense.detail"',
        ),
        encoding="utf-8",
    )
    current_paths = frozenset(file.path for file in initial_files)
    changed_files = workspace.discover(
        workspace_root=tmp_path,
        scan_roots=roots,
        excluded_globs=exclusions,
        languages=languages,
        allowed_paths=frozenset({config_path}),
    )
    incremental = IncrementalCodeGraphScanner()
    plan = incremental.plan(
        previous_artifact=base,
        changes=(GitPathChange("M", (config_path,)),),
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
        code_graph_snapshot_id="graph-struts1-incremental",
        project_id="project-1",
        repository_id="repository-1",
        repository_revision="b" * 40,
        scan_roots=roots,
        profile=profile,
        previous_artifact=base,
        plan=plan,
        affected_files=affected_files,
        current_tracked_paths=current_paths,
    ).artifact
    expected = _scan(tmp_path, graph_id="graph-struts1-full", revision="b" * 40)

    _validate_graph_artifact(actual)
    assert plan.affected_paths == tuple(sorted(current_paths))
    assert actual["files"] == expected["files"]
    assert actual["edges"] == expected["edges"]
    assert actual["diagnostics"] == expected["diagnostics"] == []


def test_struts1_adapter_fails_closed_for_malformed_config(tmp_path: Path) -> None:
    source = tmp_path / "src/main/webapp/WEB-INF"
    source.mkdir(parents=True)
    (source / "struts-config.xml").write_text(
        "<struts-config><action-mappings><action path='/broken'></struts-config>",
        encoding="utf-8",
    )

    artifact = _scan(tmp_path, graph_id="graph-struts1-broken", revision="a" * 40)

    assert artifact["scan_status"] == "truncated"
    assert "struts1_config_not_found" in artifact["diagnostics"]
    assert any(
        str(value).startswith("struts1_xml_parse_error:")
        for value in cast(list[object], artifact["diagnostics"])
    )


def test_struts1_adapter_keeps_direct_external_and_dynamic_mappings_explicit(
    tmp_path: Path,
) -> None:
    web_inf = tmp_path / "src/main/webapp/WEB-INF"
    jsp = web_inf / "jsp"
    jsp.mkdir(parents=True)
    (web_inf / "struts-config.xml").write_text(
        """<struts-config>
  <action-mappings>
    <action path="/external" type="legacy.ExternalAction"/>
    <action path="/help" forward="/WEB-INF/jsp/help.jsp"/>
  </action-mappings>
</struts-config>
""",
        encoding="utf-8",
    )
    (jsp / "help.jsp").write_text(
        '<html:form action="${selectedAction}"></html:form>\n',
        encoding="utf-8",
    )

    artifact = _scan(tmp_path, graph_id="graph-struts1-explicit", revision="a" * 40)

    assert artifact["scan_status"] == "complete"
    files = cast(list[dict[str, Any]], artifact["files"])
    symbols = [symbol for file in files for symbol in cast(list[dict[str, Any]], file["symbols"])]
    by_type_name = {
        (str(symbol["symbol_type"]), str(symbol["name"])): str(symbol["symbol_id"])
        for symbol in symbols
    }
    external = by_type_name[("struts_action_mapping", "/external")]
    help_action = by_type_name[("struts_action_mapping", "/help")]
    dynamic_route = by_type_name[("struts_jsp_route", "${selectedAction}")]
    help_jsp = next(
        str(file["file_id"])
        for file in files
        if file["path"] == "src/main/webapp/WEB-INF/jsp/help.jsp"
    )
    edges = [
        edge
        for edge in cast(list[dict[str, Any]], artifact["edges"])
        if edge["extractor"] == "struts1_mvc"
    ]
    assert _has_edge(
        edges,
        "maps_to",
        external,
        "external:java_type:legacy.ExternalAction",
        "external",
    )
    assert _has_edge(
        edges,
        "calls",
        external,
        "external:struts_action_method:legacy.ExternalAction#execute",
        "external",
    )
    assert _has_edge(
        edges,
        "exposes",
        external,
        "unresolved:struts_endpoint:/external",
        "unresolved",
    )
    assert _has_edge(edges, "navigates_to", help_action, help_jsp)
    assert _has_edge(
        edges,
        "calls",
        dynamic_route,
        "unresolved:struts_jsp_route:${selectedAction}",
        "unresolved",
    )


def test_struts1_adapter_reports_duplicate_definitions_and_unsafe_xml(
    tmp_path: Path,
) -> None:
    web_inf = tmp_path / "src/main/webapp/WEB-INF"
    web_inf.mkdir(parents=True)
    (web_inf / "struts-config.xml").write_text(
        """<struts-config>
  <form-beans>
    <form-bean name="shared" type="example.FirstForm"/>
    <form-bean name="shared" type="example.SecondForm"/>
  </form-beans>
  <global-forwards>
    <forward name="home" path="/one.jsp"/>
    <forward name="home" path="/two.jsp"/>
  </global-forwards>
  <action-mappings>
    <action path="/same" type="example.FirstAction"/>
    <action path="/same" type="example.SecondAction"/>
  </action-mappings>
</struts-config>
""",
        encoding="utf-8",
    )
    (web_inf / "tiles-defs.xml").write_text(
        """<tiles-definitions>
  <definition name="shared.layout" path="/one.jsp"/>
  <definition name="shared.layout" path="/two.jsp"/>
</tiles-definitions>
""",
        encoding="utf-8",
    )
    (web_inf / "struts-unsafe.xml").write_text(
        """<!DOCTYPE struts-config [
  <!ENTITY unsafe "value">
]>
<struts-config/>
""",
        encoding="utf-8",
    )

    artifact = _scan(tmp_path, graph_id="graph-struts1-duplicates", revision="a" * 40)

    assert artifact["scan_status"] == "truncated"
    diagnostics = set(cast(list[str], artifact["diagnostics"]))
    assert {
        "struts1_duplicate_form_name:src/main/webapp/WEB-INF/struts-config.xml:shared",
        "struts1_duplicate_global_forward:src/main/webapp/WEB-INF/struts-config.xml:home",
        "struts1_duplicate_action_path:src/main/webapp/WEB-INF/struts-config.xml:/same",
        "struts1_duplicate_tiles_definition:src/main/webapp/WEB-INF/tiles-defs.xml:shared.layout",
        "struts1_unsafe_xml_declaration:src/main/webapp/WEB-INF/struts-unsafe.xml",
    }.issubset(diagnostics)


def test_struts1_adapter_keeps_local_forwards_scoped_to_each_config(
    tmp_path: Path,
) -> None:
    web_inf = tmp_path / "src/main/webapp/WEB-INF"
    jsp = web_inf / "jsp"
    jsp.mkdir(parents=True)
    for module, target in (("sales", "sales.jsp"), ("admin", "admin.jsp")):
        (web_inf / f"struts-{module}-config.xml").write_text(
            f"""<struts-config>
  <action-mappings>
    <action path="/shared" type="example.{module.title()}Action">
      <forward name="success" path="/WEB-INF/jsp/{target}"/>
    </action>
  </action-mappings>
</struts-config>
""",
            encoding="utf-8",
        )
        (jsp / target).write_text(f"<p>{module}</p>\n", encoding="utf-8")

    artifact = _scan(tmp_path, graph_id="graph-struts1-modules", revision="a" * 40)

    assert artifact["scan_status"] == "complete"
    files = cast(list[dict[str, Any]], artifact["files"])
    symbols = [symbol for file in files for symbol in cast(list[dict[str, Any]], file["symbols"])]
    by_signature = {str(symbol["signature"]): str(symbol["symbol_id"]) for symbol in symbols}
    sales_config = "src/main/webapp/WEB-INF/struts-sales-config.xml"
    admin_config = "src/main/webapp/WEB-INF/struts-admin-config.xml"
    sales_action = by_signature[f"struts:action:{sales_config}:/shared"]
    admin_action = by_signature[f"struts:action:{admin_config}:/shared"]
    sales_forward = by_signature[f"struts:forward:{sales_config}:/shared:success"]
    admin_forward = by_signature[f"struts:forward:{admin_config}:/shared:success"]
    edges = [
        edge
        for edge in cast(list[dict[str, Any]], artifact["edges"])
        if edge["extractor"] == "struts1_mvc"
    ]
    assert _has_edge(edges, "navigates_to", sales_action, sales_forward)
    assert _has_edge(edges, "navigates_to", admin_action, admin_forward)
    assert not _has_edge(edges, "navigates_to", sales_action, admin_forward)
    assert not _has_edge(edges, "navigates_to", admin_action, sales_forward)


def _has_edge(
    edges: list[dict[str, Any]],
    edge_type: str,
    from_ref: str,
    to_ref: str,
    resolution_status: str = "resolved",
) -> bool:
    return any(
        edge["edge_type"] == edge_type
        and edge["from_ref"] == from_ref
        and edge["to_ref"] == to_ref
        and edge["resolution_status"] == resolution_status
        for edge in edges
    )
