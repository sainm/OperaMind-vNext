# 实施路线

## 当前状态

P0-P6 的 MVP 主链路已经实现。Golden Dataset、真实 Embedding Provider、人工 Approval 和 target Deployment E2E 有既存通过 Evidence；健壮性修改后旧完整本地回归 Evidence 的 source digest 已失效，必须在 WSL + Podman + Edge 中重建。真实 VS Code GitHub Copilot 会话 receipt 对应的 `github_copilot_live` 也仍为 `pending`。详细未完成事项与验收条件见 [后续任务清单](NEXT-TASKS.md)。

## 1. 开始前

- 选择一个真实项目中的 1–5 个可复核案例；需要扩大泛化覆盖时再增加不同项目。
- 为每个项目准备 before/after 文档、固定代码 commit、正确影响文件和 UI 场景。
- 人工审阅并冻结 Golden Dataset v1。
- 确认 Embedding Provider、目标 Web 环境和测试数据准备方式。

## 2. MVP 阶段

### P0：契约和数据基线

已完成：冻结二十五个核心 Artifact v1，建立 PostgreSQL migration、Repository round-trip 和 Golden Dataset 校验入口。

### P1：动态设计书写法和语义 Diff

已完成：实现 Convention Profile 多 Variant、低置信度审阅、Stable Key 和结构变化不变性测试。

### P2：真实 RAG

实现已完成：Embedding Provider、增量索引、Snapshot 门禁、hybrid retrieval、Canonical rehydration、Golden 真实检索命令、持久化质量报告和 Impact fail-closed 门均已落地；真实 Provider gate 已通过，冻结 Query 的 PostgreSQL/pgvector 回归也已通过。VisionDemo 运维数据库上的正式 Report 仍需在该 Canonical Snapshot、当前本地 Embedding Profile 和 ready/current Index 可用后采集。

### P3：Code Graph Scope

Java 及 JavaScript／TypeScript、Python、Kotlin 的通用 Tree-sitter Adapter，以及 Struts 1 的 ActionMapping／ActionForm／ActionForward、ActionServlet、Tiles/JSP Framework Adapter 已完成：实现 Framework Profile、稳定 Symbol、Import／Call／Inheritance、`exposes`／`maps_to`／`navigates_to`、双向范围查询、Test Binding 和增量／全量一致性。当前还包含 Runtime Route Evidence、静态／运行时 provenance，以及通用 Unresolved Evidence 管理闭环：每个新 Graph 自动生成不可变分类 Report，唯一证明才关闭，历史不覆盖，并提供 CLI 与日文 Web 管理页面。

### P4：Impact、MCP 和 Copilot

Impact Report、确认、Edit Packet、Approval Grant、Workspace 校验、版本固定的安全命令执行、真实命令证据绑定和 Edit Result 已实现。MCP 现有十三个工具：九个兼容细粒度工具，加上从 `CopilotCodingTask` 推导范围并自动回传测试、path-only Diff 和 committed 结果的四个 Coding Task 工具。日文 Web 可以把已批准 ChangeSession 发布到 loopback local Bridge；VS Code 扩展显示一键确认并打开 GitHub Copilot Coding Plan。当前产品路线固定使用 VS Code 上的 GitHub Copilot、local Bridge、MCP 和隔离 worktree，不实现远程生产 API Provider；`coding_task_provider_v1` 只保留为边界契约。分析启动属于控制面，人类确认留在可信 Web/VS Code，原始 UI 结果只由绑定 Deployment 的 Browser Executor/Recovery 写入。真实 GitHub Copilot 完成会话仍需外部 receipt。

### P5：UI 验证

Scenario 关联、Deployment、版本化 UI Knowledge、Canonical `screen_element` 到 draft candidate/issue 的确定性提案、追加式 runtime page observation、新 draft 版本、追加式 approve/reject 审核、业务目标到 Locator 的审核后解析、自动 Browser Preflight、approved Browser Manifest、受限 Playwright Runner、Run/Evidence、Impact coverage 和 Change Validation 已实现。日文 UI Knowledge 与 Approval Grant 管理界面均已实现；PostgreSQL 完整性与真实 Chromium approve/reject E2E 已纳入回归。VisionDemo 的固定 committed Revision 已完成正式 target Deployment、真实 Chrome 三场景和 TestPlan Case 显式映射验证，`target_deployment_e2e` gate 已通过。跨角色 Golden E2E 审核是非阻断治理任务，共享 Object Storage Adapter 是 MVP 后实现任务。

### P6：E2E

