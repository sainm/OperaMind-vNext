# 实施路线

## 1. 开始前

- 选择一个真实项目中的 1–5 个可复核案例；需要扩大泛化覆盖时再增加不同项目。
- 为每个项目准备 before/after 文档、固定代码 commit、正确影响文件和 UI 场景。
- 人工审阅并冻结 Golden Dataset v1。
- 确认 Embedding Provider、目标 Web 环境和测试数据准备方式。

## 2. MVP 阶段

### P0：契约和数据基线

冻结二十三个核心 Artifact v1，建立 PostgreSQL migration、Repository round-trip 和 Golden Dataset 校验入口。

### P1：动态设计书写法和语义 Diff

实现 Convention Profile 多 Variant、低置信度审阅、Stable Key 和结构变化不变性测试。

### P2：真实 RAG

实现 Embedding Provider、增量索引、Snapshot 门禁、hybrid retrieval、Canonical rehydration 和 Recall@K 验收。

### P3：Code Graph Scope

实现 Framework Profile、Tree-sitter Snapshot、双向范围查询和 Test Binding。当前还包含 Runtime Route Evidence、静态／运行时 provenance，以及通用 Unresolved Evidence 管理闭环：每个新 Graph 自动生成不可变分类 Report，唯一证明才关闭，历史不覆盖，并提供 CLI 与日文 Web 管理页面。

### P4：Impact、MCP 和 Copilot

Impact Report、确认、Edit Packet、Approval Grant、Workspace 校验、版本固定的安全命令执行、真实命令证据绑定和 Edit Result 已实现。MCP 现有十三个工具：九个兼容细粒度工具，加上从 `CopilotCodingTask` 推导范围并自动回传测试、path-only Diff 和 committed 结果的四个 Coding Task 工具。日文 Web 可以把已批准 ChangeSession 发布到 loopback local Bridge；VS Code 扩展显示一键确认并打开 GitHub Copilot Coding Plan。POC 固定使用 local Bridge，生产 API Provider 复用 `coding_task_provider_v1`，尚未实现。分析启动属于控制面，人类确认留在可信 Web/VS Code，原始 UI 结果只由绑定 Deployment 的 Browser Executor/Recovery 写入。真实 GitHub Copilot 完成会话仍需外部 receipt。

### P5：UI 验证

Scenario 关联、Deployment、版本化 UI Knowledge、Canonical `screen_element` 到 draft candidate/issue 的确定性提案、追加式 runtime page observation、新 draft 版本、追加式 approve/reject 审核、业务目标到 Locator 的审核后解析、自动 Browser Preflight、approved Browser Manifest、受限 Playwright Runner、Run/Evidence、Impact coverage 和 Change Validation 已实现。日文 UI Knowledge 审核界面可比较业务目标、Locator 候选、匹配／可视数量、可靠度、issue 和元素级 Evidence Screenshot，并以确认者和理由生成新的 approved/rejected Snapshot Version；PostgreSQL 完整性与真实 Chromium approve/reject E2E 已纳入回归。VisionDemo 的固定 committed Revision 已完成正式 target Deployment、真实 Chrome 三场景和 TestPlan Case 显式映射验证。跨角色审核済み Golden E2E 仍是下一实现边界。

### P6：E2E

选定的 1–5 个 Golden Dataset 案例从文档导入运行到 UI 关闭，并提供一键及手动期待值。UI 期待值 Schema、Project/Case 绑定、Scenario 唯一性、当前基线结果 coverage 和 readiness 门已实现；当前已冻结 1 个由既存 VisionDemo Silver 检查结果支撑、并在 Codex 对话中完成人工判断的案例。TestDataPlan 的顺序化 fixture/API/SQL/UI 执行、变量传递、断言、失败停止、清理与 Evidence，以及 fail-closed ChangeClosureResult 已实现。VisionDemo 已完成正式 target Deployment Binding、跨画面关联数据、失败 cleanup、真实 UI 和 100% 业务 Coverage 的 Canonical Closure；业务责任人／开发／QA 跨角色审核后的 Golden 闭环仍待完成。

跨画面关联数据已抽取为版本化 `BusinessDataTemplate`，支持主从实体、参数前置条件、共享变量、确定性生成顺序、最终断言与逆序 cleanup，并将 Template Version 和无值参数清单写入 TestDataPlan。日文失败管理页统一显示 TestData、UI、Cleanup、Coverage、Closure 的 Canonical 原因；只有服务器确认 stale 的 Run 才显示理由付き恢复入口，只有授权仍有效的终态 Run 才显示新 Run 重跑入口。

生成 Test Case 的自然语言一括修订闭环已实现：一条自然语言可同时修改多个 Case 的业务可见步骤、测试数据、期待结果和业务断言；确定性修改也先展示整体差异，全部歧义在同一画面选择后一次生成新的 TestPlan 与全部下游 Artifact Version，无法安全解释时整体阻断。旧 Run、Evidence、Screenshot、Coverage 和 Closure 保留为 stale 历史，不能充当新版本验证结果。撤销通过补偿性 `undo` Revision 恢复前一版本内容，不改写历史；恢复或改订后的 Version 均可在范围复核后以新 Run 重新执行。日文 Web 页面、API、PostgreSQL supersession／undo／replay／race 保护和真实 Chromium E2E 均纳入回归。

修订后的重新执行闭环已实现：按 TestDataPlan、UI Scenario 和执行边界比较新旧 Version；范围不变时审计式复用已完成 Grant，范围变化时在 Web 阻断并要求重新确认。随后创建新 Run、脱敏 Evidence 和 ChangeClosureResult，并在日文页面展示改订前后的运行、Coverage、测试数量和 Closure 差异。PostgreSQL 集成测试分别覆盖自动复用与重新确认路径，浏览器测试覆盖确认、启动和差分展示。

Repository-wide `--require-mvp-ready` 门禁已实现并默认失败。除冻结 Golden Dataset 外，它还要求真实 Embedding Provider、真实人工批准闭环、GitHub Copilot 会话、绑定 target Deployment E2E 和完整 PostgreSQL/真实 Chrome 回归分别提供类型匹配且 SHA-256 固定的证据；人工语义决定记录 `reviewed`，确定性完整回归可由 baseline 记录 `verified`。代码实现或 Fake 测试不能替代这些证据。

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
