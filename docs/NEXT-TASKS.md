# 后续任务清单

本文只记录当前代码与证据尚未完成的事项。状态以 `readiness/mvp-readiness.json`、Canonical PostgreSQL、不可变 Evidence 和当前测试结果为准；示例代码、Fake 测试或文档声明不能替代真实证据。

## 当前结论

- P0-P6 的 MVP 主链路已经实现：文档导入／Diff、真实 RAG、Code Graph、Impact／Approval、VS Code GitHub Copilot handoff、测试数据、Playwright UI 验证和 Change Closure 均有代码与自动测试。
- Golden Dataset 已冻结 1 个 VisionDemo 案例；真实 Embedding Provider、人工 Approval 和 target Deployment E2E 有既存通过证据。
- Web 已完成全站视觉系统、变更工作台、复杂表单分组、危险操作确认、响应式适配和主要页面视觉回归。本轮实现统一收敛到一个 Git 源码基线；后续 readiness Evidence 必须绑定该基线或其明确后继提交，不在文档中手工固定尚未生成的 commit SHA。
- 健壮性修改后的 source tree 已使旧 `full_local_regression` Evidence 失效；必须在 WSL + Podman + Microsoft Edge 中重新生成。`github_copilot_live` 仍为外部阻断 gate。因此当前不能宣称 repository-wide readiness stage 有效或达到 `mvp_ready`。

可用以下命令重新确认当前 gate，不应手工推断状态：

```bash
.venv/bin/operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --readiness-manifest readiness/mvp-readiness.json \
  --print-readiness-json
```

## A. MVP 阻断任务

### A1. 重新生成 WSL 全回归 Evidence

**现状**：当前 source tree digest 与旧 `full_local_regression` Evidence 不一致；macOS 本地单元与静态检查不能替代 WSL + Podman + Edge 证据。

**完成标准**：在 WSL 中启动安装脚本后，执行 `bash scripts/regenerate-readiness-wsl.sh visiondemo visiondemo-expense-status-filter`，确认 Podman PostgreSQL、全部非排除测试和 Playwright `msedge` 均通过，并由命令更新 Evidence 与 manifest；禁止手工改 digest。

### A2. 完成真实 VS Code GitHub Copilot 会话证据

**现状**：`github_copilot_live` 仍为 `pending` gate。代码、VS Code 扩展、local Bridge、MCP Coding Task 工具和自动测试已经存在，但它们不能证明真实 Copilot 完成过会话。

**执行条件**：VS Code GitHub Copilot 可用，并能导出对应的 `workspaceStorage` 会话 JSONL。

**完成标准**：

1. 登录状态下的非 BYOK GitHub Copilot 请求使用同一个 Canonical Coding Task。
2. 会话完整且用户确认执行 `copilot_get_coding_task`、`copilot_run_task_command`、`copilot_validate_task_diff`、`copilot_record_task_result`。
3. `operamind-readiness inspect-copilot-session` 验证通过。
4. `operamind-readiness record-copilot-session` 将脱敏 receipt 绑定到 committed、范围内且命令验证通过的 Edit Result。
5. `operamind-baseline --require-mvp-ready` 通过，readiness stage 变为 `mvp_ready`。

## B. 非阻断验证任务

### B1. 生成正式 Golden RAG 质量观测

**现状**：`0055`、`GoldenRagQualityReport`、`operamind-run-golden-rag`、逐 Query 结果、质量指标和 Impact 双重 fail-closed 门禁已经实现；冻结 Query 的 PostgreSQL 18 + pgvector 集成回归已通过。当前工作区没有已填充 VisionDemo Canonical Snapshot 的运维数据库和正在运行的本地 Embedding Provider，因此尚未生成该实际环境的正式 Report。

**完成标准**：在 VisionDemo 的固定 Snapshot、当前 Embedding Profile 和 current/ready Search Index 上运行 `operamind-run-golden-rag`，持久化 `GoldenRagQualityReport`，全部阈值通过且无跨项目泄漏；该 Report 必须来自真实本地 Embedding Provider，不能用确定性测试 Provider 作为运维 Evidence。

### B2. 补充跨角色 Golden E2E 审核

**现状**：当前 Golden 案例由对话中的人工判断冻结，Golden gate 已通过；尚未分别记录业务负责人、开发和 QA 的产品化审核结论。

**完成标准**：三类角色分别确认来源身份、预期变化与 RAG、代码范围、UI 场景和 target Deployment closure；审核身份、时间、判断与证据摘要可追溯。该任务用于提升治理质量，不应把已经通过的 Golden gate 误写成 `pending`。

## C. MVP 后实现任务

### C0. Web UI 模块化与变更行覆盖率（本轮目标）

**状态**：已实现代码与契约；WSL/Podman/Edge 环境迁移和真实数据库回归不属于本轮范围。

**完成内容**：Web 显示模型已拆分为变更管理、测试数据、Case 编辑和验证结果模块，并以 Node 调用方式进行前端单元测试。Committed Edit Result 现在根据 Git 变更行和批准命令提供的可执行／已覆盖行计算 `ChangedLineCoverageReport`；缺少证据或低于阈值会让 `ChangeClosureResult` 保持 `blocked`，日文页面展示文件、行号和阻断原因。

### C0.1. 变更链路追踪与遗漏识别（本轮追加目标）

**状态**：已实现后端只读追踪图、遗漏台账、API 路由和日文 Web 展示；未改变现有批准或执行状态机。

