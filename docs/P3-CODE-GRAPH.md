# P3 Code Graph 实现

## 当前完成范围

- `0008_code_graph_snapshots` 增加版本化 `code_graph_snapshots`、Profile 绑定、`code_files`、`code_symbols`、`code_edges` 和 `code_test_bindings`；`0042_code_graph_incremental_lineage` 增加 Revision 增量扫描的基线、变更路径、影响路径和解析／复用计数；`0045_runtime_route_evidence` 增加运行时 Route Observation／Resolution 台账和 runtime-enriched Graph lineage；`0046_unresolved_evidence_reports` 增加每个 Graph 一份的 unresolved 分类 Report、item 明细与 predecessor 历史链。
- Graph Snapshot 同时绑定 Project、Repository、不可变 Repository Revision 和一个或多个已持久化的 `CodeFrameworkProfile`。Artifact 中的 commit SHA 和 Profile Ref 必须与数据库身份完全一致。
- Repository 在写入前执行 CodeGraphSnapshot v1 Contract 和跨字段语义检查：File ID、Path、Symbol ID、Edge ID 唯一；行号范围有效；resolved Edge 两端都必须存在；Edge source path 必须属于本 Snapshot；Edge Profile 必须属于 Snapshot Profile 集合。
- 数据库只保存路径、语言、角色、content hash、Symbol 签名/行号和 Edge provenance，不保存完整源代码或完整 diff。
- 每条 Edge 保存类型、双端引用、解析状态、置信度、extractor、Profile Ref、源位置、`static`／`runtime`／`static_runtime` provenance 和 Evidence 引用。forward/reverse 索引支持后续双向 Scope 查询，Scope read model 不会丢弃 provenance。
- resolved `tests` Edge 会按 Symbol 所属 File 派生唯一 production-file/test-file Binding；同一文件对的多条 Edge 只保留置信度最高、ID 最稳定的一条 provenance。
- 新的 complete/truncated Graph 发布时，同 Repository 的旧 current Graph 进入 stale。旧 Graph 的完全重放不会重新激活；同 ID 不同内容被拒绝。
- Workspace Scanner 只接受显式确认且存在于 Code Framework Profile 的 scan roots，拒绝绝对路径、`..`、否定 glob 和 symlink scan root，不跟随文件/目录 symlink，并受文件数、单文件大小和总字节数硬上限约束。
- 发布前验证数据库登记的 workspace root、origin remote 和 commit SHA 与本地 clean Git HEAD 完全一致。扫描集合再与 `git ls-tree HEAD` 交集，因此 ignored 或未跟踪文件不会进入 Revision Graph。
- Java 由 Tree-sitter 提取 class/interface/record/enum、method/constructor/field 和 contains/imports/implements/calls Edge。Symbol 可保存用于关系解析的 `declared_type`。跨类型 call 只有从 `this`、明确类型、`new Type`、同 owner field type、增强 for 变量或唯一方法返回类型得到唯一目标时才 resolved；显式导入类型、`java.lang` 类型和其调用链标记为 external，其余继续保留 unresolved。
- JavaScript／TypeScript（含 TSX）、Python 和 Kotlin 由独立 Tree-sitter grammar 经同一 `SemanticAdapterRegistry` 提取 class/interface/object/type、function/method、import、inheritance/implements 和 call。JavaScript／TypeScript 的命名函数与变量绑定 arrow function 都形成稳定 Symbol；测试文件中唯一解析到 production Symbol 的调用生成 `tests` Edge。唯一候选才标记 resolved，已导入或语言内建目标标记 external，多候选或无证明目标继续 unresolved。
- `java_field_access` 从方法体中已证明的 `this.field` 或未被局部变量遮蔽的 owner field 访问生成 `reads`/`writes` Edge。getter/setter 名称本身不作为证据，因此规则同样适用于非 JavaBean 方法，并且不会按 DTO/Entity 类名猜字段。
- `spring_endpoint` 提取 class/method Mapping 合并后的 HTTP endpoint；`junit_test` 只从显式 JUnit annotation 且 resolved 到 production Symbol 的 call 派生 `tests` Edge。
- `spring_config_binding` 将 `@Value("${...}")` 和显式 `Environment.getProperty(...)` 连接到唯一 properties key；`spring_data_access` 将显式 JPA `@Table`、Spring Data Repository 泛型、派生查询和继承 CRUD 调用连接到唯一 SQL table，并按 Spring Data `findById -> Optional<T>` 契约解析 Lambda 参数中的 Entity 调用。`web_ui_route` 从 JSP/HTML form/link、JavaScript `url`、location 赋值、唯一局部常量别名，以及“函数参数流入 URL sink 后由调用实参提供 Route”的跨文件摘要提取 route，再按 HTTP method 与规范化 path 连接到唯一 Spring endpoint。运行时参数、对象属性和多目标仍显式保留 dynamic/unresolved，不按名称相似度猜测。
- `struts1_mvc` 读取 `struts-config.xml`、ActionServlet 的 `web.xml` mapping、Tiles definition、JSP Struts tag 和 Java `findForward()`。ActionMapping 生成稳定 Symbol 并通过 `exposes` 连接 `*.do` 或 path servlet route，通过 `maps_to` 连接 Action／ActionForm，通过 `calls` 连接唯一 `execute`／`perform`；局部／全局 ActionForward、Action input、ForwardAction、Tiles 继承／模板／body 及 JSP form/link/forward/tiles 标签用 `navigates_to` 或 `calls` 串成完整导航链。缺少 servlet mapping、外部 Action、动态 JSP 表达式、多模块歧义继续显式 unresolved/external；XML 错误、内部实体和同一配置内重复定义使 Graph truncated。
- Playwright Runner 对 approved Browser DSL 采集 `network_request`、`navigation` 和 `form_submission`。只保存 HTTP method、origin-relative path、Scenario、Action 和可选静态 Route Ref；query、fragment、header、body、cookie、token 均不进入 Route Evidence。`route_source_ref` 是内部 Manifest 绑定，不作为普通用户编辑字段。
- Runtime Route Reconciler 只在静态来源明确且只有一条待解析 Edge、method 一致、模板路径唯一命中一个本地图 Endpoint，且同一动态来源的全部观测只指向同一 Endpoint 时，把 unresolved `calls` Edge 生成新的 `static_runtime` resolved Edge。缺少来源、来源不存在／已非 unresolved／对应多条 Edge、method 不符、零候选、多候选或同一来源命中多个 Endpoint 都写入 `RuntimeRouteEvidence.resolutions` 并继续 unresolved。
- Runtime 合并不覆盖静态 Snapshot，而是发布 `scan_mode=runtime_enriched` 的新完整 Snapshot，绑定 base Snapshot 和不可变 RuntimeRouteEvidence。新 Edge 保存原 `static_edge_ref`；旧 Snapshot、原 unresolved Edge 和浏览器证据保持可审计。
- 每个 complete/truncated Graph 发布或幂等重放时都会自动确保一个确定性 `UnresolvedEvidenceReport`。Report 覆盖 Graph 中全部 unresolved Edge，并按 call target、endpoint Route、table、Entity、config key、navigation 或通用关系分类；每项保存源文件行号、候选、缺失 Evidence、解决建议、provenance 和 Evidence Ref。
- 候选数量等于一并不等于已经解决。只有后继 Graph 中存在唯一 `resolved` Edge，并能通过稳定 finding key 或 Runtime Edge 的 `static_edge_ref` 与上一 Report 对应时，才生成 `closed` item；静态、运行时或组合证明类型和 Evidence Ref 都写入 closure。多条 resolved Edge、缺少对应关系或只有名称候选时继续保持未证明状态。旧 Report 和旧 open item 永不覆盖。
- 新 Revision 默认从同 Repository、相同 scan roots 和相同 Code Framework Profile 的当前完整 Snapshot 增量刷新。Git rename-aware diff 先读取变更文件；声明、删除或重命名变化再扩散到旧目标的反向依赖、unresolved Edge 来源和可被新类型解析的 import 来源。方法体变化且声明不变时只重新解析变更文件。
- 三类框架关系需要跨配置、UI、Controller、Entity、Repository 和 SQL 做全局唯一性判断。启用任一专用 extractor 的 Profile 在 Revision 有变化时，会重新扫描已批准且受 Git 约束的 tracked file 集合；无变化 Revision 仍完整复用。后续只有在持久化框架 Fact ledger 能证明跨文件依赖未变化后，才允许缩小这类扫描范围。
- 增量扫描只按 Git 明确路径读取受影响文件，不遍历其余源码；未受影响的 File、Symbol、Edge 和 Test Binding 从上一不可变 Snapshot 复用，但发布物仍是一个可独立校验和重放的完整新 Snapshot。
- SQL table read/write 与 properties config key 使用有界确定性词法提取。未知 extractor、缺失语言/extractor 依赖、未支持的 production/test 语言、任一 Tree-sitter 语法错误、空扫描或 Framework marker 缺失都会产生持久化 diagnostic，并把 Snapshot 标为 truncated，不能把仅登记了文件路径误报为完整 Code Graph。
- Scope Resolver 接受 `path`、`symbol`、`endpoint`、`table`、`config_key`、`ui_route` 六类显式锚点。每个锚点必须引用当前 ContextPackage 中已有的文档 evidence；不允许从摘要猜测代码名称。
- 锚点在各自 namespace 中精确匹配并保留多匹配结果。扩展只使用 StructuredChange domain 对应的 Code Framework Profile relation policy，按显式 edge allowlist、深度和 reverse 规则执行有界 BFS。
- 直接命中的 production File 标为 `editable`，图扩展命中的 production File 标为 `read_only`，测试文件只从 Graph 中显式的 Test Binding 派生。每个候选保留 anchor、文档 evidence、Symbol、完整 Graph Path、距离和分数。
- Scope 查询严格绑定一个 accepted StructuredChange、一个 ContextPackage、当前 Code Graph Snapshot、Repository Revision 和当前激活且属于该 Graph 的 Code Framework Profile。

