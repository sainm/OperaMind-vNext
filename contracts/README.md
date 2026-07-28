# Core Artifact Contracts

本目录保存 vNext 主链路的二十六个核心 JSON Schema：

1. `DocumentIngestionResult`
2. `StructuredChange`
3. `ContextPackage`
4. `CodeGraphSnapshot`
5. `ImpactReport`
6. `ImpactConfirmation`
7. `CopilotEditPacket`
8. `ApprovalGrant`
9. `UiVerificationResult`
10. `ChangeRequest`
11. `DocumentChangeProposal`
12. `TestPlan`
13. `TestDataPlan`
14. `BusinessDataTemplate`
15. `AcceptanceCriteria`
16. `BusinessCoverageReport`
17. `ChangeClosureResult`
18. `ChangeOrchestrationPlan`
19. `TestDataExecutionResult`
20. `TestCaseChangeProposal`
21. `TestCaseRevision`
22. `CopilotCodingTask`
23. `RuntimeRouteEvidence`
24. `UnresolvedEvidenceReport`
25. `GoldenRagQualityReport`
26. `ChangedLineCoverageReport`

Contract 用于 API、MCP、数据库 Repository、Golden Dataset 和 UI 之间的边界校验。数据库规范化表不是由这些 JSON 代替；Artifact 只提供稳定交换格式。

规则：

- 所有 Artifact 必须包含 `artifact_type` 和 `schema_version`。
- v1 默认拒绝未知字段。
- ID 只引用 Canonical Data，不把完整源代码或完整文档塞入 Artifact。
- Contract 变更必须更新 Golden Dataset，并说明兼容策略。

当前仓库仍为 `0.1.0.dev0`、尚未发布。DocumentIngestionResult v1 固定状态事件 ID、Document Profile 数据库版本/绑定/激活事件；索引启动后还必须固定 Search Index Build 与 Embedding Profile 数据库版本/绑定。ContextPackage v1 在实现 P2 时补充了 readiness event、Search Index Build、Relation Build、Query Planner 和完整检索策略 provenance；CodeGraphSnapshot v1 在实现 P3 时补充了 framework marker、持久化 diagnostics、Symbol 的可选 `declared_type`，以及 Edge 的 `static`／`runtime`／`static_runtime` provenance 和 Evidence 引用。RuntimeRouteEvidence v1 保存脱敏的网络请求、页面导航、表单提交及其唯一匹配或 unresolved 原因，不保存 query、header、body、cookie 或 token。UnresolvedEvidenceReport v1 对每个 Code Graph 的全部 unresolved Edge 保存分类、来源位置、候选目标、缺失 Evidence、解决建议与 provenance；新 Graph 只在唯一 resolved Edge 构成证明时记录 closed item，并通过 predecessor 保留完整历史。首次对外发布后这些必填字段不得在 v1 内重写。

ImpactReport 与 ImpactConfirmation v1 在 P4 使用原有字段落地。新 Report Artifact 只以 `blocked` 或 `awaiting_confirmation` 创建；后续 confirmed/superseded 是规范化事件状态，不通过覆盖 Artifact payload 实现。

ApprovalGrant v1 从 active Edit Packet 派生精确文件、Revision、测试命令引用和 UI Scenario 范围。Grant 本体不可变，`edit_completed`、`completed`、`revoked` 只通过追加式事件记录。

CopilotCodingTask v2 是 Web、本地 Bridge、VS Code 扩展、MCP 与未来 API Provider 共用的统一变更任务契约。新任务使用 `copilot_change_task`，携带需求上下文、固定六阶段顺序以及设计差异、代码差异、TestPlan、TestDataPlan 四项必需产物；v1 `copilot_coding_plan` 仅用于读取既有历史。POC 使用 `local_bridge` 与 `vscode_github_copilot`，契约仍预留 `api_provider` route。

UiVerificationResult v1 在 P5 由规范化 Plan/Run/Scenario Result/Evidence 生成。Artifact 保存最终状态和 Evidence ID，不内嵌截图或日志；Deployment Revision 必须绑定 committed Edit Result 的 Repository Revision。

主变更闭环只从 Web 的自然语言 `ChangeRequest` 开始。VS Code GitHub Copilot 在同一个 `CopilotCodingTask` 中提交设计书差分、代码范围、TestPlan 和 TestDataPlan；`DocumentChangeProposal` 仅作为内部设计书变更 Artifact，不再构成第二个产品入口。确定性检查自动执行；业务歧义或越界修改进入人工确认。

ChangeOrchestrationPlan 将已确认的文档差异、Impact Report、审核済み Golden Case、验收标准、测试计划、跨画面测试数据流、业务覆盖率和 UI Scenario 绑定为同一次可追溯编排。TestDataPlan 的 generation_flows 按顺序传递输出变量，并要求每一步的后置条件和最终业务断言；无法解析的画面、动作、变量或清理步骤必须阻断执行。

BusinessDataTemplate 是可复用、版本化的跨画面业务数据定义。模板明确主从实体依赖、非敏感参数、实例化前置条件、共享变量生产者／消费者、生成步骤和逆序清理步骤；只有 `approved` 模板可实例化。TestDataPlan 只保存模板身份、参数名、前置条件结果和实体顺序，不保存参数值。

TestDataExecutionResult 记录各 flow 的 fixture、HTTP、SQL、UI 步骤、输出变量、断言、清理和脱敏 Evidence；失败后停止后续 setup，但不能跳过已进入流程的 cleanup。ChangeClosureResult 对 Edit Result、确定性测试、Test Data、业务覆盖率和 UI 结果做最终 fail-closed 汇总：证据不足为 `blocked`，范围越界为 `reanalysis_required`，只有全部适用条件通过才为 `passed`。

TestCaseChangeProposal 将一条自然语言中的多个 Case、步骤、测试数据和业务断言修改聚合为业务可见的整体结构化差异。确定性差异也必须先展示；存在多个 Case、验收标准或业务断言候选时，在同一 Proposal 中收集所有选项，统一确认后才一次生成版本。TestCaseRevision 固定确认后实际应用的操作、新 TestPlan／Orchestration，以及因版本变化而失效的旧 Run、Artifact、Evidence 和 ChangeClosureResult。撤销使用 `undo` Revision 生成补偿性新版本并关联原 Revision，不删除或覆盖历史。

`examples/` 为每个 v1 Artifact 保存一个可执行示例。Baseline 校验会同时检查 Schema 和示例，避免出现“Schema 合法但没有任何真实 payload 可通过”的空契约。
