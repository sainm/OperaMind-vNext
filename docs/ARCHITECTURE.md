# 系统架构

## 1. 目标

系统把不统一写法的设计书变化转化为可解释、可批准、可验证的代码修改范围。核心流程固定，设计书写法、Embedding Provider、代码框架和 UI 执行策略通过 Profile 适配。

## 2. 组件

```text
Browser Operations Web (HTML/CSS/JavaScript)
        |
FastAPI Control Plane -- local Bridge --> VS Code Extension --> GitHub Copilot
        |                                                    |
        +-------------------- MCP Server <-------------------+
        |
        +-- Document Ingestion and Semantic Diff
        +-- Embedding Index and Context Rebuilder
        +-- Tree-sitter Code Graph and Scope Resolver
        +-- Impact, Approval and Edit Packet
        +-- UI Verification Orchestrator
        |
PostgreSQL + pgvector             Playwright Runner
```

MVP 默认使用一个 Python Control Plane，避免同时维护 Java 和 Python 两套业务后端。Java、Spring、React、JSP 等是目标仓库框架，不是 OperaMind 自身必须采用的技术。

## 3. 数据面

`Canonical Data`

- Project、Repository 和不可变 Revision
- Document、DocumentVersion、DocumentNode、Fact、Relation 和 Snapshot
- StructuredChange 和 ContextPackage
- CodeGraphSnapshot、CodeFile、CodeSymbol、CodeEdge、Test Binding、RuntimeRouteEvidence 和 UnresolvedEvidenceReport 历史
- ImpactReport、Confirmation、EditPacket 和 EditResult
- CopilotCodingTask、Bridge Event、Command/Edit Result 绑定
- OrchestrationTask、Dependency、Claim Lease、Result 和 Event 台账
- VerificationScenario、UiExecutionRun、Evidence 和 ValidationResult

`Derived Index`

- Section/Slice embedding
- Keyword ranking text
- ANN index
- 可删除后从 Canonical Data 重建

`Local Workspace`

- 目标仓库源代码和 Git diff
- 不将完整源代码、完整 diff 或凭据写入 OperaMind DB

## 4. 控制面状态

```text
ingesting
  -> indexing_rag
  -> ready_for_impact
  -> analyzing
  -> awaiting_confirmation
  -> editing
  -> verifying_ui
  -> passed | failed | reanalysis_required
```

只有可索引节点 embedding 覆盖率为 100%，Analysis Case 才能进入 `ready_for_impact`。任何降级、未知高影响项、过期 Git SHA 或范围外修改都必须阻断后续自动步骤。

## 5. Web、MCP 和 VS Code

- Web：项目接入、文档导入、报告审阅、批准、UI 执行和证据查看。
- Local Bridge：Web 只向匹配当前 Workspace 的 VS Code 扩展发布任务 ID 和通知；Bearer Token 只保存在 VS Code SecretStorage，入口只允许 loopback。Task claim 使用 60 秒 lease，heartbeat 续租；断线后可按持久化 Task ID resume，lease 失效后由同 Workspace 新 consumer 追加 `claim_recovered` 并接管。用户在 VS Code 确认后 MCP 才能取得任务上下文。取消保留终端 Event，重试创建带 retry lineage 和 attempt number 的新 Task，不改写旧 Task。
- MCP：当前通过 stdio 提供十三个有界工具。九个兼容细粒度工具处理 ready case、Report、Packet、Grant、命令、Diff、UI Plan 和 Validation Result；四个 Coding Task 工具从任务身份推导全部范围，并把测试摘要、path-only Diff 和 committed 结果自动回传 Web。分析启动、人类确认、UI 原始结果写入仍不开放给模型。
- VS Code Copilot：按 `copilot_coding_plan` 读取本地批准文件并修改代码，不负责定义影响范围和最终验收。
- Provider Boundary：`CopilotCodingTask` 的 `coding_task_provider_v1` 使用 `local_bridge`；当前产品路线固定为 VS Code 上的 GitHub Copilot，不计划实现远程生产 `api_provider`。
- Playwright Runner：在绑定的 Build/Deployment 上执行 OperaMind 生成或复用的 UI 场景。

Change Automation 的每个当前 Action 会同步为 Agent-neutral `OrchestrationTask`。Task Definition 只声明 Capability、输入输出、依赖和验收条件；Agent、Subagent、人工使用同一 Claim／Lease／Result 协议，具体执行者不进入业务状态机。当前 Scheduler 对每个 Automation Run 只允许一个活动 Claim。未来启用多 Subagent 时只调整 Capability 匹配和并发策略。人工判断仍由 Canonical 人工确认 Artifact 验收，领取 Task 本身不等于批准。CopilotCodingTask 是代码修改 Action 的 local Bridge 配送适配器，不替代通用任务协议。

Profile Drift 再构筑使用独立 `ProfileRebuildBatch`，但复用相同的 Worker 身份、短期 Claim、Lease、Heartbeat 和固定命令 Handler。一个 Batch 为所有未解决影响创建 Request，并用数据库依赖屏障固定 `Snapshot → Impact → TestPlan → Evidence → Closure`。Worker 只能申报替代 Artifact 的类型与 ID；服务端从 Canonical 表重新验证同项目、同类型、业务状态、当前 Profile Binding 和未受新 Drift 影响后，才追加 old→new 替代关系并解除对应 `stale`／`blocked`。任一阶段失败或验收不通过都会保持原影响未解决、阻断后续阶段，并要求带操作者和理由的显式 Requeue。

## 6. 可复现性

每次正式分析必须绑定：

- Document Snapshot ID
- Document Profile Version
- Embedding Profile 和 Ranking Policy Version
- Code Graph Snapshot ID
- Repository Commit SHA
- Impact Policy Version
- UI Environment 和 Deployment Revision