## 当前门禁

- `complete` 和 `truncated` 可以表示 Repository 当前最新扫描结果，但后续 Impact 确认必须拒绝 `truncated`。
- `failed` Snapshot 必须记录非空 failure reason，不能成为 current。
- Graph 在内存中构建并以单事务发布，不存在可被续跑的持久化 `building`。Git/Workspace/Profile 门禁失败在写入前显式拒绝；已通过作用域校验后的非预期 scanner/runtime 异常会保存不含源码和异常消息的不可变 `failed` Snapshot，且不会激活请求的 Profile。
- `stale` 只能由系统切换产生，不能作为新 Snapshot 的初始发布状态。
- Test Binding 只从显式、resolved 的 `tests` Edge 派生，不根据文件名猜测测试覆盖。
- `complete` 必须没有 diagnostics；`truncated`/`failed` 必须至少有一条 diagnostic。原因保存在不可变 CodeGraphSnapshot Artifact，不只存在于 CLI 日志。
- 锚点未命中、多匹配/Edge/遍历/未解析 Edge 台账达到上限、相关 unresolved Edge、Graph diagnostic 或缺少 domain relation policy 都进入 `unknown_items` 并令 `confirmation_blocked=true`。
- stale/failed Graph、Revision 不一致、Profile 越界、未 accepted 的 StructuredChange、或 ContextPackage 外的 evidence 会直接拒绝 Scope 请求。

