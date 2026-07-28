# Profile Examples

Profile 是运行时配置，不包含凭据。每个 Profile 必须包含 `profile_type`、稳定 ID 和语义版本，并通过 `catalog.json` 对应的 JSON Schema。Profile 版本、项目当前绑定和追加式激活审计已接入 PostgreSQL。

Profile drift 的有序恢复使用维护专用的 `operamind-profile-rebuild-worker`。该命令只接受操作者提供的固定 handler 配置，并复用内部 Task／Claim／Lease／Result 协议；它不是主变更闭环入口，也不在 Web 中公开。

`UiLocatorProfile` 将已批准的 UI Knowledge Snapshot、Environment／Deployment、Locator 策略、最低信赖度和业务 Target Ref 固定为不可变 Profile Version。UI Knowledge 审核通过后自动登记该 Version；选择激活时使用 `ui-locator:{environment_id}:{deployment_revision}` Binding，并对依赖的 UI Test Plan、Evidence 和 Closure 执行 Drift 检测。

- `embedding-profile.example.json`：真实 RAG Provider 和批量索引参数。
- `document-convention-profile.example.json`：同类设计书的多种写法。
- `screen-design-convention-profile.example.json`：画面项目表与跨 Sheet `画面ID` 上下文。
- `program-design-convention-profile.example.json`：程序设计书的方法清单与方法契约。
- `document-relation-profile.example.json`：用 Canonical 字段精确连接跨文档事实，并记录未解析关系。
- `code-framework-profile.example.json`：代码扫描锚点和关系扩展策略。
- `polyglot-code-framework-profile.example.json`：JavaScript／TypeScript、Python、Kotlin 的 Tree-sitter Symbol、Import、Call、Inheritance 与 Test Binding 策略。
- `springboot15-thymeleaf-gradle-code-framework-profile.example.json`：Spring Boot 1.5／Thymeleaf／Gradle 工程的 Java、HTML Template、Spring MVC Route、配置与数据访问关系。
- `struts1-code-framework-profile.example.json`：Struts 1 ActionMapping／ActionForm／ActionForward、ActionServlet route、Tiles 与 JSP 导航关系。
- `visiondemo-code-framework-profile.json`：VisionDemo 单仓库模块路径的代码扫描与关系扩展策略。
- `visiondemo-document-relation-profile.json`：VisionDemo 已提取字段适用的文档关系规则。
- `command-execution-profile.example.json`：固定 argv、工作目录、超时、环境变量白名单和失败策略的安全命令模板。
- `springboot15-thymeleaf-gradle-command-profile.example.json`：通过项目 Gradle Wrapper 固定执行 compile、test 和 build；`JAVA_HOME` 必须指向该旧工程兼容的 JDK。
- `visiondemo-command-profile.json`：VisionDemo 的 Maven test/package 固定命令；只通过统一 Change Task 返回的 `command_ref` 执行，内部范围授权不作为独立操作暴露。
- `ui-locator-profile.example.json`：把已审核 UI Knowledge Snapshot、Deployment、Locator 策略和业务 Target 固定为可激活版本。

设计书、代码与测试计划由同一个 VS Code GitHub Copilot Change Task 处理。Copilot 通过 OperaMind MCP 读取 Canonical RAG 候选与限定范围，并直接登记设计差分、TestPlan、TestDataPlan、命令结果和代码差分；不再生成或搬运 `ai-response.json`。Embedding 仍独立使用本地 LM Studio 的 Nomic 模型。

注册项目开始变更时，OperaMind 会检查 `gradlew`、Wrapper 配置、`build.gradle` 中的 Spring Boot 1.5 与 Thymeleaf 依赖，以及 `src/main/resources/templates/**/*.html`。证据完整且对应 Binding 尚未设置时，系统自动登记并激活上述两个 Profile；已有项目 Profile 不会被覆盖。相同技术栈信息和固定 Gradle 命令会写入内部 Copilot Change Task，但不会作为额外步骤显示在 Web。

项目可以组合多个 Document Convention 和 Code Framework Profile。项目领域名称不能写入通用引擎。

当前 P3 Scanner 对 Java 使用 Tree-sitter，支持 `java_symbol`、`java_field_access`、`spring_endpoint` 和 `junit_test`；对 JavaScript、TypeScript/TSX、Python、Kotlin 分别使用 `javascript_symbol`、`typescript_symbol`、`python_symbol`、`kotlin_symbol`。四个通用 Adapter 生成稳定 declaration、import、call、inheritance/implements 和 resolved Test Binding，歧义关系保持 unresolved。Java 方法和字段的 `declared_type` 用于唯一返回链解析，显式 owner field 访问生成 `reads`/`writes`。`sql_table` 与 `config_key` 使用有界确定性词法提取。`web_ui_route` 把 `.html` 作为 UI Template 扫描，并解析标准 HTML 及 Thymeleaf 的 `th:href`／`th:action`，再按 HTTP method 连接 Spring MVC endpoint；动态表达式不猜测。`visiondemo-code-framework-profile.json` 启用 `spring_config_binding`、`spring_data_access` 和 `web_ui_route`；`struts1-code-framework-profile.example.json` 启用 `struts1_mvc`，将 `struts-config.xml`、Action／Form／Forward、ActionServlet URL、Tiles 和 JSP tag 连接为 `exposes`、`calls`、`maps_to`、`navigates_to`。静态证明不足的 route、DispatchAction 和外部类型继续 unresolved/external。Profile 中声明未知 extractor、缺少依赖 extractor 或缺少语言都会产生持久化 diagnostic 并把 Graph 标为 truncated，不会静默忽略。

Scope Resolver 按 StructuredChange 的 `domain` 精确选择一条 `relation_policies`，只遍历其中允许的 Edge，并遵守 `max_depth` 与 `include_reverse`。缺少 domain policy 时只保留直接锚点候选并产生阻断 unknown，不使用通用 fallback。

Baseline 除 JSON Schema 外还检查跨字段语义：Document Variant ID 唯一、Signal 权重之和为 `1.0`、Stable Key 字段存在于别名映射、每个 Stable Key 字段都有显式 normalizer、归一化后的字段别名不能指向多个 Canonical Field、Document Relation Rule ID 唯一且 source/target/normalizer 字段数量一致、Code Framework 的 relation domain 唯一，以及 Command Profile 的 command ref 唯一且工作目录/相对可执行文件不能逃出 Workspace。Embedding dimensions 上限为 pgvector `vector` 存储支持的 16,000；MVP 使用精确余弦检索，不提前声明 ANN index 能力。
