# OperaMind vNext 编程规范

## 1. 适用范围

本规范适用于 OperaMind vNext 的 Python/FastAPI、PostgreSQL/pgvector、React/TypeScript、MCP、Tree-sitter 和 Playwright 实现。

规则分为两类：

- `MUST`：违反后不能合并。
- `SHOULD`：代码审阅阈值；允许有理由地例外，但必须在 PR 中说明。

## 2. 总体原则

### 2.1 主链路优先

代码必须服务于以下主链路之一：

```text
Document -> Diff -> RAG -> Context Package -> Code Scope -> Impact Report
Impact Report -> Confirmation -> Edit Packet -> Copilot -> UI Verification
```

不属于主链路、没有明确调用方、只有未来设想的模块不得提前加入 MVP。

### 2.2 明确边界

- API 层只负责协议转换、认证、输入校验和调用 Use Case。
- Application 层负责编排业务流程和事务边界。
- Domain 层保存业务规则，不依赖 FastAPI、PostgreSQL、MCP 或 Playwright。
- Infrastructure 层实现 Repository、Embedding Provider、Git、Tree-sitter 和浏览器适配器。
- Web UI 只消费 API Contract，不复制后端业务规则。

依赖方向：

```text
api/mcp/web -> application -> domain <- infrastructure implementations
```

Domain 不得反向 import Infrastructure。

### 2.3 保持小而可替换

- MUST：一个模块只有一个主要职责。
- SHOULD：函数控制在 40 行以内；超过 60 行需要拆分或说明。
- SHOULD：Python 模块控制在 500 行以内。
- SHOULD：React 页面/组件控制在 400 行以内。
- SHOULD：构造函数依赖不超过 6 个；超过时检查职责是否过多。
- MUST：不得建立通用 `utils.py`、`helpers.py` 垃圾桶；工具按领域命名。

这些数字是审阅阈值，不是机械构建失败条件。

## 3. 目录与模块

推荐结构：

```text
src/operamind/
  domain/
  application/
  api/
  mcp/
  infrastructure/
  contracts/
web/
tests/
  unit/
  integration/
  contract/
  e2e/
```

- MUST：测试目录与生产模块保持可追踪关系。
- MUST：开发脚本进入 `tools/`，不能与生产模块混放。
- MUST：`build/`、缓存、日志、报告、数据库 dump 和编译产物不得提交。
- MUST：同一能力只有一个正式入口；旧入口完成切换后立即删除。

## 4. Python 规范

### 4.1 基础要求

- Python 3.12 或项目锁定版本。
- 所有公开函数、方法和成员必须有类型标注。
- 使用 Pydantic 校验边界数据，Domain Entity 不强制继承 Pydantic。
- 使用 `pathlib.Path` 处理路径。
- 时间统一使用 UTC aware datetime。
- 金额使用 `Decimal`，不得使用 float。
- ID 使用明确类型或命名，禁止在不同实体间复用裸字符串而不校验。

### 4.2 错误处理

- MUST：禁止 `except Exception: pass`。
- MUST：捕获异常后必须处理、转换为领域错误或记录后重新抛出。
- MUST：日志包含 `project_id`、`analysis_case_id`、`snapshot_id` 等关联 ID。
- MUST：不得在日志中输出凭据、完整设计书正文、完整源代码或 embedding。
- SHOULD：预期业务错误使用明确异常类型，不用字符串匹配错误信息。

### 4.3 异步

- 只在真实 I/O 并发场景使用 async。
- 禁止在 async handler 内调用阻塞数据库、HTTP 或 subprocess API。
- CPU 密集的 Parser、Tree-sitter 和批量 embedding 进入 Worker 或受控线程池。

### 4.4 强制工具

```text
Ruff        lint + format
Mypy        strict type checking
Pytest      unit/integration tests
Coverage    changed-code coverage report
```

不得通过全局 `# noqa`、`type: ignore` 或降低规则等级绕过问题。单行例外必须说明原因。

## 5. FastAPI 和 Application 规范