## Build 命令

Repository 必须预先登记绝对 `workspace_root`、origin remote 和目标 Revision commit。scan root 必须逐个显式传入：

```bash
export OPERAMIND_DATABASE_URL='postgresql://...'

operamind-build-code-graph \
  --profile profiles/code-framework-profile.example.json \
  --code-graph-snapshot-id code-graph-001 \
  --project-id visiondemo \
  --repository-id repository-001 \
  --repository-revision-id revision-001 \
  --workspace-root /absolute/path/to/target-repository \
  --scan-root src/main \
  --scan-root src/test \
  --profile-version-id spring-web-example@1.0.0 \
  --profile-activation-event-id code-profile-activation-001 \
  --activated-by scanner@example.com \
  --activation-reason 'Confirmed repository and scan roots'
```

命令只输出身份、计数、marker 和 diagnostic，不输出源码。完整 Artifact 保存在 PostgreSQL。

默认使用 Revision 增量扫描；输出中的 `scan_mode`、`base_code_graph_snapshot_id`、`changed_paths`、`affected_paths`、`scanned_file_count` 和 `reused_file_count` 可审计本次解析范围。如果基线 Revision 不是当前 Revision 的祖先、scan roots/Profile 改变或没有可信基线，系统自动执行全量扫描。需要人工强制重建时追加 `--full-scan`。

