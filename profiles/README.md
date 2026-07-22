# Profile Examples

Profile 是运行时配置，不包含凭据。每个 Profile 必须包含 `profile_type`、稳定 ID 和语义版本，并通过 `catalog.json` 对应的 JSON Schema。Profile 版本、项目当前绑定和追加式激活审计已接入 PostgreSQL。

- `embedding-profile.example.json`：真实 RAG Provider 和批量索引参数。
- `document-convention-profile.example.json`：同类设计书的多种写法。
- `screen-design-convention-profile.example.json`：画面项目表与跨 Sheet `画面ID` 上下文。
- `document-relation-profile.example.json`：用 Canonical 字段精确连接跨文档事实，并记录未解析关系。
- `code-framework-profile.example.json`：代码扫描锚点和关系扩展策略。
- `visiondemo-code-framework-profile.json`：VisionDemo 单仓库模块路径的代码扫描与关系扩展策略。
- `visiondemo-document-relation-profile.json`：VisionDemo 已提取字段适用的文档关系规则。
- `command-execution-profile.example.json`：固定 argv、工作目录、超时、环境变量白名单和失败策略的安全命令模板。
- `visiondemo-command-profile.json`：VisionDemo 的 Maven test/package 固定命令；只通过 Approval Grant 的 command ref 执行。

Draft 生成不使用命令型 Provider Profile。OperaMind 生成有界交接包，VS Code 上的 GitHub Copilot 只写入指定的 `ai-response.json`，随后由 `operamind-change-draft generate --response-file ...` 导入并执行 Schema、Git Revision、路径范围与业务语义校验。Embedding 则独立使用本地 LM Studio 的 Nomic 模型。

项目可以组合多个 Document Convention 和 Code Framework Profile。项目领域名称不能写入通用引擎。

当前 P3 Scanner 对 Java 使用 Tree-sitter，支持 `java_symbol`、`java_field_access`、`spring_endpoint` 和 `junit_test`；方法和字段的 `declared_type` 用于唯一返回链解析，显式 owner field 访问生成 `reads`/`writes`。`sql_table` 与 `config_key` 使用有界确定性词法提取。`visiondemo-code-framework-profile.json` 另外启用 `spring_config_binding`、`spring_data_access` 和 `web_ui_route`，把显式配置消费、JPA/Spring Data 数据访问、Spring Data Optional Lambda 以及 JSP/JavaScript Route 连接到 Graph 中唯一的 key、table 和 Spring endpoint。Route 支持局部常量和唯一函数参数 sink 摘要；运行时值仍保留 dynamic/unresolved。Profile 中声明未知 extractor、缺少依赖 extractor 或缺少 UI 语言都会产生持久化 diagnostic 并把 Graph 标为 truncated，不会静默忽略。

Scope Resolver 按 StructuredChange 的 `domain` 精确选择一条 `relation_policies`，只遍历其中允许的 Edge，并遵守 `max_depth` 与 `include_reverse`。缺少 domain policy 时只保留直接锚点候选并产生阻断 unknown，不使用通用 fallback。

Baseline 除 JSON Schema 外还检查跨字段语义：Document Variant ID 唯一、Signal 权重之和为 `1.0`、Stable Key 字段存在于别名映射、每个 Stable Key 字段都有显式 normalizer、归一化后的字段别名不能指向多个 Canonical Field、Document Relation Rule ID 唯一且 source/target/normalizer 字段数量一致、Code Framework 的 relation domain 唯一，以及 Command Profile 的 command ref 唯一且工作目录/相对可执行文件不能逃出 Workspace。Embedding dimensions 上限为 pgvector `vector` 存储支持的 16,000；MVP 使用精确余弦检索，不提前声明 ANN index 能力。
