# 系统架构

## 1. 目标

系统把不统一写法的设计书变化转化为可解释、可批准、可验证的代码修改范围。核心流程固定，设计书写法、Embedding Provider、代码框架和 UI 执行策略通过 Profile 适配。

## 2. 组件

```text
React Operations Web
        |
FastAPI Control Plane / MCP Server
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
- CodeGraphSnapshot、CodeFile、CodeSymbol、CodeEdge 和 Test Binding
- ImpactReport、Confirmation、EditPacket 和 EditResult
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
- MCP：向 VS Code 提供 ready case、Impact Report、确认和 Edit Packet 工具。
- VS Code Copilot：读取本地批准文件并修改代码，不负责定义影响范围和最终验收。
- Playwright Runner：在绑定的 Build/Deployment 上执行 OperaMind 生成或复用的 UI 场景。

## 6. 可复现性

每次正式分析必须绑定：

- Document Snapshot ID
- Document Profile Version
- Embedding Profile 和 Ranking Policy Version
- Code Graph Snapshot ID
- Repository Commit SHA
- Impact Policy Version
- UI Environment 和 Deployment Revision