## Runtime Route 合并命令

Browser Runner 的 `network_summary` Evidence 包含脱敏后的 `route_observations`。将该文件与原始静态 Graph 合并：

```bash
export OPERAMIND_DATABASE_URL='postgresql://...'

operamind-runtime-routes \
  --input /approved/evidence/network-summary.json \
  --code-graph-snapshot-id code-graph-static-001 \
  --runtime-route-evidence-id runtime-routes-001 \
  --merged-code-graph-snapshot-id code-graph-runtime-001 \
  --browser-run-id ui-run-001 \
  --captured-at 2026-07-20T08:00:00Z \
  --source-evidence-ref evidence://project-001/ui-run-001/network-summary
```

命令从 base Snapshot 读取 Project、Repository Revision 和 Profile 绑定，先发布 RuntimeRouteEvidence，再原子发布 runtime-enriched Graph。输出 Observation／resolved／unresolved 计数和 Graph 剩余 unresolved Edge 数；输入证据不能替换数据库中的作用域身份。

Unresolved Evidence 通常随 Code Graph 自动重算。迁移前 Graph 的 backfill 或审计可使用：

```bash
OPERAMIND_DATABASE_URL='postgresql://...' \
  operamind-unresolved-evidence recompute \
  --code-graph-snapshot-id code-graph-001

OPERAMIND_DATABASE_URL='postgresql://...' \
  operamind-unresolved-evidence show \
  --project-id project-001 \
  --history-limit 50
```

日文 Web 的「未解決 Evidence 管理」按 Project 展示所有 current Repository Report、来源位置、候选、缺失 Evidence、解决建议、唯一证明和不可变再计算历史。Web 只读取 Canonical Report，不在浏览器中重新推断状态。

## Scope 命令

锚点文件只包含类型化锚点和 ContextPackage 中已有的 evidence 引用：

```json
{
  "anchors": [
    {
      "anchor_id": "expense-list-endpoint",
      "kind": "endpoint",
      "value": "GET /expenses",
      "evidence_refs": ["document-node-after-001"]
    }
  ]
}
```

```bash
operamind-resolve-code-scope \
  --anchors scope-anchors.json \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --context-package-id context-package-001 \
  --structured-change-id change-001 \
  --code-graph-snapshot-id code-graph-001 \
  --repository-revision-id revision-001 \
  --profile-binding-key code-framework:repository-001
```

输出是确定性的 `scope_format_version: v1` 只读候选台账，不写入 ImpactReport。无阻断项时退出码为 `0`；存在 `unknown_items` 时仍输出完整台账并以 `1` 退出；请求身份或 evidence 越界同样以 `1` 拒绝。

## 验证

真实 PostgreSQL 集成测试覆盖：