- Router SHOULD 小于 100 行，只做协议层工作。
- Router 不得直接执行 SQL、Git 命令、embedding 或 Playwright。
- Use Case 输入输出使用明确 Command/Result 类型。
- Repository 使用接口隔离，Domain/Application 测试不依赖真实 PostgreSQL。
- 写操作必须有明确事务边界和幂等键。
- API Error 使用统一结构：`code`、`message`、`details`、`trace_id`。
- 分页、排序和过滤必须有上限，不能把数据库全表直接返回给 Web/MCP。

## 6. Canonical Data 和数据库规范

- MUST：Canonical 记录有稳定 ID、版本、来源和创建时间。
- MUST：Snapshot、DocumentVersion、CodeGraphSnapshot 和正式 Report 不原地改写。
- MUST：数据库约束保护跨 Project 引用、状态枚举和唯一键。
- MUST：向量索引表不得成为文档正文的唯一存储位置。
- MUST：不在数据库保存完整目标源代码或完整 Git diff。
- MUST：所有 Schema 变更通过不可修改的 migration。
- MUST：migration 同时提供升级测试；已发布 migration 禁止重写。
- SHOULD：JSON 只保存低频扩展字段，核心查询字段必须规范化。
- SHOULD：Repository 查询使用显式列名，避免 `SELECT *`。

SQL 命名使用 `snake_case`；外键字段使用 `<entity>_id`；时间使用 `*_at`；不可变版本引用使用 `*_version_id` 或 `*_snapshot_id`。

## 7. Contract 规范

- 所有跨模块 Artifact 必须通过 `contracts/` 中的 JSON Schema。
- Contract 必须包含 `artifact_type` 和 `schema_version`。
- v1 默认 `additionalProperties: false`。
- 字段变更必须说明 backward compatibility。
- Contract、Python Model、API 示例和 Golden Dataset 必须在同一个 PR 同步更新。
- AI 输出未经 Contract 校验不得写入 Canonical DB。
- 不为单个内部函数建立 Artifact；Contract 只用于稳定边界。

## 8. 真实 RAG 规范

- MUST：正式分析绑定 Project、Document Snapshot、Embedding Profile 和 Ranking Policy。
- MUST：Vector Search 只返回 Canonical ID、分数和检索通道。
- MUST：Context Rebuilder 按 ID 回查 Canonical DB。
- MUST：keyword-only、Provider failure 和索引不完整必须显式标记 degraded/blocked。
- MUST：不得在正式模式自动回退 fixture 后继续生成可确认报告。
- MUST：embedding 输入有确定性预处理版本和 content digest。
- MUST：凭据来自环境或 Secret Manager，不进入 Profile、日志或数据库。
- SHOULD：查询规划包含业务行为、精确锚点和验收标准三个维度。
- SHOULD：每次 Ranking Policy 变更运行 Golden Dataset Recall@K/MRR 回归。

## 9. Code Graph 规范

- MUST：Code Graph 绑定不可变 Repository Commit。
- MUST：解析和扩展规则来自 Code Framework Profile。
- MUST：通用代码不得出现 `expense`、特定客户目录或业务类名的特殊分支。
- MUST：每条边包含 extractor、置信度和 resolution status。
- MUST：truncated graph 不得成为 current graph。
- MUST：Scope 结果区分 editable、read-only、test 和 unknown。
- SHOULD：范围扩展使用有类型、有方向、有限深度的策略，禁止无界 BFS。
- SHOULD：修改后增量更新图谱，不重复扫描未变化文件。

## 10. MCP 和 Copilot 规范

- Tool 名使用 `<domain>.<verb>_<object>`，保持稳定和可搜索。
- 每个 Tool 明确 read-only、idempotent 和 destructive 属性。
- Tool 输入必须验证 Project、Workspace 和 Git HEAD 绑定。
- 没有 Confirmation 时禁止生成 Edit Packet。
- Edit Packet 必须包含 Base SHA、白名单、只读文件、禁止路径和越界策略。
- Copilot 修改阶段不得重新请求完整 Context Package。
- 修改结果必须通过 `git diff --name-only` 与白名单比较。
- 超出范围返回 `reanalysis_required`，不得静默扩大权限。

## 11. React 和 TypeScript 规范