当前已冻结 1 个由既存 VisionDemo Silver 检查结果支撑、并在 Codex 对话中完成人工判断的 Golden 案例。UI 期待值 Schema、Project/Case 绑定、Scenario 唯一性、基线 coverage 和 readiness 门已实现。TestDataPlan 的顺序化 fixture/API/SQL/UI 执行、变量传递、断言、失败停止、清理与 Evidence，以及 fail-closed ChangeClosureResult 已实现。VisionDemo 已完成正式 target Deployment Binding、跨画面关联数据、失败 cleanup、真实 UI 和 100% 业务 Coverage 的 Canonical Closure。业务责任人／开发／QA 分角色审核仍可补充，但当前 Golden gate 已通过，不应描述为 Golden 晋级阻断。

跨画面关联数据已抽取为版本化 `BusinessDataTemplate`，支持主从实体、参数前置条件、共享变量、确定性生成顺序、最终断言与逆序 cleanup，并将 Template Version 和无值参数清单写入 TestDataPlan。日文失败管理页统一显示 TestData、UI、Cleanup、Coverage、Closure 的 Canonical 原因；只有服务器确认 stale 的 Run 才显示理由付き恢复入口，只有授权仍有效的终态 Run 才显示新 Run 重跑入口。

生成 Test Case 的自然语言一括修订闭环已实现：一条自然语言可同时修改多个 Case 的业务可见步骤、测试数据、期待结果和业务断言；确定性修改也先展示整体差异，全部歧义在同一画面选择后一次生成新的 TestPlan 与全部下游 Artifact Version，无法安全解释时整体阻断。旧 Run、Evidence、Screenshot、Coverage 和 Closure 保留为 stale 历史，不能充当新版本验证结果。撤销通过补偿性 `undo` Revision 恢复前一版本内容，不改写历史；恢复或改订后的 Version 均可在范围复核后以新 Run 重新执行。日文 Web 页面、API、PostgreSQL supersession／undo／replay／race 保护和真实 Chromium E2E 均纳入回归。

修订后的重新执行闭环已实现：按 TestDataPlan、UI Scenario 和执行边界比较新旧 Version；范围不变时审计式复用已完成 Grant，范围变化时在 Web 阻断并要求重新确认。随后创建新 Run、脱敏 Evidence 和 ChangeClosureResult，并在日文页面展示改订前后的运行、Coverage、测试数量和 Closure 差异。PostgreSQL 集成测试分别覆盖自动复用与重新确认路径，浏览器测试覆盖确认、启动和差分展示。

Committed Edit Result 还会保存基于 Git 追加／修改行的 `ChangedLineCoverageReport`。报告交叉校验可执行行与已覆盖行，默认以 80% 为阈值；缺少证据或低于阈值时，Change Closure 为 `blocked`，日文 Web 同时展示文件、行号、阈值和阻断原因。Business Coverage（业务规则覆盖率）仍独立计算，低于 100% 同样阻断 Closure。

Repository-wide `--require-mvp-ready` 门禁已实现。除冻结 Golden Dataset 外，它还要求真实 Embedding Provider、真实人工批准闭环、GitHub Copilot 会话、绑定 target Deployment E2E 和完整 PostgreSQL/Edge 回归分别提供类型匹配且 SHA-256 固定的证据；人工语义决定记录 `reviewed`，确定性完整回归由 readiness 命令记录 `verified`。当前 source tree 需要重新生成 WSL 全回归 Evidence，`github_copilot_live` 也仍为 pending，因此命令失败是预期行为；代码实现或 Fake 测试不能替代真实证据。

Web 全站视觉系统也已完成：五个工作区共用设计 Token、状态与反馈语义，变更工作台优先显示当前变更、待确认、阻断、进度和下一步。复杂 Test Case／TestDataPlan／ChangeClosureResult 支持折叠分组，危险操作二次确认，主要页面有视觉基线，并在 390～1440px 的代表性宽度验证无横向溢出。

## 3. 完整版增量

- Canonical Profile Registry 和 Profile Drift。
- 文档拆分、合并和复杂移动。
- 大批量 Change Group、Coverage Ledger 和全局修改计划。
- 生产级 ANN、模型切换、成本和质量监控。
- Packet 级 UI Smoke、Red Test 和受影响范围回归。
- 多租户权限、恢复、审计和容量治理。

## 4. 工期

| 版本 | 人日 | 一人全职 |
|---|---:|---:|
| MVP | 32-50 | 7-11 周 |
| 完整版总量 | 67-103 | 14-21 周 |
| MVP 后升级 | 35-53 | 7-11 周 |

## 5. 阶段交付物

每个阶段必须同时提交：实现、migration、核心契约更新、自动测试、手动期待值、运行说明和已知限制。不得把文档和 Golden Dataset 留到最后补写。