- `0001-0045` 顺序升级、checksum 和空 Schema 安装；旧 Code Graph Snapshot 会回填为 `full` lineage。
- Repository/Revision/Profile 跨作用域检查。
- Contract Artifact 与规范化 File/Symbol/Edge round-trip。
- unresolved Edge 计数、Test Binding 派生、current/stale 切换、stale replay 和不可变冲突。
- 配置 key、UI Route/Spring Endpoint、JPA Entity/Repository/SQL table、Java 方法返回链、增强 for 变量、字段读写和 Spring Data Optional Lambda 的 resolved/external/unresolved 边，以及框架关系增量重算与同 Revision 全量图一致性。通用测试夹具使用 Customer/Record 命名，不依赖 VisionDemo 领域名称。
- VisionDemo `ad23d0a7` 实测：第一轮框架 extractor 将 unresolved 从 125 降为 99；本轮通用类型传播把 99 条 Java unresolved 全部分类为 resolved 或 external，动态 Route 摘要再增加 13 条 resolved UI route→Controller 关系。最终共有 41 条 resolved UI 关系、47 条 resolved DB 关系和 181 条字段读写关系；剩余 3 条 unresolved 全部是无法静态唯一证明的运行时 Route（`w.url`、`opts.url`、URL query 参数），Contract/Profile 校验无 diagnostic。
- Snapshot 重放除了校验 Artifact 内容，还精确核对内部 Repository Revision ID、Profile Ref 到 Profile Version ID 的映射、失败原因和规范化计数；同一外部 Profile Ref 指向不同内部版本也不能伪装成幂等重放。
- 已验证 Repository scope 后的非预期 Scanner 异常会留下 failed Artifact/规范化状态，重复同一失败可重放，正常 current Graph 和 Profile binding 不受影响。
- 数据库 forward/reverse Edge 索引和复合外键由 migration 建立。
- Runtime Route Evidence、Observation、Resolution、base Graph、runtime-enriched lineage 和 Edge provenance 均有规范化 PostgreSQL round-trip；Artifact 或规范化行漂移时失败关闭。
- 真实 Chrome 测试覆盖网络请求、页面导航和 GET form submit 三类采集，并验证 query 不进入 Evidence；通用 Customer fixture 覆盖唯一 Endpoint、模板 path、缺少来源、多 Endpoint 歧义和同一来源跨 Endpoint 冲突。
- 完整 `CodeGraphSnapshot` Artifact 是规范化图账本的权威版本。Artifact SHA-256 和 Contract 通过后，发布重放、普通 Snapshot 读取、current 读取及查询 Scope 创建还会逐项核对 Header、Profile ref、File、Symbol、Edge 和派生 Test Binding；任何规范化行删除或内容漂移均失败关闭，不能以旧 count 静默继续影响分析。
- Workspace 越界、symlink、glob、资源上限、Git 环境变量污染、dirty/untracked worktree、nested root 和 ignored file 隔离。
- 真实临时 Git Repository 到 Tree-sitter、Profile 激活、Artifact 和规范化 PostgreSQL Graph 的闭环。
- 方法体、声明、删除、重命名和新增类型的增量扫描会与同一 Revision 的全量 Graph 逐项比较；无变更 Revision 会复用全部文件，显式路径读取不会调用目录遍历。
- JavaScript／TypeScript、Python、Kotlin 的真实源码 fixture 覆盖 Symbol、相对／模块 import、resolved/external/unresolved call、inheritance/implements、Test Binding、语法错误和增量／全量一致性。非 Java 语义文件发生变化时，当前实现安全重扫受 Git 与 Profile 约束的非 Java 语义子集，避免复用跨语言陈旧关系。
- Struts 1 真实结构 fixture 覆盖外部 DTD 的 `struts-config.xml`、ActionMapping、ActionForm、局部／全局 ActionForward、ActionServlet `*.do`、Java `findForward()`、Tiles 继承与 JSP form/link/tiles 标签，并逐条验证 `exposes`、`calls`、`maps_to`、`navigates_to`。配置 Forward 改变后的增量 Graph 与同 Revision 全量 Graph 逐项一致；无 type 的直接 ActionMapping、外部 Action、动态 JSP、缺失 servlet mapping、重复定义、畸形 XML 和内部实体均有 fail-closed 回归。
- PostgreSQL 闭环验证第二个 Revision 只解析受影响文件，并保留未变化的 Test Binding；非祖先基线会安全回退到全量扫描。
- accepted StructuredChange、ContextPackage evidence、当前 Graph/Revision/Profile、endpoint 精确匹配、双向 Profile traversal、Test Binding 和缺失锚点阻断的 PostgreSQL 闭环。

## 后续任务与有意边界

- Scope 结果到正式 ImpactReport、人工确认、Edit Packet 和 Approval Grant 的状态机已经在 P4 实现。Scope Resolver 继续只生成 v1 候选台账且不自行批准修改，这是信任边界，不是未实现功能；具体状态和命令见 [P4 Impact](P4-IMPACT.md)。
- Struts 1 Adapter 当前只解析静态配置和字面量导航。运行时生成的 Forward、请求参数选择的 DispatchAction 方法、插件自定义 ActionMapping／RequestProcessor 行为和无法唯一确定的多模块前缀继续 unresolved，等待运行时 Route Evidence 或项目专用 Profile 提供证明。