- TypeScript 开启 `strict`，禁止无理由使用 `any`。
- API 类型从 Contract/OpenAPI 生成或集中维护，页面不得手写不同版本。
- 组件使用函数组件和明确 Props。
- 页面负责组合，业务逻辑进入 hook/service，格式化进入纯函数。
- 远程数据状态必须区分 loading、empty、error、stale 和 ready。
- 不把密码、Token 或敏感环境配置写入 `localStorage`。
- 按钮、表单、状态和错误必须有可访问名称。
- 禁止在组件中拼接未经转义的 HTML。

前端强制工具：ESLint、Prettier、TypeScript typecheck、Vitest；关键工作流使用 Playwright。

## 12. Playwright UI 测试规范

- 场景名称使用业务行为，不使用实现函数名。
- Locator 优先级：role/label/text -> stable business attribute -> test id -> CSS fallback。
- 禁止依赖脆弱的深层 CSS、随机 sleep 和固定等待时间。
- 使用可观察条件等待网络、页面状态或业务结果。
- 每个场景声明数据前置条件、环境、步骤、可见结果和证据。
- 失败必须分类为 business、environment、test_data、locator、authentication 或 blocked。
- 测试不得依赖执行顺序；数据由 Data Recipe 创建并清理。
- Screenshot 不能代替业务断言；通过必须有机器可判断的 assertion。

## 13. 测试规范

### 13.1 分层

- Unit：Domain、Query Planner、Stable Key、Ranking 和 Scope 规则。
- Contract：JSON Schema、API、MCP Tool 输入输出。
- Integration：PostgreSQL、pgvector、Git、Tree-sitter 和 Provider Adapter。
- Golden：真实设计书到期待 RAG/Code Scope/UI Scenario。
- E2E：文档导入到 UI Validation Closure。

### 13.2 规则

- 每个缺陷修复先增加能够复现问题的测试。
- 测试名称描述条件和期待结果。
- Unit Test 禁止真实网络、真实时间和随机不固定种子。
- Embedding Unit Test 使用确定性 Fake；真实 Provider 只在受控 Integration/Live Test 中运行。
- 禁止通过删除断言、扩大容差或修改 Golden 期待值迁就实现。
- flaky test 必须隔离并登记原因，不能无限重试掩盖。

## 14. 安全规范

- Secrets 只通过环境、OS Keychain 或 Secret Manager。
- 仓库必须提供 `.env.example`，不得提供真实值。
- 所有文件路径必须规范化并限制在批准根目录。
- subprocess 使用参数数组和命令白名单，禁止拼接不可信 shell 字符串。
- 上传文档校验大小、类型、压缩炸弹和恶意路径。
- 日志和 Evidence 在保存前执行敏感信息过滤。
- MCP 写工具和 UI 执行需要显式授权和审计记录。

## 15. Git 和依赖规范

- 分支保持单一目的，提交保持可审阅。
- Commit 使用 `type(scope): summary`，例如 `feat(rag): add snapshot vector index`。
- 不提交生成物、IDE 状态、二进制依赖、数据库 dump 或真实 `.env`。
- 依赖必须锁定版本，新增依赖需说明用途、许可证、维护状态和替代方案。
- 禁止为了一个简单函数引入大型框架。
- 重构和行为变化尽量分开提交。

## 16. 日志和可观测性

- 使用结构化日志，不用散落的 `print`。
- 每个请求携带 `trace_id`。
- 长任务记录 batch/case/snapshot/repository revision 和阶段状态。
- 指标至少覆盖 ingestion、embedding、retrieval、scope、Copilot validation 和 UI run。
- 错误日志必须可定位且不泄露正文或凭据。

## 17. Definition of Done

合并前必须满足：

- [ ] 功能属于当前 MVP/完整版范围。
- [ ] 没有项目领域硬编码和静默降级。
- [ ] Ruff/ESLint/format 通过。
- [ ] Mypy/TypeScript typecheck 通过。
- [ ] Unit、Contract 和相关 Integration Test 通过。
- [ ] 相关 Golden Dataset 回归通过。
- [ ] Migration 可在空库和上一版本升级。
- [ ] Contract、文档、手动期待值同步更新。
- [ ] 没有秘密、生成物和大日志进入 Git。
- [ ] 新增失败路径有明确状态、日志和用户可见说明。

## 18. 例外流程

规范例外必须在 PR 中写明：违反的规则、原因、风险、临时保护和删除期限。没有期限的“临时例外”不得合并。