**完成内容**：`GET /api/v1/change-requests/{request_id}/traceability` 将设计书差异、Impact、代码范围、検証基準、Test Case、跨画面 TestDataPlan、Business Coverage、UI Scenario／結果、Committed Edit Result 和 ChangeClosureResult 统一为关系图。缺少关系、执行结果、Evidence 或 Closure 阻断原因时返回 `gaps`，页面按工程阶段显示节点、关系和必须确认的不足。后端纯函数、API 假服务和 Node 前端显示模型均有测试覆盖。

### C0.2. Artifact 契约版本兼容（本轮追加目标）

**状态**：已实现 ChangeClosureResult v1/v2 兼容读取和 fail-closed 投影。

**完成内容**：历史 v1 Artifact 保持不可变且继续通过原始契约读取；包含变更行覆盖率的新结果改为 v2。旧 v1 Closure 在当前读取模型中显式标记为 stale，并以缺少变更行覆盖率的 blocked 状态展示，要求重新评价，不回写或伪造历史 Evidence。契约目录会同时校验全部已提供的版本化示例。

### C0.3. Web 全站视觉系统与工作台

**状态**：已实现并纳入视觉回归。

**完成内容**：统一颜色、字号、间距、按钮、表单、卡片、抽屉、状态标签和交互反馈；在变更、测试、证据工作区增加当前变更、待确认、阻断、进度和下一步摘要。Test Case、TestDataPlan 和 ChangeClosureResult 支持分组折叠，审批操作区可固定显示，停止、取消和撤销版本使用危险操作样式与二次确认。五个工作区在 390／768／1024／1366／1440px 下检查横向溢出，并保存变更、测试、证据、运维、设置和移动导航视觉基线。

### C1. 扩展多语言 Code Graph Adapter

**状态**：已实现。

JavaScript／TypeScript（含 TSX）、Python、Kotlin 已增加独立 Tree-sitter grammar 和统一 `SemanticAdapterRegistry`，支持 declaration、import、call、inheritance/implements 与 Test Binding。多语言真实源码 fixture 覆盖 resolved、external、unresolved 和增量／全量一致性；Profile 缺少语言专用 extractor 或源码语法错误时继续 fail closed。

### C1.1. Struts 1 Code Graph Adapter

**状态**：已实现。

`struts1_mvc` 已解析 `struts-config.xml`、ActionServlet URL mapping、Action／Form／Forward、Java `findForward()`、Tiles 和 JSP Struts tag，生成可追踪的 `exposes`、`calls`、`maps_to`、`navigates_to`。动态 route、DispatchAction 和外部类型保持 unresolved/external；配置变更增量图与全量图一致，错误 XML、内部实体和重复定义 fail closed。

### C2. 实现共享 Evidence Store

在现有 path-confined Local Evidence Store 接口后增加 S3 兼容 Adapter，保留 opaque ref、SHA-256、Project／Run scope、脱敏标志和读取时 digest 校验；不得把对象存储内容当作 Canonical 状态。

### C3. 生产 Coding Task API Provider

**状态**：当前路线取消，不列为待完成任务。

本工程固定使用 VS Code 上的 GitHub Copilot、local Bridge、MCP 和隔离 worktree 完成代码修改，不建设远程代码编辑服务。`coding_task_provider_v1` 继续保留为边界契约，只有未来明确改变产品路线时才重新评估生产 API Provider。

### C4. 实现 Canonical Profile Registry 与 Drift 检测

**状态**：已实现代码、迁移、API、Worker 和日文 Web 管理入口；0054 迁移、单 Artifact 自动解除、五阶段依赖、失败阻断与整批重试已通过临时真实 PostgreSQL 回归。

**完成内容**：Registry 统一列出 Document Convention、Document Relation、Embedding、Code Framework、Command Execution 和 UI Locator Profile 的不可变 Version 与 Project Binding。激活 Version 时自动生成 Drift Event，并沿 Canonical 关系识别 Document／UI Knowledge／Code Graph／Search Index Snapshot、ImpactReport、TestPlan／UiExecutionPlan、Edit／Command／TestData／UI Evidence 和 ChangeClosureResult；影响台账分别标为 `stale` 或 `blocked`。Impact 确认、基于旧 Snapshot 的新 Impact 和 TestData 执行会 fail closed，历史 Closure 读取时投影为 stale／blocked。一次 Web 请求为全部未解决影响创建 Batch，Worker 按 Snapshot → Impact → TestPlan → Evidence → Closure 的数据库依赖顺序执行。服务端只接受通过 Canonical 验证的新 Artifact，写入 old→new 替代关系后逐项解除 Drift；失败保持阻断，并可由操作者填写理由后重试。已批准并激活的 UI Knowledge 会自动登记对应的 `UiLocatorProfile`，不再把 Locator 版本留在独立旁路。

## 有意不开放的能力

- 不向 MCP／Copilot 开放原始 UI Verification Result 写入。原始浏览器结果只允许由绑定 Deployment 的 Browser Executor 或带操作者与理由的 Recovery 写入。
- 不让 Scope Resolver 自行批准 Impact 或修改。Resolver 只生成候选台账；正式确认、Edit Packet 和 Approval Grant 由 P4 的可信 Web／CLI 状态机完成。
- 不把 Silver、AI 候选、Fake 测试或未审阅 Evidence 自动升级为 Golden／passed。
